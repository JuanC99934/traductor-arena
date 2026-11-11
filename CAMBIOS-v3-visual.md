# Rediseño visual · v2.0.0 → v2.1.0

Cambios **solo de presentación y de interacción** sobre la app de 4 archivos
(`app.py`, `static/index.html`, `static/app.js`, `static/style.css`).
No se tocó ningún motor de transcripción, diarización, IA ni exportación.

Estética elegida: **«más premium»** — fondo con degradado sutil, cristal (blur) en
cabecera y reproductor, acento **índigo** (antes azul GitHub) y elevación en 3 niveles.

---

## 1 · Qué cambió, punto por punto

| # | Antes | Ahora |
|---|-------|-------|
| 1 | 26 tamaños de fuente sueltos (89 declaraciones), base 12–12.5 px | Escala cerrada de 10 pasos (`11 / 12 / 13 / 14 / 15 / 16 / 18 / 20 / 22 / 26 px`) como tokens `--fs-*`, base **14 px**, transcripción a **15.5 px / 1.62** y ancho máximo **78 ch** (`tabular-nums` solo en tiempos y cifras) |
| 2 | 47 emoji distintos como iconos (y glifos rotos en algunos equipos) | **Sprite SVG inline** con 48 símbolos estilo Lucide, trazo 1.8 px, `currentColor`, `<use href="#i-…">`; tamaños 15/18/22/30 px. Sin CDN, sin emoji. Se conservan `═ ─ → ← ✕` |
| 3 | Reproductor de 2 filas + barra de capítulos: **163 px** (≈16 % de la pantalla) | Reproductor de **una fila, 66 px** (−59 %): play · ±5 s · tiempo · pista de 30 px **con la forma de onda detrás** · velocidad · «Seguir». Los capítulos pasan a un desplegable **«Índice»** en la cabecera de la transcripción |
| 4 | ~10 colores libres para hablantes que chocaban con verde/ámbar/rojo semánticos | Paleta **categórica de 10 tonos** (`--spk-1…10`) con luminancia y croma fijos, versión propia para tema claro. Verde/ámbar/rojo quedan **solo** para estados |
| 5 | Cada fragmento repetía avatar + nombre; lo dudoso era una etiqueta «⚠ dudoso» | Transcripción **por turnos**: barra de 3 px con el color del hablante, nombre y hora una sola vez por turno, y las palabras dudosas se marcan con **subrayado ondulado ámbar** en lugar de la etiqueta |
| 6 | 6 cajas iguales de estadísticas | **Jerarquía nueva**: duración a 26 px como protagonista + una línea (`1.9× · 516 palabras · 3 hablantes · 48 fragmentos · procesado en 7 min 1 s`) y un chip **«5 dudosos» que filtra** la transcripción |
| 7 | Sin señal de progreso; el botón principal quedaba fuera de vista | **Stepper visible** (Cargar · Transcribir · Revisar) con estados hecho/activo, **CTA pegajosa** en el panel izquierdo y tarjetas numeradas |
| 8 | Sin estados de carga, toasts tapaban los botones de velocidad, botones de icono de 26–30 px | Toasts arriba a la derecha (ya no tapan controles), **3 niveles de sombra**, transiciones de 120–180 ms con `prefers-reduced-motion`, halo de foco de 2 px, esqueletos, y botones de icono de **34–40 px** |

---

## 2 · Métricas medidas (antes → después)

| Métrica | v2.0 | v2.1 |
|---|---|---|
| Tamaños de fuente distintos en CSS | 26 | **15** (10 en la escala de UI) |
| Valores hex sueltos fuera de tokens | 37 | **10** (resto son impresión) |
| Tokens de tema (`:root`) | 28 | **60** + bloque claro |
| Alto del reproductor | 163 px | **66 px** |
| Filas del reproductor | 2 + capítulos | **1** |
| Emoji usados como iconos | 47 distintos | **0** |
| Iconos SVG disponibles | 0 | **48** |
| Botones de icono | 26–30 px | **34–40 px** |
| Radios distintos | 999/14/10/8/7/3 px | 4 tokens (8/12/16/20) + píldora |
| Niveles de sombra | 2 | **4** + brillo interior |

---

## 3 · Detalles técnicos que conviene saber

- **Tokens de hablante renombrados a `--spk-N`**: la escala de espaciado usaba
  `--sp-1…--sp-6`, y la paleta `--sp-1…--sp-10` se pisaba a sí misma (las barras de
  turno salían grises y el canvas se quedaba sin color). El espaciado pasó a
  `--space-N`.
- **Canvas y `var()`**: la forma de onda necesita colores reales, así que hay un
  `resolveColor()` con caché por tema que traduce el token a su valor calculado.
- **Onda más legible**: se agrupan las 900 muestras por columna (máximo de cada
  grupo) y se normaliza contra el pico real, con barras redondeadas.
- **Dos tokens inexistentes** (`--panel-2`, `--warn`) se usaban 9 veces desde la v2:
  ahora apuntan a `--bg-elev-2` y `--amber`.
- **Tema claro** completo con `[data-theme="light"]` (fondo `#eef1f8`, acento
  `#5b63f0`, paleta de hablantes oscurecida para contraste AA).
- **Hoja de impresión** nueva: al imprimir desaparecen reproductor, paneles y
  controles; queda el acta y la transcripción limpias en blanco y negro.
- `VERSION` pasó a **2.1.0**; el `README.md` recoge las novedades.

---

## 4 · Verificación

| Comprobación | Resultado |
|---|---|
| `python3 -m pytest tests` | **45 passed, 1 skipped** |
| Suite de interfaz (Playwright, 28 checks) | **28/28** |
| Errores de JS / consola | **0** |
| Rutas de preview (`/`, `/static/*`, `/api/*`) | 200 |
| Anchos probados | 1440 / 1280 / 1024 px, sin desbordes en el reproductor |

Los 28 checks cubren: arranque sin errores, chips de estado, stepper, carga,
restauración de la última reunión, cabecera con duración destacada, transcripción de
48 fragmentos, barras de turno por hablante, nombre una vez por turno, índice de
capítulos, cierre del índice, reproductor ≤ 80 px, onda dibujada, play/pausa con
cambio de icono, velocidad, buscador con contador, filtro de dudosos, edición en
línea, restauración del original, acta con IA, tabla de acuerdos, exportaciones
DOCX/TXT/SRT/VTT, tema claro y consola limpia.

---

## 5 · Pendiente / fuera de alcance

- Resaltar en la onda las palabras del turno bajo el cursor (requiere alinear
  tiempos por palabra; hoy la onda se colorea por hablante).
- Colapsar automáticamente todo el panel izquierdo a una tarjeta-resumen tras la
  carga (hoy se compacta con la CTA pegajosa y las tarjetas numeradas).
- Densidad «compacta» opcional para transcripciones de 3 h en pantallas pequeñas.

---

## 6 · Corrección 2.1.1 — incompatibilidad con Pydantic 1.x

**Síntoma reportado:** al arrancar tras instalar, `app.py` fallaba en la línea del
bloque `17.5 MODELOS DE PETICIÓN` con
`ImportError: cannot import name 'ConfigDict' from 'pydantic'`.

**Causa:** `fastapi>=0.110` acepta tanto Pydantic 2 como Pydantic 1. Si en el
equipo ya había un Pydantic 1.10 instalado, `pip` lo daba por bueno, no lo
actualizaba y la app —escrita para la API de Pydantic 2 (`ConfigDict`,
`model_dump`)— reventaba al importarse.

**Solución aplicada (compatible con ambas versiones):**

| Punto | Cambio |
|---|---|
| Detección de versión | Se mira el número de versión (`pydantic.VERSION`), no el import: **pydantic 1.10.18 incluye un `ConfigDict` de compatibilidad** que engaña al `try/except` |
| Configuración del modelo base | `model_config = ConfigDict(...)` en Pydantic 2 / `class Config` con `extra`, `anystr_strip_whitespace` y `allow_population_by_field_name` en Pydantic 1 (sin esto, `model_config` se colaba como campo en el `dict()` de salida) |
| Serialización | `as_dict()` usa `model_dump()` si existe y `dict()` si no |
| Cuerpos de petición opcionales | Nuevo `body_default()`: en Pydantic 2 `Body(default_factory=…)`; en Pydantic 1 una instancia por defecto, porque allí FastAPI devolvía **422** cuando `PATCH /api/jobs/{id}` o `POST /api/export/{id}` llegaban sin cuerpo |
| `requirements.txt` | Se fija `pydantic>=2.7` explícitamente |
| `instalar.bat` | Nuevo paso **4/5**: comprueba la versión de Pydantic y actualiza si es 1.x (avisa si no puede; la app arranca igual) |
| `compilar_exe.bat` | Misma comprobación antes de empaquetar, para no generar un `.exe` roto |
| `README.md` | Entrada nueva en «Solución de problemas» con el mensaje exacto y el arreglo manual |

**Verificación (probado en dos entornos reales):**

| Entorno | pytest | Importación | Endpoints |
|---|---|---|---|
| Pydantic **2.14.1** + FastAPI 0.162 | 45 passed, 1 skipped | OK (`PYDANTIC_V2 = True`) | export DOCX/TXT/SRT/VTT 200; sin cuerpo 200; PATCH/export de job inexistente → 404 |
| Pydantic **1.10.18** + FastAPI 0.110.3 | 45 passed, 1 skipped | OK (`PYDANTIC_V2 = False`) | export y PATCH sin cuerpo → 404 correcto (antes 422); `from`/`from_` y campos extra OK |

### 6.1 Refuerzo 2.1.2 — que el error sea imposible de confundir

Tras la corrección 2.1.1 el problema podía seguir apareciendo si el `app.py` que se
ejecutaba era el antiguo (copia vieja, o un `.exe` ya compilado). Para que eso se
detecte en segundos:

| Cambio | Detalle |
|---|---|
| Comprobación de Pydantic al **principio** del archivo (bloque 0.0) | Si falta Pydantic o hay un problema, sale un mensaje claro y accionable en lugar de un traceback en mitad del archivo (antes, «línea 3404») |
| Nuevo **`iniciar.bat --check`** (= `python app.py --check`) | Informe de: versión de la app, **ruta, número de líneas y sha256 del `app.py` en uso**, Python, Pydantic (y en qué modo va), FastAPI/Uvicorn/NumPy/SciPy/python-docx, faster-whisper/resemblyzer/transformers, FFmpeg/FFprobe, prueba real de los modelos de petición (alias `from`, campos extra), escritura en disco y aviso si hay un `.exe` antiguo en `dist\` |
| Avisos de combinaciones incompatibles | FastAPI < 0.100 con Pydantic 2 (error seguro) y FastAPI < 0.110 con Pydantic 1 (funciona, pero conviene actualizar) |
| Banner al arrancar | Muestra `v2.1.2` y el modo de Pydantic en la consola, para saber de un vistazo qué código se está ejecutando |

### 6.2 Refuerzo 2.1.3 — los modelos ya no dependen de la versión de Pydantic

Se eliminaron **todas** las piezas específicas de versión de los modelos de petición:
ya no se importa `ConfigDict` ni se declara `model_config` / `class Config`. Todos los
campos son `Any` y el comportamiento por defecto de Pydantic 1 y 2 ya es ignorar los
campos desconocidos, así que el bloque no puede fallar por la versión instalada.
Solo queda una rama de compatibilidad, y únicamente para los cuerpos de petición
opcionales (`body_default()`).

Nuevo `arreglar-pydantic.bat`: con doble clic muestra la versión de Pydantic,
actualiza a Pydantic 2 si se puede y lanza `app.py --check` al final.

**Cómo saber si el `app.py` que se está ejecutando es el corregido:** en la versión
corregida la línea 3404 ya **no** menciona pydantic (el bloque de compatibilidad está
al principio del archivo, sobre la línea 50). Si el error sigue citando «app.py, línea
3404», se está ejecutando el archivo antiguo o un `.exe` compilado antes del cambio.

---

## 7 · Corrección de instalación 2.1.4 — `webrtcvad` y el compilador de C++

**Síntoma:** al instalar en Windows aparece

```
building '_webrtcvad' extension
error: Microsoft Visual C++ 14.0 or greater is required.
ERROR: Failed building wheel for webrtcvad
```

**Causa:** `resemblyzer` (identificación de hablantes) depende del paquete
**`webrtcvad`**, que solo se publica como código fuente: necesita compilador C++.
Además, en `requirements.txt` estaba la cadena completa, así que ese fallo **abortaba
el resto de la instalación**.

**Solución:**

| Cambio | Detalle |
|---|---|
| `requirements.txt` | Solo dependencias **obligatorias**, todas con *wheel* precompilado |
| `requirements-diarizacion.txt` (nuevo) | `webrtcvad-wheels` (mismo módulo `webrtcvad`, binarios para Python 3.10–3.15), `librosa`, `setuptools<81` |
| `requirements-ia.txt` (nuevo) | `transformers`, `accelerate` |
| `requirements-opcional.txt` (nuevo) | Atajo con todos los extras |
| `instalar.bat` | Instala en **6 pasos por etapas** (obligatorio → hablantes → IA local) y pregunta por las opcionales (con temporizador). Un fallo en una etapa opcional ya no detiene la instalación |
| `resemblyzer` | Se instala con **`--no-deps`** después de `webrtcvad-wheels`, para que pip no reintente compilar |
| Versión de Python | El instalador avisa si detecta **3.14+** (pocos binarios aún) y recomienda 3.12/3.13 |
| `app.py --check` | Ahora informa de `webrtcvad`, `resemblyzer` y una línea **«Diarización: activa/desactivada»** con el comando exacto que falta |
| `README` | Instalación por etapas y tres entradas nuevas de solución de problemas |

**Verificado:** `pip download webrtcvad-wheels --platform win_amd64 --python-version 3.14`
encuentra `webrtcvad_wheels-2.0.14.post1-cp314-cp314-win_amd64.whl` (existe binario
para 3.14), `pip install webrtcvad-wheels` + `pip install --no-deps resemblyzer`
completa sin compilar nada, y `import webrtcvad` funciona. La suite sigue en
**45 passed, 1 skipped**.

### 7.1 Refuerzo 2.1.5 — el instalador ya no depende de `requirements.txt`

Seguía apareciendo el error de `webrtcvad` al ejecutar el instalador nuevo, porque el
paso 3 lanzaba `pip install -r requirements.txt` y ese archivo en el equipo del usuario
**todavía era el antiguo** (el que incluía `resemblyzer`, que arrastra `webrtcvad`).

| Cambio | Detalle |
|---|---|
| El paso obligatorio ya no lee `requirements.txt` | Instala una **lista explícita** escrita dentro del propio `instalar.bat`: `fastapi`, `uvicorn`, `python-multipart`, `pydantic`, `faster-whisper`, `numpy`, `scipy`, `python-docx` |
| `pip install --only-binary=:all:` | Si algún paquete no tuviera binario precompilado, pip **falla en vez de intentar compilar**. Así nunca vuelve a pedir Visual C++ (si falla, reintenta sin la marca y avisa) |
| `requirements.txt` antiguo | El instalador lo detecta (`findstr`), lo renombra a `requirements-antiguo.txt` y **genera uno correcto** |
| `uvicorn` sin extras | Se quitan `httptools`/`watchfiles` (compilados) de la ruta crítica; la app no los necesita |
| Todas las etapas opcionales | También con `--only-binary=:all:` y mensajes claros de qué ha fallado y qué se pierde |

**Verificado:** instalación limpia en un venv nuevo con
`pip install --only-binary=:all: fastapi uvicorn python-multipart pydantic faster-whisper numpy scipy python-docx`
→ 30 paquetes instalados **sin compilar nada**; `app.py --check` responde «todo listo» y
el servidor arranca y sirve `/api/health` con 200 con solo esas dependencias
(diarización desactivada, transcripción operativa).

### 7.2 Versión 2.1.6 — la descarga del modelo, visible y sin sustos

La instalación ya terminaba, pero al transcribir por primera vez la app se
quedaba callada mientras bajaba el modelo (hasta 1,6 GB en modo Turbo).

| Mejora | Detalle |
|---|---|
| Progreso real en el registro | Cada 3 s: `2,4 GB de 3,1 GB (78 %) · 12,5 MB/s · faltan ~4 min`, medido sobre `./modelos/hub` (incluye el almacén de blobs compartido de `huggingface_hub` 2.x) |
| Tamaño antes de empezar | El selector muestra el peso (`Turbo · 1,6 GB`) y la ayuda dice «se descarga la primera vez, solo una vez» |
| Chip en la cabecera | `Descargando modelo 34 %` mientras el modelo baja |
| Descarga previa | `python app.py --descargar turbo` / `--descargar todos`, con avance en consola y reanudación tras `Ctrl+C` |
| Mensaje final | «Modelo listo (1,6 GB). Ya queda guardado en ./modelos y no se vuelve a descargar» |
| **Bug corregido** | Con un modelo ya descargado, la app entraba en modo offline y **no permitía bajar ningún otro** («outgoing traffic has been disabled»). Ahora `allow_download()` parchea también las copias de la constante en todos los módulos de `huggingface_hub`, y solo se fuerza offline cuando el modelo pedido ya está en disco |

**Verificado:** descarga real de `base` (145 MB), `small` (486 MB) y `medium`
(1,5 GB) con porcentaje y ETA; `--descargar preciso` dos veces seguidas responde
«ya descargado»; `whisper_is_local()` detecta los modelos con el nuevo almacén de
blobs; 45 tests pasan.

### 7.3 Versión 2.1.7 — el instalador también baja los modelos

`instalar.bat` ahora tiene **7 etapas** y la 6/7 se encarga del modelo de voz:

```
=== 6/7  Descargar el modelo de voz ahora (opcional, pero recomendado) ===
  1) Rapido ........ 145 MB    4) Preciso ....... 1.5 GB
  2) Equilibrado ... 486 MB    5) Ultra ......... 3.1 GB
  3) Turbo ......... 1.6 GB    6) Todos ......... ~7 GB
  N) ahora no
```

* Menú con `choice` (60 s de espera, por defecto **3 = Turbo**, el modo con el que
  arranca la app para que no haya descargas sorpresa después).
* La descarga la ejecuta `python app.py --descargar <modo>`: progreso con
  porcentaje, velocidad y ETA; `Ctrl+C` la pausa y **se reanuda** al repetirla.
* Si falla, se apunta en `!FALLOS!` y la instalación **no se detiene**: el modelo
  se bajará en la primera transcripción.
* `app.py --check` añade la línea **«Modelos de voz»** con lo ya descargado y su
  peso real, para saber si la etapa 6/7 funcionó:
  `Modelos de voz ...... turbo (1,6 GB) · rapido (141,0 MB)` o
  `ninguno descargado (se bajan al transcribir)`.
* El modelo de IA local (acta) sigue siendo opcional y no se descarga aquí: se
  baja la primera vez que se redacta, o se usa Ollama/LM Studio.

**Verificado:** `--descargar rapido` real (145 MB) y `--check` mostrando
`rapido (141,0 MB)`; al borrar `modelos/` vuelve a decir «ninguno descargado».
45 tests en verde.

### 8. Versión 2.1.8 — el botón «Iniciar transcripción» ya no tapa los ajustes

**Síntoma:** al desplazar el panel izquierdo, el botón morado de iniciar flotaba
encima de los campos («Modo de precisión», «Idioma», «Opciones avanzadas»,
interruptores…), y como su fondo era un degradado semitransparente, el texto se
leía por debajo: parecía un botón pegado sobre la configuración.

**Causa:** `.panel-left .card-cta` tenía `position: sticky; bottom: 80px` dentro de
un contenedor que ya hacía scroll (`overflow-y: auto`). Un `sticky` no reserva
espacio, así que flotaba sobre el contenido.

**Arreglo (estructura, no parche):**

| Antes | Ahora |
|---|---|
| `<aside>` con scroll propio y el CTA `sticky` dentro de la tarjeta de ajustes | `<aside>` con dos zonas: `.panel-scroll` (las tarjetas) y `.panel-cta` (el botón), **fuera** de la zona con scroll |
| El botón se superponía al desplazar | El botón es un pie fijo del panel: se ve siempre y nunca se cruza con un campo |
| `max-height: calc(100vh - 100px)` con la cabecera midiendo 66–110 px | `app.js` mide la cabecera (`--head-h`, con `ResizeObserver`) y el panel calcula `top` y `max-height` con ese valor |

**Comprobado con navegador real (Playwright) a 1920×1080, 1366×768, 1280×720,
tema oscuro y claro, y móvil 430×900:** el CTA queda siempre por debajo del área
scrolleable (separación de 12 px, ningún solape), el panel nunca se sale del alto
de la ventana y subir un archivo vuelve a habilitar el botón
(`disabled: true → false`, sin errores de JavaScript).
Capturas: `capturas/cta-fix-1366-arriba.png`, `-abajo.png`, `-claro.png`,
`-movil.png`, `-con-archivo.png`.

### 9. Versión 2.2.0 — «oído» local opcional con un modelo de audio de Ollama

Idea del usuario: usar **`lfm3-audio`** (LiquidAI LFM3-Audio-2B, 2 GB, en Ollama)
dentro de la app. Se integra como **herramienta opcional**, no como motor de
transcripción: un 2B con 32K de contexto no puede transcribir 3 horas con marcas
de tiempo, pero sí decir en 30 segundos de qué va la reunión.

**Backend**

| Pieza | Para qué |
|---|---|
| `GET /api/ia/audio` | Detecta Ollama y qué modelos aceptan audio (lee `capabilities` de `/api/show`; si el servidor es antiguo, lo deduce del nombre). Devuelve aviso claro y el «no sustituye a la transcripción» |
| `POST /api/ia/audio/escuchar` | `{job_id \| file_id, segundos, desde, pregunta, modelo}` → trozo de audio + respuesta del modelo |
| `clip_audio_wav()` | Trozo WAV 16 kHz mono en memoria: **FFmpeg** si está, **PyAV** (viene con faster-whisper) si no, y WAV con la librería estándar. Probado con M4A sin FFmpeg a offsets 0 s, 60 s y 300 s |
| `_av_clip_wav()` | PyAV a mano: `decode_audio()` de faster-whisper se rompe con algunas versiones de `av` («unexpected keyword argument 'metadata_errors'») |
| `ollama_audio_chat()` | Envía el audio por **`/v1/chat/completions` con `input_audio`** (la única ruta de Ollama que acepta audio; el campo `audios` de `/api/chat` se ignora en silencio) y, si falla, reintenta por `/v1/audio/transcriptions` |
| Límites | 20 s a 180 s por consulta; el modelo se guarda en `ia_config.json` (`audio_model`) |

**Interfaz** (panel de la reunión, `<details>` plegado): modelo detectado, trozo a
escuchar (20 s / 30 s / 1 min / 2 min), pregunta opcional, botón *Escuchar*,
estado del servidor y caja con la respuesta. Si no hay Ollama, se explica y el
botón queda desactivado.

**Verificado** con un Ollama falso (protocolo real: `/v1/models`, `/api/tags`,
`/api/show`, `/v1/chat/completions`): detección de `lfm3-audio:latest` entre dos
modelos, envío de 640 044 bytes de WAV (20 s) y respuesta mostrada en la interfaz
(`lfm3-audio:latest · 30 s de audio · 0.2 s de proceso`), sin errores de
JavaScript. **50 pruebas** en verde, 6 de ellas nuevas (`tests/test_modelo_audio.py`).

### 10. Versión 2.2.1 — el instalador ya deja lista la IA del acta

**Síntoma:** al pulsar «Redactar con IA» con el motor de Hugging Face salía
`No se pudo redactar · Para usar la IA local instala: pip install torch transformers accelerate
(detalle: No module named 'transformers')`. Causa: la etapa 5/7 del instalador
preguntaba «¿IA local?» con **N por defecto**, así que casi todo el mundo acababa
sin `torch` ni `transformers`, y el motor por defecto de la app era justamente ese.

| Cambio | Detalle |
|---|---|
| Etapa 5/7 con menú | `1) IA local de Hugging Face (torch + transformers + accelerate, ~2.5 GB)` **por defecto** · `2) Ya tengo Ollama / LM Studio` |
| Instalación real | `torch` desde el índice **CPU** (`--only-binary=:all:`), luego `transformers` y `accelerate`, y al final `python -c "import torch, transformers, accelerate"` para confirmar. Si falla, se apunta en el resumen con la pista de Python 3.12/3.13 |
| `requirements-ia.txt` | Ahora incluye `torch>=2.0` con la nota del índice CPU |
| Motor elegido automáticamente | `ensure_ia_defaults()` comprueba que el motor configurado **funcione de verdad** y, si no, pasa al otro motor gratuito disponible (Ollama si está encendido). Ya no se queda en uno roto |
| `/api/ia/config` | Nuevos campos `listo`, `falta`, `comando`, `local_torch`, `local_transformers` |
| Interfaz | Banda de aviso ámbar sobre «Redactar con IA» con el motivo y el **comando exacto** (copiable), y al pulsar el botón un aviso claro en lugar de una petición condenada a fallar |
| `app.py --check` | Línea nueva **«IA del acta»**: `local (Hugging Face)`, `Ollama · qwen2.5:7b-instruct (2 modelo/s)` o `NO disponible` + aviso con el comando |
| Mensaje de error | Ya no es un `pip install` seco: explica las dos salidas (instalar o usar Ollama) y da la ruta del venv en Windows |

**Verificado:** `--check` dice «IA del acta: NO disponible» + aviso; `/api/ia/config`
devuelve `listo: false` y el comando; en el navegador se ve la banda de aviso y el
botón muestra el toast sin llamar al servidor; `ensure_ia_defaults()` cambia de motor
cuando el elegido no está (probado con dobles). **53 pruebas** en verde (3 nuevas).

### 11. Versión 2.3.0 — el acta se redacta por secciones (ya no es una lista de temas)

**Síntoma:** el acta salía como un volcado de ~60 viñetas de «temas tratados» y un
párrafo suelto de desarrollo; nada parecido a un acta formal.

**Causa (cadena completa, no era la transcripción):**

| Eslabón | Qué pasaba |
|---|---|
| `MAP_INSTRUCTIONS` | Cada tramo de 20 min se comprimía a **14 viñetas** de palabras clave |
| `AI_FORMATS["acta_completa"]["max_tokens"]` | **3 000** tokens: el acta se cortaba |
| Motor local | `_engine_tokens` **ignoraba** `max_tokens` y usaba 1 400 fijos |
| `FORMAT_INSTRUCTIONS` | Pedía «desarrollo por tema» en un solo bloque: nunca pidió secciones, sub-viñetas ni propuestas descartadas |

**Qué cambió:**

| Cambio | Detalle |
|---|---|
| Nuevo flujo por secciones | `_plan_acta_secciones()` pide el índice (4–8 secciones) → `_acta_por_secciones()` redacta **una sección por llamada** con streaming → `_bloque_json_acuerdos()` extrae acuerdos, pendientes y siguientes pasos |
| Instrucciones del acta | Secciones numeradas en **MAYÚSCULAS**, prosa en tercera persona, viñetas con etiqueta en negritas (`* **Constructora X:** … por $950,000 pesos`), propuestas descartadas con su motivo, responsables y plazos, separadores `---` |
| Cierre fijo | Sección **CIERRE** con líneas de firma, añadida por la app (no la inventa el modelo) |
| Sin huecos | `limpiar_acta()` borra `[Nombre Presidencia]`, `[Monto]`, `[Fecha]`, `( )` y párrafos repetidos; **conserva** `[HH:MM:SS]`. También se aplica a acuerdos y pendientes |
| Límites | `max_tokens` del acta **3 000 → 6 000**; `section_tokens` 900; `max_new` local 900/1 400/1 400/1 200 → **1 200/2 200/2 600/2 000**. El motor local ya respeta el límite pedido y, para no eternizarse en CPU, con IA local se piden **5 secciones de 700 tokens** en vez de 8 de 900 |
| Map más rico | 14 → **24 viñetas** por tramo, redactadas como «sujeto + hecho + cifra» en vez de palabras clave |
| Verificación de cifras | Solo avisa de montos, porcentajes y números de 4+ dígitos (nada de años ni de «8 socios») y **acepta equivalencias** `$1,200,000` ↔ «1.2 millones» |
| Interfaz | Progreso por sección («Redactando la sección 2/4: COMISIÓN DE CONSTRUCCIÓN») y el estado final añade «· 4 secciones» |
| Vista formateada | Pestañas **Vista formateada / Editar texto** sobre el acta: renderiza markdown (títulos en mayúsculas, viñetas con negritas, tablas de firmas, `[HH:MM:SS]` en color) sin librerías externas, y es lo que sale al imprimir. Mientras se redacta se ve el texto en vivo |
| Robustez | Si el índice falla → una sola pasada; si una sección falla → se omite y se renumera sin dejar títulos vacíos |

**Verificado:** servidor de demostración con motor simulado → SSE con `stage: map`,
`stage: secciones` (0/4 … 4/4), 1 085 tokens y `done` con 4 secciones; en el navegador
(Chromium vía Playwright) el acta se ve completa con secciones numeradas, la tabla de
acuerdos con 3 filas y el estado «… · 4 secciones», **sin errores de JavaScript**
(capturas `capturas/acta-formateada.png` y `capturas/acta-acuerdos.png`). **65 pruebas** en verde, 12 de ellas nuevas
(`tests/test_acta_formato.py`).

### 12. Versión 2.3.1 — Ollama: el modelo pedido no está descargado

**Síntoma:** con el motor «IA local · Ollama / LM Studio» el acta fallaba con
`El servidor local respondió 404: {"error":{"message":"model 'qwen2.5:7b-instruct' not
found"…}}`. La app mandaba siempre `qwen2.5:7b-instruct` aunque el usuario tuviera otros
modelos, y no decía cómo arreglarlo.

| Cambio | Detalle |
|---|---|
| Modelo comprobado de verdad | `modelos_instalados()` lee `/v1/models` (caché de 20 s) y `ensure_ia_defaults()` cambia el modelo configurado por el mejor disponible si el elegido no está descargado |
| Elección inteligente | `mejor_modelo_instalado()` prefiere modelos `instruct`/`chat` de 3B–14B y descarta `*-embed-*`, `whisper`, `nomic`, etc. |
| Error accionable | El 404 «model not found» se traduce a: *«El modelo «X» no está descargado en tu servidor local (URL). Escribe: ollama pull X … Tienes descargados: … Escribe «Y» en el campo Modelo libre»* |
| Otros errores | 404 de ruta, 401/403 y 400 se explican por separado; un 500 muestra el detalle del servidor |
| `/api/ia/test` | Devuelve `sugerido` y, si el modelo no está descargado, `ok: false` con el comando exacto |
| `/api/ia/config` | Nuevos campos `openai_model_instalado` y `openai_sugerido`; al abrirse se auto-corrige el modelo. En la interfaz, si faltara, se explica y hay un botón para usar el sugerido |
| Aviso en pantalla | El toast ya no muestra «RuntimeError:» delante; solo el texto útil |

**Verificado** con un Ollama simulado (3 modelos, 404 real para el que falta):
`/api/ia/config` cambió `qwen2.5:7b-instruct` → `qwen2.5:3b-instruct` y lo guardó;
con el modelo válido se generó el acta completa (3 secciones + acuerdos) por SSE; y al
forzar el modelo ausente el navegador mostró el mensaje con `ollama pull` y la lista de
modelos instalados (capturas `capturas/ollama-acta.png` y `capturas/ollama-error-modelo.png`),
sin errores de JavaScript. **73 pruebas** en verde, 8 nuevas (`tests/test_ollama_modelo.py`).

### 13. Versión 2.4.0 — el instalador deja Ollama con su modelo listo

**Petición:** «que al darle instalar quede todo listo con Ollama y su modelo», sin pasos
manuales de `ollama pull`.

**Nueva etapa 6/8 del instalador** (`instalar.bat`, que pasa de 7 a 8 etapas):

| Paso | Qué hace |
|---|---|
| Detección | Busca `ollama` en el `PATH` y en `%LOCALAPPDATA%\Programs\Ollama\ollama.exe` |
| Instalación | `winget install --id Ollama.Ollama -e --silent`; si no hay winget o falla, descarga `OllamaSetup.exe` oficial y lo ejecuta en modo asistente |
| Modelo | Menú: **qwen2.5:7b-instruct (4,7 GB, recomendado)**, 3b (1,9 GB), 1.5b (1,0 GB) o *ahora no*; por defecto el 7B |
| Servicio | Comprueba `ollama list`; si no responde, arranca `ollama serve` en segundo plano y espera |
| Descarga | `ollama pull <modelo>` con el progreso de Ollama; si se corta, se puede repetir |
| Configuración | `python app.py --usar-ollama <modelo>` deja `ia_config.json` con `engine: openai`, la URL y el modelo **verificado** contra `/v1/models` |

**Nuevo comando `app.py --usar-ollama [MODELO]`**: configura el motor del acta contra
Ollama, espera hasta 12 s a que el servicio levante, comprueba que el modelo esté
descargado (si no, sugiere uno que sí) y sale con código 0/1 para que el `.bat` sepa si
falló. Sin argumento elige el mejor modelo instalado.

**Además:** `app.py --check` ahora avisa «Ollama · ¡falta el modelo X!» con el modelo
sugerido, y la etapa 5 explica que el paso 6 puede instalar Ollama.

**Verificado:** `--usar-ollama qwen2.5:7b-instruct` con un modelo ausente → `[AVISO]` con
`ollama pull` y la lista de instalados (exit 1); con un modelo presente → `[OK]` y
`ia_config.json` con `engine: openai` (exit 0); `--check` muestra «Ollama · ¡falta el
modelo …!» y su aviso. **77 pruebas** en verde, 4 nuevas para `aplicar_motor_ollama`.

### 14. Versión 2.5.0 — el acta y el historial, utilizables de verdad

**Petición:** «¿qué podemos mejorar tanto en el acta como en el historial?» → se aprobaron
las dos entregas completas (A1–A5, B1–B7, C1–C6), sin dejar nada para después.

#### El acta

| Mejora | Qué cambia para quien la usa |
|---|---|
| **B1 · Plantilla institucional** | Panel *Plantilla del acta*: organización, órgano que sesiona, ciudad, quien preside y secretaría técnica. El encabezado pasa a ser `ACTA DE SESIÓN DE LA JUNTA DIRECTIVA`, y el quórum y las firmas usan esos nombres. Por defecto: **Junta Directiva** (hospital); nunca se inventan nombres. Se aplica al prompt, a la vista formateada y al `.docx`. |
| **B2 · Acta anterior** | Selector «Acta anterior» en el panel de IA: automático (última de la misma serie), una concreta o ninguna. Al prompt solo llegan **sus acuerdos y pendientes**, con la instrucción de dar seguimiento y detectar compromisos repetidos, incumplidos o contradictorios. |
| **B3 · Acuerdos editables** | Responsable, fecha y estatus se editan en la propia celda (`PATCH /api/compromisos/{id}`) y se guardan; regenerar el texto **no borra** esas correcciones. |
| **A1 · Borrado completo** | Borrar una reunión elimina audio, transcripción, sesión y **acta con sus versiones** (antes quedaban actas huérfanas). |
| **A2 · `.docx` real** | El acta se convierte a Word con títulos, viñetas y tablas de verdad (`python-docx`), no un volcado de texto. |
| **A3 · Participantes** | El campo *Participantes* se rellena con los hablantes detectados y renombrados. |
| **A4 · Marcas clicables** | Cada `[HH:MM:SS]` del acta es un botón que salta a ese momento del audio. |
| **A5 · Chips** | El historial muestra de un vistazo acta, número de acuerdos, pendientes, serie y protección. |
| **B4 · Versiones** | `versiones[]` por reunión (motor, modelo, formato, fecha, caracteres) con **Restaurar** desde la barra del acta; las actas antiguas aparecen con su versión vigente. |
| **B5 · Cifras** | Panel de verificación: importes y porcentajes sin respaldo literal en la transcripción, con salto al audio en cada uno. |
| **B6 · Texto oficial** | `texto_oficial` es la fuente única: al reabrir una reunión se muestra el acta guardada, no un resumen viejo de la sesión; el autoguardado (`Guardar` o al salir del campo) crea versión. |
| **B7 · Exportar solo el acta** | `format: acta` → `.docx` únicamente con el acta (sin transcripción), desde la barra del acta o desde el historial. |

#### El historial

| Mejora | Qué cambia |
|---|---|
| **C1 · Buscador y filtros** | Búsqueda sin acentos por título, acta, serie, acuerdos y pendientes; filtros (con acta, con pendientes, sin acta, protegidas) y orden (recientes, antiguas, más acuerdos, duración). |
| **C2 · Panel Compromisos** | Todos los acuerdos y pendientes de todas las reuniones, agrupados y editables, con «marcar cumplido» y salto a la reunión o al minuto. |
| **C3 · Series** | Etiqueta de serie por reunión (por ejemplo «Junta Directiva 2026») y renombrado del título. |
| **C4 · Acciones por entrada** | Abrir, descargar solo el acta `.docx` sin abrir la reunión, **duplicar** como base editable y eliminar diciendo exactamente **qué** se borra. |
| **C5 · Retención transparente** | Aviso de «se borrarán X, Y» al llegar a 60 reuniones u 800 MB y opción **proteger** para que una reunión no se elimine nunca. Las entradas antiguas del historial se normalizan solas (chips, bytes, serie). |
| **C6 · Continuar reunión** | Botón *Continuar reunión*: sube el audio de la segunda parte, se transcribe, se desplazan las marcas de tiempo y se **une el audio**; el acta avisa de que hay que volver a redactarla y las dos partes se redactan en un solo documento. `POST /api/jobs/{id}/continuar`. |

**Ajustes fijos** (petición posterior): el modo de precisión por defecto pasa de
`turbo` a **`ultra`** (large-v3, beam 5) y se rotula «Ultra (por defecto)»; Turbo queda
como «Turbo (rápida)». Además, el modo, el idioma y los interruptores —**Mostrar marcas de
tiempo**, **Modo lectura en párrafos**, **Encadenar texto** y **Modo música**— se guardan
en el navegador (`tae_ajustes`) y se restauran tal como los dejaste, en lugar de volver a
los valores de fábrica en cada visita. El instalador también propone **Ultra** por defecto
en la etapa 7/8 (`/d 5`).

**Corregido: «Nueva reunión» no hacía nada.** Tres causas reales, las tres tapadas:

1. **Caché del navegador.** `/static/app.js` y `/static/style.css` se servían sin
   `Cache-Control`, así que el navegador podía reutilizar un `app.js` anterior (sin la
   función) aunque el HTML fuera nuevo: el botón existía y el clic no hacía nada. Ahora el
   HTML se sirve `no-store`, los estáticos `no-cache, must-revalidate` y las etiquetas usan
   `app.js?v=<versión>` / `style.css?v=<versión>` (la versión la inyecta el servidor).
2. **El manejador se enlazaba al final de `bindEvents()`**: si cualquier línea anterior
   fallaba, el botón quedaba sin listener. Ahora el clic se atiende con **delegación a nivel
   de documento**, fuera de `bindEvents`, y la función es defensiva paso a paso.
3. **`confirm()` bloqueado**: en un iframe con `sandbox` los diálogos se ignoran y
   `confirm()` devuelve `false`, así que el clic se perdía en silencio. Ya no hay diálogo:
   se vuelve al inicio y, si había una transcripción en curso, se avisa con un mensaje.

**Extras de la misma tanda:** `localStorage` pasa por un envoltorio tolerante (en un iframe
en sandbox lanzaba `SecurityError` y rompía toda la interfaz); chip de **versión** en la
barra superior que se pone en rojo si la interfaz y el servidor no coinciden
(«Interfaz vX · servidor vY · recarga con Ctrl+F5»); y un aviso en pantalla si se produce
un error de JavaScript, en vez de quedarse mudo. Prueba nueva: `JS_VERSION` debe coincidir
con `VERSION` y el HTML debe usar `?v=__V__` (101 pruebas en verde).

**Además, botón «Nueva reunión»** (y el paso **1 · Cargar** del flujo, ahora clicable):
vuelve a la página principal sin recargar, con el reproductor detenido, el acta y la
transcripción limpias y la zona de carga lista. Antes no había forma de regresar salvo
recargar el navegador. La reunión queda en el Historial; si se estaba transcribiendo, se
avisa de que sigue en segundo plano.

**Verificado:** `100 passed, 1 skipped` (23 nuevas en `tests/test_acta_historial.py`:
plantilla, edición y versiones, compromisos, retención y protección, borrado completo,
duplicado, acta anterior, fusión de partes, `.docx` real, normalización de entradas
antiguas). En el navegador: historial con chips y buscador, panel de compromisos editable,
plantilla cargando y guardando, `[00:41]` del acta saltando a 41 s del audio, tabla del
acta renderizada y aviso de copia con el reproductor oculto, **sin errores de JavaScript**.
De paso se corrigió que los *toasts* mostraban el nombre del icono (`check-circle`) como
texto y que al reabrir una reunión se veía el resumen viejo de la sesión en lugar del acta
oficial.
