# -*- mode: python ; coding: utf-8 -*-
"""
Empaquetado con PyInstaller del Transcriptor de Audio y Generador de Actas.

Uso (Windows, con las dependencias ya instaladas):

    pip install pyinstaller
    pyinstaller app.spec --noconfirm

El resultado queda en dist/TranscriptorActas/TranscriptorActas.exe
El .exe arranca la interfaz en http://127.0.0.1:8000 y abre el navegador.

Importante: los modelos de IA (./modelos), FFmpeg y las carpetas de datos
(uploads, resultados, historial, tmp, logs) NO se empaquetan: se crean junto
al .exe la primera vez que se ejecuta.
"""
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = [
    # servidor
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan", "uvicorn.lifespan.on",
    "h11", "anyio._backends._asyncio", "httptools",
    # transcripción
    "faster_whisper", "ctranslate2", "tokenizers", "huggingface_hub", "onnxruntime",
    "av", "numpy", "scipy", "scipy.cluster.hierarchy", "wave",
    # exportación / diarización / IA opcional
    "docx", "resemblyzer", "librosa", "soundfile", "webrtcvad",
    "transformers", "torch", "accelerate", "pydantic",
]
hidden += collect_submodules("faster_whisper")

datas = [("static", "static")]
try:
    datas += collect_data_files("resemblyzer")
except Exception:
    pass

block_cipher = None

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter", "PyQt5", "PySide6", "notebook", "IPython"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TranscriptorActas",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TranscriptorActas",
)
