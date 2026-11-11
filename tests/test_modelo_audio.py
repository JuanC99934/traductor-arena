"""Pruebas del «oído» local: trozo de audio y detección de modelos de audio.

No usan red ni Ollama real: la parte de red se prueba con un servidor falso
montado en un hilo (igual que hace Ollama con /api/show y /api/tags).
"""
import json
import threading
import wave
import struct
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import app as appmod


def _wav_de_prueba(path, segundos=3.0, rate=16000):
    datos = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
                     for i in range(int(segundos * rate)))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(datos)   # writeframes actualiza el encabezado
    return path


def test_ollama_root_normaliza_url():
    assert appmod._ollama_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert appmod._ollama_root("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"
    assert appmod._ollama_root("http://localhost:1234/v1/chat/completions") == "http://localhost:1234"
    assert appmod._ollama_root("http://mi-pc:8080") == "http://mi-pc:8080"


def test_wav_bytes_cabecera_y_duracion():
    import numpy as np

    muestras = np.zeros(16000, dtype="float32")  # 1 segundo
    crudo = appmod._wav_bytes(muestras)
    assert crudo[:4] == b"RIFF" and crudo[8:12] == b"WAVE"
    with wave.open(__import__("io").BytesIO(crudo), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getnframes() == 16000


def test_clip_audio_wav_recorta(tmp_path):
    origen = _wav_de_prueba(tmp_path / "tono.wav", segundos=10.0)
    trozo = appmod.clip_audio_wav(origen, segundos=5.0, desde=0.0)
    with wave.open(__import__("io").BytesIO(trozo), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert abs(wf.getnframes() - 5 * 16000) < 3000        # ≈ 5 s
    otro = appmod.clip_audio_wav(origen, segundos=4.0, desde=6.0)
    with wave.open(__import__("io").BytesIO(otro), "rb") as wf:
        assert wf.getnframes() > 0
        assert abs(wf.getnframes() - 4 * 16000) < 3000        # ≈ 4 s desde el segundo 6


def test_clip_audio_wav_minimo_y_maximo(tmp_path):
    """Ni menos de 3 s (inútil para escuchar) ni más de 180 s (no cabe)."""
    origen = _wav_de_prueba(tmp_path / "tono.wav", segundos=4.0)
    corto = appmod.clip_audio_wav(origen, segundos=0.5, desde=0)
    with wave.open(__import__("io").BytesIO(corto), "rb") as wf:
        assert wf.getnframes() >= 3 * 16000 - 3000
    largo = appmod.clip_audio_wav(origen, segundos=600, desde=0)
    with wave.open(__import__("io").BytesIO(largo), "rb") as wf:
        assert wf.getnframes() <= 180 * 16000


# --------------------------------------------------------------------------
# Motor de IA del acta: nunca debe quedarse en uno que no funcione
# --------------------------------------------------------------------------
def test_ensure_ia_defaults_cambia_a_un_motor_disponible(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "IA_CONFIG_FILE", tmp_path / "ia_config.json")
    appmod.write_json(appmod.IA_CONFIG_FILE, {"engine": "local"})
    # local no está disponible, Ollama sí
    monkeypatch.setattr(appmod, "ia_engine_ready", lambda e: e == "openai")
    cfg = appmod.ensure_ia_defaults()
    assert cfg["engine"] == "openai"
    assert appmod._read_ia_config()["engine"] == "openai"      # queda guardado


def test_ensure_ia_defaults_respeta_el_motor_que_si_funciona(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "IA_CONFIG_FILE", tmp_path / "ia_config.json")
    appmod.write_json(appmod.IA_CONFIG_FILE, {"engine": "local"})
    monkeypatch.setattr(appmod, "ia_engine_ready", lambda e: e == "local")
    cfg = appmod.ensure_ia_defaults()
    assert cfg["engine"] == "local"


def test_ia_engine_falta_dice_el_comando_exacto(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "IA_CONFIG_FILE", tmp_path / "ia_config.json")
    texto = appmod.ia_engine_falta("local")
    assert "instalar.bat" in texto and "torch" in texto
    assert "opción 1" in texto
