"""Configuración común de las pruebas: aísla los datos del usuario."""
import sys
import wave
import struct
import math
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as appmod  # noqa: E402


@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    """Redirige results/tmp/uploads/historial a una carpeta temporal."""
    dirs = {}
    for name in ("RESULT_DIR", "TMP_DIR", "UPLOAD_DIR", "HISTORY_DIR", "BACKUP_DIR", "VOICES_DIR"):
        d = tmp_path / name.lower()
        d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(appmod, name, d)
        dirs[name] = d
    monkeypatch.setattr(appmod, "HISTORY_FILE", tmp_path / "historial.json")
    monkeypatch.setattr(appmod, "SESSIONS_FILE", tmp_path / "sesiones.json")
    monkeypatch.setattr(appmod, "VOICES_FILE", tmp_path / "voces.json")
    monkeypatch.setattr(appmod, "IA_CONFIG_FILE", tmp_path / "ia_config.json")
    return dirs


@pytest.fixture()
def job():
    return {
        "id": "jobtest00001",
        "title": "Comité de operaciones",
        "filename": "reunion.mp3",
        "session": "default",
        "duration": 120.0,
        "status": "done",
        "speakers": [
            {"name": "Ana López", "key": "c1", "count": 2, "seconds": 30.0, "color": 0},
            {"name": "Luis Pérez", "key": "c2", "count": 1, "seconds": 15.0, "color": 1},
        ],
        "result": {
            "segments": [
                {"id": 0, "start": 0.0, "end": 12.0, "text": "Buenos días, revisemos el presupuesto.",
                 "speaker": "Ana López", "avg_logprob": -0.2},
                {"id": 1, "start": 12.0, "end": 27.5, "text": "El monto aprobado fue de 1.500 millones.",
                 "speaker": "Luis Pérez", "avg_logprob": -0.9},
                {"id": 2, "start": 28.0, "end": 45.0, "text": "De acuerdo, queda aprobado el ajuste.",
                 "speaker": "Ana López", "avg_logprob": -0.3},
            ],
            "info": {"language": "es", "duration": 120.0},
            "title": "Comité de operaciones",
        },
        "stats": {"words": 24, "total_seconds": 90.0},
        "warnings": [],
        "created": "2026-10-30T10:00:00",
        "cancel": None,
    }


@pytest.fixture()
def wav_file(tmp_path):
    """WAV sintético de 2 s (440 Hz) válido para ffprobe/probe_duration."""
    path = tmp_path / "muestra.wav"
    rate = 16000
    frames = bytearray()
    for i in range(rate * 2):
        frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return path
