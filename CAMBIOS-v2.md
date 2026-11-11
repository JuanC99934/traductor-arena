# Cambios aplicados · v1.0.0 → v2.0.0

Registro de todo lo que se implementó a partir de la crítica técnica, con el
archivo/función donde vive cada cosa. Todo se probó con `pytest` (46 pruebas) y
con una verificación de interfaz real en navegador (21 comprobaciones, 0 errores JS).

## 1. Críticas críticas (pérdida de datos y memoria)

| Problema | Solución implementada | Dónde |
|---|---|---|
| Las exportaciones ignoraban las correcciones de texto y los nombres de hablante | `exportDoc()` envía `segments` + `speakers`; el backend los normaliza, los **persiste** en `resultados/{id}.json`, actualiza `historial.json` y exporta con ellos. Nuevo `PATCH /api/jobs/{id}` | `static/app.js → exportDoc`, `app.py → api_export / persist_edits / _clean_segments` |
| Un reinicio del proceso perdía un trabajo de 3 h | Checkpoint por ventana (`resultados/{id}.checkpoint.json`) + `.job.json`; `recover_interrupted_jobs()` reencola al arrancar y `POST /api/jobs/{id}/resume` lo relanza desde el último punto. El WAV limpio se reutiliza | `app.py → transcribe_wav`, `persist_job_meta`, `recover_interrupted_jobs` |
| Subida de 2 GB sin reanudación | Subida por trozos de 4 MB con `/api/upload/chunk`, `status` y `complete`; el navegador guarda el `upload_id` y continúa donde se cortó (3 reintentos por trozo) | `app.py → api_upload_chunk/status/complete`, `static/app.js → uploadFile/sendChunk` |
| `read_json()` devolvía `{}` ante cualquier error (pérdida silenciosa del historial) | Lectura con recuperación desde `.bak` + aviso en el log; escritura atómica con `fsync` y copia `.bak` previa | `app.py → read_json / write_json` |
| `--host 0.0.0.0` por defecto (expuesta a la red) | `--host 127.0.0.1` por defecto, candado de instancia única y token opcional (`--token` / `TAE_TOKEN`) con cookie | `app.py → main`, `security_middleware`, `acquire_single_instance` |
| Cabecera `Content-Disposition` con acentos (descarga rota) | `filename=` solo ASCII + `filename*=UTF-8''…` | `app.py → api_export` |

## 2. Rendimiento

* **Transcripción por ventanas** de ~20 min con 5 s de solape y contexto continuo
  (`_window_plan`, `TAE_WINDOW_MIN`): memoria constante (~150 MB) en lugar de
  ~690 MB por cargar 3 h en `float32`.
* **Resultados parciales** `resultados/{id}.partial.json` + `GET /api/jobs/{id}/partial?since=N`;
  la interfaz los pinta en la tarjeta de progreso (avance en vivo).
* **Lista progresiva** de la transcripción (400 fragmentos + botón/carga al hacer scroll)
  y parches incrementales en lugar de reconstruir 3 000 nodos.
* `GZipMiddleware` (respuestas de varios MB comprimidas), caché de la envolvente de audio
  (`/peaks`), caché de `whisper_is_local()` con `lru_cache` + invalidación explícita.

## 3. Calidad de transcripción

* `word_timestamps=True` (mejores tiempos y unión de turnos).
* Anti-alucinación: `no_speech_threshold=0.6`, `log_prob_threshold=-1.0`,
  `compression_ratio_threshold=2.4`, `prompt_reset_on_temperature`, `repetition_penalty`,
  temperaturas 0→1 para reintentar tramos con `compression_ratio` alto.
* Limpieza de audio: `highpass=f=75` + **`afftdn`** (reducción de ruido) + compresor +
  **`loudnorm` de dos pasadas** (medición real de I/TP/LRA/thresh) con reintento automático
  si el FFmpeg instalado no soporta el filtro.
* `join_same_speaker_segments()`: une turnos consecutivos del mismo hablante (< 1,1 s)
  para que la transcripción se lea como diálogo.

## 4. Diarización

* Umbral del dendrograma **adaptativo por silueta** (`adaptive_cutoff`) en lugar del 0.62 fijo.
* Umbral de voz conocida **calibrado** (`voice_match_threshold` + `POST /api/voces/calibrate`
  con p95 de similitud entre voces distintas).
* Clips de referencia y calidad en `voces.json` (`/api/voces/{name}/audio`).
* **Fusionar** voces (`/speakers/merge`) y **separar** fragmentos seleccionados en una voz
  nueva (`/speakers/split`), con promediado de huellas.

## 5. Acta con IA

* **Map-reduce**: tramos de ~20 min / 14 000 caracteres → resúmenes verificables con
  marcas `[HH:MM:SS]` → documento final (antes se recortaba el contexto y se perdía la
  mitad de una reunión de 3 h).
* **Verificación de cifras**: cada número del acta se compara con la transcripción y las
  que no aparecen se avisan en la interfaz.
* **Acuerdos estructurados** en JSON (acuerdo, responsable, fecha, estatus, minuto) y tabla
  real con salto al audio, en lugar de solo regex.
* **Participantes y glosario** enviados al modelo; normalización de cifras para comparar.
* **Streaming**: `POST /api/ia/summarize/stream` (SSE) con avance por tramos y tokens en
  vivo en Ollama/LM Studio y en la IA local (transformers `TextIteratorStreamer`).
* Acta guardada en `resultados/{id}.acta.json` (`GET /api/jobs/{id}/acta`).

## 6. Interfaz

* Forma de onda pintada por hablante (`/api/jobs/{id}/peaks`), cursor de reproducción y clic para saltar.
* Capítulos automáticos (pausas > 90 s o tramos de 8 min) con salto al minuto.
* Búsqueda **sin acentos** con mapa de posiciones (`foldWithMap`) y **búsqueda global**
  entre reuniones (`/api/search`).
* **Deshacer** (30 pasos, `Ctrl+Z`), **restaurar original** y aviso de que se exportan las ediciones.
* **±5 s** (botones y flechas), casilla **Seguir audio**.
* Corrección de diarización desde la interfaz (fusionar/seleccionar/separar/calibrar).
* Accesibilidad: `role="dialog"`, `aria-modal`, foco atrapado, `aria-live`, `role="progressbar"`,
  foco visible y clase `sr-only`.
* Reproductor reorganizado en dos filas para que la onda y los capítulos no tapen los controles.

## 7. Operación, distribución y pruebas

* Log rotativo `logs/app.log` (4 × 2 MB) + `GET /api/logs`.
* Candado de instancia única (`--force` para ignorarlo).
* `app.spec` + `compilar_exe.bat` (PyInstaller) y `iniciar.bat` con `--port/--host/--token/--force`.
* Nota de antivirus/SmartScreen y exclusiones en el README.
* `tests/`: 46 pruebas (`test_core`, `test_export`, `test_api`, `test_e2e_transcripcion`).
* Modelos Pydantic para los cuerpos de petición (validación + documentación en `/api/docs`).
* README actualizado (endpoints, flags, pruebas, `.exe`, límites conocidos).

## Pendiente / no incluido en esta versión

* Sustituir resemblyzer por **pyannote 3.1 / DiariZen / Sortformer** (requiere token de HF y
  aceptar licencia) y **diarización por canales** para llamadas estéreo.
* Restauración de puntuación con modelo dedicado (Whisper ya la aporta) y traducción
  (excluida a propósito por el alcance del proyecto).
