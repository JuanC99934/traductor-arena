#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
 TRANSCRIPTOR DE AUDIO Y GENERADOR DE ACTAS  ·  Backend FastAPI
=============================================================================
 Aplicación portable y autónoma para Windows (también corre en Linux/macOS).

  · Transcripción local con faster-whisper (sin traducción: idioma original)
  · Limpieza de audio con FFmpeg + lectura de progreso en tiempo real
  · Diarización / identificación de hablantes con resemblyzer + scipy
  · Actas ejecutivas con Google Gemini o IA 100 % local (Hugging Face)
  · Cola de trabajos asíncrona, historial, exportación .docx/.txt/.srt/.vtt

 Ejecutar:   python app.py
 Abrir:      http://127.0.0.1:8000
=============================================================================
"""

from __future__ import annotations

import contextlib
import datetime as dt
import gc
import inspect
import json
import logging
import logging.handlers
import math
import os
import queue
import re
import functools
import hashlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# -----------------------------------------------------------------------------
# 0.0 COMPROBACIÓN DE PYDANTIC (se hace lo primero, para que cualquier problema
#     salga con un mensaje claro y no como un traceback en mitad del archivo)
# -----------------------------------------------------------------------------
try:
    import pydantic as _pydantic
except ImportError as _exc:  # pragma: no cover - entorno roto
    raise SystemExit(
        "[ERROR] Falta Pydantic, que FastAPI necesita.\n"
        "        Ejecuta:  .venv\\Scripts\\pip install -r requirements.txt\n"
        f"        ({_exc})"
    ) from None

try:
    _PYDANTIC_MAJOR = int(str(getattr(_pydantic, "VERSION", "1")).split(".")[0])
except (TypeError, ValueError):  # versiones raras tipo "2.0b1"
    _PYDANTIC_MAJOR = 1

# Pydantic 1.10.18 publica un "ConfigDict" de compatibilidad, así que no sirve
# probar el import para saber la versión: hay que mirar el número.
PYDANTIC_V2 = _PYDANTIC_MAJOR >= 2
PYDANTIC_VERSION = str(getattr(_pydantic, "VERSION", "?"))

# Deliberadamente NO se importa ConfigDict: los modelos no usan ninguna opción
# de configuración, así que el archivo funciona igual con Pydantic 2 y con
# Pydantic 1 sin ramas ni compatibilidades dudosas.
from pydantic import BaseModel, Field  # noqa: E402

# -----------------------------------------------------------------------------
# 0. RUTAS DEL PROYECTO
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MODEL_DIR = BASE_DIR / "modelos"
HUB_DIR = MODEL_DIR / "hub"
UPLOAD_DIR = BASE_DIR / "uploads"
HISTORY_DIR = BASE_DIR / "historial"
BACKUP_DIR = BASE_DIR / "backups"
RESULT_DIR = BASE_DIR / "resultados"
TMP_DIR = BASE_DIR / "tmp"
for _d in (STATIC_DIR, MODEL_DIR, HUB_DIR, UPLOAD_DIR, HISTORY_DIR, BACKUP_DIR, RESULT_DIR, TMP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"
PARTIAL_DIR = RESULT_DIR  # los avances de transcripción viven junto a los resultados

HISTORY_FILE = BASE_DIR / "historial.json"
SESSIONS_FILE = BASE_DIR / "sesiones.json"
VOICES_FILE = BASE_DIR / "voces.json"
VOICES_DIR = BASE_DIR / "voces"          # clips de referencia de cada voz conocida
VOICES_DIR.mkdir(parents=True, exist_ok=True)
IA_CONFIG_FILE = BASE_DIR / "ia_config.json"

# -----------------------------------------------------------------------------
# 1. PORTABILIDAD: HF_HOME local y modo offline
# -----------------------------------------------------------------------------
# Todos los modelos (Whisper, LLM local, tokenizers) viven en ./modelos.
os.environ.setdefault("HF_HOME", str(MODEL_DIR))
os.environ.setdefault("HF_HUB_CACHE", str(HUB_DIR))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(HUB_DIR))
os.environ.setdefault("TRANSFORMERS_CACHE", str(HUB_DIR))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", str(max(1, (os.cpu_count() or 4) // 2)))
os.environ.setdefault("KMP_WARNINGS", "0")

OFFLINE_ACTIVE = False


def _hub_has_models() -> bool:
    """¿Existe ya al menos un modelo descargado en ./modelos/hub?"""
    try:
        for child in HUB_DIR.iterdir():
            if child.is_dir():
                for root, _dirs, files in os.walk(child):
                    if any(f in ("model.bin", "config.json", "model.safetensors") for f in files):
                        return True
    except Exception:
        return False
    return False


def configure_offline_mode() -> bool:
    """Activa modo offline si los modelos ya existen localmente."""
    global OFFLINE_ACTIVE
    try:
        invalidate_model_cache()  # definida más abajo; en el arranque puede no existir
    except NameError:
        pass
    if _hub_has_models():
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        OFFLINE_ACTIVE = True
    else:
        os.environ["HF_HUB_OFFLINE"] = "0"
        os.environ["TRANSFORMERS_OFFLINE"] = "0"
        OFFLINE_ACTIVE = False
    return OFFLINE_ACTIVE


configure_offline_mode()


@contextlib.contextmanager
def allow_download():
    """
    Desactiva temporalmente el modo offline para poder descargar un modelo
    nuevo. Al salir restaura el estado original (offline si ya había modelos).
    """
    prev_hub = os.environ.get("HF_HUB_OFFLINE", "0")
    prev_tr = os.environ.get("TRANSFORMERS_OFFLINE", "0")
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "0"
    # Las versiones nuevas de huggingface_hub copian la constante dentro de sus
    # propios módulos al importarse, así que no basta con tocar constants:
    # hay que recorrer los módulos ya cargados y poner el valor a False.
    parcheados: List[Tuple[Any, str, Any]] = []
    try:
        import huggingface_hub  # noqa: F401  (importa lo mínimo)

        for nombre in ("huggingface_hub.constants",
                       "huggingface_hub.file_download",
                       "huggingface_hub._snapshot_download",
                       "huggingface_hub.utils"):
            try:
                __import__(nombre)
            except Exception:
                pass
        for _nombre, _mod in list(sys.modules.items()):
            if not _nombre.startswith("huggingface_hub"):
                continue
            for _attr in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
                try:
                    _valor = getattr(_mod, _attr, None)
                except Exception:
                    continue
                if _valor:
                    try:
                        setattr(_mod, _attr, False)
                        parcheados.append((_mod, _attr, _valor))
                    except Exception:
                        pass
    except Exception:
        pass
    try:
        yield
    finally:
        os.environ["HF_HUB_OFFLINE"] = prev_hub
        os.environ["TRANSFORMERS_OFFLINE"] = prev_tr
        for _mod, _attr, _valor in parcheados:
            try:
                setattr(_mod, _attr, _valor)
            except Exception:
                pass


@functools.lru_cache(maxsize=64)
def _model_is_local_cached(repo_id: str) -> bool:
    """Versión cacheada: recorrer ./modelos en cada /api/health era caro."""
    return _model_is_local_scan(repo_id)


def _model_is_local_scan(repo_id: str) -> bool:
    folder = "models--" + repo_id.replace("/", "--")
    path = HUB_DIR / folder
    if not path.is_dir():
        return False
    for _root, _dirs, files in os.walk(path):
        if any(f.endswith((".bin", ".safetensors", ".pt", ".ct2")) for f in files):
            return True
        if any(f == "model.bin" for f in files):
            return True
    return False


def model_is_local(repo_id: str) -> bool:
    """Comprueba si un repo de Hugging Face ya está en la caché local (cacheado)."""
    try:
        return _model_is_local_cached(repo_id)
    except Exception:
        return _model_is_local_scan(repo_id)


def invalidate_model_cache() -> None:
    """Tras descargar un modelo nuevo hay que volver a mirar el disco."""
    try:
        _model_is_local_cached.cache_clear()
    except Exception:
        pass


# -----------------------------------------------------------------------------
# 2. LOCALIZADOR DE FFMPEG
# -----------------------------------------------------------------------------
_FFMPEG_CACHE: Dict[str, Optional[str]] = {"ffmpeg": None, "ffprobe": None}


def find_ffmpeg(name: str = "ffmpeg") -> Optional[str]:
    """
    Busca ffmpeg/ffprobe en orden:
      1) variable de entorno FFMPEG_BIN (archivo o carpeta)
      2) PATH del sistema
      3) rutas de instalación de WinGet (Gyan.FFmpeg*)
      4) ubicaciones típicas de winget/chocolatey/scoop
    """
    cached = _FFMPEG_CACHE.get(name)
    if cached:
        return cached

    exe = f"{name}.exe" if os.name == "nt" else name

    # 1) FFMPEG_BIN
    env_bin = os.environ.get("FFMPEG_BIN", "").strip('"').strip()
    if env_bin:
        p = Path(env_bin)
        if p.is_dir():
            for cand in (p / exe, p / "bin" / exe):
                if cand.is_file():
                    _FFMPEG_CACHE[name] = str(cand)
                    return str(cand)
        elif p.is_file() and p.name.lower().startswith(name):
            _FFMPEG_CACHE[name] = str(p)
            return str(p)

    # 2) PATH
    found = shutil.which(name) or shutil.which(exe)
    if found:
        _FFMPEG_CACHE[name] = found
        return found

    # 3) WinGet (Gyan.FFmpeg) + 4) rutas típicas
    candidates: List[Path] = []
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        winget_pkgs = Path(localappdata) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.is_dir():
            try:
                for pkg in winget_pkgs.iterdir():
                    if pkg.name.lower().startswith("gyan.ffmpeg"):
                        candidates.append(pkg)
            except Exception:
                pass
        candidates += [
            Path(localappdata) / "Microsoft" / "WinGet" / "Links",
            Path(localappdata) / "Programs" / "ffmpeg",
        ]
    candidates += [
        Path(r"C:\ffmpeg"),
        Path(r"C:\ffmpeg\bin"),
        Path(r"C:\Program Files\ffmpeg\bin"),
        Path(r"C:\ProgramData\chocolatey\bin"),
        Path(os.path.expanduser("~")) / "scoop" / "shims",
        Path("/usr/bin"),
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
    ]
    for base in candidates:
        if not base.exists():
            continue
        direct = base / exe
        if direct.is_file():
            _FFMPEG_CACHE[name] = str(direct)
            return str(direct)
        try:
            for sub in base.rglob(exe):
                if sub.is_file():
                    _FFMPEG_CACHE[name] = str(sub)
                    return str(sub)
        except Exception:
            continue

    # No se cachea el fallo: si el usuario instala FFmpeg sin reiniciar, se detecta.
    _FFMPEG_CACHE[name] = None
    return None


def ffmpeg_available() -> bool:
    return find_ffmpeg("ffmpeg") is not None


def ffmpeg_dir_for_path() -> str:
    """Directorio de ffmpeg, útil para exportar FFMPEG_BIN al entorno."""
    exe = find_ffmpeg("ffmpeg")
    return str(Path(exe).parent) if exe else ""


# -----------------------------------------------------------------------------
# 3. UTILIDADES GENERALES
# -----------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    """Log rotativo a ./logs/app.log además de la consola."""
    logger = logging.getLogger("tae")
    if getattr(logger, "_configured", False):
        return logger
    logger.setLevel(logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(
            str(LOG_FILE), maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            lg.addHandler(handler)
            lg.propagate = False
    except Exception:
        pass
    logger._configured = True  # type: ignore[attr-defined]
    return logger


LOG = setup_logging()


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def human_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds or 0)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_vtt_time(seconds: float) -> str:
    return fmt_srt_time(seconds).replace(",", ".")


def read_json(path: Path, default: Any) -> Any:
    """
    Lee JSON con recuperación: si el archivo está corrupto intenta el .bak y
    deja constancia en el log. Así un corte de luz no borra el historial.
    """
    for candidate in (path, path.with_suffix(path.suffix + ".bak")):
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if candidate is not path:
                LOG.warning("JSON corrupto en %s: se recuperó desde %s", path.name, candidate.name)
            return data
        except FileNotFoundError:
            continue
        except Exception as exc:
            LOG.warning("No se pudo leer %s: %s", candidate.name, exc)
    return default


def write_json(path: Path, data: Any, backup: bool = True) -> None:
    """
    Escritura atómica + copia .bak del contenido anterior (solo archivos pequeños)
    para no corromper historial/sesiones/resultados si el proceso se corta.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        if backup and path.exists():
            try:
                if path.stat().st_size < 8 * 1024 * 1024:
                    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            except Exception:
                pass
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception as exc:
        LOG.error("No se pudo escribir %s: %s", path.name, exc)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def safe_slug(text: str, fallback: str = "reunion") -> str:
    text = re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ _-]", "", text or "").strip()
    text = re.sub(r"\s+", "_", text)
    return (text or fallback)[:60]


# -----------------------------------------------------------------------------
# 4. DETECCIÓN DE HARDWARE
# -----------------------------------------------------------------------------
_HW_INFO: Optional[Dict[str, Any]] = None


def detect_hardware() -> Dict[str, Any]:
    """
    Detecta CUDA. En CPU calcula núcleos físicos aproximados (os.cpu_count()//2,
    típico de Intel con HT) y usa int8 para aprovechar AVX2 sin saturar caché.
    """
    global _HW_INFO
    if _HW_INFO is not None:
        return _HW_INFO

    info: Dict[str, Any] = {
        "cuda": False,
        "cuda_devices": 0,
        "gpu_names": [],
        "cpu_count": os.cpu_count() or 4,
        "cpu_threads": max(1, (os.cpu_count() or 4) // 2),
        "device": "cpu",
        "compute_type": "int8",
        "ffmpeg": find_ffmpeg("ffmpeg"),
        "ffprobe": find_ffmpeg("ffprobe"),
        "offline": OFFLINE_ACTIVE,
        "platform": sys.platform,
    }
    try:
        import ctranslate2  # type: ignore

        n = int(ctranslate2.get_cuda_device_count())
        info["cuda_devices"] = n
        if n > 0:
            info["cuda"] = True
            info["device"] = "cuda"
            info["compute_type"] = "float16"
    except Exception:
        pass
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            info["cuda"] = True
            info["cuda_devices"] = max(1, torch.cuda.device_count())
            info["device"] = "cuda"
            info["compute_type"] = "float16"
            info["gpu_names"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
    except Exception:
        pass
    _HW_INFO = info
    return info


# -----------------------------------------------------------------------------
# 5. MODOS DE TRANSCRIPCIÓN
# -----------------------------------------------------------------------------
MODES: Dict[str, Dict[str, Any]] = {
    "rapido": {
        "label": "Rápido",
        "model": "base",
        "beam_size": 1,
        "compute_type": "int8",
        "batch": 4,
        "desc": "Modelo base, beam 1. Ideal para pruebas y audios cortos.",
    },
    "equilibrado": {
        "label": "Equilibrado",
        "model": "small",
        "beam_size": 3,
        "compute_type": "int8",
        "batch": 8,
        "desc": "Modelo small, beam 3. Buen balance calidad/velocidad.",
    },
    "turbo": {
        "label": "Turbo (rápida)",
        "model": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "beam_size": 2,
        "compute_type": "int8",
        "batch": 8,
        "desc": "large-v3-turbo int8, beam 2. Óptimo para sesiones de 3 horas.",
    },
    "preciso": {
        "label": "Preciso",
        "model": "medium",
        "beam_size": 3,
        "compute_type": "int8",
        "batch": 6,
        "desc": "Modelo medium, beam 3. Más exactitud, más lento.",
    },
    "ultra": {
        "label": "Ultra (por defecto)",
        "model": "Systran/faster-whisper-large-v3",
        "beam_size": 5,
        "compute_type": "int8",
        "batch": 4,
        "desc": "large-v3 beam 5. La transcripción más exacta y también la más lenta.",
    },
}
# Modo fijo de la aplicación: Ultra (large-v3, beam 5).
DEFAULT_MODE = "ultra"

# Tamaño aproximado de la descarga de cada modelo (bytes). Solo se usa para
# avisar «esto pesa 1,6 GB la primera vez» y para calcular el porcentaje
# mientras se descarga; si Hugging Face responde, se usa el tamaño real.
MODEL_SIZES: Dict[str, int] = {
    "Systran/faster-whisper-tiny": 76 * 1024 ** 2,
    "Systran/faster-whisper-base": 145 * 1024 ** 2,
    "Systran/faster-whisper-small": 486 * 1024 ** 2,
    "Systran/faster-whisper-medium": 1530 * 1024 ** 2,
    "Systran/faster-whisper-large-v3": 3090 * 1024 ** 2,
    "deepdml/faster-whisper-large-v3-turbo-ct2": 1620 * 1024 ** 2,
}

LANGUAGES: List[Dict[str, str]] = [
    {"code": "", "label": "Detección automática"},
    {"code": "es", "label": "Español"},
    {"code": "en", "label": "Inglés"},
    {"code": "pt", "label": "Portugués"},
    {"code": "fr", "label": "Francés"},
    {"code": "de", "label": "Alemán"},
    {"code": "it", "label": "Italiano"},
    {"code": "zh", "label": "Chino"},
    {"code": "ja", "label": "Japonés"},
    {"code": "ko", "label": "Coreano"},
    {"code": "ru", "label": "Ruso"},
    {"code": "ar", "label": "Árabe"},
]

# Prompt de dominio: juntas directivas, hospital, Beneficencia, contabilidad…
DOMAIN_PROMPT = (
    "Transcripción literal en español de una reunión formal de junta directiva, "
    "consejo de administración y comité técnico. Temas: hospital, atención médica, "
    "pacientes, urgencias, consulta externa, quirófano, hospitalización, enfermería, "
    "aseguradoras, pólizas, coaseguro, deducible, remisión de pacientes, "
    "La Beneficencia Española (la Bene), patronato, comisiones, comité de finanzas, "
    "contabilidad, estados financieros, EBITDA, utilidad de operación, ISR, IVA, "
    "descorche, presupuesto, flujo de efectivo, auditoría, actas, quórum, "
    "acuerdos, votación, orden del día, seguimiento de acuerdos, licitaciones, "
    "proveedores, obra, mantenimiento, recursos humanos, nómina, IMSS, COFEPRIS. "
    "Nombres propios: San Luis Potosí, Beneficencia Española, Hospital Central, "
    "doctor, licenciado, ingeniero, contador, presidente, secretario, tesorero, "
    "vocal, director general, comisario. Vocabulario y cifras exactas, sin traducción."
)


# -----------------------------------------------------------------------------
# 6. CANCELACIÓN Y ESTADO DE TRABAJOS
# -----------------------------------------------------------------------------
class JobCancelled(Exception):
    """Se lanza cuando el usuario cancela un trabajo."""


@contextlib.contextmanager
def offline_only():
    """
    Fuerza modo offline durante la carga de un modelo ya descargado: así la app
    nunca sale a internet si no hace falta (y funciona sin conexión).
    """
    prev_hub = os.environ.get("HF_HUB_OFFLINE", "0")
    prev_tr = os.environ.get("TRANSFORMERS_OFFLINE", "0")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    parcheados: List[Tuple[Any, str, Any]] = []
    for _nombre, _mod in list(sys.modules.items()):
        if not _nombre.startswith("huggingface_hub"):
            continue
        for _attr in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            try:
                _valor = getattr(_mod, _attr, None)
            except Exception:
                continue
            if _valor is False:
                try:
                    setattr(_mod, _attr, True)
                    parcheados.append((_mod, _attr, _valor))
                except Exception:
                    pass
    try:
        yield
    finally:
        os.environ["HF_HUB_OFFLINE"] = prev_hub
        os.environ["TRANSFORMERS_OFFLINE"] = prev_tr
        for _mod, _attr, _valor in parcheados:
            try:
                setattr(_mod, _attr, _valor)
            except Exception:
                pass


def check_cancel(job: Dict[str, Any]) -> None:
    ev = job.get("cancel")
    if ev is not None and ev.is_set():
        raise JobCancelled()


# -----------------------------------------------------------------------------
# 7. FFMPEG: SONDA, PREPROCESO CON PROGRESO, CORTES
# -----------------------------------------------------------------------------
def probe_duration(path: Path) -> float:
    """Duración en segundos usando ffprobe (fallback: estimación por tamaño)."""
    ffprobe = find_ffmpeg("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=120,
            )
            val = (out.stdout or "").strip().splitlines()
            if val:
                return max(0.0, float(val[0]))
        except Exception:
            pass
    # Fallback para WAV: leer cabecera
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate() or 1)
    except Exception:
        pass
    # Último recurso: estimar a 128 kbps
    try:
        return max(0.0, path.stat().st_size / (128 * 1024 / 8))
    except Exception:
        return 0.0


def measure_loudnorm(src: Path, timeout: int = 1800) -> Dict[str, float]:
    """
    Primera pasada de loudnorm: mide I/TP/LRA/thresh reales para aplicarlos en
    la segunda (normalización de dos pasadas, mucho más fiel que la de una).
    """
    ffmpeg = find_ffmpeg("ffmpeg")
    if not ffmpeg:
        return {}
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-i", str(src), "-vn", "-sn", "-dn",
        "-af", "loudnorm=I=-16:TP=-2:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        text = res.stderr or ""
        block = text[text.rfind("{"): text.rfind("}") + 1] if "{" in text else ""
        if not block:
            return {}
        data = json.loads(block)
        return {k: float(data[k]) for k in
                ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
                if k in data}
    except Exception:
        return {}


def build_audio_filter(user_filter: str = "", measured: Optional[Dict[str, float]] = None,
                       denoise: bool = True) -> str:
    """
    Cadena de limpieza: paso alto + reducción de ruido espectral (afftdn) +
    compresor suave + normalización EBU R128 (dos pasadas si hay medidas).
    Permite añadir un filtro extra definido por el usuario.
    """
    parts = ["highpass=f=75"]
    if denoise:
        parts.append("afftdn=nr=12:nf=-25")
    parts.append("acompressor=threshold=-18dB:ratio=3:attack=5:release=150:makeup=4")
    if measured:
        parts.append(
            "loudnorm=I=-16:TP=-2:LRA=11"
            f":measured_I={measured['input_i']:.2f}"
            f":measured_TP={measured['input_tp']:.2f}"
            f":measured_LRA={measured['input_lra']:.2f}"
            f":measured_thresh={measured['input_thresh']:.2f}"
            f":offset={measured.get('target_offset', 0.0):.2f}:linear=true"
        )
    else:
        parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    extra = (user_filter or "").strip().strip(",")
    if extra:
        parts.append(extra)
    return ",".join(parts)


def preprocess_audio(
    src: Path,
    dst: Path,
    duration: float,
    on_progress,
    cancel_event: Optional[threading.Event] = None,
    user_filter: str = "",
    music_mode: bool = False,
    two_pass: bool = True,
    denoise: bool = True,
) -> Path:
    """
    Convierte a WAV 16 kHz mono pcm_s16le aplicando limpieza.
    - Dos pasadas de loudnorm (medición + aplicación) cuando el audio es ≤ 1 h.
    - afftdn (reducción de ruido) con reintento automático si el build de
      FFmpeg no lo soporta.
    Lee el progreso real desde `-progress pipe:1` y permite cancelación
    inmediata matando el subproceso.
    """
    ffmpeg = find_ffmpeg("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "No se encontró FFmpeg. Instálalo con:  winget install Gyan.FFmpeg\n"
            "o define la variable de entorno FFMPEG_BIN apuntando a ffmpeg.exe"
        )
    if music_mode:
        measured = None
        af = ("highpass=f=40,acompressor=threshold=-18dB:ratio=2:attack=10:release=200,"
              "loudnorm=I=-16:TP=-1.5:LRA=11")
    else:
        measured = measure_loudnorm(src) if (two_pass and 0 < duration <= 3600) else {}
        af = build_audio_filter(user_filter, measured, denoise=denoise)

    def run(filter_chain: str) -> Tuple[int, str]:
        cmd = [
            ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-i", str(src),
            "-vn", "-sn", "-dn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            "-af", filter_chain,
            "-progress", "pipe:1", "-nostats",
            str(dst),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        done = threading.Event()

        def reader():
            try:
                for line in proc.stdout or []:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                        try:
                            sec = float(line.split("=", 1)[1]) / 1_000_000.0
                            if duration > 0:
                                on_progress(min(1.0, sec / duration), sec)
                        except Exception:
                            pass
                    elif line.startswith("out_time="):
                        try:
                            hms = line.split("=", 1)[1]
                            h, m, sec_s = hms.split(":")
                            sec = int(h) * 3600 + int(m) * 60 + float(sec_s)
                            if duration > 0:
                                on_progress(min(1.0, sec / duration), sec)
                        except Exception:
                            pass
                    elif line.startswith("progress=end"):
                        on_progress(1.0, duration)
            except Exception:
                pass
            finally:
                done.set()

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        cancelled = False
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                try:
                    proc.kill()
                except Exception:
                    pass
                break
            time.sleep(0.15)
        done.wait(timeout=3)
        err = ""
        try:
            err = (proc.stderr.read() or "")[-4000:]
        except Exception:
            pass
        if cancelled:
            raise JobCancelled()
        return proc.returncode or 0, err

    code, err = run(af)
    if code != 0:
        # Reintento con la cadena mínima (FFmpeg antiguo sin afftdn/loudnorm 2 pasadas)
        LOG.warning("FFmpeg falló con el filtro avanzado; reintentando con filtro simple: %s",
                    (err or "")[-300:])
        simple = "highpass=f=75,acompressor=threshold=-18dB:ratio=3:attack=5:release=150, loudnorm=I=-16:TP=-1.5:LRA=11".replace("\n", "")
        code, err = run(simple)
    if code != 0:
        raise RuntimeError(f"FFmpeg falló al limpiar el audio: {err}")
    if not dst.exists() or dst.stat().st_size < 1000:
        raise RuntimeError(f"FFmpeg no generó audio válido. Detalle: {err}")
    return dst


def slice_wav(src: Path, dst: Path, start: float, end: float) -> Path:
    """Extrae [start, end) del WAV a otro WAV 16 kHz mono (sin recomprimir)."""
    ffmpeg = find_ffmpeg("ffmpeg")
    if ffmpeg:
        cmd = [
            ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.2, end - start):.3f}",
            "-i", str(src), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst),
        ]
        subprocess.run(cmd, capture_output=True, timeout=1800,
                       creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        if dst.exists() and dst.stat().st_size > 1000:
            return dst
    # Fallback con el módulo wave
    with wave.open(str(src), "rb") as rf:
        rate = rf.getframerate()
        rf.setpos(min(int(start * rate), rf.getnframes()))
        frames = rf.readframes(int((end - start) * rate))
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(frames)
    return dst


def load_wav_array(path: Path, target_sr: int = 16000):
    """
    Carga un WAV 16 kHz mono como float32 usando memmap (sin copias intermedias
    de int16), para que 3 horas ≈ 690 MB en lugar de >1 GB.
    Evita depender de PyAV para decodificar el audio ya limpio.
    """
    import numpy as np  # type: ignore

    try:
        with wave.open(str(path), "rb") as wf:
            sr = wf.getframerate()
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            n = wf.getnframes()
        if sr == target_sr and ch == 1 and sw == 2 and n > 0:
            with open(path, "rb") as fh:
                head = fh.read(4096)
            pos = head.find(b"data")
            if pos != -1:
                offset = pos + 8
                mm = np.memmap(str(path), dtype=np.int16, mode="r", offset=offset, shape=(n,))
                return np.asarray(mm, dtype=np.float32) / 32768.0
    except Exception:
        pass
    # Fallback: decodificador de faster-whisper (requiere PyAV compatible)
    from faster_whisper.audio import decode_audio  # type: ignore

    chunks = list(decode_audio(str(path), sampling_rate=target_sr))
    return np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)


def read_wav_slice(path: Path, start: float, end: float):
    """Lee un tramo del WAV como float32 (sin cargar las 3 horas en RAM)."""
    import numpy as np  # type: ignore

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate() or 16000
        total = wf.getnframes()
        a = max(0, int(start * rate))
        b = min(total, int(end * rate))
        if b <= a:
            return np.zeros(1, dtype=np.float32)
        wf.setpos(a)
        raw = wf.readframes(b - a)
    data = np.frombuffer(raw, dtype=np.int16)
    if data.size == 0:
        return np.zeros(1, dtype=np.float32)
    return (data.astype(np.float32) / 32768.0)


# -----------------------------------------------------------------------------
# 8. POST-PROCESAMIENTO DE TEXTO
# -----------------------------------------------------------------------------
_INTERROGATIVE = re.compile(
    r"\b(qué|quién|quiénes|cómo|cuándo|dónde|cuál|cuáles|cuánto|cuántos|cuánta|cuántas|"
    r"por\s+qué|para\s+qué|además|oye|oiga|oigan|mira|miren|verdad|cierto|eh|este|bueno|"
    r"entonces|dime|dígame|sabes|sabe|podemos|podríamos|será|sería|hay|tiene|tienen|"
    r"ok|okay|va|van|está|estamos|quedamos|de\s+acuerdo|me\s+entiendes|"
    r"pregunta|duda|alguien|nadie|cuenta|cuentan)\b",
    re.IGNORECASE,
)


_TAG_QUESTION = re.compile(r"^(no|sí|si|verdad|cierto|ok|okay|va|de\s+acuerdo|me\s+entiendes)$", re.IGNORECASE)


def fix_question_marks(text: str) -> str:
    """
    Elimina los signos ¿...? que Whisper inserta a mitad de oración cuando el
    contenido no tiene pronombres interrogativos ni muletillas reales.
    Se conservan las preguntas reales: con interrogativo, con muletilla
    (¿verdad?, ¿no?) o al inicio de una oración o intervención.
    """
    if not text or ("¿" not in text and "?" not in text):
        return text

    def repl(match: re.Match) -> str:
        inner = match.group(1)
        if _INTERROGATIVE.search(inner):
            return match.group(0)
        if _TAG_QUESTION.match(inner.strip()):
            return match.group(0)
        before = text[: match.start()].rstrip()
        if not before or before[-1] in ".!?;:\n":
            return match.group(0)  # arranca oración: probable pregunta real
        return inner.strip()

    text = re.sub(r"¿([^¿?]{1,400})\?", repl, text)
    # Signos de apertura huérfanos (sin cierre)
    text = re.sub(r"¿(?![^¿?]*\?)", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _norm_for_repeat(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[^\wáéíóúüñ ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _collapse_inside_segment(text: str) -> str:
    """Elimina la misma oración repetida 3+ veces seguidas dentro de un segmento."""
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    if len(parts) < 3:
        return text
    out: List[str] = []
    run: List[str] = []
    for p in parts:
        key = _norm_for_repeat(p)
        if run and _norm_for_repeat(run[-1]) == key:
            run.append(p)
        else:
            if run:
                out.append(run[0] if len(run) >= 3 else " ".join(run))
            run = [p]
    if run:
        out.append(run[0] if len(run) >= 3 else " ".join(run))
    return " ".join(x.strip() for x in out if x.strip())


def collapse_repeats(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Elimina bucles de alucinación: la misma frase repetida 3 o más veces
    seguidas entre segmentos consecutivos se deja una sola vez.
    """
    if not segments:
        return segments
    for s in segments:
        s["text"] = _collapse_inside_segment(s.get("text", ""))

    out: List[Dict[str, Any]] = []
    run: List[Dict[str, Any]] = []
    for seg in segments:
        key = _norm_for_repeat(seg.get("text", ""))
        if run and key and _norm_for_repeat(run[-1].get("text", "")) == key:
            run.append(seg)
        else:
            if run:
                out.extend(run if len(run) < 3 else run[:1])
            run = [seg]
    if run:
        out.extend(run if len(run) < 3 else run[:1])

    # Fusiona segmentos consecutivos vacíos o de 1 carácter que sobran.
    cleaned: List[Dict[str, Any]] = []
    for seg in out:
        txt = (seg.get("text") or "").strip()
        if not txt:
            continue
        seg["text"] = txt
        cleaned.append(seg)
    return cleaned


# -----------------------------------------------------------------------------
# 9. CARGA DE MODELOS WHISPER (1 modelo pesado en RAM)
# -----------------------------------------------------------------------------
_MODEL_LOCK = threading.Lock()
_WHISPER: Dict[str, Any] = {"name": None, "model": None, "pipe": None, "compute": None}


def _whisper_repo(model_name: str) -> str:
    if "/" in model_name:
        return model_name
    return f"Systran/faster-whisper-{model_name}"


def whisper_is_local(model_name: str) -> bool:
    return model_is_local(_whisper_repo(model_name))


def unload_whisper() -> None:
    """Libera el modelo de Whisper de la RAM."""
    with _MODEL_LOCK:
        if _WHISPER.get("pipe") is not None:
            _WHISPER["pipe"] = None
        if _WHISPER.get("model") is not None:
            try:
                del _WHISPER["model"]
            except Exception:
                pass
            _WHISPER["model"] = None
        _WHISPER["name"] = None
        gc.collect()


def clear_cuda_cache() -> None:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# --- 9.1 Progreso de descarga de modelos (registro y cabecera) ---------------
DESCARGA: Dict[str, Any] = {
    "activo": False, "repo": "", "etiqueta": "", "bytes": 0, "total": 0,
    "pct": 0, "mb_s": 0.0, "restante": 0, "texto": "",
}


def bytes_humanos(n: float) -> str:
    """1.6 GB · 486 MB · 912 KB, con coma decimal."""
    n = float(n or 0)
    for unidad in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unidad == "TB":
            if unidad == "B":
                return f"{int(round(n))} B"
            return f"{n:.1f} {unidad}".replace(".", ",")
        n /= 1024.0
    return f"{n:.1f} TB"


def _tam_modelo(repo: str) -> int:
    """Peso real de un modelo ya descargado (sigue los enlaces al almacén)."""
    carpeta = HUB_DIR / ("models--" + _whisper_repo(repo).replace("/", "--"))
    total = 0
    vistos: set = set()
    try:
        for root, _dirs, files in os.walk(carpeta):
            for f in files:
                try:
                    real = os.path.realpath(os.path.join(root, f))
                    if real in vistos:      # enlace y blob: contar una vez
                        continue
                    vistos.add(real)
                    total += os.path.getsize(real)
                except OSError:
                    pass
    except Exception:
        pass
    return total


def _tam_carpeta(path: Path) -> int:
    """Tamaño real en disco. No sigue enlaces simbólicos: huggingface_hub
    enlaza los pesos desde un almacén compartido y se contarían dos veces."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
    except Exception:
        pass
    return total


def _total_descarga(repo: str) -> int:
    """Tamaño esperado: el real de Hugging Face si responde, si no la tabla."""
    try:
        from huggingface_hub import HfApi  # type: ignore

        info = HfApi().model_info(repo, files_metadata=True)
        tam = sum(int(s.size or 0) for s in (info.siblings or []))
        if tam > 0:
            return tam
    except Exception:
        pass
    return int(MODEL_SIZES.get(repo, 0))


def _monitor_descarga(repo: str, log=None) -> threading.Event:
    """
    Vigila el tamaño de ./modelos/hub mientras faster-whisper descarga el
    modelo y lo reporta cada 5 s (descargado, %, velocidad y tiempo restante).
    Devuelve el evento con el que se detiene.
    """
    stop = threading.Event()
    repo = _whisper_repo(repo)
    carpeta = HUB_DIR / ("models--" + repo.replace("/", "--"))
    etiqueta = repo.split("/")[-1].replace("faster-whisper-", "")
    inicio = time.time()
    # huggingface_hub nuevo guarda los pesos en un almacén compartido
    # (./modelos/hub/blobs) y en la carpeta del modelo solo deja enlaces: por
    # eso se mide el crecimiento de todo el hub, no solo esa carpeta.
    base_hub = _tam_carpeta(HUB_DIR)
    base = base_hub
    total = _total_descarga(repo)
    DESCARGA.update({"activo": True, "repo": repo, "etiqueta": etiqueta, "bytes": base,
                     "total": total, "pct": 0, "mb_s": 0.0, "restante": 0,
                     "texto": "preparando la descarga"})
    if log:
        detalle = f" · {bytes_humanos(total)}" if total else ""
        log(f"Descargando el modelo «{etiqueta}»{detalle}. "
            f"Es solo la primera vez; la descarga se reanuda si se corta.")

    def _medido() -> int:
        """Bytes nuevos descargados (almacén compartido o carpeta del modelo)."""
        valor = max(0, _tam_carpeta(HUB_DIR) - base_hub, _tam_carpeta(carpeta))
        if total:
            valor = min(valor, total)
        return valor

    def _bucle() -> None:
        ultimo_bytes, ultimo_t = 0, inicio
        while not stop.wait(3.0):
            actual = _medido()
            ahora = time.time()
            dt = max(0.001, ahora - ultimo_t)
            vel = max(0.0, (actual - ultimo_bytes) / dt)
            ultimo_bytes, ultimo_t = actual, ahora
            pct = int(100 * actual / total) if total else 0
            restante = int((total - actual) / vel) if (total and vel > 2048) else 0
            texto = bytes_humanos(actual)
            if total:
                texto += f" de {bytes_humanos(total)} ({pct} %)"
            texto += f" · {bytes_humanos(vel)}/s"
            if restante:
                if restante < 60:
                    texto += f" · faltan ~{restante} s"
                else:
                    texto += f" · faltan ~{restante // 60} min"
            DESCARGA.update({"bytes": actual, "pct": pct,
                             "mb_s": round(vel / (1024 ** 2), 2),
                             "restante": restante, "texto": texto})
            if log:
                log(f"Descargando modelo: {texto}")

    threading.Thread(target=_bucle, daemon=True, name="monitor-descarga").start()
    stop.medido = _medido  # type: ignore[attr-defined]
    return stop


def get_whisper(mode_key: str, log=None):
    """
    Devuelve (modelo, pipeline_lotes) para el modo solicitado.
    Solo mantiene un modelo pesado en memoria: si cambia, descarga el anterior.
    """
    cfg = MODES.get(mode_key) or MODES[DEFAULT_MODE]
    name = cfg["model"]
    hw = detect_hardware()

    with _MODEL_LOCK:
        if _WHISPER.get("model") is not None and _WHISPER.get("name") == name:
            return _WHISPER["model"], _WHISPER.get("pipe")

        # Un único modelo pesado en RAM
        if _WHISPER.get("model") is not None:
            if log:
                log(f"Liberando modelo anterior ({_WHISPER.get('name')}) para cargar {name}…")
            _WHISPER["model"] = None
            _WHISPER["pipe"] = None
            gc.collect()
            clear_cuda_cache()

        from faster_whisper import WhisperModel, BatchedInferencePipeline  # type: ignore

        local = whisper_is_local(name)
        compute = "float16" if hw["cuda"] else cfg.get("compute_type", "int8")
        if log:
            log(
                f"Cargando modelo «{name}» ({'GPU CUDA' if hw['cuda'] else 'CPU, ' + str(hw['cpu_threads']) + ' hilos, int8'})"
                + ("" if local else " · primera vez, se descargará en ./modelos")
            )
        # Si el modelo ya está en disco se carga sin red; si no, se permite la
        # descarga aunque la app esté en modo offline por tener otros modelos
        # (antes, con un modelo descargado, ningún otro podía bajarse nunca).
        ctx = offline_only() if local else allow_download()
        stop = None if local else _monitor_descarga(name, log)
        try:
            with ctx:
                model = WhisperModel(
                    name,
                    device=hw["device"],
                    compute_type=compute,
                    cpu_threads=hw["cpu_threads"],
                    num_workers=1,
                    download_root=str(HUB_DIR),
                )
        finally:
            if stop is not None:
                stop.set()
                DESCARGA["activo"] = False
        if not local:
            if log:
                carpeta = HUB_DIR / ("models--" + name.replace("/", "--"))
                medido = 0
                if stop is not None:
                    try:
                        medido = stop.medido()  # type: ignore[attr-defined]
                    except Exception:
                        medido = 0
                tam = medido or _total_descarga(name)
                log(f"Modelo «{name.split('/')[-1]}» listo ({bytes_humanos(tam)}). "
                    f"Ya queda guardado en ./modelos y no se vuelve a descargar.")
            invalidate_model_cache()
            configure_offline_mode()  # a partir de ahora todo offline

        pipe = None
        try:
            pipe = BatchedInferencePipeline(model=model)
        except Exception:
            pipe = None

        _WHISPER.update({"name": name, "model": model, "pipe": pipe, "compute": compute})
        return model, pipe


def _filter_kwargs(fn, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibilidad entre versiones de faster-whisper."""
    try:
        params = inspect.signature(fn).parameters
    except Exception:
        return kwargs
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


# -----------------------------------------------------------------------------
# 10. TRANSCRIPCIÓN
# -----------------------------------------------------------------------------
TRANSCRIBE_WINDOW = int(float(os.environ.get("TAE_WINDOW_MIN", "20")) * 60)  # ventana en s
WINDOW_OVERLAP = 5.0          # solape entre ventanas para no cortar palabras
CHECKPOINT_EVERY = 1          # guardar avance tras cada ventana


def _window_plan(duration: float, start: float = 0.0) -> List[Tuple[float, float]]:
    """Divide el audio en ventanas de ~20 min con 5 s de solape."""
    plan: List[Tuple[float, float]] = []
    if duration <= 0:
        return [(0.0, 0.0)]
    win = max(60.0, float(TRANSCRIBE_WINDOW))
    t = max(0.0, float(start))
    while t < duration - 0.05:
        end = min(duration, t + win)
        plan.append((round(t, 3), round(end, 3)))
        if end >= duration - 0.05:
            break
        t = end - WINDOW_OVERLAP
    return plan or [(0.0, duration)]


def checkpoint_path(job_id: str) -> Path:
    return RESULT_DIR / f"{job_id}.checkpoint.json"


def partial_path(job_id: str) -> Path:
    return RESULT_DIR / f"{job_id}.partial.json"


def job_meta_path(job_id: str) -> Path:
    return RESULT_DIR / f"{job_id}.job.json"


def _seg_dict(seg, offset: float, index: int) -> Optional[Dict[str, Any]]:
    text = (seg.text or "").strip()
    if not text:
        return None
    return {
        "id": index,
        "start": round(float(seg.start) + offset, 3),
        "end": round(float(seg.end) + offset, 3),
        "text": text,
        "avg_logprob": round(float(getattr(seg, "avg_logprob", 0.0) or 0.0), 4),
        "no_speech_prob": round(float(getattr(seg, "no_speech_prob", 0.0) or 0.0), 4),
        "compression_ratio": round(float(getattr(seg, "compression_ratio", 0.0) or 0.0), 3),
        "speaker": "",
    }


def transcribe_wav(
    job: Dict[str, Any],
    wav_path: Path,
    duration: float,
    on_progress,
) -> Dict[str, Any]:
    """
    Transcribe por ventanas de ~20 min (memoria constante ~150 MB en vez de
    ~690 MB por cargar 3 h en float32), guarda checkpoint tras cada ventana y
    publica resultados parciales para que la interfaz muestre avance en vivo.
    Parámetros anti-alucinación activos y reanudación tras un corte.
    """
    import numpy as np  # type: ignore

    opts = job["options"]
    mode = opts.get("mode", DEFAULT_MODE)
    cfg = MODES.get(mode) or MODES[DEFAULT_MODE]
    model, pipe = get_whisper(mode, log=lambda m: job_log(job, m))

    vad_on = not opts.get("music_mode", False)
    batch_size = int(cfg.get("batch", 8))
    want_words = bool(opts.get("word_timestamps", True))

    base_prompt = DOMAIN_PROMPT
    ctx_text = (opts.get("context") or "").strip()
    hot = (opts.get("hotwords") or "").strip()
    if ctx_text:
        base_prompt = f"{base_prompt} {ctx_text}"
    if hot:
        base_prompt = f"{base_prompt} Términos frecuentes: {hot}."

    common: Dict[str, Any] = dict(
        language=(opts.get("language") or None) or None,
        task="transcribe",  # SIEMPRE idioma original, sin traducción
        beam_size=int(opts.get("beam_size") or cfg["beam_size"]),
        best_of=int(opts.get("beam_size") or cfg["beam_size"]),
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        condition_on_previous_text=bool(opts.get("chain_text", True)),
        initial_prompt=base_prompt[-900:],
        hotwords=hot or None,
        word_timestamps=want_words,
        # --- calidad / anti-alucinación (Whisper "repetiré esto" en silencios)
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        prompt_reset_on_temperature=0.5,
        repetition_penalty=1.02,
        length_penalty=1.0,
        vad_filter=vad_on,
        vad_parameters=dict(threshold=0.35, min_silence_duration_ms=500, speech_pad_ms=200)
        if vad_on else None,
    )
    if vad_on and opts.get("min_silence"):
        common["vad_parameters"] = dict(
            threshold=float(opts.get("vad_threshold") or 0.35),
            min_silence_duration_ms=int(opts.get("min_silence") or 500),
            speech_pad_ms=int(opts.get("speech_pad") or 200),
        )
    common = {k: v for k, v in common.items() if v is not None}

    # ------------------------------------------------------------------
    # Reanudación desde checkpoint
    # ------------------------------------------------------------------
    ckpt = read_json(checkpoint_path(job["id"]), {}) or {}
    segments_out: List[Dict[str, Any]] = [
        s for s in (ckpt.get("segments") or []) if isinstance(s, dict) and s.get("text")
    ]
    info_data: Dict[str, Any] = dict(ckpt.get("info") or {})
    start_at = float(ckpt.get("upto") or 0.0) if segments_out else 0.0
    if start_at > 0:
        job_log(job, f"Reanudando desde {human_time(start_at)} "
                     f"({len(segments_out)} fragmentos ya transcritos).")
    if start_at > 0 and ckpt.get("language") and not opts.get("language"):
        common["language"] = ckpt["language"]

    plan = _window_plan(duration, start_at)
    total_windows = len(plan)
    job_log(
        job,
        f"Transcribiendo con «{MODES[mode]['label']}» en {total_windows} ventana(s) "
        f"de {human_time(min(duration or TRANSCRIBE_WINDOW, TRANSCRIBE_WINDOW))}"
        f"{' · lotes de ' + str(batch_size) if pipe is not None else ' · secuencial'}…",
    )

    win_tmp = TMP_DIR / f"{job['id']}_win.wav"
    last_text = segments_out[-1]["text"][-200:] if segments_out else ""

    for w_idx, (ws, we) in enumerate(plan):
        check_cancel(job)
        span = max(0.1, we - ws)
        single_pass = duration <= 0 or span >= duration - 0.05

        def _load() -> "np.ndarray":
            if single_pass:
                return load_wav_array(wav_path, 16000)
            slice_wav(wav_path, win_tmp, ws, we)
            return load_wav_array(win_tmp, 16000)

        try:
            arr = _load()
        except JobCancelled:
            raise
        except Exception as exc:
            raise RuntimeError(f"No se pudo preparar la ventana {w_idx + 1}: {exc}")

        kwargs = dict(common)
        # contexto continuo entre ventanas (mejor puntuación y nombres propios)
        if last_text:
            kwargs["initial_prompt"] = ((base_prompt + " … " + last_text).strip())[-900:]
        use_batch = bool(pipe is not None and (span > 120 or single_pass) and vad_on)

        if use_batch:
            kwargs = _filter_kwargs(pipe.transcribe, dict(kwargs, batch_size=batch_size))
            it, info = pipe.transcribe(arr, **kwargs)
        else:
            kwargs = _filter_kwargs(model.transcribe, kwargs)
            it, info = model.transcribe(arr, **kwargs)

        win_segs: List[Dict[str, Any]] = []
        for seg in it:
            check_cancel(job)
            data = _seg_dict(seg, ws, 0)
            if data is None:
                continue
            # descartar el solape del inicio de ventana (ya transcrito)
            if ws > 0.0 and data["start"] < ws + WINDOW_OVERLAP * 0.6:
                continue
            win_segs.append(data)
            last_text = data["text"][-200:]
            if duration > 0:
                on_progress(min(1.0, max(0.0, data["end"] / duration)))
        del arr
        gc.collect()
        if not single_pass or not win_tmp.exists():
            win_tmp.unlink(missing_ok=True)

        segments_out.extend(win_segs)
        if info is not None and not info_data:
            info_data = {
                "language": getattr(info, "language", None),
                "language_probability": round(float(getattr(info, "language_probability", 0) or 0), 4),
                "duration": round(float(getattr(info, "duration", duration) or duration), 3),
                "duration_after_vad": round(float(getattr(info, "duration_after_vad", duration) or duration), 3),
            }

        for i, seg in enumerate(segments_out):
            seg["id"] = i

        upto = we if we >= duration - 0.05 else max(0.0, we - WINDOW_OVERLAP)
        done_frac = min(1.0, max(0.0, upto / duration)) if duration > 0 else 1.0

        # ---------------- checkpoint + parciales (UI en vivo) -------------
        try:
            write_json(checkpoint_path(job["id"]), {
                "job_id": job["id"],
                "upto": round(upto, 3),
                "duration": round(duration, 3),
                "language": (info_data or {}).get("language"),
                "info": info_data,
                "segments": segments_out,
                "saved": now_iso(),
            }, backup=False)
        except Exception as exc:
            job_log(job, f"No se pudo guardar el checkpoint: {exc}")

        try:
            write_json(partial_path(job["id"]), {
                "job_id": job["id"],
                "status": "transcribing",
                "progress": round(done_frac * 100, 1),
                "duration": round(duration, 3),
                "segments": segments_out,
                "count": len(segments_out),
                "updated": now_iso(),
            }, backup=False)
        except Exception:
            pass
        job["partial_count"] = len(segments_out)
        on_progress(done_frac)
        job_log(
            job,
            f"Ventana {w_idx + 1}/{total_windows} lista · {len(segments_out)} fragmentos "
            f"· {human_time(upto)} de {human_time(duration)}",
            None,
        )

    segments_out.sort(key=lambda s: s["start"])
    # Elimina duplicados exactos que el solape pudo introducir
    dedup: List[Dict[str, Any]] = []
    for seg in segments_out:
        if dedup and abs(seg["start"] - dedup[-1]["start"]) < 0.15 \
                and seg["text"].strip().lower() == dedup[-1]["text"].strip().lower():
            continue
        dedup.append(seg)
    segments_out = dedup
    for i, seg in enumerate(segments_out):
        seg["id"] = i
        seg["text"] = fix_question_marks(seg["text"])
    segments_out = collapse_repeats(segments_out)
    for i, seg in enumerate(segments_out):
        seg["id"] = i

    result: Dict[str, Any] = {
        "segments": segments_out,
        "info": info_data,
        "batch": pipe is not None,
        "tail_recovered": 0.0,
        "windows": total_windows,
        "window_seconds": TRANSCRIBE_WINDOW,
        "word_timestamps": want_words,
    }
    return result


# -----------------------------------------------------------------------------
# 11. DIARIZACIÓN (resemblyzer + scipy)
# -----------------------------------------------------------------------------
def _load_voice_db() -> Dict[str, Any]:
    data = read_json(VOICES_FILE, {"speakers": []})
    if isinstance(data, list):
        data = {"speakers": data}
    data.setdefault("speakers", [])
    return data


def _cosine(a, b) -> float:
    import numpy as np  # type: ignore

    a = np.asarray(a, dtype="float32")
    b = np.asarray(b, dtype="float32")
    den = (float(np.linalg.norm(a)) * float(np.linalg.norm(b))) or 1.0
    return float(np.dot(a, b) / den)


def _silhouette_cosine(E, labels) -> float:
    """Silueta media con distancia coseno (sin depender de scikit-learn)."""
    import numpy as np  # type: ignore

    labels = np.asarray(labels)
    uniq = sorted(set(int(x) for x in labels))
    if len(uniq) < 2:
        return -1.0
    D = 1.0 - (E @ E.T)
    np.fill_diagonal(D, 0.0)
    sil: List[float] = []
    for i in range(len(labels)):
        own = labels == labels[i]
        if own.sum() < 2:
            continue
        a = float(D[i][own].sum() / (own.sum() - 1))
        b = min(float(D[i][labels == k].mean()) for k in uniq if k != labels[i])
        denom = max(a, b, 1e-9)
        sil.append((b - a) / denom)
    return float(np.mean(sil)) if sil else -1.0


def adaptive_cutoff(E, Z, default: float = 0.62) -> Tuple[float, int, float]:
    """
    Elige el umbral de corte del dendrograma maximizando la silueta (2–8
    hablantes). Evita el 0.62 fijo, que sobre-segmenta voces con ruido.
    """
    from scipy.cluster.hierarchy import fcluster  # type: ignore

    best = (default, 0, -1.0)
    for t in (0.42, 0.47, 0.52, 0.57, 0.62, 0.67, 0.72, 0.78, 0.84, 0.90):
        labels = fcluster(Z, t=t, criterion="distance")
        k = len(set(int(x) for x in labels))
        if k < 2 or k > 8:
            continue
        score = _silhouette_cosine(E, labels)
        if score > best[2]:
            best = (float(t), k, score)
    if best[2] < 0.02:  # sin estructura clara: un solo bloque de voz
        return default, len(set(int(x) for x in fcluster(Z, t=default, criterion="distance"))), best[2]
    return best


def voice_match_threshold(vdb: Dict[str, Any]) -> float:
    """
    Umbral para reconocer voces conocidas. Si el usuario calibró un EER se usa
    ese valor; si no, se estima con la distribución de similitudes entre
    hablantes distintos ya guardados (impostores).
    """
    import numpy as np  # type: ignore

    try:
        if vdb.get("eer_threshold"):
            return float(min(0.92, max(0.5, vdb["eer_threshold"])))
        embs = []
        for sp in vdb.get("speakers", []):
            emb = sp.get("embedding") or []
            if len(emb) >= 32:
                v = np.asarray(emb, dtype="float32")
                embs.append(v / max(1e-8, float(np.linalg.norm(v))))
        if len(embs) < 2:
            return 0.70
        sims = []
        for i in range(len(embs)):
            for j in range(i + 1, len(embs)):
                sims.append(float(embs[i] @ embs[j]))
        if len(sims) < 2:
            return 0.70
        thr = float(np.mean(sims) + 2.2 * np.std(sims))
        return round(min(0.88, max(0.58, thr)), 3)
    except Exception:
        return 0.70


def join_same_speaker_segments(segments: List[Dict[str, Any]], max_gap: float = 1.1,
                               max_seconds: float = 40.0) -> List[Dict[str, Any]]:
    """Une turnos consecutivos del mismo hablante para leer como diálogo real."""
    if not segments:
        return segments
    out: List[Dict[str, Any]] = []
    for seg in segments:
        if out:
            prev = out[-1]
            same = (seg.get("speaker") or "") == (prev.get("speaker") or "")
            gap = float(seg.get("start", 0)) - float(prev.get("end", 0))
            merged_len = float(seg.get("end", 0)) - float(prev.get("start", 0))
            if same and -0.2 <= gap <= max_gap and merged_len <= max_seconds:
                prev["end"] = seg.get("end", prev["end"])
                prev["text"] = f"{prev['text'].rstrip()} {seg['text'].lstrip()}".strip()
                prev["avg_logprob"] = round(
                    (float(prev.get("avg_logprob") or 0) + float(seg.get("avg_logprob") or 0)) / 2, 4)
                prev["joined"] = int(prev.get("joined") or 1) + 1
                continue
        out.append(dict(seg))
    for i, seg in enumerate(out):
        seg["id"] = i
    return out


def analyze_voices(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrae embeddings de voz (resemblyzer) y agrupa hablantes con clustering
    jerárquico (scipy, distancia coseno, cutoff 0.62). Para audios largos
    (>900 ventanas) muestrea uniformemente y luego asigna el resto por
    centroide, evitando matrices O(n²). Si la transcripción tiene pocos
    segmentos de 1-12 s (Whisper puede devolver bloques largos), se dividen
    en ventanas de 6 s y se vota el hablante de cada intervención.
    """
    from collections import Counter

    import numpy as np  # type: ignore
    from scipy.cluster.hierarchy import fcluster, linkage  # type: ignore

    segments: List[Dict[str, Any]] = job["result"]["segments"]
    wav_path = Path(job["wav_path"])
    if not segments:
        return {"speakers": [], "warning": "Sin segmentos para analizar."}
    if not wav_path.exists():
        return {"speakers": [], "warning": "WAV de trabajo no disponible."}

    try:
        from resemblyzer import VoiceEncoder  # type: ignore
    except Exception as exc:
        return {"speakers": [], "warning": f"resemblyzer no está instalado: {exc}"}

    job_log(job, "Analizando voces (diarización)…")
    try:
        encoder = VoiceEncoder("cpu", verbose=False)
    except TypeError:
        encoder = VoiceEncoder("cpu")
    except Exception as exc:
        return {"speakers": [], "warning": f"No se pudo cargar el encoder de voz: {exc}"}

    # ------------------------------------------------------------------
    # 1) Ventanas de voz candidatas (1 a 12 segundos)
    # ------------------------------------------------------------------
    windows: List[Tuple[int, float, float]] = []
    for i, s in enumerate(segments):
        st, en = float(s["start"]), float(s["end"])
        if 1.0 <= (en - st) <= 12.0:
            windows.append((i, st, en))

    if len(windows) < 3:
        # Bloques largos: se trocean en ventanas de 6 s para poder comparar voces
        for i, s in enumerate(segments):
            st, en = float(s["start"]), float(s["end"])
            dur = en - st
            if dur > 12.0:
                k = 0
                while st + k * 6.0 + 2.0 <= en and k < 8:
                    windows.append((i, st + k * 6.0, min(en, st + (k + 1) * 6.0)))
                    k += 1
            elif dur >= 0.8:
                windows.append((i, st, en))

    if not windows:
        return {"speakers": [], "warning": "No hay voz suficiente para diarizar."}

    MAX_EMB = 900
    if len(windows) > MAX_EMB:
        step = len(windows) / MAX_EMB
        sampled = [windows[int(i * step)] for i in range(MAX_EMB)]
        job_log(job, f"{len(windows)} ventanas de voz: se muestrean {MAX_EMB} para el clustering.")
    else:
        sampled = windows

    embeddings: List[Any] = []
    used_windows: List[Tuple[int, float, float]] = []
    for n, (_, st, en) in enumerate(sampled):
        check_cancel(job)
        try:
            wav = read_wav_slice(wav_path, st, min(en, st + 12.0))
            if wav.size < 16000 * 0.8:
                continue
            emb = encoder.embed_utterance(wav)
            embeddings.append(np.asarray(emb, dtype="float32"))
            used_windows.append((_, st, en))
        except Exception:
            continue
        if n % 20 == 0:
            job["speaker_progress"] = round(n / max(1, len(sampled)), 3)

    if len(embeddings) < 2:
        return {"speakers": [], "warning": "No se pudieron extraer embeddings suficientes."}

    E = np.vstack(embeddings)
    E = E / np.clip(np.linalg.norm(E, axis=1, keepdims=True), 1e-8, None)

    # ------------------------------------------------------------------
    # 2) Clustering jerárquico (coseno, cutoff 0.62)
    # ------------------------------------------------------------------
    Z = linkage(E, method="average", metric="cosine")
    cutoff, n_est, sil = adaptive_cutoff(E, Z)
    labels = fcluster(Z, t=cutoff, criterion="distance")
    job_log(
        job,
        f"Agrupación de voces: umbral adaptativo {cutoff:.2f} "
        f"({len(set(int(x) for x in labels))} grupo(s), silueta {sil:.2f}).",
    )

    centroids = {}
    for c in sorted(set(int(x) for x in labels)):
        v = E[labels == c].mean(axis=0)
        centroids[c] = v / max(1e-8, float(np.linalg.norm(v)))

    # Máximo 10 hablantes: fusiona los centroides más parecidos
    MAX_SPEAKERS = 10
    while len(centroids) > MAX_SPEAKERS:
        keys = list(centroids.keys())
        best, pair = -1.0, (keys[0], keys[1])
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                sim = _cosine(centroids[keys[i]], centroids[keys[j]])
                if sim > best:
                    best, pair = sim, (keys[i], keys[j])
        a, b = pair
        merged = centroids[a] + centroids[b]
        centroids[a] = merged / max(1e-8, float(np.linalg.norm(merged)))
        centroids.pop(b, None)

    keys = list(centroids.keys())
    centroid_matrix = np.vstack([centroids[k] for k in keys])
    win_clusters: List[int] = []
    for i in range(len(E)):
        sims = centroid_matrix @ E[i]
        win_clusters.append(int(keys[int(np.argmax(sims))]))

    # ------------------------------------------------------------------
    # 3) Votación por intervención (una intervención puede tener varias ventanas)
    # ------------------------------------------------------------------
    votes: Dict[int, Counter] = {}
    for (seg_i, _st, _en), c in zip(used_windows, win_clusters):
        votes.setdefault(seg_i, Counter())[c] += 1
    last_cluster = keys[0] if keys else 0
    for i, seg in enumerate(segments):
        if i in votes:
            last_cluster = votes[i].most_common(1)[0][0]
            seg["speaker_cluster"] = last_cluster
        elif "speaker_cluster" in seg:
            last_cluster = seg["speaker_cluster"]
        else:
            seg["speaker_cluster"] = last_cluster

    # ------------------------------------------------------------------
    # 4) Orden de aparición + comparación con voces conocidas
    # ------------------------------------------------------------------
    order: List[int] = []
    for seg in segments:
        c = seg["speaker_cluster"]
        if c not in order:
            order.append(c)

    vdb = _load_voice_db()
    known = vdb.get("speakers", [])
    known_thr = voice_match_threshold(vdb)

    speakers: List[Dict[str, Any]] = []
    used_labels: List[str] = []
    for n, c in enumerate(order, start=1):
        segs = [s for s in segments if s["speaker_cluster"] == c]
        if not segs:
            continue
        seconds = sum(float(s["end"]) - float(s["start"]) for s in segs)
        label = f"Hablante {n}"
        known_name, sim = "", 0.0
        for k in known:
            try:
                s2 = _cosine(centroids[c], k.get("embedding") or [])
            except Exception:
                continue
            if s2 > sim:
                sim, known_name = s2, k.get("name", "")
        if sim >= known_thr and known_name:
            label = known_name
        if label in used_labels:
            label = f"{label} ({n})"
        used_labels.append(label)
        sample_start = min(
            (float(s["start"]) for s in segs if (s["end"] - s["start"]) >= 2.0),
            default=float(segs[0]["start"]),
        )
        speakers.append({
            "key": f"c{c}",
            "name": label,
            "cluster": int(c),
            "count": len(segs),
            "seconds": round(seconds, 1),
            "known": bool(sim >= known_thr and known_name),
            "threshold": known_thr,
            "similarity": round(sim, 3),
            "sample_start": round(sample_start, 2),
            "embedding": [round(float(x), 5) for x in centroids[c].tolist()],
            "color": n - 1,
        })

    name_by_cluster = {sp["cluster"]: sp["name"] for sp in speakers}
    for seg in segments:
        seg["speaker"] = name_by_cluster.get(seg["speaker_cluster"], "")
        seg.pop("speaker_cluster", None)

    job["speaker_progress"] = 1.0
    job_log(job, f"Diarización lista: {len(speakers)} voz(es) detectada(s).")
    return {"speakers": [{k: v for k, v in sp.items() if k != "embedding"} for sp in speakers],
            "embeddings": {sp["name"]: sp["embedding"] for sp in speakers},
            "warning": ""}


def save_voice(name: str, embedding: List[float], sample: str = "",
               quality: Optional[float] = None) -> Dict[str, Any]:
    """Guarda o promedia una huella de voz en voces.json (con clip y calidad)."""
    import numpy as np  # type: ignore

    name = (name or "").strip()
    if not name or not embedding:
        raise ValueError("Nombre y embedding son obligatorios.")
    vdb = _load_voice_db()
    emb = np.asarray(embedding, dtype="float32")
    emb = emb / max(1e-8, float(np.linalg.norm(emb)))
    for sp in vdb["speakers"]:
        if sp.get("name", "").strip().lower() == name.lower():
            old = np.asarray(sp.get("embedding") or emb, dtype="float32")
            old = old / max(1e-8, float(np.linalg.norm(old)))
            n = int(sp.get("samples", 1))
            avg = (old * n + emb) / (n + 1)
            avg = avg / max(1e-8, float(np.linalg.norm(avg)))
            sp["embedding"] = [round(float(x), 5) for x in avg.tolist()]
            sp["samples"] = n + 1
            if sample:
                sp["sample"] = sample
            if quality is not None:
                sp["quality"] = round(float(quality), 3)
            sp["updated"] = now_iso()
            write_json(VOICES_FILE, vdb)
            return sp
    entry = {
        "name": name,
        "embedding": [round(float(x), 5) for x in emb.tolist()],
        "samples": 1,
        "created": now_iso(),
        "updated": now_iso(),
    }
    if sample:
        entry["sample"] = sample
    if quality is not None:
        entry["quality"] = round(float(quality), 3)
    vdb["speakers"].append(entry)
    write_json(VOICES_FILE, vdb)
    return entry


# -----------------------------------------------------------------------------
# 12. LOG DE TRABAJOS
# -----------------------------------------------------------------------------
def job_log(job: Dict[str, Any], message: str, progress: Optional[float] = None) -> None:
    job["message"] = message
    if progress is not None:
        job["progress"] = round(float(progress), 2)
    try:
        job.setdefault("log", []).append({"t": now_iso(), "m": message})
        job["log"] = job["log"][-120:]
    except Exception:
        pass


# -----------------------------------------------------------------------------
# 13. GENERADOR DE ACTAS CON IA
# -----------------------------------------------------------------------------
AI_FORMATS: Dict[str, Dict[str, str]] = {
    "acta_completa": {
        "label": "Acta Ejecutiva Completa",
        "hint": "Documento formal con antecedentes, desarrollo, acuerdos, cifras y firmas.",
        "max_tokens": 6000,
        "section_tokens": 900,
    },
    "resumen_directo": {
        "label": "Resumen Ejecutivo Directo",
        "hint": "Síntesis de 10 a 20 puntos con lo esencial de la reunión.",
        "max_tokens": 1600,
    },
    "acuerdos": {
        "label": "Lista de Acuerdos y Pendientes",
        "hint": "Tabla de acuerdos, responsables, fechas y temas pendientes.",
        "max_tokens": 1800,
    },
}

SECRETARY_SYSTEM_PROMPT = (
    "Eres un Secretario Técnico experto en actas corporativas, financieras y hospitalarias "
    "en español. Redactas siempre en TERCERA PERSONA, con tono formal, claro y conciso.\n\n"
    "REGLAS OBLIGATORIAS E INQUEBRANTABLES:\n"
    "1. PROHIBIDO el lenguaje meta-textual. Nunca escribas frases como «en la transcripción», "
    "«en este bloque», «en el audio», «según lo transcrito», «notas», «parciales», "
    "«fragmento», «el hablante dice», «no se entiende», «(inaudible)». "
    "Redacta el contenido como hechos de la reunión.\n"
    "2. Corrige errores fonéticos usando el contexto médico, financiero y corporativo "
    "(ej.: «la bene» → «La Beneficencia Española»; «e-bita» → «EBITDA»; «isr» → «ISR»; "
    "«descorche» → «descorche»; «remisión» → «remisión de pacientes»; «aseguradoras» se "
    "mantiene; «comisión»/«comisiones» según contexto).\n"
    "3. Conserva TODAS las cifras exactas: montos, porcentajes, fechas, plazos, quórums, "
    "números de póliza y nombres propios, tal como se mencionaron.\n"
    "4. Identifica a los participantes por su nombre o, si no se conoce, por su rol "
    "(Presidencia, Secretaría Técnica, Tesorería, Dirección General, Vocalía, Comisión de Finanzas).\n"
    "5. No inventes información que no aparezca en el material. Si un dato no está, omítelo.\n"
    "6. Usa encabezados, viñetas y tablas markdown cuando aporten claridad.\n"
    "7. PROHIBIDO dejar huecos o marcadores del tipo «[Nombre Presidencia]», «[Fecha]», "
    "«[Lugar]», «[Monto]», «[Por definir]» o «XXX». Si el dato no se mencionó, no escribas "
    "la línea; nunca rellenes con corchetes. Solo se permiten corchetes para marcas de "
    "tiempo [HH:MM:SS].\n"
    "8. PROHIBIDO entregar listas de palabras sueltas o de temas sin desarrollar. Cada tema "
    "debe explicarse en prosa: qué se planteó, qué cifras se dieron, qué se decidió y quién "
    "queda a cargo.\n"
    "9. Escribe en párrafos densos con datos concretos. Si el material da para 3 párrafos, "
    "escribe 3; no comprimas una hora de reunión en una viñeta.\n"
)

FORMAT_INSTRUCTIONS: Dict[str, str] = {
    "acta_completa": (
        "Redacta un ACTA DE SESIÓN completa y formal, como la que levantaría el secretario "
        "técnico de una junta directiva hospitalaria. Estructura obligatoria:\n"
        "\n"
        "**ACTA DE SESIÓN DE LA JUNTA DIRECTIVA** (o el título real de la reunión)\n"
        "\n"
        "Un párrafo de apertura de 3 a 5 líneas que indique quién preside, con quién se "
        "verificó el quórum (nombres si aparecen), si se aprobó el acta anterior y el objeto "
        "de la sesión.\n"
        "\n"
        "Después, entre 4 y 8 SECCIONES TEMÁTICAS numeradas, con el título en MAYÚSCULAS y "
        "negritas, por ejemplo:\n"
        "**1. INSTALACIÓN DE LA SESIÓN Y APROBACIÓN DEL ACTA ANTERIOR**\n"
        "**2. COMISIÓN DE CONSTRUCCIÓN: EVALUACIÓN Y SELECCIÓN DEL PROYECTO**\n"
        "**3. INFORME DE DIRECCIÓN GENERAL Y FINANZAS**\n"
        "**4. ESTRATEGIA COMERCIAL Y MÉDICA**\n"
        "**5. COBRANZA, PROVEEDORES, RECURSOS HUMANOS Y LEGAL**\n"
        "**6. ACUERDOS Y COMPROMISOS**\n"
        "Entre cada sección escribe una línea con tres guiones (---).\n"
        "\n"
        "Reglas de cada sección:\n"
        "• NO hagas una lista de «temas tratados» con palabras sueltas: agrupa los temas "
        "relacionados en una misma sección y desarróllalos.\n"
        "• Cada sección lleva 2 a 4 párrafos narrativos en tercera persona (mínimo 120 "
        "palabras si el material lo permite), con los datos concretos que se mencionaron.\n"
        "• Cuando haya varias propuestas, cifras, proveedores o partidas, usa viñetas con la "
        "etiqueta en negritas y dos puntos, por ejemplo: "
        "«* **Constructora La Seca:** presentó un estacionamiento para 500 autos con un costo "
        "de $950,000 pesos…»\n"
        "• Escribe las cifras EXACTAMENTE como se dijeron, con formato $1,234,567 pesos y "
        "porcentajes (p. ej. 12.63 %). No redondees ni inventes.\n"
        "• Si se descartó una opción, di por qué; si se aprobó algo, di quién queda a cargo y "
        "para cuándo.\n"
        "• Cuando se nombre a una persona, usa su nombre completo tal como aparece.\n"
        "\n"
        "La última sección, **ACUERDOS Y COMPROMISOS**, es una lista numerada donde cada "
        "acuerdo indica qué se decidió y, si se mencionaron, el responsable y la fecha o plazo.\n"
        "\n"
        "Cierra con una sección **ASUNTOS PENDIENTES** (viñetas) y no agregues firmas ni "
        "despedidas: la aplicación las añade.\n"
    ),
    "resumen_directo": (
        "Redacta un RESUMEN EJECUTIVO DIRECTO en prosa y viñetas: un párrafo de contexto de 5 "
        "líneas y luego 10 a 20 viñetas agrupadas por tema, cada una con la cifra o decisión "
        "concreta y su marca de tiempo. Sin introducciones vacías ni listas de palabras "
        "sueltas.\n"
    ),
    "acuerdos": (
        "Redacta únicamente la LISTA DE ACUERDOS Y PENDIENTES en una tabla markdown con "
        "columnas: # | Acuerdo o pendiente | Responsable | Fecha o plazo | Estatus. "
        "Agrega al final una sección «Siguientes pasos» con 3 a 7 acciones inmediatas.\n"
    ),
}

_AGREEMENT_RX = re.compile(
    r"\b(acuerd\w*|aprob\w*|autoriz\w*|decid\w*|decide|resuelv\w*|instru\w*|solicit\w*|"
    r"compromet\w*|firm\w*|votaci\w*|mayoría|unanimidad|quórum|se\s+aprueba|"
    r"se\s+autoriza|se\s+acuerda|queda\s+pendiente|pendiente|encarg\w*|responsab\w*)\b",
    re.IGNORECASE,
)
_NUMBER_RX = re.compile(
    r"(\$|mxn|usd|%\b|\d{1,3}(?:[.,]\d{3})+|\b\d+(?:[.,]\d+)?\b|"
    r"\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b|"
    r"\b(ebitda|isr|iva|presupuesto|utilidad|flujo|coaseguro|deducible|póliza|remisión|"
    r"descorche|nómina|auditoría|licitación)\b)",
    re.IGNORECASE,
)
_PENDING_RX = re.compile(
    r"\b(pendiente|seguimiento|próxima\s+sesión|siguiente\s+reunión|antes\s+de|plazo|"
    r"fecha\s+límite|compromiso|tarea|entregar|enviará|revisará|presentará|"
    r"dará\s+seguimiento|queda\s+en)\b",
    re.IGNORECASE,
)


def _seg_line(seg: Dict[str, Any]) -> str:
    sp = (seg.get("speaker") or "").strip()
    prefix = f"{sp}: " if sp else ""
    return f"[{human_time(seg.get('start', 0))}] {prefix}{seg.get('text', '').strip()}"


def build_ai_transcript(segments: List[Dict[str, Any]], budget: int = 12000) -> Tuple[str, int]:
    """
    Pre-filtro inteligente: agrupa en bloques y prioriza los que tienen mayor
    densidad de acuerdos, cifras, decisiones y pendientes. Devuelve el texto en
    orden cronológico dentro de un presupuesto de caracteres (12 000 por defecto).
    """
    if not segments:
        return "", 0

    # Bloques de ~900 caracteres (≈ 6-10 minutos de reunión)
    blocks: List[Dict[str, Any]] = []
    cur: List[Dict[str, Any]] = []
    size = 0
    for seg in segments:
        line = _seg_line(seg)
        cur.append(seg)
        size += len(line) + 1
        if size >= 900:
            blocks.append({"segs": cur, "size": size})
            cur, size = [], 0
    if cur:
        blocks.append({"segs": cur, "size": size})

    for b in blocks:
        text = "\n".join(_seg_line(s) for s in b["segs"])
        b["text"] = text
        score = 0.0
        score += 3.0 * len(_AGREEMENT_RX.findall(text))
        score += 1.2 * min(30, len(_NUMBER_RX.findall(text)))
        score += 2.0 * len(_PENDING_RX.findall(text))
        score += 0.6 * text.count("?")
        # Normaliza por longitud para no premiar sólo bloques largos
        b["score"] = score / max(1.0, math.sqrt(len(text) / 700.0))

    total = sum(len(b["text"]) + 1 for b in blocks)
    if total <= budget:
        return "\n".join(b["text"] for b in blocks), total

    # Siempre incluye el inicio (contexto, asistencia, orden del día)
    selected = {0}
    used = len(blocks[0]["text"]) + 1
    ranked = sorted(range(len(blocks)), key=lambda i: blocks[i]["score"], reverse=True)
    for i in ranked:
        if i in selected:
            continue
        ln = len(blocks[i]["text"]) + 1
        if used + ln > budget:
            continue
        selected.add(i)
        used += ln
        if used >= budget * 0.97:
            break
    text = "\n".join(blocks[i]["text"] for i in sorted(selected))
    return text, len(text)


# Motores disponibles. Los dos primeros son 100 % gratuitos y locales.
AI_ENGINES: Dict[str, Dict[str, Any]] = {
    "local": {
        "label": "IA local · Hugging Face (gratis, sin internet)",
        "desc": "Descarga un modelo libre (Apache-2.0) y lo ejecuta en tu CPU. "
                "No requiere cuenta, API Key ni pagos.",
        "free": True,
    },
    "openai": {
        "label": "IA local · Ollama / LM Studio (gratis, mejor calidad)",
        "desc": "Usa un servidor local compatible con OpenAI (por defecto Ollama en "
                "http://127.0.0.1:11434/v1). Modelos libres de 7-8B con mucha mejor "
                "redacción y 0 costo.",
        "free": True,
    },
    "gemini": {
        "label": "Google Gemini 2.5 Flash (API; capa gratuita limitada)",
        "desc": "Requiere API Key de Google AI Studio. Tiene nivel gratuito con límite "
                "de peticiones por minuto/día; al superarlo se factura.",
        "free": False,
    },
}

DEFAULT_OPENAI_BASE_URL = "http://127.0.0.1:11434/v1"
# Comando que se le muestra al usuario cuando falta la IA local.
COMANDO_IA_LOCAL = (
    '.venv\\Scripts\\python -m pip install torch transformers accelerate'
    if os.name == "nt" else
    "pip install torch transformers accelerate"
)
DEFAULT_OPENAI_MODEL = "qwen2.5:7b-instruct"


def _read_ia_config() -> Dict[str, Any]:
    cfg = read_json(IA_CONFIG_FILE, {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.setdefault("gemini_api_key", "")
    cfg.setdefault("local_model", "Qwen/Qwen2.5-1.5B-Instruct")
    cfg.setdefault("openai_base_url", DEFAULT_OPENAI_BASE_URL)
    cfg.setdefault("openai_model", DEFAULT_OPENAI_MODEL)
    # Modelo de audio opcional (Ollama). Vacío = el primero que acepte audio.
    cfg.setdefault("audio_model", "")
    cfg.setdefault("engine", "local")  # por defecto: gratis y sin dependencias
    return cfg


def ia_engine_ready(engine: str) -> bool:
    """¿Se puede redactar de verdad con este motor ahora mismo?"""
    engine = (engine or "").strip()
    if engine == "local":
        return _importable("transformers") and _importable("torch")
    if engine == "openai":
        cfg = _read_ia_config()
        ok, _modelos, _msg = probe_openai_server(
            cfg.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL, timeout=1.5)
        return ok
    if engine == "gemini":
        return bool(_read_ia_config().get("gemini_api_key"))
    return False


def ia_engine_falta(engine: str) -> str:
    """Texto de ayuda concreta cuando el motor elegido no está disponible."""
    if engine == "local":
        return ("Falta la IA local: ejecuta instalar.bat y elige la opción 1 en la "
                "etapa «IA para redactar el acta», o bien: " + COMANDO_IA_LOCAL)
    if engine == "openai":
        base = _read_ia_config().get("openai_base_url") or DEFAULT_OPENAI_BASE_URL
        instalados = modelos_instalados(base)
        if instalados:
            return (f"Ollama responde en {base}, descargados: "
                    + ", ".join(instalados[:6])
                    + f". El modelo configurado no está entre ellos: escribe uno de esos "
                      f"(por ejemplo «{mejor_modelo_instalado(instalados)}») en el campo Modelo libre.")
        return ("No se detectó Ollama/LM Studio en " + base
                + ". Ábrelo (o instálalo desde https://ollama.com) y ejecuta: "
                  "ollama pull qwen2.5:7b-instruct")
    if engine == "gemini":
        return "Falta la API Key de Google Gemini: pégala en el campo de abajo."
    return "Motor desconocido."


def ensure_ia_defaults() -> Dict[str, Any]:
    """
    Elige un motor que funcione de verdad. Si el configurado no está disponible
    (falta torch/transformers, Ollama apagado…), se pasa al otro motor gratuito
    que sí funcione, en vez de fallar al pulsar «Redactar con IA».
    """
    cfg = _read_ia_config()
    actual = str(cfg.get("engine") or "local")
    if actual == "openai" and ia_engine_ready(actual):
        # el servidor está encendido: comprobar que el MODELO exista de verdad
        base = cfg.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL
        pedido = str(cfg.get("openai_model") or DEFAULT_OPENAI_MODEL)
        instalados = modelos_instalados(base)
        if instalados and pedido not in instalados:
            nuevo = mejor_modelo_instalado(instalados)
            if nuevo:
                cfg["openai_model"] = nuevo
                try:
                    _write_ia_config(cfg)
                except Exception:
                    pass
                LOG.info("Modelo local cambiado de «%s» a «%s» (el primero no está "
                         "descargado en el servidor).", pedido, nuevo)
        return cfg
    if ia_engine_ready(actual):
        return cfg
    for candidato in ("openai", "local"):
        if candidato != actual and ia_engine_ready(candidato):
            cfg["engine"] = candidato
            try:
                _write_ia_config(cfg)
            except Exception:
                pass
            LOG.info("Motor de IA cambiado automáticamente a «%s» (el anterior «%s» "
                     "no estaba disponible).", candidato, actual)
            return cfg
    if actual not in AI_ENGINES:
        cfg["engine"] = "local"
        try:
            _write_ia_config(cfg)
        except Exception:
            pass
    return cfg


def _write_ia_config(cfg: Dict[str, Any]) -> None:
    write_json(IA_CONFIG_FILE, cfg)


def _mask_key(key: str) -> str:
    key = (key or "").strip()
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}{'•' * 12}{key[-4:]}"


def call_gemini(prompt: str, system: str, api_key: str, max_tokens: int = 3000,
                on_token=None) -> str:
    """Llamada a Gemini 2.5 Flash usando urllib.request (sin dependencias extra)."""
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("Falta la API Key de Google Gemini. Configúrala en «Motor de IA».")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={urllib.parse.quote(key)}"
    )
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.25,
            "topP": 0.9,
            "maxOutputTokens": max_tokens,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:800]
        except Exception:
            pass
        raise RuntimeError(f"Gemini respondió {exc.code}: {detail or exc.reason}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"No se pudo conectar con Gemini: {exc.reason}")

    parts = (
        payload.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise RuntimeError("Gemini no devolvió texto. Revisa la API Key o el cuórum del modelo.")
    if on_token:
        on_token(text)
    return text


# Modelos de licencia libre (Apache-2.0): sin costo, sin cuenta y sin API Key.
LOCAL_MODELS: Dict[str, Dict[str, Any]] = {
    "Qwen/Qwen2.5-0.5B-Instruct": {
        "label": "Qwen2.5 0.5B · ultrarrápido (gratis)",
        "max_new": 1200, "license": "Apache-2.0", "size_gb": 1.0,
        "desc": "Descarga ~1 GB. El más rápido en CPU; ideal para resúmenes cortos.",
    },
    "Qwen/Qwen2.5-1.5B-Instruct": {
        "label": "Qwen2.5 1.5B · recomendado (gratis)",
        "max_new": 2200, "license": "Apache-2.0", "size_gb": 3.0,
        "desc": "Descarga ~3 GB. Mejor equilibrio entre calidad y velocidad en CPU.",
    },
    "Qwen/Qwen2.5-3B-Instruct": {
        "label": "Qwen2.5 3B · mejor calidad (gratis)",
        "max_new": 2600, "license": "Apache-2.0", "size_gb": 6.0,
        "desc": "Descarga ~6 GB. Actas mejor redactadas; requiere más paciencia en CPU.",
    },
    "HuggingFaceTB/SmolLM2-1.7B-Instruct": {
        "label": "SmolLM2 1.7B · alternativa (gratis)",
        "max_new": 2000, "license": "Apache-2.0", "size_gb": 3.4,
        "desc": "Descarga ~3.4 GB. Modelo compacto alternativo de Hugging Face.",
    },
}

# Sugerencias para el motor Ollama / LM Studio (todos gratuitos)
OPENAI_SUGGESTED_MODELS = [
    "qwen2.5:7b-instruct",
    "qwen2.5:3b-instruct",
    "llama3.1:8b-instruct-q4_K_M",
    "mistral:7b-instruct",
    "gemma2:9b-instruct-q4_K_M",
]

_LOCAL_LLM: Dict[str, Any] = {"name": None, "model": None, "tok": None}
_LOCAL_LLM_LOCK = threading.Lock()


def unload_local_llm() -> None:
    with _LOCAL_LLM_LOCK:
        _LOCAL_LLM["model"] = None
        _LOCAL_LLM["tok"] = None
        _LOCAL_LLM["name"] = None
        gc.collect()


_MODELOS_CACHE: Dict[str, Any] = {"base": "", "cuando": 0.0, "modelos": []}


def modelos_instalados(base_url: str = DEFAULT_OPENAI_BASE_URL,
                       frescos: bool = False) -> List[str]:
    """Lista de modelos que el servidor local (Ollama / LM Studio) tiene descargados."""
    base = (base_url or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/")
    ahora = time.time()
    if (not frescos and _MODELOS_CACHE["base"] == base
            and ahora - _MODELOS_CACHE["cuando"] < 20):
        return list(_MODELOS_CACHE["modelos"])
    ok, modelos, _msg = probe_openai_server(base, timeout=2.0)
    if not ok:
        return []
    _MODELOS_CACHE.update({"base": base, "cuando": ahora, "modelos": modelos})
    return modelos


def mejor_modelo_instalado(modelos: List[str]) -> str:
    """
    Elige el modelo más adecuado para redactar entre los que sí están descargados:
    prefiere los «instruct» y los tamaños medio-grandes.
    """
    if not modelos:
        return ""

    def puntos(nombre: str) -> Tuple[int, float]:
        n = nombre.lower()
        p = 0
        if "instruct" in n or "chat" in n:
            p += 6
        if any(k in n for k in ("qwen", "llama", "mistral", "gemma", "phi", "deepseek")):
            p += 3
        if any(k in n for k in ("embed", "nomic", "bge", "minilm", "rerank", "whisper", "audio")):
            p -= 20
        if "coder" in n:
            p -= 2
        talla = 0.0
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*b\b", n)
        if m:
            talla = float(m.group(1).replace(",", "."))
        # premia 3B–14B: suficiente para un acta y no eterno en CPU
        bonus = 2.0 if 3 <= talla <= 8 else (1.5 if 8 < talla <= 14 else (0.5 if talla > 14 else 0.0))
        return p, talla + bonus

    return sorted(modelos, key=puntos, reverse=True)[0]


def error_modelo_no_instalado(base_url: str, model: str) -> str:
    """Mensaje claro cuando el modelo pedido no está descargado en Ollama."""
    instalados = modelos_instalados(base_url, frescos=True)
    pista = ""
    if instalados:
        sugerido = mejor_modelo_instalado(instalados)
        lista = ", ".join(f"«{m}»" for m in instalados[:6])
        pista = (f" Tienes descargados: {lista}. "
                 f"Escribe «{sugerido}» en el campo Modelo libre y vuelve a intentarlo.")
    else:
        pista = " No hay ningún modelo descargado todavía."
    base = (base_url or DEFAULT_OPENAI_BASE_URL).strip()
    return (f"El modelo «{model}» no está descargado en tu servidor local ({base}). "
            f"Escribe en la terminal:  ollama pull {model}  y espera a que termine "
            f"(o elige uno que ya tengas).{pista}")


def _http_error_openai(exc, base_url: str, model: str) -> str:
    """Traduce los errores HTTP del servidor local a instrucciones utilizables."""
    detalle = ""
    try:
        detalle = exc.read().decode("utf-8", "replace")[:600]
    except Exception:
        pass
    texto = f"{detalle} {exc.reason or ''}".lower()
    if exc.code == 404 and ("not found" in texto or "no such model" in texto
                            or "model" in texto):
        return error_modelo_no_instalado(base_url, model)
    if exc.code == 404:
        return ("El servidor local no encontró esa ruta. Revisa la URL base "
                "(por ejemplo http://127.0.0.1:11434/v1).")
    if exc.code in (401, 403):
        return "El servidor local rechazó la petición (credenciales). Revisa la URL base."
    if exc.code == 400 and "model" in texto:
        return error_modelo_no_instalado(base_url, model)
    return f"El servidor local respondió {exc.code}: {detalle or exc.reason}."


def _openai_endpoint(base_url: str) -> str:
    """Normaliza la URL base de un servidor compatible con OpenAI."""
    base = (base_url or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1"):
        base = base + "/v1"
    return base + "/chat/completions"


def probe_openai_server(base_url: str = DEFAULT_OPENAI_BASE_URL,
                        timeout: float = 2.5) -> Tuple[bool, List[str], str]:
    """Comprueba si Ollama / LM Studio está escuchando y qué modelos ofrece."""
    base = (base_url or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if not base.endswith("/v1"):
        base = base + "/v1"
    try:
        req = urllib.request.Request(f"{base}/models", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        models = [m.get("id") for m in (payload.get("data") or []) if isinstance(m, dict)]
        models = [m for m in models if m]
        return True, models, f"Servidor local disponible ({len(models)} modelo/s)."
    except Exception as exc:
        return False, [], (
            "No se detectó un servidor local en "
            f"{base}. Instala Ollama (https://ollama.com, gratis) y ejecuta, por ejemplo: "
            "ollama pull qwen2.5:7b-instruct"
        )


# -----------------------------------------------------------------------------
# 12.x MODELOS DE AUDIO LOCALES (Ollama · p. ej. lfm3-audio)
# -----------------------------------------------------------------------------
# Un modelo de audio (2B, ~2 GB) NO sustituye a Whisper: no da marcas de tiempo
# y se inventa detalles en grabaciones largas. Sirve como «oído rápido»: saber
# en 30 s de qué trata la reunión, en qué idioma está y cuántas personas hablan,
# antes de transcribir 3 horas. También como segunda opinión en fragmentos
# dudosos. Ollama recibe el audio por la ruta compatible con OpenAI
# (/v1/chat/completions con un trozo «input_audio»), no por /api/chat.
DEFAULT_AUDIO_MODEL = "lfm3-audio"
_AUDIO_MODELS_CACHE: Dict[str, Any] = {"base": "", "cuando": 0.0, "modelos": []}


def _ollama_root(base_url: str) -> str:
    """http://127.0.0.1:11434/v1 → http://127.0.0.1:11434"""
    base = (base_url or DEFAULT_OPENAI_BASE_URL).strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base.rstrip("/")


def _ollama_audio_models(base_url: str, modelos: List[str]) -> List[str]:
    """
    Modelos del servidor que aceptan audio. Ollama lo declara en /api/show
    (capabilities incluye "audio"); si el servidor no lo dice, se adivina por
    el nombre para no bloquear al usuario.
    """
    root = _ollama_root(base_url)
    ahora = time.time()
    if (_AUDIO_MODELS_CACHE["base"] == root
            and ahora - float(_AUDIO_MODELS_CACHE["cuando"]) < 30.0):
        cache = _AUDIO_MODELS_CACHE["modelos"]
        if cache:
            return [m for m in cache if m in modelos]

    capaces: List[str] = []
    for nombre in modelos:
        acepta = None
        try:
            datos = json.dumps({"model": nombre, "name": nombre}).encode("utf-8")
            req = urllib.request.Request(f"{root}/api/show", data=datos,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                info = json.loads(resp.read().decode("utf-8", "replace"))
            caps = info.get("capabilities") or []
            if isinstance(caps, list) and caps:
                acepta = any(str(c).lower() == "audio" for c in caps)
        except Exception:
            acepta = None
        if acepta is None:  # servidor antiguo o sin capabilities
            acepta = "audio" in nombre.lower() or "omni" in nombre.lower()
        if acepta:
            capaces.append(nombre)
    _AUDIO_MODELS_CACHE.update({"base": root, "cuando": ahora, "modelos": capaces})
    return capaces


def _wav_bytes(muestras, rate: int = 16000) -> bytes:
    """float32 [-1,1] → WAV mono 16 bits en memoria."""
    import io
    import numpy as np  # type: ignore

    arr = np.asarray(muestras, dtype=np.float32).reshape(-1)
    pcm = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buffer.getvalue()


def _av_clip_wav(path: Path, segundos: float, desde: float) -> Optional[bytes]:
    """
    Trozo de audio usando PyAV directamente (viene con faster-whisper), sin
    FFmpeg. Se decodifica desde el principio y se recortan las muestras: buscar
    dentro del archivo (seek) falla en algunos contenedores M4A.
    """
    try:
        import av  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return None
    try:
        cont = av.open(str(path))
    except Exception:
        return None
    try:
        if not cont.streams.audio:
            return None
        flujo = cont.streams.audio[0]
        remuestreo = av.AudioResampler(format="s16", layout="mono", rate=16000)
        partes: List[Any] = []
        total = 0
        necesarios = int((desde + segundos) * 16000)
        for frame in cont.decode(flujo):
            for salida in remuestreo.resample(frame):
                arr = salida.to_ndarray().reshape(-1)
                if arr.size:
                    partes.append(arr)
                    total += int(arr.size)
            if total >= necesarios:
                break
        for resto in remuestreo.resample(None):
            arr = resto.to_ndarray().reshape(-1)
            if arr.size:
                partes.append(arr)
                total += int(arr.size)
        if not partes:
            return None
        audio = np.concatenate(partes)
        i0 = int(desde * 16000)
        audio = audio[i0:i0 + int(segundos * 16000)]
        if not audio.size:
            return None
        return _wav_bytes(audio.astype(np.float32) / 32768.0)
    except Exception:
        return None
    finally:
        try:
            cont.close()
        except Exception:
            pass


def clip_audio_wav(path: Path, segundos: float = 30.0, desde: float = 0.0) -> bytes:
    """
    Devuelve un trozo del audio como WAV 16 kHz mono en memoria.
    1) FFmpeg (rápido y exacto en cualquier formato; recomendado).
    2) PyAV, que viene con faster-whisper (sirve para m4a/mp3/mp4 sin FFmpeg).
    3) WAV leído con la librería estándar.
    """
    segundos = max(3.0, min(180.0, float(segundos or 30)))
    desde = max(0.0, float(desde or 0))

    ffmpeg = find_ffmpeg("ffmpeg")  # 1) FFmpeg
    if ffmpeg:
        try:
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-nostdin", "-v", "error",
                 "-ss", f"{desde:.2f}", "-t", f"{segundos:.2f}", "-i", str(path),
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                 "-f", "wav", "pipe:1"],
                capture_output=True, timeout=180,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
            if proc.returncode == 0 and len(proc.stdout) > 2000:
                return proc.stdout
        except Exception:
            pass

    trozo_av = _av_clip_wav(path, segundos, desde)  # 2) PyAV
    if trozo_av:
        return trozo_av

    try:  # 3) WAV puro
        import numpy as np  # type: ignore

        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate() or 16000
            canales = wf.getnchannels() or 1
            ancho = wf.getsampwidth() or 2
            wf.setpos(min(int(desde * rate), max(0, wf.getnframes() - 1)))
            crudo = wf.readframes(int(segundos * rate))
        if ancho == 2:
            arr = np.frombuffer(crudo, dtype="<i2").astype(np.float32) / 32768.0
        elif ancho == 1:
            arr = (np.frombuffer(crudo, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        else:
            arr = np.frombuffer(crudo, dtype="<i4").astype(np.float32) / 2147483648.0
        if canales > 1:
            arr = arr.reshape(-1, canales).mean(axis=1)
        if rate != 16000 and arr.size:
            destino = int(arr.size * 16000 / rate)
            idx = (np.arange(destino) * (rate / 16000.0)).astype(int)
            arr = arr[np.clip(idx, 0, arr.size - 1)]
        return _wav_bytes(arr)
    except Exception as exc:
        raise HTTPException(
            400,
            "No se pudo leer el audio. Instala FFmpeg (winget install Gyan.FFmpeg) "
            f"o faster-whisper para formatos comprimidos. Detalle: {exc}",
        )


def ollama_audio_chat(base_url: str, modelo: str, wav: bytes, pregunta: str = "",
                      timeout: float = 300.0) -> Tuple[str, str]:
    """
    Manda el trozo de audio a Ollama y devuelve (texto, via).
    Primero la ruta OpenAI con «input_audio» (la única que acepta audio en
    Ollama) y, si el servidor no la soporta, /v1/audio/transcriptions.
    """
    import base64

    root = _ollama_root(base_url)
    texto = (pregunta or "").strip() or (
        "Escucha este fragmento de una reunión y describe en español, en 3 líneas: "
        "idioma, cuántas personas hablan y de qué tratan."
    )
    b64 = base64.b64encode(wav).decode("ascii")
    cuerpo = {
        "model": modelo,
        "stream": False,
        "temperature": 0,
        "messages": [
            {"role": "system",
             "content": "Eres un asistente que escucha audio real. Responde en español, "
                        "concreto y sin inventar: si no se entiende, dilo."},
            {"role": "user", "content": [
                {"type": "text", "text": texto},
                {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}},
            ]},
        ],
    }
    datos = json.dumps(cuerpo).encode("utf-8")
    req = urllib.request.Request(f"{root}/v1/chat/completions", data=datos,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        eleccion = (payload.get("choices") or [{}])[0]
        mensaje = eleccion.get("message") or {}
        contenido = mensaje.get("content")
        if isinstance(contenido, list):  # algunos servidores devuelven partes
            contenido = " ".join(str(c.get("text", "")) for c in contenido
                                 if isinstance(c, dict))
        respuesta = str(contenido or "").strip()
        if respuesta:
            return respuesta, "chat"
        raise RuntimeError("El servidor respondió vacío.")
    except urllib.error.HTTPError as exc:
        detalle = ""
        try:
            detalle = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code not in (400, 404, 405, 422, 500):
            raise HTTPException(502, f"Ollama respondió {exc.code}: {detalle}")
    except Exception:
        pass

    # Alternativa: ruta de transcripción tipo OpenAI (algunos servidores solo
    # exponen esta para modelos de audio).
    limite = "----transcriptor" + uuid.uuid4().hex
    partes: List[bytes] = []
    for nombre, valor in (("model", modelo), ("response_format", "json")):
        partes.append(f"--{limite}\r\nContent-Disposition: form-data; name=\"{nombre}\""
                      f"\r\n\r\n{valor}\r\n".encode("utf-8"))
    partes.append(f"--{limite}\r\nContent-Disposition: form-data; name=\"file\";"
                  f" filename=\"trozo.wav\"\r\nContent-Type: audio/wav\r\n\r\n".encode("utf-8"))
    partes.append(wav)
    partes.append(f"\r\n--{limite}--\r\n".encode("utf-8"))
    cuerpo_mp = b"".join(partes)
    req2 = urllib.request.Request(
        f"{root}/v1/audio/transcriptions", data=cuerpo_mp,
        headers={"Content-Type": f"multipart/form-data; boundary={limite}"})
    try:
        with urllib.request.urlopen(req2, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return str(payload.get("text") or "").strip(), "transcripciones"
    except Exception as exc:
        raise HTTPException(
            502,
            "El servidor de Ollama no aceptó el audio. Necesitas Ollama reciente "
            "(con soporte de audio) y un modelo que escuche, por ejemplo: "
            f"ollama pull {modelo}. Detalle: {exc}",
        )


def call_openai_compatible(
    prompt: str,
    system: str,
    base_url: str = DEFAULT_OPENAI_BASE_URL,
    model: str = DEFAULT_OPENAI_MODEL,
    max_tokens: int = 2600,
    on_token=None,
) -> str:
    """
    Motor GRATUITO de mayor calidad: cualquier servidor local compatible con la
    API de OpenAI (Ollama, LM Studio, llama.cpp, Jan…). Nada sale del equipo.
    """
    base = (base_url or DEFAULT_OPENAI_BASE_URL).strip()
    model = (model or DEFAULT_OPENAI_MODEL).strip()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.25,
        "top_p": 0.9,
        "max_tokens": int(max_tokens),
        "stream": bool(on_token),
    }
    req = urllib.request.Request(
        _openai_endpoint(base),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
        method="POST",
    )
    if on_token:
        # ---- streaming SSE (Ollama / LM Studio / llama.cpp) --------------
        chunks: List[str] = []
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    delta = ((obj.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                    if delta:
                        chunks.append(delta)
                        try:
                            on_token(delta)
                        except Exception:
                            pass
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_http_error_openai(exc, base, model))
        except urllib.error.URLError as exc:
            ok, _m, msg = probe_openai_server(base, timeout=1.5)
            raise RuntimeError(msg if not ok else f"No se pudo conectar: {exc.reason}")
        text = "".join(chunks).strip()
        if not text:
            raise RuntimeError("El servidor local no devolvió texto. Revisa el modelo seleccionado.")
        return text

    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_http_error_openai(exc, base, model))
    except urllib.error.URLError as exc:
        ok, _m, msg = probe_openai_server(base, timeout=1.5)
        raise RuntimeError(msg if not ok else f"No se pudo conectar: {exc.reason}")

    try:
        choice = (payload.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or choice.get("text") or ""
    except Exception:
        text = ""
    text = (text or "").strip()
    if not text:
        raise RuntimeError("El servidor local no devolvió texto. Revisa el modelo seleccionado.")
    return text


def call_local_llm(prompt: str, system: str, model_id: str, max_new_tokens: int = 1400,
                   on_token=None) -> str:
    """
    IA 100 % local en CPU (transformers + Qwen2.5-Instruct).
    Antes de cargar el LLM se libera el modelo de Whisper: nunca hay dos
    modelos pesados en RAM al mismo tiempo.
    """
    model_id = model_id or "Qwen/Qwen2.5-1.5B-Instruct"
    try:
        import torch  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Falta la IA local de Hugging Face (torch, transformers, accelerate). "
            "Dos formas de arreglarlo, elige una:\n"
            "  · Ejecuta instalar.bat y elige la opción 1 en la etapa "
            "«IA para redactar el acta» (lo hace solo).\n"
            "  · O en una consola de la carpeta de la app: " + COMANDO_IA_LOCAL + "\n"
            "Si prefieres no descargar nada, instala Ollama (gratis), ejecuta "
            "ollama pull qwen2.5:7b-instruct y elige el motor «Ollama / LM Studio»."
            f"\n(detalle técnico: {exc})"
        )

    with _LOCAL_LLM_LOCK:
        if _LOCAL_LLM.get("model") is None or _LOCAL_LLM.get("name") != model_id:
            _LOCAL_LLM["model"] = None
            _LOCAL_LLM["tok"] = None
            gc.collect()
            unload_whisper()
            local = model_is_local(model_id)
            ctx = offline_only() if local else allow_download()
            with ctx:
                tok = AutoTokenizer.from_pretrained(model_id, cache_dir=str(HUB_DIR))
                model = AutoModelForCausalLM.from_pretrained(
                    model_id,
                    cache_dir=str(HUB_DIR),
                    torch_dtype=torch.float32,
                    low_cpu_mem_usage=True,
                    attn_implementation="eager",
                )
            model.eval()
            _LOCAL_LLM.update({"name": model_id, "model": model, "tok": tok})
            configure_offline_mode()

        tok = _LOCAL_LLM["tok"]
        model = _LOCAL_LLM["model"]

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        try:
            input_ids = tok.apply_chat_template(
                messages, add_generation_prompt=True, return_tensors="pt"
            )
        except Exception:
            full = f"{system}\n\n{prompt}\n\nACTA:"
            input_ids = tok(full, return_tensors="pt").input_ids
        # Recorta el contexto a ~9 000 tokens para que quepa holgadamente en CPU
        max_ctx = 9000
        if input_ids.shape[-1] > max_ctx:
            input_ids = input_ids[:, -max_ctx:]

        gen_kwargs = dict(
            max_new_tokens=int(max_new_tokens),
            do_sample=False,
            repetition_penalty=1.08,
            pad_token_id=tok.eos_token_id,
        )
        if on_token:
            from transformers import TextIteratorStreamer  # type: ignore
            streamer = TextIteratorStreamer(tok, skip_prompt=True, skip_special_tokens=True)
            gen_kwargs["streamer"] = streamer
            holder: Dict[str, Any] = {}

            def _run():
                try:
                    with torch.no_grad():
                        holder["out"] = model.generate(input_ids, **gen_kwargs)
                except Exception as exc:  # pragma: no cover
                    holder["err"] = exc

            th = threading.Thread(target=_run, daemon=True)
            th.start()
            parts: List[str] = []
            for piece in streamer:
                if piece:
                    parts.append(piece)
                    try:
                        on_token(piece)
                    except Exception:
                        pass
            th.join(timeout=5)
            if holder.get("err"):
                raise holder["err"]
            text = "".join(parts).strip()
            gc.collect()
            if not text:
                raise RuntimeError("El modelo local no devolvió texto.")
            return text

        with torch.no_grad():
            out = model.generate(input_ids, **gen_kwargs)
        gen = out[0][input_ids.shape[-1]:]
        text = tok.decode(gen, skip_special_tokens=True).strip()
        del out, gen, input_ids
        gc.collect()
        if not text:
            raise RuntimeError("El modelo local no devolvió texto.")
        return text


# -----------------------------------------------------------------------------
# 13.1 ACTA POR MAP-REDUCE (reuniones de 3 h sin perder detalle)
# -----------------------------------------------------------------------------
ACTA_BLOCK_SECONDS = 1200        # ~20 minutos por bloque (map)
ACTA_BLOCK_CHARS = 14000         # tope de caracteres por bloque (tramos de ~20 min)
ACTA_DIRECT_CHARS = 12000        # por debajo de esto se hace una sola pasada

MAP_SYSTEM_PROMPT = (
    "Eres un analista de reuniones meticuloso. Tu única tarea es extraer datos "
    "VERIFICABLES de un tramo de transcripción. No inventes nada: si un dato no "
    "está en el tramo, no lo escribas."
)

MAP_INSTRUCTIONS = (
    "Lee este tramo de la transcripción y devuelve SOLO viñetas, agrupadas así. Cada viñeta "
    "debe estar redactada de forma que sirva tal cual para escribir el acta después "
    "(sujeto + hecho + cifra), no como palabra clave:\n"
    "• TEMAS: qué se trató, con el desarrollo mínimo (una o dos líneas por tema, con sus "
    "cifras y nombres)\n"
    "• ACUERDOS/DECISIONES: qué se decidió y quién se responsabiliza (si se nombra)\n"
    "• CIFRAS Y NOMBRES: montos, cantidades, fechas, plazos, empresas y personas, "
    "escritos EXACTAMENTE como aparecen\n"
    "• PENDIENTES: lo que queda por hacer\n"
    "Cada viñeta debe terminar con la marca de tiempo entre corchetes del momento en que "
    "ocurre, por ejemplo [01:12:35]. Máximo 14 viñetas. Sin introducción ni cierre."
)

CITATION_RULE = (
    "IMPORTANTE: al final de cada acuerdo, decisión, cifra o dato concreto añade su marca "
    "de tiempo entre corchetes con el formato [HH:MM:SS] o [MM:SS], tomada del material. "
    "No inventes marcas que no aparezcan."
)

ACUERDOS_JSON_RULE = (
    "Al final del documento, después de una línea con solo tres guiones (---), añade un "
    "bloque ```json con esta forma exacta:\n"
    '{"acuerdos": [{"acuerdo": "...", "responsable": "...", "fecha": "...", "estatus": "..."}], '
    '"pendientes": [{"tema": "...", "responsable": "...", "fecha": "..."}], '
    '"siguientes_pasos": ["..."]}\n'
    "Usa cadenas vacías cuando el dato no aparezca en el material. No repitas el documento ahí."
)


# --- 13.x Acta por secciones (map-reduce jerárquico) ------------------------
# Una sola pasada con un modelo pequeño devuelve un acta pobre (o truncada). Se
# hace como un secretario real: primero el índice, después sección por sección,
# y al final se extraen acuerdos y pendientes.
ACTA_SECCIONES = True
ACTA_MAX_SECCIONES = 8
ACTA_SECCION_CHARS = 18000   # material máximo que ve cada sección
ACTA_SECCIONES_LOCAL = 5     # con IA local en CPU: menos secciones y más cortas
ACTA_SECCION_TOKENS_LOCAL = 700

_PLACEHOLDER_RX = re.compile(
    r"\[\s*(?:nombre|nombres|fecha|lugar|hora|cargo|rol|puesto|participante|empresa|"
    r"monto|cifra|dato|por\s+definir|pendiente|completar|xxx)[^\]\n]{0,60}\]",
    re.IGNORECASE,
)


def limpiar_acta(texto: str) -> str:
    """
    Quita los huecos tipo «[Nombre Presidencia]» que dejan los modelos pequeños,
    normaliza espacios y elimina líneas repetidas. Nunca toca las marcas de
    tiempo [HH:MM:SS].
    """
    t = _PLACEHOLDER_RX.sub("", texto or "")
    t = re.sub(r"\(\s*\)|\[\s*\]", "", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r" +([,.;:])", r"\1", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    lineas: List[str] = []
    vistos: set = set()
    for linea in t.split("\n"):
        clave = linea.strip().lower()
        if clave and len(clave) > 60 and clave in vistos:
            continue          # viñeta repetida
        if clave:
            vistos.add(clave)
        lineas.append(linea)
    return "\n".join(lineas).strip()


def _plan_acta_secciones(material: str, title: str, engine: str, cfg: Dict[str, Any],
                          warnings: List[str]) -> List[Dict[str, str]]:
    """Pide al modelo el índice del acta (4 a 8 secciones temáticas)."""
    prompt = (
        (f"Reunión: {title}\n\n" if title else "")
        + "A partir del MATERIAL, propón el índice del acta. Devuelve SOLO líneas con el "
          "formato exacto:\n"
          "1. TÍTULO EN MAYÚSCULAS — qué cubre esa sección en una frase\n"
          "Reglas: entre 4 y 8 secciones; agrupa temas relacionados (no una sección por "
          "palabra suelta); en orden de la reunión; la primera debe tratar la instalación de "
          "la sesión, el quórum y los asistentes; la última se titula ACUERDOS Y COMPROMISOS. "
          "Sin introducción ni cierre.\n\nMATERIAL:\n" + material[:7000]
    )
    try:
        txt = _engine_tokens(prompt, SECRETARY_SYSTEM_PROMPT, engine, cfg, 500, None)
    except Exception as exc:
        warnings.append(f"No se pudo planear el índice del acta: {exc}")
        return []
    secciones: List[Dict[str, str]] = []
    vistos: set = set()
    for linea in (txt or "").splitlines():
        linea = linea.strip().strip("*").strip()
        m = re.match(r"^(?:\d+[.)\-]\s*)(.+?)\s*(?:—|–|-{1,2}\s|:\s)(.*)$", linea)
        titulo, detalle = (m.group(1), m.group(2)) if m else (linea, "")
        titulo = re.sub(r"^[#*\s]+|[#*\s]+$", "", titulo or "").strip()
        titulo = re.sub(r"\*\*", "", titulo)
        if len(titulo) < 4 or len(titulo) > 110:
            continue
        clave = titulo.lower()[:40]
        if clave in vistos:
            continue
        vistos.add(clave)
        secciones.append({"titulo": titulo, "detalle": (detalle or "").strip()})
        if len(secciones) >= (ACTA_SECCIONES_LOCAL if engine == "local" else ACTA_MAX_SECCIONES):
            break
    return secciones


def _bloque_json_acuerdos(material: str, engine: str, cfg: Dict[str, Any],
                          warnings: List[str]) -> str:
    """Pide el bloque JSON de acuerdos/pendientes que usa la interfaz."""
    prompt = (
        "Del siguiente documento extrae los acuerdos, los pendientes y las acciones "
        "inmediatas.\n" + ACUERDOS_JSON_RULE.replace(
            "Al final del documento, después de una línea con solo tres guiones (---), añade un ",
            "Responde únicamente con un ") + "\n\nDOCUMENTO:\n" + material[:14000]
    )
    try:
        return _engine_tokens(prompt, SECRETARY_SYSTEM_PROMPT, engine, cfg, 800, None)
    except Exception as exc:
        warnings.append(f"No se pudieron extraer los acuerdos con IA: {exc}")
        return ""


# --- Plantilla institucional del acta (B1) ---------------------------------
PLANTILLA_DEFECTO: Dict[str, str] = {
    "organizacion": "",
    "organo": "Junta Directiva",
    "ciudad": "",
    "presidencia": "",
    "presidencia_cargo": "",
    "secretaria": "",
    "secretaria_cargo": "",
}
_PLANTILLA_CAMPOS = tuple(PLANTILLA_DEFECTO)


def leer_plantilla() -> Dict[str, str]:
    """Datos fijos de la organización que encabezan y firman todas las actas."""
    cfg = read_json(IA_CONFIG_FILE, {})
    pl = cfg.get("plantilla") if isinstance(cfg, dict) else None
    out = dict(PLANTILLA_DEFECTO)
    if isinstance(pl, dict):
        for clave in _PLANTILLA_CAMPOS:
            valor = pl.get(clave)
            if isinstance(valor, str):
                out[clave] = valor.strip()[:120]
    return out


def guardar_plantilla(datos: Dict[str, Any]) -> Dict[str, str]:
    cfg = _read_ia_config()
    pl = dict(PLANTILLA_DEFECTO)
    for clave in _PLANTILLA_CAMPOS:
        valor = (datos or {}).get(clave)
        if isinstance(valor, str):
            pl[clave] = valor.strip()[:120]
    cfg["plantilla"] = pl
    _write_ia_config(cfg)
    return pl


def _articulo_organo(organo: str) -> str:
    """«DE LA Junta Directiva» / «DEL Comité de Calidad»."""
    o = (organo or "").strip().lower()
    return "DEL" if o.startswith(("comité", "comite", "consejo", "club", "directorio",
                                  "órgano", "organo", "cabildo")) else "DE LA"


def _encabezado_acta(title: str = "", plantilla: Optional[Dict[str, str]] = None) -> str:
    pl = plantilla or leer_plantilla()
    organo = (pl.get("organo") or "").strip() or "Junta Directiva"
    titulo = f"**ACTA DE SESIÓN {_articulo_organo(organo)} {organo.upper()}**"
    extra = [x for x in ((pl.get("organizacion") or "").strip(),
                         (title or "").strip()) if x]
    if extra:
        titulo += "\n\n" + " · ".join(extra)
    return titulo


def _cierre_acta(plantilla: Optional[Dict[str, str]] = None) -> str:
    """Pie de firma fijo: usa la plantilla si el usuario la rellenó."""
    pl = plantilla or leer_plantilla()
    presi = (pl.get("presidencia") or "").strip() or "______________________________"
    secre = (pl.get("secretaria") or "").strip() or "______________________________"
    c1 = (pl.get("presidencia_cargo") or "").strip()
    c2 = (pl.get("secretaria_cargo") or "").strip()
    filas = f"| {presi} | {secre} |\n"
    if c1 or c2:
        filas += f"| {c1 or 'Presidencia'} | {c2 or 'Secretaría Técnica'} |\n"
    else:
        filas += "| Presidencia | Secretaría Técnica |\n"
    lugar = (pl.get("ciudad") or "").strip()
    linea_lugar = (f"\nEn {lugar}, " if lugar else "\n")
    return (
        "\n\n---\n\n**CIERRE**\n"
        + linea_lugar
        + "no habiendo otro asunto que tratar, la Presidencia dio por concluida la sesión, "
          "firmando al calce quienes en ella intervinieron.\n\n"
          "| Firma | Cargo |\n| --- | --- |\n" + filas
    )


def split_acta_blocks(segments: List[Dict[str, Any]],
                      block_seconds: int = ACTA_BLOCK_SECONDS,
                      max_chars: int = ACTA_BLOCK_CHARS) -> List[Dict[str, Any]]:
    """Divide la transcripción en tramos de ~20 min (o ~9 000 caracteres)."""
    blocks: List[Dict[str, Any]] = []
    cur: List[Dict[str, Any]] = []
    size = 0
    start_t = None
    for seg in segments:
        if start_t is None:
            start_t = float(seg.get("start") or 0)
        line = _seg_line(seg)
        cur.append(seg)
        size += len(line) + 1
        seg_end = float(seg.get("end") or seg.get("start") or 0)
        if size >= max_chars or (seg_end - start_t) >= block_seconds:
            blocks.append({"start": start_t, "end": seg_end, "segs": cur,
                           "text": "\n".join(_seg_line(x) for x in cur)})
            cur, size, start_t = [], 0, None
    if cur:
        blocks.append({"start": start_t or 0.0,
                       "end": float(cur[-1].get("end") or 0),
                       "segs": cur,
                       "text": "\n".join(_seg_line(x) for x in cur)})
    for i, b in enumerate(blocks, start=1):
        b["index"] = i
        b["label"] = f"{human_time(b['start'])} – {human_time(b['end'])}"
    return blocks


def _digits(text: str) -> List[str]:
    """Normaliza cifras para comparar (sin separadores de miles ni decimales)."""
    out = []
    for tok in re.findall(r"\d[\d.,]*", text or ""):
        d = re.sub(r"[.,]", "", tok)
        d = d.lstrip("0") or "0"
        if len(d) >= 1:
            out.append(d)
    return out


def _valores_numericos(texto: str) -> set:
    """
    Valores numéricos de un texto admitiendo las dos convenciones (1.500 = 1500 y
    1.5 = 1,5). Se usa para emparejar cifras del acta con la transcripción.
    """
    vals: set = set()
    for tok in re.findall(r"\d[\d.,]*", texto or ""):
        tok = tok.rstrip(".,")
        for cand in {re.sub(r"[.,]", "", tok), tok.replace(",", ".")}:
            try:
                vals.add(round(float(cand), 4))
            except ValueError:
                continue
    return vals


def verify_figures(acta_text: str, transcript_text: str) -> Dict[str, Any]:
    """
    Comprueba que cada cifra del acta exista literalmente en la transcripción.
    Devuelve las que no aparecen (posibles invenciones del modelo).
    """
    acta_nums = set(_digits(acta_text))
    tr_nums = set(_digits(transcript_text))
    if not acta_nums:
        return {"checked": 0, "missing": [], "ok": True}
    # solo cifras relevantes: montos, porcentajes y números de 4 o más dígitos
    signif: set = set()
    for mt in re.finditer(r"\$\s*[\d.,]+|\d{1,3}(?:[.,]\d{3})+|\d[\d.,]*\d\s*%|\b\d{4,}\b",
                          acta_text):
        signif |= set(_digits(mt.group(0)))
    missing = sorted(n for n in signif if n not in tr_nums
                     and not (len(n) == 4 and 1900 <= int(n) <= 2100))
    # el acta puede escribir «$1,200,000 pesos» donde la transcripción dijo «1.2 millones»:
    # esas equivalencias no son cifras inventadas
    if missing:
        valores = _valores_numericos(transcript_text)
        missing = [n for n in missing
                   if not any(round(c, 4) in valores for c in
                              (float(n) / 1000.0, float(n) / 1000000.0))]
    missing = missing[:25]
    # las marcas de tiempo [HH:MM:SS] no cuentan como cifras del acta
    tiempos = set(_digits(" ".join(re.findall(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", acta_text))))
    missing = [m for m in missing if m not in tiempos]
    return {"checked": len(acta_nums), "missing": missing[:25], "ok": not missing}


def parse_agreements(text: str) -> Tuple[Dict[str, Any], str]:
    """
    Extrae el bloque JSON de acuerdos que pide el prompt y lo quita del texto
    visible. Si el modelo no lo devuelve, se cae al clasificador por patrones.
    """
    clean = text or ""
    data: Dict[str, Any] = {}
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean, re.S)
    raw = m.group(1) if m else ""
    if not raw:
        # último objeto {...} que contenga "acuerdos"
        for cand in re.finditer(r"\{.*\}", clean, re.S):
            if '"acuerdos"' in cand.group(0):
                raw = cand.group(0)
    if raw:
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                data = obj
                clean = clean.replace(raw, "")
                clean = re.sub(r"```(?:json)?\s*```", "", clean)
                clean = re.sub(r"\n-{3,}\s*$", "", clean.strip())
        except Exception:
            data = {}
    acuerdos = []
    for item in data.get("acuerdos") or []:
        if isinstance(item, dict):
            acuerdos.append({
                "acuerdo": str(item.get("acuerdo") or "").strip(),
                "responsable": str(item.get("responsable") or "").strip(),
                "fecha": str(item.get("fecha") or "").strip(),
                "estatus": str(item.get("estatus") or "").strip(),
            })
        elif isinstance(item, str) and item.strip():
            acuerdos.append({"acuerdo": item.strip(), "responsable": "", "fecha": "", "estatus": ""})
    pendientes = []
    for item in data.get("pendientes") or []:
        if isinstance(item, dict):
            pendientes.append({
                "tema": str(item.get("tema") or item.get("pendiente") or "").strip(),
                "responsable": str(item.get("responsable") or "").strip(),
                "fecha": str(item.get("fecha") or "").strip(),
            })
        elif isinstance(item, str) and item.strip():
            pendientes.append({"tema": item.strip(), "responsable": "", "fecha": ""})
    siguientes = [str(x).strip() for x in (data.get("siguientes_pasos") or []) if str(x).strip()]
    if not acuerdos:
        cls = quick_classify(clean)
        acuerdos = [{"acuerdo": a, "responsable": "", "fecha": "", "estatus": ""}
                    for a in cls.get("acuerdos", [])]
        pendientes = pendientes or [{"tema": p, "responsable": "", "fecha": ""}
                                    for p in cls.get("pendientes", [])]
        siguientes = siguientes or list(cls.get("siguientes", []))
    return {"acuerdos": acuerdos, "pendientes": pendientes,
            "siguientes_pasos": siguientes}, clean.strip()


def _engine_tokens(prompt: str, system: str, engine: str, cfg: Dict[str, Any],
                   max_tokens: int, on_token) -> str:
    """Lanza el motor elegido en un hilo y va entregando tokens por callback."""
    if engine == "gemini":
        return call_gemini(prompt, system, cfg.get("api_key") or "", max_tokens=max_tokens,
                           on_token=on_token)
    if engine == "openai":
        return call_openai_compatible(
            prompt, system,
            base_url=cfg.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL,
            model=cfg.get("openai_model") or DEFAULT_OPENAI_MODEL,
            max_tokens=max_tokens, on_token=on_token,
        )
    meta = LOCAL_MODELS.get(cfg.get("local_model") or "", {})
    # Antes se ignoraba max_tokens y el acta local se cortaba siempre en ~1 400 token:
    # por eso salía tan corta. Ahora se respeta el límite del formato pedido.
    tope_modelo = int(meta.get("max_new") or 1200)
    pedido = int(max_tokens or 0)
    tope = min(pedido, tope_modelo) if pedido > 0 else tope_modelo
    return call_local_llm(prompt, system, cfg.get("local_model") or "Qwen/Qwen2.5-1.5B-Instruct",
                          max_new_tokens=max(256, tope), on_token=on_token)


def _emitir_engine(prompt: str, system: str, engine: str, cfg: Dict[str, Any],
                   max_tokens: int, stream: bool):
    """
    Generador: lanza el motor en un hilo, emite ("token", {...}) mientras escribe y
    devuelve el texto completo (valor de retorno del generador).
    """
    if not stream:
        try:
            return _engine_tokens(prompt, system, engine, cfg, max_tokens, None) or ""
        except Exception as exc:
            if engine == "local":
                unload_local_llm()
            raise exc
    tokens: "queue.Queue[Tuple[str, Any]]" = queue.Queue()

    def _worker():
        try:
            txt = _engine_tokens(prompt, system, engine, cfg, max_tokens,
                                 lambda ch: tokens.put(("t", ch)))
            tokens.put(("ok", txt))
        except Exception as exc:  # pragma: no cover
            tokens.put(("err", exc))

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    collected: List[str] = []
    while True:
        kind, val = tokens.get()
        if kind == "t":
            collected.append(val)
            yield ("token", {"text": val})
        elif kind == "ok":
            th.join(timeout=2)
            return (val or "".join(collected) or "").strip()
        else:
            if engine == "local":
                unload_local_llm()
            raise val


def _acta_por_secciones(material: str, title: str, ctx_bits: List[str], engine: str,
                        cfg: Dict[str, Any], warnings: List[str], stream: bool):
    """
    Generador: redacta el acta sección por sección (índice → secciones → acuerdos) y
    devuelve el documento ensamblado. Devuelve "" si no se pudo planear el índice.
    """
    plan = _plan_acta_secciones(material, title, engine, cfg, warnings)
    if not plan:
        return ""

    total = len(plan)
    plantilla = leer_plantilla()
    encabezado = _encabezado_acta(title, plantilla)
    partes: List[str] = [encabezado]
    exitosas: List[Tuple[int, str, str]] = []
    num = 1
    contexto = ("\n".join(ctx_bits) + "\n") if ctx_bits else ""

    for i, sec in enumerate(plan, 1):
        yield ("stage", {"stage": "secciones", "done": i - 1, "total": total,
                         "message": f"Redactando la sección {i}/{total}: {sec['titulo']}"})
        prompt = (
            "Estás escribiendo el acta de una reunión, sección por sección.\n"
            f"Sección {i} de {total}: «{sec['titulo']}»"
            + (f" — debe cubrir: {sec['detalle']}" if sec["detalle"] else "") + "\n\n"
            "Escribe SOLO el cuerpo de esta sección:\n"
            "• 2 a 4 párrafos en tercera persona, con los hechos, las cifras exactas y los "
            "nombres tal como aparecen en el material.\n"
            "• Usa viñetas con la etiqueta en negritas y dos puntos cuando haya varias "
            "propuestas, partidas, proveedores o cifras (por ejemplo «* **Constructora X:** "
            "presentó … por $950,000 pesos»).\n"
            "• Di qué se decidió, quién queda a cargo y para cuándo, si se mencionó.\n"
            "• No escribas el título de la sección, no repitas otras secciones, no escribas "
            "el cierre ni inventes datos que no estén en el material.\n"
            "• Nada de corchetes con huecos tipo [Nombre], [Fecha] o [Monto].\n"
            "• Si es la primera sección, deja claro quién preside y quién da fe, usando los "
            "datos de la plantilla cuando el material no diga otra cosa.\n"
            + (("\n" + contexto) if contexto else "")
            + CITATION_RULE + "\n\nMATERIAL DE LA REUNIÓN:\n" + material[:ACTA_SECCION_CHARS]
        )
        cabecera = f"\n\n---\n\n**{num}. {sec['titulo']}**\n\n"
        yield ("token", {"text": cabecera})
        try:
            tope_sec = (ACTA_SECCION_TOKENS_LOCAL if engine == "local"
                        else AI_FORMATS["acta_completa"]["section_tokens"])
            cuerpo = yield from _emitir_engine(prompt, SECRETARY_SYSTEM_PROMPT, engine, cfg,
                                               tope_sec, stream)
        except Exception as exc:
            warnings.append(f"La sección «{sec['titulo']}» no se pudo redactar: {exc}")
            continue
        cuerpo = limpiar_acta(cuerpo or "")
        if not cuerpo.strip():
            warnings.append(f"La sección «{sec['titulo']}» quedó vacía.")
            continue
        # se numera solo lo que sí se redactó, para no dejar títulos vacíos
        exitosas.append((num, sec["titulo"], cuerpo.strip()))
        num += 1

    if not exitosas:
        return ""
    for k, titulo_sec, cuerpo in exitosas:
        partes.append(f"\n\n---\n\n**{k}. {titulo_sec}**\n\n{cuerpo}")
    partes.append(_cierre_acta(plantilla))

    documento = limpiar_acta("\n".join(partes).strip())
    yield ("stage", {"stage": "secciones", "done": total, "total": total,
                     "message": "Cerrando acuerdos y pendientes…"})
    json_txt = _bloque_json_acuerdos(documento, engine, cfg, warnings)
    ini, fin = json_txt.find("{"), json_txt.rfind("}")
    if ini != -1 and fin > ini:
        documento += ("\n\n---\n\n```json\n" + json_txt[ini:fin + 1].strip() + "\n```")
    return documento


def acta_pipeline(segments: List[Dict[str, Any]], fmt: str, engine: str,
                  cfg: Dict[str, Any], extra_notes: str = "", title: str = "",
                  participants: str = "", glossary: str = "",
                  acta_previa: str = "", stream: bool = True):
    """
    Generador de eventos del acta:
      ("stage", {...})  avance por fases/tramos (map → secciones → reduce)
      ("token", {...})  texto incremental para la vista previa en vivo
      ("done",  {...})  resultado final con acuerdos, cifras verificadas y avisos
    """
    started = time.time()
    fmt = fmt if fmt in AI_FORMATS else "acta_completa"
    if engine == "openai":
        unload_whisper()
    if not segments:
        raise RuntimeError("La transcripción está vacía; no hay material para redactar.")

    full_transcript = "\n".join(_seg_line(s) for s in segments)
    blocks = split_acta_blocks(segments)
    total_chars = sum(len(b["text"]) for b in blocks)
    warnings: List[str] = []
    partials: List[str] = []
    chars_used = 0

    if len(blocks) > 1 and total_chars > ACTA_DIRECT_CHARS:
        yield ("stage", {"stage": "map",
                         "message": f"Reunión larga: resumiendo {len(blocks)} tramos "
                                    f"de ~20 min antes de redactar…",
                         "done": 0, "total": len(blocks), "blocks": [
                             {"index": b["index"], "label": b["label"], "chars": len(b["text"])}
                             for b in blocks]})
        for b in blocks:
            prompt = (
                (f"Reunión: {title}\n" if title else "")
                + f"Tramo {b['index']} de {len(blocks)} ({b['label']}).\n\n"
                + MAP_INSTRUCTIONS
                + "\n\nTRAMO DE TRANSCRIPCIÓN:\n" + b["text"]
            )
            try:
                part = _engine_tokens(prompt, MAP_SYSTEM_PROMPT, engine, cfg, 900, None)
            except Exception as exc:
                warnings.append(f"El tramo {b['index']} no se pudo resumir: {exc}")
                part = ""
            chars_used += len(b["text"])
            if part.strip():
                partials.append(f"### TRAMO {b['index']} ({b['label']})\n{part.strip()}")
            yield ("stage", {"stage": "map", "done": b["index"], "total": len(blocks),
                             "message": f"Tramo {b['index']}/{len(blocks)} resumido "
                                        f"({b['label']})",
                             "partial": part.strip()[:2000]})
        material = "\n\n".join(partials)
        if not material.strip():
            material, chars_used = build_ai_transcript(segments, budget=12000)
            warnings.append("No se pudo resumir por tramos; se usó el extracto directo.")
    else:
        material, chars_used = build_ai_transcript(segments, budget=ACTA_DIRECT_CHARS)

    ctx_bits = []
    _pl = leer_plantilla()
    _datos_pl = [f"{etiqueta}: {_pl[campo]}" for campo, etiqueta in (
        ("organizacion", "Organización"), ("organo", "Órgano que sesiona"),
        ("ciudad", "Ciudad"), ("presidencia", "Quien preside"),
        ("presidencia_cargo", "Cargo de quien preside"),
        ("secretaria", "Secretaría Técnica"), ("secretaria_cargo", "Cargo de secretaría"),
    ) if _pl.get(campo)]
    if _datos_pl:
        ctx_bits.append("Plantilla institucional del acta (úsala para el encabezado, el quórum "
                        "y las firmas; no la contradigas): " + "; ".join(_datos_pl))
    if participants.strip():
        ctx_bits.append(f"Participantes conocidos (usa estos nombres exactos): {participants[:600]}")
    if glossary.strip():
        ctx_bits.append(f"Glosario de términos y siglas de la organización: {glossary[:1200]}")
    if extra_notes.strip():
        ctx_bits.append(f"Indicaciones adicionales del usuario: {extra_notes[:800]}")
    if acta_previa.strip():
        ctx_bits.append(
            "Acta anterior de la misma serie (solo acuerdos y pendientes). Úsala para dar "
            "seguimiento —«se dio seguimiento a lo acordado en la sesión del …»— y para "
            "señalar acuerdos incumplidos, repetidos o contradictorios. No copies su "
            "contenido ni inventes datos que no estén en el material de hoy:\n"
            + acta_previa[:2600]
        )

    prompt = (
        (f"Título o referencia de la reunión: {title}\n\n" if title else "")
        + f"{FORMAT_INSTRUCTIONS[fmt]}\n"
        + "\n".join(ctx_bits) + ("\n" if ctx_bits else "")
        + CITATION_RULE + "\n"
        + ACUERDOS_JSON_RULE + "\n\n"
        + ("MATERIAL: resúmenes verificables por tramo, en orden cronológico "
           "(cada viñeta trae su marca de tiempo):\n" if partials else
           "MATERIAL DE LA REUNIÓN (fragmentos cronológicos con marcas de tiempo y hablantes):\n")
        + material
        + "\n\nRedacta ahora el documento final en español. No menciones el material, ni el "
          "proceso de transcripción: entrega únicamente el documento solicitado."
    )

    yield ("stage", {"stage": "reduce",
                     "message": "Redactando el documento final…",
                     "chars_used": chars_used, "blocks": len(blocks)})

    if fmt == "acta_completa" and ACTA_SECCIONES and material.strip():
        yield ("stage", {"stage": "secciones", "done": 0, "total": 0,
                         "message": "Planeando las secciones del acta…"})
        final_text = yield from _acta_por_secciones(material, title, ctx_bits, engine, cfg,
                                                    warnings, stream)
        if not final_text.strip():
            warnings.append("No se pudo planear el acta por secciones; se redactó de una vez.")
            final_text = yield from _emitir_engine(
                prompt, SECRETARY_SYSTEM_PROMPT, engine, cfg,
                AI_FORMATS[fmt]["max_tokens"], stream)
    else:
        final_text = yield from _emitir_engine(
            prompt, SECRETARY_SYSTEM_PROMPT, engine, cfg,
            AI_FORMATS[fmt]["max_tokens"], stream)
    if engine == "local":
        unload_local_llm()

    final_text = (final_text or "").strip()
    if not final_text:
        raise RuntimeError("El motor de IA no devolvió texto. Prueba otro motor o formato.")
    structured, clean_text = parse_agreements(final_text)
    clean_text = limpiar_acta(clean_text)
    for clave in ("acuerdos", "pendientes"):
        for item in structured.get(clave) or []:
            for campo, valor in list(item.items()):
                if isinstance(valor, str):
                    item[campo] = limpiar_acta(valor)
    structured["siguientes_pasos"] = [limpiar_acta(p) for p in
                                      (structured.get("siguientes_pasos") or [])]
    figures = verify_figures(clean_text, full_transcript)
    if figures["missing"]:
        warnings.append(
            "Cifras del acta que no aparecen literalmente en la transcripción "
            f"(revísalas): {', '.join(figures['missing'][:10])}"
        )

    used_model = {
        "gemini": "gemini-2.5-flash",
        "openai": f"{cfg.get('openai_model') or DEFAULT_OPENAI_MODEL} (local)",
    }.get(engine, cfg.get("local_model") or "Qwen/Qwen2.5-1.5B-Instruct")

    yield ("done", {
        "text": clean_text,
        "raw_text": final_text,
        "engine": engine,
        "model": used_model,
        "format": fmt,
        "sections": len(re.findall(r"(?m)^\*\*\d+\.\s", clean_text)),
        "chars_used": chars_used,
        "blocks": len(blocks),
        "elapsed": round(time.time() - started, 1),
        "acuerdos": structured["acuerdos"],
        "pendientes": structured["pendientes"],
        "siguientes_pasos": structured["siguientes_pasos"],
        "figures": figures,
        "warnings": warnings,
    })


def summarize_with_ai(
    segments: List[Dict[str, Any]],
    fmt: str,
    engine: str,
    api_key: str = "",
    local_model: str = "Qwen/Qwen2.5-1.5B-Instruct",
    extra_notes: str = "",
    title: str = "",
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL,
    openai_model: str = DEFAULT_OPENAI_MODEL,
    participants: str = "",
    glossary: str = "",
    acta_previa: str = "",
) -> Dict[str, Any]:
    """Versión sin streaming: recorre el pipeline y devuelve el resultado final."""
    cfg = {"api_key": api_key, "local_model": local_model,
           "openai_base_url": openai_base_url, "openai_model": openai_model}
    result: Dict[str, Any] = {}
    for kind, payload in acta_pipeline(
        segments, fmt=fmt, engine=engine, cfg=cfg, extra_notes=extra_notes,
        title=title, participants=participants, glossary=glossary,
        acta_previa=acta_previa, stream=False,
    ):
        if kind == "done":
            result = payload
    if not result:
        raise RuntimeError("El motor de IA no devolvió resultado.")
    return result


def quick_classify(text: str) -> Dict[str, List[str]]:
    """Clasificador rápido por expresiones regulares en 3 columnas."""
    out = {"acuerdos": [], "pendientes": [], "siguientes": []}
    if not text:
        return out
    for raw in re.split(r"[\n•·]+", text):
        raw = (raw or "").strip()
        if raw.startswith("#"):  # encabezado markdown: no es un acuerdo
            continue
        line = re.sub(r"^\s*(?:[-*#>\d.)]+)\s*", "", raw).strip()
        line = re.sub(r"\*\*", "", line).strip()
        if len(line) < 8:
            continue
        low = line.lower()
        if re.search(r"\b(acuerd|aprob|autoriz|decid|resolv|se\s+acuerda|se\s+aprueba|unanimidad|votaci)", low):
            out["acuerdos"].append(line)
        elif re.search(r"\b(pendiente|seguimiento|queda\s+en|compromiso|plazo|antes\s+de|fecha\s+límite)", low):
            out["pendientes"].append(line)
        elif re.search(r"\b(siguiente|próxim|proxim|se\s+presentará|se\s+revisará|enviará|convocar)", low):
            out["siguientes"].append(line)
    for k in out:
        out[k] = out[k][:14]
    return out


# -----------------------------------------------------------------------------
# 14. EXPORTACIÓN A WORD (.docx)
# -----------------------------------------------------------------------------
def _docx_runs(par, texto: str, base_bold: bool = False) -> None:
    """Añade texto a un párrafo interpretando **negritas**."""
    for trozo in re.split(r"(\*\*.+?\*\*)", texto or ""):
        if not trozo:
            continue
        if trozo.startswith("**") and trozo.endswith("**") and len(trozo) > 4:
            run = par.add_run(trozo[2:-2])
            run.bold = True
        else:
            run = par.add_run(trozo)
            if base_bold:
                run.bold = True


def _docx_markdown(doc, texto: str, heading_level: int = 2) -> None:
    """
    Vuelca el markdown del acta en el .docx como documento de verdad: títulos,
    viñetas con negritas, listas numeradas y tablas (firmas, acuerdos).
    """
    try:
        from docx.shared import Pt  # type: ignore
    except Exception:  # pragma: no cover
        Pt = None
    tabla: List[str] = []
    primer_titulo = [True]

    def volcar_tabla() -> None:
        if not tabla:
            return
        filas = [f for f in tabla if not re.match(r"^\|[\s:|-]+\|?$", f)]
        celdas = [[c.strip() for c in f.strip().strip("|").split("|")] for f in filas]
        tabla.clear()
        if not celdas:
            return
        ancho = max(len(f) for f in celdas)
        t = doc.add_table(rows=0, cols=ancho)
        try:
            t.style = "Light Grid Accent 1"
        except Exception:
            pass
        for i, fila in enumerate(celdas):
            row = t.add_row().cells
            for j in range(ancho):
                celda = row[j]
                celda.text = ""
                par = celda.paragraphs[0]
                _docx_runs(par, fila[j] if j < len(fila) else "", base_bold=(i == 0))
        doc.add_paragraph()

    def titulo(txt: str) -> None:
        nivel = 1 if primer_titulo[0] else heading_level
        primer_titulo[0] = False
        doc.add_heading(txt.strip(), level=nivel)

    for linea in (texto or "").split("\n"):
        t = linea.rstrip()
        if t.strip().startswith("|"):
            tabla.append(t.strip())
            continue
        volcar_tabla()
        limpio = t.strip()
        if not limpio:
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", limpio):
            continue                     # separador de secciones: no se imprime
        m = re.match(r"^\*\*(.+)\*\*$", limpio) or re.match(r"^#{1,4}\s+(.*)$", limpio)
        if m:
            titulo(m.group(1))
            continue
        m = re.match(r"^[-*•]\s+(.*)$", limpio)
        if m:
            par = doc.add_paragraph(style="List Bullet")
            _docx_runs(par, m.group(1))
            continue
        m = re.match(r"^(\d+)[.)]\s+(.*)$", limpio)
        if m:
            par = doc.add_paragraph(style="List Number")
            _docx_runs(par, m.group(2))
            continue
        par = doc.add_paragraph()
        _docx_runs(par, limpio)
        if Pt:
            for run in par.runs:
                run.font.size = Pt(10.5)
    volcar_tabla()


def build_acta_docx(title: str = "", texto: str = "", plantilla: Optional[Dict[str, Any]] = None) -> Path:
    """Documento con SOLO el acta (sin transcripción), para enviar a terceros."""
    try:
        from docx import Document  # type: ignore
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore
        from docx.shared import Pt, Cm, RGBColor  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"python-docx no está instalado: {exc}")
    pl = plantilla or leer_plantilla()
    doc = Document()
    estilo = doc.styles["Normal"]
    estilo.font.name = "Calibri"
    estilo.font.size = Pt(10.5)
    for seccion in doc.sections:
        seccion.top_margin = Cm(2)
        seccion.bottom_margin = Cm(2)
        seccion.left_margin = Cm(2.2)
        seccion.right_margin = Cm(2.2)
    if pl.get("organizacion"):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(" " + pl["organizacion"].upper() + " ")
        r.bold = True
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
    _docx_markdown(doc, texto, heading_level=2)
    pie = doc.add_paragraph()
    pie.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = pie.add_run(f"Documento generado el {dt.datetime.now().strftime('%d/%m/%Y %H:%M')} · "
                     + (title or ""))
    fr.italic = True
    fr.font.size = Pt(8)
    fr.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    out = RESULT_DIR / f"acta_{safe_slug(title, 'documento-')}{dt.datetime.now():%H%M%S}.docx"
    doc.save(str(out))
    return out


def build_docx(
    job: Dict[str, Any],
    conclusion: str = "",
    speakers: Optional[List[Dict[str, Any]]] = None,
    title: str = "",
) -> Path:
    """Genera un .docx con metadatos, participantes, transcripción y acta."""
    try:
        from docx import Document  # type: ignore
        from docx.enum.text import WD_ALIGN_PARAGRAPH  # type: ignore
        from docx.shared import Pt, RGBColor, Cm  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"python-docx no está instalado: {exc}")

    result = job.get("result") or {}
    segments = result.get("segments") or []
    meta = result.get("info") or {}
    stats = job.get("stats") or {}
    doc_title = title or job.get("filename") or "Acta de reunión"

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    for section in doc.sections:
        section.top_margin = Cm(2)
        section.bottom_margin = Cm(2)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.2)

    h = doc.add_heading("Acta de Reunión", level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(doc_title)
    run.bold = True
    run.font.size = Pt(13)

    # --- Metadatos -----------------------------------------------------
    doc.add_heading("Datos del documento", level=1)
    tbl = doc.add_table(rows=0, cols=2)
    tbl.style = "Light Grid Accent 1"
    meta_rows = [
        ("Archivo original", job.get("filename", "—")),
        ("Fecha de procesamiento", (job.get("created") or now_iso()).replace("T", " ")),
        ("Duración del audio", human_time(job.get("duration") or meta.get("duration") or 0)),
        ("Idioma detectado", str(meta.get("language") or "—")),
        ("Modo de transcripción", str((job.get("options") or {}).get("mode", "—"))),
        ("Tiempo de proceso", human_time(stats.get("total_seconds") or 0)),
        ("Factor de velocidad", f"{stats.get('speed_factor', '—')}× tiempo real"),
        ("Palabras transcritas", str(stats.get("words", 0))),
        ("Fragmentos", str(len(segments))),
        ("Hablantes detectados", str(len(speakers or job.get("speakers") or []))),
    ]
    for k, v in meta_rows:
        cells = tbl.add_row().cells
        cells[0].text = k
        cells[1].text = str(v)
        for r in cells[0].paragraphs[0].runs:
            r.bold = True

    # --- Participantes -------------------------------------------------
    doc.add_heading("Participantes", level=1)
    sp_list = speakers or job.get("speakers") or []
    if sp_list:
        for i, sp in enumerate(sp_list, start=1):
            n_int = int(sp.get("count", 0) or 0)
            doc.add_paragraph(
                f"{i}. {sp.get('name', 'Hablante')} — {n_int} "
                f"{'intervención' if n_int == 1 else 'intervenciones'}, "
                f"{human_time(sp.get('seconds', 0))} de voz.",
                style="List Bullet",
            )
    else:
        doc.add_paragraph("No se identificaron hablantes de forma automática.")

    # --- Acta / conclusión --------------------------------------------
    if conclusion.strip():
        doc.add_heading("Acta / Conclusión", level=1)
        _docx_markdown(doc, conclusion, heading_level=2)

    # --- Transcripción -------------------------------------------------
    doc.add_page_break()
    doc.add_heading("Transcripción", level=1)
    sub = doc.add_paragraph()
    r = sub.add_run("Transcripción literal en idioma original, con marcas de tiempo y hablantes.")
    r.italic = True
    r.font.size = Pt(9)
    r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    last_speaker = None
    for seg in segments:
        speaker = (seg.get("speaker") or "").strip()
        line = doc.add_paragraph()
        line.paragraph_format.space_after = Pt(3)
        if speaker and speaker != last_speaker:
            ts = line.add_run(f"{speaker}  ")
            ts.bold = True
            ts.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
            last_speaker = speaker
        stamp = line.add_run(f"[{human_time(seg.get('start', 0))}] ")
        stamp.font.size = Pt(8)
        stamp.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
        line.add_run(seg.get("text", ""))

    # --- Pie ------------------------------------------------------------
    footer = doc.add_paragraph()
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = footer.add_run(
        "Documento generado con «Transcriptor de Audio y Generador de Actas» · "
        f"{dt.datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    fr.font.size = Pt(8)
    fr.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    safe_title = safe_slug(doc_title, "acta")
    out = TMP_DIR / f"{job['id']}_{safe_title}.docx"
    doc.save(str(out))
    return out


def build_plain_text(job: Dict[str, Any], conclusion: str = "", title: str = "") -> str:
    result = job.get("result") or {}
    segments = result.get("segments") or []
    stats = job.get("stats") or {}
    lines = [
        "=" * 78,
        f"ACTA / TRANSCRIPCIÓN · {title or job.get('filename') or 'Reunión'}",
        "=" * 78,
        f"Fecha de proceso : {(job.get('created') or now_iso()).replace('T', ' ')}",
        f"Duración audio   : {human_time(job.get('duration') or 0)}",
        f"Idioma           : {(result.get('info') or {}).get('language') or '—'}",
        f"Palabras         : {stats.get('words', 0)}",
        f"Tiempo de proceso: {human_time(stats.get('total_seconds') or 0)} ({stats.get('speed_factor', '—')}×)",
        "=" * 78,
        "",
    ]
    if conclusion.strip():
        lines += ["--- ACTA / CONCLUSIÓN ---", conclusion.strip(), "", ""]
    lines.append("--- TRANSCRIPCIÓN ---")
    current = None
    for seg in segments:
        sp = (seg.get("speaker") or "").strip()
        if sp and sp != current:
            lines.append("")
            lines.append(f"### {sp}")
            current = sp
        lines.append(f"[{human_time(seg.get('start', 0))}] {seg.get('text', '')}")
    return "\n".join(lines)


def build_srt(job: Dict[str, Any]) -> str:
    segments = (job.get("result") or {}).get("segments") or []
    chunks: List[str] = []
    for i, seg in enumerate(segments, start=1):
        speaker = (seg.get("speaker") or "").strip()
        text = f"{speaker}: {seg.get('text', '')}" if speaker else seg.get("text", "")
        chunks.append(
            f"{i}\n{fmt_srt_time(seg.get('start', 0))} --> {fmt_srt_time(seg.get('end', 0))}\n{text}\n"
        )
    return "\n".join(chunks)


def build_vtt(job: Dict[str, Any]) -> str:
    segments = (job.get("result") or {}).get("segments") or []
    out = ["WEBVTT", ""]
    for i, seg in enumerate(segments, start=1):
        speaker = (seg.get("speaker") or "").strip()
        text = f"{speaker}: {seg.get('text', '')}" if speaker else seg.get("text", "")
        out.append(f"{i}\n{fmt_vtt_time(seg.get('start', 0))} --> {fmt_vtt_time(seg.get('end', 0))}\n{text}\n")
    return "\n".join(out)


# -----------------------------------------------------------------------------
# 15. HISTORIAL, SESIONES Y RESPALDOS
# -----------------------------------------------------------------------------
MAX_HISTORY = 60
MAX_HISTORY_BYTES = 800 * 1024 * 1024  # 800 MB


def load_history() -> List[Dict[str, Any]]:
    data = read_json(HISTORY_FILE, {"entries": []})
    if isinstance(data, list):
        return data
    return data.get("entries", []) if isinstance(data, dict) else []


def save_history(entries: List[Dict[str, Any]]) -> None:
    write_json(HISTORY_FILE, {"entries": entries})


def _borrar_archivos_entrada(entry: Dict[str, Any]) -> List[str]:
    """Borra audio comprimido, transcripción y acta de una entrada del historial."""
    borrados: List[str] = []
    rutas: List[Path] = []
    if entry.get("media"):
        rutas.append(BASE_DIR / str(entry["media"]))
    job_id = str(entry.get("job_id") or "")
    if job_id:
        rutas += [RESULT_DIR / f"{job_id}.json", ruta_acta_json(job_id),
                  RESULT_DIR / f"{job_id}.ckpt.json"]
    for ruta in rutas:
        try:
            if ruta.exists():
                ruta.unlink()
                borrados.append(ruta.name)
        except Exception:
            continue
    return borrados


def _entradas_fuera(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Qué entradas se borrarían si entrara una reunión más: por número (las más
    antiguas) y por tamaño (las que ya no caben en el presupuesto de MB).
    Las protegidas nunca salen.
    """
    entradas = sorted(entries, key=lambda e: e.get("created", ""), reverse=True)
    protegidas = [e for e in entradas if e.get("protegido")]
    cupo = max(0, MAX_HISTORY - 1 - len(protegidas))
    conservadas = 0
    total = 0
    fuera: List[Dict[str, Any]] = []
    for entrada in entradas:
        if entrada.get("protegido"):
            continue
        size = int(entrada.get("media_bytes") or entrada.get("size") or 0)
        if conservadas < cupo and (total + size <= MAX_HISTORY_BYTES or conservadas == 0):
            conservadas += 1
            total += size
        else:
            fuera.append(entrada)
    return fuera


def evict_history(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Mantiene máximo MAX_HISTORY entradas y MAX_HISTORY_BYTES de audio comprimido.
    Nunca borra las marcadas como «protegidas»; al eliminar, borra también el
    audio, la transcripción y el acta (antes quedaban actas huérfanas en disco).
    """
    entradas = sorted(entries, key=lambda e: e.get("created", ""), reverse=True)
    protegidas = [e for e in entradas if e.get("protegido")]
    cupo = max(0, MAX_HISTORY - len(protegidas))
    conservadas = 0
    total = 0
    quedan: List[Dict[str, Any]] = []
    for entrada in entradas:
        if entrada.get("protegido"):
            quedan.append(entrada)
            continue
        size = int(entrada.get("media_bytes") or entrada.get("size") or 0)
        if conservadas < cupo and (total + size <= MAX_HISTORY_BYTES or conservadas == 0):
            conservadas += 1
            total += size
            quedan.append(entrada)
        else:
            _borrar_archivos_entrada(entrada)
    return sorted(quedan, key=lambda e: e.get("created", ""), reverse=True)


def compress_to_history(job: Dict[str, Any]) -> Optional[Path]:
    """Comprime el audio original a .m4a AAC 32 kbps en ./historial/."""
    src = Path(job.get("source_path", ""))
    if not src.exists():
        return None
    ffmpeg = find_ffmpeg("ffmpeg")
    if not ffmpeg:
        return None
    out = HISTORY_DIR / f"{job['id']}.m4a"
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-c:a", "aac", "-b:a", "32k", "-movflags", "+faststart", str(out),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=7200,
                       creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    except Exception:
        return None
    return out if out.exists() and out.stat().st_size > 2000 else None


def add_history_entry(job: Dict[str, Any]) -> Dict[str, Any]:
    media = compress_to_history(job)
    result = job.get("result") or {}
    info = result.get("info") or {}
    stats = job.get("stats") or {}
    entry = {
        "job_id": job["id"],
        "session_id": job.get("session") or "",
        "title": job.get("title") or Path(job.get("filename", "reunion")).stem,
        "filename": job.get("filename", ""),
        "created": now_iso(),
        "duration": round(float(job.get("duration") or 0), 1),
        "duration_human": human_time(job.get("duration") or 0),
        "language": info.get("language") or "",
        "mode": (job.get("options") or {}).get("mode", DEFAULT_MODE),
        "speakers": len(job.get("speakers") or []),
        "words": stats.get("words", 0),
        "media": str(media.relative_to(BASE_DIR)) if media else "",
        "media_bytes": media.stat().st_size if media else 0,
        "result_json": f"resultados/{job['id']}.json",
        # resumen para la lista del historial (se actualiza al guardar el acta)
        "has_acta": False,
        "acuerdos_n": 0,
        "pendientes_n": 0,
        "acta_version": 0,
        "serie": "",
        "protegido": False,
    }
    acta_previa = leer_acta_guardada(str(job["id"]))
    if acta_previa:
        entry.update({"has_acta": True, **_resumen_acuerdos(acta_previa),
                      "acta_version": acta_previa.get("version") or 0})
    entries = [e for e in load_history() if e.get("job_id") != job["id"]]
    entries.insert(0, entry)
    save_history(evict_history(entries))
    if media:
        # El audio comprimido sustituye al original de hasta 2 GB
        try:
            Path(job["source_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        job["media_path"] = str(media)
    return entry


def daily_backup() -> None:
    """Copia diaria de los JSON de estado en ./backups/AAAA-MM-DD/."""
    try:
        day = dt.date.today().isoformat()
        folder = BACKUP_DIR / day
        folder.mkdir(parents=True, exist_ok=True)
        for f in (HISTORY_FILE, SESSIONS_FILE, VOICES_FILE, IA_CONFIG_FILE):
            if f.exists():
                shutil.copy2(f, folder / f.name)
        # Conserva 14 días de respaldos
        for old in sorted([p for p in BACKUP_DIR.iterdir() if p.is_dir()])[:-14]:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass


def load_sessions() -> Dict[str, Any]:
    data = read_json(SESSIONS_FILE, {"sessions": {}})
    if not isinstance(data, dict):
        data = {"sessions": {}}
    data.setdefault("sessions", {})
    return data


def save_session_payload(session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    db = load_sessions()
    ses = db["sessions"].setdefault(session_id, {"jobs": {}, "created": now_iso()})
    job_id = str(payload.get("job_id") or "general")
    rec = ses["jobs"].setdefault(job_id, {})
    for key in ("labels", "conclusion", "summary", "title", "summary_format", "segments_rev"):
        if key in payload:
            rec[key] = payload[key]
    rec["updated"] = now_iso()
    ses["updated"] = now_iso()
    db["sessions"][session_id] = ses
    write_json(SESSIONS_FILE, db)
    daily_backup()
    return rec


def get_session(session_id: str) -> Dict[str, Any]:
    db = load_sessions()
    return db["sessions"].get(session_id, {"jobs": {}})


# -----------------------------------------------------------------------------
# 16. NOTIFICACIONES NATIVAS (Windows Toast vía PowerShell)
# -----------------------------------------------------------------------------
def notify_windows(title: str, message: str) -> None:
    if os.name != "nt":
        return

    def run():
        try:
            safe_title = (title or "").replace("'", "''")[:120]
            safe_msg = (message or "").replace("'", "''")[:240]
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null;"
                "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
                "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
                "$n = $t.GetElementsByTagName('text');"
                f"$n.Item(0).AppendChild($t.CreateTextNode('{safe_title}')) > $null;"
                f"$n.Item(1).AppendChild($t.CreateTextNode('{safe_msg}')) > $null;"
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($t);"
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                "'Transcriptor de Audio').Show($toast);"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                 "-Command", ps],
                capture_output=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


# -----------------------------------------------------------------------------
# 17. COLA DE TRABAJOS Y HILO TRABAJADOR
# -----------------------------------------------------------------------------
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()
WORK_QUEUE: "queue.Queue[str]" = queue.Queue()
UPLOADS: Dict[str, Dict[str, Any]] = {}
WORKER: Dict[str, Any] = {"thread": None, "stop": threading.Event()}


def public_job(job: Dict[str, Any]) -> Dict[str, Any]:
    speakers = []
    for sp in job.get("speakers") or []:
        speakers.append({k: v for k, v in sp.items() if k != "embedding"})
    return {
        "id": job["id"],
        "status": job.get("status", "queued"),
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
        "error": job.get("error", ""),
        "created": job.get("created"),
        "started": job.get("started"),
        "finished": job.get("finished"),
        "filename": job.get("filename"),
        "title": job.get("title") or Path(job.get("filename") or "reunion").stem,
        "duration": job.get("duration", 0),
        "duration_human": human_time(job.get("duration") or 0),
        "options": {k: v for k, v in (job.get("options") or {}).items() if k != "api_key"},
        "stats": job.get("stats") or {},
        "speakers": speakers,
        "warnings": job.get("warnings") or [],
        "log": (job.get("log") or [])[-40:],
        "phase": job.get("phase", ""),
        "restored": bool(job.get("restored")),
        "has_media": bool(job.get("media_path") or job.get("source_path")),
        "continua_de": job.get("continua_de") or "",
        "parte": job.get("parte") or 0,
        "merged_into": job.get("merged_into") or "",
        "partes": (job.get("result") or {}).get("partes") or [],
        "duplicado": bool(job.get("duplicado") or (job.get("result") or {}).get("duplicado")),
    }


def _set_status(job: Dict[str, Any], status: str, message: str = "", progress: Optional[float] = None):
    job["status"] = status
    if message:
        job_log(job, message, progress)


def cleanup_job_temp(job: Dict[str, Any]) -> None:
    for p in (job.get("wav_path"), job.get("tail_path")):
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


def persist_job_meta(job: Dict[str, Any]) -> None:
    """Guarda los datos mínimos para poder reanudar el trabajo tras un corte."""
    try:
        write_json(job_meta_path(job["id"]), {
            "id": job["id"],
            "session": job.get("session") or "default",
            "file_id": job.get("file_id") or "",
            "filename": job.get("filename"),
            "title": job.get("title"),
            "source_path": job.get("source_path"),
            "media_path": job.get("media_path"),
            "duration": job.get("duration"),
            "created": job.get("created"),
            "options": {k: v for k, v in (job.get("options") or {}).items() if k != "api_key"},
            "saved": now_iso(),
        }, backup=False)
    except Exception as exc:
        LOG.warning("No se pudo guardar el .job.json de %s: %s", job.get("id"), exc)


def clear_job_artifacts(job_id: str) -> None:
    for path in (checkpoint_path(job_id), partial_path(job_id), job_meta_path(job_id)):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def recover_interrupted_jobs() -> int:
    """
    Al arrancar, busca checkpoints huérfanos (corte de luz / cierre forzado) y
    reencola esos trabajos automáticamente desde el último punto guardado.
    """
    recovered = 0
    try:
        checkpoints = sorted(RESULT_DIR.glob("*.checkpoint.json"))
    except Exception:
        return 0
    for ck in checkpoints:
        job_id = ck.name[: -len(".checkpoint.json")]
        meta = read_json(job_meta_path(job_id), {}) or {}
        if not meta.get("source_path"):
            clear_job_artifacts(job_id)
            continue
        src = Path(meta["source_path"])
        if not src.exists():
            LOG.warning("Trabajo %s interrumpido: el audio original ya no existe; "
                        "se descartan los restos.", job_id)
            clear_job_artifacts(job_id)   # sin audio no hay nada que reanudar
            continue
        ckpt = read_json(ck, {}) or {}
        with JOBS_LOCK:
            if job_id in JOBS:
                continue
            job: Dict[str, Any] = {
                "id": job_id,
                "session": meta.get("session") or "default",
                "file_id": meta.get("file_id") or "",
                "filename": meta.get("filename") or src.name,
                "title": meta.get("title") or src.stem,
                "source_path": str(src),
                "media_path": meta.get("media_path") or str(src),
                "duration": float(meta.get("duration") or ckpt.get("duration") or 0) or probe_duration(src),
                "options": dict(meta.get("options") or {"mode": DEFAULT_MODE}),
                "status": "queued",
                "progress": 0,
                "message": "Reanudando tras un cierre inesperado…",
                "phase": "queued",
                "created": meta.get("created") or now_iso(),
                "started": None,
                "finished": None,
                "stats": {},
                "speakers": [],
                "warnings": [],
                "log": [],
                "phases": {},
                "result": None,
                "resume": True,
                "cancel": threading.Event(),
            }
            JOBS[job_id] = job
        job_log(
            job,
            f"Reanudando trabajo interrumpido desde {human_time(float(ckpt.get('upto') or 0))} "
            f"({len(ckpt.get('segments') or [])} fragmentos guardados).",
        )
        persist_job_meta(job)
        WORK_QUEUE.put(job_id)
        recovered += 1
    if recovered:
        LOG.info("Se reencolaron %s trabajo(s) interrumpido(s).", recovered)
    return recovered


def concatenar_audio(rutas: List[Path], destino: Path) -> Optional[Path]:
    """Une varios audios en un solo archivo (para «continuar reunión»)."""
    rutas = [r for r in rutas if r and Path(r).exists()]
    if not rutas:
        return None
    ffmpeg = find_ffmpeg("ffmpeg")
    if not ffmpeg:
        return None
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y"]
    for ruta in rutas:
        cmd += ["-i", str(ruta)]
    filtro = "".join(f"[{i}:a]" for i in range(len(rutas)))
    filtro += f"concat=n={len(rutas)}:v=0:a=1[out]"
    cmd += ["-filter_complex", filtro, "-map", "[out]", "-ac", "1",
            "-c:a", "aac", "-b:a", "48k", "-movflags", "+faststart", str(destino)]
    try:
        proceso = subprocess.run(
            cmd, capture_output=True, timeout=7200,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    except Exception as exc:
        LOG.warning("No se pudo unir el audio: %s", exc)
        return None
    if proceso.returncode != 0 or not destino.exists() or destino.stat().st_size < 2000:
        LOG.warning("FFmpeg no pudo unir el audio: %s",
                    (proceso.stderr or b"")[-400:].decode("utf-8", "ignore"))
        return None
    return destino


def fusionar_parte_en_reunion(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    C6 · Pega la transcripción de la parte nueva (job) al final de la reunión
    original: desplaza las marcas de tiempo, une el audio y recalcula totales.
    Devuelve la reunión original actualizada, o None si no se pudo fusionar.
    """
    padre_id = str(job.get("continua_de") or "")
    padre = ensure_job_loaded(padre_id) if padre_id else None
    if not padre:
        return None
    base = padre.get("result") or {}
    segmentos = list(base.get("segments") or [])
    if not segmentos:
        return None
    nuevo = job.get("result") or {}
    nuevos = [s for s in (nuevo.get("segments") or []) if isinstance(s, dict)]
    if not nuevos:
        return None
    offset = float(base.get("duration") or (base.get("info") or {}).get("duration")
                   or padre.get("duration") or 0)
    parte = int(job.get("parte") or 2)
    desplazados: List[Dict[str, Any]] = []
    for seg in nuevos:
        copia = dict(seg)
        copia["start"] = round(float(seg.get("start") or 0) + offset, 2)
        copia["end"] = round(float(seg.get("end") or 0) + offset, 2)
        if isinstance(copia.get("words"), list):
            copia["words"] = [
                {**w, "start": round(float(w.get("start") or 0) + offset, 2),
                 "end": round(float(w.get("end") or 0) + offset, 2)}
                for w in copia["words"] if isinstance(w, dict)
            ]
        copia["parte"] = parte
        desplazados.append(copia)

    base["segments"] = segmentos + desplazados
    total = round(offset + float(job.get("duration") or 0), 1)
    base["duration"] = total
    info = base.setdefault("info", {})
    info["duration"] = total
    palabras = sum(len((s.get("text") or "").split()) for s in base["segments"])
    caracteres = sum(len(s.get("text") or "") for s in base["segments"])
    stats = dict(base.get("stats") or {})
    stats.update({"words": palabras, "chars": caracteres, "segments": len(base["segments"])})
    base["stats"] = stats
    partes = list(base.get("partes") or [{
        "n": 1, "duration": round(offset, 1), "filename": padre.get("filename") or "",
    }])
    partes.append({"n": parte, "duration": round(float(job.get("duration") or 0), 1),
                   "filename": job.get("filename") or ""})
    base["partes"] = partes

    # --- audio unido, para que el reproductor cubra las dos partes ---
    padre_media = None
    if padre.get("media_path") or padre.get("source_path"):
        try:
            padre_media = _resolve_media(padre)
        except Exception:
            padre_media = None
    nueva_media = Path(job.get("source_path") or job.get("media_path") or "")
    unido = None
    if padre_media and nueva_media.exists():
        unido = concatenar_audio([padre_media, nueva_media],
                                 RESULT_DIR / f"{padre_id}_completo.m4a")
    if unido:
        anterior = str(padre.get("media_path") or "")
        padre["media_path"] = str(unido)
        padre["source_path"] = str(unido)
        if anterior and Path(anterior).exists() and Path(anterior) != unido:
            try:
                Path(anterior).unlink()   # el audio comprimido de la 1.ª parte se sustituye
            except Exception:
                pass
    else:
        padre.setdefault("warnings", []).append(
            "No se pudo unir el audio de las partes: la transcripción está completa, "
            "pero el reproductor solo tiene la primera parte.")
    padre["duration"] = total
    padre["result"] = base
    padre["stats"] = stats
    write_json(RESULT_DIR / f"{padre_id}.json", base)

    # --- historial: duración, peso y número de partes ---
    bytes_media = unido.stat().st_size if unido else 0
    actualizar_entrada_historial(
        padre_id,
        duration=total, duration_human=human_time(total), words=palabras,
        partes=len(partes),
        **({"media": str(unido.relative_to(BASE_DIR)), "media_bytes": bytes_media}
           if unido else {}),
    )
    # --- el acta existente queda desactualizada hasta volver a redactarla ---
    acta = leer_acta_guardada(padre_id)
    if acta:
        acta["desactualizada"] = True
        acta["partes_acta"] = parte
        acta["aviso"] = ("Se añadió una parte nueva a la reunión: vuelve a redactar el "
                         "acta para incluirla.")
        write_json(ruta_acta_json(padre_id), acta)
    job["merged_into"] = padre_id
    LOG.info("Parte %s fusionada en la reunión %s (total %s)", parte, padre_id,
             human_time(total))
    return padre


def process_job(job: Dict[str, Any]) -> None:
    """Pipeline completo de un trabajo: limpieza → transcripción → diarización."""
    t0 = time.time()
    job["started"] = now_iso()
    source = Path(job["source_path"])
    if not source.exists():
        raise RuntimeError("El archivo de audio ya no está disponible en disco.")

    duration = float(job.get("duration") or 0) or probe_duration(source)
    job["duration"] = duration
    opts = job.get("options") or {}
    persist_job_meta(job)

    # ---------------- 1. Limpieza con FFmpeg (0 % → 6 %) ----------------
    wav = TMP_DIR / f"{job['id']}_16k.wav"
    job["wav_path"] = str(wav)
    reuse_wav = bool(job.get("resume")) and wav.exists() and wav.stat().st_size > 1000
    if reuse_wav:
        _set_status(job, "preprocessing", "Reutilizando el audio limpio del intento anterior…", 6)
        job["phase"] = "cleanup"
        job["phases"]["cleanup"] = {"start": time.time(), "end": time.time()}
    else:
        _set_status(job, "preprocessing", "Limpiando audio con FFmpeg…", 1)
        job["phase"] = "cleanup"
        job["phases"]["cleanup"] = {"start": time.time(), "end": None}

        def cleanup_progress(p: float, sec: float):
            pct = 6.0 * max(0.0, min(1.0, p))
            job_log(job, f"Limpiando audio… {human_time(sec)} / {human_time(duration)}", pct)

        preprocess_audio(
            source, wav, duration, cleanup_progress,
            cancel_event=job["cancel"],
            user_filter=opts.get("ffmpeg_filter", ""),
            music_mode=bool(opts.get("music_mode")),
            two_pass=bool(opts.get("two_pass_norm", True)),
            denoise=bool(opts.get("denoise", True)),
        )
        job["phases"]["cleanup"]["end"] = time.time()
    check_cancel(job)

    # ---------------- 2. Transcripción (6 % → 99 %) ---------------------
    _set_status(job, "transcribing", "Transcribiendo audio…", 6)
    job["phase"] = "transcribe"
    job["phases"]["transcribe"] = {"start": time.time(), "end": None}

    def tx_progress(p: float):
        job_log(job, f"Transcribiendo… {int(p * 100)} % del audio", 6 + 93 * max(0.0, min(1.0, p)))

    result = transcribe_wav(job, wav, duration, tx_progress)
    job["phases"]["transcribe"]["end"] = time.time()
    job["result"] = result
    check_cancel(job)
    if not result["segments"]:
        raise RuntimeError(
            "No se detectó voz en el audio. Si es música o hay silencios largos, "
            "activa «Modo música (sin VAD)» en Opciones avanzadas."
        )
    job_log(job, f"Transcripción terminada: {len(result['segments'])} fragmentos.", 99)

    # ---------------- 3. Diarización (99 % → 100 %) ---------------------
    _set_status(job, "diarizing", "Identificando hablantes…", 99)
    job["phase"] = "diarization"
    job["phases"]["diarization"] = {"start": time.time(), "end": None}
    try:
        diar = analyze_voices(job)
        job["speakers"] = diar.get("speakers", [])
        job["speaker_embeddings"] = diar.get("embeddings", {})
        if diar.get("warning"):
            job["warnings"].append(diar["warning"])
        result["speakers"] = job["speakers"]
    except JobCancelled:
        raise
    except Exception as exc:
        job["warnings"].append(f"Diarización no disponible: {exc}")
    job["phases"]["diarization"]["end"] = time.time()

    # ---------------- 3b. Unir turnos consecutivos del mismo hablante ----
    if job["speakers"] and opts.get("join_turns", True):
        before_n = len(result["segments"])
        merged_segs = join_same_speaker_segments(result["segments"])
        if len(merged_segs) < before_n:
            job_log(job, f"Turnos unidos: {before_n} → {len(merged_segs)} fragmentos "
                         f"(mismo hablante, pausas < 1,1 s).")
            result["segments"] = merged_segs

    # ---------------- 4. Estadísticas y guardado ------------------------
    segments = result["segments"]
    words = sum(len((s.get("text") or "").split()) for s in segments)
    chars = sum(len(s.get("text") or "") for s in segments)
    total = time.time() - t0
    stats = {
        "total_seconds": round(total, 1),
        "cleanup_seconds": round(
            (job["phases"]["cleanup"]["end"] or time.time()) - job["phases"]["cleanup"]["start"], 1
        ),
        "transcribe_seconds": round(
            (job["phases"]["transcribe"]["end"] or time.time()) - job["phases"]["transcribe"]["start"], 1
        ),
        "diarize_seconds": round(
            (job["phases"]["diarization"]["end"] or time.time()) - job["phases"]["diarization"]["start"], 1
        ),
        "words": words,
        "chars": chars,
        "segments": len(segments),
        "low_confidence": sum(1 for s in segments if float(s.get("avg_logprob") or 0) < -0.6),
        "speed_factor": round((duration / total), 2) if total > 0 else 0,
        "tail_recovered": result.get("tail_recovered", 0.0),
        "batch": result.get("batch", False),
    }
    job["stats"] = stats
    result["stats"] = stats
    result["job_id"] = job["id"]
    result["created"] = job.get("created")
    result["title"] = job.get("title") or Path(job.get("filename") or "reunion").stem
    write_json(RESULT_DIR / f"{job['id']}.json", result)

    # C6 · ¿esta transcripción es una parte nueva de una reunión existente?
    fusionada_en = ""
    if job.get("continua_de"):
        try:
            padre = fusionar_parte_en_reunion(job)
            if padre:
                fusionada_en = str(padre.get("id") or "")
        except Exception as exc:
            job["warnings"].append(f"No se pudo unir la parte a la reunión original: {exc}")
    if fusionada_en:
        # La parte no es una reunión aparte: sus archivos ya están en la original
        for ruta in (RESULT_DIR / f"{job['id']}.json", Path(job.get("source_path") or "")):
            try:
                ruta.unlink(missing_ok=True)
            except Exception:
                pass
        job["history_entry"] = None
        job_log(job, f"Parte {job.get('parte') or 2} añadida a la reunión original.", 100)
    else:
        # Historial (comprime a .m4a y libera el original)
        try:
            job["history_entry"] = add_history_entry(job)
        except Exception as exc:
            job["warnings"].append(f"No se pudo guardar en el historial: {exc}")

    # Sesión (etiquetas y conclusiones se completan desde la interfaz)
    try:
        db = load_sessions()
        db["sessions"].setdefault(job.get("session") or "default", {"jobs": {}, "created": now_iso()})
        write_json(SESSIONS_FILE, db)
    except Exception:
        pass

    job["finished"] = now_iso()
    job["phase"] = "done"
    if fusionada_en:
        _set_status(
            job, "done",
            f"Parte {job.get('parte') or 2} añadida · {len(segments)} fragmentos nuevos · "
            f"{words} palabras · la reunión ya incluye esta parte",
            100,
        )
    else:
        _set_status(
            job, "done",
            f"Listo · {len(segments)} fragmentos · {words} palabras · {human_time(duration)} de audio",
            100,
        )
    job["progress"] = 100
    clear_job_artifacts(job["id"])
    cleanup_job_temp(job)
    notify_windows(
        "Transcripción terminada",
        f"{Path(job.get('filename') or 'Reunión').name} · {human_time(duration)} · {words} palabras",
    )
    gc.collect()


def worker_loop() -> None:
    while not WORKER["stop"].is_set():
        try:
            job_id = WORK_QUEUE.get(timeout=0.7)
        except queue.Empty:
            continue
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job or job.get("cancel", threading.Event()).is_set():
                if job and job.get("status") == "queued":
                    job["status"] = "cancelled"
                    job["message"] = "Trabajo cancelado antes de iniciar."
                    job["finished"] = now_iso()
                WORK_QUEUE.task_done()
                continue
        try:
            process_job(job)
        except JobCancelled:
            job["status"] = "cancelled"
            job["message"] = "Trabajo cancelado por el usuario."
            job["finished"] = now_iso()
            job["phase"] = "cancelled"
            clear_job_artifacts(job["id"])
            cleanup_job_temp(job)
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)
            job["message"] = f"Error: {exc}"
            job["finished"] = now_iso()
            job["phase"] = "error"
            job.setdefault("log", []).append({"t": now_iso(), "m": traceback.format_exc()[-1500:]})
            cleanup_job_temp(job)
        finally:
            try:
                WORK_QUEUE.task_done()
            except Exception:
                pass
            gc.collect()


def start_worker() -> None:
    if WORKER.get("thread") and WORKER["thread"].is_alive():
        return
    WORKER["stop"].clear()
    t = threading.Thread(target=worker_loop, name="work-queue", daemon=True)
    WORKER["thread"] = t
    t.start()


def cancel_job(job_id: str) -> bool:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return False
        job["cancel"].set()
        if job.get("status") == "queued":
            job["status"] = "cancelled"
            job["message"] = "Cancelado antes de iniciar."
            job["finished"] = now_iso()
        elif job.get("status") in ("preprocessing", "transcribing", "diarizing"):
            job["message"] = "Cancelando…"
        return True


def cancel_session(session_id: str) -> int:
    n = 0
    with JOBS_LOCK:
        for jid, job in list(JOBS.items()):
            if job.get("session") == session_id and job.get("status") in (
                "queued", "preprocessing", "transcribing", "diarizing"
            ):
                if cancel_job(jid):
                    n += 1
    return n


def ensure_job_loaded(job_id: str) -> Optional[Dict[str, Any]]:
    """Reconstruye un trabajo terminado desde el historial (tras reiniciar)."""
    with JOBS_LOCK:
        if job_id in JOBS:
            return JOBS[job_id]
    result_path = RESULT_DIR / f"{job_id}.json"
    if not result_path.exists():
        return None
    result = read_json(result_path, {})
    if not result:
        return None
    entry = next((e for e in load_history() if e.get("job_id") == job_id), {})
    media = BASE_DIR / entry["media"] if entry.get("media") else None
    job = {
        "id": job_id,
        "session": entry.get("session_id", ""),
        "filename": entry.get("filename") or "Reunión",
        "title": entry.get("title") or "Reunión",
        "source_path": str(media) if media and media.exists() else "",
        "media_path": str(media) if media and media.exists() else "",
        "duration": float(entry.get("duration") or (result.get("info") or {}).get("duration") or 0),
        "options": {"mode": entry.get("mode", DEFAULT_MODE)},
        "status": "done",
        "progress": 100,
        "message": "Recuperado del historial",
        "stats": result.get("stats") or {},
        "result": result,
        "speakers": result.get("speakers") or [],
        "speaker_embeddings": {},
        "warnings": [],
        "created": entry.get("created", now_iso()),
        "finished": entry.get("created", now_iso()),
        "log": [],
        "phases": {},
        "cancel": threading.Event(),
        "restored": True,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    return job


# -----------------------------------------------------------------------------
# 17.5 MODELOS DE PETICIÓN (Pydantic): validación y documentación automática
# -----------------------------------------------------------------------------
# Los imports y la detección de versión están al principio del archivo (bloque
# 0.0). Aquí solo viven los modelos. Funciona igual con Pydantic 2 y 1.x.


class _Loose(BaseModel):
    """Base tolerante: todos los campos son `Any` y los desconocidos se ignoran.

    No define `model_config` (Pydantic 2) ni `class Config` (Pydantic 1) a
    propósito: el comportamiento por defecto de ambas versiones ya es
    "ignorar campos desconocidos", y así este bloque no puede fallar por culpa
    de la versión de Pydantic instalada.
    """


class JobCreateRequest(_Loose):
    file_id: Any = ""
    mode: Any = ""
    language: Any = ""
    title: Any = ""
    session_id: Any = ""
    timestamps: Any = True
    paragraphs: Any = False
    hotwords: Any = ""
    context: Any = ""
    ffmpeg_filter: Any = ""
    chain_text: Any = True
    music_mode: Any = False
    min_silence: Any = None
    vad_threshold: Any = None
    speech_pad: Any = None
    beam_size: Any = None
    word_timestamps: Any = True
    join_turns: Any = True
    two_pass_norm: Any = True
    denoise: Any = True


class UploadCompleteRequest(_Loose):
    upload_id: Any = ""
    filename: Any = ""
    session_id: Any = ""


class JobPatchRequest(_Loose):
    segments: Any = None
    speakers: Any = None
    title: Any = ""


class ExportRequest(_Loose):
    format: Any = "docx"
    conclusion: Any = ""
    title: Any = ""
    segments: Any = None
    speakers: Any = None


class SummarizeRequest(_Loose):
    job_id: Any = ""
    format: Any = "acta_completa"
    engine: Any = ""
    api_key: Any = ""
    save_key: Any = False
    local_model: Any = ""
    openai_base_url: Any = ""
    openai_model: Any = ""
    title: Any = ""
    notes: Any = ""
    participants: Any = ""
    glossary: Any = ""
    acta_previa: Any = ""
    segments: Any = None


class SpeakerMergeRequest(_Loose):
    from_: Any = Field(default="", alias="from")
    into: Any = ""


class SpeakerSplitRequest(_Loose):
    name: Any = ""
    new_name: Any = ""
    segment_ids: Any = None
    ids: Any = None


class SaveVoiceRequest(_Loose):
    job_id: Any = ""
    speaker_name: Any = ""
    save_as: Any = ""


class IaConfigRequest(_Loose):
    engine: Any = ""
    local_model: Any = ""
    openai_base_url: Any = ""
    openai_model: Any = ""
    gemini_api_key: Any = ""


class SessionSaveRequest(_Loose):
    job_id: Any = ""
    title: Any = ""
    summary: Any = ""
    conclusion: Any = ""
    summary_format: Any = ""
    labels: Any = None


def as_dict(model: BaseModel) -> Dict[str, Any]:
    """Modelo → dict plano (con alias resueltos y sin nulos)."""
    dump = getattr(model, "model_dump", None)      # Pydantic 2
    if dump is None:                               # Pydantic 1
        dump = model.dict
    data = dump(exclude_none=True, by_alias=False)
    # "from" llega como from_ por el alias
    if "from_" in data:
        data["from"] = data.pop("from_")
    return data


def body_default(model_cls):
    """Cuerpo JSON opcional, igual de permisivo en Pydantic 1 y 2.

    En Pydantic 2 `Body(default_factory=...)` basta; en Pydantic 1 FastAPI
    interpreta ese parámetro como "cuerpo obligatorio" y devuelve 422 cuando
    la petición llega sin cuerpo, así que ahí se pasa una instancia por defecto.
    """
    if PYDANTIC_V2:
        return Body(default_factory=model_cls)
    return Body(default=model_cls())


# -----------------------------------------------------------------------------
# 18. API FASTAPI
# -----------------------------------------------------------------------------
import urllib.parse  # noqa: E402  (usado por Gemini / Content-Disposition)

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import (  # noqa: E402
    FileResponse, JSONResponse, Response, StreamingResponse,
)
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

VERSION = "2.5.0"
APP_TOKEN = os.environ.get("TAE_TOKEN", "").strip()
_LOCK_SOCKET: Dict[str, Any] = {"sock": None, "busy": False}
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024 + 200 * 1024 * 1024  # 2 GB + margen
ALLOWED_EXT = {
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".aiff", ".aif",
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpeg", ".mpg", ".3gp", ".amr", ".caf",
}


def hw_ffmpeg() -> str:
    return find_ffmpeg("ffmpeg") or "no encontrado"


def acquire_single_instance() -> bool:
    """
    Evita dos instancias escribiendo historial.json/sesiones.json a la vez.
    Reserva un puerto local derivado de la ruta del proyecto.
    """
    if _LOCK_SOCKET.get("sock") is not None:
        return True
    port = 47100 + (int(hashlib.sha1(str(BASE_DIR).encode()).hexdigest()[:6], 16) % 800)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        _LOCK_SOCKET["sock"] = sock
        _LOCK_SOCKET["busy"] = False
        return True
    except OSError:
        try:
            sock.close()
        except Exception:
            pass
        _LOCK_SOCKET["busy"] = True
        LOG.warning("Otra instancia de la aplicación parece estar en ejecución (lock %s).", port)
        return False


def release_single_instance() -> None:
    sock = _LOCK_SOCKET.get("sock")
    if sock is not None:
        try:
            sock.close()
        except Exception:
            pass
        _LOCK_SOCKET["sock"] = None


def cleanup_old_files(days_uploads: int = 7, days_tmp: int = 2) -> None:
    now = time.time()
    try:
        for p in UPLOAD_DIR.glob("*"):
            if p.is_file() and now - p.stat().st_mtime > days_uploads * 86400:
                p.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        for p in TMP_DIR.glob("*"):
            if p.is_file() and now - p.stat().st_mtime > days_tmp * 86400:
                p.unlink(missing_ok=True)
    except Exception:
        pass
    # restos de subidas por trozos abandonadas (más de 2 días sin actividad)
    try:
        for p in UPLOAD_DIR.glob("*.part"):
            if p.is_file() and now - p.stat().st_mtime > 2 * 86400:
                meta_file = UPLOAD_DIR / f"{p.stem}.upload.json"
                p.unlink(missing_ok=True)
                meta_file.unlink(missing_ok=True)
    except Exception:
        pass


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_offline_mode()
    ensure_ia_defaults()
    cleanup_old_files()
    acquire_single_instance()
    recover_interrupted_jobs()
    start_worker()
    daily_backup()
    LOG.info("Inicio de la aplicación v%s · FFmpeg=%s · offline=%s", VERSION,
             hw_ffmpeg(), OFFLINE_ACTIVE)
    print("\n" + "=" * 78)
    hw = detect_hardware()
    print("  TRANSCRIPTOR DE AUDIO Y GENERADOR DE ACTAS · v" + VERSION)
    print(f"  FFmpeg  : {hw.get('ffmpeg') or 'NO ENCONTRADO (instala: winget install Gyan.FFmpeg)'}")
    print(f"  Hardware: {'GPU CUDA' if hw['cuda'] else 'CPU'} · {hw['cpu_threads']} hilos · "
          f"int8 · {'offline' if OFFLINE_ACTIVE else 'descarga inicial de modelos'}")
    print(f"  Modelos : {MODEL_DIR}")
    print("=" * 78 + "\n")
    yield
    WORKER["stop"].set()
    release_single_instance()


app = FastAPI(title="Transcriptor de Audio y Generador de Actas", version=VERSION,
              lifespan=lifespan, docs_url="/api/docs", redoc_url=None)

# Compresión de respuestas: /api/jobs/{id}/result puede pesar varios MB en 3 h
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def no_cache_estaticos(request: Request, call_next):
    """Los estáticos se revalidan siempre: cambiar app.js no deja nada en caché."""
    respuesta = await call_next(request)
    if request.url.path.startswith("/static/"):
        respuesta.headers["Cache-Control"] = "no-cache, must-revalidate"
    return respuesta


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """
    Token opcional (TAE_TOKEN / --token) para uso en red, y cabeceras seguras.
    Sin token configurado la app queda abierta pero solo en 127.0.0.1.
    """
    path = request.url.path
    if APP_TOKEN and not path.startswith("/static") and path != "/favicon.ico":
        token = (request.query_params.get("token")
                 or request.headers.get("x-auth-token")
                 or request.cookies.get("tae_token") or "")
        if token != APP_TOKEN:
            return JSONResponse({"detail": "Token de acceso requerido."}, status_code=401)
    response = await call_next(request)
    if (APP_TOKEN and path == "/" and request.query_params.get("token") == APP_TOKEN):
        response.set_cookie("tae_token", APP_TOKEN, httponly=True, samesite="lax",
                            max_age=30 * 86400)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "microphone=(self)")
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    f = STATIC_DIR / "index.html"
    if not f.exists():
        return JSONResponse({"error": "Falta static/index.html"}, status_code=500)
    try:
        html = f.read_text(encoding="utf-8").replace("__V__", VERSION)
    except Exception:
        return FileResponse(str(f), media_type="text/html",
                            headers={"Cache-Control": "no-store"})
    # no-store en el HTML y ?v=VERSION en app.js/style.css: el navegador nunca
    # se queda con una versión anterior de los archivos estáticos.
    return Response(html, media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/api/logs")
def api_logs(lines: int = 200):
    """Últimas líneas del log de la aplicación (diagnóstico)."""
    lines = max(10, min(2000, int(lines or 200)))
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-lines:]
        return {"file": str(LOG_FILE), "lines": [ln.rstrip("\n") for ln in tail]}
    except Exception as exc:
        return {"file": str(LOG_FILE), "lines": [], "error": str(exc)}


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


# -----------------------------------------------------------------------------
# 18.1 Estado del sistema
# -----------------------------------------------------------------------------
@app.get("/api/health")
def api_health():
    import importlib.util

    hw = detect_hardware()
    modes = []
    for key, cfg in MODES.items():
        modes.append({
            "key": key,
            "label": cfg["label"],
            "model": cfg["model"],
            "beam_size": cfg["beam_size"],
            "desc": cfg["desc"],
            "local": whisper_is_local(cfg["model"]),
            "bytes": int(MODEL_SIZES.get(_whisper_repo(cfg["model"]), 0)),
        })
    hist = load_history()
    hist_bytes = sum(e.get("media_bytes", 0) for e in hist)
    ia_cfg = _read_ia_config()
    return {
        "ok": True,
        "version": VERSION,
        "download": dict(DESCARGA),
        "security": {"token_required": bool(APP_TOKEN), "single_instance": not _LOCK_SOCKET.get("busy")},
        "log_file": str(LOG_FILE),
        "ffmpeg": hw.get("ffmpeg"),
        "ffprobe": hw.get("ffprobe"),
        "ffmpeg_ok": bool(hw.get("ffmpeg")),
        "hardware": {
            "cuda": hw["cuda"],
            "gpu_names": hw["gpu_names"],
            "cpu_count": hw["cpu_count"],
            "cpu_threads": hw["cpu_threads"],
            "device": hw["device"],
            "compute_type": hw["compute_type"],
        },
        "offline": OFFLINE_ACTIVE,
        "models_dir": str(MODEL_DIR),
        "models": modes,
        "default_mode": DEFAULT_MODE,
        "languages": LANGUAGES,
        "diarization": importlib.util.find_spec("resemblyzer") is not None,
        "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
        "local_llm_ready": importlib.util.find_spec("transformers") is not None,
        "engines": {
            "default": ia_cfg.get("engine", "local"),
            "local": {"ready": importlib.util.find_spec("transformers") is not None,
                      "models": LOCAL_MODELS, "default": ia_cfg.get("local_model")},
            "openai": {"ready": None, "base_url": ia_cfg.get("openai_base_url"),
                       "model": ia_cfg.get("openai_model"), "free": True},
            "gemini": {"ready": bool(ia_cfg.get("gemini_api_key")), "model": "gemini-2.5-flash",
                       "free_tier": True},
        },
        "history": {
            "count": len(hist),
            "bytes": hist_bytes,
            "mb": round(hist_bytes / (1024 * 1024), 1),
            "max_entries": MAX_HISTORY,
            "max_mb": MAX_HISTORY_BYTES // (1024 * 1024),
        },
        "queue": {
            "pending": WORK_QUEUE.qsize(),
            "active": sum(1 for j in JOBS.values()
                          if j.get("status") in ("preprocessing", "transcribing", "diarizing")),
        },
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
    }


# -----------------------------------------------------------------------------
# 18.2 Subida de archivos
# -----------------------------------------------------------------------------
@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), session_id: str = Form("")):
    ext = Path(file.filename or "").suffix.lower()
    if ext and ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Formato «{ext}» no soportado. Usa audio o video común.")
    file_id = uuid.uuid4().hex
    dest = UPLOAD_DIR / f"{file_id}{ext or '.bin'}"
    size = 0
    try:
        with open(dest, "wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, "El archivo supera el límite de 2 GB.")
                fh.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"No se pudo guardar el archivo: {exc}")
    if size < 1024:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "El archivo está vacío o es demasiado pequeño.")

    duration = probe_duration(dest)
    meta = {
        "file_id": file_id,
        "filename": file.filename or dest.name,
        "path": str(dest),
        "size": size,
        "size_mb": round(size / (1024 * 1024), 1),
        "duration": round(duration, 1),
        "duration_human": human_time(duration),
        "session_id": session_id,
        "created": now_iso(),
    }
    UPLOADS[file_id] = meta
    write_json(UPLOAD_DIR / f"{file_id}.json", meta)
    if abs(duration - size / (128 * 1024 / 8)) < 1 and not ffmpeg_available():
        meta["warnings"] = ["FFmpeg no está instalado: no se pudo verificar la duración real."]
    return meta


# -----------------------------------------------------------------------------
# Subida por trozos con reanudación (archivos de hasta 2 GB)
# -----------------------------------------------------------------------------
CHUNK_MAX_BYTES = 16 * 1024 * 1024


def _chunk_meta_path(upload_id: str) -> Path:
    return UPLOAD_DIR / f"{upload_id}.upload.json"


@app.get("/api/upload/status/{upload_id}")
def api_upload_status(upload_id: str):
    """Bytes ya recibidos (para reanudar una subida interrumpida)."""
    if not upload_id.isalnum():
        raise HTTPException(400, "Identificador de subida inválido.")
    meta = read_json(_chunk_meta_path(upload_id), {}) or {}
    part = UPLOAD_DIR / f"{upload_id}.part"
    received = part.stat().st_size if part.exists() else 0
    if not meta and received == 0:
        raise HTTPException(404, "Subida no encontrada.")
    return {
        "upload_id": upload_id,
        "received": received,
        "total": int(meta.get("total_bytes") or 0),
        "chunk_size": int(meta.get("chunk_size") or 0),
        "filename": meta.get("filename"),
        "complete": bool(meta.get("complete")),
        "file_id": meta.get("file_id"),
    }


@app.post("/api/upload/chunk")
async def api_upload_chunk(
    chunk: UploadFile = File(...),
    upload_id: str = Form(""),
    offset: int = Form(0),
    total_bytes: int = Form(0),
    filename: str = Form(""),
    session_id: str = Form(""),
    chunk_size: int = Form(0),
):
    """
    Recibe un trozo y lo escribe en la posición indicada. Si la conexión se
    corta, el cliente consulta /api/upload/status y continúa desde ahí.
    """
    ext = Path(filename or "").suffix.lower()
    if ext and ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Formato «{ext}» no soportado. Usa audio o video común.")
    if not upload_id:
        upload_id = uuid.uuid4().hex
    if not upload_id.isalnum():
        raise HTTPException(400, "Identificador de subida inválido.")
    part = UPLOAD_DIR / f"{upload_id}.part"
    current = part.stat().st_size if part.exists() else 0
    offset = int(offset or 0)
    if offset > current:
        raise HTTPException(409, f"Faltan trozos: se esperaba como máximo {current} bytes, llegó {offset}.")
    if total_bytes and total_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "El archivo supera el límite de 2 GB.")
    data = await chunk.read()
    if len(data) > CHUNK_MAX_BYTES:
        raise HTTPException(413, "Trozo demasiado grande (máximo 16 MB).")
    try:
        with open(part, "r+b" if part.exists() else "wb") as fh:
            if offset == 0:
                fh.truncate(0)
            else:
                fh.truncate(offset)
            fh.seek(offset)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as exc:
        raise HTTPException(500, f"No se pudo escribir el trozo: {exc}")
    received = part.stat().st_size
    if received > MAX_UPLOAD_BYTES:
        part.unlink(missing_ok=True)
        raise HTTPException(413, "El archivo supera el límite de 2 GB.")
    meta = read_json(_chunk_meta_path(upload_id), {}) or {}
    meta.update({
        "upload_id": upload_id,
        "filename": filename or meta.get("filename") or "audio",
        "total_bytes": int(total_bytes or meta.get("total_bytes") or 0),
        "chunk_size": int(chunk_size or meta.get("chunk_size") or 0),
        "session_id": session_id or meta.get("session_id") or "",
        "received": received,
        "updated": now_iso(),
    })
    write_json(_chunk_meta_path(upload_id), meta, backup=False)
    total = meta.get("total_bytes") or 0
    return {
        "upload_id": upload_id,
        "received": received,
        "total": total,
        "percent": round(100.0 * received / total, 1) if total else None,
    }


@app.post("/api/upload/complete")
def api_upload_complete(payload: UploadCompleteRequest = Body(...)):
    payload = as_dict(payload)
    """Cierra una subida por trozos: valida, sondea duración y registra el archivo."""
    upload_id = str(payload.get("upload_id") or "")
    if not upload_id or not upload_id.isalnum():
        raise HTTPException(400, "upload_id inválido.")
    part = UPLOAD_DIR / f"{upload_id}.part"
    if not part.exists():
        raise HTTPException(404, "No hay datos para cerrar esta subida.")
    meta_up = read_json(_chunk_meta_path(upload_id), {}) or {}
    filename = str(payload.get("filename") or meta_up.get("filename") or "audio")
    ext = Path(filename).suffix.lower() or ".bin"
    size = part.stat().st_size
    expected = int(meta_up.get("total_bytes") or 0)
    if expected and size != expected:
        raise HTTPException(409, f"Subida incompleta: {size} de {expected} bytes.")
    if size < 1024:
        part.unlink(missing_ok=True)
        raise HTTPException(400, "El archivo está vacío o es demasiado pequeño.")
    if size > MAX_UPLOAD_BYTES:
        part.unlink(missing_ok=True)
        raise HTTPException(413, "El archivo supera el límite de 2 GB.")

    file_id = upload_id
    dest = UPLOAD_DIR / f"{file_id}{ext}"
    try:
        os.replace(part, dest)
    except Exception as exc:
        raise HTTPException(500, f"No se pudo finalizar la subida: {exc}")
    duration = probe_duration(dest)
    meta = {
        "file_id": file_id,
        "filename": filename,
        "path": str(dest),
        "size": size,
        "size_mb": round(size / (1024 * 1024), 1),
        "duration": round(duration, 1),
        "duration_human": human_time(duration),
        "session_id": str(payload.get("session_id") or meta_up.get("session_id") or ""),
        "created": now_iso(),
    }
    UPLOADS[file_id] = meta
    write_json(UPLOAD_DIR / f"{file_id}.json", meta)
    meta_up.update({"complete": True, "file_id": file_id, "completed": now_iso()})
    write_json(_chunk_meta_path(upload_id), meta_up, backup=False)
    LOG.info("Subida completa: %s (%.1f MB)", filename, size / (1024 * 1024))
    return meta


def _lookup_upload(file_id: str) -> Dict[str, Any]:
    meta = UPLOADS.get(file_id)
    if meta:
        return meta
    path = UPLOAD_DIR / f"{file_id}.json"
    if path.exists():
        meta = read_json(path, {})
        if meta:
            UPLOADS[file_id] = meta
            return meta
    raise HTTPException(404, "Archivo no encontrado. Vuelve a subirlo.")


# -----------------------------------------------------------------------------
# 18.3 Trabajos
# -----------------------------------------------------------------------------
@app.post("/api/jobs")
async def api_create_job(payload: JobCreateRequest = Body(...)):
    payload = as_dict(payload)
    file_id = str(payload.get("file_id") or "")
    meta = _lookup_upload(file_id)
    src = Path(meta["path"])
    if not src.exists():
        raise HTTPException(404, "El archivo ya no está en disco. Súbelo de nuevo.")
    mode = str(payload.get("mode") or DEFAULT_MODE)
    if mode not in MODES:
        mode = DEFAULT_MODE
    options = {
        "mode": mode,
        "language": str(payload.get("language") or ""),
        "timestamps": bool(payload.get("timestamps", True)),
        "paragraphs": bool(payload.get("paragraphs", False)),
        "hotwords": str(payload.get("hotwords") or "")[:300],
        "context": str(payload.get("context") or "")[:600],
        "ffmpeg_filter": str(payload.get("ffmpeg_filter") or "")[:200],
        "chain_text": bool(payload.get("chain_text", True)),
        "music_mode": bool(payload.get("music_mode", False)),
        "min_silence": int(payload.get("min_silence") or 500),
        "vad_threshold": float(payload.get("vad_threshold") or 0.35),
        "speech_pad": int(payload.get("speech_pad") or 200),
        "beam_size": payload.get("beam_size"),
        "word_timestamps": bool(payload.get("word_timestamps", True)),
        "join_turns": bool(payload.get("join_turns", True)),
        "two_pass_norm": bool(payload.get("two_pass_norm", True)),
        "denoise": bool(payload.get("denoise", True)),
    }
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "session": str(payload.get("session_id") or "default"),
        "file_id": file_id,
        "filename": meta.get("filename", "reunion"),
        "title": str(payload.get("title") or Path(meta.get("filename", "reunion")).stem),
        "source_path": str(src),
        "media_path": str(src),
        "duration": float(meta.get("duration") or 0),
        "options": options,
        "status": "queued",
        "progress": 0,
        "message": "En cola de procesamiento…",
        "phase": "queued",
        "created": now_iso(),
        "started": None,
        "finished": None,
        "stats": {},
        "speakers": [],
        "warnings": [],
        "log": [{"t": now_iso(), "m": "Trabajo creado y en cola."}],
        "phases": {},
        "result": None,
        "cancel": threading.Event(),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    persist_job_meta(job)
    WORK_QUEUE.put(job_id)
    LOG.info("Trabajo %s encolado (%s, modo %s)", job_id, job["filename"], mode)
    return {"job_id": job_id, "position": WORK_QUEUE.qsize(), "job": public_job(job)}


@app.get("/api/jobs/{job_id}")
def api_job_status(job_id: str):
    job = ensure_job_loaded(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    return public_job(job)


def _resolve_media(job: Dict[str, Any]) -> Path:
    for key in ("media_path", "source_path"):
        p = job.get(key)
        if p and Path(p).exists():
            return Path(p)
    raise HTTPException(404, "El audio de este trabajo ya no está disponible.")


def _range_file_response(path: Path, request: Request, media_type: Optional[str] = None) -> Response:
    """Sirve audio con soporte de Range para que el <audio> pueda buscar."""
    import mimetypes

    size = path.stat().st_size
    mt = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    range_header = request.headers.get("range") or request.headers.get("Range")
    if not range_header:
        return FileResponse(str(path), media_type=mt,
                            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"})
    m = re.match(r"bytes=(\d*)-(\d*)", range_header)
    if not m:
        return FileResponse(str(path), media_type=mt)
    start_s, end_s = m.group(1), m.group(2)
    if start_s:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    else:
        # Sufijo: bytes=-N
        length = int(end_s or 0)
        start = max(0, size - length)
        end = size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    length = end - start + 1

    def iterfile():
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(1024 * 512, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "no-store",
    }
    return StreamingResponse(iterfile(), status_code=206, media_type=mt, headers=headers)


@app.get("/api/jobs/{job_id}/media")
def api_job_media(job_id: str, request: Request):
    job = ensure_job_loaded(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    return _range_file_response(_resolve_media(job), request)


@app.get("/api/jobs/{job_id}/result")
def api_job_result(job_id: str):
    job = ensure_job_loaded(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    result = job.get("result") or {}
    session = get_session(job.get("session") or "default")
    return {
        "job": public_job(job),
        "segments": result.get("segments") or [],
        "info": result.get("info") or {},
        "speakers": [{k: v for k, v in sp.items() if k != "embedding"} for sp in (job.get("speakers") or [])],
        "stats": job.get("stats") or result.get("stats") or {},
        "warnings": job.get("warnings") or [],
        "media_url": f"/api/jobs/{job_id}/media",
        "title": result.get("title") or job.get("title") or "",
        "session": session.get("jobs", {}).get(job_id, {}),
        "low_confidence_threshold": -0.6,
    }


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str):
    ok = cancel_job(job_id)
    if not ok:
        raise HTTPException(404, "Trabajo no encontrado.")
    return {"ok": True, "message": "Cancelación solicitada."}


@app.post("/api/session/{session_id}/cancel")
def api_cancel_session(session_id: str):
    n = cancel_session(session_id)
    return {"ok": True, "cancelled": n, "message": f"{n} trabajo(s) cancelado(s) en esta sesión."}


def _fold(text: str) -> str:
    """Minúsculas y sin acentos: búsqueda tolerante a tildes y ñ."""
    base = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in base if not unicodedata.combining(c))


@app.get("/api/jobs/{job_id}/peaks")
def api_job_peaks(job_id: str, buckets: int = 900):
    """
    Envolvente del audio para dibujar la forma de onda (máx. por tramo).
    Se calcula con FFmpeg una vez y se guarda en caché junto al resultado.
    """
    job = ensure_job_loaded(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    buckets = max(80, min(4000, int(buckets or 900)))
    cache = RESULT_DIR / f"{job_id}.peaks.json"
    cached = read_json(cache, {}) or {}
    if cached.get("peaks"):
        values = cached["peaks"]
        # remuestreo al número de barras pedido
        if len(values) != buckets:
            step = len(values) / buckets
            values = [values[min(len(values) - 1, int(i * step))] for i in range(buckets)]
        return {"job_id": job_id, "buckets": buckets, "duration": cached.get("duration"),
                "peaks": values, "cached": True}

    media = _resolve_media(job)
    ffmpeg = find_ffmpeg("ffmpeg")
    duration = float(job.get("duration") or 0) or probe_duration(media)
    if not ffmpeg:
        raise HTTPException(400, "FFmpeg no está disponible para calcular la forma de onda.")
    sr = 8000
    total_samples = max(1, int(duration * sr))
    bucket_samples = max(1, total_samples // buckets)
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(media),
           "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    peaks: List[float] = []
    buffer = bytearray()
    need = bucket_samples * 2
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        while True:
            block = proc.stdout.read(1 << 20)
            if not block:
                break
            buffer.extend(block)
            while len(buffer) >= need and len(peaks) < buckets:
                chunk = bytes(buffer[:need])
                del buffer[:need]
                arr = memoryview(chunk).cast("h")
                peaks.append(max(abs(min(arr)), abs(max(arr))) / 32768.0)
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
    except Exception as exc:
        raise HTTPException(500, f"No se pudo calcular la forma de onda: {exc}")
    if not peaks:
        raise HTTPException(500, "El audio no se pudo decodificar para la forma de onda.")
    write_json(cache, {"duration": round(duration, 2), "buckets": len(peaks),
                       "peaks": [round(p, 4) for p in peaks]}, backup=False)
    if len(peaks) != buckets:
        step = len(peaks) / buckets
        peaks = [peaks[min(len(peaks) - 1, int(i * step))] for i in range(buckets)]
    return {"job_id": job_id, "buckets": buckets, "duration": round(duration, 2),
            "peaks": [round(p, 4) for p in peaks], "cached": False}


@app.get("/api/search")
def api_search(q: str = "", limit: int = 40, exclude: str = ""):
    """
    Búsqueda global entre todas las reuniones (sin distinguir acentos).
    Busca en resultados/*.json y devuelve coincidencias con su marca de tiempo.
    """
    needle = _fold(q).strip()
    if len(needle) < 2:
        raise HTTPException(400, "Escribe al menos 2 caracteres para buscar.")
    limit = max(1, min(200, int(limit or 40)))
    hits: List[Dict[str, Any]] = []
    history = {e.get("job_id"): e for e in load_history()}
    files = sorted(RESULT_DIR.glob("*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    scanned = 0
    for path in files:
        name = path.name
        if any(tag in name for tag in (".partial", ".checkpoint", ".job", ".acta", ".peaks")):
            continue
        if exclude and name.startswith(exclude):
            continue
        scanned += 1
        if scanned > 80:  # presupuesto de tiempo
            break
        data = read_json(path, {}) or {}
        segs = data.get("segments") or []
        job_id = name[:-5]
        entry = history.get(job_id) or {}
        for seg in segs:
            text = seg.get("text") or ""
            if needle in _fold(text):
                hits.append({
                    "job_id": job_id,
                    "title": data.get("title") or entry.get("title") or job_id,
                    "filename": entry.get("filename"),
                    "created": entry.get("created") or data.get("created"),
                    "start": seg.get("start"),
                    "end": seg.get("end"),
                    "speaker": seg.get("speaker") or "",
                    "text": text[:400],
                })
                break  # una coincidencia por reunión basta para orientar
        if len(hits) >= limit:
            break
    return {"query": q, "count": len(hits), "scanned": scanned, "results": hits}


@app.get("/api/jobs/{job_id}/speaker-sample/{index}")
def api_speaker_sample(job_id: str, index: int):
    job = ensure_job_loaded(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    speakers = job.get("speakers") or []
    if not (0 <= index < len(speakers)):
        raise HTTPException(404, "Hablante no encontrado.")
    sp = speakers[index]
    src = _resolve_media(job)
    start = float(sp.get("sample_start") or 0)
    out = TMP_DIR / f"sample_{job_id}_{index}.wav"
    if not out.exists() or out.stat().st_size < 2000:
        ffmpeg = find_ffmpeg("ffmpeg")
        if not ffmpeg:
            raise HTTPException(500, "FFmpeg no disponible para extraer la muestra.")
        subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-y", "-ss", f"{start:.2f}", "-t", "12",
             "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
            capture_output=True, timeout=300,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    if not out.exists() or out.stat().st_size < 1000:
        raise HTTPException(500, "No se pudo extraer la muestra de voz.")
    return FileResponse(str(out), media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})


# -----------------------------------------------------------------------------
# 18.4 Voces conocidas
# -----------------------------------------------------------------------------
@app.get("/api/voces")
def api_voces():
    vdb = _load_voice_db()
    return {
        "speakers": [
            {"name": s.get("name"), "samples": s.get("samples", 1),
             "updated": s.get("updated"), "created": s.get("created")}
            for s in vdb.get("speakers", [])
        ]
    }


@app.post("/api/voces")
def api_save_voice(payload: SaveVoiceRequest = Body(...)):
    payload = as_dict(payload)
    job = ensure_job_loaded(str(payload.get("job_id") or ""))
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    speaker_name = str(payload.get("speaker_name") or "")
    save_as = str(payload.get("save_as") or speaker_name).strip()
    embeddings = job.get("speaker_embeddings") or {}
    emb = embeddings.get(speaker_name)
    if emb is None:
        # Busca el embedding dentro de la lista de hablantes del trabajo
        for sp in job.get("speakers") or []:
            if sp.get("name") == speaker_name and sp.get("embedding"):
                emb = sp["embedding"]
                break
    if not emb:
        raise HTTPException(400, "No hay huella de voz disponible para ese hablante "
                                "(recuperado del historial). Vuelve a procesar el audio.")
    # --- clip de referencia (para escucharlo y recalcular la huella) -----
    sample_rel, quality = "", None
    sp_info = next((sp for sp in job.get("speakers") or [] if sp.get("name") == speaker_name), {})
    media = _resolve_media(job)
    if media and media.exists():
        clip = VOICES_DIR / f"{safe_slug(save_as, 'voz')}.wav"
        start = float(sp_info.get("sample_start") or 0.0)
        try:
            slice_wav(media, clip, start, start + 8.0)
            if clip.exists() and clip.stat().st_size > 2000:
                sample_rel = str(clip.relative_to(BASE_DIR))
                try:
                    from resemblyzer import VoiceEncoder  # type: ignore
                    from resemblyzer import preprocess_wav  # type: ignore
                    enc = VoiceEncoder("cpu", verbose=False)
                    wav = preprocess_wav(str(clip))
                    e2 = enc.embed_utterance(wav)
                    import numpy as _np
                    a = _np.asarray(emb, dtype="float32")
                    a = a / max(1e-8, float(_np.linalg.norm(a)))
                    b = _np.asarray(e2, dtype="float32")
                    b = b / max(1e-8, float(_np.linalg.norm(b)))
                    quality = float(a @ b)
                except Exception:
                    quality = None
        except Exception as exc:
            LOG.warning("No se pudo crear el clip de voz: %s", exc)
    try:
        saved = save_voice(save_as, emb, sample=sample_rel, quality=quality)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "speaker": {"name": saved.get("name"), "samples": saved.get("samples"),
                                    "sample": saved.get("sample"), "quality": saved.get("quality"),
                                    "updated": saved.get("updated")}}


@app.get("/api/voces/{name}/audio")
def api_voice_audio(name: str, request: Request):
    """Devuelve el clip de referencia de una voz conocida."""
    vdb = _load_voice_db()
    sp = next((x for x in vdb.get("speakers", [])
               if x.get("name", "").lower() == name.lower()), None)
    if not sp:
        raise HTTPException(404, "Voz no encontrada.")
    rel = sp.get("sample") or ""
    path = (BASE_DIR / rel) if rel else (VOICES_DIR / f"{safe_slug(name, 'voz')}.wav")
    if not path.exists():
        raise HTTPException(404, "Esta voz no tiene clip guardado.")
    return _range_file_response(path, request, media_type="audio/wav")


@app.post("/api/voces/calibrate")
def api_voices_calibrate(payload: Dict[str, Any] = Body(default={})):
    """
    Ajusta el umbral de reconocimiento de voces con la distribución de
    similitudes entre hablantes distintos (impostores). Sin muestras
    suficientes no cambia nada y lo explica.
    """
    import numpy as np  # type: ignore

    vdb = _load_voice_db()
    embs = {}
    for sp in vdb.get("speakers", []):
        emb = sp.get("embedding") or []
        if len(emb) >= 32:
            v = np.asarray(emb, dtype="float32")
            embs[sp.get("name") or "?"] = v / max(1e-8, float(np.linalg.norm(v)))
    if len(embs) < 3:
        return {"ok": False, "threshold": voice_match_threshold(vdb),
                "message": "Necesitas al menos 3 voces guardadas para calibrar con fiabilidad."}
    sims = [float(embs[a] @ embs[b])
            for i, a in enumerate(list(embs)) for b in list(embs)[i + 1:]]
    thr = float(np.percentile(sims, 95)) + 0.05
    thr = round(min(0.9, max(0.55, thr)), 3)
    vdb["eer_threshold"] = thr
    vdb["calibrated"] = now_iso()
    vdb["calibration"] = {
        "voices": len(embs), "pairs": len(sims),
        "impostor_mean": round(float(np.mean(sims)), 3),
        "impostor_p95": round(float(np.percentile(sims, 95)), 3),
        "threshold": thr,
    }
    write_json(VOICES_FILE, vdb)
    LOG.info("Umbral de voces calibrado en %.3f (%s pares).", thr, len(sims))
    return {"ok": True, "threshold": thr, **vdb["calibration"],
            "message": f"Umbral ajustado a {thr:.2f} con {len(sims)} comparaciones entre voces."}


@app.post("/api/jobs/{job_id}/speakers/merge")
def api_merge_speakers(job_id: str, payload: SpeakerMergeRequest = Body(...)):
    payload = as_dict(payload)
    """Fusiona un hablante en otro: todos sus fragmentos pasan al destino."""
    job = ensure_job_loaded(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    origin = str(payload.get("from") or "").strip()
    target = str(payload.get("into") or "").strip()
    if not origin or not target or origin == target:
        raise HTTPException(400, "Indica dos hablantes distintos (from / into).")
    segments = [dict(sg) for sg in ((job.get("result") or {}).get("segments") or [])]
    moved = 0
    for sg in segments:
        if (sg.get("speaker") or "") == origin:
            sg["speaker"] = target
            moved += 1
    if moved == 0:
        raise HTTPException(404, f"No hay fragmentos de «{origin}».")
    speakers = [dict(sp) for sp in (job.get("speakers") or []) if sp.get("name") != origin]
    sims = [sp for sp in (job.get("speakers") or []) if sp.get("name") == origin]
    for sp in speakers:
        if sp.get("name") == target:
            sp["count"] = sum(1 for sg in segments if (sg.get("speaker") or "") == target)
            sp["seconds"] = round(sum(float(sg["end"]) - float(sg["start"]) for sg in segments
                                      if (sg.get("speaker") or "") == target), 1)
    # la huella de voz del destino promedia la del origen si existía
    emb_src = (job.get("speaker_embeddings") or {}).get(origin)
    if emb_src:
        embs = job.setdefault("speaker_embeddings", {})
        old = embs.get(target)
        if old:
            embs[target] = [round((float(a) + float(b)) / 2, 5) for a, b in zip(old, emb_src)]
        else:
            embs[target] = emb_src
        embs.pop(origin, None)
    persist_edits(job, segments, speakers)
    job_log(job, f"Hablante «{origin}» fusionado en «{target}» ({moved} fragmentos).")
    return {"ok": True, "moved": moved, "speakers": len(speakers),
            "removed": bool(sims)}


@app.post("/api/jobs/{job_id}/speakers/split")
def api_split_speaker(job_id: str, payload: SpeakerSplitRequest = Body(...)):
    payload = as_dict(payload)
    """Separa fragmentos concretos de un hablante en una voz nueva."""
    job = ensure_job_loaded(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    origin = str(payload.get("name") or "").strip()
    ids = payload.get("segment_ids") or payload.get("ids") or []
    try:
        ids = {int(x) for x in ids}
    except Exception:
        raise HTTPException(400, "segment_ids debe ser una lista de números.")
    if not origin or not ids:
        raise HTTPException(400, "Indica el hablante y los fragmentos a separar.")
    new_name = str(payload.get("new_name") or "").strip()
    segments = [dict(sg) for sg in ((job.get("result") or {}).get("segments") or [])]
    speakers = [dict(sp) for sp in (job.get("speakers") or [])]
    if not new_name:
        used = {sp.get("name") for sp in speakers}
        n = len(speakers) + 1
        while f"Hablante {n}" in used:
            n += 1
        new_name = f"Hablante {n}"
    moved = 0
    moved_seconds = 0.0
    for sg in segments:
        if int(sg.get("id", -1)) in ids and (sg.get("speaker") or "") == origin:
            sg["speaker"] = new_name
            moved += 1
            moved_seconds += float(sg["end"]) - float(sg["start"])
    if not moved:
        raise HTTPException(404, "Ningún fragmento de ese hablante coincide con los ids.")
    src = next((sp for sp in speakers if sp.get("name") == origin), {})
    colors = [int(sp.get("color") or 0) for sp in speakers] or [0]
    speakers.append({
        "key": f"split{len(speakers)}",
        "name": new_name,
        "cluster": -len(speakers),
        "count": moved,
        "seconds": round(moved_seconds, 1),
        "known": False,
        "similarity": 0.0,
        "sample_start": round(min((float(sg["start"]) for sg in segments
                                   if (sg.get("speaker") or "") == new_name), default=0.0), 2),
        "color": (max(colors) + 1) % 10,
    })
    for sp in speakers:
        if sp.get("name") == origin:
            rest = [sg for sg in segments if (sg.get("speaker") or "") == origin]
            sp["count"] = len(rest)
            sp["seconds"] = round(sum(float(sg["end"]) - float(sg["start"]) for sg in rest), 1)
    persist_edits(job, segments, speakers)
    job_log(job, f"Voz nueva «{new_name}» creada con {moved} fragmentos de «{origin}».")
    return {"ok": True, "created": new_name, "moved": moved, "speakers": len(speakers)}


@app.delete("/api/voces/{name}")
def api_delete_voice(name: str):
    vdb = _load_voice_db()
    before = len(vdb["speakers"])
    vdb["speakers"] = [s for s in vdb["speakers"] if s.get("name", "").lower() != name.lower()]
    write_json(VOICES_FILE, vdb)
    return {"ok": True, "deleted": before - len(vdb["speakers"])}


# -----------------------------------------------------------------------------
# 18.5 Configuración de IA y redacción de actas
# -----------------------------------------------------------------------------
@app.get("/api/ia/config")
def api_ia_config():
    import importlib.util

    # si Ollama está encendido pero el modelo configurado no está descargado,
    # aquí se cambia solo a uno que sí exista (evita el 404 al redactar)
    try:
        cfg = ensure_ia_defaults()
    except Exception:
        cfg = _read_ia_config()
    ok_local_server, server_models, server_msg = probe_openai_server(
        cfg.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL
    )
    tiene_torch = _importable("torch")
    tiene_tf = _importable("transformers")
    motor = str(cfg.get("engine") or "local")
    listo = (
        (ok_local_server if motor == "openai"
         else (tiene_tf and tiene_torch) if motor == "local"
         else bool(cfg.get("gemini_api_key")))
    )
    return {
        "engine": motor,
        "listo": bool(listo),
        "falta": "" if listo else ia_engine_falta(motor),
        "comando": COMANDO_IA_LOCAL,
        "local_torch": tiene_torch,
        "local_transformers": tiene_tf,
        "engines": [
            {"key": k, **v,
             "ready": (
                 ok_local_server if k == "openai"
                 else ((tiene_tf and tiene_torch) if k == "local"
                       else bool(cfg.get("gemini_api_key")))
             )}
            for k, v in AI_ENGINES.items()
        ],
        "local_model": cfg.get("local_model", "Qwen/Qwen2.5-1.5B-Instruct"),
        "openai_base_url": cfg.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL,
        "openai_model": cfg.get("openai_model") or DEFAULT_OPENAI_MODEL,
        "openai_suggested": OPENAI_SUGGESTED_MODELS,
        "openai_online": ok_local_server,
        "openai_models": server_models,
        "openai_model_instalado": (not server_models) or (cfg.get("openai_model") in server_models),
        "openai_sugerido": mejor_modelo_instalado(server_models),
        "openai_msg": server_msg,
        "has_key": bool(cfg.get("gemini_api_key")),
        "key_masked": _mask_key(cfg.get("gemini_api_key", "")),
        "models": LOCAL_MODELS,
        "formats": [{"key": k, "label": v["label"], "hint": v["hint"]} for k, v in AI_FORMATS.items()],
        "local_ready": importlib.util.find_spec("transformers") is not None,
    }


@app.post("/api/ia/config")
def api_ia_config_save(payload: IaConfigRequest = Body(...)):
    payload = as_dict(payload)
    cfg = _read_ia_config()
    if "gemini_api_key" in payload:
        key = str(payload.get("gemini_api_key") or "").strip()
        cfg["gemini_api_key"] = key
    if payload.get("engine") in AI_ENGINES:
        cfg["engine"] = payload["engine"]
    if payload.get("local_model") in LOCAL_MODELS:
        cfg["local_model"] = payload["local_model"]
    if payload.get("openai_base_url"):
        cfg["openai_base_url"] = str(payload["openai_base_url"]).strip()[:200]
    if payload.get("openai_model"):
        cfg["openai_model"] = str(payload["openai_model"]).strip()[:120]
    _write_ia_config(cfg)
    return {"ok": True, "engine": cfg.get("engine"), "has_key": bool(cfg.get("gemini_api_key")),
            "key_masked": _mask_key(cfg.get("gemini_api_key", "")),
            "local_model": cfg.get("local_model"),
            "openai_base_url": cfg.get("openai_base_url"),
            "openai_model": cfg.get("openai_model")}


@app.post("/api/ia/test")
def api_ia_test(payload: Dict[str, Any] = Body(default={})):
    """Prueba de conexión del motor elegido (sin gastar tokens de pago)."""
    engine = str(payload.get("engine") or "local")
    cfg = _read_ia_config()
    if engine == "openai":
        base = str(payload.get("openai_base_url") or cfg.get("openai_base_url")
                   or DEFAULT_OPENAI_BASE_URL)
        ok, models, msg = probe_openai_server(base)
        pedido = str(payload.get("openai_model") or cfg.get("openai_model")
                     or DEFAULT_OPENAI_MODEL)
        sugerido = mejor_modelo_instalado(models)
        if ok and models and pedido not in models:
            # el servidor está encendido pero ese modelo no está descargado
            return {"ok": False, "engine": engine, "models": models, "sugerido": sugerido,
                    "message": (f"El servidor responde, pero el modelo «{pedido}» no está "
                                f"descargado. Ejecuta: ollama pull {pedido}"
                                + (f" — o escribe «{sugerido}», que sí lo tienes." if sugerido else ""))}
        if ok and sugerido:
            return {"ok": True, "engine": engine, "models": models, "sugerido": sugerido,
                    "message": f"Servidor listo ({len(models)} modelo/s). Se usará «{pedido}»."}
        return {"ok": ok, "engine": engine, "models": models, "message": msg, "sugerido": sugerido}
    if engine == "gemini":
        key = str(payload.get("api_key") or cfg.get("gemini_api_key") or "").strip()
        if not key:
            return {"ok": False, "engine": engine,
                    "message": "Falta la API Key. Puedes omitir Gemini y usar un motor local gratuito."}
        try:
            txt = call_gemini("Responde únicamente: OK", "Eres breve.", key, max_tokens=8)
            return {"ok": True, "engine": engine, "message": f"Gemini respondió: {txt[:40]}"}
        except Exception as exc:
            return {"ok": False, "engine": engine, "message": str(exc)}
    import importlib.util

    if importlib.util.find_spec("transformers") is None or importlib.util.find_spec("torch") is None:
        return {"ok": False, "engine": engine,
                "message": "Faltan torch y transformers. Instálalos con: "
                           "pip install torch --index-url https://download.pytorch.org/whl/cpu "
                           "y pip install transformers accelerate"}
    model = str(payload.get("local_model") or cfg.get("local_model")
                or "Qwen/Qwen2.5-1.5B-Instruct")
    ready = model_is_local(model)
    return {"ok": True, "engine": engine,
            "message": (f"Modelo {model} listo en ./modelos."
                        if ready else
                        f"Todo listo: {model} se descargará (~{LOCAL_MODELS.get(model, {}).get('size_gb', 3)} GB) "
                        "la primera vez que redactes un acta.")}


def _contexto_acta_previa(job: Dict[str, Any], pedido: str = "") -> Dict[str, Any]:
    """
    B2 · Elige la reunión anterior de la misma serie y devuelve SOLO sus acuerdos
    y pendientes, para dar seguimiento sin copiar contenido ni inventar datos.
    Si el usuario pide una reunión concreta (`pedido`), se respeta esa.
    """
    actual_id = str(job.get("id") or "")
    entradas = load_history()
    actual = next((e for e in entradas if e.get("job_id") == actual_id), {})
    candidatas = [e for e in entradas
                  if e.get("job_id") != actual_id and e.get("has_acta")]
    elegida = None
    if pedido:
        elegida = next((e for e in candidatas if e.get("job_id") == pedido), None)
    else:
        clave_serie = (actual.get("serie") or "").strip().lower()
        clave_titulo = (actual.get("title") or "").strip().lower()
        corte = str(actual.get("created") or "")
        def _clave(e):
            return ((e.get("serie") or "").strip().lower(),
                    (e.get("title") or "").strip().lower())
        mismo = []
        for e in candidatas:
            serie, titulo = _clave(e)
            if clave_serie and serie == clave_serie:
                mismo.append(e)
            elif not clave_serie and clave_titulo and titulo == clave_titulo:
                mismo.append(e)
        mismo.sort(key=lambda e: str(e.get("created") or ""), reverse=True)
        for e in mismo:
            if not corte or str(e.get("created") or "") < corte:
                elegida = e
                break
        if not elegida and mismo:
            elegida = mismo[0]
    if not elegida:
        return {"id": "", "title": "", "texto": ""}
    acta = leer_acta_guardada(str(elegida.get("job_id")))
    if not acta:
        return {"id": "", "title": "", "texto": ""}
    lineas: List[str] = []
    fecha = str(elegida.get("created") or "")[:10]
    for acuerdo in (acta.get("acuerdos") or [])[:12]:
        if not isinstance(acuerdo, dict) or not acuerdo.get("acuerdo"):
            continue
        detalle = ", ".join(x for x in (acuerdo.get("responsable"), acuerdo.get("fecha"),
                                        acuerdo.get("estatus")) if x)
        lineas.append(f"- Acuerdo: {acuerdo['acuerdo']}" + (f" ({detalle})" if detalle else ""))
    for pendiente in (acta.get("pendientes") or [])[:10]:
        if not isinstance(pendiente, dict) or not pendiente.get("tema"):
            continue
        detalle = ", ".join(x for x in (pendiente.get("responsable"), pendiente.get("fecha")) if x)
        lineas.append(f"- Pendiente: {pendiente['tema']}" + (f" ({detalle})" if detalle else ""))
    if not lineas:
        return {"id": "", "title": "", "texto": ""}
    cabecera = f"Reunión anterior de la serie: {elegida.get('title') or 'Reunión'}"
    if fecha:
        cabecera += f" ({fecha})"
    return {
        "id": str(elegida.get("job_id")),
        "title": str(elegida.get("title") or ""),
        "texto": (cabecera + "\n" + "\n".join(lineas))[:2600],
    }


def _ia_request_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza y valida la petición de acta (job, motor, formato, contexto)."""
    job_id = str(payload.get("job_id") or "")
    job = ensure_job_loaded(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        segments = (job.get("result") or {}).get("segments") or []
    if not segments:
        raise HTTPException(400, "No hay transcripción para resumir.")

    engine = str(payload.get("engine") or _read_ia_config().get("engine") or "local")
    if engine not in AI_ENGINES:
        engine = "local"
    fmt = str(payload.get("format") or "acta_completa")
    cfg = _read_ia_config()
    api_key = str(payload.get("api_key") or cfg.get("gemini_api_key") or "").strip()
    if payload.get("save_key") and api_key:
        cfg["gemini_api_key"] = api_key
        _write_ia_config(cfg)
    if engine == "gemini" and not api_key:
        raise HTTPException(400, "Falta la API Key de Gemini. Agrégala en el motor de IA "
                                 "o elige un motor local gratuito (Hugging Face u Ollama).")

    participants = payload.get("participants") or payload.get("participantes") or ""
    if isinstance(participants, list):
        participants = ", ".join(str(x).strip() for x in participants if str(x).strip())
    previa_ctx = _contexto_acta_previa(job, str(payload.get("acta_previa") or ""))
    return {
        "job": job,
        "job_id": job_id,
        "segments": segments,
        "engine": engine,
        "fmt": fmt,
        "api_key": api_key,
        "local_model": str(payload.get("local_model") or cfg.get("local_model")
                           or "Qwen/Qwen2.5-1.5B-Instruct"),
        "openai_base_url": str(payload.get("openai_base_url") or cfg.get("openai_base_url")
                               or DEFAULT_OPENAI_BASE_URL),
        "openai_model": str(payload.get("openai_model") or cfg.get("openai_model")
                            or DEFAULT_OPENAI_MODEL),
        "title": str(payload.get("title") or job.get("title") or ""),
        "notes": str(payload.get("notes") or "")[:800],
        "participants": str(participants)[:600],
        "glossary": str(payload.get("glossary") or payload.get("glosario") or "")[:1200],
        "acta_previa": previa_ctx["texto"],
        "acta_previa_id": previa_ctx["id"],
        "acta_previa_title": previa_ctx["title"],
        "serie": str(payload.get("serie") or ""),
    }


def _ia_cfg(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {"api_key": ctx["api_key"], "local_model": ctx["local_model"],
            "openai_base_url": ctx["openai_base_url"], "openai_model": ctx["openai_model"]}


def actualizar_entrada_historial(job_id: str, **campos: Any) -> None:
    """Actualiza campos de una entrada del historial (acta, serie, protección…)."""
    entradas = load_history()
    for entrada in entradas:
        if entrada.get("job_id") == job_id:
            entrada.update(campos)
            save_history(entradas)
            return


def ruta_acta_json(job_id: str) -> Path:
    return RESULT_DIR / f"{job_id}.acta.json"


def leer_acta_guardada(job_id: str) -> Dict[str, Any]:
    data = read_json(ruta_acta_json(job_id), {})
    return data if isinstance(data, dict) else {}


def _resumen_acuerdos(out: Dict[str, Any]) -> Dict[str, int]:
    return {
        "acuerdos_n": len([a for a in (out.get("acuerdos") or []) if isinstance(a, dict)]),
        "pendientes_n": len([p for p in (out.get("pendientes") or []) if isinstance(p, dict)]),
    }


def guardar_acta_editada(job_id: str, cambios: Dict[str, Any]) -> Dict[str, Any]:
    """Guarda el acta tal como la dejó el usuario (texto y/o acuerdos) en {id}.acta.json."""
    data = leer_acta_guardada(job_id)
    if not data:
        raise HTTPException(404, "Este trabajo todavía no tiene acta.")
    if "texto" in cambios and isinstance(cambios.get("texto"), str):
        data["texto_oficial"] = cambios["texto"]
        data["text"] = cambios["texto"]
    for clave in ("acuerdos", "pendientes", "siguientes_pasos"):
        if isinstance(cambios.get(clave), list):
            data[clave] = cambios[clave]
    if isinstance(data.get("acuerdos"), list):
        data["classified"] = {
            "acuerdos": [a.get("acuerdo", "") for a in data["acuerdos"] if isinstance(a, dict)
                         and a.get("acuerdo")],
            "pendientes": [p.get("tema", "") for p in (data.get("pendientes") or [])
                           if isinstance(p, dict) and p.get("tema")],
            "siguientes": list(data.get("siguientes_pasos") or []),
        }
    data["editado"] = True
    data["editado_el"] = now_iso()
    # el usuario revisó el texto: ya no está «desactualizada» por una parte nueva
    data.pop("desactualizada", None)
    data.pop("aviso", None)
    data.setdefault("texto_oficial", data.get("text") or "")
    write_json(ruta_acta_json(job_id), data)
    campos = {"has_acta": True, **_resumen_acuerdos(data),
              "acta_resumen": (data.get("texto_oficial") or "")[:700]}
    actualizar_entrada_historial(job_id, **campos)
    return data


def _olvidar_sesion(job_id: str) -> None:
    """Quita el acta/transcripción guardada en sesiones.json para ese trabajo."""
    try:
        db = load_sessions()
        tocado = False
        for ses in (db.get("sessions") or {}).values():
            if isinstance(ses, dict) and job_id in (ses.get("jobs") or {}):
                ses["jobs"].pop(job_id, None)
                tocado = True
        if tocado:
            write_json(SESSIONS_FILE, db)
    except Exception:
        pass


def _ia_save_result(ctx: Dict[str, Any], out: Dict[str, Any]) -> Dict[str, Any]:
    """Guarda el acta (texto + acuerdos estructurados) junto al resultado."""
    job = ctx["job"]
    classified = {
        "acuerdos": [a.get("acuerdo", "") for a in out.get("acuerdos") or [] if a.get("acuerdo")],
        "pendientes": [p.get("tema", "") for p in out.get("pendientes") or [] if p.get("tema")],
        "siguientes": list(out.get("siguientes_pasos") or []),
    }
    previo = leer_acta_guardada(ctx["job_id"])
    versiones = [v for v in (previo.get("versiones") or []) if isinstance(v, dict)]
    texto = out.get("text") or ""
    numero = max([int(v.get("v") or 0) for v in versiones] or [0]) + 1
    versiones.append({
        "v": numero, "texto": texto, "motor": ctx["engine"],
        "modelo": out.get("model") or "", "formato": ctx["fmt"],
        "creada": now_iso(), "chars": len(texto),
        "secciones": out.get("sections") or 0,
    })
    payload = {
        **out,
        "job_id": ctx["job_id"],
        "title": ctx["title"],
        "engine": ctx["engine"],
        "format": ctx["fmt"],
        "created": now_iso(),
        "classified": classified,
        "version": numero,
        "versiones": versiones[-10:],
        "texto_oficial": texto,
        "editado": False,
        "plantilla": leer_plantilla(),
        "acta_previa_id": ctx.get("acta_previa_id") or "",
        "acta_previa_title": ctx.get("acta_previa_title") or "",
    }
    try:
        save_session_payload(job.get("session") or "default", {
            "job_id": ctx["job_id"], "summary": out.get("text", ""),
            "summary_format": ctx["fmt"], "title": ctx["title"],
        })
    except Exception:
        pass
    try:
        write_json(ruta_acta_json(ctx["job_id"]), payload)
    except Exception as exc:
        LOG.warning("No se pudo guardar el acta en disco: %s", exc)
    try:
        actualizar_entrada_historial(ctx["job_id"], has_acta=True,
                                     acta_created=payload["created"],
                                     acta_version=numero,
                                     acta_resumen=(texto or "")[:700],
                                     serie=(ctx.get("serie") or leer_plantilla().get("organo") or ""),
                                     **_resumen_acuerdos(out))
    except Exception:
        pass
    return payload


@app.post("/api/ai/summarize")
@app.get("/api/ia/audio")
def api_ia_audio_estado():
    """
    Estado del «oído» local: ¿hay Ollama y qué modelos aceptan audio?
    Se usa para el panel opcional «Escuchar el audio con un modelo de audio».
    """
    cfg = _read_ia_config()
    base_url = cfg.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL
    ok, todos, msg = probe_openai_server(base_url)
    capaces = _ollama_audio_models(base_url, todos) if ok else []
    elegido = str(cfg.get("audio_model") or "").strip()
    if not elegido:
        elegido = next((m for m in capaces if "audio" in m.lower()), "")
        elegido = elegido or (capaces[0] if capaces else DEFAULT_AUDIO_MODEL)
    instalado = any(m == elegido or m.split(":")[0] == elegido.split(":")[0]
                    for m in todos) if todos else False
    if not ok:
        ayuda = ("No se detectó Ollama en " + _ollama_root(base_url) +
                 ". Instálalo (https://ollama.com) y ejecuta: ollama pull "
                 + DEFAULT_AUDIO_MODEL)
    elif not capaces:
        ayuda = ("Ollama responde, pero ningún modelo instalado acepta audio. "
                 "Ejecuta: ollama pull " + DEFAULT_AUDIO_MODEL +
                 " (2 GB) y vuelve a intentarlo.")
    else:
        ayuda = f"Listo: {len(capaces)} modelo(s) de audio disponible(s)."
    return {
        "ok": ok,
        "base": _ollama_root(base_url),
        "modelo": elegido,
        "modelos": capaces,
        "todos": todos,
        "instalado": instalado,
        "aviso": ayuda,
        "nota": ("Un modelo de audio no sustituye a la transcripción: no da marcas "
                 "de tiempo ni transcribe 3 horas. Sirve para saber de qué trata y "
                 "para revisar fragmentos cortos."),
    }


@app.post("/api/ia/audio/escuchar")
def api_ia_audio_escuchar(payload: Dict[str, Any] = Body(default={})):
    """
    Escucha un trozo corto del audio con un modelo de audio de Ollama
    (lfm3-audio por defecto) y devuelve qué se oye.
    Cuerpo: {job_id | file_id, segundos, desde, pregunta, modelo}
    """
    payload = payload if isinstance(payload, dict) else {}
    cfg = _read_ia_config()
    base_url = cfg.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL
    segundos = float(payload.get("segundos") or 30.0)
    desde = float(payload.get("desde") or 0.0)
    pregunta = str(payload.get("pregunta") or "").strip()[:600]
    modelo = str(payload.get("modelo") or cfg.get("audio_model")
                 or DEFAULT_AUDIO_MODEL).strip()[:120]

    # 1) ¿De dónde sale el audio?
    ruta: Optional[Path] = None
    job_id = str(payload.get("job_id") or "").strip()
    file_id = str(payload.get("file_id") or "").strip()
    if job_id:
        job = ensure_job_loaded(job_id)
        if not job:
            raise HTTPException(404, "Ese trabajo no existe.")
        try:
            ruta = _resolve_media(job)
        except HTTPException:
            ruta = None
    if ruta is None and file_id:
        meta = _lookup_upload(file_id)
        if meta.get("path") and Path(meta["path"]).exists():
            ruta = Path(meta["path"])
    if ruta is None:
        raise HTTPException(404, "No hay audio que escuchar: sube un archivo o abre "
                                 "una reunión del historial.")

    # 2) Trozo WAV de 16 kHz en memoria
    t0 = time.time()
    wav = clip_audio_wav(ruta, segundos=segundos, desde=desde)
    if len(wav) < 2048:
        raise HTTPException(400, "El trozo de audio salió vacío. Prueba con otro "
                                 "segundo de inicio.")

    # 3) Al modelo de audio
    texto, via = ollama_audio_chat(base_url, modelo, wav, pregunta,
                                   timeout=float(payload.get("timeout") or 300.0))
    ms = int((time.time() - t0) * 1000)
    cfg["audio_model"] = modelo
    try:
        _write_ia_config(cfg)
    except Exception:
        pass
    LOG.info("Modelo de audio %s escuchó %.0f s de %s (%s, %d ms)",
             modelo, segundos, ruta.name, via, ms)
    return {
        "ok": True,
        "modelo": modelo,
        "via": via,
        "texto": texto,
        "segundos": round(min(segundos, 180.0), 1),
        "desde": round(desde, 1),
        "kb_audio": round(len(wav) / 1024.0, 1),
        "ms": ms,
        "archivo": ruta.name,
    }


@app.post("/api/ia/summarize")
def api_summarize(payload: SummarizeRequest = Body(...)):
    payload = as_dict(payload)
    ctx = _ia_request_context(payload)
    try:
        out = summarize_with_ai(
            ctx["segments"], fmt=ctx["fmt"], engine=ctx["engine"], api_key=ctx["api_key"],
            local_model=ctx["local_model"], extra_notes=ctx["notes"], title=ctx["title"],
            openai_base_url=ctx["openai_base_url"], openai_model=ctx["openai_model"],
            participants=ctx["participants"], glossary=ctx["glossary"],
            acta_previa=ctx.get("acta_previa") or "",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"{exc}")
    return _ia_save_result(ctx, out)


@app.post("/api/ia/summarize/stream")
def api_summarize_stream(payload: SummarizeRequest = Body(...)):
    payload = as_dict(payload)
    """
    Igual que /api/ia/summarize pero en Server-Sent Events: el texto del acta
    llega token a token y el avance por tramos se ve en vivo.
    """
    ctx = _ia_request_context(payload)

    def event_stream():
        def send(obj: Dict[str, Any]) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        try:
            for kind, data in acta_pipeline(
                ctx["segments"], fmt=ctx["fmt"], engine=ctx["engine"], cfg=_ia_cfg(ctx),
                extra_notes=ctx["notes"], title=ctx["title"],
                participants=ctx["participants"], glossary=ctx["glossary"],
                acta_previa=ctx.get("acta_previa") or "", stream=True,
            ):
                if kind == "done":
                    final = _ia_save_result(ctx, data)
                    yield send({"type": "done", **final})
                else:
                    yield send({"type": kind, **data})
        except Exception as exc:
            yield send({"type": "error", "message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@app.get("/api/plantilla")
def api_get_plantilla():
    """Datos institucionales que encabezan y firman las actas."""
    return {"plantilla": leer_plantilla(), "defecto": PLANTILLA_DEFECTO}


@app.post("/api/plantilla")
def api_save_plantilla(payload: Dict[str, Any] = Body(default={})):
    return {"ok": True, "plantilla": guardar_plantilla(payload)}


class ActaEditRequest(_Loose):
    texto: Optional[str] = None
    acuerdos: Optional[List[Dict[str, Any]]] = None
    pendientes: Optional[List[Dict[str, Any]]] = None
    siguientes_pasos: Optional[List[str]] = None


@app.put("/api/jobs/{job_id}/acta")
def api_update_acta(job_id: str, payload: ActaEditRequest = body_default(ActaEditRequest)):
    """Guarda las ediciones del acta (texto y/o acuerdos) como versión oficial."""
    data = guardar_acta_editada(job_id, as_dict(payload))
    return {"ok": True, "acta": data}


@app.get("/api/jobs/{job_id}/acta/versiones")
def api_acta_versiones(job_id: str):
    data = leer_acta_guardada(job_id)
    if not data:
        raise HTTPException(404, "Este trabajo todavía no tiene acta.")
    versiones = [{"v": v.get("v"), "creada": v.get("creada"), "motor": v.get("motor"),
                  "modelo": v.get("modelo"), "formato": v.get("formato"),
                  "chars": v.get("chars"), "secciones": v.get("secciones")}
                 for v in (data.get("versiones") or []) if isinstance(v, dict)]
    if not versiones:
        # Actas anteriores a la v2.5: no guardaban historial de versiones, pero el
        # texto oficial sigue existiendo. Se muestra como versión única.
        texto = data.get("texto_oficial") or data.get("text") or ""
        if texto:
            numero = int(data.get("version") or 1)
            versiones = [{"v": numero, "creada": data.get("created") or data.get("editado_el"),
                          "motor": data.get("engine") or "", "modelo": data.get("model") or "",
                          "formato": data.get("format") or "", "chars": len(texto),
                          "secciones": data.get("sections") or 0}]
            data["version"] = numero
    return {"versiones": versiones, "actual": data.get("version"),
            "editado": bool(data.get("editado")), "version_oficial": data.get("version_oficial")}


@app.post("/api/jobs/{job_id}/acta/restaurar")
def api_acta_restaurar(job_id: str, payload: Dict[str, Any] = Body(default={})):
    data = leer_acta_guardada(job_id)
    if not data:
        raise HTTPException(404, "Este trabajo todavía no tiene acta.")
    try:
        numero = int(payload.get("v") or 0)
    except Exception:
        raise HTTPException(400, "Indica el número de versión.")
    version = next((v for v in (data.get("versiones") or [])
                    if isinstance(v, dict) and int(v.get("v") or 0) == numero), None)
    if not version:
        raise HTTPException(404, f"No existe la versión {numero}.")
    data["texto_oficial"] = version.get("texto") or ""
    data["text"] = data["texto_oficial"]
    data["version_oficial"] = numero
    data["editado"] = True
    data["editado_el"] = now_iso()
    write_json(ruta_acta_json(job_id), data)
    return {"ok": True, "v": numero, "texto": data["texto_oficial"]}


_MINUTO_RX = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")


@app.get("/api/compromisos")
def api_compromisos():
    """Todos los acuerdos y pendientes de todas las reuniones del historial."""
    items: List[Dict[str, Any]] = []
    for entrada in load_history():
        job_id = str(entrada.get("job_id") or "")
        data = leer_acta_guardada(job_id)
        if not data:
            continue
        for tipo, clave, campo in (("acuerdo", "acuerdos", "acuerdo"),
                                   ("pendiente", "pendientes", "tema")):
            for idx, item in enumerate(data.get(clave) or []):
                if not isinstance(item, dict):
                    continue
                texto = str(item.get(campo) or "").strip()
                if not texto:
                    continue
                m = _MINUTO_RX.search(texto) or _MINUTO_RX.search(str(item.get("fecha") or ""))
                items.append({
                    "job_id": job_id,
                    "reunion": entrada.get("title") or "",
                    "fecha_reunion": entrada.get("created") or "",
                    "serie": entrada.get("serie") or "",
                    "tipo": tipo,
                    "idx": idx,
                    "texto": texto,
                    "responsable": str(item.get("responsable") or "").strip(),
                    "fecha": str(item.get("fecha") or "").strip(),
                    "estatus": str(item.get("estatus") or "").strip()
                               or ("pendiente" if tipo == "pendiente" else ""),
                    "minuto": m.group(1) if m else "",
                })
    items.sort(key=lambda x: (x["estatus"].lower() == "hecho", x["fecha_reunion"]), reverse=True)
    return {"items": items, "total": len(items),
            "pendientes": sum(1 for i in items if i["estatus"].lower() not in ("hecho", "cumplido",
                                                                              "cerrado", "completado"))}


@app.patch("/api/compromisos/{job_id}")
def api_compromiso_editar(job_id: str, payload: Dict[str, Any] = Body(default={})):
    """Cambia estatus, responsable o fecha de un acuerdo/pendiente ya guardado."""
    data = leer_acta_guardada(job_id)
    if not data:
        raise HTTPException(404, "Ese acuerdo no existe (la reunión no tiene acta).")
    tipo = str(payload.get("tipo") or "acuerdo")
    clave = "acuerdos" if tipo == "acuerdo" else "pendientes"
    campo = str(payload.get("campo") or "estatus")
    if campo not in ("estatus", "responsable", "fecha", "texto"):
        raise HTTPException(400, "Campo no editable.")
    try:
        idx = int(payload.get("idx"))
    except Exception:
        raise HTTPException(400, "Índice inválido.")
    lista = data.get(clave) or []
    if idx < 0 or idx >= len(lista) or not isinstance(lista[idx], dict):
        raise HTTPException(404, "Ese acuerdo no existe.")
    destino = "acuerdo" if tipo == "acuerdo" else "tema"
    lista[idx][campo if campo != "texto" else destino] = str(payload.get("valor") or "").strip()[:400]
    data["editado"] = True
    data["editado_el"] = now_iso()
    write_json(ruta_acta_json(job_id), data)
    actualizar_entrada_historial(job_id, **_resumen_acuerdos(data))
    return {"ok": True, "item": lista[idx]}


@app.post("/api/historial/{job_id}")
def api_history_update(job_id: str, payload: Dict[str, Any] = Body(default={})):
    """Renombra, etiqueta (serie) o protege una entrada del historial."""
    entradas = load_history()
    entrada = next((e for e in entradas if e.get("job_id") == job_id), None)
    if not entrada:
        raise HTTPException(404, "Entrada de historial no encontrada.")
    if isinstance(payload.get("title"), str) and payload["title"].strip():
        entrada["title"] = payload["title"].strip()[:160]
    if isinstance(payload.get("serie"), str):
        entrada["serie"] = payload["serie"].strip()[:80]
    if "protegido" in payload:
        entrada["protegido"] = bool(payload.get("protegido"))
    save_history(entradas)
    return {"ok": True, "entrada": entrada}


@app.post("/api/historial/{job_id}/duplicar")
def api_history_duplicate(job_id: str, payload: Dict[str, Any] = Body(default={})):
    """
    C4 · Duplica una reunión como base editable: copia el acta (texto, acuerdos,
    pendientes y plantilla) en una entrada nueva, sin audio ni transcripción.
    Sirve para reuniones periódicas: se abre la copia, se actualiza y se guarda.
    """
    origen_acta = leer_acta_guardada(job_id)
    if not origen_acta:
        raise HTTPException(404, "Esta reunión todavía no tiene acta que duplicar.")
    entradas = load_history()
    origen = next((e for e in entradas if e.get("job_id") == job_id), None)
    if not origen:
        raise HTTPException(404, "Esa reunión ya no está en el historial.")
    nuevo_id = uuid.uuid4().hex[:12]
    titulo = str(payload.get("title") or "").strip() or \
        f"{origen.get('title') or 'Reunión'} (copia)"
    texto = origen_acta.get("texto_oficial") or origen_acta.get("text") or ""
    copia = {
        **{k: v for k, v in origen_acta.items()
           if k in ("acuerdos", "pendientes", "siguientes_pasos", "figures", "plantilla")},
        "job_id": nuevo_id,
        "title": titulo,
        "duplicado_de": job_id,
        "duplicado": True,
        "engine": "duplicado",
        "format": origen_acta.get("format") or "acta_completa",
        "created": now_iso(),
        "classified": origen_acta.get("classified") or {},
        "version": 1,
        "versiones": [{"v": 1, "texto": texto, "motor": "duplicado", "modelo": "",
                       "formato": origen_acta.get("format") or "acta_completa",
                       "creada": now_iso(), "chars": len(texto)}],
        "texto_oficial": texto,
        "text": texto,
        "editado": True,
        "editado_el": now_iso(),
        "plantilla": leer_plantilla(),
    }
    write_json(ruta_acta_json(nuevo_id), copia)
    # Resultado mínimo: la copia se abre en la interfaz sin audio ni transcripción
    write_json(RESULT_DIR / f"{nuevo_id}.json", {
        "job_id": nuevo_id, "title": titulo, "duplicado": True,
        "segments": [], "speakers": [], "stats": {}, "info": {},
        "created": now_iso(), "title_origen": origen.get("title") or "",
    })
    entrada = {
        "job_id": nuevo_id,
        "session_id": "",
        "title": titulo,
        "filename": "",
        "created": now_iso(),
        "duration": 0.0,
        "duration_human": "sin audio",
        "language": origen.get("language") or "",
        "mode": origen.get("mode") or DEFAULT_MODE,
        "speakers": 0,
        "words": 0,
        "media": "",
        "media_bytes": 0,
        "result_json": f"resultados/{nuevo_id}.json",
        "has_acta": True,
        "duplicado": True,
        "duplicado_de": job_id,
        "acta_version": 1,
        "acta_resumen": (texto or "")[:700],
        "serie": origen.get("serie") or "",
        "protegido": False,
        **_resumen_acuerdos(copia),
    }
    entradas.insert(0, entrada)
    save_history(evict_history(entradas))
    LOG.info("Acta duplicada: %s → %s", job_id, nuevo_id)
    return {"ok": True, "job_id": nuevo_id, "title": titulo, "entry": entrada,
            "avisos": ["La copia no tiene audio ni transcripción: es una base editable "
                       "del acta para actualizarla."]}


@app.get("/api/jobs/{job_id}/acta")
def api_get_acta(job_id: str):
    """Última acta guardada de un trabajo (para reabrirla sin regenerar)."""
    data = read_json(RESULT_DIR / f"{job_id}.acta.json", {}) or {}
    if not data:
        raise HTTPException(404, "Este trabajo todavía no tiene acta guardada.")
    return data


# -----------------------------------------------------------------------------
# 18.6 Exportaciones (siempre con lo que el usuario ve en pantalla)
# -----------------------------------------------------------------------------
def _clean_segments(raw: Any) -> List[Dict[str, Any]]:
    """Normaliza los fragmentos que envía la interfaz (ya editados)."""
    out: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for idx, sg in enumerate(raw):
        if not isinstance(sg, dict):
            continue
        text = str(sg.get("text") or "").strip()
        if not text:
            continue
        try:
            start = max(0.0, float(sg.get("start") or 0.0))
        except Exception:
            start = 0.0
        try:
            end = max(start, float(sg.get("end") or start))
        except Exception:
            end = start
        out.append({
            "id": idx,
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "speaker": str(sg.get("speaker") or ""),
            "avg_logprob": float(sg.get("avg_logprob") or 0.0),
            "no_speech_prob": float(sg.get("no_speech_prob") or 0.0),
        })
    return out


def persist_edits(job: Dict[str, Any], segments: Optional[List[Dict[str, Any]]] = None,
                  speakers: Optional[List[Dict[str, Any]]] = None,
                  title: Optional[str] = None) -> Dict[str, Any]:
    """
    Guarda en resultados/{id}.json las correcciones hechas en la interfaz
    (texto, hablantes, nombres) para que exportar, recargar o reabrir el
    historial use SIEMPRE la versión del usuario y no la original de Whisper.
    """
    result = dict(job.get("result") or {})
    if segments is not None:
        result["segments"] = segments
        job["result"] = result
    if speakers is not None:
        job["speakers"] = speakers
        result["speakers"] = speakers
    if title:
        result["title"] = title
        job["title"] = title
    path = RESULT_DIR / f"{job['id']}.json"
    if path.exists() or job.get("status") == "done":
        try:
            stored = read_json(path, {}) or {}
            stored.update(result)
            stored["segments"] = result.get("segments") or []
            stored["speakers"] = job.get("speakers") or []
            stored["title"] = job.get("title") or stored.get("title")
            stored["edited"] = now_iso()
            write_json(path, stored)
        except Exception as exc:
            LOG.warning("No se pudieron guardar las correcciones de %s: %s", job.get("id"), exc)
    # el historial muestra el título y los nombres de hablante
    try:
        entries = load_history()
        changed = False
        for entry in entries:
            if entry.get("job_id") == job.get("id"):
                if title:
                    entry["title"] = title
                    changed = True
                if speakers is not None:
                    entry["speakers"] = [sp.get("name") for sp in speakers if sp.get("name")]
                    changed = True
        if changed:
            save_history(entries)
    except Exception:
        pass
    return {"segments": len(result.get("segments") or []),
            "speakers": len(job.get("speakers") or []),
            "saved": bool(path.exists())}


@app.patch("/api/jobs/{job_id}")
def api_patch_job(job_id: str, payload: JobPatchRequest = body_default(JobPatchRequest)):
    payload = as_dict(payload)
    """Guarda ediciones del transcriptor (texto, hablantes, título) en disco."""
    job = ensure_job_loaded(job_id)
    if not job:
        raise HTTPException(404, "Trabajo no encontrado.")
    segs = _clean_segments(payload.get("segments")) if "segments" in payload else None
    speakers = payload.get("speakers") if isinstance(payload.get("speakers"), list) else None
    title = str(payload.get("title") or "").strip() or None
    if segs is None and speakers is None and not title:
        raise HTTPException(400, "Nada que guardar (envía segments, speakers o title).")
    info = persist_edits(job, segs, speakers, title)
    return {"ok": True, **info}


@app.get("/api/jobs/{job_id}/partial")
def api_job_partial(job_id: str, since: int = 0):
    """
    Resultados parciales para la vista en vivo: devuelve solo los fragmentos
    nuevos a partir de `since` (parche incremental, sin re-render completo).
    """
    path = partial_path(job_id)
    data = read_json(path, {}) or {}
    segs = data.get("segments") or []
    since = max(0, int(since or 0))
    job = ensure_job_loaded(job_id) if not data else JOBS.get(job_id)
    return {
        "job_id": job_id,
        "status": (job or {}).get("status") or data.get("status") or "desconocido",
        "progress": (job or {}).get("progress") if job else data.get("progress"),
        "count": len(segs),
        "since": since,
        "segments": segs[since:],
        "updated": data.get("updated"),
        "done": not path.exists(),
    }


class ContinuarRequest(_Loose):
    file_id: Any = ""


@app.post("/api/jobs/{job_id}/continuar")
def api_job_continuar(job_id: str, payload: ContinuarRequest = Body(...)):
    """
    C6 · Añade otra parte de audio a una reunión ya transcrita (por ejemplo,
    después de un receso) y redacta un solo acta con ambas partes.
    """
    payload = as_dict(payload)
    padre = ensure_job_loaded(job_id)
    if not padre:
        raise HTTPException(404, "Reunión no encontrada.")
    if padre.get("duplicado"):
        raise HTTPException(400, "Esta entrada es una copia del acta: no tiene audio que continuar.")
    resultado = padre.get("result") or {}
    if not resultado.get("segments"):
        raise HTTPException(400, "Primero termina la transcripción de esta reunión.")
    with JOBS_LOCK:
        en_curso = [j for j in JOBS.values()
                    if j.get("continua_de") == job_id and j.get("status") not in
                    ("done", "error", "cancelled")]
    if en_curso:
        raise HTTPException(409, "Ya hay una parte de esta reunión en proceso. "
                                 "Espera a que termine.")
    meta = _lookup_upload(str(payload.get("file_id") or ""))
    src = Path(meta["path"])
    if not src.exists():
        raise HTTPException(404, "El archivo ya no está en disco. Súbelo de nuevo.")
    partes = resultado.get("partes") or []
    parte = len(partes) + 1 if partes else 2
    nuevo_id = uuid.uuid4().hex[:12]
    options = dict(padre.get("options") or {})
    options.pop("api_key", None)
    job = {
        "id": nuevo_id,
        "session": padre.get("session") or "default",
        "file_id": meta.get("file_id"),
        "filename": meta.get("filename", "parte"),
        "title": padre.get("title") or "",
        "source_path": str(src),
        "media_path": str(src),
        "duration": float(meta.get("duration") or 0),
        "options": options,
        "status": "queued",
        "progress": 0,
        "message": "En cola de procesamiento…",
        "phase": "queued",
        "created": now_iso(),
        "started": None,
        "finished": None,
        "stats": {},
        "speakers": [],
        "warnings": [],
        "log": [{"t": now_iso(),
                 "m": f"Parte {parte} de la reunión «{padre.get('title') or job_id}» "
                      f"en cola de procesamiento."}],
        "phases": {},
        "result": None,
        "cancel": threading.Event(),
        "continua_de": job_id,
        "parte": parte,
    }
    with JOBS_LOCK:
        JOBS[nuevo_id] = job
    persist_job_meta(job)
    WORK_QUEUE.put(nuevo_id)
    LOG.info("Parte %s encolada para la reunión %s (%s)", parte, job_id, job["filename"])
    return {"ok": True, "job_id": nuevo_id, "parte": parte, "reunion": job_id,
            "job": public_job(job)}


@app.post("/api/jobs/{job_id}/resume")
def api_resume_job(job_id: str):
    """Reanuda un trabajo interrumpido desde su último checkpoint."""
    ckpt_file = checkpoint_path(job_id)
    if not ckpt_file.exists():
        raise HTTPException(404, "No hay checkpoint para este trabajo.")
    job = ensure_job_loaded(job_id) or JOBS.get(job_id)
    if job and job.get("status") in ("queued", "preprocessing", "transcribing", "diarizing"):
        return {"ok": True, "message": "El trabajo ya está en marcha."}
    recovered = recover_interrupted_jobs()
    return {"ok": recovered > 0, "recovered": recovered}


@app.post("/api/export/{job_id}")
def api_export(job_id: str, payload: ExportRequest = body_default(ExportRequest)):
    payload = as_dict(payload)
    fmt = str(payload.get("format") or "docx").lower()
    job = ensure_job_loaded(job_id)
    if not job and fmt not in ("acta", "acta_docx"):
        raise HTTPException(404, "Trabajo no encontrado.")
    job = job or {"id": job_id, "title": payload.get("title") or ""}
    conclusion = str(payload.get("conclusion") or "")
    title = str(payload.get("title") or job.get("title") or "Acta de reunión")
    base = safe_slug(title, "acta")
    # las cabeceras HTTP solo admiten ASCII: el nombre real va en filename*
    ascii_base = base.encode("ascii", "ignore").decode("ascii").strip("_") or "acta"

    # --- el navegador manda lo que el usuario ve (editado); tiene prioridad
    speakers = job.get("speakers") or []
    if isinstance(payload.get("speakers"), list) and payload.get("speakers"):
        speakers = [sp for sp in payload["speakers"] if isinstance(sp, dict)]
    incoming = payload.get("segments")
    segments = _clean_segments(incoming) if isinstance(incoming, list) and incoming else None
    if segments is not None or payload.get("speakers"):
        try:
            persist_edits(job, segments, speakers if payload.get("speakers") else None, None)
        except Exception as exc:
            LOG.warning("No se pudieron persistir las ediciones al exportar: %s", exc)
    export_job = dict(job)
    if segments is not None:
        export_job["result"] = dict(job.get("result") or {}, segments=segments)
    export_job["speakers"] = speakers

    if fmt == "docx":
        try:
            path = build_docx(export_job, conclusion=conclusion, speakers=speakers, title=title)
        except Exception as exc:
            raise HTTPException(500, str(exc))
        return FileResponse(
            str(path),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename=acta_{job_id}.docx; "
                                       f"filename*=UTF-8''{urllib.parse.quote(base)}.docx",
                "Cache-Control": "no-store",
            },
        )
    if fmt in ("acta", "acta_docx"):
        guardada = leer_acta_guardada(job_id)
        texto = (guardada.get("texto_oficial") or guardada.get("text") or conclusion or "").strip()
        if not texto:
            raise HTTPException(404, "Este trabajo todavía no tiene acta guardada.")
        try:
            path = build_acta_docx(title=title, texto=texto, plantilla=guardada.get("plantilla"))
        except Exception as exc:
            raise HTTPException(500, str(exc))
        return FileResponse(
            str(path),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f"attachment; filename=acta_solo_{job_id}.docx; "
                                            f"filename*=UTF-8''{urllib.parse.quote(base)}_acta.docx",
                     "Cache-Control": "no-store"},
        )
    if fmt == "txt":
        content, mime = build_plain_text(export_job, conclusion=conclusion, title=title), "text/plain; charset=utf-8"
        ext = "txt"
    elif fmt == "srt":
        content, mime, ext = build_srt(export_job), "application/x-subrip; charset=utf-8", "srt"
    elif fmt == "vtt":
        content, mime, ext = build_vtt(export_job), "text/vtt; charset=utf-8", "vtt"
    else:
        raise HTTPException(400, "Formato no soportado. Usa docx, txt, srt o vtt.")
    return Response(
        content=content.encode("utf-8-sig"),
        media_type=mime,
        headers={
            "Content-Disposition": f"attachment; filename={ascii_base}.{ext}; "
                                   f"filename*=UTF-8''{urllib.parse.quote(base)}.{ext}",
            "Cache-Control": "no-store",
        },
    )


# -----------------------------------------------------------------------------
# 18.7 Historial y sesiones
# -----------------------------------------------------------------------------
def normalizar_entradas_historial(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Completa las entradas antiguas del historial (creadas antes de la v2.5) para
    que la lista muestre acta, acuerdos, pendientes, serie y protección sin tener
    que volver a transcribir nada.
    """
    cambiado = False
    for entrada in entries:
        job_id = str(entrada.get("job_id") or "")
        if "media_bytes" not in entrada and entrada.get("size"):
            entrada["media_bytes"] = int(entrada.get("size") or 0)
            cambiado = True
        for clave, valor in (("serie", ""), ("protegido", False), ("acta_version", 0),
                             ("acuerdos_n", 0), ("pendientes_n", 0), ("partes", 1)):
            if clave not in entrada:
                entrada[clave] = valor
                cambiado = True
        if "has_acta" not in entrada:
            acta = leer_acta_guardada(job_id) if job_id else {}
            entrada["has_acta"] = bool(acta)
            if acta:
                entrada.update(_resumen_acuerdos(acta))
                entrada["acta_version"] = int(acta.get("version") or 0)
                entrada["acta_resumen"] = (acta.get("texto_oficial")
                                           or acta.get("text") or "")[:700]
                if not entrada.get("serie"):
                    entrada["serie"] = str((acta.get("plantilla") or {}).get("organo") or "")
            cambiado = True
    if cambiado:
        try:
            save_history(entries)
        except Exception:
            pass
    return entries


@app.get("/api/historial")
def api_historial():
    entries = normalizar_entradas_historial(load_history())
    total = sum(e.get("media_bytes", 0) for e in entries)
    # qué se borraría si entrara una reunión nueva (transparencia de la retención)
    proximos = [{"job_id": e.get("job_id"), "title": e.get("title"), "created": e.get("created")}
                for e in _entradas_fuera(entries)]
    return {
        "entries": entries,
        "count": len(entries),
        "bytes": total,
        "max_entries": MAX_HISTORY,
        "max_mb": MAX_HISTORY_BYTES // (1024 * 1024),
        "protegidas": sum(1 for e in entries if e.get("protegido")),
        "proximos_a_borrar": proximos[:5],
        "series": sorted({str(e.get("serie") or "").strip() for e in entries
                          if str(e.get("serie") or "").strip()}),
    }


@app.delete("/api/historial/{job_id}")
def api_history_delete(job_id: str):
    entries = load_history()
    entry = next((e for e in entries if e.get("job_id") == job_id), None)
    if not entry:
        raise HTTPException(404, "Entrada de historial no encontrada.")
    borrados = _borrar_archivos_entrada(entry)
    _olvidar_sesion(job_id)          # quita también el acta guardada en sesiones.json
    save_history([e for e in entries if e.get("job_id") != job_id])
    with JOBS_LOCK:
        JOBS.pop(job_id, None)
    return {"ok": True, "borrados": borrados}


@app.get("/api/session/{session_id}")
def api_get_session(session_id: str):
    return get_session(session_id)


@app.post("/api/session/{session_id}/save")
def api_save_session(session_id: str, payload: SessionSaveRequest = Body(...)):
    payload = as_dict(payload)
    rec = save_session_payload(session_id, payload)
    return {"ok": True, "record": rec}


# -----------------------------------------------------------------------------
# 19. ARRANQUE
# -----------------------------------------------------------------------------
def _open_browser(url: str) -> None:
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass


def _version_de(paquete: str) -> str:
    """Versión de un paquete sin fallar si no está instalado.

    Distingue tres casos: importable (devuelve la versión), instalado pero roto
    (por ejemplo resemblyzer sin torch) y no instalado.
    """
    try:
        mod = __import__(paquete)
    except Exception:
        try:
            from importlib.metadata import version as _dist_version
            for nombre in (paquete, paquete.replace("_", "-"), paquete.replace("-", "_")):
                try:
                    return f"{_dist_version(nombre)} (instalado, no importable)"
                except Exception:
                    continue
        except Exception:
            pass
        return "no instalado"
    for attr in ("__version__", "VERSION", "version"):
        v = getattr(mod, attr, None)
        if isinstance(v, str) and v.strip():
            return v.split("\n")[0].strip()
    return "instalado"


def _importable(paquete: str) -> bool:
    """True si el paquete se puede importar de verdad (no solo si está listado)."""
    try:
        __import__(paquete)
        return True
    except Exception:
        return False


def aplicar_motor_ollama(modelo: str, base_url: str = "",
                         esperar: float = 0.0) -> Dict[str, Any]:
    """
    Deja la app configurada para redactar con Ollama y el modelo indicado.
    Lo usa instalar.bat (`python app.py --usar-ollama qwen2.5:7b-instruct`).
    Devuelve {"ok", "engine", "model", "models", "mensaje"}.
    """
    modelo = (modelo or "").strip()
    base = (base_url or "").strip() or DEFAULT_OPENAI_BASE_URL
    if esperar > 0:
        # tras instalar Ollama el servicio tarda unos segundos en levantar
        limite = time.time() + esperar
        while time.time() < limite:
            ok, modelos, _m = probe_openai_server(base, timeout=1.5)
            if ok and modelos:
                break
            time.sleep(1.5)
    cfg = _read_ia_config()
    ok, modelos, msg = probe_openai_server(base, timeout=3.0)
    if not ok:
        return {"ok": False, "engine": cfg.get("engine"), "model": modelo, "models": [],
                "mensaje": "Ollama no responde en " + base + ". Ábrelo (menú Inicio → "
                           "Ollama) y vuelve a ejecutar: ollama pull " + (modelo or "qwen2.5:7b-instruct")}
    if modelo and modelos and modelo not in modelos:
        sugerido = mejor_modelo_instalado(modelos)
        return {"ok": False, "engine": cfg.get("engine"), "model": modelo, "models": modelos,
                "sugerido": sugerido,
                "mensaje": (f"El modelo «{modelo}» no está descargado todavía. "
                            f"Ejecuta: ollama pull {modelo}"
                            + (f" — o usa «{sugerido}», que sí está." if sugerido else ""))}
    if not modelo:
        modelo = mejor_modelo_instalado(modelos)
    cfg["engine"] = "openai"
    cfg["openai_base_url"] = base
    cfg["openai_model"] = modelo
    try:
        _write_ia_config(cfg)
    except Exception as exc:
        return {"ok": False, "engine": cfg.get("engine"), "model": modelo, "models": modelos,
                "mensaje": f"No se pudo guardar ia_config.json: {exc}"}
    LOG.info("Motor de IA configurado en Ollama con el modelo «%s».", modelo)
    return {"ok": True, "engine": "openai", "model": modelo, "models": modelos,
            "mensaje": f"Listo: el acta se redactará con Ollama · {modelo}."}


def diagnose() -> int:
    """Diagnóstico de instalación: `python app.py --check`.

    Sirve para saber en un vistazo qué versión de Pydantic hay, si las
    dependencias están completas y si los modelos de petición funcionan.
    Devuelve 0 si todo está listo y 1 si hay algo que impide arrancar.
    """
    problemas: List[str] = []
    avisos: List[str] = []

    try:
        propio = Path(__file__).resolve()
        lineas = len(propio.read_text(encoding="utf-8", errors="ignore").splitlines())
        huella = hashlib.sha256(propio.read_bytes()).hexdigest()[:12]
        archivo = f"{propio} ({lineas} líneas · sha256 {huella})"
    except Exception:
        archivo = str(globals().get("__file__", "?"))

    def linea(etiqueta: str, valor: object) -> None:
        print(f" {etiqueta + ' ':.<21} {valor}")

    print("=" * 74)
    print(" Transcriptor de Audio y Generador de Actas · DIAGNÓSTICO")
    print("=" * 74)
    linea("Aplicación", f"v{VERSION}")
    linea("Archivo", archivo)
    linea("Python", f"{sys.version.split()[0]} ({sys.platform})")
    modo = "Pydantic 2" if PYDANTIC_V2 else "compatibilidad con Pydantic 1"
    linea("Pydantic", f"{PYDANTIC_VERSION} → modo {modo}")
    if not PYDANTIC_V2:
        avisos.append('Pydantic 1.x: la app funciona, pero conviene actualizar con '
                      '.venv\\Scripts\\pip install -U "pydantic>=2.7"')
    for etiqueta, paquete in (("FastAPI", "fastapi"), ("Uvicorn", "uvicorn"),
                              ("NumPy", "numpy"), ("SciPy", "scipy"),
                              ("python-docx", "docx")):
        version = _version_de(paquete)
        linea(etiqueta, version)
        if version == "no instalado" and paquete in ("fastapi", "uvicorn"):
            problemas.append(f"Falta {etiqueta}: pip install -r requirements.txt")

    # FastAPI antiguo + Pydantic 2 es otra combinación que revienta al importar
    try:
        import fastapi as _fastapi
        _fmajor, _fminor = (list(map(int, str(_fastapi.__version__).split(".")[:2])) + [0, 0])[:2]
        if PYDANTIC_V2 and (_fmajor, _fminor) < (0, 100):
            problemas.append(f"FastAPI {_fastapi.__version__} es demasiado antiguo para Pydantic 2: "
                             'actualiza con pip install -U "fastapi>=0.110"')
        elif not PYDANTIC_V2 and (_fmajor, _fminor) >= (0, 100) and (_fmajor, _fminor) < (0, 110):
            avisos.append(f"FastAPI {_fastapi.__version__} con Pydantic 1: funciona, "
                          "pero actualizar ambos es lo recomendable")
    except Exception:
        pass

    for etiqueta, paquete, opcional in (("faster-whisper", "faster_whisper", True),
                                        ("resemblyzer", "resemblyzer", True),
                                        ("webrtcvad", "webrtcvad", True),
                                        ("transformers", "transformers", True)):
        version = _version_de(paquete)
        linea(etiqueta, version + ("  (opcional)" if version == "no instalado" else ""))
        if version == "no instalado" and not opcional:
            problemas.append(f"Falta {etiqueta}")

    # webrtcvad y resemblyzer van de la mano: sin ambos (y sin torch) no hay
    # diarización. Se comprueba la importación real, no solo que estén listados.
    tiene_vad = _importable("webrtcvad")
    tiene_res = _importable("resemblyzer")
    if tiene_vad and tiene_res:
        linea("Diarización", "activa (identifica hablantes)")
    else:
        linea("Diarización", "desactivada: la app transcribe igual")
        if not tiene_vad:
            avisos.append("Falta el detector de voz: instala webrtcvad-wheels "
                          '(NO "webrtcvad", que exige compilador C++): '
                          "pip install webrtcvad-wheels")
        elif not tiene_res:
            avisos.append("resemblyzer está instalado pero no se puede importar "
                          "(normalmente falta torch): "
                          "pip install torch --index-url https://download.pytorch.org/whl/cpu")

    # modelos de voz ya descargados (lo que el instalador puede dejar listo)
    try:
        bajados = []
        for clave, cfg in MODES.items():
            if whisper_is_local(cfg["model"]):
                tam = _tam_modelo(cfg["model"])
                bajados.append(f"{clave} ({bytes_humanos(tam) if tam else 'listo'})")
        if bajados:
            linea("Modelos de voz", " · ".join(bajados))
        else:
            linea("Modelos de voz", "ninguno descargado (se bajan al transcribir)")
    except Exception:
        pass

    # IA para redactar el acta: qué motor va a funcionar de verdad
    try:
        cfg_ia = _read_ia_config()
        motor = str(cfg_ia.get("engine") or "local")
        tiene_tf, tiene_torch = _importable("transformers"), _importable("torch")
        ollama_ok, ollama_models, _m = probe_openai_server(
            cfg_ia.get("openai_base_url") or DEFAULT_OPENAI_BASE_URL, timeout=1.5)
        if motor == "local" and tiene_tf and tiene_torch:
            linea("IA del acta", "local (Hugging Face) · " + str(cfg_ia.get("local_model")))
        elif motor == "openai" and ollama_ok:
            pedido = str(cfg_ia.get("openai_model") or "")
            if ollama_models and pedido not in ollama_models:
                linea("IA del acta", f"Ollama · ¡falta el modelo {pedido}! "
                                     f"(tienes {len(ollama_models)} modelo/s)")
                avisos.append(f"Ollama está encendido pero el modelo «{pedido}» no está "
                              f"descargado: ejecuta ollama pull {pedido}"
                              + (f" o usa «{mejor_modelo_instalado(ollama_models)}»."
                                 if ollama_models else ""))
            else:
                linea("IA del acta", f"Ollama · {pedido} "
                                     f"({len(ollama_models)} modelo/s)")
        elif motor == "gemini" and cfg_ia.get("gemini_api_key"):
            linea("IA del acta", "Gemini 2.5 Flash (con API Key)")
        elif tiene_tf and tiene_torch:
            linea("IA del acta", "local (Hugging Face) disponible")
        elif ollama_ok:
            linea("IA del acta", f"Ollama disponible ({cfg_ia.get('openai_model')})")
        else:
            linea("IA del acta", "NO disponible: el acta no se podrá redactar")
            avisos.append("Sin IA para el acta. Ejecuta instalar.bat y elige la opción 1 "
                          "en la etapa «IA para redactar el acta», o instala Ollama y "
                          "ejecuta ollama pull qwen2.5:7b-instruct. Comando directo: "
                          + COMANDO_IA_LOCAL)
    except Exception:
        pass

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    linea("FFmpeg", ffmpeg or "NO encontrado en el PATH")
    linea("FFprobe", ffprobe or "NO encontrado en el PATH")
    if not ffmpeg:
        avisos.append("Sin FFmpeg no se limpia el audio ni se leen vídeos "
                      "(winget install Gyan.FFmpeg)")

    # modelos de petición: la parte que fallaba con Pydantic 1
    try:
        prueba = JobCreateRequest(file_id="x", title="  Prueba  ")
        datos = as_dict(prueba)
        fusion = as_dict(SpeakerMergeRequest(**{"from": "Ana", "into": "Ana López"}))
        assert datos.get("file_id") == "x", datos
        assert "model_config" not in datos, "model_config se coló como campo"
        assert "no_existe" not in as_dict(JobPatchRequest(**{"no_existe": 1})), "extra no ignorado"
        assert fusion.get("from") == "Ana" and fusion.get("into") == "Ana López", fusion
        linea("Modelos", "OK (alias «from», campos extra ignorados)")
    except Exception as exc:  # pragma: no cover
        problemas.append(f"Los modelos de petición fallan: {exc!r}")
        linea("Modelos", f"ERROR → {exc!r}")

    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        chivato = TMP_DIR / ".diagnostico"
        chivato.write_text("ok", encoding="utf-8")
        chivato.unlink()
        linea("Escritura", "OK")
    except Exception as exc:
        problemas.append(f"No se puede escribir en {TMP_DIR}: {exc}")
        linea("Escritura", f"ERROR → {exc}")

    exe = BASE_DIR / "dist" / "TranscriptorActas" / "TranscriptorActas.exe"
    if exe.exists():
        avisos.append(f"Existe un .exe antiguo en {exe}: si lo usas, vuelve a compilar "
                      "con compilar_exe.bat para que lleve los últimos cambios")

    print("-" * 74)
    for aviso in avisos:
        print(f" AVISO: {aviso}")
    for problema in problemas:
        print(f" PROBLEMA: {problema}")
    if problemas:
        print("\n Resultado: faltan cosas para poder arrancar.")
        return 1
    print("\n Resultado: todo listo. Arranca con:  iniciar.bat   (o python app.py)")
    return 0


def descargar_modelo(clave: str) -> int:
    """
    Descarga el modelo de un modo antes de usarlo: `python app.py --descargar turbo`
    (o `--descargar todos`). Muestra el avance y se puede cortar con Ctrl+C:
    al volver a ejecutarlo continúa donde quedó.
    """
    clave = (clave or "").strip().lower()
    if clave in ("", "todos", "all"):
        claves = list(MODES)
    elif clave in MODES:
        claves = [clave]
    else:
        print(f"\n[ERROR] Modo desconocido: {clave}")
        print(f"        Modos: {', '.join(MODES)}")
        print("        Ejemplo: python app.py --descargar turbo")
        print("                 python app.py --descargar todos\n")
        return 2

    print("\n" + "=" * 74)
    print(" DESCARGA PREVIA DE MODELOS  (Ctrl+C para pausar; se reanuda luego)")
    print("=" * 74)
    for k in claves:
        cfg = MODES[k]
        name = cfg["model"]
        carpeta = HUB_DIR / ("models--" + name.replace("/", "--"))
        print(f"\n· {cfg['label']} · {name}")
        if whisper_is_local(name):
            print(f"  ya descargado en ./modelos, no hace falta bajarlo otra vez.")
            continue
        t0 = time.time()
        try:
            get_whisper(k, log=lambda m: print("  " + m, flush=True))
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"  [ERROR] No se pudo descargar: {exc}")
            print("  Comprueba la conexión y que faster-whisper está instalado.")
            return 1
        medido = int(DESCARGA.get("bytes") or 0) or _total_descarga(name)
        print(f"  listo: {bytes_humanos(medido)} en {int(time.time() - t0)} s")
        unload_whisper()
    print("\nYa está todo descargado. Arranca con: iniciar.bat\n")
    return 0


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Transcriptor de Audio y Generador de Actas")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host (por defecto 127.0.0.1; usa 0.0.0.0 solo en red de confianza)")
    parser.add_argument("--port", type=int, default=8000, help="Puerto (por defecto 8000)")
    parser.add_argument("--no-browser", action="store_true", help="No abrir el navegador")
    parser.add_argument("--token", default="", help="Token de acceso opcional (equivale a TAE_TOKEN)")
    parser.add_argument("--force", action="store_true", help="Ignorar el candado de instancia única")
    parser.add_argument("--check", action="store_true",
                        help="Diagnóstico: versiones, dependencias, FFmpeg y modelos (no arranca)")
    parser.add_argument("--usar-ollama", metavar="MODELO", default=None,
                        help="Configura la app para redactar con Ollama y ese modelo "
                             "(lo usa instalar.bat). Sin valor, elige el mejor instalado.")
    parser.add_argument("--descargar", nargs="?", const="turbo", default=None,
                        metavar="MODO",
                        help="Descarga el modelo de un modo antes de usarlo "
                             "(rapido, equilibrado, turbo, preciso, ultra o todos)")
    args = parser.parse_args()

    if args.check:
        sys.exit(diagnose())

    if args.usar_ollama is not None:
        res = aplicar_motor_ollama(args.usar_ollama, esperar=12.0)
        print(("\n[OK]   " if res["ok"] else "\n[AVISO] ") + res["mensaje"])
        if res.get("models"):
            print("       Modelos instalados en Ollama: " + ", ".join(res["models"][:8]))
        sys.exit(0 if res["ok"] else 1)

    if args.descargar:
        try:
            sys.exit(descargar_modelo(args.descargar))
        except KeyboardInterrupt:
            print("\n\nDescarga interrumpida. Vuelve a lanzar el mismo comando: "
                  "continúa donde se quedó.\n")
            sys.exit(130)

    global APP_TOKEN
    if args.token:
        APP_TOKEN = args.token.strip()
        os.environ["TAE_TOKEN"] = APP_TOKEN

    if not acquire_single_instance() and not args.force:
        print("\n[ERROR] Ya hay otra instancia de esta aplicación ejecutándose.\n"
              "        Ciérrala o usa --force si estás seguro.\n")
        sys.exit(1)

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.8, lambda: _open_browser(url)).start()

    import uvicorn

    print(f"\nTranscriptor de Audio y Generador de Actas v{VERSION}")
    print(f"  Pydantic {PYDANTIC_VERSION} · modo {'2' if PYDANTIC_V2 else 'compatible 1.x'}")
    print(f"  Abre: {url}   (Ctrl+C para detener)\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
