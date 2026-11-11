"""
Prueba de extremo a extremo del motor de transcripción.

Se salta automáticamente si faster-whisper no está instalado o si el modelo
`base` no está descargado en ./modelos. Para ejecutarla:

    TAE_E2E=1 python -m pytest tests/test_e2e_transcripcion.py -v
"""
import importlib.util
import wave
import struct
import math

import pytest

import app

tiene_fw = importlib.util.find_spec("faster_whisper") is not None


@pytest.mark.slow
@pytest.mark.skipif(not tiene_fw, reason="faster-whisper no está instalado")
def test_pipeline_ventanas_y_checkpoint(tmp_path, monkeypatch, wav_file):
    if not app.model_is_local(app._whisper_repo("base")) and not app.OFFLINE_ACTIVE:
        pytest.skip("El modelo 'base' no está en ./modelos (sin descarga en esta prueba)")

    monkeypatch.setattr(app, "TMP_DIR", tmp_path)
    monkeypatch.setattr(app, "RESULT_DIR", tmp_path / "resultados")
    (tmp_path / "resultados").mkdir(exist_ok=True)
    monkeypatch.setattr(app, "TRANSCRIBE_WINDOW", 5.0)   # ventanas de 5 s a propósito
    monkeypatch.setattr(app, "WINDOW_OVERLAP", 1.0)

    # WAV de 12 s con tonos y silencios (VAD descartará el tono puro)
    wav = tmp_path / "e2e.wav"
    rate = 16000
    frames = bytearray()
    for i in range(rate * 12):
        amp = 9000 if (i // (rate // 2)) % 2 == 0 else 0
        frames += struct.pack("<h", int(amp * math.sin(2 * math.pi * 300 * i / rate)))
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))

    job = {
        "id": "e2e0001",
        "options": {"mode": "rapido", "language": "es", "word_timestamps": True,
                    "join_turns": True},
        "cancel": __import__("threading").Event(),
        "log": [],
    }
    progreso = []
    result = app.transcribe_wav(job, wav, 12.0, lambda p: progreso.append(p))

    assert isinstance(result["segments"], list)
    assert result["windows"] >= 2, "el audio debe partirse en varias ventanas"
    assert progreso and max(progreso) > 0.9
    ckpt = app.checkpoint_path("e2e0001")
    assert ckpt.exists(), "debe quedar un checkpoint para reanudar"
    assert app.read_json(ckpt, {}).get("upto", 0) > 0
