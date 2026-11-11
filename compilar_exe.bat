@echo off
REM ===========================================================================
REM  Compila la aplicacion a un .exe portable con PyInstaller (Windows)
REM ===========================================================================
setlocal
cd /d "%~dp0"

echo.
echo  === 1/5  Comprobando Python ===
python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python no esta instalado o no esta en el PATH.
  pause
  exit /b 1
)

echo.
echo  === 2/5  Instalando dependencias y PyInstaller ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo.
echo  === 3/5  Comprobando Pydantic ===
python -c "import sys, pydantic; sys.exit(0 if int(pydantic.VERSION.split('.')[0]) >= 2 else 1)" >nul 2>&1
if errorlevel 1 (
  echo        Pydantic 1.x detectado: actualizando a Pydantic 2...
  python -m pip install --upgrade "pydantic>=2.7"
)

echo.
echo  === 4/5  Empaquetando (puede tardar varios minutos) ===
python -m PyInstaller app.spec --noconfirm --clean
if errorlevel 1 (
  echo [ERROR] Fallo el empaquetado. Revisa el mensaje anterior.
  pause
  exit /b 1
)

echo.
echo  === 5/5  Copiando archivos auxiliares ===
copy /y iniciar.bat "dist\TranscriptorActas\" >nul
copy /y README.md "dist\TranscriptorActas\" >nul
mkdir "dist\TranscriptorActas\modelos\hub" 2>nul
mkdir "dist\TranscriptorActas\uploads" 2>nul
mkdir "dist\TranscriptorActas\resultados" 2>nul
mkdir "dist\TranscriptorActas\historial" 2>nul
mkdir "dist\TranscriptorActas\tmp" 2>nul
mkdir "dist\TranscriptorActas\logs" 2>nul

echo.
echo  Listo. Ejecuta: dist\TranscriptorActas\TranscriptorActas.exe
echo  (o el iniciar.bat de esa carpeta)
echo.
pause
