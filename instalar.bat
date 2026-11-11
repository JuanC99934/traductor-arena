@echo off
REM ===========================================================================
REM  Instalacion (una sola vez) · Transcriptor de Audio y Generador de Actas
REM  · Instala por etapas: un paquete opcional que falle NO tumba el resto.
REM  · Instala solo binarios precompilados (--only-binary): no hace falta
REM    Visual C++ Build Tools ni ningun compilador.
REM  · No depende de requirements.txt (lo regenera si es antiguo).
REM ===========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "FALLOS="
REM Paquetes obligatorios. Sin extras ni paquetes que se compilen.
set CORE="fastapi>=0.110" "uvicorn>=0.27" "python-multipart>=0.0.9" "pydantic>=2.7" "faster-whisper>=1.0.3" "numpy>=1.24" "scipy>=1.10" "python-docx>=1.1.0"

echo.
echo  ============================================================
echo   TRANSCRIPTOR DE AUDIO Y GENERADOR DE ACTAS · instalacion
echo  ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python no esta instalado.
  echo         Descargalo de https://www.python.org/downloads/windows/
  echo         Marca la casilla "Add Python to PATH" durante la instalacion.
  pause
  exit /b 1
)

REM --- version de Python: 3.10 a 3.13 es lo mas seguro ---------------------
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
  set "PYMAJ=%%a"
  set "PYMIN=%%b"
)
echo  Python detectado: !PYVER!
if "!PYMAJ!"=="3" (
  if !PYMIN! GEQ 14 (
    echo.
    echo  [AVISO] Python !PYVER! es muy reciente. Para que funcionen TODAS las
    echo          funciones ^(hablantes e IA local^) usa Python 3.12 o 3.13.
    echo          La app se instala y transcribe igual en 3.14.
    echo.
  )
)

echo  === 1/8  Creando entorno virtual (.venv) ===
if not exist ".venv" (
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] No se pudo crear el entorno virtual.
    pause
    exit /b 1
  )
) else (
  echo        ya existe, se reutiliza.
)

echo.
echo  === 2/8  Preparando pip, setuptools y wheel ===
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip setuptools wheel

REM --- requirements.txt antiguo: se aparta y se regenera -------------------
if exist requirements.txt (
  findstr /i /c:"webrtcvad" /c:"resemblyzer" requirements.txt >nul 2>&1
  if not errorlevel 1 set "VIEJO=1"
)
if defined VIEJO (
  echo.
  echo  [AVISO] Tu requirements.txt es de una version anterior ^(incluye webrtcvad,
  echo          que necesita compilador C++^). Lo renombro a requirements-antiguo.txt
  echo          y creo uno correcto.
  if exist "requirements-antiguo.txt" del "requirements-antiguo.txt"
  ren requirements.txt requirements-antiguo.txt
  > requirements.txt echo # Dependencias obligatorias (generado por instalar.bat)
  >> requirements.txt echo fastapi^>=0.110
  >> requirements.txt echo uvicorn^>=0.27
  >> requirements.txt echo python-multipart^>=0.0.9
  >> requirements.txt echo pydantic^>=2.7
  >> requirements.txt echo faster-whisper^>=1.0.3
  >> requirements.txt echo numpy^>=1.24
  >> requirements.txt echo scipy^>=1.10
  >> requirements.txt echo python-docx^>=1.1.0
)

echo.
echo  === 3/8  Dependencias obligatorias (5-15 minutos la primera vez) ===
echo        Solo binarios precompilados: no se compila nada.
python -m pip install --only-binary=:all: %CORE%
if errorlevel 1 (
  echo.
  echo        [AVISO] Con "solo binarios" no se pudo. Reintentando normal...
  python -m pip install %CORE%
  if errorlevel 1 (
    echo.
    echo [ERROR] Fallaron las dependencias obligatorias.
    echo         Comprueba que tienes internet y usa Python 3.12 o 3.13.
    pause
    exit /b 1
  )
)
echo        OK

echo.
echo  === 4/8  Identificacion de hablantes (opcional, ~2 GB con torch) ===
echo.
echo        Responde S para instalar diarizacion, N para saltarla.
echo        Sin ella la app transcribe igual, pero no distingue hablantes.
choice /c SN /t 20 /d S /m "        Instalar diarizacion [S/N]"
if errorlevel 2 (
  echo        Omitida. Puedes instalarla luego volviendo a ejecutar instalar.bat
) else (
  REM webrtcvad-wheels trae el mismo modulo ya compilado: evita el error
  REM "Microsoft Visual C++ 14.0 or greater is required" de webrtcvad.
  python -m pip install --only-binary=:all: webrtcvad-wheels
  if errorlevel 1 (
    set "FALLOS=!FALLOS! webrtcvad-wheels"
    echo        [AVISO] No se pudo instalar webrtcvad-wheels.
  ) else (
    echo        webrtcvad-wheels OK ^(sin compilador^)
  )
  python -m pip install --only-binary=:all: librosa
  if errorlevel 1 (
    set "FALLOS=!FALLOS! librosa"
    echo        [AVISO] No se pudo instalar librosa.
  )
  python -m pip install --only-binary=:all: torch --index-url https://download.pytorch.org/whl/cpu
  if errorlevel 1 (
    echo        [AVISO] Torch CPU no esta disponible para esta version de Python.
    echo                Si usas 3.14, instala Python 3.12/3.13 para la diarizacion.
    set "FALLOS=!FALLOS! torch"
  )
  REM --no-deps: resemblyzer pide "webrtcvad" y pip intentaria compilarlo
  python -m pip install --no-deps resemblyzer
  if errorlevel 1 (
    set "FALLOS=!FALLOS! resemblyzer"
    echo        [AVISO] No se pudo instalar resemblyzer.
  )
  python -c "import resemblyzer" >nul 2>&1
  if errorlevel 1 (
    set "FALLOS=!FALLOS! diarizacion"
    echo        [AVISO] La diarizacion no queda operativa ^(normalmente falta torch^).
  ) else (
    echo        Diarizacion lista.
  )
)

echo.
echo  === 5/8  IA para redactar el acta (necesaria si NO usas Ollama) ===
echo.
echo        El acta se redacta con un modelo de lenguaje. Tienes dos opciones:
echo.
echo          1^) IA local de Hugging Face ^(torch + transformers + accelerate^).
echo             Son ~2.5 GB y queda instalada en la app. Es la opcion sin
echo             depender de nada mas.
echo          2^) No instalar la IA local ahora (el paso 6 puede instalar
echo             Ollama y su modelo, que da mejores actas).
echo.
echo        Si eliges 2 y tampoco instalas Ollama en el paso 6, la app no
echo        podra redactar el acta ^(vuelve a ejecutar instalar.bat cuando quieras^).
echo.
choice /c 12 /n /t 45 /d 1 /m "        Elige 1 o 2 [por defecto 1 = instalar IA local] "
if errorlevel 2 (
  echo        Omitida. Recuerda: para el acta necesitaras Ollama o LM Studio,
  echo        por ejemplo:  ollama pull qwen2.5:7b-instruct
) else (
  echo        Descargando torch de CPU ^(el mas ligero^)...
  python -m pip install --only-binary=:all: torch --index-url https://download.pytorch.org/whl/cpu
  if errorlevel 1 (
    set "FALLOS=!FALLOS! torch"
    echo        [AVISO] No se pudo instalar torch ^(puede que tu Python no tenga
    echo                binario; prueba con Python 3.12 o 3.13^).
  )
  python -m pip install --only-binary=:all: "transformers>=4.44" "accelerate>=0.30"
  if errorlevel 1 (
    set "FALLOS=!FALLOS! transformers/accelerate"
    echo        [AVISO] No se pudo instalar transformers/accelerate.
  )
  REM Comprobacion real: si esto falla, el acta no funcionara.
  python -c "import torch, transformers, accelerate" >nul 2>&1
  if errorlevel 1 (
    set "FALLOS=!FALLOS! IA-local"
    echo        [AVISO] La IA local no quedo operativa. Usa Ollama o revisa el
    echo                mensaje de arriba.
  ) else (
    echo        IA local lista ^(el acta ya se puede redactar sin Ollama^).
  )
)

echo.
echo  === 6/8  Ollama + modelo del acta (opcional, deja todo listo) ===
echo.
echo        Ollama es gratis y redacta actas mas largas y mejor armadas que la
echo        IA local pequena. Se instala una vez y queda como servicio de Windows.
echo        La app lo detecta sola y lo usa como motor del acta.
echo.
set "OLLAMA="
for /f "delims=" %%p in ('where ollama 2^>nul') do set "OLLAMA=%%p"
if not defined OLLAMA (
  if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
    set "PATH=!PATH!;%LOCALAPPDATA%\Programs\Ollama"
  ) else (
    if exist "%ProgramFiles%\Ollama\ollama.exe" (
      set "OLLAMA=%ProgramFiles%\Ollama\ollama.exe"
      set "PATH=!PATH!;%ProgramFiles%\Ollama"
    )
  )
)
if defined OLLAMA (
  echo        Ollama ya esta instalado en:
  echo          !OLLAMA!
  echo.
  choice /c 12 /n /t 30 /d 1 /m "        Descargar ahora el modelo del acta? 1=si  2=no "
  if errorlevel 2 goto :ollama_fin
  goto :ollama_modelo
)
echo          1^) Instalar Ollama ahora y descargar el modelo  ^(RECOMENDADO^)
echo          2^) No, ya lo tengo en otro equipo / lo instalo yo despues
echo.
choice /c 12 /n /t 45 /d 1 /m "        Elige 1 o 2 [por defecto 1 = instalar Ollama] "
if errorlevel 2 (
  echo        Omitido. Ollama esta en https://ollama.com/download si lo quieres luego.
  goto :ollama_fin
)
echo.
echo        Instalando Ollama con winget (unos minutos, sin ventanas)...
where winget >nul 2>&1
if errorlevel 1 goto :ollama_sin_winget
winget install --id Ollama.Ollama -e --silent --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :ollama_sin_winget
goto :ollama_buscar

:ollama_sin_winget
echo        [AVISO] winget no esta o fallo. Se usa el instalador oficial.
set "OLLAMA_EXE=%TEMP%\OllamaSetup.exe"
echo        Descargando OllamaSetup.exe ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri 'https://ollama.com/download/OllamaSetup.exe' -OutFile $env:TEMP\OllamaSetup.exe -UseBasicParsing; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  set "FALLOS=!FALLOS! ollama"
  echo        [AVISO] No se pudo descargar el instalador de Ollama.
  echo                Instalalo desde https://ollama.com/download y vuelve a ejecutar
  echo                instalar.bat: el paso 6 lo detectara.
  goto :ollama_fin
)
echo        Abriendo el instalador de Ollama (sigue las ventanas que aparezcan)...
start /wait "" "%OLLAMA_EXE%"
del "%OLLAMA_EXE%" >nul 2>&1

:ollama_buscar
set "PATH=!PATH!;%LOCALAPPDATA%\Programs\Ollama"
if not defined OLLAMA if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not defined OLLAMA if exist "%ProgramFiles%\Ollama\ollama.exe" set "OLLAMA=%ProgramFiles%\Ollama\ollama.exe"
if not defined OLLAMA for /f "delims=" %%p in ('where ollama 2^>nul') do set "OLLAMA=%%p"
if not defined OLLAMA (
  set "FALLOS=!FALLOS! ollama"
  echo        [AVISO] Ollama no quedo disponible en esta ventana.
  echo                Cierra esta ventana, abre una nueva y ejecuta de nuevo instalar.bat.
  goto :ollama_fin
)
echo        Ollama instalado en: !OLLAMA!

:ollama_modelo
echo.
echo        Que modelo quieres como motor del acta?
echo            1^) qwen2.5:7b-instruct .... 4.7 GB  ^(mejor calidad, RECOMENDADO^)
echo            2^) qwen2.5:3b-instruct .... 1.9 GB  ^(mas rapido^)
echo            3^) qwen2.5:1.5b-instruct .. 1.0 GB  ^(equipos modestos^)
echo            N^) ahora no
echo.
echo        Se descarga una sola vez y se puede cortar con Ctrl+C.
echo.
choice /c 123N /n /t 60 /d 1 /m "        Elige 1-3 o N [por defecto 1 = 7B] "
if errorlevel 4 goto :ollama_fin
if errorlevel 3 goto :ollama_m15
if errorlevel 2 goto :ollama_m3
goto :ollama_m7

:ollama_m7
set "MODELO_OLLAMA=qwen2.5:7b-instruct"
goto :ollama_pull
:ollama_m3
set "MODELO_OLLAMA=qwen2.5:3b-instruct"
goto :ollama_pull
:ollama_m15
set "MODELO_OLLAMA=qwen2.5:1.5b-instruct"

:ollama_pull
echo.
echo        Comprobando el servicio de Ollama...
"!OLLAMA!" list >nul 2>&1
if errorlevel 1 (
  echo        Arrancando Ollama (segundo plano)...
  start "" /min "!OLLAMA!" serve
  timeout /t 8 /nobreak >nul
)
echo        Descargando !MODELO_OLLAMA! ^(una sola vez^)...
"!OLLAMA!" pull !MODELO_OLLAMA!
if errorlevel 1 (
  set "FALLOS=!FALLOS! modelo-ollama"
  echo        [AVISO] No se pudo descargar !MODELO_OLLAMA!.
  echo                Prueba a mano:  "!OLLAMA!" pull !MODELO_OLLAMA!
  goto :ollama_fin
)
echo        Modelo descargado.
echo.
echo        Dejando la app configurada para redactar con Ollama...
python app.py --usar-ollama "!MODELO_OLLAMA!"
if errorlevel 1 (
  set "FALLOS=!FALLOS! config-ollama"
  echo        [AVISO] La app no pudo guardar la configuracion de Ollama.
  echo                Abre la app, elige el motor "Ollama" y escribe el modelo a mano.
) else (
  echo        Listo: el acta se redactara con Ollama - !MODELO_OLLAMA!
)

:ollama_fin
echo.

echo.
echo  === 7/8  Descargar el modelo de voz ahora (opcional, pero recomendado) ===
echo.
echo          Es lo unico que necesita internet y solo se baja una vez.
echo          Se guarda en la carpeta modelos\ y sirve para siempre.
echo.
echo            1^) Rapido ........ 145 MB   ^(pruebas rapidas^)
echo            2^) Equilibrado ... 486 MB   ^(buen equilibrio^)
echo            3^) Turbo ......... 1.6 GB   ^(rapida, para sesiones largas^)
echo            4^) Preciso ....... 1.5 GB   ^(mas exacto, mas lento^)
echo            5^) Ultra ......... 3.1 GB   ^(POR DEFECTO, la que usa la app^)
echo            6^) Todos ......... ~7 GB    ^(todo descargado^)
echo            N^) ahora no ..... se descargara en la primera transcripcion
echo.
echo          La descarga se puede cortar con Ctrl+C y se reanuda despues.
echo          Si tu conexion es lenta, elige 1 ^(145 MB^) y prueba ya.
echo.
choice /c 123456N /n /t 60 /d 5 /m "        Elige 1-6 o N [por defecto 5 = Ultra] "
if errorlevel 7 goto :modelo_omitido
if errorlevel 6 goto :modelo_todos
if errorlevel 5 goto :modelo_ultra
if errorlevel 4 goto :modelo_preciso
if errorlevel 3 goto :modelo_turbo
if errorlevel 2 goto :modelo_equilibrado
goto :modelo_rapido

:modelo_todos
call :descargar todos
goto :modelo_fin
:modelo_ultra
call :descargar ultra
goto :modelo_fin
:modelo_preciso
call :descargar preciso
goto :modelo_fin
:modelo_turbo
call :descargar turbo
goto :modelo_fin
:modelo_equilibrado
call :descargar equilibrado
goto :modelo_fin
:modelo_rapido
call :descargar rapido
goto :modelo_fin

:modelo_omitido
echo        Omitido. El modelo se descargara solo la primera vez que transcribas.

:modelo_fin

echo.
echo  === 8/8  Comprobando FFmpeg y la aplicacion ===
where ffmpeg >nul 2>&1
if errorlevel 1 (
  echo [AVISO] FFmpeg no esta en el PATH.
  echo         Instalalo con:  winget install Gyan.FFmpeg
  echo         y vuelve a abrir esta ventana.
) else (
  echo        FFmpeg encontrado.
)

echo.
python app.py --check

echo.
echo  ============================================================
if defined FALLOS (
  echo   Instalacion terminada CON AVISOS:!FALLOS!
  echo   La app arranca igual; esas funciones quedan desactivadas.
) else (
  echo   Instalacion terminada sin incidencias.
)
echo.
echo   Nota: para redactar el acta necesitas UN motor: la IA local de Hugging
echo   Face o Ollama con su modelo. Si no instalaste ninguno, el paso 6 de
echo   este instalador puede dejarlo listo en cualquier momento.
echo.
echo   Ejecuta ahora:  iniciar.bat
echo  ============================================================
pause
exit /b 0

REM ---------------------------------------------------------------------------
REM  Descarga un modelo de voz con barra de progreso (llama a app.py --descargar)
REM ---------------------------------------------------------------------------
:descargar
echo.
echo        Descargando modelo %~1 ... (Ctrl+C lo pausa; se reanuda luego)
python app.py --descargar %~1
if errorlevel 1 (
  set "FALLOS=!FALLOS! modelo-%~1"
  echo        [AVISO] No se pudo descargar el modelo %~1.
  echo                La app lo intentara en la primera transcripcion.
) else (
  echo        Modelo %~1 descargado y guardado en modelos\.
)
exit /b 0
