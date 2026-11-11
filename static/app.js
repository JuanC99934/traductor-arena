/* ============================================================================
   TRANSCRIPTOR DE AUDIO Y GENERADOR DE ACTAS · app.js
   Vanilla JS · sin frameworks · ES2020
   ========================================================================== */
(() => {
  'use strict';

  /* ----------------------------- UTILIDADES ----------------------------- */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const esc = (s = '') => String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  const fmtTime = (sec = 0, forceHours = false) => {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const mm = String(m).padStart(2, '0'), ss = String(s).padStart(2, '0');
    return (h || forceHours) ? `${String(h).padStart(2, '0')}:${mm}:${ss}` : `${mm}:${ss}`;
  };
  const fmtDur = (sec = 0) => {
    sec = Math.max(0, Math.round(Number(sec) || 0));
    if (sec >= 3600) return fmtTime(sec, true);
    if (sec >= 60) return `${Math.floor(sec / 60)} min ${sec % 60}s`;
    return `${sec} s`;
  };
  const fmtNum = (n = 0) => Number(n || 0).toLocaleString('es-MX');
  const fmtBytes = (b = 0) => {
    b = Number(b) || 0;
    if (b >= 1073741824) return (b / 1073741824).toFixed(2) + ' GB';
    if (b >= 1048576) return (b / 1048576).toFixed(1) + ' MB';
    if (b >= 1024) return (b / 1024).toFixed(0) + ' KB';
    return b + ' B';
  };
  const debounce = (fn, ms = 400) => {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  };
  const initials = (name = '') => {
    const clean = String(name).replace(/^(Hablante|Dr\.?|Lic\.?|Ing\.?|C\.?P\.?)\s+/i, '').trim();
    const parts = clean.split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  };
  /* Paleta categórica de hablantes: tokens CSS (--sp-1…--sp-10) para que el
     tema claro y el oscuro compartan el mismo código con contrastes correctos. */
  const SPEAKER_COLORS = ['--spk-1', '--spk-2', '--spk-3', '--spk-4', '--spk-5',
                          '--spk-6', '--spk-7', '--spk-8', '--spk-9', '--spk-10'];
  const colorFor = (i) => `var(${SPEAKER_COLORS[i % SPEAKER_COLORS.length]})`;

  /* El canvas no entiende var(): resolvemos a hex/rgb con caché por tema. */
  let _colorCache = {};
  let _colorTheme = null;
  function resolveColor(cssColor) {
    const theme = document.documentElement.getAttribute('data-theme') || 'dark';
    if (theme !== _colorTheme) { _colorCache = {}; _colorTheme = theme; }
    const key = String(cssColor);
    if (!key.startsWith('var(')) return key;
    if (_colorCache[key]) return _colorCache[key];
    const name = key.slice(4, -1).trim();
    const val = getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#8b93a8';
    _colorCache[key] = val;
    return val;
  }
  /* Iconos SVG del sprite inline (index.html) — nunca emoji sueltos. */
  const icon = (name, cls = 'icon') =>
    `<svg class="${cls}" aria-hidden="true" focusable="false"><use href="#i-${name}"></use></svg>`;
  const setIcon = (el, name, cls = 'icon') => { if (el) el.innerHTML = icon(name, cls); };

  /* Almacenamiento tolerante: en un iframe con sandbox o con cookies bloqueadas,
     `localStorage` lanza una excepción; la app debe seguir funcionando igual. */
  /* Versión de ESTE archivo (debe coincidir con VERSION de app.py). Sirve para
     detectar en pantalla que el navegador está usando un app.js viejo en caché. */
  const JS_VERSION = '2.5.0';

  const store = {
    _mem: {},
    get(clave, porDefecto = null) {
      try {
        const v = localStorage.getItem(clave);
        return v === null ? (clave in this._mem ? this._mem[clave] : porDefecto) : v;
      } catch (_) {
        return clave in this._mem ? this._mem[clave] : porDefecto;
      }
    },
    set(clave, valor) {
      this._mem[clave] = String(valor);
      try { localStorage.setItem(clave, String(valor)); } catch (_) { /* sin permiso */ }
    },
    del(clave) {
      delete this._mem[clave];
      try { localStorage.removeItem(clave); } catch (_) { /* sin permiso */ }
    },
    json(clave, porDefecto = null) {
      try { return JSON.parse(this.get(clave) || 'null') ?? porDefecto; } catch (_) { return porDefecto; }
    },
  };

  const uid = () => (crypto.randomUUID ? crypto.randomUUID() : 'sid-' + Date.now() + '-' + Math.random().toString(16).slice(2));

  /** Minúsculas y sin acentos: búsquedas tolerantes a tildes y ñ. */
  const fold = (str = '') => String(str)
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();

  /** Anuncia un mensaje a lectores de pantalla sin mover el foco. */
  function announce(message, assertive = false) {
    const el = $('#srAnnounce');
    if (!el) return;
    el.setAttribute('aria-live', assertive ? 'assertive' : 'polite');
    el.textContent = '';
    setTimeout(() => { el.textContent = message; }, 40);
  }

  /** Guarda una instantánea para poder deshacer. */
  function pushUndo(label = '') {
    try {
      S.undoStack.push({
        label,
        segments: S.segments.map(x => ({ ...x })),
        speakers: S.speakers.map(x => ({ ...x })),
      });
      if (S.undoStack.length > 30) S.undoStack.shift();
      const btn = $('#btnUndo');
      if (btn) { btn.disabled = false; btn.title = `Deshacer${label ? ': ' + label : ''} (Ctrl+Z)`; }
      const note = $('#editsNote');
      if (note) note.hidden = false;
    } catch (_) { /* sin memoria disponible */ }
  }

  function undoLast() {
    const snap = S.undoStack.pop();
    if (!snap) { toast('No hay cambios que deshacer.', 'info'); return; }
    S.segments = snap.segments;
    S.speakers = snap.speakers;
    renderSpeakers();
    renderTranscript(true);
    renderStats();
    renderMergeSelects();
    const btn = $('#btnUndo');
    if (btn) btn.disabled = !S.undoStack.length;
    saveSession({ labels: Object.fromEntries(S.speakers.map(sp => [sp.original || sp.name, sp.name])) });
    toast(`Deshecho${snap.label ? ': ' + snap.label : ''}.`, 'ok');
  }

  function restoreOriginal() {
    if (!S.originalSegments) { toast('No hay copia original de esta transcripción.', 'info'); return; }
    if (!confirm('¿Volver a la transcripción original? Se perderán los cambios de texto y de hablantes.')) return;
    pushUndo('restaurar original');
    S.segments = S.originalSegments.map(x => ({ ...x }));
    S.speakers = (S.originalSpeakers || []).map(x => ({ ...x }));
    // Los ids de hablante se reconstruyen por nombre original
    const byName = new Map(S.speakers.map((sp, i) => [sp.name, i]));
    S.segments.forEach(seg => { seg.si = byName.has(seg.speaker) ? byName.get(seg.speaker) : -1; });
    renderSpeakers();
    renderTranscript(true);
    renderStats();
    renderMergeSelects();
    toast('Transcripción original restaurada.', 'ok');
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {},
      ...opts,
    });
    if (!res.ok) {
      let detail = `Error ${res.status}`;
      try {
        const data = await res.json();
        detail = data.detail || data.error || detail;
      } catch (_) { /* respuesta sin JSON */ }
      throw new Error(detail);
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  }

  /* ------------------------------- ESTADO ------------------------------- */
  const S = {
    sessionId: store.get('tae_session') || uid(),
    health: null,
    iaConfig: null,
    upload: null,          // { file_id, filename, duration, size, size_mb }
    uploadXhr: null,
    jobId: store.get('tae_last_job') || '',
    job: null,
    pollTimer: null,
    pollingStatus: '',
    segments: [],
    speakers: [],
    info: {},
    stats: {},
    mediaUrl: '',
    title: '',
    lowThreshold: -0.6,
    lowPointer: -1,
    editing: false,
    activeId: -1,
    search: { query: '', matches: [], index: -1 },
    audio: null,
    recorder: null,
    summaryTimer: null,
    // --- v2 ---
    renderLimit: 400,        // fragmentos dibujados (carga progresiva)
    undoStack: [],           // instantáneas para deshacer
    originalSegments: null,  // transcripción tal como la devolvió el motor
    originalSpeakers: null,
    edited: false,
    follow: true,            // desplazar la transcripción con el audio
    selecting: false,        // modo selección para separar hablantes
    selection: new Set(),
    wave: null,              // { peaks, duration }
    chapters: [],            // [{ start, label }]
    globalHits: [],
    streamAbort: null,       // AbortController del acta en streaming
    partialSince: 0,         // cursor de los resultados parciales
    partialCount: 0,
  };
  store.set('tae_session', S.sessionId);

  async function loadKnownVoices() {
    try {
      const data = await api('/api/voces');
      $('#knownVoices').innerHTML = (data.speakers || [])
        .map(sp => `<option value="${esc(sp.name)}"></option>`).join('');
    } catch (_) { /* opcional */ }
  }

  /* ------------------------------- TOASTS ------------------------------- */
  function toast(message, type = 'info', title = '') {
    const box = $('#toasts');
    const icons = { info: 'info', ok: 'check-circle', err: 'x-circle', warn: 'alert' };
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<span class="t-icon">${icon(icons[type] || icons.info, 'icon icon-sm')}</span>
      <div class="t-body">${title ? `<strong>${esc(title)}</strong>` : ''}<span>${esc(message)}</span></div>`;
    box.appendChild(el);
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 260);
    }, type === 'err' ? 9000 : 4600);
  }

  /* -------------------------------- TEMA -------------------------------- */
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    setIcon($('#themeIcon'), theme === 'light' ? 'sun' : 'moon');
    $('#themeLabel').textContent = theme === 'light' ? 'Claro' : 'Oscuro';
    store.set('tae_theme', theme);
  }
  const toggleTheme = () => applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');

  /* ------------------------- ESTADO DEL SISTEMA ------------------------- */
  /** Alto real de la cabecera (para que el panel izquierdo no se salga). */
  let headH = 0;
  function syncHeadHeight() {
    const head = document.querySelector('.topbar') || document.querySelector('header');
    const h = head ? Math.round(head.getBoundingClientRect().height) : 0;
    if (h > 0 && h !== headH) {
      headH = h;
      document.documentElement.style.setProperty('--head-h', h + 'px');
    }
  }
  window.addEventListener('resize', syncHeadHeight);
  window.addEventListener('load', syncHeadHeight);
  syncHeadHeight();
  // La cabecera cambia de alto cuando llegan los chips de estado: se vigila.
  if (window.ResizeObserver) {
    const head = document.querySelector('.topbar');
    if (head) new ResizeObserver(() => syncHeadHeight()).observe(head);
  }

  async function loadHealth() {
    try {
      const h = await api('/api/health');
      S.health = h;
      renderHealthChips(h);
      renderModes(h);
      renderLanguages(h);
      cargarAjustes();
      await loadIaConfig();
      await loadAudioTool();
    } catch (err) {
      renderHealthChips(null);
      toast('No se pudo obtener el estado del servidor: ' + err.message, 'err', 'Servidor');
    }
  }

  function renderHealthChips(h) {
    const box = $('#healthChips');
    if (!h) { box.innerHTML = `<span class="chip chip-bad"><span class="dot"></span>Sin conexión con el backend</span>`; return; }
    const hw = h.hardware || {};
    const chips = [
      h.ffmpeg_ok
        ? `<span class="chip chip-ok" title="${esc(h.ffmpeg || '')}"><span class="dot"></span>FFmpeg listo</span>`
        : `<span class="chip chip-bad" title="Instala FFmpeg: winget install Gyan.FFmpeg"><span class="dot"></span>FFmpeg no encontrado</span>`,
      hw.cuda
        ? `<span class="chip chip-ok" title="${esc((hw.gpu_names || []).join(', '))}"><span class="dot"></span>GPU CUDA</span>`
        : `<span class="chip chip-warn" title="Se usa int8 con ${hw.cpu_threads} hilos"><span class="dot"></span>CPU int8 · ${hw.cpu_threads} hilos</span>`,
      h.offline
        ? `<span class="chip chip-ok" title="Los modelos ya están en ./modelos"><span class="dot"></span>Offline</span>`
        : `<span class="chip chip-muted" title="Se descargarán modelos en ./modelos"><span class="dot"></span>Modelos por descargar</span>`,
      h.diarization
        ? `<span class="chip chip-ok"><span class="dot"></span>Diarización</span>`
        : `<span class="chip chip-muted" title="pip install resemblyzer"><span class="dot"></span>Sin diarización</span>`,
      `<span class="chip chip-muted" title="Trabajos en cola">Cola: ${(h.queue && (h.queue.pending + h.queue.active)) || 0}</span>`,
    ];
    const dl = h.download;
    if (dl && dl.activo) {
      const pct = dl.total ? ` ${dl.pct} %` : '';
      chips.push(
        `<span class="chip chip-warn" title="${esc(dl.texto || '')}">` +
        `<span class="dot"></span>Descargando modelo${pct}</span>`);
    }
    if (h.version && h.version !== JS_VERSION) {
      chips.push(`<span class="chip chip-bad" title="El navegador tiene una versión anterior de la interfaz. Recarga con Ctrl+F5 (Cmd+Shift+R en Mac).">` +
        `<span class="dot"></span>Interfaz v${esc(JS_VERSION)} · servidor v${esc(h.version)} · recarga con Ctrl+F5</span>`);
    } else {
      chips.push(`<span class="chip chip-muted" title="Versión de la aplicación">v${esc(h.version || JS_VERSION)}</span>`);
    }
    box.innerHTML = chips.join('');
  }

  /* Ajustes de transcripción «fijos»: se recuerdan entre sesiones. */
  const PREFS_KEY = 'tae_ajustes';
  const PREFS_CAMPOS = {
    modeSelect: 'value', languageSelect: 'value',
    optTimestamps: 'checked', optParagraphs: 'checked',
    optChain: 'checked', optMusic: 'checked',
  };

  function guardarAjustes() {
    const datos = {};
    Object.entries(PREFS_CAMPOS).forEach(([id, prop]) => {
      const el = $('#' + id);
      if (el) datos[id] = el[prop];
    });
    store.set(PREFS_KEY, JSON.stringify(datos));
  }

  function cargarAjustes() {
    let datos = null;
    datos = store.json(PREFS_KEY, null);
    if (!datos) return;
    Object.entries(PREFS_CAMPOS).forEach(([id, prop]) => {
      const el = $('#' + id);
      if (!el || datos[id] === undefined) return;
      // en los <select>, solo si esa opción existe de verdad
      if (el.tagName === 'SELECT'
        && ![...el.options].some(o => o.value === String(datos[id]))) return;
      el[prop] = datos[id];
    });
    aplicarAjustesDeLectura();
  }

  /** Refleja los interruptores en la transcripción ya cargada. */
  function aplicarAjustesDeLectura() {
    const lista = $('#segmentList');
    if (!lista) return;
    lista.classList.toggle('show-ts', $('#optTimestamps').checked);
    lista.classList.toggle('paragraphs', $('#optParagraphs').checked);
  }

  function renderModes(h) {
    const sel = $('#modeSelect');
    const prev = sel.value;
    sel.innerHTML = (h.models || []).map(m =>
      `<option value="${m.key}">${esc(m.label)}${m.local ? '' : ' · ' + fmtBytes(m.bytes)}</option>`).join('');
    sel.value = prev && [...sel.options].some(o => o.value === prev) ? prev : (h.default_mode || 'turbo');
    updateModeDesc();
  }

  function updateModeDesc() {
    const mode = $('#modeSelect').value;
    const m = (S.health && S.health.models || []).find(x => x.key === mode);
    $('#modeDesc').textContent = m
      ? `${m.desc}${m.local
        ? ' · modelo ya descargado en ./modelos'
        : ` · se descarga la primera vez${m.bytes ? ' (' + fmtBytes(m.bytes) + ')' : ''}, solo una vez`}`
      : '—';
    updateStartButton();
  }

  function renderLanguages(h) {
    const sel = $('#languageSelect');
    sel.innerHTML = (h.languages || [{ code: '', label: 'Detección automática' }])
      .map(l => `<option value="${esc(l.code)}">${esc(l.label)}</option>`).join('');
  }

  /* ------------- modelo de audio local (Ollama · lfm3-audio) ------------- */
  async function loadAudioTool() {
    try {
      const d = await api('/api/ia/audio');
      S.audio = d;
      const lista = Array.isArray(d.modelos) ? d.modelos : [];
      const sel = $('#audioModelSelect');
      sel.innerHTML = lista.length
        ? lista.map(m => `<option value="${esc(m)}"${m === d.modelo ? ' selected' : ''}>${esc(m)}</option>`).join('')
        : '<option value="">— ninguno detectado —</option>';
      $('#audioToolState').textContent = d.aviso || '';
      $('#audioToolState').classList.toggle('muted', !lista.length);
      $('#btnAudioListen').disabled = !lista.length;
      if (d.nota) $('#audioToolState').title = d.nota;
    } catch (_) {
      $('#audioToolState').textContent = 'No se pudo consultar Ollama (es opcional).';
      $('#btnAudioListen').disabled = true;
    }
  }

  async function escucharAudio() {
    const btn = $('#btnAudioListen');
    const estado = $('#audioStatus');
    const fuente = (S.upload && S.upload.file_id)
      ? { file_id: S.upload.file_id }
      : (S.job && S.job.id ? { job_id: S.job.id } : null);
    if (!fuente) {
      toast('Primero sube un archivo o abre una reunión del historial.', 'warn');
      return;
    }
    btn.disabled = true;
    estado.textContent = 'Escuchando… (puede tardar en CPU)';
    $('#audioResult').value = '';
    try {
      const r = await api('/api/ia/audio/escuchar', {
        method: 'POST',
        body: JSON.stringify({
          ...fuente,
          segundos: Number($('#audioSeconds').value) || 30,
          pregunta: $('#audioQuestion').value.trim(),
          modelo: $('#audioModelSelect').value || '',
        }),
      });
      $('#audioResult').value = r.texto || '(el modelo no devolvió texto)';
      estado.textContent = `${r.modelo} · ${r.segundos} s de audio · ${(r.ms / 1000).toFixed(1)} s de proceso`;
    } catch (err) {
      estado.textContent = '';
      toast(err.message || 'No se pudo escuchar el audio.', 'err', 'Modelo de audio');
    } finally {
      btn.disabled = false;
    }
  }

  async function loadIaConfig() {
    try {
      const cfg = await api('/api/ia/config');
      S.iaConfig = cfg;

      // Motores: primero los gratuitos
      const order = ['local', 'openai', 'gemini'];
      const engines = (cfg.engines || []).slice().sort(
        (a, b) => order.indexOf(a.key) - order.indexOf(b.key));
      $('#engineSelect').innerHTML = engines.map(e =>
        `<option value="${esc(e.key)}">${esc(e.label)}${e.free ? '' : ' · requiere API Key'}</option>`
      ).join('');
      $('#engineSelect').value = cfg.engine || 'local';

      $('#localModelSelect').innerHTML = Object.entries(cfg.models || {}).map(([id, m]) =>
        `<option value="${esc(id)}">${esc(m.label)}</option>`).join('');
      $('#localModelSelect').value = cfg.local_model || 'Qwen/Qwen2.5-1.5B-Instruct';

      $('#openaiBaseUrl').value = cfg.openai_base_url || 'http://127.0.0.1:11434/v1';
      $('#openaiModel').value = cfg.openai_model || 'qwen2.5:7b-instruct';
      const suggested = (cfg.openai_models && cfg.openai_models.length)
        ? cfg.openai_models : (cfg.openai_suggested || []);
      $('#openaiModels').innerHTML = suggested
        .map(m => `<option value="${esc(m)}"></option>`).join('');
      const modelosOllama = (cfg.openai_models || []);
      if (cfg.openai_online && cfg.openai_model_instalado === false) {
        const sugerido = cfg.openai_sugerido || (modelosOllama[0] || '');
        $('#openaiHelp').innerHTML = `Ollama responde, pero el modelo
          <code>${esc(cfg.openai_model || '')}</code> no está descargado. Tienes:
          ${modelosOllama.map(m => `<code>${esc(m)}</code>`).join(', ') || '—'}.
          Descárgalo con <code>ollama pull ${esc(cfg.openai_model || 'qwen2.5:7b-instruct')}</code>`
          + (sugerido ? ` o usa <button type="button" class="link-btn" id="btnUsarSugerido">${esc(sugerido)}</button>.` : '.');
      } else {
        $('#openaiHelp').innerHTML = cfg.openai_online
          ? `Servidor detectado con ${modelosOllama.length} modelo(s).`
          : `No se detectó un servidor local. Instala <b>Ollama</b> (gratis) y ejecuta
             <code>ollama pull qwen2.5:7b-instruct</code>.`;
      }
      const btnSug = $('#btnUsarSugerido');
      if (btnSug) {
        btnSug.addEventListener('click', () => {
          $('#openaiModel').value = btnSug.textContent.trim();
          toast('Modelo escrito en el campo; pulsa «Guardar configuración» o «Redactar con IA».', 'ok', 'Motor de IA');
        });
      }

      if (cfg.has_key && !$('#geminiKey').value) {
        $('#geminiKey').placeholder = `Guardada: ${cfg.key_masked}`;
      }
      toggleEngineFields();
    } catch (_) { /* silencioso */ }
  }

  function engineInfo(key) {
    return ((S.iaConfig && S.iaConfig.engines) || []).find(e => e.key === key) || {};
  }

  function toggleEngineFields() {
    const engine = $('#engineSelect').value;
    $('#geminiKeyField').hidden = engine !== 'gemini';
    $('#localModelField').hidden = engine !== 'local';
    $('#openaiFields').hidden = engine !== 'openai';
    const info = engineInfo(engine);
    const cfg = S.iaConfig || {};
    let hint = info.desc || '';
    const ready = info.ready;
    if (engine === 'local') {
      const meta = (cfg.models || {})[$('#localModelSelect').value] || {};
      hint = ready
        ? `${meta.desc || ''} Licencia ${meta.license || 'libre'}.`
        : 'Falta instalar la IA local (torch, transformers, accelerate).';
    } else if (engine === 'openai') {
      hint = cfg.openai_online
        ? 'Servidor local conectado. Sin costo y sin enviar datos a internet.'
        : 'Abre Ollama/LM Studio o cambia al motor de Hugging Face. Ambos son gratuitos.';
    } else {
      hint = cfg.has_key
        ? 'Gemini configurado. La capa gratuita tiene límite de peticiones por minuto/día.'
        : 'Requiere API Key. Si no quieres pagar, usa cualquiera de los motores locales.';
    }
    $('#engineHelp').textContent = hint;
    renderIaAviso(engine, info, cfg);
    const free = info.free !== false;
    $('#iaHint').textContent = free ? 'IA gratuita' : (cfg.has_key ? 'Gemini listo' : 'API Key pendiente');
    const status = $('#summaryStatus');
    if (!status.dataset.busy) status.textContent = (engine === 'gemini' && !cfg.has_key) ? 'Falta la API Key de Gemini' : '';
  }

  /** Aviso visible cuando el motor elegido todavía no se puede usar. */
  function renderIaAviso(engine, info, cfg) {
    const caja = $('#iaAviso');
    if (!caja) return;
    const comando = (cfg && cfg.comando) || 'pip install torch transformers accelerate';
    let texto = '';
    if (info && info.ready === false) {
      if (engine === 'local') {
        texto = 'Falta la IA local de Hugging Face. Ejecuta <code>instalar.bat</code> y '
              + 'elige la <strong>opción 1</strong> en la etapa «IA para redactar el acta», '
              + 'o pega en una consola: '
              + '<code>' + esc(comando) + '</code>. También puedes usar Ollama (gratis) y '
              + 'elegir el motor «Ollama / LM Studio».';
      } else if (engine === 'openai') {
        texto = 'No se detectó Ollama ni LM Studio en el servidor configurado. Ábrelo (o '
              + 'instálalo desde ollama.com) y ejecuta <code>ollama pull qwen2.5:7b-instruct</code>. '
              + 'Mientras tanto puedes usar la IA local de Hugging Face.';
      } else {
        texto = 'Falta la API Key de Google Gemini: pégala en el campo de abajo para poder redactar.';
      }
    }
    caja.innerHTML = texto;
    caja.hidden = !texto;
  }

  /* ------------------------------- SUBIDA ------------------------------- */
  function bindDropzone() {
    const dz = $('#dropZone'), input = $('#fileInput');

    const openPicker = () => input.click();
    dz.addEventListener('click', (e) => { if (e.target.tagName !== 'BUTTON') openPicker(); });
    dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPicker(); } });
    $('#btnPickFile').addEventListener('click', (e) => { e.stopPropagation(); openPicker(); });
    input.addEventListener('change', () => { if (input.files[0]) uploadFile(input.files[0]); input.value = ''; });

    ['dragenter', 'dragover'].forEach(ev => dz.addEventListener(ev, (e) => {
      e.preventDefault(); e.stopPropagation(); dz.classList.add('dragover');
    }));
    ['dragleave', 'drop'].forEach(ev => dz.addEventListener(ev, (e) => {
      e.preventDefault(); e.stopPropagation();
      if (ev === 'dragleave' && dz.contains(e.relatedTarget)) return;
      dz.classList.remove('dragover');
    }));
    dz.addEventListener('drop', (e) => {
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) uploadFile(f);
    });
    window.addEventListener('dragover', (e) => e.preventDefault());
    window.addEventListener('drop', (e) => e.preventDefault());
  }

  const CHUNK_SIZE = 4 * 1024 * 1024;   // 4 MB por trozo
  const uploadKey = (file) => `tae_up_${file.name}_${file.size}_${file.lastModified}`;

  function sendChunk(file, uploadId, offset, onProgress) {
    return new Promise((resolve, reject) => {
      const end = Math.min(file.size, offset + CHUNK_SIZE);
      const fd = new FormData();
      fd.append('chunk', file.slice(offset, end), 'chunk');
      fd.append('upload_id', uploadId);
      fd.append('offset', String(offset));
      fd.append('total_bytes', String(file.size));
      fd.append('filename', file.name);
      fd.append('session_id', S.sessionId);
      fd.append('chunk_size', String(CHUNK_SIZE));

      const xhr = new XMLHttpRequest();
      S.uploadXhr = xhr;
      S.uploadAbort = { abort: () => xhr.abort() };
      xhr.open('POST', '/api/upload/chunk');
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded); };
      xhr.onload = () => {
        S.uploadXhr = null;
        if (xhr.status >= 200 && xhr.status < 300) {
          let data = {};
          try { data = JSON.parse(xhr.responseText); } catch (_) { /* noop */ }
          resolve(data);
        } else {
          let msg = `Error ${xhr.status}`;
          try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (_) { /* noop */ }
          reject(new Error(msg));
        }
      };
      xhr.onerror = () => { S.uploadXhr = null; reject(new Error('Se perdió la conexión durante la subida.')); };
      xhr.onabort = () => {
        S.uploadXhr = null;
        const err = new Error('Subida cancelada.');
        err.aborted = true;
        reject(err);
      };
      xhr.send(fd);
    });
  }

  /**
   * Subida por trozos de 4 MB con reanudación: si se corta la red o se cierra
   * el navegador, al elegir el mismo archivo continúa donde se quedó.
   */
  async function uploadFile(file) {
    if (!S.health || !S.health.ffmpeg_ok) {
      toast('FFmpeg no está instalado. Ejecuta: winget install Gyan.FFmpeg y reinicia la app.',
        'err', 'Falta FFmpeg');
    }
    const MAX = (S.health && S.health.max_upload_mb || 2200) * 1024 * 1024;
    if (file.size > MAX) {
      toast(`El archivo pesa ${fmtBytes(file.size)} y el límite es ${fmtBytes(MAX)}.`, 'err', 'Archivo muy grande');
      return;
    }
    if (S.uploadXhr) { try { S.uploadXhr.abort(); } catch (_) { /* noop */ } }

    const wrap = $('#uploadProgressWrap');
    wrap.hidden = false;
    $('#uploadName').textContent = file.name;
    $('#uploadPct').textContent = '0 %';
    $('#uploadBar').style.width = '0%';
    $('#uploadInfo').textContent = `${fmtBytes(file.size)} · preparando envío por trozos…`;

    const key = uploadKey(file);
    let uploadId = '';
    let offset = 0;

    // --- ¿había una subida a medias de este mismo archivo? -----------------
    try {
      const saved = store.json(key, null);
      if (saved && saved.uploadId) {
        const st = await api(`/api/upload/status/${saved.uploadId}`).catch(() => null);
        if (st && !st.complete && st.received > 0 && (!st.total || st.total === file.size)) {
          uploadId = saved.uploadId;
          offset = Math.floor(st.received / CHUNK_SIZE) * CHUNK_SIZE;
          toast(`Reanudando la subida en ${fmtBytes(offset)} de ${fmtBytes(file.size)}.`, 'info', 'Subida reanudada');
        }
      }
    } catch (_) { /* sin subida previa */ }
    if (!uploadId) uploadId = uid().replace(/-/g, '').slice(0, 32);
    store.set(key, JSON.stringify({ uploadId, name: file.name, size: file.size }));

    const paint = (sent) => {
      const pct = Math.min(100, Math.round((sent / file.size) * 100));
      $('#uploadBar').style.width = pct + '%';
      $('#uploadPct').textContent = pct + ' %';
      $('#uploadInfo').textContent = `${fmtBytes(sent)} de ${fmtBytes(file.size)} · ${pct} %`
        + (sent < file.size ? ' · reanudable' : ' · procesando');
    };
    paint(offset);

    let chunkOffset = offset;
    while (chunkOffset < file.size) {
      let attempt = 0;
      // 3 intentos por trozo antes de rendirse (red inestable)
      for (;;) {
        try {
          const res = await sendChunk(file, uploadId, chunkOffset, (loaded) => paint(chunkOffset + loaded));
          paint(res.received ?? (chunkOffset + CHUNK_SIZE));
          break;
        } catch (err) {
          if (err.aborted) {
            wrap.hidden = true;
            toast('Subida pausada: elige el mismo archivo para reanudarla.', 'warn', 'Subida cancelada');
            updateStartButton();
            return;
          }
          attempt += 1;
          if (attempt >= 3) {
            wrap.hidden = true;
            toast(`${err.message} Elige el mismo archivo para reanudar donde se quedó.`, 'err', 'Subida interrumpida');
            updateStartButton();
            return;
          }
          $('#uploadInfo').textContent = `Reintentando trozo (${attempt}/3)…`;
          await new Promise(r => setTimeout(r, 900 * attempt));
        }
      }
      chunkOffset += CHUNK_SIZE;
    }

    try {
      const meta = await api('/api/upload/complete', {
        method: 'POST',
        body: JSON.stringify({ upload_id: uploadId, filename: file.name, session_id: S.sessionId }),
      });
      store.del(key);
      S.upload = meta;
      wrap.hidden = true;
      $('#uploadBar').style.width = '100%';
      showSelectedFile(meta);
      toast(`${meta.filename} · ${meta.duration_human || fmtTime(meta.duration)}`, 'ok', 'Audio cargado');
      announce(`Audio ${meta.filename} cargado y listo para transcribir`);
      if (meta.warnings && meta.warnings.length) toast(meta.warnings.join(' '), 'warn', 'Aviso');
    } catch (err) {
      wrap.hidden = true;
      toast(err.message, 'err', 'No se pudo cerrar la subida');
    } finally {
      updateStartButton();
    }
  }

  function showSelectedFile(meta) {
    $('#selectedFile').hidden = false;
    $('#fileName').textContent = meta.filename || 'archivo';
    $('#fileName').title = meta.filename || '';
    $('#fileDetails').textContent =
      `${meta.size_mb ? meta.size_mb + ' MB' : fmtBytes(meta.size)} · ${meta.duration_human || fmtTime(meta.duration)} · listo para transcribir`;
    $('#dropZone').classList.add('disabled');
  }

  function clearFile() {
    S.upload = null;
    $('#selectedFile').hidden = true;
    $('#dropZone').classList.remove('disabled');
    $('#fileInput').value = '';
    updateStartButton();
  }

  function updateStartButton() {
    const btn = $('#btnStart');
    const busy = S.job && ['queued', 'preprocessing', 'transcribing', 'diarizing'].includes(S.job.status);
    btn.disabled = !S.upload || !S.upload.file_id || !!busy;
    btn.innerHTML = busy ? icon('loader', 'icon spin') + ' Procesando…'
                        : icon('rocket') + ' Iniciar transcripción';
    const results = $('#results');
    if (busy) setStep(2);
    else setStep(results && !results.hidden ? 3 : 1);
  }

  /* --------------------------- CREAR TRABAJO ---------------------------- */
  async function startJob() {
    if (!S.upload || !S.upload.file_id) { toast('Primero carga un archivo de audio o graba en vivo.', 'warn'); return; }
    setStep(2);
    const payload = {
      file_id: S.upload.file_id,
      session_id: S.sessionId,
      title: (S.upload.filename || '').replace(/\.[^.]+$/, ''),
      mode: $('#modeSelect').value,
      language: $('#languageSelect').value,
      timestamps: $('#optTimestamps').checked,
      paragraphs: $('#optParagraphs').checked,
      hotwords: $('#optHotwords').value.trim(),
      context: $('#optContext').value.trim(),
      ffmpeg_filter: $('#optFfmpeg').value.trim(),
      chain_text: $('#optChain').checked,
      music_mode: $('#optMusic').checked,
    };
    try {
      const res = await api('/api/jobs', { method: 'POST', body: JSON.stringify(payload) });
      S.jobId = res.job_id;
      store.set('tae_last_job', S.jobId);
      S.job = res.job;
      showJobPanel();
      updateJobPanel(res.job);
      startPolling();
      toast('Trabajo en cola. La transcripción comenzará en breve.', 'ok', 'Procesando');
    } catch (err) {
      toast(err.message, 'err', 'No se pudo iniciar');
    }
    updateStartButton();
  }

  function showJobPanel() {
    $('#jobPanel').hidden = false;
    if (typeof resetLive === 'function') resetLive();
    $('#btnCancelAll').hidden = false;
    $('#jobLog').hidden = true;
  }

  function updateJobPanel(job) {
    if (!job) return;
    S.job = job;
    const statusMap = {
      queued: ['En cola', 'warn'], preprocessing: ['Limpiando audio', ''], transcribing: ['Transcribiendo', ''],
      diarizing: ['Diarizando', ''], done: ['Completado', 'ok'], error: ['Error', 'err'],
      cancelled: ['Cancelado', 'err'],
    };
    const [label, cls] = statusMap[job.status] || ['—', ''];
    const badge = $('#jobStatus');
    badge.textContent = label;
    badge.className = 'badge ' + cls;

    const pct = Math.max(0, Math.min(100, Math.round(job.progress || 0)));
    $('#progressBar').style.width = pct + '%';
    $('#progressPct').textContent = pct + ' %';
    $('#jobMessage').textContent = job.message || '…';
    $('#jobMessage').title = job.message || '';
    $('#equalizer').classList.toggle('paused', job.status === 'done' || job.status === 'error' || job.status === 'cancelled');
    $('#btnCancelJob').disabled = ['done', 'error', 'cancelled'].includes(job.status);
    $('#progressBar').classList.toggle('shimmer', ['queued', 'preprocessing', 'transcribing', 'diarizing'].includes(job.status));
    $('#jobLog').textContent = (job.log || []).map(l => `${(l.t || '').slice(11, 19)}  ${l.m}`).join('\n');
    if (!$('#jobLog').hidden) $('#jobLog').scrollTop = $('#jobLog').scrollHeight;

    const q = S.health && S.health.queue;
    $('#queueInfo').textContent = q && (q.pending || q.active) ? `Cola: ${q.pending} pendientes · ${q.active} en proceso` : '';
    updateStartButton();
  }

  function startPolling() {
    stopPolling();
    S.pollTimer = setInterval(pollJob, 1300);
    pollJob();
  }
  function stopPolling() {
    if (S.pollTimer) clearInterval(S.pollTimer);
    S.pollTimer = null;
  }

  async function pollJob() {
    if (!S.jobId) return;
    try {
      const job = await api(`/api/jobs/${S.jobId}`);
      const prevStatus = S.pollingStatus;
      S.pollingStatus = job.status;
      updateJobPanel(job);
      if (job.status === 'done' && prevStatus !== 'done') {
        stopPolling();
        await loadResult(S.jobId);
        toast('Transcripción lista. Revisa los hablantes y redacta el acta.', 'ok', 'Terminado');
      } else if (job.status === 'error') {
        stopPolling();
        toast(job.error || job.message || 'Error desconocido', 'err', 'Falló el procesamiento');
        refreshHealthSoon();
      } else if (job.status === 'cancelled') {
        stopPolling();
        toast('El trabajo fue cancelado.', 'warn');
      } else if (['transcribing', 'diarizing'].includes(job.status)) {
        pollPartial();
      }
    } catch (err) {
      // 404 tras reinicio o limpieza del historial
      if (/no encontrado/i.test(err.message)) { stopPolling(); return; }
    }
  }

  const refreshHealthSoon = debounce(() => loadHealth(), 1500);

  /* ---------------------------- CARGAR RESULTADO ------------------------- */
  async function loadResult(jobId) {
    try {
      const data = await api(`/api/jobs/${jobId}/result`);
      S.jobId = jobId;
      store.set('tae_last_job', jobId);
      S.job = data.job;
      S.segments = data.segments || [];
      S.speakers = (data.speakers || []).map((sp, i) => ({
        ...sp, original: sp.name, color: i, name: sp.name,
      }));
      // Índice estable de hablante por segmento (para renombrados masivos)
      const byName = new Map(S.speakers.map((sp, i) => [sp.name, i]));
      S.segments.forEach(seg => { seg.si = byName.has(seg.speaker) ? byName.get(seg.speaker) : -1; });
      const saved = (data.session && data.session.labels) || {};
      if (saved && Object.keys(saved).length) {
        S.speakers.forEach(sp => { if (saved[sp.original]) sp.name = saved[sp.original]; });
        const nameByIdx = new Map(S.speakers.map((sp, i) => [i, sp.name]));
        S.segments.forEach(seg => { if (seg.si >= 0 && nameByIdx.has(seg.si)) seg.speaker = nameByIdx.get(seg.si); });
      }
      S.info = data.info || {};
      S.stats = data.stats || {};
      S.mediaUrl = data.media_url || `/api/jobs/${jobId}/media`;
      S.title = data.title || S.job.title || 'Reunión';
      S.lowThreshold = data.low_confidence_threshold ?? -0.6;
      S.lowPointer = -1;

      $('#emptyState').hidden = true;
      $('#results').hidden = false;
      setStep(3);
      const tieneAudio = !!(data.job && data.job.has_media) && !(data.job && data.job.duplicado);
      $('#playerBar').hidden = !tieneAudio;
      if (data.job && data.job.restored) {
        $('#jobPanel').hidden = true;
        $('#btnCancelAll').hidden = true;
      } else {
        showJobPanel();
        updateJobPanel(data.job);
      }

      // copia original + estado de la capa de correcciones
      S.originalSegments = S.segments.map(x => ({ ...x }));
      S.originalSpeakers = S.speakers.map(x => ({ ...x }));
      S.undoStack = [];
      S.renderLimit = 400;
      S.selection.clear();
      S.selecting = false;
      const undoBtn = $('#btnUndo');
      if (undoBtn) undoBtn.disabled = true;
      const note = $('#editsNote');
      if (note) note.hidden = !S.edited;
      resetLive();
      renderMergeSelects();
      buildChapters();

      renderResultHeader();
      renderStats();
      renderWarnings(data.warnings || []);
      renderSpeakers();
      renderTranscript();
      renderSummaryFromSession(data.session || {});
      // A3 · los nombres ya detectados/corregidos van al campo Participantes del acta
      const inv = $('#iaParticipants');
      if (inv && !inv.value.trim() && (S.speakers || []).length) {
        inv.value = S.speakers.map(sp => (sp.name || '').trim()).filter(Boolean).join(', ');
      }
      if (tieneAudio) {
        setupAudio(S.mediaUrl);
        drawWaveform();
        loadWaveform();
      }
      loadStoredActa();
      renderNotasReunion(data.job || {});
      cargarActasPrevias();

      aplicarAjustesDeLectura();
      updateSearch('');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      toast(err.message, 'err', 'No se pudo cargar el resultado');
    }
  }

  /* ------------------- NOTAS DE LA REUNIÓN (copia / partes) --------------- */
  function renderNotasReunion(job) {
    const dup = $('#duplicadoNote');
    if (dup) {
      if (job.duplicado) {
        dup.hidden = false;
        dup.innerHTML = '<strong>Copia del acta:</strong> esta entrada no tiene audio ni '
          + 'transcripción. Edita el texto, ajusta los acuerdos y pulsa «Guardar»: el acta '
          + 'original no se modifica.';
      } else {
        dup.hidden = true;
      }
    }
    const partes = $('#partesNote');
    if (partes) {
      const lista = job.partes || [];
      if (lista.length > 1) {
        partes.hidden = false;
        partes.innerHTML = `<strong>Reunión en ${lista.length} partes:</strong> `
          + lista.map(pp => `${esc(String(pp.n))}.ª parte · ${esc(fmtTime(pp.duration || 0))}`
            + (pp.filename ? ` · ${esc(pp.filename)}` : '')).join('  |  ')
          + '. Las marcas de tiempo son continuas: la segunda parte empieza donde termina la primera.';
      } else {
        partes.hidden = true;
      }
    }
    const btn = $('#btnContinuar');
    if (btn) btn.hidden = !!job.duplicado;
  }

  /* ------------- B2 · acta anterior para dar seguimiento a acuerdos -------- */
  async function cargarActasPrevias() {
    const sel = $('#iaActaPrevia');
    if (!sel) return;
    try {
      const data = await api('/api/historial');
      const previas = (data.entries || []).filter(e => e.has_acta && e.job_id !== S.jobId);
      const opciones = previas.slice(0, 40).map(e =>
        `<option value="${esc(e.job_id)}">${esc((e.title || 'Reunión').slice(0, 60))} · `
        + `${esc((e.created || '').slice(0, 10))}</option>`).join('');
      const elegido = sel.value;
      sel.innerHTML = '<option value="">Automático: última reunión de la misma serie</option>'
        + '<option value="ninguna">Sin acta anterior</option>' + opciones;
      if (elegido && [...sel.options].some(o => o.value === elegido)) sel.value = elegido;
    } catch (_) { /* el selector se queda con las opciones por defecto */ }
  }

  /* ---------------- C6 · AÑADIR OTRA PARTE A LA REUNIÓN ------------------ */
  function subirParteSimple(file) {
    return new Promise((resolve, reject) => {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('session_id', S.sessionId || '');
      const xhr = new XMLHttpRequest();
      const btn = $('#btnContinuar');
      xhr.open('POST', '/api/upload');
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable && btn) {
          btn.innerHTML = `Subiendo ${Math.round((ev.loaded / ev.total) * 100)} %`;
        }
      };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText || '{}'); } catch (_) { data = null; }
        if (xhr.status >= 200 && xhr.status < 300 && data && data.file_id) resolve(data);
        else reject(new Error((data && data.detail) || `No se pudo subir el audio (${xhr.status}).`));
      };
      xhr.onerror = () => reject(new Error('Se cortó la conexión al subir el audio. Vuelve a intentarlo.'));
      xhr.send(fd);
    });
  }

  function esperarParte(parteId, numero, reunionId) {
    return new Promise((resolve) => {
      const btn = $('#btnContinuar');
      const timer = setInterval(async () => {
        try {
          const job = await api(`/api/jobs/${parteId}`);
          if (btn) btn.innerHTML = `Parte ${numero} · ${Math.round(job.progress || 0)} %`;
          if (job.status === 'done') {
            clearInterval(timer);
            await loadResult(reunionId);
            toast(`Parte ${numero} añadida a la reunión. Vuelve a redactar el acta para incluirla.`,
              'ok', 'Reunión actualizada');
            resolve();
          } else if (job.status === 'error' || job.status === 'cancelled') {
            clearInterval(timer);
            toast(job.error || job.message || 'La parte no se pudo procesar.',
              'err', 'Continuar reunión');
            resolve();
          }
        } catch (err) {
          clearInterval(timer);
          toast(err.message, 'err', 'Continuar reunión');
          resolve();
        }
      }, 1600);
    });
  }

  async function iniciarContinuar(file) {
    if (!file || !S.jobId) return;
    const btn = $('#btnContinuar');
    const original = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = 'Subiendo 0 %'; }
    const reunionId = S.jobId;
    try {
      const meta = await subirParteSimple(file);
      if (btn) btn.innerHTML = 'Encolando la parte…';
      const res = await api(`/api/jobs/${reunionId}/continuar`, {
        method: 'POST',
        body: JSON.stringify({ file_id: meta.file_id }),
      });
      toast(`Parte ${res.parte} en cola: se transcribirá y se unirá a esta reunión.`,
        'info', 'Continuar reunión');
      await esperarParte(res.job_id, res.parte, reunionId);
    } catch (err) {
      toast(err.message, 'err', 'No se pudo añadir la parte');
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = original; }
      const inp = $('#continuarFile');
      if (inp) inp.value = '';
    }
  }

  function renderResultHeader() {
    const meta = S.job || {};
    $('#resultTitle').textContent = S.title || meta.filename || 'Reunión';
    const bits = [
      meta.filename,
      `${fmtTime(meta.duration || S.info.duration || 0, true)} de audio`,
      S.info.language ? `idioma: ${S.info.language}${S.info.language_probability ? ` (${Math.round(S.info.language_probability * 100)} %)` : ''}` : '',
      `${fmtNum(S.segments.length)} fragmentos`,
    ].filter(Boolean);
    $('#resultMeta').textContent = bits.join(' · ');
  }

  function renderStats() {
    const st = S.stats || {};
    const total = Number(st.total_seconds || 0);
    $('#statDuration').textContent = fmtTime((S.job && S.job.duration) || S.info.duration || 0, true);
    $('#statTime').textContent = fmtDur(total);
    $('#statSpeed').textContent = st.speed_factor ? `${st.speed_factor}×` : '—';
    $('#statWords').textContent = fmtNum(st.words || 0);
    $('#statSpeakers').textContent = S.speakers.length || 0;
    const lowCount = S.segments.filter(s => (s.avg_logprob ?? 0) < S.lowThreshold).length;
    $('#statLowConf').textContent = fmtNum(lowCount);
    const frag = $('#statFragments');
    if (frag) frag.textContent = fmtNum(S.segments.length);
    const chip = $('#btnLowChip');
    if (chip) {
      chip.classList.toggle('muted', lowCount === 0);
      chip.disabled = lowCount === 0;
      chip.title = lowCount
        ? 'Mostrar solo los fragmentos marcados como dudosos'
        : 'No hay fragmentos con baja confianza';
    }

    const phases = [
      ['Cleanup', Number(st.cleanup_seconds || 0)],
      ['Transcribe', Number(st.transcribe_seconds || 0)],
      ['Diarize', Number(st.diarize_seconds || 0)],
    ];
    const base = Math.max(total, phases.reduce((a, p) => a + p[1], 0), 1);
    phases.forEach(([name, sec]) => {
      const bar = $(`#phase${name}Bar`), time = $(`#phase${name}Time`);
      bar.style.width = Math.max(1, Math.min(100, (sec / base) * 100)) + '%';
      bar.title = `${fmtDur(sec)} de ${fmtDur(total)}`;
      time.textContent = sec >= 60 ? fmtTime(sec, true) : `${sec.toFixed(1)} s`;
    });
    $('#transcriptStats').textContent =
      `${fmtNum(S.segments.length)} fragmentos · ${fmtNum(st.words || 0)} palabras · ${S.speakers.length} hablantes`;
  }

  function renderWarnings(list) {
    const box = $('#warningsBox');
    const items = (list || []).filter(Boolean);
    if (!items.length) { box.hidden = true; box.innerHTML = ''; return; }
    box.hidden = false;
    box.innerHTML = `<ul>${items.map(w => `<li>${esc(w)}</li>`).join('')}</ul>`;
  }

  /* ------------------------------ HABLANTES ----------------------------- */
  function renderSpeakers() {
    const box = $('#speakerList');
    if (!S.speakers.length) {
      box.innerHTML = `<p class="muted small">No se detectaron hablantes (instala <code>resemblyzer</code> y reprocesa el audio para identificarlos).</p>`;
      $('#diarHint').textContent = 'Sin diarización';
      return;
    }
    box.innerHTML = S.speakers.map((sp, i) => `
      <div class="speaker-row" data-index="${i}" style="--sp-color:${colorFor(i)}">
        <span class="avatar" title="${esc(sp.name)}">${esc(initials(sp.name))}</span>
        <input class="input speaker-name-input" value="${esc(sp.name)}" data-index="${i}"
               list="knownVoices" autocomplete="off" aria-label="Nombre del hablante ${i + 1}">
        <span class="speaker-meta">${fmtNum(sp.count)} int. · ${fmtDur(sp.seconds)}</span>
        ${sp.known ? `<span class="known-chip" title="Coincide con una voz guardada (${Math.round((sp.similarity || 0) * 100)} %)">voz conocida</span>` : '<span></span>'}
        <button class="icon-only btn-listen" data-index="${i}" title="Escuchar muestra de esta voz">▶</button>
        <button class="icon-only btn-save-voice" data-index="${i}" title="Guardar esta voz para futuras reuniones">${icon("save", "icon icon-sm")}</button>
      </div>`).join('');

    $$('.speaker-name-input', box).forEach(input => {
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); applyNames(); } });
      input.addEventListener('input', () => {
        const i = Number(input.dataset.index);
        S.speakers[i].name = input.value;
        const avatar = input.closest('.speaker-row').querySelector('.avatar');
        avatar.textContent = initials(input.value);
        avatar.title = input.value;
      });
    });
    $$('.btn-listen', box).forEach(btn => btn.addEventListener('click', () => listenSpeaker(Number(btn.dataset.index))));
    $$('.btn-save-voice', box).forEach(btn => btn.addEventListener('click', () => saveVoice(Number(btn.dataset.index))));
    renderMergeSelects();
  }

  let sampleAudio = null;
  function listenSpeaker(index) {
    const sp = S.speakers[index];
    if (!sp) return;
    if (sampleAudio) { sampleAudio.pause(); sampleAudio = null; }
    const url = `/api/jobs/${S.jobId}/speaker-sample/${index}?t=${Date.now()}`;
    sampleAudio = new Audio(url);
    sampleAudio.play()
      .then(() => toast(`Escuchando muestra de ${sp.name}`, 'info'))
      .catch(() => toast('No se pudo reproducir la muestra de voz.', 'err'));
  }

  async function saveVoice(index) {
    const sp = S.speakers[index];
    if (!sp) return;
    const name = (prompt('Guardar esta huella de voz como:', sp.name || '') || '').trim();
    if (!name) return;
    try {
      const res = await api('/api/voces', {
        method: 'POST',
        body: JSON.stringify({ job_id: S.jobId, speaker_name: sp.original, save_as: name }),
      });
      toast(`Voz «${res.speaker.name}» guardada (${res.speaker.samples} muestra/s).`, 'ok', 'Base de voces');
      loadKnownVoices();
    } catch (err) {
      toast(err.message, 'err', 'No se pudo guardar la voz');
    }
  }

  function applyNames() {
    if (!S.speakers.length) return;
    pushUndo('aplicar nombres');
    S.segments.forEach(seg => {
      if (seg.si >= 0 && S.speakers[seg.si]) seg.speaker = (S.speakers[seg.si].name || '').trim();
    });
    renderTranscript();
    renderStats();
    const labels = {};
    S.speakers.forEach(sp => { labels[sp.original] = sp.name; });
    saveSession({ labels });
    toast('Nombres aplicados a toda la transcripción.', 'ok');
  }

  function speakerMenu(anchor, seg) {
    $$('.sp-menu').forEach(m => m.remove());
    const menu = document.createElement('div');
    menu.className = 'sp-menu';
    menu.innerHTML = S.speakers.map((sp, i) =>
      `<button type="button" data-i="${i}"><span class="avatar" style="--sp-color:${colorFor(i)}">${esc(initials(sp.name))}</span>${esc(sp.name)}</button>`).join('')
      + `<button type="button" data-new="1">${icon("plus", "icon icon-sm")} Nuevo hablante…</button>`;
    document.body.appendChild(menu);
    const r = anchor.getBoundingClientRect();
    menu.style.top = (r.bottom + window.scrollY + 6) + 'px';
    menu.style.left = Math.min(r.left + window.scrollX, window.innerWidth - 230) + 'px';
    const close = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', close); } };
    setTimeout(() => document.addEventListener('click', close), 10);
    menu.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.new) {
        const name = (prompt('Nombre del nuevo hablante:') || '').trim();
        if (name) {
          S.speakers.push({ name, original: name, count: 0, seconds: 0, known: false, similarity: 0, color: S.speakers.length });
          seg.si = S.speakers.length - 1;
          seg.speaker = name;
          renderSpeakers();
          renderTranscript();
          saveSession({ labels: Object.fromEntries(S.speakers.map((s, i) => [s.original, s.name])) });
        }
      } else {
        const i = Number(b.dataset.i);
        seg.si = i;
        seg.speaker = S.speakers[i].name;
        renderTranscript();
        renderStats();
      }
      menu.remove();
    });
  }

  /* ---------------------------- TRANSCRIPCIÓN --------------------------- */
  function segmentNode(seg, i, prevSeg, paragraphs) {
    const speaker = (seg.speaker || '').trim();
    const prevSpeaker = prevSeg ? (prevSeg.speaker || '').trim() : '';
    const prevEnd = prevSeg ? Number(prevSeg.end ?? prevSeg.start ?? 0) : -1;
    const si = seg.si >= 0 ? seg.si : S.speakers.findIndex(sp => sp.name === speaker);
    const color = si >= 0 ? colorFor(si) : 'var(--muted)';
    const low = (seg.avg_logprob ?? 0) < S.lowThreshold;
    const cont = paragraphs && speaker && speaker === prevSpeaker;
    // Mismo hablante y sin pausa larga ⇒ sigue el turno: no repetimos su nombre.
    const sameTurn = !!(speaker && speaker === prevSpeaker &&
      (Number(seg.start) - prevEnd) < 10);
    const div = document.createElement('div');
    div.className = 'segment' + (low ? ' low-confidence' : '') + (cont ? ' cont' : '')
      + (sameTurn ? ' same-turn' : '')
      + (S.selecting ? ' selectable' : '') + (S.selection.has(i) ? ' selected' : '');
    if (si >= 0) div.style.setProperty('--sp-color', color);
    div.dataset.index = i;
    div.dataset.start = seg.start;
    div.dataset.end = seg.end;
    div.innerHTML = `
      <span class="ts" title="Ir a ${fmtTime(seg.start, true)}">${fmtTime(seg.start)}</span>
      <div class="seg-body">
        ${sameTurn ? '' : `<div class="seg-head${cont ? ' is-cont' : ''}">
          ${speaker ? `<span class="seg-speaker" style="--sp-color:${color}">
              <span class="avatar" style="--sp-color:${color}">${esc(initials(speaker))}</span>${esc(speaker)}
            </span>` : ''}
          ${seg.joined ? `<span class="seg-warn" title="Turnos unidos del mismo hablante">${icon("link", "icon icon-sm")} ${seg.joined}</span>` : ''}
          <button class="add-speaker" type="button" title="Asignar hablante a esta intervención">+ hablante</button>
        </div>`}
        <div class="seg-text" ${S.editing ? 'contenteditable="true" spellcheck="false"' : ''}>${esc(seg.text || '')}</div>
      </div>`;
    if (S.editing) {
      const textEl = div.querySelector('.seg-text');
      textEl.addEventListener('input', debounce(() => {
        S.segments[i].text = textEl.innerText.replace(/\s+\n/g, '\n').trim();
        S.edited = true;
        const note = $('#editsNote');
        if (note) note.hidden = false;
        const st = S.stats;
        st.words = S.segments.reduce((a, x) => a + (x.text || '').split(/\s+/).filter(Boolean).length, 0);
        $('#statWords').textContent = fmtNum(st.words);
        saveSession({});
      }, 700));
      textEl.addEventListener('focus', () => pushUndo('edición de texto'), { once: true });
    }
    return div;
  }

  /**
   * Dibuja la transcripción por tramos (400 fragmentos) y añade más al hacer
   * scroll: en reuniones de 3 h el primer render ya no bloquea el navegador.
   */
  function renderTranscript(keepScroll = false) {
    const box = $('#segmentList');
    const total = S.segments.length;
    const limit = Math.min(total, Math.max(200, S.renderLimit));
    const paragraphs = $('#optParagraphs').checked;
    const frag = document.createDocumentFragment();
    for (let i = 0; i < limit; i += 1) {
      const seg = S.segments[i];
      frag.appendChild(segmentNode(seg, i, i > 0 ? S.segments[i - 1] : null, paragraphs));
    }
    box.innerHTML = '';
    box.appendChild(frag);
    if (limit < total) {
      const more = document.createElement('button');
      more.type = 'button';
      more.className = 'list-more';
      more.id = 'btnMoreSegments';
      more.textContent = `Mostrar ${fmtNum(Math.min(400, total - limit))} fragmentos más `
        + `(${fmtNum(limit)} de ${fmtNum(total)})`;
      more.addEventListener('click', () => {
        S.renderLimit = limit + 400;
        renderTranscript(true);
      });
      box.appendChild(more);
    }
    box.classList.toggle('selecting', S.selecting);
    if (!box.dataset.bound) {
      box.dataset.bound = '1';
      box.addEventListener('click', (e) => {
        const more = e.target.closest('#btnMoreSegments');
        if (more) {
          S.renderLimit = Math.max(200, S.renderLimit) + 400;
          renderTranscript(true);
          return;
        }
        const el = e.target.closest('.segment');
        if (!el) return;
        const idx = Number(el.dataset.index);
        const sub = e.target.closest('.add-speaker');
        if (sub) { e.stopPropagation(); speakerMenu(sub, S.segments[idx]); return; }
        if (S.editing && e.target.closest('.seg-text')) return;
        if (S.selecting) {
          if (S.selection.has(idx)) S.selection.delete(idx); else S.selection.add(idx);
          el.classList.toggle('selected', S.selection.has(idx));
          updateSelectInfo();
          return;
        }
        seekTo(Number(el.dataset.start) + 0.01);
        setActive(idx, true);
      });
    }
    if (S.search.query) applyHighlight();
    setActive(S.activeId, false);
    if (keepScroll) { /* el navegador conserva la posición al no cambiar el alto */ }
  }

  function updateSelectInfo() {
    const info = $('#selectInfo');
    if (info) {
      info.textContent = S.selecting
        ? `${S.selection.size} seleccionados`
        : '0 seleccionados';
    }
  }

  function toggleSelectMode(on) {
    S.selecting = on === undefined ? !S.selecting : on;
    if (!S.selecting) S.selection.clear();
    const btn = $('#btnSelectSegments');
    if (btn) {
      btn.classList.toggle('btn-primary', S.selecting);
      btn.innerHTML = S.selecting ? icon('check') + ' Terminar selección' : 'Seleccionar fragmentos';
    }
    $('#segmentList').classList.toggle('selecting', S.selecting);
    if (S.selecting) {
      toast('Haz clic en los fragmentos que pertenecen a otra voz y pulsa «Separar».', 'info', 'Selección activa');
    }
    renderTranscript(true);
    updateSelectInfo();
  }

  function setActive(index, scroll) {
    if (!S.segments.length) return;
    S.activeId = index;
    const box = $('#segmentList');
    if (index >= 0 && index >= S.renderLimit) {
      S.renderLimit = index + 200;
      renderTranscript(true);
    }
    $$('.segment', box).forEach(el => el.classList.remove('active'));
    if (index < 0) return;
    const el = box.querySelector(`.segment[data-index="${index}"]`);
    if (!el) return;
    el.classList.add('active');
    if (scroll && S.follow) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  function toggleEdit(on) {
    S.editing = on === undefined ? !S.editing : on;
    $('#btnEdit').innerHTML = icon('pencil') + (S.editing ? ' Edición activa' : ' Editar texto');
    $('#btnEdit').classList.toggle('btn-primary', S.editing);
    $('#editBanner').hidden = !S.editing;
    renderTranscript();
    if (S.editing) toast('Edita cualquier fragmento: se guardará antes de exportar.', 'info');
  }

  /* ------------------------------- BUSCADOR ----------------------------- */
  const norm = (str = '') => String(str).toLocaleLowerCase('es');

  /** Texto plegado (sin acentos) + mapa posición plegada → posición original. */
  function foldWithMap(text = '') {
    let out = '';
    const map = [];
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      const folded = ch.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
      for (let k = 0; k < Math.max(1, folded.length); k += 1) {
        out += folded[k] || '';
        map.push(i);
      }
    }
    return { folded: out, map };
  }
  function updateSearch(query) {
    S.search.query = query;
    S.search.matches = [];
    S.search.index = -1;
    const q = fold(query).trim();
    if (q) {
      S.segments.forEach((seg, i) => {
        const { folded, map } = foldWithMap(seg.text || '');
        let from = 0, pos;
        while ((pos = folded.indexOf(q, from)) !== -1 && S.search.matches.length < 3000) {
          S.search.matches.push({ i, pos, origPos: map[pos] ?? pos });
          from = pos + q.length;
        }
      });
    }
    $('#searchCount').textContent = q ? `${S.search.matches.length} coincidencia${S.search.matches.length === 1 ? '' : 's'}` : '';
    if (q && S.search.matches.length) { S.search.index = 0; }
    applyHighlight();
    if (S.search.index >= 0) focusMatch(S.search.index);
  }

  function applyHighlight() {
    const q = S.search.query.trim();
    const current = S.search.index >= 0 ? S.search.matches[S.search.index] : null;
    $$('.segment').forEach(el => {
      const i = Number(el.dataset.index);
      const textEl = el.querySelector('.seg-text');
      const text = S.segments[i] ? (S.segments[i].text || '') : '';
      if (!q) {
        if (textEl.innerHTML !== esc(text)) textEl.innerHTML = esc(text);
        return;
      }
      if (S.editing && document.activeElement === textEl) return;
      const { folded, map } = foldWithMap(text);
      const needle = fold(q);
      let out = '', from = 0, pos;
      while ((pos = folded.indexOf(needle, from)) !== -1) {
        const startOrig = map[pos] ?? pos;
        const endOrig = (map[pos + needle.length - 1] ?? (startOrig + needle.length - 1)) + 1;
        out += esc(text.slice(from, startOrig));
        const isCur = current && current.i === i && current.pos === pos;
        out += `<mark class="${isCur ? 'current' : ''}" data-i="${i}" data-pos="${pos}">${esc(text.slice(startOrig, endOrig))}</mark>`;
        from = endOrig;
        pos += needle.length;
        if (needle.length === 0) break;
      }
      out += esc(text.slice(from));
      textEl.innerHTML = out;
    });
  }

  function focusMatch(idx) {
    const m = S.search.matches[idx];
    if (!m) return;
    if (m.i >= S.renderLimit) {
      S.renderLimit = m.i + 200;
      renderTranscript(true);
    }
    applyHighlight();
    const segEl = $(`.segment[data-index="${m.i}"]`);
    const mark = segEl && segEl.querySelector('mark.current');
    if (mark) mark.scrollIntoView({ block: 'center', behavior: 'smooth' });
    else if (segEl) segEl.scrollIntoView({ block: 'center', behavior: 'smooth' });
    $('#searchCount').textContent = `${idx + 1} / ${S.search.matches.length}`;
  }

  function stepMatch(dir) {
    if (!S.search.matches.length) return;
    S.search.index = (S.search.index + dir + S.search.matches.length) % S.search.matches.length;
    focusMatch(S.search.index);
  }

  function jumpToLowConfidence() {
    const list = S.segments.filter(s => (s.avg_logprob ?? 0) < S.lowThreshold);
    if (!list.length) { toast('No hay fragmentos con baja confianza acústica.', 'ok'); return; }
    S.lowPointer = (S.lowPointer + 1) % list.length;
    const seg = list[S.lowPointer];
    const target = S.segments.findIndex(s => s.id === seg.id || (s.start === seg.start && s.text === seg.text));
    seekTo(seg.start + 0.01);
    setActive(target, true);
    toast(`Fragmento dudoso ${S.lowPointer + 1} de ${list.length} · ${fmtTime(seg.start)} · logprob ${(seg.avg_logprob ?? 0).toFixed(2)}`,
      'warn', 'Revisión sugerida');
  }

  /* ----------------------------- REPRODUCTOR ---------------------------- */
  function setupAudio(url) {
    const audio = $('#audio');
    S.audio = audio;
    if (audio.dataset.src !== url) {
      audio.src = url;
      audio.dataset.src = url;
      audio.load();
    }
    audio.playbackRate = Number(store.get('tae_speed') || 1);
    if (sampleAudio) { sampleAudio.pause(); sampleAudio = null; }
    $('#playerDuration').textContent = fmtTime(S.job.duration || S.info.duration || 0, true);
    $('#nowPlaying').textContent = 'Selecciona un fragmento para escucharlo';
  }

  const findSegmentAt = (t) => {
    let lo = 0, hi = S.segments.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const seg = S.segments[mid];
      if (t >= seg.start - 0.05) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    return ans;
  };

  function seekTo(seconds) {
    const audio = S.audio || $('#audio');
    if (!audio || !isFinite(seconds)) return;
    try {
      audio.currentTime = Math.max(0, seconds);
      if (audio.paused) {
        audio.play().then(() => setPlayIcon(true)).catch(() => setPlayIcon(false));
      }
    } catch (_) { /* aún sin metadata */ }
  }

  function skipSeconds(delta) {
    const audio = S.audio || $('#audio');
    if (!audio) return;
    const dur = currentDuration() || audio.duration || 0;
    audio.currentTime = Math.max(0, Math.min(dur || Infinity, (audio.currentTime || 0) + delta));
    updateWaveCursor();
  }

  function setPlayIcon(playing) {
    $('#btnPlayPause').innerHTML = icon(playing ? 'pause' : 'play', 'icon icon-lg');
    $('#btnPlayPause').setAttribute('aria-label', playing ? 'Pausar' : 'Reproducir');
  }

  function bindPlayer() {
    const audio = $('#audio');
    $('#btnPlayPause').addEventListener('click', () => {
      if (audio.paused) audio.play().catch(() => toast('No se pudo reproducir el audio.', 'err'));
      else audio.pause();
    });
    audio.addEventListener('play', () => setPlayIcon(true));
    audio.addEventListener('pause', () => setPlayIcon(false));
    audio.addEventListener('loadedmetadata', () => {
      $('#playerDuration').textContent = fmtTime(audio.duration, true);
    });
    audio.addEventListener('timeupdate', () => {
      const t = audio.currentTime;
      $('#playerCurrent').textContent = fmtTime(t, true);
      const dur = audio.duration || S.job && S.job.duration || 0;
      $('#seekBar').value = dur ? (t / dur) * 100 : 0;
      const prog = $('#waveProgress');
      if (prog) prog.style.width = `${dur ? Math.min(100, (t / dur) * 100) : 0}%`;
      updateWaveCursor();
      markChapter(t);
      const idx = findSegmentAt(t);
      if (idx !== S.activeId) {
        setActive(idx, true);
        const seg = S.segments[idx];
        if (seg) {
          const sp = (seg.speaker || '').trim();
          $('#nowPlaying').textContent = `${fmtTime(seg.start)} · ${sp || 'Voz'}: ${(seg.text || '').slice(0, 54)}…`;
        }
      }
    });
    $('#btnBack5').addEventListener('click', () => skipSeconds(-5));
    $('#btnFwd5').addEventListener('click', () => skipSeconds(5));
    const follow = $('#chkFollow');
    if (follow) {
      follow.checked = S.follow;
      follow.addEventListener('change', () => {
        S.follow = follow.checked;
        toast(S.follow ? 'La transcripción seguirá al audio.' : 'Desplazamiento automático desactivado.',
          'info');
      });
    }
    const waveWrap = $('#waveWrap');
    if (waveWrap) {
      waveWrap.addEventListener('click', (e) => {
        if (e.target.closest('#seekBar')) return;   // el range nativo ya salta solo
        const dur = currentDuration();
        if (!dur) return;
        const rect = waveWrap.getBoundingClientRect();
        const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        seekTo(ratio * dur);
      });
    }
    window.addEventListener('resize', debounce(() => drawWaveform(), 250));
    initTocMenu();

    $('#seekBar').addEventListener('input', (e) => {
      const dur = audio.duration || S.job && S.job.duration || 0;
      if (dur) audio.currentTime = (Number(e.target.value) / 100) * dur;
    });
    $$('#speedGroup .speed-btn').forEach(btn => {
      const sp = Number(btn.dataset.speed);
      btn.classList.toggle('is-active', Math.abs(sp - Number(store.get('tae_speed') || 1)) < 0.01);
      btn.addEventListener('click', () => {
        audio.playbackRate = sp;
        store.set('tae_speed', String(sp));
        $$('#speedGroup .speed-btn').forEach(b => b.classList.toggle('is-active', b === btn));
      });
    });
    document.addEventListener('keydown', (e) => {
      if (e.target.matches('input, textarea, select, [contenteditable="true"]')) return;
      if (e.code === 'Space' && !$('#playerBar').hidden) { e.preventDefault(); $('#btnPlayPause').click(); }
    });
  }

  /* -------------------------------- ACTA -------------------------------- */
  const CLASSIFY_RX = {
    acuerdos: /(acuerd|aprob|autoriz|decid|resolv|se\s+acuerda|se\s+aprueba|unanimidad|votaci)/i,
    pendientes: /(pendiente|seguimiento|queda\s+en|compromiso|plazo|antes\s+de|fecha\s+límite)/i,
    siguientes: /(siguiente|próxim|proxim|se\s+presentará|se\s+revisará|enviará|convocar)/i,
  };

  function classifyText(text) {
    const out = { acuerdos: [], pendientes: [], siguientes: [] };
    (text || '').split(/\n|•|·/).forEach(raw => {
      const trimmed = (raw || '').trim();
      if (trimmed.startsWith('#')) return;  // encabezado, no es un acuerdo
      let line = trimmed.replace(/^\s*(?:[-*#>]+|\d+[.)])\s*/, '').replace(/\*\*/g, '').trim();
      if (line.length < 8) return;
      if (CLASSIFY_RX.acuerdos.test(line)) out.acuerdos.push(line);
      else if (CLASSIFY_RX.pendientes.test(line)) out.pendientes.push(line);
      else if (CLASSIFY_RX.siguientes.test(line)) out.siguientes.push(line);
    });
    Object.keys(out).forEach(k => { out[k] = out[k].slice(0, 14); });
    return out;
  }

  function paintClassified(cls) {
    const map = { colAcuerdos: cls.acuerdos, colPendientes: cls.pendientes, colSiguientes: cls.siguientes };
    Object.entries(map).forEach(([id, items]) => {
      const ul = $('#' + id);
      ul.innerHTML = (items && items.length)
        ? items.map(x => `<li>${esc(x)}</li>`).join('')
        : '<li class="placeholder">Sin elementos</li>';
    });
  }

  async function loadStoredActa() {
    if (!S.jobId) return;
    try {
      const data = await api(`/api/jobs/${S.jobId}/acta`);
      if (data && (data.texto_oficial || data.text)) {
        // B6 · el acta oficial guardada manda sobre el resumen de la sesión
        const oficial = data.texto_oficial || data.text || '';
        const area = $('#summaryText');
        if (area && area.value.trim() !== oficial.trim()) area.value = oficial;
        renderAcuerdos(data);
        if (data.pendientes || data.acuerdos) {
          paintClassified(data.classified || classifyText(data.text));
        }
        updateSummaryCount();
        renderActaPreview();
        renderAcuerdos(data);
        renderFiguresPanel(data);
        cargarVersiones();
        avisarActaDesactualizada(data);
      }
    } catch (_) { /* sin acta guardada todavía */ }
  }

  function renderSummaryFromSession(rec) {
    const stored = rec && (rec.summary || rec.conclusion);
    if (stored) {
      $('#summaryText').value = stored;
      paintClassified(classifyText(stored));
    } else {
      $('#summaryText').value = '';
      paintClassified({ acuerdos: [], pendientes: [], siguientes: [] });
    }
    if (rec && rec.summary_format) $('#formatSelect').value = rec.summary_format;
    if (rec && rec.title) S.title = rec.title;
    updateSummaryCount();
    renderActaPreview();
  }


  /** Avisa si el acta se redactó antes de añadir una parte nueva a la reunión. */
  function avisarActaDesactualizada(acta) {
    const barra = $('#actaViewBar');
    if (!barra) return;
    let aviso = $('#actaDesactualizada');
    if (!acta || !acta.desactualizada) {
      if (aviso) aviso.remove();
      return;
    }
    if (!aviso) {
      aviso = document.createElement('div');
      aviso.id = 'actaDesactualizada';
      aviso.className = 'note-banner';
      barra.parentNode.insertBefore(aviso, barra);
    }
    aviso.innerHTML = '<strong>Acta desactualizada:</strong> esta reunión tiene una parte '
      + 'nueva que no está en el texto. Pulsa «Redactar acta» para incluirla; el acta '
      + 'anterior sigue guardada en las versiones.';
  }

  /** «12:35» o «1:12:35» → segundos. */
  function tsSegundos(txt) {
    const partes = String(txt || '').split(':').map(Number);
    if (partes.some(n => !isFinite(n))) return 0;
    if (partes.length === 3) return partes[0] * 3600 + partes[1] * 60 + partes[2];
    if (partes.length === 2) return partes[0] * 60 + partes[1];
    return partes[0] || 0;
  }

  /** Salta al minuto indicado y deja seleccionado el fragmento. */
  function irAlMinuto(txt) {
    const secs = tsSegundos(txt);
    seekTo(secs);
    const idx = findSegmentAt(secs);
    if (idx >= 0) setActive(idx, true);
    closeHistory();
    closeCompromisos();
  }

  // ---- Cifras por revisar (B5): qué frase las contiene y salto al audio ----
  function renderFiguresPanel(data) {
    const panel = $('#figuresPanel');
    const lista = $('#figuresList');
    if (!panel || !lista) return;
    const figs = (data && data.figures) || {};
    const faltan = figs.missing || [];
    if (!faltan.length) { panel.hidden = true; lista.innerHTML = ''; return; }
    const texto = $('#summaryText').value || '';
    const lineas = texto.split('\n');
    lista.innerHTML = faltan.slice(0, 12).map(cifra => {
      const linea = lineas.find(l => l.includes(cifra.replace(/(\d{3})(?=\d)/g, '$1')))
        || lineas.find(l => l.replace(/[.,\s]/g, '').includes(String(cifra)))
        || '';
      const m = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/.exec(linea);
      const recorte = linea.trim().replace(/^[*\-•\s]+/, '').slice(0, 150);
      return `<li>
        <code class="mono">${esc(cifra)}</code>
        <span class="fig-text">${esc(recorte || 'Sin contexto en el acta')}</span>
        ${m ? `<button type="button" class="link-btn" data-seek="${esc(m[1])}">${esc(m[1])}</button>` : ''}
      </li>`;
    }).join('');
    panel.hidden = false;
  }

  // ---- Versiones del acta (B4) ----
  async function cargarVersiones() {
    const sel = $('#actaVersionSelect');
    if (!sel || !S.jobId) return;
    try {
      const data = await api(`/api/jobs/${S.jobId}/acta/versiones`);
      const vs = data.versiones || [];
      sel.innerHTML = vs.map(v => {
        const eta = (v.creada || '').replace('T', ' ').slice(0, 16);
        const sello = v.modelo || v.motor || '';
        return `<option value="${esc(String(v.v))}">v${esc(String(v.v))} · ${esc(eta)} · ${esc(sello)} · ${fmtNum(v.chars || 0)} car.</option>`;
      }).join('') || '<option value="">Sin versiones</option>';
      const actual = String(data.version_oficial || data.actual || vs.length || '');
      if (actual) sel.value = actual;
      $('#btnActaRestaurar').disabled = vs.length < 2;
    } catch (_) {
      sel.innerHTML = '<option value="">—</option>';
    }
  }

  async function restaurarVersion() {
    const v = $('#actaVersionSelect').value;
    if (!v || !S.jobId) return;
    if (!confirm(`¿Restaurar la versión v${v}? El acta actual se reemplaza (las versiones se conservan).`)) return;
    try {
      const res = await api(`/api/jobs/${S.jobId}/acta/restaurar`, {
        method: 'POST', body: JSON.stringify({ v: Number(v) }),
      });
      $('#summaryText').value = res.texto || '';
      updateSummaryCount();
      renderActaPreview();
      toast(`Versión v${v} restaurada.`, 'ok', 'Acta');
    } catch (err) { toast(err.message, 'err', 'No se pudo restaurar'); }
  }

  // ---- Guardar el acta editada (B3/B6) ----
  async function guardarActaOficial(silencioso) {
    if (!S.jobId) return false;
    const texto = $('#summaryText').value || '';
    if (!texto.trim()) { if (!silencioso) toast('No hay acta que guardar.', 'warn'); return false; }
    try {
      await api(`/api/jobs/${S.jobId}/acta`, {
        method: 'PUT', body: JSON.stringify({ texto }),
      });
      saveSession({ summary: texto, conclusion: texto });
      if (!silencioso) toast('Acta guardada como versión oficial.', 'ok', 'Acta');
      return true;
    } catch (err) {
      if (!silencioso) toast(err.message, 'err', 'No se pudo guardar');
      return false;
    }
  }

  // ---- Plantilla institucional (B1) ----
  async function cargarPlantilla() {
    try {
      const data = await api('/api/plantilla');
      const pl = data.plantilla || {};
      const mapa = {
        '#plOrganizacion': 'organizacion', '#plOrgano': 'organo', '#plCiudad': 'ciudad',
        '#plPresidencia': 'presidencia', '#plPresidenciaCargo': 'presidencia_cargo',
        '#plSecretaria': 'secretaria', '#plSecretariaCargo': 'secretaria_cargo',
      };
      Object.entries(mapa).forEach(([sel, clave]) => {
        const el = $(sel);
        if (el && pl[clave] != null) el.value = pl[clave];
      });
    } catch (_) { /* sin plantilla guardada */ }
  }

  async function guardarPlantilla() {
    const payload = {
      organizacion: $('#plOrganizacion').value.trim(),
      organo: $('#plOrgano').value.trim(),
      ciudad: $('#plCiudad').value.trim(),
      presidencia: $('#plPresidencia').value.trim(),
      presidencia_cargo: $('#plPresidenciaCargo').value.trim(),
      secretaria: $('#plSecretaria').value.trim(),
      secretaria_cargo: $('#plSecretariaCargo').value.trim(),
    };
    try {
      const res = await api('/api/plantilla', { method: 'POST', body: JSON.stringify(payload) });
      $('#plantillaStatus').textContent = `Guardada: ${res.plantilla.organo || 'acta'}`;
      toast('Plantilla guardada: se usará en las próximas actas.', 'ok', 'Plantilla');
    } catch (err) { toast(err.message, 'err', 'No se pudo guardar'); }
  }

  // ---- Vista formateada del acta (markdown → HTML, sin dependencias) --------
  function mdActa(src) {
    const safe = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const inl = (t) => safe(t)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g,
               '<button type="button" class="ts" data-seek="$1" title="Escuchar este momento">[$1]</button>');
    const out = [];
    let list = null;
    let table = [];
    const closeList = () => { if (list) { out.push('</' + list + '>'); list = null; } };
    const flushTable = () => {
      if (!table.length) return;
      const rows = table.filter(r => !/^\|[\s:|-]+\|?$/.test(r));
      const cells = rows.map(r => r.replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim()));
      const head = cells.shift() || [];
      out.push('<div class="acta-table-wrap"><table class="acta-table"><thead><tr>'
        + head.map(c => '<th>' + inl(c) + '</th>').join('') + '</tr></thead><tbody>'
        + cells.map(r => '<tr>' + r.map(c => '<td>' + inl(c) + '</td>').join('') + '</tr>').join('')
        + '</tbody></table></div>');
      table = [];
    };
    String(src || '').split(/\r?\n/).forEach(raw => {
      const line = raw.trim();
      if (/^\|/.test(line)) { closeList(); table.push(line); return; }
      flushTable();
      if (!line) { closeList(); return; }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) { closeList(); out.push('<hr>'); return; }
      let m;
      if ((m = /^#{1,4}\s+(.*)$/.exec(line))) {
        closeList();
        out.push((out.length ? '<h3>' : '<h2>') + inl(m[1]) + (out.length ? '</h3>' : '</h2>'));
        return;
      }
      if ((m = /^\*\*(.+)\*\*$/.exec(line))) {          // línea entera en negritas = título
        closeList();
        const tag = out.length ? 'h3' : 'h2';
        out.push('<' + tag + '>' + inl(m[1]) + '</' + tag + '>');
        return;
      }
      if ((m = /^[*\-•]\s+(.*)$/.exec(line))) {
        if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul'; }
        out.push('<li>' + inl(m[1]) + '</li>');
        return;
      }
      if ((m = /^\d+[.)]\s+(.*)$/.exec(line))) {
        if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol'; }
        out.push('<li>' + inl(m[1]) + '</li>');
        return;
      }
      closeList();
      out.push('<p>' + inl(line) + '</p>');
    });
    closeList();
    flushTable();
    return out.join('\n');
  }

  let actaViewFormatted = true;
  let actaViewUserPicked = false;   // si el usuario eligió una pestaña, se respeta

  function applyActaView() {
    const txt = $('#summaryText');
    const prev = $('#actaPreview');
    if (!txt || !prev) return;
    const has = !!(txt.value || '').trim();
    prev.hidden = !(has && actaViewFormatted);
    txt.hidden = has && actaViewFormatted;
    const fmt = $('#btnActaFormatted');
    const raw = $('#btnActaRaw');
    if (fmt) { fmt.classList.toggle('is-active', actaViewFormatted); fmt.setAttribute('aria-selected', String(actaViewFormatted)); }
    if (raw) { raw.classList.toggle('is-active', !actaViewFormatted); raw.setAttribute('aria-selected', String(!actaViewFormatted)); }
    const hint = $('#actaViewHint');
    if (hint) {
      hint.textContent = actaViewFormatted
        ? 'Así se verá al imprimir o exportar.'
        : 'Puedes editar el texto: se guarda y se exporta tal cual.';
    }
  }

  function renderActaPreview() {
    const txt = $('#summaryText');
    const prev = $('#actaPreview');
    const bar = $('#actaViewBar');
    if (!txt || !prev || !bar) return;
    const raw = (txt.value || '').trim();
    bar.hidden = !raw;
    prev.innerHTML = raw ? mdActa(raw) : '';
    applyActaView();
    if (window.__syncActaEmpty) window.__syncActaEmpty();   // oculta la tarjeta «aún no hay acta»
  }

  function updateSummaryCount() {
    $('#summaryCount').textContent = `${fmtNum($('#summaryText').value.length)} caracteres`;
  }

  function renderAcuerdos(data) {
    const wrap = $('#acuerdosWrap');
    const body = $('#acuerdosBody');
    if (!wrap || !body) return;
    const acuerdos = data.acuerdos || [];
    const pendientes = data.pendientes || [];
    if (!acuerdos.length && !pendientes.length) { wrap.hidden = true; return; }
    wrap.hidden = false;
    const timeOf = (txt) => {
      const m = /\[(\d{1,2}:\d{2}(?::\d{2})?)\]/.exec(txt || '');
      return m ? m[1] : '';
    };
    const stripTs = (txt) => String(txt || '').replace(/\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*/g, ' ').trim();
    const campo = (tipo, idx, clave, valor, ancho) => `
      <input class="cell-input${ancho ? ' ' + ancho : ''}" type="text"
             data-tipo="${tipo}" data-idx="${idx}" data-campo="${clave}"
             value="${esc(valor || '')}" placeholder="—"
             aria-label="${esc(clave)} del ${tipo} ${idx + 1}">`;
    const fila = (item, tipo, idx, etiqueta, estatus) => {
      const texto = tipo === 'acuerdo' ? item.acuerdo : item.tema;
      const ts = timeOf(texto) || timeOf(item.fecha);
      return `<tr data-tipo="${tipo}" data-idx="${idx}">
        <td class="cell-text">${esc(stripTs(texto))}${etiqueta ? ` <span class="small muted">(${etiqueta})</span>` : ''}</td>
        <td>${campo(tipo, idx, 'responsable', item.responsable)}</td>
        <td>${campo(tipo, idx, 'fecha', item.fecha, 'cell-sm')}</td>
        <td>${campo(tipo, idx, 'estatus', item.estatus || estatus, 'cell-sm')}</td>
        <td class="mono" data-seek="${esc(ts)}" title="Ir al momento del acuerdo">${esc(ts || '—')}</td>
      </tr>`;
    };
    body.innerHTML = acuerdos.map((a, i) => fila(a, 'acuerdo', i, '', '')).join('')
      + pendientes.map((p, i) => fila(p, 'pendiente', i, 'pendiente', 'pendiente')).join('');
    const hint = $('#acuerdosHint');
    if (hint) hint.hidden = false;

    $$('#acuerdosBody td.mono[data-seek]').forEach(td => td.addEventListener('click', () => {
      if (td.dataset.seek) irAlMinuto(td.dataset.seek);
    }));

    // edición en línea: se guarda al salir del campo (B3)
    $$('#acuerdosBody .cell-input').forEach(inp => {
      inp.addEventListener('change', async () => {
        const { tipo, idx, campo } = inp.dataset;
        try {
          await api(`/api/compromisos/${S.jobId}`, {
            method: 'PATCH',
            body: JSON.stringify({ tipo, idx: Number(idx), campo, valor: inp.value }),
          });
          inp.classList.add('is-saved');
          setTimeout(() => inp.classList.remove('is-saved'), 1200);
        } catch (err) {
          toast(err.message, 'err', 'No se pudo guardar el acuerdo');
        }
      });
      inp.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') inp.blur(); });
    });

    const figs = data.figures || {};
    $('#figuresInfo').textContent = figs.checked
      ? (figs.ok ? `✓ ${figs.checked} cifras verificadas contra la transcripción`
                 : `${figs.missing.length} cifra(s) sin respaldo literal`)
      : '';
    renderFiguresPanel(data);
    if (acuerdos.length || pendientes.length) {
      paintClassified({
        acuerdos: acuerdos.map(a => stripTs(a.acuerdo)),
        pendientes: pendientes.map(p => stripTs(p.tema)),
        siguientes: data.siguientes_pasos || [],
      });
    }
  }


  function iaProgress(show, pct, text) {
    const box = $('#iaProgress');
    if (!box) return;
    box.hidden = !show;
    if (pct != null) $('#iaProgressBar').style.width = Math.max(0, Math.min(100, pct)) + '%';
    if (text != null) $('#iaProgressText').textContent = text;
  }

  /** Redacta el acta con streaming (token a token) o en modo clásico. */
  async function summarize() {
    if (!S.jobId) { toast('Primero transcribe una reunión.', 'warn'); return; }
    const engine = $('#engineSelect').value;
    const infoMotor = engineInfo(engine);
    if (infoMotor && infoMotor.ready === false) {
      const comando = (S.iaConfig && S.iaConfig.comando) || 'pip install torch transformers accelerate';
      toast(engine === 'local'
        ? `Falta la IA local. Ejecuta instalar.bat y elige la opción 1 en la etapa «IA para redactar el acta», o pega: ${comando}`
        : engine === 'openai'
          ? 'No se detectó Ollama. Ábrelo y ejecuta: ollama pull qwen2.5:7b-instruct'
          : 'Falta la API Key de Gemini.',
        'warn', 'Motor de IA no disponible');
      $('#iaAviso').scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    const btn = $('#btnSummarize');
    const stopBtn = $('#btnStopSummarize');
    const status = $('#summaryStatus');
    const area = $('#summaryText');
    const started = Date.now();
    const segments = S.segments.map(sg => ({
      start: sg.start, end: sg.end, text: sg.text, speaker: sg.speaker,
    }));
    const payload = {
      job_id: S.jobId,
      format: $('#formatSelect').value,
      engine,
      api_key: $('#geminiKey').value.trim(),
      save_key: !!$('#geminiKey').value.trim(),
      local_model: $('#localModelSelect').value,
      openai_base_url: $('#openaiBaseUrl').value.trim(),
      openai_model: $('#openaiModel').value.trim(),
      title: S.title,
      participants: $('#iaParticipants') ? $('#iaParticipants').value.trim() : '',
      glossary: $('#iaGlossary') ? $('#iaGlossary').value.trim() : '',
      acta_previa: $('#iaActaPrevia') ? $('#iaActaPrevia').value : '',
      segments,
    };

    btn.disabled = true;
    stopBtn.hidden = false;
    if (!actaViewUserPicked) { actaViewFormatted = false; applyActaView(); }   // vista en vivo
    status.dataset.busy = '1';
    status.textContent = 'Preparando…';
    area.value = '';
    iaProgress(true, 0, 'Preparando el material…');
    S.summaryTimer = setInterval(() => {
      if (!S.streamStarted) status.textContent = `Preparando… ${Math.floor((Date.now() - started) / 1000)} s`;
    }, 700);

    let finalData = null;
    let streamed = '';
    const handle = (ev) => {
      if (ev.type === 'stage') {
        if (ev.stage === 'secciones') {
          const pct = ev.total ? (ev.done / ev.total) * 100 : 0;
          iaProgress(true, pct, ev.message || 'Redactando el acta por secciones…');
          status.textContent = ev.total
            ? `Sección ${ev.done || 0}/${ev.total} · ${Math.round((Date.now() - started) / 1000)} s`
            : `Planeando el acta… · ${Math.round((Date.now() - started) / 1000)} s`;
          S.streamStarted = true;
        } else if (ev.stage === 'map') {
          const pct = ev.total ? (ev.done / ev.total) * 100 : 0;
          iaProgress(true, pct, `Resumiendo tramo ${ev.done || 0} de ${ev.total || '?'} · ${ev.message || ''}`);
          status.textContent = `Tramo ${ev.done || 0}/${ev.total || '?'} · ${Math.round((Date.now() - started) / 1000)} s`;
        } else {
          iaProgress(true, 100, 'Redactando el documento final…');
          status.textContent = 'Redactando…';
          S.streamStarted = true;
        }
      } else if (ev.type === 'token') {
        S.streamStarted = true;
        streamed += ev.text || '';
        area.value = streamed;
        area.scrollTop = area.scrollHeight;
        updateSummaryCount();
        status.textContent = `Redactando… ${streamed.length} caracteres · ${Math.round((Date.now() - started) / 1000)} s`;
      } else if (ev.type === 'done') {
        finalData = ev;
      } else if (ev.type === 'error') {
        throw new Error(ev.message || 'El motor de IA falló.');
      }
    };

    try {
      S.streamAbort = new AbortController();
      S.streamStarted = false;
      const res = await fetch('/api/ia/summarize/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: S.streamAbort.signal,
      });
      if (!res.ok || !res.body) {
        let msg = `Error ${res.status}`;
        try { msg = (await res.json()).detail || msg; } catch (_) { /* noop */ }
        throw new Error(msg);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += dec.decode(value, { stream: true });
        let cut;
        while ((cut = buffer.indexOf('\n\n')) >= 0) {
          const chunk = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          const line = chunk.split('\n').find(l => l.startsWith('data:'));
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
          handle(ev);
          if (ev.type === 'done') break;
        }
        if (finalData) break;
      }

      if (!finalData) throw new Error('El motor no devolvió el documento completo.');
      area.value = finalData.text || streamed;
      updateSummaryCount();
      if (!actaViewUserPicked) actaViewFormatted = true;
      renderActaPreview();
      renderFiguresPanel(finalData);
      cargarVersiones();
      renderAcuerdos(finalData);
      paintClassified(finalData.classified || classifyText(finalData.text || ''));
      const secs = finalData.elapsed ?? Math.round((Date.now() - started) / 1000);
      status.textContent = `Listo · ${finalData.model || engine} · ${secs} s · `
        + `${fmtNum(finalData.chars_used || 0)} caracteres de contexto`
        + (finalData.blocks > 1 ? ` · ${finalData.blocks} tramos` : '')
        + (finalData.sections > 1 ? ` · ${finalData.sections} secciones` : '');
      iaProgress(false);
      toast(`Documento generado con ${finalData.model || engine} en ${secs} s.`, 'ok', 'IA');
      announce('Acta generada');
      (finalData.warnings || []).forEach(w => toast(w, 'warn', 'Revisa el acta'));
      saveSession({
        conclusion: area.value, summary: area.value,
        summary_format: $('#formatSelect').value,
      });
      if (engine === 'gemini' && $('#geminiKey').value.trim()) loadIaConfig();
    } catch (err) {
      if (err.name === 'AbortError') {
        status.textContent = 'Detenido';
        toast('Redacción detenida.', 'warn');
      } else {
        status.textContent = 'Error';
        const limpio = String(err.message || err).replace(/^(RuntimeError|ValueError|Error|Exception):\s*/i, '');
        toast(limpio, 'err', 'No se pudo redactar');
      }
      iaProgress(false);
    } finally {
      clearInterval(S.summaryTimer);
      S.streamAbort = null;
      btn.disabled = false;
      stopBtn.hidden = true;
      delete status.dataset.busy;
      delete S.streamStarted;
    }
  }

  async function saveIaConfig() {
    try {
      const payload = {
        engine: $('#engineSelect').value,
        local_model: $('#localModelSelect').value,
        openai_base_url: $('#openaiBaseUrl').value.trim(),
        openai_model: $('#openaiModel').value.trim(),
      };
      const key = $('#geminiKey').value.trim();
      if (key) payload.gemini_api_key = key;
      const res = await api('/api/ia/config', { method: 'POST', body: JSON.stringify(payload) });
      $('#geminiKey').value = '';
      $('#geminiKey').placeholder = res.has_key ? `Guardada: ${res.key_masked}` : 'AIza…';
      toast('Configuración guardada en ia_config.json', 'ok', 'Motor de IA');
      await loadIaConfig();
    } catch (err) {
      toast(err.message, 'err', 'No se pudo guardar');
    }
  }

  async function testEngine() {
    const btn = $('#btnTestEngine');
    const engine = $('#engineSelect').value;
    const status = $('#summaryStatus');
    btn.disabled = true;
    status.textContent = 'Probando conexión…';
    try {
      const res = await api('/api/ia/test', {
        method: 'POST',
        body: JSON.stringify({
          engine,
          api_key: $('#geminiKey').value.trim(),
          local_model: $('#localModelSelect').value,
          openai_base_url: $('#openaiBaseUrl').value.trim(),
        }),
      });
      if (res.ok) await loadIaConfig();
      status.textContent = res.message || '';
      toast(res.message, res.ok ? 'ok' : 'warn', res.ok ? 'Motor listo' : 'Revisa la configuración');
    } catch (err) {
      status.textContent = err.message;
      toast(err.message, 'err', 'Prueba fallida');
    } finally {
      btn.disabled = false;
    }
  }

  async function exportDoc(format) {
    if (!S.jobId) { toast('No hay una reunión cargada.', 'warn'); return; }
    const btn = [...document.querySelectorAll(`[data-export="${format}"]`)];
    btn.forEach(b => { b.disabled = true; });
    try {
      // Se envía EXACTAMENTE lo que hay en pantalla: texto corregido, hablantes
      // renombrados y la conclusión editada. El servidor lo guarda y lo usa.
      const segments = S.segments.map(sg => ({
        start: sg.start, end: sg.end, text: sg.text || '',
        speaker: sg.speaker || '', avg_logprob: sg.avg_logprob ?? 0,
        no_speech_prob: sg.no_speech_prob ?? 0,
      }));
      const speakers = S.speakers.map((sp, i) => ({
        name: sp.name || '', key: sp.key || `s${i}`, color: sp.color ?? i,
        count: sp.count || 0, seconds: sp.seconds || 0,
        known: !!sp.known, similarity: sp.similarity || 0,
      }));
      const res = await fetch(`/api/export/${S.jobId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          format,
          conclusion: $('#summaryText').value,
          title: S.title || 'Acta de reunión',
          segments, speakers,
        }),
      });
      if (!res.ok) {
        let msg = `Error ${res.status}`;
        try { msg = (await res.json()).detail || msg; } catch (_) { /* noop */ }
        throw new Error(msg);
      }
      const blob = await res.blob();
      const disp = res.headers.get('content-disposition') || '';
      const match = /filename\*=UTF-8''([^;]+)/i.exec(disp) || /filename="?([^";]+)"?/i.exec(disp);
      const name = match ? decodeURIComponent(match[1]) : `acta.${format}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      toast(`Descargado ${name} · con tus correcciones`, 'ok', 'Exportación');
      announce(`Archivo ${name} descargado`);
    } catch (err) {
      toast(err.message, 'err', 'No se pudo exportar');
    } finally {
      btn.forEach(b => { b.disabled = false; });
    }
  }

  /* ------------------------------ SESIÓN -------------------------------- */
  let sessionPayload = {};
  const flushSession = debounce(async () => {
    if (!S.jobId) return;
    const payload = sessionPayload;
    sessionPayload = {};
    try {
      await api(`/api/session/${S.sessionId}/save`, {
        method: 'POST',
        body: JSON.stringify({ job_id: S.jobId, title: S.title, ...payload }),
      });
    } catch (_) { /* guardado silencioso */ }
  }, 900);
  function saveSession(payload) {
    Object.assign(sessionPayload, payload || {});
    flushSession();
  }

  /* ------------------------------ GRABADORA ----------------------------- */
  const R = {
    rec: null, stream: null, chunks: [], ctx: null, analyser: null, src: null,
    raf: 0, timer: null, elapsed: 0, paused: false, mime: '', unloadHandler: null,
  };

  async function openRecorder() {
    trapFocus($('#recorderModal'));
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      toast('Este navegador no soporta grabación de audio (usa Chrome o Edge).', 'err', 'No disponible');
      return;
    }
    $('#recorderModal').hidden = false;
    $('#recTimer').textContent = '00:00:00';
    $('#recStatus').textContent = 'Pulsa «Iniciar grabación» para comenzar.';
    drawRecCanvas(null);
    setVu(0);
    await listMics();
  }

  async function listMics() {
    const sel = $('#micSelect');
    try {
      let devices = await navigator.mediaDevices.enumerateDevices();
      let mics = devices.filter(d => d.kind === 'audioinput');
      if (!mics.length || mics.every(m => !m.label)) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach(t => t.stop());
        devices = await navigator.mediaDevices.enumerateDevices();
        mics = devices.filter(d => d.kind === 'audioinput');
      }
      sel.innerHTML = mics.length
        ? mics.map((d, i) => `<option value="${esc(d.deviceId)}">${esc(d.label || 'Micrófono ' + (i + 1))}</option>`).join('')
        : '<option value="">Micrófono predeterminado</option>';
    } catch (err) {
      sel.innerHTML = '<option value="">Micrófono predeterminado</option>';
      $('#recStatus').textContent = 'No se pudo enumerar micrófonos: ' + (err.message || err);
    }
  }

  async function startRecording() {
    try {
      const deviceId = $('#micSelect').value;
      const constraints = {
        audio: Object.assign(
          { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          deviceId ? { deviceId: { exact: deviceId } } : {}
        ),
      };
      R.stream = await navigator.mediaDevices.getUserMedia(constraints);
      R.mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4', '']
        .find(m => !m || (window.MediaRecorder && MediaRecorder.isTypeSupported(m))) || '';
      R.rec = R.mime ? new MediaRecorder(R.stream, { mimeType: R.mime }) : new MediaRecorder(R.stream);
      R.chunks = [];
      R.elapsed = 0;
      R.paused = false;
      R.rec.ondataavailable = (e) => { if (e.data && e.data.size) R.chunks.push(e.data); };
      R.rec.onstop = finalizeRecording;
      R.rec.start(1000);

      R.timer = setInterval(() => {
        if (!R.paused) R.elapsed++;
        $('#recTimer').textContent = fmtTime(R.elapsed, true);
        document.title = `${R.paused ? '⏸' : '⏺'} ${fmtTime(R.elapsed, true)} · Grabando — Transcriptor de Audio`;
      }, 1000);

      R.unloadHandler = (e) => {
        e.preventDefault();
        e.returnValue = 'Hay una grabación en curso. ¿Seguro que quieres salir?';
        return e.returnValue;
      };
      window.addEventListener('beforeunload', R.unloadHandler);

      startVisualizer(R.stream);
      $('#btnRecStart').hidden = true;
      $('#btnRecPause').hidden = false;
      $('#btnRecPause').innerHTML = icon('pause') + ' Pausar';
      $('#btnRecStop').hidden = false;
      $('#btnRecClose').disabled = true;
      $('#micSelect').disabled = true;
      $('#recStatus').textContent = 'Grabando… habla con claridad cerca del micrófono.';
      $('#vuBars').classList.add('recording');
      toast('Grabación iniciada.', 'ok');
    } catch (err) {
      toast('No se pudo acceder al micrófono: ' + (err.message || err), 'err', 'Permiso denegado');
      $('#recStatus').textContent = 'Sin acceso al micrófono. Revisa los permisos del navegador.';
    }
  }

  function togglePauseRecording() {
    if (!R.rec) return;
    if (R.rec.state === 'recording') {
      R.rec.pause();
      R.paused = true;
      $('#btnRecPause').textContent = '▶ Reanudar';
      $('#recStatus').textContent = 'Grabación en pausa.';
      $('#vuBars').classList.remove('recording');
      document.title = `⏸ ${fmtTime(R.elapsed, true)} · En pausa — Transcriptor de Audio`;
    } else if (R.rec.state === 'paused') {
      R.rec.resume();
      R.paused = false;
      $('#btnRecPause').innerHTML = icon('pause') + ' Pausar';
      $('#recStatus').textContent = 'Grabando…';
      $('#vuBars').classList.add('recording');
    }
  }

  function stopRecording() {
    if (!R.rec) return;
    $('#recStatus').textContent = 'Procesando la grabación…';
    try { R.rec.stop(); } catch (_) { finalizeRecording(); }
  }

  function cleanupRecorder() {
    clearInterval(R.timer); R.timer = null;
    if (R.raf) cancelAnimationFrame(R.raf); R.raf = 0;
    if (R.stream) R.stream.getTracks().forEach(t => t.stop());
    R.stream = null;
    if (R.ctx) { R.ctx.close().catch(() => {}); R.ctx = null; }
    R.analyser = null; R.src = null;
    if (R.unloadHandler) {
      window.removeEventListener('beforeunload', R.unloadHandler);
      R.unloadHandler = null;
    }
    document.title = 'Transcriptor de Audio y Generador de Actas';
    $('#btnRecStart').hidden = false;
    $('#btnRecPause').hidden = true;
    $('#btnRecStop').hidden = true;
    $('#btnRecClose').disabled = false;
    $('#micSelect').disabled = false;
    $('#vuBars').classList.remove('recording');
    setVu(0);
  }

  function finalizeRecording() {
    const chunks = R.chunks.slice();
    const mime = R.mime || 'audio/webm';
    cleanupRecorder();
    R.rec = null;
    if (!chunks.length) {
      toast('La grabación quedó vacía.', 'warn');
      $('#recorderModal').hidden = true;
      return;
    }
    const ext = mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm';
    const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
    const blob = new Blob(chunks, { type: mime });
    const file = new File([blob], `reunion_${stamp}.${ext}`, { type: mime });
    $('#recorderModal').hidden = true;
    toast(`${fmtTime(R.elapsed, true)} grabados · ${fmtBytes(file.size)}. Subiendo…`, 'ok', 'Grabación');
    uploadFile(file);
  }

  function cancelRecording() {
    if (R.rec && R.rec.state !== 'inactive') {
      R.rec.onstop = null;
      try { R.rec.stop(); } catch (_) { /* noop */ }
    }
    R.chunks = [];
    cleanupRecorder();
    R.rec = null;
    $('#recorderModal').hidden = true;
    $('#recStatus').textContent = 'Pulsa «Iniciar grabación» para comenzar.';
    toast('Grabación descartada.', 'warn');
  }

  function startVisualizer(stream) {
    try {
      R.ctx = new (window.AudioContext || window.webkitAudioContext)();
      R.src = R.ctx.createMediaStreamSource(stream);
      R.analyser = R.ctx.createAnalyser();
      R.analyser.fftSize = 2048;
      R.analyser.smoothingTimeConstant = 0.75;
      R.src.connect(R.analyser);
    } catch (err) {
      return;
    }
    const time = new Uint8Array(R.analyser.fftSize);
    const freq = new Uint8Array(R.analyser.frequencyBinCount);
    const canvas = $('#recCanvas');
    const cctx = canvas.getContext('2d');

    const loop = () => {
      R.raf = requestAnimationFrame(loop);
      if (!R.analyser) return;
      R.analyser.getByteTimeDomainData(time);
      R.analyser.getByteFrequencyData(freq);
      drawRecCanvas(time, cctx, canvas);
      // Vúmetro de 12 barras por bandas de frecuencia
      const bars = $$('#vuBars span');
      const bands = 12, step = Math.floor(freq.length / bands) || 1;
      let sum = 0;
      bars.forEach((bar, i) => {
        let acc = 0;
        for (let k = 0; k < step; k++) acc += freq[i * step + k] || 0;
        const level = Math.min(1, (acc / step) / 170);
        sum += level;
        bar.style.height = Math.max(6, level * 100) + '%';
        bar.style.opacity = String(0.45 + level * 0.55);
      });
      $('#vuBars').dataset.level = String(sum / bands);
    };
    loop();
  }

  function setVu(level) {
    $$('#vuBars span').forEach(b => { b.style.height = Math.max(6, level * 100) + '%'; });
  }

  function drawRecCanvas(data, cctx, canvas) {
    const ctx2 = cctx || $('#recCanvas').getContext('2d');
    const cv = canvas || $('#recCanvas');
    const W = cv.width, H = cv.height;
    ctx2.clearRect(0, 0, W, H);
    // Rejilla sutil
    ctx2.strokeStyle = 'rgba(139,148,158,.18)';
    ctx2.lineWidth = 1;
    for (let y = 0; y <= H; y += H / 4) {
      ctx2.beginPath(); ctx2.moveTo(0, y); ctx2.lineTo(W, y); ctx2.stroke();
    }
    if (!data) {
      ctx2.strokeStyle = 'rgba(139,148,158,.55)';
      ctx2.lineWidth = 2;
      ctx2.beginPath(); ctx2.moveTo(0, H / 2); ctx2.lineTo(W, H / 2); ctx2.stroke();
      return;
    }
    const grad = ctx2.createLinearGradient(0, 0, W, 0);
    grad.addColorStop(0, '#2f81f7');
    grad.addColorStop(0.5, '#a371f7');
    grad.addColorStop(1, '#3fb950');
    ctx2.strokeStyle = grad;
    ctx2.lineWidth = 2.2;
    ctx2.beginPath();
    const step = Math.max(1, Math.floor(data.length / W));
    for (let x = 0; x < W; x++) {
      const v = (data[x * step] || 128) / 128;
      const y = H / 2 + (v - 1) * (H / 2) * 0.92;
      x === 0 ? ctx2.moveTo(x, y) : ctx2.lineTo(x, y);
    }
    ctx2.stroke();
    // Punto brillante al final
    ctx2.fillStyle = 'rgba(163,113,247,.9)';
    ctx2.beginPath(); ctx2.arc(W - 3, H / 2, 3, 0, Math.PI * 2); ctx2.fill();
  }

  /* ------------------------------- HISTORIAL ---------------------------- */
  /* ------------------------------ HISTORIAL ----------------------------- */
  const HIST = { data: null, q: '', filtro: 'all', orden: 'recientes' };

  async function openHistory() {
    trapFocus($('#historyDrawer'));
    $('#historyDrawer').hidden = false;
    $('#scrim').hidden = false;
    $('#historyList').innerHTML = '<p class="muted small">Cargando…</p>';
    await recargarHistorial();
  }

  async function recargarHistorial() {
    try {
      HIST.data = await api('/api/historial');
      renderHistoryList();
    } catch (err) {
      $('#historyList').innerHTML = `<p class="muted small">No se pudo cargar el historial: ${esc(err.message)}</p>`;
    }
  }

  function historialFiltrado() {
    const datos = HIST.data || { entries: [] };
    const q = HIST.q.trim().toLowerCase();
    let lista = (datos.entries || []).slice();
    if (HIST.filtro === 'acta') lista = lista.filter(e => e.has_acta);
    if (HIST.filtro === 'sin_acta') lista = lista.filter(e => !e.has_acta);
    if (HIST.filtro === 'pendientes') lista = lista.filter(e => (e.pendientes_n || 0) > 0);
    if (HIST.filtro === 'protegidas') lista = lista.filter(e => e.protegido);
    if (q) {
      lista = lista.filter(e => [e.title, e.filename, e.serie, e.acta_resumen, e.language]
        .filter(Boolean).join(' ').toLowerCase().includes(q));
    }
    const orden = HIST.orden;
    lista.sort((a, b) => {
      if (orden === 'antiguas') return String(a.created || '').localeCompare(String(b.created || ''));
      if (orden === 'largas') return (b.duration || 0) - (a.duration || 0);
      if (orden === 'titulo') return String(a.title || '').localeCompare(String(b.title || ''));
      return String(b.created || '').localeCompare(String(a.created || ''));
    });
    return lista;
  }

  function renderHistoryList() {
    const datos = HIST.data || { entries: [] };
    const lista = historialFiltrado();
    const foot = $('#historyFoot');
    foot.textContent = `${lista.length} de ${datos.count || 0} reuniones · `
      + `${fmtBytes(datos.bytes || 0)} de ${datos.max_mb || 0} MB de audio comprimido`;
    const nota = $('#historyRetention');
    if (nota) {
      const proximos = datos.proximos_a_borrar || [];
      if (proximos.length) {
        nota.hidden = false;
        nota.innerHTML = `<strong>Retención:</strong> al llegar al límite se borrarán `
          + proximos.map(p => `<em>${esc(p.title || p.job_id)}</em>`).join(', ')
          + `. Marca «proteger» en las que no quieras perder.`;
      } else {
        nota.hidden = true;
      }
    }
    if (!lista.length) {
      $('#historyList').innerHTML = `<p class="muted small">${(datos.count || 0)
        ? 'Ninguna reunión coincide con la búsqueda o el filtro.'
        : 'Aún no hay reuniones. Cuando termines una transcripción aparecerá aquí con su audio comprimido en .m4a.'}</p>`;
      return;
    }
    $('#historyList').innerHTML = lista.map(e => {
      const chips = [];
      if (e.has_acta) chips.push(`<span class="chip chip-ok">Acta${e.acta_version > 1 ? ' v' + esc(String(e.acta_version)) : ''}</span>`);
      else chips.push('<span class="chip chip-muted">Sin acta</span>');
      if (e.acuerdos_n) chips.push(`<span class="chip">${esc(String(e.acuerdos_n))} acuerdos</span>`);
      if (e.pendientes_n) chips.push(`<span class="chip chip-warn">${esc(String(e.pendientes_n))} pendientes</span>`);
      if (e.serie) chips.push(`<span class="chip">${esc(e.serie)}</span>`);
      if (e.protegido) chips.push('<span class="chip chip-lock">Protegida</span>');
      return `
      <div class="history-item${e.protegido ? ' is-protected' : ''}" data-id="${esc(e.job_id)}">
        <div class="hi-main">
          <h3 class="ellipsis" title="${esc(e.title || e.filename)}">${esc(e.title || e.filename || 'Reunión')}</h3>
          <div class="hi-meta">
            ${esc((e.created || '').replace('T', ' ').slice(0, 16))} ·
            ${esc(e.duration_human || fmtTime(e.duration))} ·
            ${esc(String(e.speakers || 0))} hablantes · ${fmtNum(e.words || 0)} palabras
            ${e.media_bytes ? ' · ' + fmtBytes(e.media_bytes) : ''}
          </div>
          <div class="hi-chips">${chips.join('')}</div>
        </div>
        <div class="hi-actions">
          <button class="icon-only" data-act="open" title="Abrir la reunión">${icon('folder', 'icon icon-sm')}</button>
          <button class="icon-only" data-act="docx" title="Descargar el acta en Word"${e.has_acta ? '' : ' disabled'}>${icon('download', 'icon icon-sm')}</button>
          <button class="icon-only" data-act="dup" title="Duplicar el acta como base editable"${e.has_acta ? '' : ' disabled'}>${icon('copy', 'icon icon-sm')}</button>
          <button class="icon-only" data-act="rename" title="Renombrar">${icon('pencil', 'icon icon-sm')}</button>
          <button class="icon-only" data-act="serie" title="Etiquetar con una serie">${icon('pin', 'icon icon-sm')}</button>
          <button class="icon-only${e.protegido ? ' is-on' : ''}" data-act="protect" title="${e.protegido ? 'Dejar de proteger' : 'Proteger (no se borrará)'}">${icon(e.protegido ? 'lock' : 'check', 'icon icon-sm')}</button>
          <button class="icon-only" data-act="del" title="Eliminar del historial">${icon('trash', 'icon icon-sm')}</button>
        </div>
      </div>`;
    }).join('');

    $$('.history-item', $('#historyList')).forEach(item => {
      const id = item.dataset.id;
      item.addEventListener('click', async (ev) => {
        const btn = ev.target.closest('[data-act]');
        const act = btn && btn.dataset.act;
        if (!act || act === 'open') {
          closeHistory();
          await loadResult(id);
          toast('Reunión cargada desde el historial.', 'ok');
          return;
        }
        ev.stopPropagation();
        if (act === 'docx') {
          descargarExport(id, 'acta');
          return;
        }
        if (act === 'dup') {
          const entrada = (HIST.data.entries || []).find(x => x.job_id === id) || {};
          const nuevo = prompt('Nombre de la copia (útil para reuniones periódicas):',
            `${entrada.title || 'Reunión'} (copia)`);
          if (nuevo === null) return;
          try {
            const res = await api(`/api/historial/${id}/duplicar`, {
              method: 'POST', body: JSON.stringify({ title: nuevo.trim() }),
            });
            toast('Copia creada: ábrela para actualizar el acta.', 'ok', 'Acta duplicada');
            await recargarHistorial();
            closeHistory();
            await loadResult(res.job_id);
          } catch (err) { toast(err.message, 'err', 'No se pudo duplicar'); }
          return;
        }
        if (act === 'rename') {
          const actual = item.querySelector('h3').textContent.trim();
          const nuevo = prompt('Nuevo nombre de la reunión:', actual);
          if (!nuevo || !nuevo.trim() || nuevo.trim() === actual) return;
          try {
            await api(`/api/historial/${id}`, { method: 'POST', body: JSON.stringify({ title: nuevo.trim() }) });
            toast('Nombre actualizado.', 'ok');
            await recargarHistorial();
          } catch (err) { toast(err.message, 'err'); }
          return;
        }
        if (act === 'serie') {
          const entrada = (HIST.data.entries || []).find(x => x.job_id === id) || {};
          const serie = prompt('Serie o carpeta (por ejemplo «Junta Directiva 2026»):', entrada.serie || '');
          if (serie === null) return;
          try {
            await api(`/api/historial/${id}`, { method: 'POST', body: JSON.stringify({ serie: serie.trim() }) });
            toast(serie.trim() ? `Etiquetada como «${serie.trim()}».` : 'Etiqueta quitada.', 'ok');
            await recargarHistorial();
          } catch (err) { toast(err.message, 'err'); }
          return;
        }
        if (act === 'protect') {
          const entrada = (HIST.data.entries || []).find(x => x.job_id === id) || {};
          try {
            await api(`/api/historial/${id}`, { method: 'POST', body: JSON.stringify({ protegido: !entrada.protegido }) });
            toast(entrada.protegido ? 'Ya se puede borrar automáticamente.' : 'Protegida: no se borrará automáticamente.', 'ok');
            await recargarHistorial();
          } catch (err) { toast(err.message, 'err'); }
          return;
        }
        if (act === 'del') {
          const entrada = (HIST.data.entries || []).find(x => x.job_id === id) || {};
          const detalle = ['el audio comprimido', 'la transcripción', 'el acta y sus versiones']
            .filter((_, i) => i === 0 ? !!entrada.media_bytes : true);
          if (!confirm(`¿Eliminar «${entrada.title || 'esta reunión'}»?\n\nSe borrarán ${detalle.join(', ')}.`)) return;
          try {
            await api(`/api/historial/${id}`, { method: 'DELETE' });
            toast('Reunión eliminada del historial.', 'ok');
            await recargarHistorial();
          } catch (err) { toast(err.message, 'err'); }
        }
      });
    });
  }

  function closeHistory() {
    $('#historyDrawer').hidden = true;
    if ($('#compromisosDrawer').hidden) $('#scrim').hidden = true;
  }

  /* ---------------------- COMPROMISOS (todas las reuniones) -------------- */
  const COMP = { items: [], q: '', filtro: 'pendientes' };

  async function openCompromisos() {
    trapFocus($('#compromisosDrawer'));
    $('#compromisosDrawer').hidden = false;
    $('#scrim').hidden = false;
    $('#compromisosList').innerHTML = '<p class="muted small">Cargando…</p>';
    await recargarCompromisos();
  }

  function closeCompromisos() {
    const d = $('#compromisosDrawer');
    if (d) d.hidden = true;
    if ($('#historyDrawer').hidden) $('#scrim').hidden = true;
  }

  async function recargarCompromisos() {
    try {
      const data = await api('/api/compromisos');
      COMP.items = data.items || [];
      renderCompromisos();
    } catch (err) {
      $('#compromisosList').innerHTML = `<p class="muted small">No se pudieron cargar los compromisos: ${esc(err.message)}</p>`;
    }
  }

  const esHecho = (estatus) => ['hecho', 'cumplido', 'cerrado', 'completado']
    .includes(String(estatus || '').toLowerCase());

  function renderCompromisos() {
    const lista = $('#compromisosList');
    const q = COMP.q.trim().toLowerCase();
    let items = COMP.items.slice();
    if (COMP.filtro === 'pendientes') items = items.filter(i => !esHecho(i.estatus));
    if (COMP.filtro === 'hechos') items = items.filter(i => esHecho(i.estatus));
    if (COMP.filtro === 'vencidos') items = items.filter(i => !esHecho(i.estatus) && i.fecha);
    if (q) {
      items = items.filter(i => [i.texto, i.responsable, i.reunion, i.fecha]
        .filter(Boolean).join(' ').toLowerCase().includes(q));
    }
    const pendientes = COMP.items.filter(i => !esHecho(i.estatus)).length;
    $('#compromisosFoot').textContent = `${items.length} de ${COMP.items.length} compromisos · ${pendientes} sin cumplir`;
    if (!items.length) {
      lista.innerHTML = '<p class="muted small">Nada que mostrar con este filtro. Genera un acta para que aparezcan sus acuerdos.</p>';
      return;
    }
    let ultimaReunion = '';
    lista.innerHTML = items.map(i => {
      const cabecera = i.reunion !== ultimaReunion
        ? `<div class="comp-group">${esc(i.reunion || i.job_id)}<span class="small muted"> · ${esc((i.fecha_reunion || '').replace('T', ' ').slice(0, 10))}${i.serie ? ' · ' + esc(i.serie) : ''}</span></div>`
        : '';
      ultimaReunion = i.reunion;
      return cabecera + `
      <div class="comp-item${esHecho(i.estatus) ? ' is-done' : ''}" data-job="${esc(i.job_id)}" data-tipo="${esc(i.tipo)}" data-idx="${esc(String(i.idx))}">
        <div class="comp-text">${esc(i.texto.replace(/\[\d{1,2}:\d{2}(?::\d{2})?\]/g, '').trim())}</div>
        <div class="comp-fields">
          <label class="comp-field"><span>Responsable</span>
            <input class="cell-input" data-campo="responsable" value="${esc(i.responsable || '')}" placeholder="—"></label>
          <label class="comp-field"><span>Fecha</span>
            <input class="cell-input" data-campo="fecha" value="${esc(i.fecha || '')}" placeholder="—"></label>
          <label class="comp-field"><span>Estatus</span>
            <input class="cell-input" data-campo="estatus" value="${esc(i.estatus || 'pendiente')}" placeholder="pendiente"></label>
        </div>
        <div class="comp-actions">
          ${i.minuto ? `<button type="button" class="link-btn" data-seek="${esc(i.minuto)}">${esc(i.minuto)}</button>` : ''}
          <button type="button" class="link-btn" data-open="${esc(i.job_id)}">Abrir reunión</button>
          <button type="button" class="link-btn" data-hecho="${esc(i.job_id)}">Marcar ${esHecho(i.estatus) ? 'pendiente' : 'cumplido'}</button>
        </div>
      </div>`;
    }).join('');

    $$('#compromisosList .cell-input').forEach(inp => {
      inp.addEventListener('change', async () => {
        const caja = inp.closest('.comp-item');
        try {
          await api(`/api/compromisos/${caja.dataset.job}`, {
            method: 'PATCH',
            body: JSON.stringify({
              tipo: caja.dataset.tipo, idx: Number(caja.dataset.idx),
              campo: inp.dataset.campo, valor: inp.value,
            }),
          });
          inp.classList.add('is-saved');
          setTimeout(() => inp.classList.remove('is-saved'), 1200);
          const it = COMP.items.find(x => x.job_id === caja.dataset.job
            && x.tipo === caja.dataset.tipo && String(x.idx) === caja.dataset.idx);
          if (it) it[inp.dataset.campo] = inp.value;
        } catch (err) { toast(err.message, 'err', 'No se pudo guardar'); }
      });
      inp.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') inp.blur(); });
    });
    $$('#compromisosList [data-seek]').forEach(b => b.addEventListener('click', () => {
      irAlMinuto(b.dataset.seek);
    }));
    $$('#compromisosList [data-open]').forEach(b => b.addEventListener('click', async () => {
      closeCompromisos();
      await loadResult(b.dataset.open);
    }));
    $$('#compromisosList [data-hecho]').forEach(b => b.addEventListener('click', async () => {
      const caja = b.closest('.comp-item');
      const it = COMP.items.find(x => x.job_id === caja.dataset.job
        && x.tipo === caja.dataset.tipo && String(x.idx) === caja.dataset.idx);
      const nuevo = esHecho(it && it.estatus) ? 'pendiente' : 'cumplido';
      try {
        await api(`/api/compromisos/${caja.dataset.job}`, {
          method: 'PATCH',
          body: JSON.stringify({ tipo: caja.dataset.tipo, idx: Number(caja.dataset.idx),
                                 campo: 'estatus', valor: nuevo }),
        });
        if (it) it.estatus = nuevo;
        renderCompromisos();
        toast(`Marcado como ${nuevo}.`, 'ok', 'Compromisos');
      } catch (err) { toast(err.message, 'err'); }
    }));
  }

  /** Descarga una exportación de otra reunión sin abrirla. */
  async function descargarExport(jobId, formato) {
    try {
      const res = await fetch(`/api/export/${jobId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ format: formato }),
      });
      if (!res.ok) {
        let msg = `Error ${res.status}`;
        try { msg = (await res.json()).detail || msg; } catch (_) { /* noop */ }
        throw new Error(msg);
      }
      const blob = await res.blob();
      const disp = res.headers.get('content-disposition') || '';
      const match = /filename\*=UTF-8''([^;]+)/i.exec(disp) || /filename="?([^";]+)"?/i.exec(disp);
      const name = match ? decodeURIComponent(match[1]) : `acta.docx`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      toast(`Descargado ${name}`, 'ok', 'Acta');
    } catch (err) {
      toast(err.message, 'err', 'No se pudo descargar');
    }
  }

  /* ------------------------------- CANCELAR ----------------------------- */
  async function cancelCurrent() {
    if (!S.jobId) return;
    try {
      await api(`/api/jobs/${S.jobId}/cancel`, { method: 'POST' });
      toast('Cancelación solicitada…', 'warn');
    } catch (err) { toast(err.message, 'err'); }
  }

  async function cancelSession() {
    try {
      const res = await api(`/api/session/${S.sessionId}/cancel`, { method: 'POST' });
      toast(res.message, res.cancelled ? 'warn' : 'info');
    } catch (err) { toast(err.message, 'err'); }
  }

  /* ------------------------------- COPIAR ------------------------------- */
  function transcriptText() {
    const withTs = $('#optTimestamps').checked;
    return S.segments.map(s => {
      const sp = (s.speaker || '').trim();
      return `${withTs ? `[${fmtTime(s.start, true)}] ` : ''}${sp ? sp + ': ' : ''}${s.text}`;
    }).join('\n');
  }

  async function copyTranscript() {
    const text = transcriptText();
    try {
      await navigator.clipboard.writeText(text);
      toast(`${fmtNum(text.length)} caracteres copiados al portapapeles.`, 'ok');
    } catch (_) {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
      toast('Transcripción copiada.', 'ok');
    }
  }

  /* ---------------------- stepper del flujo de trabajo ------------------- */
  /**
   * Vuelve a la página principal (pantalla de carga) para transcribir otra
   * reunión. La reunión abierta queda guardada en el historial.
   *
   * Es deliberadamente defensiva: primero cambia la pantalla y después limpia el
   * estado, cada paso protegido, para que un fallo parcial nunca deje el botón
   * «sin hacer nada». No usa confirm(): en un iframe con sandbox los diálogos se
   * ignoran y el clic se perdería en silencio.
   */
  function irAlInicio() {
    const enCurso = !!(S.job && ['queued', 'preprocessing', 'transcribing', 'diarizing']
      .includes(S.job.status));

    // 1) La pantalla primero (esto es lo que el usuario ve)
    const resultados = $('#results');
    const vacio = $('#emptyState');
    if (resultados) resultados.hidden = true;
    if (vacio) vacio.hidden = false;
    const reproductor = $('#playerBar');
    if (reproductor) reproductor.hidden = true;
    const panelTrabajo = $('#jobPanel');
    if (panelTrabajo) panelTrabajo.hidden = true;
    try { setStep(1); } catch (_) { /* la pantalla ya cambió */ }
    try { window.scrollTo({ top: 0, behavior: 'smooth' }); } catch (_) { /* noop */ }

    // 2) Limpieza del estado, paso a paso y sin abortar el resto
    const pasos = [
      () => stopPolling(),
      () => {
        const audio = $('#audio');
        if (audio) { audio.pause(); audio.removeAttribute('src'); audio.load(); }
        resetLive();
      },
      () => {
        if (S.summaryTimer) { clearInterval(S.summaryTimer); S.summaryTimer = null; }
        if (S.streamAbort) { S.streamAbort.abort(); S.streamAbort = null; }
      },
      () => {
        S.jobId = ''; S.job = null; S.segments = []; S.speakers = [];
        S.originalSegments = []; S.originalSpeakers = []; S.undoStack = [];
        S.selection.clear(); S.selecting = false; S.mediaUrl = '';
        S.title = ''; S.info = {}; S.stats = {}; S.wave = null;
        store.del('tae_last_job');
      },
      () => {
        ['#duplicadoNote', '#partesNote'].forEach(sel => {
          const el = $(sel); if (el) el.hidden = true;
        });
        const resumen = $('#summaryText');
        if (resumen) resumen.value = '';
        updateSummaryCount();
        renderActaPreview();
        const cuerpo = $('#acuerdosBody');
        if (cuerpo) cuerpo.innerHTML = '';
        const acuerdos = $('#acuerdosWrap');
        if (acuerdos) acuerdos.hidden = true;
        const figuras = $('#figuresPanel');
        if (figuras) figuras.hidden = true;
        const versiones = $('#actaVersionSelect');
        if (versiones) versiones.innerHTML = '';
        const aviso = $('#actaDesactualizada');
        if (aviso) aviso.remove();
      },
      () => clearFile(),
    ];
    pasos.forEach(paso => { try { paso(); } catch (err) { console.warn('[inicio]', err); } });

    if (enCurso) {
      toast('La transcripción sigue en segundo plano: cuando termine estará en el Historial.',
        'info', 'Volviste al inicio');
    }
  }

  function setStep(n) {
    $$('#stepper .step').forEach(li => {
      const k = Number(li.dataset.step);
      li.classList.toggle('is-active', k === n);
      li.classList.toggle('is-done', k < n);
    });
  }

  /* -------------------------------- EVENTOS ----------------------------- */
  /* ══════════════════════ FORMA DE ONDA Y CAPÍTULOS ══════════════════════ */
  function currentDuration() {
    const audio = S.audio || $('#audio');
    return (audio && isFinite(audio.duration) && audio.duration > 0)
      ? audio.duration
      : (S.job && S.job.duration) || S.info.duration || (S.wave && S.wave.duration) || 0;
  }

  function colorAtTime(t) {
    if (!S.segments.length) return resolveColor('var(--muted)');
    const idx = findSegmentAt(t);
    const seg = S.segments[idx];
    if (!seg) return resolveColor('var(--muted)');
    const si = seg.si >= 0 ? seg.si : S.speakers.findIndex(sp => sp.name === (seg.speaker || ''));
    return si >= 0 ? resolveColor(colorFor(si)) : resolveColor('var(--muted)');
  }

  async function loadWaveform() {
    if (!S.jobId) return;
    S.wave = null;
    drawWaveform();
    try {
      const data = await api(`/api/jobs/${S.jobId}/peaks?buckets=900`);
      S.wave = { peaks: data.peaks || [], duration: data.duration || currentDuration() };
    } catch (_) {
      S.wave = null;
    }
    drawWaveform();
  }

  function drawWaveform() {
    const canvas = $('#waveCanvas');
    if (!canvas) return;
    const wrap = canvas.parentElement;
    const w = Math.max(120, wrap.clientWidth || 600);
    const h = 42;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const peaks = (S.wave && S.wave.peaks) || [];
    const dur = (S.wave && S.wave.duration) || currentDuration();
    if (!peaks.length) {
      ctx.fillStyle = 'rgba(139,148,158,.35)';
      ctx.font = '11px system-ui, sans-serif';
      ctx.fillText(S.jobId ? 'Calculando forma de onda…' : '', 10, h / 2 + 4);
      return;
    }
    /* Si hay más muestras que píxeles, agrupamos por columnas (máximo de cada
       grupo): la onda se ve limpia en vez de ruidosa. */
    const group = Math.max(1, Math.floor(peaks.length / Math.max(40, Math.floor(w / 2.5))));
    const n = Math.ceil(peaks.length / group);
    const barW = w / n;
    const radius = Math.min(1.5, barW / 2);
    // Normalizamos contra el pico real (y suavizamos con raíz) para que la onda
    // ocupe el alto disponible en vez de quedarse en puntitos.
    let maxPeak = 0;
    for (let i = 0; i < peaks.length; i += 1) if ((peaks[i] || 0) > maxPeak) maxPeak = peaks[i];
    const norm = maxPeak > 0.02 ? 1 / maxPeak : 1;
    for (let i = 0; i < n; i += 1) {
      let amp = 0;
      for (let k = 0; k < group; k += 1) amp = Math.max(amp, peaks[i * group + k] || 0);
      amp = Math.pow(amp * norm, 0.72);
      const t = ((i + 0.5) / n) * dur;
      const hh = Math.max(2.5, amp * (h - 6));
      const y = (h - hh) / 2;
      const bw = Math.max(1.2, barW - 0.6);
      ctx.fillStyle = colorAtTime(t);
      ctx.globalAlpha = 0.9;
      if (ctx.roundRect) {
        ctx.beginPath();
        ctx.roundRect(i * barW, y, bw, hh, radius);
        ctx.fill();
      } else {
        ctx.fillRect(i * barW, y, bw, hh);
      }
    }
    ctx.globalAlpha = 1;
  }

  function updateWaveCursor() {
    const cursor = $('#waveCursor');
    const wrap = $('#waveWrap');
    if (!cursor || !wrap || !S.wave) return;
    const dur = currentDuration();
    const audio = S.audio || $('#audio');
    if (!dur || !audio) { cursor.hidden = true; return; }
    cursor.hidden = false;
    cursor.style.left = `${Math.min(100, (audio.currentTime / dur) * 100)}%`;
  }

  function buildChapters() {
    const bar = $('#chapterBar');
    if (!bar) return;
    S.chapters = [];
    if (!S.segments.length) { bar.innerHTML = ''; return; }
    const GAP = 90;         // silencio largo ⇒ posible cambio de tema
    const MAX_SPAN = 480;   // y como máximo 8 minutos por capítulo
    let cur = { start: S.segments[0].start, text: S.segments[0].text || '' };
    S.segments.forEach((seg) => {
      const gap = Number(seg.start) - (Number(cur.last) || cur.start);
      const span = Number(seg.start) - cur.start;
      if (gap > GAP || span > MAX_SPAN) {
        S.chapters.push(cur);
        cur = { start: seg.start, text: seg.text || '' };
      }
      cur.last = seg.end;
    });
    S.chapters.push(cur);
    S.chapters = S.chapters.slice(0, 60);
    bar.innerHTML = S.chapters.map((c, i) => {
      const label = (c.text || '').replace(/\s+/g, ' ').trim().slice(0, 46) || 'Continuación';
      return `<button type="button" class="chapter-chip" data-start="${c.start}" data-i="${i}"
        title="${esc(label)} · ${fmtTime(c.start, true)}">
        <span class="mono">${fmtTime(c.start)}</span><span class="ch-label">${esc(label)}</span>
      </button>`;
    }).join('');
    $$('.chapter-chip', bar).forEach(chip => chip.addEventListener('click', () => {
      seekTo(Number(chip.dataset.start) + 0.01);
      const idx = findSegmentAt(Number(chip.dataset.start));
      if (idx >= 0) setActive(idx, true);
    }));
  }

  /* Menú desplegable del índice de capítulos (antes ocupaba una fila del
     reproductor; ahora vive en la cabecera de la transcripción). */
  function initTocMenu() {
    const btn = $('#btnToc');
    const panel = $('#tocPanel');
    if (!btn || !panel) return;
    const close = () => {
      panel.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
      $('#tocWrap')?.classList.remove('open');
    };
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = panel.hidden;
      panel.hidden = !willOpen;
      btn.setAttribute('aria-expanded', String(willOpen));
      $('#tocWrap')?.classList.toggle('open', willOpen);
      if (willOpen) {
        const active = panel.querySelector('.chapter-chip.is-active');
        if (active) active.scrollIntoView({ block: 'nearest' });
      }
    });
    document.addEventListener('click', (e) => {
      if (panel.hidden) return;
      if (!e.target.closest('#tocWrap')) close();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !panel.hidden) { close(); btn.focus(); }
    });
    panel.addEventListener('click', (e) => {
      if (e.target.closest('.chapter-chip')) close();
    });
  }

  function markChapter(t) {
    const i = S.chapters.reduce((acc, c, k) => (t >= c.start ? k : acc), -1);
    $$('.chapter-chip').forEach((chip, k) => chip.classList.toggle('is-active', k === i));
  }

  /* ══════════════════ AVANCE EN VIVO DURANTE LA TRANSCRIPCIÓN ═════════════ */
  let partialBusy = false;
  function resetLive() {
    S.partialSince = 0;
    S.partialCount = 0;
    const body = $('#liveBody');
    if (body) body.innerHTML = '';
    const box = $('#liveTranscript');
    if (box) box.hidden = true;
    const count = $('#liveCount');
    if (count) count.textContent = '0 fragmentos';
  }

  async function pollPartial() {
    if (partialBusy || !S.jobId) return;
    partialBusy = true;
    try {
      const data = await api(`/api/jobs/${S.jobId}/partial?since=${S.partialSince}`);
      const segs = data.segments || [];
      if (segs.length) {
        const box = $('#liveBody');
        $('#liveTranscript').hidden = false;
        const frag = document.createDocumentFragment();
        segs.forEach(sg => {
          const d = document.createElement('div');
          d.className = 'live-seg';
          d.innerHTML = `<span class="live-ts">${fmtTime(sg.start)}</span>${esc(sg.text || '')}`;
          frag.appendChild(d);
        });
        box.appendChild(frag);
        while (box.children.length > 400) box.removeChild(box.firstChild);
        box.scrollTop = box.scrollHeight;
        S.partialSince = data.count || (S.partialSince + segs.length);
        S.partialCount = data.count || S.partialSince;
        $('#liveCount').textContent = `${fmtNum(S.partialCount)} fragmentos`;
      }
    } catch (_) { /* el parcial es opcional */ }
    finally { partialBusy = false; }
  }

  /* ═════════════════════ CORRECCIÓN DE HABLANTES ═════════════════════════ */
  function renderMergeSelects() {
    const from = $('#mergeFrom');
    const into = $('#mergeInto');
    if (!from || !into) return;
    if (S.speakers.length < 2) {
      from.innerHTML = '<option value="">—</option>';
      into.innerHTML = '<option value="">—</option>';
      return;
    }
    const opts = S.speakers.map(sp => `<option value="${esc(sp.name)}">${esc(sp.name)}</option>`).join('');
    const prevFrom = from.value, prevInto = into.value;
    from.innerHTML = opts;
    into.innerHTML = opts;
    if (S.speakers.some(sp => sp.name === prevFrom)) from.value = prevFrom;
    if (S.speakers.some(sp => sp.name === prevInto)) into.value = prevInto;
    else into.selectedIndex = Math.min(1, S.speakers.length - 1);
  }

  function recountSpeakers() {
    const counts = new Map();
    S.segments.forEach(seg => {
      const key = seg.speaker || '';
      const prev = counts.get(key) || { count: 0, seconds: 0 };
      prev.count += 1;
      prev.seconds += Math.max(0, (seg.end || 0) - (seg.start || 0));
      counts.set(key, prev);
    });
    S.speakers.forEach(sp => {
      const c = counts.get(sp.name) || { count: 0, seconds: 0 };
      sp.count = c.count;
      sp.seconds = Math.round(c.seconds * 10) / 10;
    });
    const byName = new Map(S.speakers.map((sp, i) => [sp.name, i]));
    S.segments.forEach(seg => { seg.si = byName.has(seg.speaker) ? byName.get(seg.speaker) : -1; });
  }

  async function mergeSpeakers() {
    if (!S.jobId) return;
    const from = $('#mergeFrom').value;
    const into = $('#mergeInto').value;
    if (!from || !into || from === into) {
      toast('Elige dos voces distintas para fusionar.', 'warn');
      return;
    }
    pushUndo(`fusionar ${from} → ${into}`);
    try {
      const res = await api(`/api/jobs/${S.jobId}/speakers/merge`, {
        method: 'POST', body: JSON.stringify({ from, into }),
      });
      S.segments.forEach(seg => { if ((seg.speaker || '') === from) seg.speaker = into; });
      S.speakers = S.speakers.filter(sp => sp.name !== from);
      recountSpeakers();
      renderSpeakers();
      renderMergeSelects();
      renderTranscript(true);
      renderStats();
      drawWaveform();
      toast(`«${from}» fusionado en «${into}» (${res.moved} fragmentos).`, 'ok', 'Hablantes');
    } catch (err) {
      S.undoStack.pop();
      toast(err.message, 'err', 'No se pudo fusionar');
    }
  }

  async function splitSpeaker() {
    if (!S.jobId) return;
    if (!S.selection.size) {
      toast('Activa «Seleccionar fragmentos» y marca los que son de otra voz.', 'warn');
      return;
    }
    const ids = [...S.selection].sort((a, b) => a - b);
    const origin = (S.segments[ids[0]] || {}).speaker || '';
    pushUndo(`separar voz de ${origin || 'sin hablante'}`);
    try {
      const res = await api(`/api/jobs/${S.jobId}/speakers/split`, {
        method: 'POST',
        body: JSON.stringify({
          name: origin, segment_ids: ids, new_name: ($('#splitName').value || '').trim(),
        }),
      });
      const name = res.created;
      let target = S.speakers.find(sp => sp.name === name);
      if (!target) {
        target = {
          name, original: name, count: 0, seconds: 0, known: false,
          similarity: 0, color: S.speakers.length, key: `split${S.speakers.length}`,
        };
        S.speakers.push(target);
      }
      ids.forEach(i => { if (S.segments[i]) S.segments[i].speaker = name; });
      S.selection.clear();
      toggleSelectMode(false);
      $('#splitName').value = '';
      recountSpeakers();
      renderSpeakers();
      renderMergeSelects();
      renderTranscript(true);
      renderStats();
      drawWaveform();
      toast(`${res.moved} fragmentos movidos a «${name}».`, 'ok', 'Voz separada');
    } catch (err) {
      S.undoStack.pop();
      toast(err.message, 'err', 'No se pudo separar');
    }
  }

  async function calibrateVoices() {
    try {
      const res = await api('/api/voces/calibrate', { method: 'POST', body: '{}' });
      $('#calibrateInfo').textContent = res.message || `Umbral: ${res.threshold}`;
      toast(res.message || 'Umbral actualizado.', res.ok ? 'ok' : 'warn', 'Voces');
    } catch (err) {
      toast(err.message, 'err', 'No se pudo calibrar');
    }
  }

  /* ═════════════════════ BÚSQUEDA EN TODAS LAS REUNIONES ══════════════════ */
  async function globalSearch() {
    const q = $('#searchInput').value.trim();
    if (q.length < 2) {
      toast('Escribe al menos 2 caracteres y pulsa «Todas».', 'warn');
      return;
    }
    const box = $('#globalResults');
    box.hidden = false;
    box.innerHTML = '<div class="global-item">Buscando en las reuniones guardadas…</div>';
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(q)}&limit=40`);
      S.globalHits = data.results || [];
      if (!S.globalHits.length) {
        box.innerHTML = `<div class="global-item">Sin coincidencias en ${fmtNum(data.scanned || 0)} reuniones revisadas.</div>`;
        return;
      }
      const hl = (text) => {
        const t = String(text || '');
        const folded = fold(t);
        const needle = fold(q);
        const at = folded.indexOf(needle);
        if (at < 0) return esc(t.slice(0, 160));
        const s0 = Math.max(0, at - 60);
        return esc(t.slice(s0, at)) + '<mark>' + esc(t.slice(at, at + needle.length)) + '</mark>'
          + esc(t.slice(at + needle.length, at + needle.length + 120));
      };
      box.innerHTML = S.globalHits.map((hit, i) => `
        <button type="button" class="global-item" data-i="${i}">
          <span class="gi-head">
            <strong>${esc(hit.title || hit.filename || hit.job_id)}</strong>
            <span class="mono">${fmtTime(hit.start || 0, true)}</span>
            ${hit.speaker ? `<span>· ${esc(hit.speaker)}</span>` : ''}
          </span>
          <span class="gi-text">${hl(hit.text)}</span>
        </button>`).join('');
      $$('.global-item', box).forEach(btn => btn.addEventListener('click', async () => {
        const hit = S.globalHits[Number(btn.dataset.i)];
        if (!hit) return;
        box.hidden = true;
        if (hit.job_id !== S.jobId) {
          await loadResult(hit.job_id);
        }
        setTimeout(() => {
          seekTo((hit.start || 0) + 0.01);
          const idx = findSegmentAt(hit.start || 0);
          if (idx >= 0) setActive(idx, true);
        }, 350);
      }));
      toast(`${S.globalHits.length} reunión(es) con «${q}».`, 'ok', 'Búsqueda global');
    } catch (err) {
      box.innerHTML = `<div class="global-item">${esc(err.message)}</div>`;
    }
  }

  /* ═════════════════════════ ACCESIBILIDAD ═══════════════════════════════ */
  function trapFocus(container) {
    if (!container || container.dataset.trap) return;
    container.dataset.trap = '1';
    container.addEventListener('keydown', (e) => {
      if (e.key !== 'Tab') return;
      const sel = 'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';
      const items = $$(sel, container).filter(el => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    setTimeout(() => {
      const first = container.querySelector('button, input, select, textarea');
      if (first) first.focus();
    }, 80);
  }

  function bindEvents() {
    $('#themeToggle').addEventListener('click', toggleTheme);
    $('#btnHistory').addEventListener('click', openHistory);
    $('#btnCloseHistory').addEventListener('click', closeHistory);
    $('#scrim').addEventListener('click', () => { closeHistory(); closeCompromisos(); });

    // Historial: buscador, filtros y orden
    $('#historySearch').addEventListener('input', debounce(() => {
      HIST.q = $('#historySearch').value; renderHistoryList();
    }, 200));
    $('#historyFilter').addEventListener('change', () => {
      HIST.filtro = $('#historyFilter').value; renderHistoryList();
    });
    $('#historySort').addEventListener('change', () => {
      HIST.orden = $('#historySort').value; renderHistoryList();
    });

    // Compromisos de todas las reuniones
    $('#btnCompromisos').addEventListener('click', openCompromisos);
    $('#btnCloseCompromisos').addEventListener('click', closeCompromisos);
    $('#compromisosSearch').addEventListener('input', debounce(() => {
      COMP.q = $('#compromisosSearch').value; renderCompromisos();
    }, 200));
    $('#compromisosFilter').addEventListener('change', () => {
      COMP.filtro = $('#compromisosFilter').value; renderCompromisos();
    });

    // Acta: guardar, versiones, descarga y saltos al audio
    $('#btnActaGuardar').addEventListener('click', () => guardarActaOficial(false));
    $('#btnActaRestaurar').addEventListener('click', restaurarVersion);
    $('#btnActaDocx').addEventListener('click', () => {
      if (!S.jobId) { toast('Primero transcribe una reunión.', 'warn'); return; }
      guardarActaOficial(true).then(() => descargarExport(S.jobId, 'acta'));
    });
    $('#actaPreview').addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-seek]');
      if (btn) irAlMinuto(btn.dataset.seek);
    });
    $('#figuresList').addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-seek]');
      if (btn) irAlMinuto(btn.dataset.seek);
    });
    $('#btnSavePlantilla').addEventListener('click', guardarPlantilla);
    cargarPlantilla();
    // C6 · continuar una reunión con otro audio
    const btnContinuar = $('#btnContinuar');
    if (btnContinuar) {
      btnContinuar.addEventListener('click', () => {
        if (!S.jobId) { toast('Primero abre una reunión transcrita.', 'warn'); return; }
        const inp = $('#continuarFile');
        if (inp) inp.click();
      });
    }
    const inputContinuar = $('#continuarFile');
    if (inputContinuar) {
      inputContinuar.addEventListener('change', () => {
        const file = inputContinuar.files && inputContinuar.files[0];
        if (file) iniciarContinuar(file);
      });
    }

    $('#btnAudioListen').addEventListener('click', escucharAudio);
    $('#btnStart').addEventListener('click', startJob);
    $('#btnClearFile').addEventListener('click', clearFile);
    $('#btnCancelJob').addEventListener('click', cancelCurrent);
    $('#btnCancelAll').addEventListener('click', cancelSession);
    $('#btnToggleLog').addEventListener('click', () => {
      const log = $('#jobLog');
      log.hidden = !log.hidden;
      if (!log.hidden) log.scrollTop = log.scrollHeight;
    });
    $('#btnAbortUpload').addEventListener('click', () => { if (S.uploadXhr) S.uploadXhr.abort(); });

    $('#modeSelect').addEventListener('change', () => { updateModeDesc(); guardarAjustes(); });
    $('#languageSelect').addEventListener('change', guardarAjustes);
    ['#optChain', '#optMusic'].forEach(sel => {
      const el = $(sel);
      if (el) el.addEventListener('change', guardarAjustes);
    });
    $('#optTimestamps').addEventListener('change', () => {
      guardarAjustes();
      $('#segmentList').classList.toggle('show-ts', $('#optTimestamps').checked);
    });
    $('#optParagraphs').addEventListener('change', () => {
      guardarAjustes();
      $('#segmentList').classList.toggle('paragraphs', $('#optParagraphs').checked);
      if (S.segments.length) renderTranscript();
    });

    // Buscador
    const input = $('#searchInput');
    input.addEventListener('input', debounce(() => updateSearch(input.value), 160));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); stepMatch(e.shiftKey ? -1 : 1); }
      if (e.key === 'Escape') { input.value = ''; updateSearch(''); input.blur(); }
    });
    $('#btnNextMatch').addEventListener('click', () => stepMatch(1));
    $('#btnPrevMatch').addEventListener('click', () => stepMatch(-1));
    $('#btnJumpLow').addEventListener('click', jumpToLowConfidence);
    $('#btnEdit').addEventListener('click', () => toggleEdit());
    $('#btnEditDone').addEventListener('click', () => toggleEdit(false));
    $('#btnApplyNames').addEventListener('click', applyNames);
    $('#btnCopy').addEventListener('click', copyTranscript);
    $('#btnPrint').addEventListener('click', () => window.print());

    // Acta
    $('#btnSummarize').addEventListener('click', summarize);
    $('#btnSaveKey').addEventListener('click', saveIaConfig);
    $('#engineSelect').addEventListener('change', toggleEngineFields);
    $('#localModelSelect').addEventListener('change', toggleEngineFields);
    $('#btnTestEngine').addEventListener('click', testEngine);
    $('#btnClassify').addEventListener('click', () => {
      paintClassified(classifyText($('#summaryText').value));
      toast('Clasificación actualizada.', 'ok');
    });
    $('#btnActaFormatted').addEventListener('click', () => {
      actaViewFormatted = true; actaViewUserPicked = true; renderActaPreview();
    });
    $('#btnActaRaw').addEventListener('click', () => {
      actaViewFormatted = false; actaViewUserPicked = true; renderActaPreview();
    });
    $('#summaryText').addEventListener('change', () => {
      if ((S.summaryEditTimer || null)) clearTimeout(S.summaryEditTimer);
      S.summaryEditTimer = setTimeout(() => { guardarActaOficial(true); }, 1500);
    });
    $('#summaryText').addEventListener('input', debounce(() => {
      updateSummaryCount();
      renderActaPreview();
      saveSession({ conclusion: $('#summaryText').value, summary: $('#summaryText').value });
    }, 900));
    $$('[data-export]').forEach(btn => btn.addEventListener('click', () => exportDoc(btn.dataset.export)));

    // Grabadora
    $('#btnRecord').addEventListener('click', openRecorder);
    $('#btnRecClose').addEventListener('click', () => {
      if (R.rec && R.rec.state !== 'inactive') {
        if (!confirm('Hay una grabación en curso. ¿Descartarla?')) return;
        cancelRecording();
      } else {
        cleanupRecorder();
        $('#recorderModal').hidden = true;
      }
    });
    $('#btnRecStart').addEventListener('click', startRecording);
    $('#btnRecPause').addEventListener('click', togglePauseRecording);
    $('#btnRecStop').addEventListener('click', stopRecording);
    $('#micSelect').addEventListener('change', () => {
      if (R.rec) toast('Detén la grabación para cambiar de micrófono.', 'warn');
    });
    $('#recorderModal').addEventListener('click', (e) => {
      if (e.target.id === 'recorderModal' && !(R.rec && R.rec.state !== 'inactive')) {
        $('#recorderModal').hidden = true;
      }
    });

    // Capa de correcciones
    $('#btnUndo').addEventListener('click', undoLast);
    $('#btnRestore').addEventListener('click', restoreOriginal);
    $('#btnGlobalSearch').addEventListener('click', globalSearch);
    $('#btnMergeSpeakers').addEventListener('click', mergeSpeakers);
    const lowChip = $('#btnLowChip');
    if (lowChip) {
      lowChip.addEventListener('click', () => {
        const list = $('#segmentList');
        const on = list.classList.toggle('only-low');
        lowChip.classList.toggle('is-on', on);
        const first = on ? $$('.segment.low-confidence', list)[0] : null;
        if (first) first.scrollIntoView({ block: 'center', behavior: 'smooth' });
        announce(on ? 'Mostrando solo los fragmentos dudosos.' : 'Mostrando toda la transcripción.');
      });
    }

    $('#btnSelectSegments').addEventListener('click', () => toggleSelectMode());
    $('#btnSplitSpeaker').addEventListener('click', splitSpeaker);
    $('#btnCalibrate').addEventListener('click', calibrateVoices);
    $('#btnStopSummarize').addEventListener('click', () => {
      if (S.streamAbort) S.streamAbort.abort();
    });
    $('#btnCopyAcuerdos').addEventListener('click', async () => {
      const rows = $$('#acuerdosBody tr').map(tr =>
        $$('td', tr).map(td => td.textContent.trim()).join('\t'));
      if (!rows.length) { toast('Todavía no hay acuerdos estructurados.', 'warn'); return; }
      try {
        await navigator.clipboard.writeText(['Acuerdo\tResponsable\tFecha\tEstatus\tMinuto', ...rows].join('\n'));
        toast('Tabla de acuerdos copiada (lista para Excel o Word).', 'ok');
      } catch (_) { toast('El navegador bloqueó el portapapeles.', 'err'); }
    });

    // Reproductor
    const actaEmpty = $('#actaEmpty');
    if (actaEmpty) {
      const txt = $('#summaryText');
      const sync = () => { actaEmpty.hidden = !!(txt.value || '').trim(); };
      sync();
      txt.addEventListener('input', sync);
      $$('#actaEmpty .ae-card').forEach(card => card.addEventListener('click', () => {
        const sel = $('#formatSelect');
        if (sel && card.dataset.format) sel.value = card.dataset.format;
        toast(`Formato «${card.querySelector('b').textContent}» seleccionado. Pulsa «Redactar con IA».`, 'info');
        $('#btnSummarize')?.focus();
      }));
      window.__syncActaEmpty = sync;
    }

    bindPlayer();

    // Atajos globales
    document.addEventListener('keydown', (e) => {
      const typing = e.target.matches('input, textarea, select, [contenteditable="true"]');
      if ((e.key === '/' && !typing) || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k')) {
        e.preventDefault();
        $('#searchInput').focus();
        $('#searchInput').select();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && !typing) {
        e.preventDefault();
        undoLast();
      }
      if (!typing && $('#playerBar') && !$('#playerBar').hidden) {
        if (e.key === 'ArrowLeft') { e.preventDefault(); skipSeconds(-5); }
        if (e.key === 'ArrowRight') { e.preventDefault(); skipSeconds(5); }
      }
      if (e.key === 'Escape') {
        closeHistory();
        if ($('#globalResults')) $('#globalResults').hidden = true;
        $$('.sp-menu').forEach(m => m.remove());
        if (!$('#recorderModal').hidden && !(R.rec && R.rec.state !== 'inactive')) $('#recorderModal').hidden = true;
      }
    });

    // Reintento de salud periódico suave
    setInterval(() => { if (!$('#jobPanel').hidden && S.job && ['queued', 'preprocessing', 'transcribing', 'diarizing'].includes(S.job.status)) refreshHealthSoon(); }, 20000);
  }

  /* --------------------------------- INIT -------------------------------- */
  async function init() {
    applyTheme(store.get('tae_theme') || 'dark');
    bindDropzone();
    bindEvents();
    updateStartButton();
    await loadHealth();
    loadKnownVoices();

    // Restaura el último trabajo si existe (por comodidad al recargar)
    if (S.jobId) {
      try {
        await loadResult(S.jobId);
        toast('Se restauró la última reunión abierta.', 'info');
      } catch (_) {
        S.jobId = '';
        store.del('tae_last_job');
      }
    }
    // Cierra la grabadora con Escape / fondo si quedó abierta por error
    window.addEventListener('beforeunload', () => {
      if (S.job && ['preprocessing', 'transcribing', 'diarizing'].includes(S.job.status)) {
        try { navigator.sendBeacon(`/api/jobs/${S.job.id}/cancel`); } catch (_) { /* backend cancela al cerrar */ }
      }
    });
  }

  /* «Nueva reunión» (botón y paso 1 del flujo): delegado a nivel de documento
     para que funcione siempre, aunque otra parte de la interfaz falle. */
  document.addEventListener('click', (ev) => {
    const objetivo = ev.target && ev.target.closest
      ? ev.target.closest('#btnNuevaReunion, #stepper .step[data-step="1"]') : null;
    if (!objetivo) return;
    ev.preventDefault();
    irAlInicio();
  }, true);

  /* Si algo revienta, que se vea en pantalla y no solo en la consola. */
  let erroresMostrados = 0;
  window.addEventListener('error', (ev) => {
    if (erroresMostrados >= 3) return;
    erroresMostrados += 1;
    const detalle = (ev && ev.message) || 'Error desconocido';
    toast(`Algo falló en la interfaz: ${detalle}`, 'err', 'Error de JavaScript');
  });

  document.addEventListener('DOMContentLoaded', init);
})();
