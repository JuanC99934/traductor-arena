@echo off
REM ===========================================================================
REM  Arreglo rápido del error de Pydantic  ·  Transcriptor de Audio y Actas
REM  Uso: doble clic. Deja el entorno con Pydantic 2 y comprueba la app.
REM ===========================================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo.
echo  ============================================================
echo   ARREGLO DEL ERROR DE PYDANTIC
echo  ============================================================
echo.

echo  === 1/3  Version actual ===
"%PY%" -c "import pydantic; print('   Pydantic', pydantic.VERSION)" 2>nul
if errorlevel 1 (
  echo    [ERROR] No se pudo importar pydantic con "%PY%".
  echo            Ejecuta primero instalar.bat
  pause
  exit /b 1
)

echo.
echo  === 2/3  Actualizando a Pydantic 2 ===
"%PY%" -m pip install --upgrade "pydantic>=2.7"
if errorlevel 1 (
  echo.
  echo  [AVISO] No se pudo actualizar ^(sin internet, permisos o algun paquete
  echo          que exige Pydantic 1^). No pasa nada: el app.py corregido
  echo          funciona tambien con Pydantic 1.x. Asegurate de usar el
  echo          app.py nuevo ^(el que incluye el bloque "0.0 COMPROBACION DE
  echo          PYDANTIC" al principio del archivo^).
)

echo.
echo  === 3/3  Comprobando la aplicacion ===
"%PY%" app.py --check
echo.
echo  Si arriba pone "Resultado: todo listo", arranca con: iniciar.bat
echo  Si sigue fallando, copia este texto completo y envialo.
echo.
pause
