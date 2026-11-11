@echo off
REM ===========================================================================
REM  Arranca la aplicacion (abre el navegador en http://127.0.0.1:8000)
REM  Opciones extra:
REM    iniciar.bat --port 8010              cambia el puerto
REM    iniciar.bat --host 0.0.0.0           expone en la red local (¡usa --token!)
REM    iniciar.bat --token secreto123       exige un token de acceso
REM    iniciar.bat --no-browser             no abre el navegador
REM    iniciar.bat --force                  ignora el candado de instancia unica
REM    iniciar.bat --check                  diagnostico: versiones, Pydantic, FFmpeg
REM    iniciar.bat --descargar turbo        baja el modelo antes de usarlo (barra de progreso)
REM    iniciar.bat --descargar todos        baja todos los modelos de una vez
REM ===========================================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

if exist "TranscriptorActas.exe" (
  echo Iniciando version empaquetada...
  start "" "TranscriptorActas.exe" %*
  exit /b 0
)

echo Iniciando Transcriptor de Audio y Generador de Actas...
echo  (si algo falla, ejecuta:  iniciar.bat --check )
echo  (la primera transcripcion descarga el modelo; para hacerlo antes:
echo   iniciar.bat --descargar turbo )
"%PY%" app.py %*
if errorlevel 1 (
  echo.
  echo La aplicacion termino con error. Revisa logs\app.log
  pause
)
