# 🎙️ Transcriptor de Audio y Generador de Actas

Aplicación web **local, portable y autónoma** para Windows (también corre en Linux/macOS) que
transcribe reuniones de **hasta 3+ horas y 2 GB**, identifica **quién habla** y redacta el
**acta ejecutiva** con IA. Todo el procesamiento de audio es local: **sin traducción**, siempre
en el idioma original.

```
app.py                → Backend FastAPI (cola, transcripción, diarización, IA, exportación)
static/index.html     → Interfaz (dos columnas, reproductor con onda, grabadora)
static/app.js         → Lógica de la interfaz en Vanilla JS
static/style.css      → Tema oscuro/claro, animaciones, estilos de impresión
requirements.txt      → Dependencias
instalar.bat          → Instalación automática (venv + dependencias + FFmpeg)
iniciar.bat           → Arranque en Windows (acepta --port, --host, --token…)
compilar_exe.bat      → Genera el .exe portable con PyInstaller
app.spec              → Receta de PyInstaller
tests/                → Pruebas automáticas (pytest)
```

> **Novedades v2.5 — acta y historial de verdad utilizables** — plantilla institucional
> (*Junta Directiva* de hospital por defecto) para encabezado, quórum y firmas; **acta
> anterior** como contexto para dar seguimiento a los acuerdos; **acuerdos y pendientes
> editables** en tabla; **versiones del acta** con restaurar; **compromisos de todas las
> reuniones** en un panel con semáforo de cumplimiento; **buscador, series y protección**
> en el historial; **duplicar** el acta como base editable y **continuar una reunión** con
> otra parte de audio en un solo documento. Ver `CAMBIOS-v3-visual.md` §14.
>
> **Novedades v2.1 — rediseño visual** — sistema de diseño con escala tipográfica
> cerrada, paleta categórica de hablantes (tonos fijos y contrastados), iconos SVG
> propios (sin emoji), reproductor de una sola fila con la onda detrás de la pista,
> transcripción por turnos, flujo visible (Cargar · Transcribir · Revisar),
> tema claro y oscuro. Ver `CAMBIOS-v3-visual.md`.
>
> **Novedades v2.0** — exportación con tus correcciones, guardado y **reanudación**
> automática tras un corte, transcripción **por ventanas** (memoria constante),
> subida de 2 GB **reanudable**, diarización con umbral adaptativo y fusión/división de
> voces, acta por **map-reduce** con citas `[HH:MM:SS]` y acuerdos en tabla, forma de onda
> por hablante, capítulos, búsqueda global sin acentos, deshacer, accesibilidad y `logs/app.log`.

---

## 1. Requisitos

| Componente | Detalle |
|---|---|
| **Python** | 3.10 – 3.13 (64 bits) |
| **FFmpeg** | Obligatorio. `winget install Gyan.FFmpeg` (o variable `FFMPEG_BIN`) |
| **RAM** | 8 GB recomendado (1 modelo pesado a la vez, `int8`) |
| **Disco** | ~1.5 GB para el modelo *turbo* + espacio para el historial |
| **Navegador** | Chrome o Edge (grabadora: MediaRecorder + Web Audio) |
| **IA del acta** | Gratuita: Hugging Face local (torch+transformers) u Ollama. Gemini es opcional |

---

## 2. Instalación (Windows)

```bat
:: 1) Instalación automática en 3 etapas (te pregunta por las opcionales)
instalar.bat

:: 2) Arrancar la aplicación (abre el navegador en http://127.0.0.1:8000)
iniciar.bat

:: 3) Si algo va mal, diagnóstico con versiones y estado:
iniciar.bat --check
```

`instalar.bat` instala por etapas, de forma que **un paquete opcional que falle no
tumba la instalación**:

| Etapa | Contenido | Si falla |
|---|---|---|
| 1/8 · Entorno | crea `.venv` (si no existe) | se detiene |
| 2/8 · Base | actualiza `pip`, `setuptools`, `wheel` y repara un `requirements.txt` antiguo | sigue |
| 3/8 · Obligatorio | `fastapi`, `uvicorn`, `pydantic`, `faster-whisper`, `numpy`, `scipy`, `python-docx` **solo binarios** | se detiene con un mensaje claro |
| 4/8 · Hablantes | `webrtcvad-wheels`, `librosa`, `torch` (CPU) y `resemblyzer --no-deps` | aviso; la app transcribe sin distinguir hablantes |
| 5/8 · IA local | `transformers`, `accelerate` | aviso; el acta funciona con Ollama/LM Studio/Gemini |
| **6/8 · Ollama** | instala Ollama (winget o instalador oficial) y descarga el modelo del acta (1,0–4,7 GB); deja la app configurada | aviso; el acta sigue funcionando con la IA local |
| **7/8 · Modelo de voz** | te pregunta cuál bajar y lo descarga con progreso (145 MB a 3,1 GB) | aviso; se descarga en la primera transcripción |
| 8/8 · Comprobación | FFmpeg + `app.py --check` (dice versión, modelos descargados y FFmpeg) | informa |

Instalación manual equivalente:

```bat
python -m venv .venv
.venv\Scripts\activate
:: obligatorio (solo binarios: no se compila nada)
pip install --only-binary=:all: fastapi uvicorn python-multipart pydantic ^
            faster-whisper numpy scipy python-docx
pip install webrtcvad-wheels librosa                         :: diarización
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install --no-deps resemblyzer                            :: IMPORTANTE: --no-deps
pip install -r requirements-ia.txt                           :: IA local (opcional)
winget install Gyan.FFmpeg
python app.py
```

**Notas de instalación**

* **Nunca instales `webrtcvad` directamente.** Ese paquete solo se publica como
  código fuente y en Windows exige *Microsoft Visual C++ Build Tools*
  (`error: Microsoft Visual C++ 14.0 or greater is required`). Usa
  **`webrtcvad-wheels`**, que trae el mismo módulo `webrtcvad` ya compilado para
  Python 3.10–3.15.
* `resemblyzer` declara `webrtcvad` como dependencia, así que hay que instalarlo con
  **`--no-deps`** después de `webrtcvad-wheels`; si no, pip vuelve a intentar compilarlo.
* `setuptools<81` sigue fijado porque parte de la cadena de audio usa `pkg_resources`,
  eliminado en setuptools ≥ 81.
* **Python 3.14** es muy reciente: `torch` y `librosa` pueden no tener binarios todavía.
  Para todo funcionando a la primera usa **Python 3.12 o 3.13**. La app arranca igual
  en 3.14 sin diarización ni IA local.
* La primera transcripción descarga el modelo de Whisper en `./modelos/hub`.
  A partir de ahí la app arranca en **modo offline** (`HF_HUB_OFFLINE=1`).

---

## 2 bis. La primera vez: descargar el modelo (paciencia, se descarga una sola vez)

El modelo de voz **no viene incluido**: se baja de Hugging Face la primera vez que
transcribes y queda guardado en `./modelos`. La app **te dice cuánto pesa** en el
selector de modo y muestra el avance en el registro del trabajo.

| Modo | Modelo | Descarga |
|---|---|---|
| Rápido | `base` | ~145 MB |
| Equilibrado | `small` | ~486 MB |
| Turbo (rápida) | `large-v3-turbo` int8 | ~1,6 GB |
| Preciso | `medium` | ~1,5 GB |
| **Ultra (por defecto)** | `large-v3` int8, beam 5 | ~3,1 GB |

**El propio `instalar.bat` lo puede bajar por ti** en la etapa 7/8: te muestra el
peso de cada modo y descarga el que elijas con barra de progreso (por defecto el 5,
**Ultra**, que es el modo fijo de la app). Si prefieres hacerlo aparte, con la consola (con
barra de progreso, porcentaje, velocidad y tiempo restante):

```bat
iniciar.bat
:: o bien, dentro del entorno virtual:
.venv\Scripts\python app.py --descargar ultra      :: un modo concreto
.venv\Scripts\python app.py --descargar todos      :: todos seguidos
```

Se puede cortar con `Ctrl+C` y **se reanuda** al volver a lanzarlo. Para una
primera prueba de 5 minutos usa el modo **Rápido** (145 MB) y cambia a Turbo
cuando ya tengas el modelo.

## 2 ter. Opcional: «escuchar» la reunión con un modelo de audio (Ollama)

Si tienes **Ollama** instalado puedes usar un modelo de audio (por ejemplo
**`lfm3-audio`**, 2 GB) para que **escuche 30 s de la grabación** y te diga en qué
idioma está, cuántas personas hablan y de qué trata, **antes** de transcribir 3
horas. Está en el panel de la reunión: *Escuchar el audio con un modelo de audio*.

```bat
ollama pull lfm3-audio
:: necesita Ollama reciente (soporte de audio)
```

* **No sustituye a la transcripción**: no da marcas de tiempo y no transcribe
  reuniones largas. Es un «oído rápido» y una segunda opinión para fragmentos
  cortos.
* El audio sale de tu equipo solo hacia tu propio Ollama (`127.0.0.1`).
* Si no hay Ollama o no hay ningún modelo con audio, el panel lo dice y se queda
  desactivado: **la app funciona igual**.
* El trozo que se envía es WAV 16 kHz mono, entre 20 s y 2 min (máximo 180 s).
  Necesita FFmpeg para formatos comprimidos, o faster-whisper (trae PyAV).

## 3. Uso en 4 pasos

1. **Cargar**: arrastra el audio/video (MP3, WAV, M4A, MP4, MKV, WEBM, FLAC…) o pulsa
   **🎙️ Grabar reunión en vivo**.
2. **Ajustar**: modo de precisión (Rápido / Equilibrado / Turbo / Preciso / **Ultra**, el
   fijo por defecto; lo que elijas se recuerda la próxima vez),
   idioma, marcas de tiempo, lectura en párrafos. En *Opciones avanzadas*: hotwords,
   contexto, filtro FFmpeg extra, encadenado de texto y modo música (sin VAD).
3. **Procesar**: la barra muestra limpieza de audio (0-6 %) y transcripción (6-99 %).
   Al terminar se identifican los hablantes y se notifica con un *toast* de Windows.
4. **Revisar y exportar**: reproductor sincronizado, buscador, fragmentos dudosos,
   edición en vivo, nombres de hablantes, acta con IA y descarga en
   **.docx / .txt / .srt / .vtt**.

---

## 4. Cómo funciona el backend

### 4.1 Portabilidad y hardware
* `HF_HOME` y caché de Hugging Face fijados a `./modelos` (todo queda junto a la app).
* Si ya hay modelos en `./modelos/hub`, se activa `HF_HUB_OFFLINE=1` y
  `TRANSFORMERS_OFFLINE=1`; solo se desactiva temporalmente al descargar uno nuevo.
* `find_ffmpeg()` busca en `FFMPEG_BIN` → `PATH` → `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*`
  → rutas típicas de winget/chocolatey/scoop.
* Detecta CUDA con CTranslate2/PyTorch. Sin GPU usa `os.cpu_count() // 2` hilos reales
  (evita el *hyperthreading*) y cuantización **int8** (AVX2 sin saturar la caché).

### 4.2 Audio y transcripción
* `preprocess_audio()` convierte a **WAV 16 kHz mono `pcm_s16le`** con la cadena
  `highpass=f=75,acompressor=threshold=-18dB:ratio=3:attack=5:release=150:makeup=4,loudnorm=I=-16:TP=-1.5:LRA=11`
  y lee el progreso real con `-progress pipe:1`; cancelar mata el subproceso.
* Modos:

  | Modo | Modelo | Beam | Uso |
  |---|---|---|---|
  | `rapido` | `base` | 1 | pruebas |
  | `equilibrado` | `small` | 3 | uso general |
  | **`turbo`** | `deepdml/faster-whisper-large-v3-turbo-ct2` | 2 | **por defecto**, 3 h |
  | `preciso` | `medium` | 3 | más exactitud |
  | `ultra` | `Systran/faster-whisper-large-v3` | 5 | máxima calidad |

* Un solo modelo pesado en RAM (si cambia, se libera el anterior + `gc.collect()`).
  El array de NumPy del audio se libera con `del arr; gc.collect()` al terminar.
* `BatchedInferencePipeline` automático si el audio dura **> 120 s** y el VAD está activo
  (`threshold 0.35`, `min_silence_duration_ms 500`, `speech_pad_ms 200`).
* **Verificación de cola**: si el VAD corta más del 15 % final de un audio largo, se hace
  una segunda pasada sobre el tramo faltante y se fusionan los tiempos.
* Prompt de dominio (`DOMAIN_PROMPT`) con vocabulario de juntas directivas, hospital,
  La Beneficencia, comisiones, EBITDA/ISR/descorche/remisión, aseguradoras y nombres propios.
* Post-proceso: `collapse_repeats()` (bucles de alucinación ≥ 3 repeticiones) y
  `fix_question_marks()` (elimina `¿...?` falsos a mitad de oración).

### 4.2.1 Ventanas, memoria y reanudación

* El audio se procesa en **ventanas de ~20 min con 5 s de solape**
  (`TAE_WINDOW_MIN`, por defecto `20`). Así una reunión de 3 h usa ~150 MB de RAM
  en lugar de cargar la pista completa en `float32` (≈690 MB).
* Tras cada ventana se guarda `resultados/{id}.checkpoint.json` y se publica
  `resultados/{id}.partial.json`; la interfaz muestra el avance **en vivo**
  (`GET /api/jobs/{id}/partial?since=N`).
* Si la app se cierra a la fuerza, al arrancar `recover_interrupted_jobs()`
  reencola el trabajo **desde el último punto** (o `POST /api/jobs/{id}/resume`).
* Limpieza de audio de dos pasadas: `highpass` + `afftdn` (reducción de ruido) +
  compresor + `loudnorm` medido (I/TP/LRA reales) con reintento automático si el
  FFmpeg instalado no soporta algún filtro.
* Anti-alucinación de Whisper: `word_timestamps`, `no_speech_threshold`,
  `log_prob_threshold`, `compression_ratio_threshold`, `prompt_reset_on_temperature`
  y `repetition_penalty`.

### 4.3 Diarización
* `resemblyzer.VoiceEncoder("cpu")` sobre segmentos de **1–12 s** (si Whisper devuelve
  bloques largos se trocean en ventanas de 6 s y se vota el hablante).
* **> 900 ventanas** → muestreo uniforme + `scipy.linkage(cosine)` + `fcluster(cutoff=0.62)`;
  el resto de segmentos se asigna al centroide más cercano (sin matrices O(n²)).
* Comparación con `voces.json`: similitud coseno **≥ 0.70** = hablante conocido.
* Guardar una voz **promedia** las huellas existentes (`⬇︎ 💾` en la tarjeta de hablantes).

* **Umbral adaptativo**: el corte del dendrograma se elige maximizando la silueta
  (2–8 voces) en vez del fijo `0.62`.
* **Reconocimiento de voces** con umbral calibrado (`POST /api/voces/calibrate`
  estima el p95 de similitud entre hablantes distintos) y clips de referencia en `./voces`.
* **Unión de turnos** consecutivos del mismo hablante (pausas < 1,1 s) para leer
  como diálogo real.
* **Corrección manual**: fusionar dos voces (`/api/jobs/{id}/speakers/merge`) o
  separar fragmentos seleccionados en una voz nueva (`/speakers/split`).

### 4.4 Acta con IA (`POST /api/ai/summarize`) — **gratis por defecto**

Hay **tres motores** y los dos primeros son **100 % gratuitos y locales**
(sin cuenta, sin API Key, sin cuota y sin enviar la reunión a internet):

| Motor | Costo | Requisitos | Calidad / velocidad |
|---|---|---|---|
| **IA local · Hugging Face** *(predeterminado)* | **Gratis** | `torch` + `transformers`; descarga única del modelo | Buena; 40–110 s por acta en CPU |
| **IA local · Ollama / LM Studio** | **Gratis** | [Ollama](https://ollama.com) o LM Studio abierto (el instalador lo deja listo en la etapa 6/8) | Muy buena (modelos 7–8B); 20–90 s |
| Google Gemini 2.5 Flash | Capa gratuita limitada (luego se factura) | API Key de Google AI Studio | Excelente; 5–20 s |

**Ollama sin pasos manuales:** la etapa **6/8** de `instalar.bat` instala Ollama
(winget o instalador oficial), descarga el modelo que elijas (`qwen2.5:7b-instruct`
4,7 GB por defecto, 3b o 1.5b si el equipo es modesto) y deja la app apuntando a él
con `python app.py --usar-ollama <modelo>`; el modelo se comprueba contra `/v1/models`.
Si luego quieres cambiarlo: `python app.py --usar-ollama qwen2.5:3b-instruct`.

**En la primera ejecución la app elige sola el mejor motor gratuito disponible:** si detecta
Ollama/LM Studio escuchando en `http://127.0.0.1:11434/v1`, lo usa; si no, usa Hugging Face.
Puedes cambiarlo en cualquier momento en **Minuta, Acta y Resumen Inteligente → Motor de IA**
con el botón **🔌 Probar conexión** (no gasta tokens).

**Modelos libres incluidos (licencia Apache-2.0, uso comercial permitido):**

| Modelo | Descarga | Nota |
|---|---|---|
| `Qwen/Qwen2.5-0.5B-Instruct` | ~1 GB | El más rápido en CPU |
| `Qwen/Qwen2.5-1.5B-Instruct` | ~3 GB | **Recomendado** |
| `Qwen/Qwen2.5-3B-Instruct` | ~6 GB | Mejor redacción |
| `HuggingFaceTB/SmolLM2-1.7B-Instruct` | ~3.4 GB | Alternativa compacta |

**Con Ollama (gratis) obtienes la mejor calidad sin pagar nada:**

```bat
winget install Ollama.Ollama
ollama pull qwen2.5:7b-instruct
:: la app lo detecta en http://127.0.0.1:11434/v1
```

* **Pre-filtro inteligente**: elige los bloques con más acuerdos, cifras y decisiones en un
  presupuesto de **12 000 caracteres** para que un modelo local termine en 1–2 minutos.
* Formatos: **Acta Ejecutiva Completa**, **Resumen Ejecutivo Directo**, **Lista de Acuerdos y Pendientes**.
* **Ollama sin sorpresas**: la app comprueba que el modelo configurado esté realmente
  descargado (`/v1/models`) y, si no, cambia a uno que sí exista; el error 404 de
  Ollama se traduce a instrucciones (`ollama pull …` + qué modelos tienes).
* **Vista formateada** del acta (pestañas «Vista formateada» / «Editar texto»): el
  markdown se ve como documento —títulos, viñetas, tablas— y así se imprime; el texto
  editable sigue siendo la fuente de verdad para copiar y exportar.
* Estructura del acta: encabezado, secciones numeradas en **MAYÚSCULAS** separadas con
  `---`, prosa en tercera persona, viñetas con la etiqueta en negritas, cierre con
  líneas de firma fijas (las añade la app, no el modelo). Prohibido dejar huecos tipo
  «[Nombre Presidencia]» o «[Monto]»: si el dato no se dijo, se omite.
* Prompt de *Secretario Técnico*: tercera persona, prohibido el lenguaje meta-textual
  (“en la transcripción”, “en este bloque”, “en el audio”, “notas”, “parciales”),
  corrección de errores fonéticos y conservación de **todas las cifras exactas**.
* Si eliges Gemini, la API Key se guarda en `ia_config.json`; la configuración local
  (modelo, URL y modelo de Ollama) se guarda en el mismo archivo.

#### Reuniones largas: *map-reduce* con citas

1. La transcripción se corta en **tramos de ~20 min** (`ACTA_BLOCK_SECONDS`).
2. Cada tramo se resume por separado (*map*) extrayendo temas, acuerdos, cifras
   exactas y pendientes **con su marca `[HH:MM:SS]`**.
3. **Redacción por secciones** (acta completa): con esos resúmenes el modelo primero
   propone el **índice del acta** (4 a 8 secciones temáticas), después redacta **una
   sección por llamada** (2–4 párrafos en tercera persona + viñetas por propuesta,
   empresa o partida, con las cifras exactas) y al final extrae un bloque `json` con
   `acuerdos / pendientes / siguientes_pasos`. Así el acta sale larga y formal aunque
   el modelo sea pequeño, en vez de una lista de temas.
4. **Verificación de cifras**: cada número del acta se compara con la
   transcripción completa; los que no aparecen literalmente se avisan en la interfaz.
5. El resultado se guarda en `resultados/{id}.acta.json` (`GET /api/jobs/{id}/acta`)
   y la tabla de acuerdos se rellena con datos estructurados (acuerdo, responsable,
   fecha, estatus, minuto).

`POST /api/ia/summarize/stream` devuelve **Server-Sent Events**: avance tramo a tramo
y el texto token a token (Ollama/LM Studio, Hugging Face y Gemini).

### 4.5 Cola, historial y exportación
* `WORK_QUEUE` + hilo trabajador único; cancelación por trabajo o por sesión de navegador.
* `historial.json`: máximo **60 entradas**; el audio se comprime a **`.m4a` AAC 32 kbps**
  en `./historial` con presupuesto de **800 MB** (se eliminan las más antiguas).
* `sesiones.json`: etiquetas de hablantes, acta y conclusiones editadas.
  Copia de seguridad diaria en `./backups/AAAA-MM-DD/` (14 días).
* Exportación `.docx` con `python-docx`: metadatos, participantes, transcripción con
  tiempos/hablantes y el acta final. También `.txt`, `.srt` y `.vtt`.
* **Se exporta lo que ves**: la interfaz envía `segments` y `speakers` con el texto ya
  corregido y los nombres reales; el servidor los guarda (`PATCH /api/jobs/{id}`) y los
  usa en todas las exportaciones posteriores.
* JSON a prueba de cortes: escritura atómica + copia `.bak` y recuperación automática
  (`read_json` avisa en el log si tiene que usar el respaldo).
* **Instancia única** (candado local) y **log rotativo** en `logs/app.log`
  (`GET /api/logs?lines=200`).

### 4.6 Arranque, red y diagnóstico

```bat
iniciar.bat                          :: abre http://127.0.0.1:8000
iniciar.bat --port 8010              :: otro puerto
iniciar.bat --host 127.0.0.1         :: (por defecto) solo este equipo
iniciar.bat --host 0.0.0.0 --token S3cr3t0   :: red local protegida con token
iniciar.bat --no-browser             :: sin abrir el navegador
iniciar.bat --force                  :: ignora el candado de instancia única
```

* `--host` es `127.0.0.1` por defecto: **la app no queda expuesta a la red** salvo
  que lo pidas explícitamente. Si la expones, usa `--token` (o la variable
  `TAE_TOKEN`): el navegador guarda la cookie al abrir `/?token=…`.
* `TAE_WINDOW_MIN=15` cambia el tamaño de ventana de transcripción (minutos).
* Todo lo relevante se registra en `logs/app.log` (rotativo, 4 archivos de 2 MB) y
  también se puede leer desde `GET /api/logs`.

### 4.7 Pruebas automáticas

```bash
python -m pytest              # 46 pruebas: funciones puras, exportación y API
TAE_E2E=1 python -m pytest tests/test_e2e_transcripcion.py -v   # con modelo real
```

Cubren `collapse_repeats`, `fix_question_marks`, `build_ai_transcript`,
`evict_history`, troceado del acta, verificación de cifras, subida reanudable,
`PATCH`/exportación con ediciones, búsqueda sin acentos y fusión/división de voces.

### 4.8 Versión portable (.exe)

```bat
compilar_exe.bat        :: instala PyInstaller y empaqueta en dist\TranscriptorActas\
```

El `.exe` **no** incluye modelos, FFmpeg ni datos: se crean junto al ejecutable.
Para llevarla en USB copia `dist\TranscriptorActas\` + `modelos\` (o déjala
descargar la primera vez con internet).

> **Antivirus / SmartScreen.** Un `.exe` sin firma digital suele generar avisos
> (PyInstaller es habitual en falsos positivos). Añade la carpeta de la aplicación a
> las exclusiones de Windows Defender (`Seguridad de Windows → Protección antivirus →
> Exclusiones`) y, si aparece SmartScreen, pulsa *Más información → Ejecutar de todas
> formas*. Alternativa sin `.exe`: `instalar.bat` + `iniciar.bat` (no genera avisos).

---

## 5. API (resumen)

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/health` | Estado de FFmpeg, hardware, modelos, cola, historial |
| POST | `/api/upload` | Subida multipart simple (límite 2 GB) |
| POST | `/api/upload/chunk` | Subida **por trozos de 4 MB reanudable** |
| GET | `/api/upload/status/{upload_id}` | Bytes ya recibidos (para reanudar) |
| POST | `/api/upload/complete` | Cierra la subida por trozos → `file_id` + duración |
| POST | `/api/jobs` | Crea trabajo (modo, idioma, opciones) |
| GET | `/api/jobs/{id}` | Progreso, estado, log, estadísticas, hablantes |
| GET | `/api/jobs/{id}/result` | Segmentos, info, hablantes, sesión guardada |
| PATCH | `/api/jobs/{id}` | Guarda ediciones (`segments`, `speakers`, `title`) |
| GET | `/api/jobs/{id}/partial?since=N` | Fragmentos nuevos mientras transcribe |
| POST | `/api/jobs/{id}/resume` | Reanuda desde el checkpoint |
| GET | `/api/jobs/{id}/peaks?buckets=900` | Envolvente de audio (forma de onda) |
| GET | `/api/jobs/{id}/acta` | Última acta guardada (texto + acuerdos) |
| POST | `/api/jobs/{id}/speakers/merge` · `/split` | Fusionar / separar voces |
| GET | `/api/jobs/{id}/media` | Audio con soporte de `Range` (seek) |
| GET | `/api/jobs/{id}/speaker-sample/{i}` | Muestra de 12 s de una voz |
| POST | `/api/jobs/{id}/cancel` · `/api/session/{sid}/cancel` | Cancelar |
| POST | `/api/jobs/{id}/continuar` | **Añade otra parte** de audio a la reunión (`{file_id}`) |
| PUT | `/api/jobs/{id}/acta` | Guarda el acta editada como **versión oficial** |
| GET | `/api/jobs/{id}/acta/versiones` | Versiones del acta (motor, modelo, fecha, caracteres) |
| POST | `/api/jobs/{id}/acta/restaurar` | Restaura una versión (`{v}`) |
| GET/PATCH | `/api/compromisos[/{id}]` | Acuerdos y pendientes de **todas** las reuniones |
| GET/POST | `/api/plantilla` | Plantilla del acta (organización, órgano, ciudad, firmas) |
| POST | `/api/historial/{id}` | Renombrar, etiquetar con una **serie** o **proteger** |
| DELETE | `/api/historial/{id}` | Borra audio + transcripción + acta de esa reunión |
| POST | `/api/historial/{id}/duplicar` | Copia el acta como base editable |
| POST | `/api/ia/summarize` (alias `/api/ai/summarize`) | Redacta acta/resumen/acuerdos |
| POST | `/api/ia/summarize/stream` | Lo mismo en **SSE** (tokens en vivo) |
| GET | `/api/search?q=` | Búsqueda global entre todas las reuniones (sin acentos) |
| GET | `/api/logs?lines=200` | Últimas líneas de `logs/app.log` |
| GET/POST | `/api/ia/config` | Motores, modelo local, URL/modelo de Ollama y API Key |
| POST | `/api/ia/test` | Prueba de conexión del motor (sin costo) |
| GET/POST | `/api/voces` | Huellas de voz conocidas (con clip y calidad) |
| GET | `/api/voces/{name}/audio` | Clip de referencia de una voz |
| POST | `/api/voces/calibrate` | Calibra el umbral de reconocimiento |
| GET/DELETE | `/api/historial[/{id}]` | Historial |
| GET/POST | `/api/session/{sid}[/save]` | Etiquetas y conclusiones |
| POST | `/api/export/{id}` | `{format, conclusion, title, segments?, speakers?}` · `format: acta` exporta **solo el acta** |

Documentación interactiva: `http://127.0.0.1:8000/api/docs`.

---

## 6. Atajos y detalles de la interfaz

* **«Nueva reunión»** (arriba a la derecha del resultado) o clic en el paso **1 · Cargar**
  → vuelve a la página principal para transcribir otra; lo que estabas viendo queda en el
  Historial. No hace falta recargar.
* Tus ajustes de transcripción (**modo**, idioma, *marcas de tiempo*, *párrafos*,
  *encadenar texto*, *modo música*) se recuerdan en el navegador: quedan como los dejaste.
  El modo por defecto es **Ultra**.
* `/` o `Ctrl+K` → buscar; `Enter` / `Shift+Enter` → siguiente/anterior coincidencia.
* Clic en cualquier fragmento o marca de tiempo → salta a ese segundo.
* `Espacio` → reproducir/pausar (fuera de campos de texto).
* Velocidades `1x / 1.25x / 1.5x / 2x` (se recuerdan en el navegador).
* Fragmentos con `avg_logprob < -0.6` en **ámbar** + botón **⚠️ Dudoso** para recorrerlos.
* `✏️ Editar texto` activa `contenteditable` en todos los fragmentos (se guarda en la sesión).
* Chips **+ hablante** para reasignar una intervención puntual.
* Tema oscuro `#0d1117` por defecto y tema claro; `Ctrl+P` genera una versión imprimible.
* `←` / `→` → retroceder/avanzar **5 s**; casilla **Seguir** para que la transcripción
  se desplace con el audio (o desactívala para leer mientras suena).
* **Forma de onda** coloreada por hablante: clic para saltar; los **capítulos** se
  generan solos (pausas > 90 s o tramos de 8 min) y también llevan al minuto.
* **↩️ Deshacer** (`Ctrl+Z`, hasta 30 pasos) y **♻️ Original** para volver al texto de Whisper.
* **🌐 Todas** busca en el historial completo (ignora acentos y mayúsculas).
* **🛠️ Corregir la diarización**: fusionar voces, separar fragmentos seleccionados y
  calibrar el reconocimiento; los discos **💾** guardan la huella de cada voz.
* Accesibilidad: modales con `role="dialog"` + foco atrapado, `aria-live` en progreso y
  avisos, foco visible y contraste alto.

---

## 6 bis. Acta institucional, versiones y compromisos

* **Plantilla del acta** (panel de IA → *Plantilla del acta*): organización, órgano que
  sesiona, ciudad, quien preside y secretaría técnica. Se usa en el **encabezado**, el
  **quórum** y el **pie de firmas**, tanto en la vista formateada como en el `.docx`.
  Por defecto viene «Junta Directiva»; los nombres nunca se inventan: si un campo está
  vacío quedan líneas de firma en blanco. Se guarda en `ia_config.json` (tu equipo).
* **Acta anterior**: al redactar puedes elegir la reunión previa (o dejar el modo
  automático, que toma la última de la misma *serie*). Solo se envían al modelo sus
  **acuerdos y pendientes**, para escribir «se dio seguimiento a lo acordado el …» y
  detectar compromisos repetidos o incumplidos. No se copia su texto.
* **Acuerdos editables**: en la tabla de acuerdos puedes corregir responsable, fecha y
  estatus; se guardan en `{id}.acta.json` y **no se pierden** al volver a redactar (solo
  se sustituye el texto). El panel **Compromisos** reúne los de todas las reuniones.
* **Versiones**: cada redacción y cada edición manual quedan como versión (motor, modelo,
  fecha, caracteres). Puedes **restaurar** cualquiera desde la barra del acta.
* **Cifras**: el panel de verificación lista los importes y porcentajes del acta que no
  aparecen literalmente en la transcripción, con un botón para **escuchar** ese momento.
* **Historial**: buscador (título, acta, serie, acuerdos), filtros, orden, **series**,
  renombrar, **proteger** (nunca se borra) y borrado con detalle de qué se elimina.
* **Duplicar**: crea una copia editable del acta (sin audio) para reuniones periódicas.
* **Continuar reunión**: añade otro audio a la misma reunión (tras un receso) y redacta
  **un solo acta**; las marcas de tiempo siguen siendo continuas y el acta anterior se
  marca como desactualizada hasta que la vuelvas a redactar.

---

## 7. Rendimiento y límites reales

| Escenario | Referencia aproximada |
|---|---|
| 10 min de audio, `turbo`, CPU 8 núcleos | ~3–6 min |
| 3 h de audio, `turbo`, CPU 8 núcleos | ~45–90 min |
| 3 h de audio, GPU CUDA (`float16`) | ~8–15 min |
| Acta con Qwen2.5-1.5B local, CPU | ~40–110 s |
| Acta con Qwen2.5-7B vía Ollama (CPU/GPU) | ~20–90 s |

* 3 h en float32 ≈ 690 MB de RAM solo para el audio decodificado.
* `rapido` es ideal para validar el flujo antes de una sesión larga.
* El historial comprime a 32 kbps: 3 h ≈ 43 MB.

---

## 8. Solución de problemas

**Un botón «no hace nada» o la interfaz parece vieja.** Casi siempre es la caché del
navegador. Mira el chip de versión arriba a la derecha: si sale en rojo
(«Interfaz vX · servidor vY»), recarga con **Ctrl+F5** (Cmd+Shift+R en Mac). Desde la
v2.5.0 el servidor versiona los estáticos (`app.js?v=…`) para que esto no vuelva a pasar.

| Síntoma | Solución |
|---|---|
| `FFmpeg no encontrado` | `winget install Gyan.FFmpeg` o define `FFMPEG_BIN=C:\ruta\ffmpeg.exe` y reinicia |
| `open() got an unexpected keyword argument 'metadata_errors'` | Actualiza PyAV (`pip install -U av`); la app ya no depende de PyAV para el WAV limpio |
| Diarización no disponible (`No module named 'resemblyzer'`) | `pip install resemblyzer` + `setuptools<81` (usa `webrtcvad-wheels` si falla la compilación) |
| IA local: `instala torch transformers accelerate` | `pip install torch --index-url https://download.pytorch.org/whl/cpu` y `pip install transformers accelerate` |
| Gemini 400/403 | Revisa la API Key y que el modelo `gemini-2.5-flash` esté habilitado |
| “No se detectó un servidor local” | Abre Ollama/LM Studio (`ollama serve`) o cambia al motor Hugging Face; ambos son gratis |
| Ollama dice que el modelo no existe | `ollama pull qwen2.5:7b-instruct` (o el nombre que pusiste en el campo) |
| Quiero 0 costo y 0 dependencias extra | Motor **Hugging Face local**: solo `torch` + `transformers`, sin cuenta ni internet |
| “No se detectó voz” | Activa *Modo música (sin VAD)* o sube el volumen del audio original |
| La descarga del modelo se corta | Vuelve a lanzar el trabajo: reanuda desde `./modelos/hub` |
| Puerto ocupado | `python app.py --port 8010` |
| Todo va lento en CPU | Usa `rapido`/`equilibrado`, cierra otras apps y evita audios en paralelo |

---

### 8.1 Problemas nuevos de la v2

| Síntoma | Causa y solución |
|---|---|
| «Otra instancia… ya está en ejecución» | Hay otra copia abierta: ciérrala o usa `iniciar.bat --force`. |
| La subida se corta y quieres retomarla | Vuelve a elegir **el mismo archivo**: continúa desde el último trozo (se guarda el id en el navegador). |
| Tras un corte de luz el trabajo no aparece | Abre la app: se reencola solo; si no, `POST /api/jobs/{id}/resume`. Revisa `logs/app.log`. |
| «Cifras del acta que no aparecen en la transcripción» | Aviso de verificación: el modelo pudo redondear. Revisa esas cifras o usa otro motor. |
| Ollama dice `model 'qwen2.5:7b-instruct' not found` | Ese modelo no está descargado. La app **elige sola** uno que sí tengas al abrir la configuración; si prefieres ese, ejecuta `ollama pull qwen2.5:7b-instruct`. El aviso de error te dice qué modelos tienes y cuál usar. |
| El acta sale corta o en lista de temas | Usa el formato **Acta Ejecutiva Completa**: se redacta por secciones (índice → 4–8 secciones → acuerdos). Con la IA local de 1.5B las secciones son más breves; con `Qwen2.5-3B-Instruct` u Ollama `qwen2.5:7b-instruct` salen bastante más largas. |
| El acta tarda mucho en una reunión de 3 h | Es normal: se resume por tramos (map) y luego se redacta. Con Ollama 7B ≈ 3–6 min; con IA local 1.5B puede tardar 10–20 min. |
| El navegador bloquea el portapapeles | La copia de la tabla funciona en `http://127.0.0.1`; en otros orígenes puede requerir permiso. |
| Windows Defender marca el `.exe` | Falso positivo de PyInstaller: añade la carpeta a exclusiones (ver 4.8). |
| Cualquier error al arrancar («Pydantic», imports, FFmpeg…) | Ejecuta **`iniciar.bat --check`** (o `python app.py --check`): imprime versiones de Python, Pydantic, FastAPI, FFmpeg, si los modelos de petición funcionan y el hash del `app.py` que está usando. Copia esa salida si necesitas ayuda. |
| `error: Microsoft Visual C++ 14.0 or greater is required` al compilar `_webrtcvad` | **No instales `webrtcvad`**: usa `pip install webrtcvad-wheels` y luego `pip install --no-deps resemblyzer`. El `instalar.bat` actualizado ya lo hace así. |
| «Falta la IA local (torch, transformers, accelerate)» al redactar el acta | Ejecuta `instalar.bat` y elige la **opción 1** en la etapa 5/8 «IA para redactar el acta», o deja que la etapa 6/8 instale Ollama con el modelo. O pega: `.venv\Scripts\python -m pip install torch transformers accelerate`. Alternativa sin descargar nada: `ollama pull qwen2.5:7b-instruct` y elige el motor «Ollama / LM Studio» |
| `Failed building wheel for webrtcvad` durante `pip install -r requirements.txt` | Ese paquete ya no está en `requirements.txt` (pasó a `requirements-diarizacion.txt` como `webrtcvad-wheels`). Actualiza los archivos y repite la instalación. |
| `ImportError: cannot import name 'ConfigDict' from 'pydantic'` en `app.py` | Tenías **Pydantic 1.x** en el entorno y FastAPI lo acepta. Desde la 2.1.1 el código funciona con Pydantic 1 y 2, y `instalar.bat` actualiza a Pydantic 2 solo. Si quieres forzarlo a mano: `.venv\Scripts\pip install -U "pydantic>=2.7"`. |

### 8.2 Límites conocidos

* La diarización sigue usando **resemblyzer**; `pyannote`/Sortformer requieren token de
  Hugging Face y licencia aceptada, así que quedan como mejora opcional.
* La separación por **canales** (llamadas estéreo con una vía por participante) aún no
  está automatizada: se puede forzar transcribiendo cada canal por separado.
* El streaming de tokens depende del motor: Gemini entrega el texto al final (su API
  se llama sin `stream`), Ollama y la IA local sí van token a token.

---

## 9. Privacidad

* El audio, los embeddings y la transcripción **no salen del equipo**.
* Los motores **Hugging Face local** y **Ollama / LM Studio** funcionan sin internet
  una vez descargado el modelo: 0 costo y 0 datos enviados.
* Solo se envía texto a Google si eliges explícitamente el motor **Gemini**.
* `ia_config.json` guarda tu API Key en texto plano local: protégelo o usa la IA local.
* La primera vez que se descarga un modelo se consulta Hugging Face; después todo es offline.

---

Hecho para juntas de hospital, patronato, comisiones y consejo: **3 horas de reunión → acta lista para firmar.**
