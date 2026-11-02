/* ===== ENTOMIRA · SPA (JS vanilla, sin build) ===== */
'use strict';
const API = '/api/v1';
// EL MENU SE ENGANCHA SOLO SI ESTA, Y ESTO NO ES DEFENSA POR DEFENDER.
//
// Esto corre en la raiz del fichero, antes que nada. Si un dia cambia un `id` en el HTML,
// `null.addEventListener` lanza en la primera linea y **se lleva app.js entero**: la web sale
// en blanco, sin galeria, sin trampas y sin un mensaje. Ese fallo exacto ya costo dos dias
// aqui, y por eso `node --check` es obligatorio antes de desplegar. Un menu que no engancha se
// nota y se arregla; una pagina en blanco no dice de que va.
const menuButton = document.getElementById('menu-toggle');
const menuPanel = document.getElementById('header-menu');
function closeHeaderMenu(focus = false) {
  if (!menuButton || !menuPanel) return;
  menuButton.setAttribute('aria-expanded', 'false');
  menuPanel.classList.remove('is-open');
  if (focus) menuButton.focus();
}
if (menuButton && menuPanel) {
menuButton.addEventListener('click', () => {
  const open = menuButton.getAttribute('aria-expanded') !== 'true';
  menuButton.setAttribute('aria-expanded', String(open));
  menuPanel.classList.toggle('is-open', open);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && menuButton.getAttribute('aria-expanded') === 'true') closeHeaderMenu(true);
});
document.addEventListener('click', e => {
  if (!e.target.closest('.topbar')) closeHeaderMenu();
});
menuPanel.addEventListener('click', e => { if (e.target.closest('a')) closeHeaderMenu(); });
window.addEventListener('hashchange', () => closeHeaderMenu());
}
const SECTORES = ['A', 'B', 'pasillo', 'C', 'D'];
// Nombres legibles de especie (el modelo v2 devuelve códigos)
// Solo mosca blanca y trips desde el 2026-10-23 (orden de Antonio): la fauna auxiliar ya no se
// gestiona; el servidor ni la cuenta ni la envía.
const CLASES = { WF: 'Mosca blanca', TR: 'Trips', insecto: 'Insecto' };
const CLASE_META = {
  WF: { name: 'Mosca blanca', emoji: '🪰', tipo: 'Plaga', color: '#E04449' },
  TR: { name: 'Trips', emoji: '🦟', tipo: 'Plaga', color: '#B45309' },
  insecto: { name: 'Insecto', emoji: '🐛', tipo: '', color: '#00544D' },
};
const CLASE_SIGLA = { WF: 'MB', TR: 'TR', insecto: 'INS' };  // siglas en español (mosca blanca = MB)
const clsName = (c) => CLASES[c] || c;
// "online real": vista hace poco (independiente de si está asignada a invernadero)
// UNA SOLA FUENTE DE VERDAD: lo dice el servidor.
//
// Esto eran `180000` ms a pelo —de cuando la telemetria iba cada 10 s— mientras el servidor
// usaba (cadencia + retencion) x 2 + 60, que hoy son 1500 s. Entre 180 s y 1500 s la misma
// tarjeta pintaba «En linea» y «sin conexion» al mismo tiempo, y el panel dejaba de pedir
// video a una trampa que estaba perfectamente viva: habla cada ~2 min, justo en el filo.
//
// El respaldo por fecha solo cubre una respuesta antigua sin el campo; y usa el mismo margen
// generoso, no 3 minutos.
const trapOnline = (t) => (t.en_linea !== undefined && t.en_linea !== null)
  ? !!t.en_linea
  : (!!t.ultimo_visto && (Date.now() - aFecha(t.ultimo_visto).getTime()) < 1500000);
const clsSigla = (c) => CLASE_SIGLA[c] || c;
const clsMeta = (c) => CLASE_META[c] || { name: c, emoji: '🐛', tipo: '', color: '#00544D' };

/* ---------- mandos de la cámara (libcamera) ----------
   ETIQUETA en castellano, VALOR en inglés. El valor viaja sin tocar desde este desplegable
   hasta el `--awb` / `--autofocus-mode` de `rpicam-still` en la trampa: traducirlo aquí
   obligaría a destraducirlo allí, y esa capa intermedia es justo la que se acaba de quitar.
   Son los de `BALANCES_VALIDOS` y `ENFOQUES_VALIDOS` del servidor; si allí se añade uno y
   aquí no, la web simplemente no lo ofrece (no falla). */
const BALANCES = {
  auto: 'Automático', daylight: 'Luz de día', cloudy: 'Nublado',
  indoor: 'Interior', incandescent: 'Incandescente', tungsten: 'Tungsteno',
  fluorescent: 'Fluorescente',
};
const ENFOQUES = {
  auto: 'Automático (enfoca al disparar)',
  continuous: 'Continuo (enfoca siempre)',
  manual: 'Manual (distancia fija)',
};
const nombreBalance = (v) => BALANCES[v] || v || '—';
const nombreEnfoque = (v) => ENFOQUES[v] || v || '—';
/* Dioptrías → distancia, que es como lo piensa una persona: 0 es infinito y 3,3 dioptrías
   son los ~30 cm a los que cuelga la tarjeta del brazo. */
const distanciaFoco = (d) => (!d || +d <= 0) ? 'infinito' : `${Math.round(100 / +d)} cm`;
/* Desplegable a partir de una de las tablas de arriba. */
function selectDe(tabla, valor) {
  return el('select', {}, ...Object.entries(tabla).map(([v, txt]) =>
    el('option', { value: v, selected: v === valor ? 'selected' : null }, txt)));
}

/* ---------- utilidades ---------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return n;
}
// Caché corta de endpoints estables → los cambios de pestaña no re-piden lo mismo
// (invernaderos/trampas/stats). Cualquier mutación (POST/PUT/DELETE) la invalida entera.
const _apiCache = new Map();
const _CACHEABLE = new Set(['/greenhouses', '/traps', '/stats']);
const _CACHE_TTL = 10000; // ms
function invalidateApiCache() { _apiCache.clear(); }
async function api(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  if (method === 'GET') {
    const hit = _CACHEABLE.has(path) ? _apiCache.get(path) : null;
    if (hit && Date.now() - hit.t < _CACHE_TTL) return hit.v;
  } else {
    invalidateApiCache();
  }
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  const data = await res.json();
  if (method === 'GET' && _CACHEABLE.has(path)) _apiCache.set(path, { t: Date.now(), v: data });
  return data;
}
function toast(msg, err = false) {
  const t = el('div', { class: 'toast' + (err ? ' err' : '') }, msg);
  $('#toast-root').append(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3200);
}
function aFecha(s) {
  // El servidor devuelve UTC sin sufijo de zona; márcalo como UTC ('Z') para que el
  // navegador lo convierta a la hora local (Madrid, CEST/CET).
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(s)) s += 'Z';
  return new Date(s);
}
function fmtDate(s) {
  if (!s) return '—';
  const d = aFecha(s);
  return d.toLocaleString('es-ES', { day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Madrid' });
}
function haceDesde(s) {
  if (!s) return '';
  const sg = Math.max(0, (Date.now() - aFecha(s).getTime()) / 1000);
  if (sg < 90) return `hace ${Math.round(sg)} s`;
  if (sg < 5400) return `hace ${Math.round(sg / 60)} min`;
  if (sg < 172800) return `hace ${Math.round(sg / 3600)} h`;
  return `hace ${Math.round(sg / 86400)} días`;
}
// «1 trampas» cantaba: plural solo cuando toca.
function plural(n, sing, pl) { return `${n} ${n === 1 ? sing : pl}`; }

/* Por dónde está conectada la trampa. No es cosmético: determina si se le puede abrir el
   streaming directo (solo en la red local), si cada foto cuesta datos móviles, y si está
   colgada de un cable a la Pi. */
const REDES = {
  wifi: { icono: '📶', nombre: 'WiFi', ayuda: 'En la red local: streaming directo disponible.' },
  lte:  { icono: '📡', nombre: '4G',   ayuda: 'Por red móvil: detrás del NAT del operador, la vista en vivo va por empuje y cada foto consume datos.' },
  eth:  { icono: '🔗', nombre: 'Ethernet', ayuda: 'Conectada por cable a la red.' },
  vpn:  { icono: '🔒', nombre: 'VPN', ayuda: 'Conectada al servidor mediante una red privada.' },
  usb:  { icono: '🔌', nombre: 'USB',  ayuda: 'Colgada por cable de la Raspberry Pi. El puente serie solo transporta fotos: NO hay vista en vivo.' },
};
function redInfo(t) { return REDES[t && t.red] || null; }
function redTexto(t) {
  const r = redInfo(t);
  const pct = t.wifi_pct != null ? ` ${t.wifi_pct}%` : '';
  const op = t.red === 'lte' && t.operador ? ` · ${t.operador}` : '';
  if (!trapOnline(t)) {
    const pctConocido = t.wifi_pct != null ? ` ${t.wifi_pct} %` : '';
    const red = !r ? 'sin dato' : `${r.icono} ${r.nombre}${pctConocido}${op}`;
    const lte = t.red === 'lte' || t.lte_lista == null ? '' : ` · 4G: ${t.lte_lista ? 'sí' : 'no'}`;
    const edad = haceDesde(t.ultimo_visto);
    return `Última conexión conocida${edad ? ` · ${edad}` : ''}: ${red}${lte}`;
  }
  const usada = !r
    ? 'Red en uso: sin dato'
    : t.red === 'usb'
    ? `${r.icono} ${r.nombre} a la Pi en uso`
    : `${r.icono} ${r.nombre}${pct}${op} en uso`;
  // `null` significa que la telemetría aún no lo sabe (reinicio del servidor o firmware
  // antiguo), no que falte el módem. Si ya se usa LTE, esa es la prueba más directa de que
  // está disponible y no hace falta repetirlo como una segunda etiqueta.
  if (t.red === 'lte') return usada;
  if (t.lte_lista === true) return `${usada} · 📡 4G disponible`;
  if (t.lte_lista === false) return `${usada} · 4G no disponible`;
  return `${usada} · disponibilidad 4G sin dato`;
}
function ultimoDatoRed(t, valor, vacio = '—') {
  const dato = valor != null && valor !== '' ? valor : vacio;
  if (trapOnline(t)) return dato;
  const edad = haceDesde(t.ultimo_visto);
  return `Último dato conocido${edad ? ` · ${edad}` : ''}: ${dato}`;
}
function horaReposo(t) {
  if (!t || !t.reposo_hasta) return '';
  return aFecha(t.reposo_hasta).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function textoEstado(t) {
  // Un corte rechazado manda sobre cualquier estado calculado con el horario (revisión Codex).
  if (t && t.reposo_error) return `Pausa cancelada: ${t.reposo_error}`;
  if (t?.despertar_origen === 'apagado' && t.despertar_informado &&
      (!t.ultimo_visto || aFecha(t.ultimo_visto) <= aFecha(t.despertar_informado)))
    return 'Apagada · encendido manual (A–GND)';
  if (t?.config?.deep_sleep) {
    const visto = t.ultimo_visto ? aFecha(t.ultimo_visto).getTime() : 0;
    const confirmado = t.despertar_confirmado ? aFecha(t.despertar_confirmado).getTime() : 0;
    const informado = t.despertar_informado ? aFecha(t.despertar_informado).getTime() : 0;
    const hora = ms => new Date(ms).toLocaleTimeString('es-ES', {hour:'2-digit', minute:'2-digit', timeZone:'Europe/Madrid'});
    // «Dormida» solo si NO ha vuelto a hablar después de avisar de la alarma (+60 s): si el
    // corte falló, la Zero sigue mandando telemetría y esto deja de cumplirse (revisión Codex).
    if (confirmado && informado && visto <= informado + 60000) return Date.now() > confirmado + 600000
      ? 'No contactó tras despertar' : `Dormida · alarma armada para las ${hora(confirmado)}`;
    if (trapOnline(t)) return 'En línea';
    const horas = t.config.horas_despertar?.length ? t.config.horas_despertar : [t.config.hora_despertar || '08:00'];
    const partes = new Intl.DateTimeFormat('en-GB', {timeZone:'Europe/Madrid', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'}).formatToParts(new Date());
    const p = Object.fromEntries(partes.map(x => [x.type, +x.value]));
    const ahoraMin = p.hour * 60 + p.minute;
    const turnos = horas.map(x => { const [h,m] = x.split(':').map(Number); return h*60+m; }).filter(Number.isFinite).sort((a,b)=>a-b);
    if (turnos.length) {
      const siguiente = turnos.find(x => x >= ahoraMin) ?? turnos[0];
      const anterior = [...turnos].reverse().find(x => x <= ahoraMin) ?? turnos.at(-1);
      const minDesdeAnterior = (ahoraMin - anterior + 1440) % 1440;
      if (visto && minDesdeAnterior > 10 && Date.now() - visto > minDesdeAnterior*60000)
        return 'No contactó tras despertar';
      return `Despertar previsto ${String(Math.floor(siguiente/60)).padStart(2,'0')}:${String(siguiente%60).padStart(2,'0')} (sin confirmar)`;
    }
  }
  if (t && t.estado === 'reposo') return `En pausa hasta ${t.reposo_modo === 'apagado' ? '~' : ''}${horaReposo(t) || 'pronto'}${t.reposo_modo === 'wifi' ? ' · solo WiFi; despertar sin validar' : ''}`;
  if (t && t.reposo_error) return `Pausa cancelada: ${t.reposo_error}`;
  return trapOnline(t) ? 'En línea' : t && t.estado === 'apagada' ? 'Apagada' : 'Sin conexión';
}
function estadoTag(estado) {
  const map = { online: ['tag', 'En línea'], offline: ['tag gray', 'Desconectada'], apagada: ['tag gray', 'Apagada'], reposo: ['tag gray', 'En reposo'], sin_asignar: ['tag amber', 'Sin asignar'] };
  const [cls, txt] = map[estado] || ['tag gray', estado];
  return el('span', { class: cls }, txt);
}

/* ---------- carga de batería (CRATE del MAX17048, en %/hora con signo) ----------
   El fuel-gauge no mide corriente: estima el ritmo de carga/descarga y lo filtra, así que
   tarda unos minutos en asentarse. Se interpreta el SIGNO, no el valor exacto. La zona
   muerta de ±0,5 %/h evita que el ruido haga parpadear el indicador. */
const CARGA_UMBRAL = 0.5;
function cargando(t) { return (t.carga_estado || '') === 'cargando'; }
function bateriaTexto(t) {
  if (t.bateria != null) return `${t.bateria}% medido`;
  if (t.bateria_estimada != null) return `≈${t.bateria_estimada}% estimado`;
  return 'sin dato';
}
function cargaTexto(t) {
  // El servidor da el veredicto (`carga_estado`) mirando la PENDIENTE DE TENSION, que
  // reacciona al instante, y usa el `crate` solo cuando la tension no se mueve. Aqui se
  // muestra tambien DE DONDE sale, para no dar por medido lo que es una estimacion lenta.
  const est = t.carga_estado;
  const mv = t.tendencia_mv_h;
  const detalle = [];
  if (mv != null) detalle.push(`${mv > 0 ? '+' : ''}${mv} mV/h`);
  if (t.crate != null) detalle.push(`${t.crate > 0 ? '+' : ''}${t.crate.toFixed(1)} %/h`);
  // Veredicto de la propia trampa (detector de 1 min): se dice con qué confianza.
  if (t.carga_confianza && t.carga_confianza !== 'alta') detalle.push(`confianza ${t.carga_confianza}`);
  const suf = detalle.length ? ` (${detalle.join(' · ')})` : '';
  if (est === 'llena')         return { texto: `🔋 Llena${suf}`, color: 'var(--teal)' };
  if (est === 'indeterminado') return { texto: 'Carga: sin tendencia clara todavía', color: 'var(--muted, #6B7280)' };
  if (est === 'cargando')    return { texto: `⚡ CARGANDO${suf}`, color: 'var(--teal)' };
  // `--rojo-texto` y no `--rojo`: esto es TEXTO, y el rojo de marca se queda en 4,12:1
  // sobre blanco, por debajo del 4,5 de WCAG AA. Al sol de un invernadero se pierde.
  if (est === 'descargando') return { texto: `🔻 Descargando${suf}`, color: 'var(--rojo-texto)' };
  if (est === 'reposo')      return { texto: `⏸ En reposo${suf}`, color: 'var(--muted, #6B7280)' };
  if (est === 'midiendo')    return { texto: 'Midiendo la batería…', color: 'var(--muted, #6B7280)' };
  return { texto: 'ritmo de carga no disponible', color: 'var(--muted, #6B7280)' };
}

/* ---------- modal ---------- */
function modal({ title, body, actions, className = '' }) {
  const root = $('#modal-root');
  const close = () => { root.innerHTML = ''; };
  // Cerrar al hacer clic FUERA, pero solo si el gesto empezó fuera. Con `onclick` a secas se
  // cerraba al seleccionar texto dentro y soltar el ratón fuera: el navegador dispara el
  // clic en el ancestro común de donde se pulsó y donde se soltó, que es este fondo.
  let empezoFuera = false;
  const back = el('div', {
    class: 'modal-back',
    onpointerdown: (e) => { empezoFuera = (e.target === back); },
    onclick: (e) => { if (empezoFuera && e.target === back) close(); },
  },
    el('div', { class: `modal ${className}` },
      el('div', { class: 'head' }, el('h3', {}, title), el('button', { class: 'btn ghost sm', onclick: close }, '✕')),
      el('div', { class: 'content' }, body),
      el('div', { class: 'foot' }, ...(actions ? actions(close) : [el('button', { class: 'btn ghost', onclick: close }, 'Cerrar')])),
    ));
  root.innerHTML = '';
  root.append(back);
  return close;
}

/* ---------- pills de estado ---------- */
async function refreshStatus() {
  try {
    const h = await api('/health');
    setPill('#pill-server', true, 'Servidor ✓');
  } catch { setPill('#pill-server', false, 'Servidor ✗'); }
  try {
    const w = await api('/health/worker');
    setPill('#pill-worker', w.online, w.online ? 'IA en línea' : 'IA offline');
  } catch { setPill('#pill-worker', false, 'IA offline'); }
  refreshDisk();
  refreshAvisos();
}

/* ---------- avisos: que un fallo se VEA ----------
   El 31/07 el disco se llenó, la ingesta dejó de aceptar fotos y nadie se enteró en tres
   días: el servicio seguía activo y la web funcionaba. Esta pastilla existe para que eso no
   vuelva a pasar en silencio. Si no hay nada que decir, no ocupa sitio. */
let _avisos = [];
async function refreshAvisos() {
  const p = $('#pill-avisos');
  if (!p) return;
  let d;
  try { d = await api('/avisos'); } catch { p.hidden = true; return; }
  _avisos = d.avisos || [];
  if (!_avisos.length) { p.hidden = true; return; }
  p.hidden = false;
  p.textContent = `${d.criticos ? '🔴' : '⚠️'} ${d.total} ${d.total === 1 ? 'aviso' : 'avisos'}`;
  p.className = 'pill ' + (d.criticos ? 'off' : '');
  p.onclick = panelAvisos;
}
function panelAvisos() {
  if (!_avisos.length) return;
  const cuerpo = el('div', { style: 'display:flex;flex-direction:column;gap:14px' },
    ..._avisos.map(a => el('div', {
      style: 'padding-left:14px;border-left:3px solid ' +
             (a.nivel === 'critico' ? 'var(--rojo)' : 'var(--ambar, #E8871B)')
    },
      el('div', { style: 'font-weight:600;margin-bottom:2px' },
         (a.nivel === 'critico' ? '🔴 ' : '⚠️ ') + a.titulo),
      el('div', { class: 'muted', style: 'font-size:13.5px' }, a.detalle),
      el('div', { style: 'font-size:13.5px;margin-top:4px' }, '→ ' + a.accion))));
  modal({ title: 'Avisos del sistema', body: cuerpo });
}
function setPill(sel, ok, txt) {
  const p = $(sel); if (!p) return;
  p.textContent = txt; p.className = 'pill ' + (ok ? 'ok' : 'off');
}

/* ---------- gadget de almacenamiento ----------
   Pastilla siempre visible con el espacio libre del servidor; al pulsarla, un panel con
   el desglose. Existe porque el disco de la Pi se llenó y tumbó la ingesta en silencio
   (ver Docs_Obsidian/09_Troubleshooting.md, G4). */
let SYS = null;
async function refreshDisk() {
  const p = $('#pill-disk'); if (!p) return;
  try {
    SYS = await api('/system/health');
  } catch { p.hidden = true; return; }
  const nivel = SYS.disk_nivel || 'ok';
  p.hidden = false;
  p.textContent = `💾 ${fmtGb(SYS.disk_free_gb)} libres`;
  p.className = 'pill disk ' + nivel;
  p.title = `Almacenamiento: ${SYS.disk_used_gb} de ${SYS.disk_total_gb} GB usados `
          + `(${SYS.disk_used_pct} %). Pulsa para ver el detalle.`;
  p.onclick = panelAlmacenamiento;
}
function fmtGb(gb) {
  return gb >= 1 ? `${gb} GB` : `${Math.round(gb * 1000)} MB`;
}
function panelAlmacenamiento() {
  if (!SYS) return;
  const pct = SYS.disk_used_pct || 0;
  const nivel = SYS.disk_nivel || 'ok';
  const fila = (etq, val, extra) => el('div', { class: 'st-row' },
    el('span', { class: 'st-k' }, etq),
    el('span', { class: 'st-v' }, val),
    extra ? el('span', { class: 'st-x' }, extra) : null);

  const aviso = {
    ok: null,
    aviso: '⚠️ Más del 70 % ocupado. La purga automática debería contenerlo; vigílalo.',
    critico: '🔴 Más del 85 % ocupado. Si llega al 100 %, el servidor deja de aceptar fotos '
           + '(la web sigue funcionando, así que el fallo no se nota). Vacía la galería o '
           + 'baja el intervalo de captura.',
  }[nivel];

  modal({
    title: '💾 Almacenamiento del servidor',
    body: el('div', { class: 'storage' },
      el('div', { class: 'st-big' },
        el('b', {}, fmtGb(SYS.disk_free_gb)),
        el('span', {}, ` libres de ${SYS.disk_total_gb} GB`)),
      el('div', { class: 'st-bar' },
        el('div', { class: 'st-fill ' + nivel, style: `width:${Math.min(100, pct)}%` })),
      el('div', { class: 'st-legend' }, `${SYS.disk_used_gb} GB usados · ${pct} %`),
      aviso ? el('div', { class: 'st-alert ' + nivel }, aviso) : null,
      el('div', { class: 'st-rows' },
        fila('🖼️ Imágenes', `${(SYS.images_mb / 1000).toFixed(2)} GB`,
             `${SYS.images_n} ficheros`),
        fila('🗃️ Base de datos', `${SYS.db_mb} MB`),
        fila('🌡️ Temperatura CPU', SYS.pi && SYS.pi.cpu_temp ? `${SYS.pi.cpu_temp} °C` : '—'),
        fila('🧠 RAM de la Pi', SYS.pi && SYS.pi.ram_total
             ? `${SYS.pi.ram_used} / ${SYS.pi.ram_total} MB` : '—'),
        fila('⏱️ Servidor activo', `${SYS.uptime_h} h`, `v${SYS.version}`)),
      el('div', { class: 'st-note' },
        'Las fotos ya no se borran por antigüedad (desde el 23/10/2026): al acabar la campaña '
        + 'se archivan por invernadero en el disco externo.'),
      ME && ME.rol === 'admin' ? panelDisco() : null),
  });
}

/* ---------- disco externo y apagado seguro (solo admin, 2026-10-23) ----------
   El disco va siempre enchufado. exFAT no tiene diario: antes de desenchufarlo se EXPULSA, y
   antes de quitarle la corriente al servidor se APAGA desde aquí. Lo irreversible pide escribir
   la palabra, no solo un clic. */
function confirmarEscribiendo(titulo, texto, palabra, accion) {
  const inp = el('input', { type: 'text', placeholder: palabra, autocomplete: 'off' });
  const err = el('div', { class: 'muted', style: 'color:#b91c1c;margin-top:6px' });
  modal({
    title: titulo,
    body: el('div', {}, el('p', {}, texto),
      el('p', { class: 'muted' }, `Escribe ${palabra} para confirmar.`), inp, err),
    actions: (close) => [
      el('button', { class: 'btn ghost', onclick: close }, 'Cancelar'),
      el('button', { class: 'btn danger', onclick: async (e) => {
        if (inp.value.trim().toUpperCase() !== palabra) { err.textContent = `Escribe ${palabra}.`; return; }
        e.target.disabled = true;
        try { const r = await accion(); close(); alert(r && r.mensaje ? r.mensaje : 'Hecho.'); }
        catch (x) { err.textContent = x.message; e.target.disabled = false; }
      } }, 'Confirmar')],
  });
}

function panelDisco() {
  const caja = el('div', { class: 'st-rows', style: 'margin-top:14px' },
    el('div', { class: 'muted' }, 'Disco externo: consultando…'));
  const pintar = (d) => {
    caja.innerHTML = '';
    const fila = (k, v) => el('div', { class: 'st-row' }, el('span', { class: 'st-k' }, k),
      el('span', { class: 'st-v' }, v));
    caja.append(el('div', { style: 'font-weight:700;margin-bottom:6px' }, '🗄️ Disco externo (backup)'));
    if (!d.montado) {
      caja.append(fila('Estado', 'no montado'),
        el('div', { class: 'acciones' },
          el('button', { class: 'btn sm', onclick: async (e) => {
            e.target.disabled = true;
            try { pintar(await api('/disco/montar', { method: 'POST' })); }
            catch (x) { alert(x.message); e.target.disabled = false; }
          } }, 'Montar')));
    } else {
      const b = d.backup || {};
      caja.append(fila('Estado', `montado · ${d.libre_gb} GB libres de ${d.total_gb} GB`),
        fila('Último espejo', d.espejo ? `${fmtDate(d.espejo.fecha)} · ${d.espejo.fotos_nuevas} fotos nuevas` : 'todavía ninguno'),
        fila('Packs de campaña', String((d.packs || []).length)),
        ...(d.packs || []).slice(-5).map(p => fila('  · ' + p.invernadero, `${p.fichero} · ${p.mb} MB`)),
        b.en_curso ? fila('Copia en curso', `invernadero ${b.invernadero_id} desde ${fmtDate(b.inicio)}`) : null,
        !b.en_curso && b.error ? fila('Última copia', '❌ ' + b.error) : null,
        !b.en_curso && b.resultado ? fila('Última copia', `✅ ${b.resultado.fotos} fotos · ${b.resultado.mb} MB · verificada`) : null);
      const sel = el('select', {});
      api('/greenhouses').then(ghs => ghs.forEach(g => sel.append(el('option', { value: g.id }, g.nombre)))).catch(() => {});
      caja.append(el('div', { class: 'acciones', style: 'flex-wrap:wrap;gap:6px' },
        sel,
        el('button', { class: 'btn sm', disabled: !!b.en_curso, onclick: async () => {
          try { await api(`/disco/backup/invernadero/${sel.value}`, { method: 'POST', body: '{}' });
                setTimeout(() => api('/disco').then(pintar), 1500); }
          catch (x) { alert(x.message); }
        } }, '📦 Backup de la campaña'),
        el('button', { class: 'btn ghost sm', onclick: () => confirmarEscribiendo(
          'Expulsar el disco externo',
          'Se desmonta y se apaga para poder desenchufarlo. Hasta que lo vuelvas a enchufar no '
          + 'habrá espejo diario ni backups.', 'EXPULSAR',
          () => api('/disco/expulsar', { method: 'POST', body: JSON.stringify({ confirmar: 'EXPULSAR' }) })) },
        '⏏ Expulsar')));
    }
    caja.append(el('div', { class: 'acciones', style: 'margin-top:10px' },
      el('button', { class: 'btn danger sm peligro', onclick: () => confirmarEscribiendo(
        'Apagar el servidor',
        'Se desmonta el disco y la Raspberry se apaga en 5 s. La web y los avisos dejan de '
        + 'funcionar hasta que le vuelvas a dar corriente; las trampas guardan las fotos y las '
        + 'suben al volver.', 'APAGAR',
        () => api('/sistema/apagar', { method: 'POST', body: JSON.stringify({ confirmar: 'APAGAR' }) })) },
      '⏻ Apagar el servidor (para moverlo)')));
  };
  api('/disco').then(pintar).catch(x => { caja.innerHTML = ''; caja.append(el('div', { class: 'muted' }, 'Disco externo: ' + x.message)); });
  return caja;
}

/* ============================================================= VISTAS */

/* ----- Galería ----- */
async function viewGaleria(app) {
  app.innerHTML = '';
  const state = { invernadero: '', sector: '', clase: '', trampa: '', page: 1 };
  const PAGE_SIZE = 120;
  const ghs = await api('/greenhouses').catch(() => []);
  const traps = await api('/traps').catch(() => []);
  const s = await api('/stats').catch(() => ({}));

  galSelected.clear();

  // Sube un fichero con XHR para poder medir el progreso real de subida (fetch no lo expone).
  function xhrUpload(file, onProgress) {
    return new Promise((resolve) => {
      const fd = new FormData();
      fd.append('image', file);
      fd.append('device_id', 'pc-' + (ME ? ME.username : 'web'));
      fd.append('captured_at', new Date().toISOString());
      const xhr = new XMLHttpRequest();
      xhr.open('POST', API + '/ingest');
      xhr.withCredentials = true;
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      xhr.onload = () => resolve(xhr.status >= 200 && xhr.status < 300);
      xhr.onerror = () => resolve(false);
      xhr.send(fd);
    });
  }

  // Sube uno o varios ficheros (botón «Subir imagen» o arrastrar-soltar) con barra de progreso.
  async function uploadFiles(files) {
    const imgs = [...files].filter(f => f.type.startsWith('image/'));
    if (!imgs.length) return;
    const pctTxt = el('b', {}, '0%');
    const nameTxt = el('div', { class: 'up-name' }, 'Preparando…');
    const fill = el('div', { class: 'up-fill' });
    const panel = el('div', { class: 'up-panel' },
      el('div', { class: 'up-top' },
        el('span', {}, `⬆ Subiendo ${imgs.length} imagen${imgs.length > 1 ? 'es' : ''}`),
        el('div', { class: 'spacer' }), pctTxt),
      el('div', { class: 'up-track' }, fill),
      nameTxt);
    document.body.append(panel);
    const setPct = (frac) => { const p = Math.round(frac * 100); fill.style.width = p + '%'; pctTxt.textContent = p + '%'; };
    let ok = 0;
    for (let i = 0; i < imgs.length; i++) {
      const f = imgs[i];
      nameTxt.textContent = `(${i + 1}/${imgs.length}) ${f.name}`;
      const done = await xhrUpload(f, (frac) => setPct((i + frac) / imgs.length));
      if (done) ok++;
      setPct((i + 1) / imgs.length);
    }
    panel.classList.add(ok === imgs.length ? 'up-ok' : 'up-err');
    pctTxt.textContent = ok === imgs.length ? '✓' : '!';
    nameTxt.textContent = `${ok}/${imgs.length} subida(s) · la IA las analizará en breve`;
    setTimeout(() => panel.remove(), 2800);
    invalidateApiCache();            // refresca KPIs/stats tras subir
    setTimeout(load, 1800);
  }
  const fileInput = el('input', {
    type: 'file', accept: 'image/*', multiple: 'multiple', style: 'display:none',
    onchange: e => { uploadFiles(e.target.files); e.target.value = ''; },
  });

  // Barra de descarga en grupo (aparece al seleccionar imágenes).
  const selCount = el('b', {}, '0');
  const selBar = el('div', { class: 'gal-selbar', style: 'display:none' },
    el('span', {}, selCount, ' sel.'),
    el('button', {
      class: 'btn sm', onclick: () => {
        if (!galSelected.size) return;
        const a = el('a', { href: `${API}/images/zip?ids=${[...galSelected].join(',')}`, download: 'entomira_imagenes.zip' });
        document.body.append(a); a.click(); a.remove();
      }
    }, '⬇ Descargar'),
    // «Quitar» solo vaciaba la SELECCIÓN, pero se leía como «quitar estas imágenes» y el
    // usuario esperaba que las borrase. Ahora el nombre dice lo que hace y al lado está el
    // borrado de verdad, que antes sencillamente no existía.
    ME && ME.rol === 'admin'
      ? el('button', {
        class: 'btn danger sm', onclick: async () => {
          const n = galSelected.size;
          if (!n) return;
          if (!confirm(`¿Borrar ${n} ${n === 1 ? 'imagen seleccionada' : 'imágenes seleccionadas'} `
                       + `del servidor?\n\nSe borran el fichero y sus detecciones. `
                       + `Esta acción NO se puede deshacer.`)) return;
          try {
            const r = await api(`/images?ids=${[...galSelected].join(',')}`, { method: 'DELETE' });
            toast(`Borradas ${r.borradas} ${r.borradas === 1 ? 'imagen' : 'imágenes'} `
                  + `(${r.liberado_mb} MB).`);
            galSelected.clear();
            window.__galSelChanged();
            invalidateApiCache();
            load();
          } catch (e) { toast(e.message, true); }
        }
      }, '🗑 Borrar')
      : null,
    el('button', { class: 'btn ghost sm', onclick: () => { galSelected.clear(); window.__galSelChanged(); load(); } }, 'Deseleccionar'));
  window.__galSelChanged = () => {
    selCount.textContent = galSelected.size;
    selBar.style.display = galSelected.size ? 'flex' : 'none';
  };

  // Vaciar la galería: borra las imágenes del servidor (ficheros + registros). Solo admin.
  // Doble confirmación porque es irreversible; las de demostración se conservan.
  async function vaciarGaleria() {
    let total = 0;
    try { total = (await api('/images/count')).total || 0; } catch {}
    if (!total) { toast('La galería ya está vacía.'); return; }
    if (!confirm(
      `¿Vaciar la galería?\n\nSe borrarán ${total} imágenes del servidor: los ficheros ` +
      `y sus registros, junto con las detecciones asociadas.\n\n` +
      `Las imágenes de demostración se conservan.\n\nEsta acción NO se puede deshacer.`)) return;
    if (!confirm('Última confirmación: el borrado es definitivo. ¿Continuar?')) return;
    const btn = $('#btn-vaciar');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Vaciando…'; }
    try {
      const r = await api('/images', { method: 'DELETE' });
      invalidateApiCache();
      // Las de demostración se conservan a propósito (alimentan la vista Demo), pero hay que
      // DECIRLO: si no, la galería sigue mostrando fotos después de «vaciar» y parece que el
      // botón no ha hecho nada. Es exactamente la confusión que reportó el usuario.
      if (r.conservadas_demo) {
        toast(`Vaciada: ${r.borradas} imágenes (${r.liberado_mb} MB). Quedan `
              + `${r.conservadas_demo} de demostración.`);
        router();
        if (confirm(
          `Se han borrado ${r.borradas} imágenes.\n\n`
          + `Quedan ${r.conservadas_demo} imágenes de DEMOSTRACIÓN, que se conservan porque `
          + `son las que usa la vista Demo cuando no hay trampas conectadas.\n\n`
          + `¿Borrar también esas y dejar la galería completamente vacía?`)) {
          const r2 = await api('/images?incluir_demo=true', { method: 'DELETE' });
          invalidateApiCache();
          toast(`Galería completamente vacía: ${r2.borradas} imágenes más borradas.`);
          router();
        }
      } else {
        toast(`Galería vaciada: ${r.borradas} imágenes, ${r.liberado_mb} MB liberados.`);
        router();                     // repinta la vista con los KPIs ya a cero
      }
    } catch (e) {
      toast(e.message, true);
      if (btn) { btn.disabled = false; btn.textContent = '🗑 Vaciar galería'; }
    }
  }

  const head = el('div', { class: 'view-head' },
    el('h1', {}, ME && ME.rol !== 'admin' ? 'Mis fotos' : 'Galería'),
    el('div', { class: 'spacer' }),
    selBar,
    ME && ME.rol === 'admin'
      ? el('button', { class: 'btn danger', id: 'btn-vaciar', onclick: vaciarGaleria },
           '🗑 Vaciar galería')
      : null,
    el('button', { class: 'btn', onclick: () => fileInput.click() }, '⬆ Subir imagen'),
    fileInput);
  app.append(head, renderKpis(s));
  if (ME && ME.rol === 'admin') app.append(renderComposition(s));
  if (ME && ME.rol === 'agricultor') app.append(renderPoblacion(s));
  const grid = el('div', {
    class: 'grid gal',
    ondragover: e => { e.preventDefault(); grid.classList.add('dropping'); },
    ondragleave: () => grid.classList.remove('dropping'),
    ondrop: e => { e.preventDefault(); grid.classList.remove('dropping'); uploadFiles(e.dataTransfer.files); },
  }, el('div', { class: 'spinner' }));

  // cualquier cambio de filtro vuelve a la página 1
  const onFilter = (key) => (e) => { state[key] = e.target.value; state.page = 1; load(); };
  const selGh = el('select', { onchange: onFilter('invernadero') },
    el('option', { value: '' }, 'Todos los invernaderos'),
    ...ghs.map(g => el('option', { value: g.id }, g.nombre)));
  const selSec = el('select', { onchange: onFilter('sector') },
    el('option', { value: '' }, 'Todos los sectores'),
    ...SECTORES.map(s => el('option', { value: s }, 'Sector ' + s)));
  const selTrap = el('select', { onchange: onFilter('trampa') },
    el('option', { value: '' }, 'Todas las trampas'),
    ...traps.map(t => el('option', { value: t.id }, t.alias)));
  const selCls = el('select', { onchange: onFilter('clase') },
    el('option', { value: '' }, 'Todas las especies'),
    ...['WF', 'TR'].map(c => el('option', { value: c }, clsName(c))));

  const pager = el('div', { class: 'pager' });
  app.append(el('div', { class: 'filters' }, selGh, selSec, selTrap, selCls), grid, pager);

  function baseParams() {
    const qs = new URLSearchParams();
    if (state.invernadero) qs.set('invernadero', state.invernadero);
    if (state.sector) qs.set('sector', state.sector);
    if (state.trampa) qs.set('trampa', state.trampa);
    if (state.clase) qs.set('clase', state.clase);
    return qs;
  }
  async function load() {
    grid.innerHTML = ''; grid.append(el('div', { class: 'spinner' })); pager.innerHTML = '';
    // total con los filtros actuales → nº de páginas
    let total = 0;
    try { total = (await api('/images/count?' + baseParams().toString())).total || 0; }
    catch (e) { grid.innerHTML = ''; toast(e.message, true); return; }
    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (state.page > pages) state.page = pages;
    // imágenes de la página actual
    let imgs = [];
    const qs = baseParams();
    qs.set('limit', PAGE_SIZE); qs.set('offset', (state.page - 1) * PAGE_SIZE);
    try { imgs = await api('/images?' + qs.toString()); } catch (e) { toast(e.message, true); }
    grid.innerHTML = '';
    if (!total) {
      grid.append(emptyState('🖼️', 'Aún no hay imágenes', 'Las capturas aparecerán aquí una vez procesadas por la IA.'));
      return;
    }
    imgs.forEach(im => grid.append(galCard(im)));
    renderPager(pages, total);
  }

  // Actualización SIN parpadeo: en vez de repintar la vista (que recarga todas las
  // miniaturas y da un fogonazo), se piden las últimas y se anteponen SOLO las que no
  // están. Lo que ya se está mirando no se toca.
  window.__galSuave = async () => {
    if (state.page !== 1) return;              // en páginas antiguas no entra nada nuevo
    const qs = baseParams();
    qs.set('limit', PAGE_SIZE); qs.set('offset', 0);
    let imgs = [];
    try { imgs = await api('/images?' + qs.toString()); } catch { return; }
    const presentes = new Set([...grid.querySelectorAll('[data-img-id]')]
      .map(n => Number(n.dataset.imgId)));
    const nuevas = imgs.filter(im => !presentes.has(im.id));
    if (!nuevas.length) return;
    // de la más antigua a la más nueva, para que la última quede arriba del todo
    nuevas.reverse().forEach(im => grid.prepend(galCard(im)));
    // se recortan las que sobran por abajo: la rejilla no crece sin fin
    const tarjetas = grid.querySelectorAll('[data-img-id]');
    for (let i = PAGE_SIZE; i < tarjetas.length; i++) tarjetas[i].remove();
  };
  function renderPager(pages, total) {
    pager.innerHTML = '';
    const go = (p) => { state.page = Math.min(pages, Math.max(1, p)); load(); window.scrollTo({ top: 0, behavior: 'smooth' }); };
    const btn = (label, p, opts = {}) => el('button', {
      class: 'page-btn' + (opts.active ? ' active' : '') + (opts.disabled ? ' disabled' : ''),
      disabled: opts.disabled ? 'disabled' : null,
      onclick: opts.disabled || opts.active ? null : () => go(p),
    }, label);
    if (pages <= 1) {  // una sola página: solo el recuento
      pager.append(el('span', { class: 'page-info' }, `${total} ${total === 1 ? 'imagen' : 'imágenes'}`));
      return;
    }
    // ventana de páginas: 1 … (p-2..p+2) … última, con elipsis
    const nums = new Set([1, pages]);
    for (let i = state.page - 2; i <= state.page + 2; i++) if (i >= 1 && i <= pages) nums.add(i);
    const ordered = [...nums].sort((a, b) => a - b);
    pager.append(btn('‹', state.page - 1, { disabled: state.page === 1 }));
    let prev = 0;
    for (const n of ordered) {
      if (n - prev > 1) pager.append(el('span', { class: 'page-gap' }, '…'));
      pager.append(btn(String(n), n, { active: n === state.page }));
      prev = n;
    }
    pager.append(btn('›', state.page + 1, { disabled: state.page === pages }));
    pager.append(el('span', { class: 'page-info' }, `${total} imágenes · pág. ${state.page}/${pages}`));
  }
  load();
}
function renderPoblacion(s) {
  const especies = [
    ['WF', 'Mosca blanca', '#E04449'], ['TR', 'Trips', '#B45309'],
  ];
  const valores = especies.map(([k, nombre, color]) =>
    [nombre, color, Number((s.por_especie || {})[k] || 0)]);
  const visibles = valores.filter(([, , n]) => n > 0);
  const cont = el('div', {}, el('div', { class: 'card', style: 'margin-bottom:18px' },
    el('div', { class: 'body' },
      el('div', { style: 'font-weight:700;margin-bottom:8px' }, 'Población por especie en las tarjetas'),
      el('div', { class: 'complegend' },
        ...(visibles.length ? visibles : valores).map(([nombre, color, n]) =>
          el('span', {}, el('span', { class: 'dot', style: `background:${color}` }), `${nombre} ${n}`))))));
  if ((s.pendientes ?? 0) > 0) cont.append(el('div', { class: 'nota-pendiente' },
    `${s.pendientes} foto${s.pendientes === 1 ? '' : 's'} sin analizar: el recuento todavía no las incluye.`));
  return cont;
}
const galSelected = new Set();
function galCard(im, base = '/images', readOnly = false) {
  const wrap = el('div', { class: 'thumb-wrap' },
    el('img', { src: `${API}${base}/${im.id}/thumb`, loading: 'lazy', alt: '' }),
    el('span', { class: 'count' }, `🐛 ${im.num_insectos}`));
  wrap.dataset.imgId = im.id;   // identifica la tarjeta para la actualizacion suave
  if (im.es_demo) wrap.append(el('span', { class: 'demo-badge' }, 'DEMO'));
  // Pie compacto: dos renglones en vez de cuatro elementos sueltos con `space-between`.
  // Con 120 fotos por pagina, cada 20 px de mas por celda son 500 px de scroll; el pie medía
  // casi tanto como la miniatura. El invernadero y el sector van juntos en una linea, y el
  // estado solo grita cuando dice algo (`pendiente`); «IA» es lo normal y no necesita etiqueta.
  const card = el('div', { class: 'card gal-card' + (!readOnly && galSelected.has(im.id) ? ' selected' : ''), onclick: () => openImage(im, base, readOnly) },
    wrap,
    el('div', { class: 'body' },
      el('div', { class: 'row' },
        el('b', {}, im.alias || '—'),
        el('span', { class: 'muted', style: 'font-size:11.5px' }, fmtDate(im.capturada))),
      el('div', { class: 'row', style: 'margin-top:3px' },
        el('span', { class: 'muted gal-sitio' },
          (im.invernadero_nombre || 'Sin invernadero') + (im.sector ? ' · ' + im.sector : '')),
        im.estado === 'procesada' ? el('span', { class: 'muted', style: 'font-size:11px' },
                                       im.modelo_version || 'IA')
          : el('span', { class: 'tag amber' }, 'pendiente'))));
  // Casilla de selección (para descarga en grupo). No abre el detalle.
  const chk = el('input', {
    type: 'checkbox', class: 'gal-sel', checked: galSelected.has(im.id) ? 'checked' : null,
    title: 'Seleccionar', onclick: e => e.stopPropagation(),
    onchange: e => {
      if (e.target.checked) galSelected.add(im.id); else galSelected.delete(im.id);
      card.classList.toggle('selected', e.target.checked);
      if (window.__galSelChanged) window.__galSelChanged();
    },
  });
  if (!readOnly) wrap.append(chk);
  return card;
}
// Visor a pantalla completa: encaja la imagen ENTERA a pantalla con sus cajas de detección;
// al clicar la imagen se amplía a resolución completa (con scroll para hacer pan). Descarga.
// Cuántas detecciones caben con etiqueta antes de que el texto tape la foto. Por encima se
// pintan solo las cajas —con el COLOR de la especie, que es la información que de verdad se
// mira— y las etiquetas pasan a un interruptor.
const ETIQUETAS_MAX = 12;

// Pinta las cajas de una imagen. Devuelve el conteo por especie, para la leyenda.
function pintaDetecciones(layer, dets, forzarEtiquetas = false) {
  const lista = dets || [];
  const conEtiqueta = forzarEtiquetas || lista.length <= ETIQUETAS_MAX;
  const cuenta = {};
  lista.forEach(d => {
    cuenta[d.clase] = (cuenta[d.clase] || 0) + 1;
    const col = (CLASE_META[d.clase] || {}).color || '#B4FF48';
    const b = el('div', {
      class: 'bbox' + (conEtiqueta ? '' : ' sin-etiqueta'),
      style: `left:${(d.x - d.w / 2) * 100}%;top:${(d.y - d.h / 2) * 100}%;`
           + `width:${d.w * 100}%;height:${d.h * 100}%;--c:${col}`,
      title: `${clsName(d.clase)} ${(d.confianza * 100).toFixed(0)}%`,
    }, el('span', {}, `${clsName(d.clase)} ${(d.confianza * 100).toFixed(0)}%`));
    layer.append(b);
  });
  return cuenta;
}

// La leyenda sustituye a las etiquetas cuando hay demasiadas: dice lo mismo (qué especie y
// cuántas) sin taparlo todo, y el color la ata con las cajas.
function leyendaEspecies(cuenta, total) {
  const items = Object.entries(cuenta).sort((a, b) => b[1] - a[1]).map(([k, n]) => {
    const e = CLASE_META[k] || { color: '#B4FF48', name: k, emoji: '•' };
    return el('span', { class: 'esp-chip' },
      el('i', { style: `background:${e.color}` }), `${e.emoji || ''} ${e.name || k}`,
      el('b', {}, String(n)));
  });
  if (!items.length) return el('div', { class: 'muted', style: 'font-size:13px' }, 'Sin detecciones.');
  return el('div', { class: 'esp-leyenda' }, ...items,
    total > ETIQUETAS_MAX
      ? el('span', { class: 'muted', style: 'font-size:12px' },
          '· con tantas detecciones, las etiquetas se ocultan para no tapar la foto')
      : null);
}

function lightbox(url, dets, name) {
  let verEtiquetas = (dets || []).length <= ETIQUETAS_MAX;
  const ov = el('div', { class: 'lightbox', onclick: () => close() });
  const stage = el('div', { class: 'lb-stage' });
  const img = el('img', { class: 'lb-img', src: url });
  const layer = el('div', { class: 'bbox-layer' });
  stage.append(img, layer);
  let zoomed = false;
  const sizeFit = () => {
    const nw = stage._nw, nh = stage._nh;
    if (!nw) return;
    if (zoomed) { stage.style.width = nw + 'px'; stage.style.height = nh + 'px'; }
    else {
      // SE MIDE EL HUECO QUE HAY DE VERDAD, no un 96 % y un 92 % a ojo.
      //
      // Los porcentajes fijos daban por supuesto que el visor ocupa toda la ventana, y dejo
      // de ser cierto al meterle relleno arriba para que los botones no tapen la foto: el
      // escenario mas el relleno pasaban de la altura disponible, asi que la imagen AJUSTADA
      // ya salia con barra de desplazamiento. Se nota sobre todo en ventanas bajas y en
      // apaisado, que es justo como se mira un movil. Hallazgo de Codex, 2026-09-20.
      const cs = getComputedStyle(ov);
      const hueco = (lado, a, b) => Math.max(120,
        ov['client' + lado] - parseFloat(cs['padding' + a]) - parseFloat(cs['padding' + b]));
      const f = Math.min(hueco('Width', 'Left', 'Right') / nw,
                         hueco('Height', 'Top', 'Bottom') / nh, 1);
      stage.style.width = (nw * f) + 'px'; stage.style.height = (nh * f) + 'px';
    }
  };
  img.onload = () => {
    stage._nw = img.naturalWidth; stage._nh = img.naturalHeight;
    sizeFit();
    layer.innerHTML = '';
    pintaDetecciones(layer, dets, verEtiquetas);
  };
  const toggleZoomAt = (clientX, clientY) => {
    const before = stage.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (clientX - before.left) / before.width));
    const y = Math.max(0, Math.min(1, (clientY - before.top) / before.height));
    zoomed = !zoomed;
    stage.classList.toggle('zoomed', zoomed);
    sizeFit();

    // Conserva bajo el puntero el punto pulsado al cambiar el tamaño. Hay que esperar a
    // que el navegador recalcule también los márgenes automáticos del escenario.
    requestAnimationFrame(() => {
      const after = stage.getBoundingClientRect();
      ov.scrollLeft += after.left + x * after.width - clientX;
      ov.scrollTop += after.top + y * after.height - clientY;
    });
  };
  let drag = null;
  stage.onpointerdown = e => {
    if (e.button !== 0 || !e.isPrimary) return;
    e.stopPropagation();
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY,
      scrollLeft: ov.scrollLeft, scrollTop: ov.scrollTop, moved: false };
    stage.setPointerCapture(e.pointerId);
  };
  stage.onpointermove = e => {
    if (!drag || drag.id !== e.pointerId) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.hypot(dx, dy) > 5) drag.moved = true;
    if (!zoomed || !drag.moved) return;
    e.preventDefault();
    stage.classList.add('dragging');
    ov.scrollLeft = drag.scrollLeft - dx;
    ov.scrollTop = drag.scrollTop - dy;
  };
  stage.onpointerup = e => {
    if (!drag || drag.id !== e.pointerId) return;
    e.stopPropagation();
    const wasDrag = drag.moved;
    drag = null;
    stage.classList.remove('dragging');
    if (stage.hasPointerCapture(e.pointerId)) stage.releasePointerCapture(e.pointerId);
    if (!wasDrag) toggleZoomAt(e.clientX, e.clientY);
  };
  stage.onpointercancel = () => { drag = null; stage.classList.remove('dragging'); };
  // El click sintético posterior al pointerup no debe llegar al fondo y cerrar el visor.
  stage.onclick = e => e.stopPropagation();
  const close = () => { ov.remove(); document.removeEventListener('keydown', esc); };
  const esc = e => { if (e.key === 'Escape') close(); };
  const tools = el('div', { class: 'lb-tools' },
    // Con muchas detecciones las etiquetas se ocultan solas; este botón las recupera para
    // quien quiera leerlas, sin obligar a nadie a tragárselas.
    el('button', { class: 'lb-btn', title: 'Mostrar / ocultar etiquetas',
      onclick: e => {
        e.stopPropagation();
        verEtiquetas = !verEtiquetas;
        layer.innerHTML = '';
        pintaDetecciones(layer, dets, verEtiquetas);
      } }, '🏷'),
    el('a', { class: 'lb-btn', href: url, download: name || 'entomira.jpg', title: 'Descargar', onclick: e => e.stopPropagation() }, '⬇'),
    el('button', { class: 'lb-btn', title: 'Cerrar (Esc)', onclick: close }, '✕'));
  ov.append(stage, tools);
  document.body.append(ov);
  // SE VUELVE A AJUSTAR YA DENTRO DEL DOM. `sizeFit()` mide el hueco real del visor, y si la
  // imagen venia de cache su `onload` dispara ANTES de este `append`: entonces el visor aun
  // no tiene tamaño, se mediria cero y la foto saldria diminuta. Llamar otra vez aqui es
  // barato y quita ese caso de en medio.
  sizeFit();
  document.addEventListener('keydown', esc);
}

async function openImage(im, base = '/images', readOnly = false) {
  const full = readOnly ? im : await api(`${base}/${im.id}`).catch(() => im);
  // Las cajas se superponen al recorte; solo el admin puede abrir el original.
  let original = false;
  let conRecuadros = true;
  const urlActual = () => `${API}${base}/${im.id}/${original ? 'original' : 'file'}`;
  const stage = el('div', {
    class: 'thumb-wrap', style: 'max-height:70vh;max-width:100%;width:auto;margin:0 auto;cursor:zoom-in',
    title: 'Ver a resolución completa',
    onclick: () => lightbox(urlActual(), original || !conRecuadros ? [] : full.detecciones,
                            `entomira_${im.id}${original ? '_original' : ''}.jpg`),
  }, el('img', { src: urlActual(), style: 'position:relative;object-fit:contain' }));
  const layer = el('div', { class: 'bbox-layer' });
  stage.append(layer);
  const botonCajas = el('button', { class: 'btn ghost sm',
    'aria-pressed': 'true', onclick: () => {
    conRecuadros = !conRecuadros;
    botonCajas.textContent = conRecuadros ? 'Ocultar recuadros' : 'Mostrar recuadros';
    botonCajas.setAttribute('aria-pressed', String(conRecuadros));
    layer.style.display = conRecuadros && !original ? '' : 'none';
  } }, 'Ocultar recuadros');
  const botonOriginal = !readOnly && ME && ME.rol === 'admin' && full.recortada ? el('button', { class: 'btn ghost sm', onclick: () => {
    original = !original;
    botonOriginal.textContent = original ? 'Ver recortada' : 'Ver sin recortar';
    layer.style.display = original || !conRecuadros ? 'none' : '';
    stage.querySelector('img').src = urlActual();
  } }, 'Ver sin recortar') : null;
  const img = stage.querySelector('img');
  img.onload = () => {
    // ajusta la caja al aspect-ratio real de la imagen para que las bboxes encuadren
    if (img.naturalWidth && img.naturalHeight)
      stage.style.aspectRatio = img.naturalWidth + ' / ' + img.naturalHeight;
    const cuenta = pintaDetecciones(layer, full.detecciones);
    if (cajaLeyenda) {
      cajaLeyenda.innerHTML = '';
      cajaLeyenda.append(leyendaEspecies(cuenta, (full.detecciones || []).length));
    }
  };
  const cajaLeyenda = el('div', { style: 'margin-top:10px' });
  modal({
    title: `${im.alias || 'Imagen'} · 🐛 ${full.num_insectos} insectos`,
    body: el('div', {}, stage,
      el('div', { style: 'margin-top:8px;text-align:center' }, botonCajas,
        ...(botonOriginal ? [botonOriginal] : [])),
      cajaLeyenda,
      el('div', { style: 'margin-top:12px' },
        kv('Invernadero', full.invernadero_nombre || '—'),
        kv('Sector', full.sector || '—'),
        kv('Capturada', fmtDate(full.capturada)),
        kv('Modelo', full.modelo_version || '—'),
        kv('Especies', speciesSummary(full.detecciones) || '—'),
        kv('Detecciones', String((full.detecciones || []).length)),
        // CON QUÉ SE TOMÓ. El resultado de una foto lo explica sobre todo el tamaño aparente
        // del insecto, así que sin la resolución y la exposición el conteo no se puede
        // interpretar ni comparar con otro. Las fotos anteriores a este registro no lo tienen.
        ...condicionesKv(full.condiciones))),
  });
}
function kv(k, v) { return el('div', { class: 'kv' }, el('span', { class: 'muted' }, k), el('b', {}, v)); }
/* Condiciones de captura de una imagen. Devuelve una lista de filas (vacía si la foto es
   anterior a que esto se registrara: mejor no enseñar nada que enseñar un dato inventado). */
function condicionesKv(c) {
  if (!c) return [];
  const filas = [kv('Resolución', c.resolucion || '—')];
  // `ev` son pasos de exposición de libcamera, no el `ae_level` -2..2 del ESP32. Se pinta con
  // el signo delante porque lo que interesa es si se subexpuso o se sobreexpuso.
  if (c.ev != null) filas.push(kv('Exposición (EV)', (c.ev > 0 ? '+' : '') + c.ev));
  if (c.completa) {
    // Ahora MÁS es MEJOR (es el `-q` de rpicam-still). Con el ESP32 era al revés y el número
    // suelto no lo delata, así que la etiqueta lleva el /100.
    filas.push(kv('Calidad JPEG', `${c.calidad_jpeg}/100`));
    filas.push(kv('Balance de blancos', nombreBalance(c.balance)));
    // El enfoque es media razón de este cambio: con la tarjeta a distancia fija del brazo,
    // saber con qué foco se disparó explica una foto borrosa mejor que ningún otro dato.
    filas.push(kv('Enfoque', c.enfoque === 'manual'
      ? `${nombreEnfoque(c.enfoque)} · ${distanciaFoco(c.lente_dioptrias)}`
      : nombreEnfoque(c.enfoque)));
  }
  if (c.px_mm != null) filas.push(kv('Escala', `${c.px_mm} px/mm`));
  filas.push(kv('Config', c.config_version != null
    ? `v${c.config_version}${c.completa ? '' : ' (sin detalle)'}` : '—'));
  return filas;
}

/* ----- Trampas ----- */
async function viewTrampas(app) {
  app.innerHTML = '';
  const page = el('section', { class: 'trampas-view', 'aria-label': 'Trampas' });
  app.append(page);
  const ghs = await api('/greenhouses').catch(() => []);
  if (!page.isConnected) return;
  const state = { invernadero: '', sector: '' };
  let revision = 0;
  const total = el('span', { class: 'trampas-total', 'aria-live': 'polite' }, 'Cargando…');
  const head = el('div', { class: 'view-head' },
    el('h1', {}, 'Trampas'), total, el('div', { class: 'spacer' }),
    el('button', { class: 'btn trap-primary', onclick: () => trapForm(ghs) }, '+ Añadir trampa'));
  const selGh = el('select', { 'aria-label': 'Invernadero', onchange: e => { state.invernadero = e.target.value; load(); } },
    el('option', { value: '' }, 'Todos los invernaderos'),
    ...ghs.map(g => el('option', { value: g.id }, g.nombre)));
  const selSec = el('select', { 'aria-label': 'Sector', onchange: e => { state.sector = e.target.value; load(); } },
    el('option', { value: '' }, 'Todos los sectores'),
    ...SECTORES.map(s => el('option', { value: s }, 'Sector ' + s)));
  const incidents = el('div', { class: 'trap-incidents', hidden: true, 'aria-live': 'polite' });
  const systemIncidents = el('div', { class: 'trap-incidents', hidden: true, 'aria-live': 'polite' });
  const grid = el('div', { class: 'trap-list', 'aria-live': 'polite' });
  const systemStatus = el('span', {}, 'Sistema · Comprobando estado…');
  const panels = el('div', { class: 'trap-system-panels' });
  const system = el('details', { class: 'trap-system' },
    el('summary', {}, systemStatus, el('span', { class: 'trap-system-link' }, 'Ver sistema')), panels);
  let panelsLoaded = false;
  system.addEventListener('toggle', () => {
    if (system.open && !panelsLoaded) { panelsLoaded = true; buildTrampasPanels(panels); }
  });
  page.append(head, systemIncidents, incidents, el('div', { class: 'filters' }, selGh, selSec), grid, system);
  api('/avisos').then(data => {
    if (!page.isConnected) return;
    systemStatus.textContent = data.total === 0 ? 'Sistema operativo' : `Sistema · ${data.total} incidencias`;
    systemIncidents.replaceChildren(...(data.avisos || []).map(a => el('p', {}, a.titulo)));
    systemIncidents.hidden = !(data.avisos || []).length;
  }).catch(() => { systemStatus.textContent = 'Sistema · Estado no disponible'; });

  function card(t, im, imageError) {
    const online = trapOnline(t);
    const name = t.alias || t.device_id;
    const processed = im?.estado === 'procesada' && im.num_insectos != null;
    const pending = im?.estado === 'pendiente';
    const issue = !online || imageError || (im && !processed);
    const preview = el('div', { class: 'trap-preview' });
    if (im) {
      const img = el('img', { src: `${API}/images/${im.id}/thumb`, alt: `Última captura de ${name}`, loading: 'lazy' });
      img.addEventListener('error', () => { preview.replaceChildren(el('span', {}, 'Imagen no disponible')); });
      preview.append(img, el('span', { class: 'trap-preview-label' }, 'Última captura'));
    } else preview.append(el('span', {}, imageError ? 'Imagen no disponible' : 'Sin capturas'));
    const result = processed ? `Último recuento: ${im.num_insectos} insectos`
      : pending ? 'Pendiente de análisis'
      : im?.estado === 'error' ? 'Análisis con error'
      : 'Recuento sin dato';
    const body = el('div', { class: 'trap-content' },
      el('div', { class: 'trap-identity' }, el('h2', {}, name),
        el('span', { class: 'tag ' + (online ? '' : 'trap-offline') }, textoEstado(t))),
      t.device_id && t.device_id !== name ? el('p', { class: 'muted' }, t.device_id) : null,
      el('p', {}, (t.invernadero_nombre || 'Sin invernadero') + (t.sector ? ' · Sector ' + t.sector : '') + (t.linea ? ' · Línea ' + t.linea : '')),
      el('div', { class: 'trap-result' + (processed ? '' : ' trap-result-unknown') },
        el('strong', {}, result),
        el('p', { class: 'muted' }, im ? `Capturada: ${fmtDate(im.capturada)}` : imageError ? 'No se ha podido consultar la última captura.' : 'Todavía no hay imágenes para analizar.'),
        processed && im.procesada ? el('p', { class: 'muted' }, 'Analizada: ' + fmtDate(im.procesada)) : null),
      el('p', {}, 'Último contacto: ' + (t.ultimo_visto ? fmtDate(t.ultimo_visto) : 'sin dato')),
      el('p', { class: 'muted trap-network', style: online ? '' : 'opacity:.72' }, [redTexto(t),
        `Batería ${bateriaTexto(t)}`].join(' · ')),
      el('p', { class: 'muted trap-history' }, t.num_imagenes != null ? `${t.num_imagenes} fotos en el histórico` : 'Histórico de fotos sin dato'),
      el('div', { class: 'trap-actions' },
        el('a', { class: 'btn trap-primary', href: '#/control/' + t.id }, 'Abrir control'),
        el('button', { class: 'btn ghost', onclick: () => abrirTendencia({ trampa_id: t.id }, name) }, '📈 Tendencia'),
        el('button', { class: 'btn ghost', onclick: () => trapForm(ghs, t) }, 'Editar'),
        el('button', { class: 'btn danger', 'aria-label': `Eliminar ${name}`, onclick: () => delTrap(t) }, 'Eliminar')));
    return { issue, node: el('article', { class: 'card trap-item' + (issue ? ' trap-needs-attention' : '') }, preview, body) };
  }
  // `suave` = actualizar SIN vaciar la rejilla. Lo usa el sondeo automático, y sin eso cada
  // refresco pintaba «Cargando trampas…» durante un instante: un fogonazo cada pocos segundos.
  async function load(suave = false) {
    const current = ++revision;
    if (!suave) {
      grid.replaceChildren(el('p', {}, 'Cargando trampas…'));
      incidents.hidden = true;
    }
    const qs = new URLSearchParams();
    if (state.invernadero) qs.set('invernadero', state.invernadero);
    if (state.sector) qs.set('sector', state.sector);
    try {
      const traps = (await api('/traps?' + qs)).filter(t => !t.es_movil);
      const cards = traps.map(t => card(t, t.ultima_imagen, false));
      if (current !== revision || !page.isConnected) return;
      total.textContent = `${traps.length} ${traps.length === 1 ? 'trampa' : 'trampas'}${state.invernadero || state.sector ? ' en este filtro' : ''}`;
      const issues = cards.filter(c => c.issue).length;
      incidents.textContent = issues ? `${issues} ${issues === 1 ? 'trampa requiere revisión' : 'trampas requieren revisión'} · Primero en la lista` : '';
      incidents.hidden = !issues;
      cards.sort((a, b) => Number(b.issue) - Number(a.issue));
      grid.replaceChildren(...cards.map(c => c.node));
      if (!traps.length) grid.append(emptyState('📷', 'No hay trampas', state.invernadero || state.sector
        ? 'No hay trampas con estos filtros.' : 'Añade una trampa o enciéndela para que se registre al enviar datos.'));
    } catch (e) {
      if (current !== revision || !page.isConnected) return;
      total.textContent = 'Sin dato';
      grid.replaceChildren(el('p', { role: 'alert' }, 'No se han podido cargar las trampas.'),
        el('button', { class: 'btn ghost', onclick: load }, 'Reintentar'));
    }
  }
  window.__reloadTraps = load;
  // ACTUALIZACIÓN EN CALIENTE DE «TRAMPAS».
  //
  // Antes el sondeo de cambios llamaba a `router()`, que reconstruye la vista ENTERA. Y esta
  // vista se repinta ante cambios de tipo `bateria`, que la trampa anuncia continuamente:
  // medido el 2026-09-19, **12 reconstrucciones en 60 segundos**, una cada cinco.
  //
  // Con el panel «Ver sistema» abierto eso se veía como una recarga en bucle, porque el
  // `<details>` vuelve a nacer cerrado en cada reconstrucción. Lo reportó Antonio: «cuando le
  // doy a Sistema operativo, Ver sistema, la página se recarga en bucle».
  //
  // `load(true)` sólo cambia las tarjetas; el panel, los filtros y el foco se quedan donde
  // estaban. Es lo mismo que ya hacían la galería (`__galSuave`) y el control (`__ctrlSuave`).
  window.__trampasSuave = () => load(true);
  onLeave(() => {
    revision++;
    if (window.__reloadTraps === load) window.__reloadTraps = null;
    delete window.__trampasSuave;
  });
  load();
}
function trapCard(t, ghs) {
  const online = trapOnline(t);
  // UNA sola etiqueta de estado. Habia dos —«● en linea» arriba y «En linea» debajo— que
  // salian de sitios distintos y podian contradecirse; desde que `en_linea` lo calcula solo el
  // servidor dicen siempre lo mismo, asi que repetirlo era ruido.
  return el('div', { class: 'card' }, el('div', { class: 'body' },
    el('div', { class: 'row' }, el('b', {}, t.alias),
      el('span', { class: online ? 'tag' : 'tag gray' }, online ? '● en línea' : '○ sin conexión')),
    el('div', { class: 'muted', style: 'font-size:12px;margin:4px 0 6px' }, t.device_id),
    el('div', { class: 'row', style: 'margin-bottom:8px' },
      el('span', { class: 'muted', style: `font-size:12px${online ? '' : ';opacity:.72'}`, title: (redInfo(t) || {}).ayuda || '' },
        redTexto(t) + (t.ip ? ` · ${online ? '' : 'IP conocida: '}${t.ip}` : ''))),
    el('div', { class: 'row' },
      el('span', { class: 'muted', style: 'font-size:13px' },
        (t.invernadero_nombre || 'Sin invernadero') +
        (t.sector ? ' · ' + t.sector : '') + (t.linea ? ' · L' + t.linea : '')),
      el('span', { class: 'muted', style: 'font-size:12px' },
        (t.bateria != null || t.bateria_estimada != null
          ? `${cargando(t) ? '⚡' : '🔋'} ${bateriaTexto(t)}${t.voltaje != null ? ` · ${t.voltaje.toFixed(2)} V` : ''} · `
          : '') + `${t.num_imagenes} img` +
        (t.ultimo_conteo != null ? ` · 🐛 ${t.ultimo_conteo}` : ''))),
    el('div', { class: 'acciones' },
      el('button', { class: 'btn sm', onclick: () => { location.hash = '#/control/' + t.id; } }, '🖥 Panel'),
      el('button', { class: 'btn ghost sm', onclick: () => trapForm(ghs, t) }, '✎ Editar'),
      // El streaming se abre solo a petición desde el panel de control.
      el('button', { class: 'btn danger sm peligro', onclick: () => delTrap(t) }, '🗑'))));
}
async function delTrap(t) {
  if (!confirm(`¿Eliminar la trampa ${t.alias}?`)) return;
  try { await api(`/traps/${t.id}`, { method: 'DELETE' }); toast('Trampa eliminada'); if (window.__reloadTraps) window.__reloadTraps(); }
  catch (e) { toast(e.message, true); }
}
function trapForm(ghs, t = null) {
  const alias = el('input', { value: t?.alias || '', placeholder: 'Alias' });
  const devId = el('input', { value: t?.device_id || '', placeholder: 'zero-trampa01…', disabled: t ? 'disabled' : null });
  const selGh = el('select', {}, el('option', { value: '' }, 'Sin asignar'),
    ...ghs.map(g => el('option', { value: g.id, selected: t?.invernadero_id === g.id ? 'selected' : null }, g.nombre)));
  const selSec = el('select', {}, el('option', { value: '' }, '—'),
    ...SECTORES.map(s => el('option', { value: s, selected: t?.sector === s ? 'selected' : null }, 'Sector ' + s)));
  const linea = el('input', { type: 'number', min: 1, placeholder: 'p. ej. 3', value: t?.linea ?? '' });
  modal({
    title: t ? 'Editar trampa' : 'Añadir trampa',
    body: el('div', { class: 'form-grid' },
      field('Alias', alias), field('Device ID', devId),
      field('Invernadero', selGh), field('Sector', selSec),
      field('Lineo', linea)),
    actions: (close) => [
      el('button', { class: 'btn ghost', onclick: close }, 'Cancelar'),
      el('button', { class: 'btn', onclick: async () => {
        try {
          const payload = { alias: alias.value, invernadero_id: selGh.value ? +selGh.value : null, sector: selSec.value || null, linea: linea.value ? +linea.value : null };
          if (t) await api(`/traps/${t.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
          else await api('/traps', { method: 'POST', body: JSON.stringify({ device_id: devId.value, ...payload }) });
          toast('Guardado'); close(); if (window.__reloadTraps) window.__reloadTraps();
        } catch (e) { toast(e.message, true); }
      } }, 'Guardar')],
  });
}
async function trapConfig(t, seccion = 'camara') {
  // Los valores por defecto son los del servidor (`ConfigCamara`), no los del ESP32.
  //
  // ⚠️ OJO AL NEUTRO, que aquí cambió de número sin cambiar de nombre: en libcamera el brillo
  // neutro es 0, pero el contraste y la saturación neutros son **1**. Un 0 en esos dos —que
  // con el ESP32 significaba «no toques nada»— da una foto gris y plana, y una foto gris no
  // da ningún error: se sube igual y se cuenta igual. Por eso cada campo lleva escrito su
  // rango Y su neutro, en vez de fiarlo a que alguien se acuerde.
  const c = t.config || {};
  const v = (x, d) => (x == null ? d : x);
  const num = (attrs, valor) => el('input', { type: 'number', ...attrs, value: valor });
  const inter = num({ min: 10, max: 86400, step: 1 }, v(c.intervalo_seg, 20));
  const campoInter = field(t.device_id?.startsWith('zero-')
    ? 'Intervalo captura en banco (s)' : 'Intervalo captura (s)', inter);
  // Sin resolución: la trampa dispara siempre a máxima (ver el panel de control).
  // La calidad sí, porque a 12 MP es lo único que decide el tamaño de la foto — y el tamaño
  // de la foto es el plan de datos de la trampa.
  const cal = num({ min: 1, max: 100, step: 1 }, v(c.calidad_jpeg, 95));
  const bri = num({ min: -1, max: 1, step: 0.1 }, v(c.brillo, 0));
  const con = num({ min: 0, max: 2, step: 0.1 }, v(c.contraste, 1));
  const sat = num({ min: 0, max: 2, step: 0.1 }, v(c.saturacion, 1));
  const ev = num({ min: -2, max: 2, step: 0.1 }, v(c.ev, 0));
  const bal = selectDe(BALANCES, v(c.balance, 'auto'));
  // EL ENFOQUE. Es media razón de esta migración: la cámara vieja no enfocaba, y la que la
  // sustituyó se calentó buscando foco sin parar. Con la tarjeta a distancia FIJA del brazo,
  // clavar el foco a mano vale más que cualquier automatismo — pero solo si se puede.
  const enf = selectDe(ENFOQUES, v(c.enfoque, 'auto'));
  const diop = num({ min: 0, max: 10, step: 0.1 }, v(c.lente_dioptrias, 0));
  const dist = el('span', { class: 'muted', style: 'font-size:12px' });
  const campoDiop = field('Distancia de enfoque (dioptrías)',
    el('div', { style: 'display:flex;gap:8px;align-items:center' }, diop, dist));
  const pintaFoco = () => {
    const manual = enf.value === 'manual';
    // Con enfoque automático el número no pinta nada: enseñarlo invita a tocarlo y a creer
    // que hace algo. Se esconde en vez de dejarlo ahí mintiendo.
    campoDiop.style.display = manual ? '' : 'none';
    dist.textContent = manual ? `≈ ${distanciaFoco(diop.value)}` : '';
  };
  const esZero = t.device_id?.startsWith('zero-');
  const modo = selectDe({ banco: 'Modo banco · siempre encendida',
                          invernadero: 'Modo invernadero · fotos programadas y corte' },
                        c.deep_sleep ? 'invernadero' : 'banco');
  const horas = [...(c.horas_despertar?.length ? c.horas_despertar : [c.hora_despertar || '08:00'])];
  const listaHoras = el('div', { style: 'display:grid;gap:8px;min-width:0' });
  const pintarHoras = () => {
    listaHoras.replaceChildren(...horas.map((valor, i) => {
      const entrada = el('input', { type: 'time', value: valor, step: '60',
        'aria-label': `Hora de despertar ${i + 1}`, style: 'min-width:0;flex:1' });
      entrada.oninput = () => { horas[i] = entrada.value; pintaNota(); };
      return el('div', { style: 'display:flex;gap:8px;min-width:0' }, entrada,
        el('button', { type: 'button', class: 'btn ghost', disabled: horas.length === 1 ? '' : null,
          'aria-label': `Eliminar hora ${valor}`, onclick: () => {
            if (horas.length > 1) { horas.splice(i, 1); pintarHoras(); pintaNota(); }
          } }, 'Eliminar'));
    }));
  };
  const campoHora = field('Horas de despertar · Europe/Madrid', el('div', {}, listaHoras,
    el('button', { type: 'button', class: 'btn ghost', onclick: () => {
      horas.push(''); pintarHoras(); pintaNota();
    } }, 'Añadir hora')));
  const vent = el('input', { type: 'number', min: 5, max: 300,
                             value: c.ventana_despierto_seg ?? 15 });
  const campoVent = field('Ventana despierta (s)', vent);
  const nota = el('p', { class: 'muted', style: 'margin:10px 0 0;font-size:12px;line-height:1.5' });
  const pintaNota = () => {
    const campo = esZero && modo.value === 'invernadero';
    campoInter.style.display = campo ? 'none' : '';
    campoHora.style.display = campo ? '' : 'none';
    campoVent.style.display = campo ? '' : 'none';
    nota.textContent = campo
      ? `La Zero despertará cada día a las ${horas.filter(Boolean).sort().join(', ') || '—'} (hora de Madrid), hará una foto, `
      + `esperará a que el servidor confirme la subida y cortará VOUT hasta el siguiente despertar. `
      + `Si cambias la hora mientras está apagada, en el despertar ya programado hará la foto `
      + `y después usará la lista nueva. Sin cobertura, guardará la foto, reintentará hasta 5 minutos y cortará hasta el siguiente horario. Siempre debe quedar al menos una hora.`
      : 'Modo banco: permanece encendida y disponible hasta que pidas un corte de alimentación.';
  };
  pintarHoras(); pintaNota(); pintaFoco();
  modo.onchange = pintaNota;
  enf.onchange = pintaFoco; diop.oninput = pintaFoco;
  modal({
    title: `${seccion === 'modo' ? 'Funcionamiento y horarios' : 'Parámetros de cámara'} · ${t.alias}`,
    className: 'modal-camera-config',
    body: el('div', {},
      el('div', { class: 'form-grid' },
        ...(seccion === 'modo'
          ? [campoInter, ...(esZero ? [field('Modo de funcionamiento', modo), campoHora, campoVent] : [])]
          : [field('Calidad JPEG (1–100, más = mejor)', cal),
             field('🎯 Enfoque', enf), campoDiop,
             field('💡 Balance de blancos', bal),
             field('Exposición EV (−2…2, 0 = neutro)', ev),
             field('Brillo (−1…1, 0 = neutro)', bri),
             field('Contraste (0…2, 1 = neutro)', con),
             field('Saturación (0…2, 1 = neutro)', sat)])),
      ...(seccion === 'modo' ? [nota] : [])),
    actions: (close) => [
      el('button', { class: 'btn ghost', onclick: close }, 'Cancelar'),
      el('button', { class: 'btn', onclick: async () => {
        try {
          if (seccion === 'modo' && esZero &&
              (!horas.length || horas.some(h => !/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(h)))) {
            throw new Error('Completa todas las horas; debe quedar al menos una.');
          }
          const payload = seccion === 'modo'
            ? { intervalo_seg: +inter.value,
                ...(esZero ? { deep_sleep: modo.value === 'invernadero' ? 1 : 0,
                               horas_despertar: [...new Set(horas)].sort(),
                               ventana_despierto_seg: +vent.value } : {}) }
            // Balance y enfoque conservan sus valores en inglés para rpicam-still.
            : { calidad_jpeg: +cal.value, brillo: +bri.value, contraste: +con.value,
                saturacion: +sat.value, ev: +ev.value, balance: bal.value,
                enfoque: enf.value, lente_dioptrias: +diop.value };
          await api(`/traps/${t.id}/config`, { method: 'PUT', body: JSON.stringify(payload) });
          toast('Configuración aplicada'); close();
          if (location.hash === `#/control/${t.id}`) router();
          else if (location.hash.startsWith('#/color')) {
            invalidateApiCache();
            viewColor($('#app'));
          }
          else if (window.__reloadTraps) window.__reloadTraps();
        } catch (e) { toast(e.message, true); }
      } }, 'Aplicar')],
  });
}
// Botón «tomar foto»: el vídeo va a baja resolución para que fluya, así que hace falta una
// forma de pedir la captura BUENA en el instante que interesa — encuadrando la trampa, por
// ejemplo. Vive aquí suelto porque lo usa el panel de control.
/**
 * Reinicia la placa a distancia.
 *
 * Hasta ahora reiniciar una trampa exigia tenerla delante con un cable. Con la trampa colgada
 * en un invernadero eso es un viaje, y una placa atascada deja de dar datos hasta que alguien
 * va. La orden viaja en la respuesta de la telemetria y la placa la obedece al leerla, asi que
 * tarda como mucho una ventana de retencion (~2 min por 4G).
 *
 * Con confirmacion a proposito: es la unica accion del panel que deja la trampa sin dar senales
 * durante ~40 s, y si esta subiendo una foto, esa foto se pierde.
 */
function botonReiniciarPlaca(trapId) {
  const aviso = el('div', { class: 'muted', style: 'font-size:13px;margin-top:8px' }, '');
  // Secundario a proposito: «Tomar foto» es lo que se usa a diario; reiniciar es el recurso
  // de cuando algo va mal. Pintarlos igual invita a pulsar el que no toca.
  const btn = el('button', { class: 'btn ghost', onclick: async () => {
    if (!confirm('¿Reiniciar la trampa?\n\nTardará ~40 s en volver y perderá lo que esté '
               + 'haciendo ahora mismo. La orden caduca sola en 5 minutos si no la recoge.')) return;
    btn.disabled = true;
    const antes = btn.textContent;
    btn.textContent = '⟳ Pidiendo…';
    try {
      // Se anota cuándo habló por última vez ANTES de pedir nada: así se puede saber de verdad
      // cuándo ha vuelto, en vez de prometer un tiempo y dejar al usuario mirando un botón.
      let visto0 = null;
      try { visto0 = (await api(`/traps/${trapId}`)).ultimo_visto; } catch { /* da igual */ }
      await api(`/traps/${trapId}/reiniciar`, { method: 'POST' });
      btn.textContent = '⟳ Reiniciando…';
      aviso.textContent = 'Orden enviada. Esperando a que la trampa la recoja…';

      // Se espera a verla volver. Sin esto el usuario pulsa y no pasa nada visible durante
      // hasta tres minutos —la telemetría va retenida— y concluye que el botón no funciona.
      const t0 = Date.now();
      const reloj = setInterval(async () => {
        let t;
        try { t = await api(`/traps/${trapId}`); } catch { return; }
        const seg = Math.round((Date.now() - t0) / 1000);
        if (visto0 && t.ultimo_visto && t.ultimo_visto !== visto0) {
          clearInterval(reloj);
          aviso.textContent = `✅ La placa ha vuelto (${seg} s). Firmware ${t.fw_version}.`;
          btn.disabled = false; btn.textContent = antes;
          if (window.__reloadTraps) window.__reloadTraps();
          return;
        }
        aviso.textContent = `Orden enviada. Esperando a que la placa vuelva… (${seg} s)`;
        if (seg > 240) {
          clearInterval(reloj);
          aviso.textContent = '⚠️ Cuatro minutos sin volver. La orden ya ha caducado; '
                            + 'mira si la trampa tiene cobertura.';
          btn.disabled = false; btn.textContent = antes;
        }
      }, 5000);
    } catch (e) {
      aviso.textContent = 'No se pudo pedir el reinicio: ' + e.message;
      btn.disabled = false; btn.textContent = antes;
    }
  } }, '⟳ Reiniciar trampa');
  return el('div', {}, btn, aviso);
}

/** En banco: corte de alimentación por Pololu, con o sin alarma RTC. */
function botonApagarPlaca(trapId, conRtc) {
  const aviso = el('div', { class: 'muted', style: 'font-size:13px;margin-top:8px' }, '');
  const cancelar = el('button', { class: 'btn ghost sm', hidden: '', onclick: async () => {
    try {
      await api(`/traps/${trapId}/apagar/cancelar`, { method: 'POST' });
      aviso.textContent = 'Orden de apagado retirada (si la trampa aún no la había recogido).';
    } catch (e) { aviso.textContent = 'No se pudo cancelar: ' + e.message; }
    cancelar.hidden = true; btn.disabled = false;
  } }, 'Cancelar');
  const btn = el('button', { class: 'btn ghost', onclick: () => {
    const d = new Date(Date.now() + 5 * 60000);
    const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
    const fechaCampo = el('input', { type: 'datetime-local', step: '60',
                                    value: local.toISOString().slice(0, 16) });
    const error = el('p', { role: 'alert', class: 'muted' }, '');
    modal({ title: 'Cortar alimentación', body: el('div', {},
      el('p', {}, conRtc
        ? 'El corte detiene la Zero y el Pololu deja VOUT sin alimentación. Puedes programar el encendido por RTC o elegir un corte sin alarma, que requiere un pulso A–GND para volver a encenderla. La fecha usa la hora de este navegador.'
        : 'Esta trampa requiere un encendido manual después del apagado.'),
      conRtc ? lbl('Volver a encender · fecha y hora', fechaCampo) : null, error),
    actions: close => [
      el('button', { class: 'btn ghost', onclick: close }, 'Cancelar'),
      ...(conRtc ? [
      el('button', { class: 'btn', onclick: async e => {
        const fecha = new Date(fechaCampo.value);
        const seg = (fecha.getTime() - Date.now()) / 1000;
        if (!Number.isFinite(seg) || seg < 180 || seg >= 27 * 86400) {
          error.textContent = 'Elige una fecha entre 3 minutos y 27 días desde ahora.';
          return;
        }
        e.currentTarget.disabled = true;
        try {
          await api(`/traps/${trapId}/apagar/despertar-en?cuando=${encodeURIComponent(fecha.toISOString())}`,
                    { method: 'POST' });
          aviso.textContent = 'Orden enviada; esperando confirmación del corte real…';
          close();
          const limite = Date.now() + 300000;
          while (Date.now() < limite) {
            await new Promise(resolve => setTimeout(resolve, 5000));
            let estado;
            try { estado = await api(`/traps/${trapId}`); } catch { continue; }
            if (estado.reposo_error) {
              aviso.textContent = `⚠️ Corte rechazado: ${estado.reposo_error}`;
              if (window.__reloadTraps) window.__reloadTraps();
              return;
            }
            const hasta = Date.parse(estado.reposo_hasta || '');
            if (estado.reposo_modo === 'apagado' && Math.abs(hasta - fecha.getTime()) <= 60000) {
              aviso.textContent = `✅ Corte confirmado; encendido programado para ${fecha.toLocaleString('es-ES')}.`;
              if (window.__reloadTraps) window.__reloadTraps();
              return;
            }
          }
          aviso.textContent = '⚠️ No llegó confirmación del corte en 5 minutos. Consulta el estado de la trampa.';
          if (window.__reloadTraps) window.__reloadTraps();
        } catch (err) {
          error.textContent = 'No se pudo pedir el apagado con encendido: ' + err.message;
          e.currentTarget.disabled = false;
        }
      } }, 'Cortar alimentación y programar encendido')] : []),
      el('button', { class: 'btn danger', onclick: async e => {
        if (!confirm(conRtc
          ? 'Confirmar apagado SIN alarma RTC: la Zero no volverá sola. Con VIN conectado, tendrás que encenderla con un pulso A–GND en el Pololu. ¿Continuar?'
          : 'La trampa quedará apagada hasta un encendido manual. ¿Continuar?')) return;
        e.currentTarget.disabled = true;
        try {
          await api(`/traps/${trapId}/apagar`, { method: 'POST' });
          aviso.textContent = conRtc
            ? 'Corte sin alarma solicitado. Cuando la Zero recoja la orden, el Pololu cortará VOUT. Para encenderla, pulsa A–GND.'
            : 'Orden de apagado enviada. Puedes cancelarla mientras la trampa no la haya recogido.';
          cancelar.hidden = false;
          close();
        } catch (err) {
          error.textContent = 'No se pudo pedir el apagado: ' + err.message;
          e.currentTarget.disabled = false;
        }
      } }, conRtc ? 'Cortar hasta encendido manual' : 'Apagar hasta encendido manual'),
    ] });
  } }, '⏻ Cortar alimentación');
  return el('div', {}, btn, cancelar, aviso);
}

function botonReposo(trapId) {
  const aviso = el('div', { class: 'muted', style: 'font-size:13px;margin-top:8px' }, '');
  const btn = el('button', { class: 'btn ghost', onclick: async () => {
    const valor = prompt('Minutos de pausa (2-1440):', '5');
    if (valor === null) return;
    const minutos = Number(valor);
      if (!Number.isInteger(minutos) || minutos < 2 || minutos > 1440) {
        aviso.textContent = 'Escribe un número entero entre 2 y 1440 minutos.';
      return;
    }
    btn.disabled = true;
    try {
      await api(`/traps/${trapId}/reposo?minutos=${minutos}`, { method: 'POST' });
      aviso.textContent = `Orden enviada. El estado cambiará al recibir el acuse; volverá `
                        + `automáticamente en unos ${minutos} min.`;
      if (window.__reloadTraps) window.__reloadTraps();
    } catch (e) {
      aviso.textContent = 'No se pudo pedir el reposo: ' + e.message;
    } finally { btn.disabled = false; }
  } }, '⏸ Reposo…');
  return el('div', {}, btn, aviso);
}

function botonTomarFoto(trapId) {
  const aviso = el('div', { class: 'muted', style: 'font-size:13px;margin-top:8px' }, '');
  const btn = el('button', { class: 'btn', onclick: async () => {
    btn.disabled = true;
    const antes = btn.textContent;
    btn.textContent = '📸 Pidiendo…';
    try {
      await api(`/traps/${trapId}/foto`, { method: 'POST' });
      aviso.textContent = 'Pedida. La trampa la hace en unos segundos y aparecerá en la Galería.';
      // Se espera a que entre y se avisa, en vez de dejar al usuario adivinando.
      const desde = Date.now();
      const espera = setInterval(async () => {
        let n = [];
        try { n = await api(`/images?trampa=${trapId}&limit=1`); } catch { return; }
        if (n.length && aFecha(n[0].capturada).getTime() > desde - 60000) {
          clearInterval(espera);
          aviso.textContent = `✅ Foto guardada en la Galería (${n[0].ancho}×${n[0].alto}).`;
        } else if (Date.now() - desde > 90000) {
          clearInterval(espera);
          aviso.textContent = 'La trampa no ha respondido; inténtalo de nuevo.';
        }
      }, 3000);
    } catch (e) {
      aviso.textContent = 'Error: ' + e.message;
    }
    btn.disabled = false;
    btn.textContent = antes;
  } }, '📸 Tomar foto');
  return el('div', {}, btn, aviso);
}

/* ----- Invernaderos ----- */
async function viewInvernaderos(app) {
  app.innerHTML = '';
  const head = el('div', { class: 'view-head' },
    el('h1', {}, 'Invernaderos'),
    el('div', { class: 'spacer' }),
    el('button', { class: 'btn', onclick: () => ghForm() }, '＋ Crear invernadero'));
  const grid = el('div', { class: 'grid gh' }, el('div', { class: 'spinner' }));
  app.append(head, grid);
  window.__reloadGh = async () => {
    grid.innerHTML = ''; grid.append(el('div', { class: 'spinner' }));
    let ghs = [];
    try { ghs = await api('/greenhouses'); } catch (e) { toast(e.message, true); }
    grid.innerHTML = '';
    if (!ghs.length) { grid.append(emptyState('🏡', 'No hay invernaderos', 'Crea uno para asignarle trampas y ver su mapa de calor.')); return; }
    for (const g of ghs) grid.append(await ghCard(g));
  };
  window.__reloadGh();
}
async function ghCard(g) {
  const hm = await api(`/greenhouses/${g.id}/heatmap`).catch(() => ({ sectors: {}, total: 0 }));
  const card = el('div', { class: 'card' }, el('div', { class: 'body' },
    el('div', { class: 'row' }, el('h3', { style: 'margin:0' }, g.nombre),
      // Capturas NUEVAS de la semana, no el acumulado de las tarjetas: es lo comparable
      // entre invernaderos y lo que indica si hay que actuar.
      el('span', { class: 'tag amber', title: `Capturas nuevas en los últimos ${hm.window_days ?? 7} días` },
        `📈 ${hm.total}/${hm.window_days ?? 7} d`)),
    el('div', { class: 'muted', style: 'font-size:13px;margin:6px 0 12px' },
      `${g.ubicacion || 'Sin ubicación'} · ${plural(g.num_trampas, 'trampa', 'trampas')} · ${plural(g.num_lineos, 'lineo', 'lineos')}`),
    await miniPlano(g),
    el('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' },
      `vista aérea del invernadero · presión de plaga de los últimos ${hm.window_days ?? 7} días`),
    el('div', { style: 'margin-top:12px' },
      kv('Responsable', g.responsable || '—'),
      kv('Contacto', g.contacto || '—')),
    el('div', { class: 'acciones' },
      el('button', { class: 'btn ghost sm', onclick: () => ghForm(g) }, '✎ Editar'),
      el('button', { class: 'btn ghost sm', onclick: () => copyClientLink(g) }, '🔗 Enlace cliente'),
      el('button', { class: 'btn ghost sm', onclick: () => { location.hash = '#/mapa'; } }, '🗺️ Ver en mapa'),
      el('button', { class: 'btn danger sm', onclick: () => delGh(g) }, '🗑'))));
  return card;
}
// Versión pequeña del mismo plano para la tarjeta de invernadero: misma forma, mismos
// colores, sin números. Que la miniatura y el plano grande sean el MISMO dibujo es lo que
// permite reconocer de un vistazo qué invernadero es cuál.
async function miniPlano(g) {
  const [data, traps] = await Promise.all([
    api(`/greenhouses/${g.id}/map`).catch(() => null),
    api(`/traps?invernadero=${g.id}`).catch(() => []),
  ]);
  if (!data) return el('div', { class: 'muted' }, 'sin datos');
  return planoInvernadero(data, traps.filter(t => !t.es_movil), { mini: true });
}

function heatColor(t, isPasillo) {
  if (isPasillo) return `rgba(135,135,135,${0.25 + t * 0.6})`;
  // teal (poco) → lima → rojo (mucho) — degradado corporativo grodi
  if (t < 0.5) { const k = t / 0.5; return mix([0, 111, 102], [180, 255, 72], k); }
  const k = (t - 0.5) / 0.5; return mix([180, 255, 72], [224, 68, 73], k);
}
function mix(a, b, k) { return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(',')})`; }
async function copyClientLink(g) {
  try {
    const r = await api(`/greenhouses/${g.id}/client-link`, { method: 'POST' });
    const url = location.origin + r.path;
    try { await navigator.clipboard.writeText(url); toast('Enlace de cliente copiado'); }
    catch { prompt('Enlace de cliente (solo lectura):', url); }
  } catch (e) { toast(e.message, true); }
}
function delGh(g) {
  if (!confirm(`¿Eliminar el invernadero ${g.nombre}? Sus trampas quedarán sin asignar.`)) return;
  api(`/greenhouses/${g.id}`, { method: 'DELETE' }).then(() => { toast('Invernadero eliminado'); window.__reloadGh(); })
    .catch(e => toast(e.message, true));
}
function ghForm(g = null) {
  const f = {};
  const mk = (ph, val = '') => el('input', { placeholder: ph, value: val });
  f.nombre = mk('Nombre', g?.nombre || '');
  f.ubicacion = mk('Paraje…, El Ejido', g?.ubicacion || '');
  f.responsable = mk('Nombre y apellidos', g?.responsable || '');
  f.email = mk('cliente@correo.com', g?.email || '');
  f.contacto = mk('6XX XXX XXX', g?.contacto || '');
  f.tipo = mk('Multitúnel, capilla, raspa…', g?.tipo || '');
  f.cultivo = mk('Tomate, pimiento, pepino…', g?.cultivo || '');
  f.num_lineos = el('input', { type: 'number', placeholder: 'p. ej. 50', value: g?.num_lineos ?? 0 });
  f.notas = mk('opcional', g?.notas || '');
  modal({
    title: g ? 'Editar invernadero' : 'Crear invernadero',
    body: el('div', { class: 'form-grid' },
      field('Nombre *', f.nombre), field('Ubicación', f.ubicacion),
      field('Responsable', f.responsable), field('Email', f.email),
      field('Teléfono', f.contacto), field('Tipo de invernadero', f.tipo),
      field('Tipo de cultivo', f.cultivo), field('Nº de lineos', f.num_lineos),
      el('div', { class: 'full' }, field('Notas', f.notas))),
    actions: (close) => [
      el('button', { class: 'btn ghost', onclick: close }, 'Cancelar'),
      el('button', { class: 'btn', onclick: async () => {
        const payload = { nombre: f.nombre.value, ubicacion: f.ubicacion.value,
          num_lineos: +f.num_lineos.value || 0, responsable: f.responsable.value,
          contacto: f.contacto.value, email: f.email.value, tipo: f.tipo.value,
          cultivo: f.cultivo.value, notas: f.notas.value };
        if (!payload.nombre) { toast('El nombre es obligatorio', true); return; }
        try {
          if (g) await api(`/greenhouses/${g.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
          else await api('/greenhouses', { method: 'POST', body: JSON.stringify(payload) });
          toast('Guardado'); close(); window.__reloadGh();
        } catch (e) { toast(e.message, true); }
      } }, 'Guardar')],
  });
}

/* ----- Demo ----- */
async function viewDemo(app) {
  app.innerHTML = '';
  const traps = await api('/traps').catch(() => []);
  const camaras = traps.filter(t => trapOnline(t) && t.ip);
  // «Imagen del dataset (fallback)»: «fallback» es jerga de programador, y ESTA es justo la
  // pantalla que se pone delante de otra persona. Se dice lo que es.
  const selTrap = el('select', {}, el('option', { value: '' }, '🖼 Foto de ejemplo del dataset'),
    ...camaras.map(t => el('option', { value: t.id }, '📷 ' + t.alias + ' (cámara real)')));
  const stage = el('div', { class: 'demo-stage' },
    el('div', { class: 'scan-grid' }),
    emptyState('🔬', 'Listo para analizar', 'Pulsa «Analizar» para que la IA detecte, cuente e identifique los insectos.'));
  // Primario como el resto de la web. Estaba en ámbar/lima, que aquí no significa nada: el
  // ámbar es el color de aviso en este sistema, y este botón no avisa de nada.
  const btn = el('button', { class: 'btn', onclick: run }, '🔬 Analizar');
  const result = el('div', { style: 'text-align:center;margin-top:16px' });

  // ¿ESTÁ LA IA EN MARCHA? Esta demo la ejecuta el worker del PC, que Antonio abre y cierra a
  // mano. Si está cerrado, pulsar «Analizar» deja la imagen girando y no pasa nada — delante de
  // quien se lo estés enseñando. Mejor decirlo ANTES de pulsar, y decir qué hacer.
  const aviso = el('div', {});
  api('/worker/stats').then(w => {
    if (w && w.online) return;
    btn.disabled = true;
    aviso.append(el('div', { class: 'nota-pendiente' },
      el('b', {}, '⚠️ La IA no está en marcha. '),
      'Esta demo la ejecuta el worker del PC: abre ',
      el('b', {}, 'Worker_IA.exe'),
      ' y vuelve a esta página. Sin él, «Analizar» se quedaría esperando sin dar resultado.'));
  }).catch(() => { /* si no se puede preguntar, no se bloquea: mejor intentarlo */ });

  app.append(
    el('div', { class: 'view-head' }, el('h1', {}, 'Demo')),
    el('p', { class: 'muted', style: 'margin:-6px 0 14px;max-width:70ch' },
      'Toma una imagen —de la cámara de una trampa o del dataset— y la pasa por el mismo '
      + 'modelo que analiza las capturas reales. Es el sistema entero en un botón.'),
    aviso,
    el('div', { class: 'filters' }, el('label', { class: 'field' }, 'Fuente', selTrap), btn),
    stage, result);

  async function run() {
    btn.disabled = true; result.innerHTML = '';
    stage.classList.remove('sweeping');
    stage.innerHTML = ''; stage.append(el('div', { class: 'scan-grid' }));
    try {
      const qs = selTrap.value ? `?trap_id=${selTrap.value}` : '';
      const job = await api('/demo/run' + qs, { method: 'POST' });
      // la inferencia (polling) arranca ya, en paralelo
      const resultReady = pollDemo(job.id);

      // 1) aparece la imagen
      const img = el('img', { src: `${API}/images/${job.id}/file` });
      stage.append(img);
      await new Promise(r => (img.onload = r));
      requestAnimationFrame(() => img.classList.add('shown'));
      await new Promise(r => setTimeout(r, 420));

      // 2) un único barrido de arriba a abajo
      const scan = el('div', { class: 'scanline' });
      stage.append(scan);
      const sweepDone = new Promise(r => {
        let fired = false;
        const fin = () => { if (!fired) { fired = true; r(); } };
        scan.addEventListener('animationend', fin, { once: true });
        setTimeout(fin, 2200);   // seguro por si animationend no dispara
      });
      stage.classList.add('sweeping');

      // esperar a que termine el barrido Y a que el Worker tenga el resultado
      const [done] = await Promise.all([resultReady, sweepDone]);
      stage.classList.remove('sweeping'); scan.remove();

      // 3) salen las etiquetas (siglas en español)
      const layer = el('div', { class: 'bbox-layer' });
      stage.append(layer);
      (done.detecciones || []).forEach((d, i) => layer.append(el('div', { class: 'bbox', style:
        `left:${(d.x - d.w / 2) * 100}%;top:${(d.y - d.h / 2) * 100}%;width:${d.w * 100}%;height:${d.h * 100}%;` +
        `border-color:${clsMeta(d.clase).color};animation-delay:${i * 40}ms` },
        el('span', { style: `background:${clsMeta(d.clase).color}` },
          `${clsSigla(d.clase)} ${(d.confianza * 100).toFixed(0)}%`))));
      const n = done.num_insectos;
      result.append(
        el('h2', { style: 'color:var(--teal);margin-bottom:4px' },
          n ? `🐛 ${n} ${n === 1 ? 'insecto detectado' : 'insectos detectados'}`
            : 'No se han detectado insectos'),
        speciesChips(done.detecciones),
        el('p', { class: 'muted', style: 'margin-top:10px;font-size:12px' },
          done.modelo_version ? 'modelo ' + done.modelo_version : ''));
    } catch (e) {
      stage.classList.remove('sweeping');
      stage.innerHTML = ''; stage.append(el('div', { class: 'scan-grid' }),
        emptyState('⚠️', 'No se pudo completar', e.message));
    } finally { btn.disabled = false; }
  }
  async function pollDemo(id, tries = 60) {
    for (let i = 0; i < tries; i++) {
      const im = await api(`/demo/${id}`);
      if (im.estado === 'procesada') return im;
      if (im.estado === 'error') throw new Error('El Worker devolvió un error');
      await new Promise(r => setTimeout(r, 400));
    }
    throw new Error('El Worker de IA no respondió (¿está Worker_IA.exe en marcha?)');
  }
}

/* ----- Mapa (rejilla sector × línea) ----- */
async function viewMapa(app) {
  app.innerHTML = '';
  const page = el('section', { class: 'mapa-view', 'aria-label': 'Mapa de plagas' });
  app.append(page);
  page.append(el('div', { class: 'view-head' }, el('h1', {}, 'Mapa de plagas')),
    el('p', { class: 'map-intro' }, 'Estado de las trampas y capturas recientes, en su posición dentro del invernadero.'));
  let revision = 0;
  onLeave(() => { revision++; });
  let ghs;
  try { ghs = await api('/greenhouses'); }
  catch { if (page.isConnected) page.append(el('p', { role: 'alert' }, 'No se han podido cargar los invernaderos.'),
    el('button', { class: 'btn ghost', onclick: () => viewMapa(app) }, 'Reintentar')); return; }
  if (!page.isConnected) return;
  if (!ghs.length) { page.append(emptyState('🏡', 'No hay invernaderos', 'Crea uno en Invernaderos y asigna trampas.')); return; }
  const sel = el('select', { 'aria-label': 'Invernadero' }, ...ghs.map(g => el('option', { value: g.id }, g.nombre)));
  const cont = el('div', { 'aria-live': 'polite' });
  const actions = el('details', { class: 'map-actions' });
  const btnClient = el('button', { class: 'btn ghost', onclick: async () => {
    btnClient.disabled = true;
    try {
      const r = await api(`/greenhouses/${sel.value}/client-link`, { method: 'POST' });
      const url = location.origin + r.path;
      try { await navigator.clipboard.writeText(url); toast('Enlace de cliente copiado'); }
      catch { prompt('Enlace de cliente (solo lectura):', url); }
    } catch (e) { toast(e.message, true); }
    finally { btnClient.disabled = false; actions.open = false; }
  } }, 'Copiar enlace de cliente');
  actions.append(el('summary', { 'aria-label': 'Más acciones' }, '···'),
    el('div', { class: 'map-actions-menu' }, btnClient));
  page.append(el('div', { class: 'filters map-filters' }, el('label', { class: 'field' }, 'Invernadero', sel),
    el('button', { class: 'btn ghost map-refresh', onclick: () => load() }, '↻ Actualizar'), actions), cont);
  sel.onchange = load;
  async function load() {
    const current = ++revision;
    cont.replaceChildren(el('p', {}, 'Cargando mapa…'));
    const [data, traps] = await Promise.all([
      api(`/greenhouses/${sel.value}/map`).catch(() => null),
      api(`/traps?invernadero=${sel.value}`).catch(() => null),
    ]);
    if (current !== revision || !page.isConnected) return;
    cont.replaceChildren();
    if (!data) { cont.append(el('p', { role: 'alert' }, 'Mapa no disponible. Pulsa Actualizar para reintentar.')); return; }
    const fixed = traps?.filter(t => !t.es_movil);
    const nTraps = data.num_trampas ?? fixed?.length ?? 0;
    const summary = mapSummary(data.greenhouse, data, fixed);
    if (!nTraps) {
      // Sin trampas el plano sigue saliendo: es la planta del invernadero, y es donde se ve
      // dónde irán. Lo que no sale es la lista ni la leyenda de presión, que no tienen nada que decir.
      cont.append(summary, mapEmptyState(data.greenhouse, true),
        mapPlanPanel(data, [], { onExpand: () => openMapPlanDialog(data, [], {}) }));
      return;
    }
    let selectedTrapIds = [];
    const selectionScopes = new Set();
    const selectTraps = (ids) => {
      selectedTrapIds = [...new Set(ids.map(String))];
      selectionScopes.forEach(scope => paintMapSelection(scope, selectedTrapIds));
    };
    const plan = mapPlanPanel(data, fixed || [], {
      onExpand: () => openMapPlanDialog(data, fixed || [], {
        selected: () => selectedTrapIds,
        onSelect: selectTraps,
        onMount: (scope) => {
          selectionScopes.add(scope);
          paintMapSelection(scope, selectedTrapIds);
          return () => selectionScopes.delete(scope);
        },
      }),
    });
    const list = el('div', { class: 'map-traps' });
    if (!fixed) list.append(el('p', { role: 'alert' }, 'No se han podido consultar las trampas. Su posición no está disponible.'));
    else {
      const readings = fixed.map(t => mapTrapReading(t, data)).sort((a, b) =>
        Number(b.incident) - Number(a.incident)
        || Number(b.noData) - Number(a.noData)
        || b.pressure - a.pressure
        || a.name.localeCompare(b.name, 'es'));
      const incidents = readings.filter(r => r.incident).length;
      list.append(el('div', { class: 'map-traps-head' },
        el('h2', {}, 'Trampas'),
        el('span', { class: incidents ? 'map-incident' : 'map-ok' },
          incidents ? plural(incidents, 'incidencia', 'incidencias') : 'Sin incidencias')));
      readings.forEach(r => list.append(mapTrapCard(r)));
    }
    plan.append(leyendaPlano(data));
    cont.append(summary, el('div', { class: 'map-layout' }, list, plan));
    selectionScopes.add(cont);
    wireMapSelection(cont, selectTraps, cont);
  }
  load();
}
function mapSummary(gh, data, traps = null) {
  const bits = [];
  if (gh.ubicacion) bits.push(gh.ubicacion);
  if (gh.cultivo) bits.push(gh.cultivo);
  if (gh.tipo) bits.push(gh.tipo);
  if (gh.responsable) bits.push(`Responsable: ${gh.responsable}`);
  if (gh.contacto) bits.push(gh.contacto);
  const n = data.num_trampas ?? traps?.length ?? 0;
  const measured = Object.values(data.cells || {}).reduce((sum, c) => sum + Number(c.con_dato || 0), 0);
  const missing = data.trampas_sin_dato ?? Math.max(0, n - measured);
  const pending = data.pendientes ?? 0;
  let pressure = 'Sin dato';
  let pressureNote = missing ? plural(missing, 'trampa sin medida', 'trampas sin medida') : '';
  if (pending && !measured) {
    pressure = 'Pendiente de análisis';
    pressureNote = 'Todavía no hay una medida válida';
  } else if (measured) {
    pressure = `${data.total_nuevas ?? 0} capturas nuevas`;
    if (pending) pressureNote = 'Datos incompletos';
  }
  const offline = traps ? traps.filter(t => !trapOnline(t)).length : null;
  const item = (label, value, cls = '', note = '') => el('div', { class: `map-summary-item ${cls}`.trim() },
    el('span', { class: 'map-summary-label' }, label),
    el('strong', {}, value),
    note ? el('small', {}, note) : null);
  const items = [
    item('Trampas instaladas', String(n)),
    item(`Capturas · ${data.dias_presion ?? 7} días`, pressure, '', pressureNote),
  ];
  if (offline != null && n) items.push(item('Estado', offline ? `${offline} sin contacto` : 'Todas en línea', offline ? 'map-summary-incident' : 'map-summary-ok'));
  if (pending) items.push(item('Pendientes', `${pending} por analizar`, 'map-summary-pending', 'No se incluyen todavía'));
  return el('section', { class: 'map-summary', 'aria-label': 'Resumen del invernadero' },
    el('div', { class: 'map-summary-grid' }, items),
    bits.length ? el('div', { class: 'map-summary-meta muted' }, bits.join('  ·  ')) : null);
}

function mapEmptyState(gh, canManage = false) {
  return el('section', { class: 'map-empty' },
    el('div', { class: 'map-empty-icon', 'aria-hidden': 'true' }, '◎'),
    el('div', {},
      el('h2', {}, 'Todavía no hay trampas en este invernadero'),
      el('p', {}, `El invernadero tiene ${plural(gh.num_lineos || 0, 'lineo', 'lineos')}. Asigna una trampa y su posición aparecerá aquí.`),
      canManage ? el('a', { class: 'btn ghost', href: '#/trampas' }, 'Ir a Trampas') : null));
}

function mapTrapReading(t, data) {
  const cell = t.sector && t.linea ? data.cells?.[`${t.sector}|${t.linea}`] : null;
  const conDato = Number(cell?.con_dato || 0);
  const sinDato = Number(cell?.sin_dato || 0);
  const trapMeasured = t.ultimo_conteo != null;
  const pending = t.ultima_imagen?.estado === 'pendiente';
  const analysisError = t.ultima_imagen?.estado === 'error';
  const online = trapOnline(t);
  let reading = 'Presión sin dato';
  if (trapMeasured && conDato > 0) {
    reading = `${cell.nuevas ?? 0} nuevas en este tramo y lineo`;
    if (sinDato) reading += ' · dato parcial';
    if (pending) reading += ' · análisis pendiente';
  } else if (pending) reading = 'Pendiente de análisis';
  else if (analysisError) reading = 'Análisis con error';
  return {
    trap: t,
    name: t.alias || t.device_id,
    online,
    incident: !online || analysisError,
    noData: !trapMeasured || conDato === 0,
    pressure: trapMeasured && conDato > 0 ? Number(cell?.nuevas || 0) : -1,
    reading,
  };
}

function mapTrapCard(reading) {
  const t = reading.trap;
  const position = t.sector && t.linea ? `Lineo ${t.linea} · Tramo ${t.sector}` : 'Sin posición asignada';
  const status = !reading.online ? 'Sin contacto'
    : t.ultima_imagen?.estado === 'error' ? 'Error de análisis'
    : 'En línea';
  return el('article', {
    class: `map-trap${reading.incident ? ' has-incident' : ''}`,
    'data-trap-id': t.id,
    tabindex: '0',
  },
    el('div', { class: 'map-trap-main' },
      el('div', {}, el('h3', {}, reading.name), el('p', { class: 'map-trap-position muted' }, position)),
      el('span', { class: reading.incident ? 'map-incident' : 'map-ok' }, status)),
    el('div', { class: 'map-trap-reading' },
      el('strong', {}, reading.reading),
      el('a', { class: 'btn ghost sm', href: '#/control/' + t.id }, 'Ver trampa →')));
}

function mapPlanPanel(data, traps, { onExpand } = {}) {
  return el('div', { class: 'map-plan' },
    el('div', { class: 'map-plan-head' },
      el('div', {}, el('span', { class: 'map-eyebrow' }, 'LOCALIZACIÓN'),
        el('h2', {}, 'Plano')),
      onExpand ? el('button', { class: 'btn ghost map-expand', onclick: onExpand }, 'Ampliar') : null),
    el('p', {}, `${plural(data.n_lineas || 0, 'lineo', 'lineos')} · A–D indican el tramo de cada trampa`),
    el('div', { class: 'map-plan-canvas' }, planoInvernadero(data, traps)));
}

function markerTrapIds(marker) {
  return (marker.dataset.trapIds || marker.dataset.trapId || '').split(',').filter(Boolean);
}

function paintMapSelection(scope, ids) {
  const selected = new Set(ids.map(String));
  $$('.map-trap[data-trap-id]', scope).forEach(card => {
    card.classList.toggle('is-selected', selected.has(card.dataset.trapId));
  });
  $$('.map-marker[data-trap-id]', scope).forEach(marker => {
    const active = markerTrapIds(marker).some(id => selected.has(id));
    marker.classList.toggle('is-selected', active);
    marker.setAttribute('aria-pressed', String(active));
  });
}

function wireMapSelection(scope, onSelect, cardRoot = null) {
  let movingFocus = false;
  const markerActions = $$('.map-marker[data-trap-id]', scope).map(marker => {
    const activate = () => {
      const ids = markerTrapIds(marker);
      onSelect(ids);
      const firstCard = cardRoot?.querySelector(`.map-trap[data-trap-id="${CSS.escape(ids[0] || '')}"]`);
      if (firstCard && scope.contains(firstCard)) {
        firstCard.scrollIntoView({ block: 'nearest' });
        movingFocus = true;
        firstCard.focus({ preventScroll: true });
        movingFocus = false;
        onSelect(ids);
      }
    };
    marker.addEventListener('keydown', e => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();
      activate();
    });
    return { marker, activate };
  });
  const svg = $('.plano svg', scope);
  svg?.addEventListener('click', e => {
    // Resuelve por cercanía para que dos hit areas de 44 px nunca manden a la trampa que
    // simplemente quedó la última en el SVG cuando hay lineos contiguos.
    const nearest = markerActions.map(item => {
      const box = $('.map-marker-shape', item.marker).getBoundingClientRect();
      return { ...item, d: Math.hypot(e.clientX - (box.left + box.width / 2), e.clientY - (box.top + box.height / 2)) };
    }).sort((a, b) => a.d - b.d)[0];
    if (nearest && nearest.d <= 22) nearest.activate();
  });
  $$('.map-trap[data-trap-id]', scope).forEach(card => {
    const activate = () => { if (!movingFocus) onSelect([card.dataset.trapId]); };
    card.addEventListener('click', activate);
    card.addEventListener('focusin', activate);
  });
}

function openMapPlanDialog(data, traps, {
  selected = () => [], onSelect = () => {}, onMount = () => () => {},
} = {}) {
  const dialog = el('dialog', { class: 'map-plan-dialog', 'aria-label': 'Plano ampliado del invernadero' });
  const close = el('button', { class: 'btn ghost map-dialog-close', onclick: () => dialog.close() }, 'Cerrar');
  const viewport = el('div', { class: 'map-dialog-viewport' }, planoInvernadero(data, traps));
  dialog.append(el('div', { class: 'map-dialog-head' }, el('h2', {}, 'Plano del invernadero'), close), viewport);
  document.body.append(dialog);
  const unmount = onMount(dialog);
  wireMapSelection(dialog, onSelect);
  paintMapSelection(dialog, selected());
  // El «atrás» del móvil cambia la vista por debajo: el plano ampliado no puede quedarse encima.
  const alNavegar = () => dialog.close();
  window.addEventListener('hashchange', alNavegar, { once: true });
  dialog.addEventListener('close', () => {
    window.removeEventListener('hashchange', alNavegar);
    unmount(); dialog.remove();
  }, { once: true });
  dialog.showModal();
  close.focus();
}
/* ---------- Plano del invernadero ----------
 * Antes esto era una rejilla de sector×línea con celdas de color: un Excel. Un técnico no se
 * orienta por una matriz, se orienta por la forma de la nave, el pasillo y los lineos.
 *
 * Y la primera versión del plano seguía teniendo mal el modelo: partía cada lineo en cinco
 * huertos, uno por sector, como si un sector fuera una parcela. NO LO ES. Corregido por el
 * usuario el 2026-09-12:
 *
 *   · un LINEO   = UNA línea de cultivo, entera, de punta a punta de la nave;
 *   · un SECTOR  = A QUÉ ALTURA de ese lineo cuelga la trampa. Una coordenada, no un sitio.
 *
 * Por eso el cultivo se dibuja continuo y los sectores son una regla por encima de la nave.
 * Lo único que corta un lineo es el pasillo, porque por ahí se anda.
 *
 * El COLOR sigue siendo presión de plaga (capturas nuevas en la ventana), no acumulado: es lo
 * comparable entre zonas, que fue justo el arreglo del 2026-09-09.
 */
function planoInvernadero(data, traps = [], opts = {}) {
  const mini = !!opts.mini;
  const sectores = (data.sectores || ['A', 'B', 'pasillo', 'C', 'D']);
  const cultivables = sectores.filter(s => s !== 'pasillo');
  const nL = Math.max(data.n_lineas || 1, ...(data.lineas_con_datos || [0]), 1);
  const cells = data.cells || {};
  const lineosConTrampa = new Set((data.lineas_con_datos || []).map(Number));
  traps.forEach(t => { if (t.linea) lineosConTrampa.add(Number(t.linea)); });
  const maxV = Math.max(1, ...Object.values(cells)
    .filter(c => Number(c.con_dato || 0) > 0).map(c => c.nuevas ?? 0));

  // QUÉ ES CADA COSA, porque el dibujo anterior lo tenía mal y se leía al revés:
  //
  // - Un **LINEO** es UNA línea de cultivo, entera, de punta a punta de la nave. Se dibuja de
  //   un solo trazo. El invernadero son `n_lineas` lineos paralelos.
  // - Un **SECTOR** no es una zona ni un trozo de cultivo: es la forma de decir **a qué altura
  //   del lineo** cuelga una trampa. Es una escala SOBRE el lineo, no una división de él.
  //
  // La versión anterior partía cada lineo en cinco huertos con hueco entre ellos y pintaba
  // cada sector como si fuera una parcela con su plaga. Eso convertía una coordenada en un
  // sitio, que es exactamente lo que el plano tiene que evitar: el técnico busca «lineo 12,
  // altura C», no «el sector C».
  //
  // Lo único que parte un lineo de verdad es el **pasillo**, porque por ahí se anda.
  const W = 1000;
  const M = mini ? 14 : 48;
  // NO HAY TECHO, y no es un olvido: esto es una vista AEREA. Los dientes de sierra del
  // parral son ALZADO —desde arriba no se ve la altura de la cubierta—, asi que dibujarlos
  // mezclaba una planta con un trozo de seccion encima. Se pintaban porque «se reconoce un
  // invernadero de Almeria», que es una razon estetica, no una razon de plano.
  const regla = mini ? 0 : 44;                  // franja de la escala de sectores (A · B · C · D)
  // ESCALA. En un invernadero de verdad la separacion entre lineos es la misma tenga 10 o 50:
  // un lineo mide lo que mide. Con la altura del dibujo fija, 10 lineos salian igual de altos
  // que 50 —la separacion cambiaba sola— y el plano dejaba de representar un tamaño.
  //
  // Asi que la separacion se mantiene y es la NAVE la que crece con el numero de lineos, con
  // topes para que ni un invernadero de 4 lineos salga raquitico ni uno de 80 sea una tira.
  const filaH = mini ? Math.max(2.6, Math.min(9, 150 / nL))
                     : Math.max(6, Math.min(22, 620 / nL));
  const filaGruesa = filaH >= 14;          // ¿hay sitio para etiquetar cada lineo?
  const trazo = mini ? 1.1 : (filaGruesa ? 1.8 : Math.max(0.7, filaH / 9));
  // En la miniatura el dibujo mide un tercio: con la misma opacidad que en grande se queda en
  // un folio en blanco y la tarjeta del invernadero no enseña nada.
  const opacidadLineo = mini ? 0.42
                             : (filaGruesa ? 0.32 : Math.max(0.16, 0.32 - (nL - 14) * 0.004));
  // PASILLO PERIMETRAL. El cultivo no llega nunca a la pared: hace falta sitio para pasar con
  // la carretilla y para la cabecera del lineo, donde se gira. Sin esto el dibujo parecia una
  // hoja pautada dentro de una caja, no un invernadero.
  const cab = mini ? 3 : 15;
  const cuerpoH = nL * filaH + cab * 2;
  const H = M * 2 + regla + cuerpoH + (mini ? 8 : 34);   // hueco para el rotulo de la entrada
  const x0 = M, y0 = M + regla;
  const anchoUtil = W - M * 2;
  const x1 = x0 + anchoUtil;
  const yCul = y0 + cab;                        // donde empieza el cultivo

  // El pasillo se lleva menos ancho que una altura de cultivo: es un paso, no una parcela.
  const pesoPasillo = 0.5;
  const anchoCultivo = anchoUtil - cab * 2;
  const wSector = anchoCultivo / (cultivables.length + pesoPasillo);
  const xs = {};
  let xAcc = x0 + cab;
  sectores.forEach(s => {
    const w = (s === 'pasillo') ? wSector * pesoPasillo : wSector;
    xs[s] = { x: xAcc, w };
    xAcc += w;
  });
  const pas = xs['pasillo'];

  const p = [];
  const esc = (t) => String(t).replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

  // ---- suelo y cubierta
  p.push(`<rect x="${x0}" y="${y0}" width="${anchoUtil}" height="${cuerpoH}" fill="#F7F6F2"/>`);
  // Se esta mirando a traves del plastico: un velo calido, muy flojo, que unifica el interior
  // y lo separa del fondo de la pagina. Es lo que hace que se lea «nave» y no «tabla».
  p.push(`<rect x="${x0}" y="${y0}" width="${anchoUtil}" height="${cuerpoH}" fill="#EDEFE6" opacity=".55"/>`);
  // El area de CULTIVO, un punto mas verde que el pasillo perimetral: de un vistazo se ve
  // donde hay planta y donde se anda, sin leer una sola etiqueta.
  p.push(`<rect x="${x0 + cab}" y="${yCul}" width="${anchoUtil - cab * 2}" height="${nL * filaH}" fill="#E3EADF" opacity=".5"/>`);

  // ---- LOS LINEOS: cada uno, UNA línea de cultivo continua de punta a punta.
  //
  // Los tramos salen de quitarle al ancho el hueco del pasillo, NO de trocear por sectores:
  // un lineo no sabe nada de sectores, solo lo cruza el pasillo.
  const xCulA = x0 + cab, xCulB = x1 - cab;
  const tramos = pas
    ? [[xCulA, pas.x - 2], [pas.x + pas.w + 2, xCulB]]
    : [[xCulA, xCulB]];

  for (let l = 1; l <= nL; l++) {
    const yc = yCul + (l - 0.5) * filaH;
    // UN LINEO, UN TRAZO. Nada mas: ni matas dibujadas encima ni banda de fondo. El lineo es
    // la unidad con la que se orienta el tecnico, y cualquier adorno sobre el trazo compite con
    // lo unico que hay que leer ahi — donde esta la trampa.
    // El grosor y la opacidad bajan con la densidad: con 50 lineos, un trazo grueso convierte
    // la nave en un codigo de barras.
    tramos.forEach(([xa, xb]) => {
      p.push(`<line class="map-row ${lineosConTrampa.has(l) ? 'has-trap' : 'is-context'}" x1="${xa}" y1="${yc}" x2="${xb}" y2="${yc}" stroke="#2D6A4F" stroke-width="${trazo}" opacity="${opacidadLineo}" stroke-linecap="round"/>`);
    });
    const toca = filaGruesa || l === 1 || l === nL || l % 5 === 0;
    if (!mini && toca) {
      const minor = l !== 1 && l !== nL && l % 10 !== 0 ? ' is-minor' : '';
      p.push(`<text class="map-line-label${minor}${lineosConTrampa.has(l) ? ' has-trap' : ''}" x="${x0 - 9}" y="${yc}" text-anchor="end" dominant-baseline="middle" font-size="${filaGruesa ? 16 : 14}" fill="#666">${l}</text>`);
    }
  }

  // Rotulo del eje: sin esto, la columna de numeros de la izquierda no dice de que son.
  if (!mini) {
    const cyl = y0 + cuerpoH / 2;
    p.push(`<text class="map-axis-title" x="${x0 - 32}" y="${cyl}" text-anchor="middle" font-size="15" font-weight="700" fill="#666" transform="rotate(-90 ${x0 - 32} ${cyl})" letter-spacing="1">LINEOS</text>`);
  }

  // ---- el pasillo: lo único que corta los lineos, porque por ahí se anda
  if (pas) {
    p.push(`<rect x="${pas.x}" y="${y0}" width="${pas.w}" height="${cuerpoH}" fill="#F0EBE1" stroke="#DED7C9" stroke-width="1"/>`);
    // Sin rayado. Las rayas horizontales de antes parecian lineos atravesando el pasillo, que
    // es exactamente lo contrario de lo que el pasillo es: lo unico que CORTA un lineo.
    if (!mini) {
      const cxp = pas.x + pas.w / 2, cyp = y0 + cuerpoH / 2;
      p.push(`<text x="${cxp}" y="${cyp}" text-anchor="middle" font-size="9.5" fill="#9A9078" transform="rotate(-90 ${cxp} ${cyp})" letter-spacing="2.5">PASILLO</text>`);
    }
  }

  // ---- LA ESCALA DE ALTURAS (los sectores), arriba y como una regla: son coordenadas.
  //
  // Van fuera de la nave a propósito. Dentro, con fondo de color, parecían parcelas —y un
  // sector no es un sitio distinto, es un punto del mismo lineo—.
  if (!mini) {
    const yLet = M + 28;                       // letras A · B · C · D
    const yr = M + 36;                         // la regla, justo encima de la nave
    p.push(`<line x1="${x0}" y1="${yr}" x2="${x1}" y2="${yr}" stroke="#C6C6C6" stroke-width="1"/>`);
    sectores.forEach(s => {
      const { x, w } = xs[s];
      p.push(`<line x1="${x}" y1="${yr - 4}" x2="${x}" y2="${yr + 4}" stroke="#C6C6C6" stroke-width="1"/>`);
      if (s === 'pasillo') return;
      p.push(`<text class="map-sector-label" x="${x + w / 2}" y="${yLet}" text-anchor="middle" font-size="20" font-weight="800" fill="#30332F">${s}</text>`);
    });
    p.push(`<line x1="${x1}" y1="${yr - 4}" x2="${x1}" y2="${yr + 4}" stroke="#C6C6C6" stroke-width="1"/>`);
    // Guías tenues hacia abajo: sitúan la trampa sin partir el cultivo.
    sectores.forEach(s => {
      if (s === sectores[0]) return;
      p.push(`<line x1="${xs[s].x}" y1="${y0}" x2="${xs[s].x}" y2="${y0 + cuerpoH}" stroke="#C9C9C9" stroke-width="1" stroke-dasharray="2 5" opacity=".55"/>`);
    });
  }

  // ---- LA PRESIÓN, solo donde se ha medido
  //
  // Antes se teñía la columna entera del sector. Eso pintaba de plaga 50 lineos por lo que
  // decía UNA trampa, y dejaba el único sector con datos más oscuro que el resto solo por
  // tener datos. Ahora el color vive en la celda (lineo × altura) que de verdad tiene trampa.
  Object.entries(cells).forEach(([clave, celda]) => {
    const [s, l] = clave.split('|');
    if (!xs[s] || !Number(l)) return;
    const { x, w } = xs[s];
    const known = Number(celda.con_dato || 0) > 0;
    const t = known ? (celda.nuevas ?? 0) / maxV : 0;
    const color = known ? mapPressureColor(t) : '#CFD5CF';
    const opacity = known ? (0.22 + t * 0.44).toFixed(2) : '0.58';
    p.push(`<rect x="${x + 1}" y="${yCul + (Number(l) - 1) * filaH}" width="${w - 2}" height="${filaH}" fill="${color}" opacity="${opacity}"/>`);
  });

  // ---- estructura y contorno de la nave
  // Sin reticula de postes. Los dibuje porque un parral se sostiene sobre ellos y desde arriba
  // se ven, pero sobre 50 lineos la nube de puntos competia con lo unico que hay que leer aqui
  // —donde esta la trampa— y ensuciaba el plano. La estructura no es informacion.
  p.push(`<rect x="${x0}" y="${y0}" width="${anchoUtil}" height="${cuerpoH}" fill="none" stroke="#7E8C7E" stroke-width="2.4"/>`);
  // BANDAS DE VENTILACION en los laterales largos. Se abren y se cierran, y son media razon de
  // que la plaga entre por donde entra: quien mira este plano para decidir donde colgar una
  // trampa necesita verlas.
  if (!mini) {
    const trozo = cuerpoH / 9;
    for (let k = 1; k < 9; k += 2) {
      const ya = y0 + trozo * k;
      [x0, x1].forEach(xx => {
        p.push(`<line x1="${xx}" y1="${ya.toFixed(1)}" x2="${xx}" y2="${(ya + trozo).toFixed(1)}" stroke="#FDFDFB" stroke-width="3.2"/>`);
        p.push(`<line x1="${xx}" y1="${ya.toFixed(1)}" x2="${xx}" y2="${(ya + trozo).toFixed(1)}" stroke="#9FB4A4" stroke-width="1.6" stroke-dasharray="3 3"/>`);
      });
    }
  }

  // ---- entrada, al final del pasillo
  if (pas && !mini) {
    // Una puerta es un HUECO en el muro, no una pegatina pegada por fuera: se tapa el trozo de
    // pared y se marca el vano. Va donde muere el pasillo, que es por donde se entra de verdad.
    const yPar = y0 + cuerpoH;
    p.push(`<line x1="${pas.x + 3}" y1="${yPar}" x2="${pas.x + pas.w - 3}" y2="${yPar}" stroke="#F7F6F2" stroke-width="5"/>`);
    p.push(`<line x1="${pas.x + 3}" y1="${yPar}" x2="${pas.x + pas.w - 3}" y2="${yPar}" stroke="#F7A93B" stroke-width="3"/>`);
    p.push(`<text class="map-entry-label" x="${pas.x + pas.w / 2}" y="${yPar + 20}" text-anchor="middle" font-size="16" font-weight="700" fill="#666">entrada</text>`);
  }

  // ---- las trampas, colgadas en su lineo y a su altura
  const porCelda = {};
  traps.forEach(t => {
    if (!t.sector || !t.linea || !xs[t.sector]) return;
    (porCelda[`${t.sector}|${t.linea}`] ||= []).push(t);
  });
  Object.entries(porCelda).forEach(([clave, lista]) => {
    const [s, l] = clave.split('|');
    const { x, w } = xs[s];
    const yc = yCul + (Number(l) - 0.5) * filaH;
    const cx = x + w / 2;
    const celda = cells[clave] || {};
    const tt = (celda.nuevas ?? 0) / maxV;
    // Sin presión en NINGÚN sitio no hay escala que aplicar: «gris» conserva sin dato.
    const known = Number(celda.con_dato || 0) > 0;
    const col = known ? mapPressureColor(tt) : '#CFD5CF';
    const r = mini ? 3 : (filaGruesa ? 11 : 9);
    const cy = Math.min(Math.max(yc, yCul + r - 4), yCul + nL * filaH - r + 4);
    const off = lista.some(t => !trapOnline(t));
    const ids = lista.map(t => String(t.id));
    const names = lista.map(t => t.alias || t.device_id);
    const quantity = lista.length;
    const value = quantity > 1 ? `×${quantity}` : (known ? celda.nuevas ?? 0 : '?');
    const label = quantity > 1
      ? `${quantity} trampas: ${names.join(', ')} · lineo ${l}, tramo ${s} · ${known ? `${celda.nuevas ?? 0} capturas nuevas` : 'sin dato'}${off ? ' · incidencia sin contacto' : ''}`
      : `${names[0]} · lineo ${l}, tramo ${s} · ${known ? `${celda.nuevas ?? 0} nuevas en ${data.dias_presion ?? 7} días` : 'sin dato'}${off ? ' · sin contacto' : ''}`;
    p.push(`<g class="map-marker" role="button" tabindex="0" focusable="true"
        data-trap-id="${esc(ids[0])}" data-trap-ids="${esc(ids.join(','))}" aria-label="${esc(label)}" aria-pressed="false">
      <title>${esc(label)}</title>
      <circle class="map-marker-halo" cx="${cx}" cy="${cy}" r="${r + 7}"/>
      <circle class="map-marker-hit" cx="${cx}" cy="${cy}" r="1"/>
      <rect class="map-marker-shape" x="${cx - r}" y="${cy - r}" width="${r * 2}" height="${r * 2}" rx="${r * 0.35}"
            fill="${col}" stroke="${off ? '#E04449' : '#1D1E1C'}" stroke-width="${off ? 2 : 1.2}"/>
      ${mini ? '' : `<text class="map-marker-value" x="${cx}" y="${cy + 1}" text-anchor="middle" dominant-baseline="middle"
            font-size="${quantity > 1 ? 11 : (filaGruesa ? 11 : 10)}" font-weight="800" fill="#1D1E1C" stroke="#fff" stroke-width="2.5"
            paint-order="stroke" pointer-events="none">${esc(value)}</text>`}
    </g>`);
  });

  const svg = el('div', { class: 'plano' });
  svg.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
      aria-label="Vista aérea del invernadero: ${nL} lineos de cultivo y las trampas en su lineo y tramo">${p.join('')}</svg>`;
  return svg;
}

/**
 * Corriente de entrada de las últimas horas: media de cada ventana (línea) y pico (sombra).
 *
 * Dos reglas de la casa: un hueco en el tiempo se DIBUJA como hueco —unir dos puntos
 * separados por una caída de red inventaría un consumo que nadie midió—, y una ventana
 * saturada se marca arriba en rojo en vez de caer a cero, que sería decir lo contrario.
 */
function graficaCorriente(vs, horas) {
  const W = 320, H = 84, PAD = 4, BASE = H - 14;
  const fin = Date.now(), ini = fin - horas * 3600e3;
  const x = (t) => PAD + (t - ini) / (fin - ini) * (W - 2 * PAD);
  const tope = Math.max(50, ...vs.map(w => Math.max(w.ma_max ?? 0, w.ma_media ?? 0))) * 1.1;
  const y = (ma) => BASE - (ma / tope) * (BASE - PAD);
  const HUECO_MS = 12 * 60e3;          // más de dos ventanas sin dato: se corta la línea
  const trazos = (campo) => {
    const out = []; let cur = []; let prev = null;
    vs.forEach(w => {
      const t = aFecha(w.fin).getTime();
      if (w[campo] == null || (prev != null && t - prev > HUECO_MS)) { if (cur.length) out.push(cur); cur = []; }
      if (w[campo] != null) cur.push(`${x(t).toFixed(1)},${y(w[campo]).toFixed(1)}`);
      prev = t;
    });
    if (cur.length) out.push(cur);
    return out;
  };
  const linea = (campo, color, ancho, op) => trazos(campo).map(pts => pts.length === 1
    ? `<circle cx="${pts[0].split(',')[0]}" cy="${pts[0].split(',')[1]}" r="1.8" fill="${color}" opacity="${op}"/>`
    : `<polyline points="${pts.join(' ')}" fill="none" stroke="${color}" stroke-width="${ancho}" stroke-linejoin="round" opacity="${op}"/>`).join('');
  const sat = vs.filter(w => w.saturadas).map(w => {
    const cx = x(aFecha(w.fin).getTime()).toFixed(1);
    return `<line x1="${cx}" y1="${PAD}" x2="${cx}" y2="${PAD + 6}" stroke="var(--rojo)" stroke-width="2"><title>${w.saturadas} muestras por encima del techo</title></line>`;
  }).join('');
  const caja = el('div', { style: 'margin-top:8px' });
  caja.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
      aria-label="Corriente de entrada en las últimas ${horas} h: media y pico de cada ventana de 5 minutos">
    <line x1="${PAD}" y1="${BASE}" x2="${W - PAD}" y2="${BASE}" stroke="var(--linea-2)" stroke-width="1"/>
    <text x="${PAD}" y="${PAD + 8}" font-size="9" fill="var(--texto-mute)">${Math.round(tope)} mA</text>
    ${linea('ma_max', 'var(--lima-ink)', 1, 0.45)}
    ${linea('ma_media', 'var(--teal)', 1.8, 1)}
    ${sat}
    <text x="${PAD}" y="${H - 2}" font-size="9" fill="var(--texto-mute)">−${horas} h</text>
    <text x="${W - PAD}" y="${H - 2}" font-size="9" fill="var(--texto-mute)" text-anchor="end">ahora · media <tspan fill="var(--teal)">━</tspan> pico <tspan fill="var(--lima-ink)">━</tspan></text>
  </svg>`;
  return caja;
}

// Escala relativa de capturas, sin inventar un umbral de actuación.
function mapPressureColor(ratio) {
  return ratio > 0.66 ? '#006F66' : ratio > 0.33 ? '#80B7AF' : '#D5E8E4';
}

// Qué significa cada color y cada símbolo. Un plano sin leyenda es un dibujo bonito.
/**
 * Leyenda del plano.
 *
 * `conTrampas` existe porque la vista de CLIENTE dibuja el mismo plano **sin marcadores** (su
 * enlace no lista dispositivos), y la leyenda seguía explicando «cada cuadro colgado es una
 * trampa… borde rojo = sin contacto»: describía algo que ahí no aparece. Una leyenda que nombra
 * lo que no está es peor que no tener leyenda — el que mira se pone a buscarlo.
 */
function leyendaPlano(data, { conTrampas = true } = {}) {
  const muestra = (kind, txt) => el('span', { class: 'lg-item' },
    el('i', { class: `map-key map-key-${kind}` }), txt);
  return el('div', { class: 'plano-leyenda' },
    el('div', { class: 'lg-fila' },
      el('b', {}, 'Capturas nuevas'),
      muestra('low', 'Menor'),
      muestra('mid', 'Intermedio'),
      muestra('high', 'Mayor'),
      muestra('unknown', 'Sin dato')),
    el('details', { class: 'map-help' },
      el('summary', {}, 'Cómo leer el plano'),
      el('div', { class: 'map-help-copy muted' },
        el('p', {}, `El color representa capturas nuevas en ${data.dias_presion ?? 7} días. Gris indica ausencia de dato; rojo, una incidencia.`),
        el('p', {}, 'La intensidad compara recuentos dentro de este invernadero; no es un umbral de actuación ni el acumulado de la tarjeta.'),
        (data.pendientes ?? 0) > 0
          ? el('p', {}, `Hay ${plural(data.pendientes, 'foto', 'fotos')} sin analizar; el plano solo incluye lo ya contado.`)
          : null,
        conTrampas
          ? el('p', {}, 'Cada cuadro es una trampa. El número es la presión de su tramo y lineo; ? significa sin dato y el borde rojo indica una incidencia.')
          : null,
        el('p', {}, 'Cada fila es un lineo completo. A·B·C·D indican en qué tramo de ese lineo está la trampa; solo el pasillo corta el lineo.'))));
}

/* ----- Tendencia diaria (2026-10-23) -----
   Media por día de mosca blanca y trips en las fotos analizadas de cada trampa. La calcula el
   servidor en cada petición a partir de las detecciones: si un modelo mejor reprocesa fotos del
   pasado, la gráfica cambia sola. La tarjeta ACUMULA: la serie sube mientras el cartón está puesto
   y cae al cambiarlo (líneas verticales). Un día sin fotos analizadas no se pinta como cero. */
function graficaTendencia(serie) {
  const dias = serie.dias.filter(d => d.n_fotos > 0);
  if (!dias.length) return el('p', { class: 'muted' }, 'Sin fotos analizadas todavía.');
  const W = 720, H = 280, ml = 44, mr = 24, mt = 14, mb = 44;
  const t0 = Date.parse(dias[0].dia), t1 = Date.parse(dias[dias.length - 1].dia);
  const span = Math.max(t1 - t0, 86400000);
  const ymax = Math.max(1, ...dias.map(d => Math.max(d.wf_media, d.tr_media)));
  const X = (dia) => ml + (Date.parse(dia) - t0) / span * (W - ml - mr);
  const Y = (v) => mt + (1 - v / ymax) * (H - mt - mb);
  const NS = 'http://www.w3.org/2000/svg';
  const s = (tag, attrs, txt) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (txt != null) n.textContent = txt;
    return n;
  };
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img',
    'aria-label': `Tendencia de ${serie.alias}`, style: 'max-width:100%;height:auto;color:inherit' });
  for (let k = 0; k <= 4; k++) {
    const v = ymax * k / 4, y = Y(v);
    svg.append(s('line', { x1: ml, x2: W - mr, y1: y, y2: y, stroke: 'currentColor', 'stroke-opacity': 0.12 }),
      s('text', { x: ml - 6, y: y + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'currentColor', 'fill-opacity': 0.7 },
        v >= 10 ? v.toFixed(0) : v.toFixed(1)));
  }
  const paso = Math.max(1, Math.ceil(dias.length / 8));
  dias.forEach((d, k) => {
    if (k % paso) return;
    svg.append(s('text', { x: X(d.dia), y: H - mb + 16, 'text-anchor': 'middle', 'font-size': 11,
      fill: 'currentColor', 'fill-opacity': 0.7 }, d.dia.slice(8, 10) + '/' + d.dia.slice(5, 7)));
  });
  (serie.tarjetas || []).forEach(tj => {
    const tt = Date.parse(tj.colocada);
    if (tt < t0 || tt > t1) return;
    const x = X(tj.colocada);
    svg.append(s('line', { x1: x, x2: x, y1: mt, y2: H - mb, stroke: 'currentColor', 'stroke-opacity': 0.45,
      'stroke-dasharray': '4 4' }), s('text', { x: x + 3, y: mt + 10, 'font-size': 10, fill: 'currentColor',
      'fill-opacity': 0.7 }, 'tarjeta nueva'));
  });
  for (const [k, color, nombre] of [['wf_media', '#E04449', 'Mosca blanca'], ['tr_media', '#B45309', 'Trips']]) {
    svg.append(s('polyline', { points: dias.map(d => `${X(d.dia)},${Y(d[k])}`).join(' '), fill: 'none',
      stroke: color, 'stroke-width': 2 }));
    dias.forEach(d => {
      const c = s('circle', { cx: X(d.dia), cy: Y(d[k]), r: 3.5, fill: color });
      c.append(s('title', {}, `${nombre} · ${d.dia}: media ${d[k]} en ${d.n_fotos} foto(s)`
        + (d.pendientes ? ` · ${d.pendientes} sin analizar` : '')));
      svg.append(c);
    });
  }
  svg.append(s('text', { x: ml, y: H - 8, 'font-size': 11, fill: 'currentColor', 'fill-opacity': 0.7 },
    'Media diaria de insectos en las fotos analizadas (acumulado en la tarjeta)'));
  const ult = dias[dias.length - 1];
  return el('div', {},
    el('div', { style: 'display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 16px;margin-bottom:4px' },
      el('b', {}, serie.alias),
      el('span', {}, el('span', { style: 'color:#E04449' }, '●'), ` Mosca blanca ${ult.wf_media}`),
      el('span', {}, el('span', { style: 'color:#B45309' }, '●'), ` Trips ${ult.tr_media}`),
      el('span', { class: 'muted', style: 'font-size:12px' }, `último día ${ult.dia} · modelo ${(ult.modelos || []).join(', ') || '—'}`)),
    svg);
}

async function abrirTendencia(filtro, titulo) {
  const qs = new URLSearchParams(Object.entries(filtro).filter(([, v]) => v != null)).toString();
  const cuerpo = el('div', {}, el('p', { class: 'muted' }, 'Cargando…'));
  modal({
    title: `📈 Tendencia por días · ${titulo}`,
    className: 'ancho',
    body: cuerpo,
    actions: (close) => [
      el('a', { class: 'btn ghost', href: `${API}/stats/tendencia?${qs}${qs ? '&' : ''}formato=csv`,
        download: 'tendencia_entomira.csv' }, '⬇ CSV'),
      ME && ME.rol === 'admin' ? el('button', { class: 'btn ghost', title: 'El worker vuelve a analizar estas fotos con el modelo que tenga puesto', onclick: async () => {
        if (!confirm('¿Volver a analizar todas estas fotos con el modelo actual del worker? Mientras tanto sigue valiendo el conteo anterior.')) return;
        try { const r = await api('/images/reprocesar' + (filtro.trampa_id ? `?trampa=${filtro.trampa_id}` : ''), { method: 'POST' });
              alert(`${r.marcadas} fotos en cola para reanalizar.`); }
        catch (x) { alert(x.message); }
      } }, '🔁 Reanalizar') : null,
      el('button', { class: 'btn', onclick: close }, 'Cerrar')],
  });
  try {
    const d = await api(`/stats/tendencia${qs ? '?' + qs : ''}`);
    cuerpo.innerHTML = '';
    const series = (d.series || []).filter(x => x.dias.length);
    if (!series.length) { cuerpo.append(el('p', { class: 'muted' }, 'Sin fotos todavía.')); return; }
    series.forEach(x => cuerpo.append(el('div', { style: 'margin-bottom:18px' }, graficaTendencia(x))));
    cuerpo.append(el('p', { class: 'muted', style: 'font-size:12px' },
      'Si un día hay varias fotos, se hace la media. Se recalcula cada vez: si se reanalizan las '
      + 'fotos con un modelo mejor, la gráfica se actualiza sola.'));
  } catch (x) { cuerpo.innerHTML = ''; cuerpo.append(el('p', {}, '❌ ' + x.message)); }
}

/* ----- Plagas (guía de fauna) ----- */
const FAUNA = [
  { k: 'WF', nombre: 'Mosca blanca', sci: 'Bemisia tabaci / Trialeurodes vaporariorum',
    tipo: 'Plaga', color: '#E04449', emoji: '🪰',
    desc: 'Pequeño insecto blanco que se alimenta de la savia en el envés de las hojas. Es la plaga más común del invernadero mediterráneo.',
    a: ['Daño', 'Debilita la planta, excreta melaza (favorece la negrilla) y transmite virosis graves como el TYLCV (virus de la cuchara del tomate).'],
    b: ['Manejo', 'Umbrales bajos si hay riesgo de virus. Control biológico, mallas en las entradas y químico solo si supera el umbral.'] },
  { k: 'TR', nombre: 'Trips', sci: 'Frankliniella occidentalis / Echinothrips americanus',
    tipo: 'Plaga', color: '#B45309', emoji: '🦟',
    desc: 'Insecto alargado de 1–2 mm que puede afectar hojas y frutos.',
    a: ['Daño', 'Raspa y succiona los tejidos, causando plateado y deformaciones en hojas y frutos. Frankliniella occidentalis transmite el virus del bronceado del tomate (TSWV).'],
    b: ['Manejo', 'Control biológico con Orius y ácaros fitoseidos (Amblyseius swirskii), placas cromotrópicas y mallas; control químico solo por encima del umbral.'] },
];
async function viewPlagas(app) {
  app.innerHTML = '';
  const s = await api('/stats').catch(() => ({}));
  app.append(el('div', { class: 'view-head' }, el('h1', {}, 'Guía de fauna'),
    el('button', { class: 'btn sm', onclick: () => abrirTendencia({}, 'todas las trampas') }, '📈 Tendencia por días')));
  // Un cero de «en las tarjetas» con fotos en cola no es un cero medido, igual que en el mapa.
  if ((s.pendientes ?? 0) > 0) {
    app.append(el('div', { class: 'nota-pendiente' },
      `⏳ ${s.pendientes} fotos sin analizar: los recuentos de abajo no las incluyen todavía.`));
  }
  const grid = el('div', { class: 'grid gh' });
  FAUNA.forEach(f => {
    const n = (s.por_especie || {})[f.k] || 0;
    grid.append(el('div', { class: 'card', style: `border-top:4px solid ${f.color}` },
      el('div', { class: 'body' },
        el('div', { style: 'font-size:34px' }, f.emoji),
        el('h3', { style: 'margin:4px 0 0' }, f.nombre),
        el('div', { class: 'muted', style: 'font-style:italic;font-size:13px;margin-bottom:8px' }, f.sci),
        el('div', { class: 'row', style: 'justify-content:flex-start;gap:8px;margin-bottom:10px' },
          el('span', { class: 'tag', style: `background:${f.color}22;color:${f.color}` }, f.tipo),
          el('span', { class: 'tag gray', title: 'Pegados ahora mismo en las tarjetas puestas' },
            `🐛 ${n} en las tarjetas`)),
        el('p', { style: 'font-size:14px' }, f.desc),
        // El patron etiqueta/valor vale para un dato corto («Batería  100 %»), no para un
        // parrafo: «Daño» y «Manejo» son cuatro renglones, y alineados a la derecha y en
        // bandera se leen fatal —el ojo pierde el arranque de cada linea—. Van debajo de su
        // titulo y alineados a la izquierda, que es como se lee un texto.
        fichaBloque(f.a[0], f.a[1]),
        fichaBloque(f.b[0], f.b[1]))));
  });
  app.append(grid);
}

/**
 * Bloque de ficha: titulo arriba, texto debajo y a la izquierda.
 *
 * Sustituye al uso de `.kv` para prosa. `.kv` empareja una etiqueta con un dato corto en la
 * misma linea; con un parrafo de cuatro renglones alineado a la derecha, el ojo tiene que
 * buscar donde empieza cada linea.
 */
function fichaBloque(titulo, texto) {
  return el('div', { class: 'ficha-bloque' },
    el('b', {}, titulo),
    el('p', { class: 'muted' }, texto));
}

/* ----- Vista de cliente (solo lectura) ----- */
async function viewCliente(app, token) {
  app.innerHTML = '';
  const base = `/client/${encodeURIComponent(token)}`;
  const data = await api(base).catch(() => null);
  if (!data) { app.append(emptyState('🔒', 'Enlace no válido', 'Pide un enlace actualizado.')); return; }
  const gh = data.greenhouse;
  app.append(el('div', { class: 'view-head client-map-head' },
    el('h1', {}, `🐛 ENTOMIRA · ${gh.nombre}`)));
  try {
    const stats = await api(`${base}/stats`);
    app.append(renderKpis(stats), renderPoblacion(stats));
  } catch (e) {
    app.append(el('p', { class: 'muted' }, 'No se pudo cargar la población.'));
  }
  app.append(el('h2', {}, 'Fotos'), el('div', { class: 'grid gal', id: 'client-photos' }));
  const grid = $('#client-photos');
  const more = el('button', { class: 'btn ghost', style: 'display:none;margin:16px auto', onclick: loadMore }, 'Cargar más');
  app.append(more);
  let offset = 0;
  const pageSize = 60;
  async function loadMore() {
    more.disabled = true;
    try {
      const images = await api(`${base}/images?limit=${pageSize}&offset=${offset}`);
      images.forEach(im => grid.append(galCard(im, `${base}/images`, true)));
      offset += images.length;
      if (!offset) grid.append(emptyState('🖼️', 'Aún no hay imágenes', 'Las capturas aparecerán aquí una vez procesadas por la IA.'));
      more.style.display = images.length === pageSize ? 'block' : 'none';
    } catch (e) {
      if (!offset) grid.append(el('p', { class: 'muted' }, 'No se pudieron cargar las fotos.'));
      more.style.display = offset ? 'block' : 'none';
    } finally { more.disabled = false; }
  }
  await loadMore();
  app.append(mapSummary(gh, data));
  if (!data.num_trampas) app.append(mapEmptyState(gh));
  else {
    // El mismo plano que ve el tecnico dentro: si el cliente viera otra cosa, estariamos
    // manteniendo dos dibujos del mismo invernadero — y uno de los dos se quedaria viejo.
    // Sin trampas individuales: el enlace de cliente no lista dispositivos.
    app.append(planoInvernadero(data, []), leyendaPlano(data, { conTrampas: false }));
  }
  app.append(el('p', { class: 'muted client-map-footer' }, 'Vista de solo lectura · ENTOMIRA'));
}

/* ---------- helpers de UI ---------- */
function speciesSummary(dets) {
  const c = {};
  (dets || []).forEach(d => { c[d.clase] = (c[d.clase] || 0) + 1; });
  return Object.entries(c).map(([k, v]) => `${clsName(k)}: ${v}`).join('  ·  ');
}
function speciesChips(dets) {
  const c = {};
  (dets || []).forEach(d => { c[d.clase] = (c[d.clase] || 0) + 1; });
  const cont = el('div', { class: 'sp-chips' });
  const entries = Object.entries(c).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return cont;
  entries.forEach(([k, v]) => {
    const m = clsMeta(k);
    cont.append(el('div', { class: 'sp-chip', style: `border-color:${m.color}` },
      el('span', { class: 'sp-emoji' }, m.emoji),
      el('div', {},
        el('div', { class: 'sp-name', style: `color:${m.color}` }, `${m.name} × ${v}`),
        m.tipo ? el('div', { class: 'sp-tipo' }, m.tipo) : null)));
  });
  return cont;
}
async function buildTrampasPanels(panels) {
  const [sys, wk, params] = await Promise.all([
    api('/system/health').catch(() => ({})),
    api('/worker/stats').catch(() => ({})),
    api('/worker/params').catch(() => ({ conf: 0.25, iou: 0.45, intervalo: 2 })),
  ]);
  // salud del sistema
  const hi = (ic, txt) => el('span', { class: 'hi' }, ic, txt);
  panels.append(el('div', { class: 'card', style: 'margin-bottom:16px' }, el('div', { class: 'body' },
    el('h3', {}, '🩺 Salud del sistema'),
    el('div', { class: 'hpanel', style: 'margin-top:8px' },
      hi('💾', el('span', {}, el('b', {}, `${sys.disk_used_pct ?? '—'}%`), ` disco usado (${sys.disk_free_gb ?? '—'} GB libres)`)),
      hi('🖼️', el('span', {}, 'imágenes ', el('b', {}, `${sys.images_mb ?? '—'} MB`), ' · BD ', el('b', {}, `${sys.db_mb ?? '—'} MB`))),
      hi('⏱️', el('span', {}, 'servidor activo ', el('b', {}, `${sys.uptime_h ?? '—'} h`))),
      hi('🔒', el('b', {}, 'acceso protegido')),
      hi('', el('span', { class: 'muted' }, 'v' + (sys.version || '—')))))));

  // worker + gpu + pi
  const stat = (lbl, val) => el('div', { class: 'st' }, el('span', { class: 'muted' }, lbl), el('b', {}, val));
  const pi = wk.pi || {};
  panels.append(el('div', { class: 'card', style: 'margin-bottom:16px' }, el('div', { class: 'body' },
    el('div', { class: 'row' }, el('h3', { style: 'margin:0' }, '🖥️ Worker del PC'),
      el('span', { class: wk.online ? 'tag' : 'tag gray' }, wk.online ? '● en línea' : '○ offline')),
    wk.online ? el('div', {},
      el('div', { class: 'muted', style: 'margin:10px 0 2px;font-weight:700' }, '🖥️ ' + (wk.gpu_name || 'GPU')),
      el('div', { class: 'statgrid' },
        stat('GPU temp', fmtU(wk.gpu_temp, '°C')), stat('GPU uso', fmtU(wk.gpu_util, '%')),
        stat('GPU memoria', wk.gpu_mem_used != null ? `${wk.gpu_mem_used.toFixed(1)}/${(wk.gpu_mem_total || 0).toFixed(0)} GB` : '—'),
        stat('GPU potencia', fmtU(wk.gpu_power, 'W')),
        stat('CPU', fmtU(wk.cpu, '%')),
        stat('RAM', wk.ram_used != null ? `${wk.ram_used}/${wk.ram_total} GB` : '—')),
      el('div', { class: 'muted', style: 'margin:12px 0 2px;font-weight:700' }, '🍓 Raspberry Pi (servidor)'),
      el('div', { class: 'statgrid' },
        stat('CPU temp', fmtU(pi.cpu_temp, '°C')), stat('CPU', fmtU(pi.cpu, '%')),
        stat('RAM', pi.ram_used != null ? `${pi.ram_used}/${pi.ram_total} MB` : '—')))
      : el('p', { class: 'muted', style: 'margin-top:10px' }, 'Worker_IA.exe no está en marcha.'),
    // parámetros de inferencia
    el('div', { class: 'filters', style: 'margin-top:14px;align-items:flex-end' },
      (() => { const c = el('input', { type: 'number', step: '0.05', min: 0, max: 1, value: params.conf }); c.id = 'p-conf'; return el('label', { class: 'field' }, 'Confianza (conf)', c); })(),
      (() => { const c = el('input', { type: 'number', step: '0.05', min: 0, max: 1, value: params.iou }); c.id = 'p-iou'; return el('label', { class: 'field' }, 'Solape (IOU)', c); })(),
      (() => { const c = el('input', { type: 'number', min: 1, value: params.intervalo }); c.id = 'p-int'; return el('label', { class: 'field' }, 'Intervalo (s)', c); })(),
      el('button', { class: 'btn', onclick: async () => {
        try { await api('/worker/params', { method: 'PUT', body: JSON.stringify({
          conf: +$('#p-conf').value, iou: +$('#p-iou').value, intervalo: +$('#p-int').value }) });
          toast('Parámetros aplicados'); } catch (e) { toast(e.message, true); }
      } }, 'Aplicar'),
      el('button', { class: 'btn ghost', onclick: async () => {
        const p = await api('/worker/params'); const np = !p.pausado;
        await api('/worker/params', { method: 'PUT', body: JSON.stringify({ pausado: np }) });
        toast(np ? 'Análisis pausado' : 'Análisis reanudado');
      } }, '⏯ Pausar/Reanudar')))));
}
function fmtU(v, u) { return v == null ? '—' : `${v}${u}`; }
function renderKpis(s) {
  const kpi = (cls, lbl, val, sub) => el('div', { class: 'kpi ' + cls },
    el('div', { class: 'k-lbl' }, lbl), el('div', { class: 'k-val' }, String(val)),
    el('div', { class: 'k-sub' }, sub));
  return el('div', { class: 'kpis' },
    kpi('', 'Fotos', s.fotos ?? 0, `${s.analizadas ?? 0} analizadas · ${s.pendientes ?? 0} pendientes`),
    // «En las tarjetas» es un ACUMULADO: lo pegado ahora mismo, leído de la última foto de
    // cada trampa. Antes decía «insectos detectados» y sumaba todas las fotos, o sea el mismo
    // bicho una vez por captura: con 108 fotos/día, 30 moscas salían como 3.240.
    // Los dos conteos que salen de la IA llevan la coletilla cuando hay cola: son los que se
    // leen como «no hay plaga» si se enseña un cero a secas.
    kpi('', '🐛 En las tarjetas', s.insectos ?? 0,
      (s.pendientes ?? 0) > 0 ? `pegados ahora · ${s.pendientes} sin analizar` : 'pegados ahora · acumulado'),
    kpi('amber', '🪰 Mosca blanca', s.mosca_blanca ?? 0, 'plaga · objetivo de control'),
    kpi('amber', '🦟 Trips', s.trips ?? 0, 'plaga'),
    // Y esto es el RITMO: la derivada de la curva de acumulación, que es con lo que se decide
    // un tratamiento. No cambia si cambias el intervalo de captura.
    kpi('', 'Nuevas en 24 h', s.actividad_1d ?? 0,
      (s.pendientes ?? 0) > 0
        ? `capturas nuevas · ${s.pendientes} sin analizar`
        : `capturas nuevas · ${s.trampas_activas ?? 0} trampa(s) activas`));
}
function renderComposition(s) {
  const wf = s.mosca_blanca || 0, tr = s.trips || 0;
  const tot = wf + tr;
  if (!tot) return el('div', {});
  const pct = v => (v / tot * 100).toFixed(0);
  const seg = (v, c) => el('span', { style: `width:${v / tot * 100}%;background:${c}` });
  return el('div', { class: 'card', style: 'margin-bottom:18px' }, el('div', { class: 'body' },
    el('div', { class: 'muted', style: 'font-weight:700;margin-bottom:8px' }, 'Composición por especie'),
    el('div', { class: 'compbar' }, seg(wf, '#E04449'), seg(tr, '#B45309')),
    el('div', { class: 'complegend' },
      el('span', {}, el('span', { class: 'dot', style: 'background:#E04449' }), `Mosca blanca ${wf} · ${pct(wf)}%`),
      el('span', {}, el('span', { class: 'dot', style: 'background:#B45309' }), `Trips ${tr} · ${pct(tr)}%`))));
}
function field(label, input) { return el('label', { class: 'field' }, label, input); }
function emptyState(icon, title, sub) {
  return el('div', { class: 'empty' }, el('div', { class: 'big' }, icon), el('h3', {}, title), el('p', { class: 'muted' }, sub || ''));
}

/* ---------- Usuarios (solo admin) ---------- */
function field(label, input) {
  return el('label', { class: 'field' }, el('span', { class: 'muted' }, label), input);
}
async function viewUsuarios(app) {
  app.innerHTML = '';
  const [users, ghs] = await Promise.all([
    api('/users').catch(() => []), api('/greenhouses').catch(() => [])]);

  app.append(el('div', { class: 'view-head' }, el('h1', {}, 'Usuarios')));

  const f = { rol: 'agricultor' };
  const form = el('div', { class: 'card' }, el('div', { class: 'body' },
    el('h2', { style: 'font-size:18px;margin-bottom:12px' }, 'Registrar agricultor'),
    el('div', { class: 'user-form' },
      field('Nombre y apellidos', el('input', { placeholder: 'Nombre Apellido', oninput: e => f.nombre = e.target.value })),
      field('Usuario', el('input', { placeholder: 'usuario', oninput: e => f.username = e.target.value.trim() })),
      field('Contraseña', el('input', { type: 'password', oninput: e => f.password = e.target.value })),
      field('Invernadero', el('select', { onchange: e => f.invernadero_id = e.target.value },
        el('option', { value: '' }, '— elige invernadero —'),
        ...ghs.map(g => el('option', { value: g.id }, g.nombre))))),
    el('button', {
      class: 'btn', style: 'margin-top:12px', onclick: async () => {
        if (!f.nombre || !f.username || !f.password) { toast('Rellena nombre, usuario y contraseña', true); return; }
        if (!f.invernadero_id) { toast('El agricultor debe estar asociado a un invernadero', true); return; }
        try {
          await api('/users', { method: 'POST', body: JSON.stringify({
            username: f.username, password: f.password, nombre: f.nombre,
            rol: 'agricultor', invernadero_id: Number(f.invernadero_id) }) });
          toast('Usuario registrado'); viewUsuarios(app);
        } catch (e) { toast(e.message, true); }
      }
    }, '＋ Registrar')));
  app.append(form);

  app.append(el('div', { class: 'view-head', style: 'margin-top:18px' },
    el('h2', { style: 'font-size:20px' }, 'Agricultores')));
  if (!users.length) { app.append(emptyState('👤', 'Sin usuarios', 'Registra el primer agricultor arriba.')); return; }
  const grid = el('div', { class: 'grid users' });
  users.forEach(u => grid.append(userCard(u, app)));
  app.append(grid);
}
// Editar nombre e invernadero. Antes solo se podía crear y borrar: un cambio de nombre o de
// finca obligaba a borrar al usuario y crearlo otra vez, perdiendo lo que tuviera asociado.
async function userForm(u, app) {
  const ghs = await api('/greenhouses').catch(() => []);
  const nombre = el('input', { value: u.nombre || '' });
  const gh = el('select', {}, el('option', { value: '' }, '— sin invernadero —'),
    ...ghs.map(g => el('option', { value: g.id, selected: u.invernadero_id === g.id ? 'selected' : null }, g.nombre)));
  modal({
    title: `Editar · @${u.username}`,
    body: el('div', { class: 'form-grid' },
      field('Nombre y apellidos', nombre), field('Invernadero', gh)),
    actions: (close) => [
      el('button', { class: 'btn ghost', onclick: close }, 'Cancelar'),
      el('button', { class: 'btn', onclick: async () => {
        try {
          await api('/users/' + u.id, { method: 'PATCH', body: JSON.stringify({
            nombre: nombre.value, invernadero_id: gh.value ? Number(gh.value) : null }) });
          toast('Usuario actualizado'); close(); viewUsuarios(app);
        } catch (e) { toast(e.message, true); }
      } }, 'Guardar')],
  });
}

function userPassword(u) {
  const p1 = el('input', { type: 'password', placeholder: 'nueva contraseña' });
  const p2 = el('input', { type: 'password', placeholder: 'repítela' });
  modal({
    title: `Contraseña de @${u.username}`,
    body: el('div', { class: 'form-grid' },
      field('Nueva contraseña', p1), field('Repetir', p2)),
    actions: (close) => [
      el('button', { class: 'btn ghost', onclick: close }, 'Cancelar'),
      el('button', { class: 'btn', onclick: async () => {
        if (!p1.value || p1.value !== p2.value) { toast('Las contraseñas no coinciden', true); return; }
        try {
          await api('/users/' + u.id, { method: 'PATCH', body: JSON.stringify({ password: p1.value }) });
          toast('Contraseña cambiada'); close();
        } catch (e) { toast(e.message, true); }
      } }, 'Cambiar')],
  });
}

function userCard(u, app) {
  return el('div', { class: 'card' + (u.activo ? '' : ' inactivo') }, el('div', { class: 'body' },
    el('div', { class: 'row' }, el('b', {}, u.nombre || u.username),
      el('span', { class: 'tag' + (u.rol === 'admin' ? '' : ' gray') }, u.rol),
      u.activo ? null : el('span', { class: 'tag', style: 'background:#E0444922;color:#E04449' }, 'de baja')),
    kv('Usuario', '@' + u.username),
    kv('Invernadero', u.invernadero_nombre || '—'),
    kv('Dispositivo', u.device_id || '(sin subir aún)'),
    kv('Fotos subidas', String(u.num_fotos)),
    u.rol !== 'admin' ? el('div', { class: 'acciones' },
      el('button', { class: 'btn ghost sm', onclick: () => userForm(u, app) }, '✎ Editar'),
      el('button', { class: 'btn ghost sm', onclick: () => userPassword(u) }, '🔑 Contraseña'),
      // Dar de baja en vez de borrar: al agricultor que deja la finca hay que quitarle el
      // acceso, pero borrarlo se lleva por delante la trazabilidad de lo que subió.
      el('button', { class: 'btn ghost sm', onclick: async () => {
        try {
          await api('/users/' + u.id, { method: 'PATCH', body: JSON.stringify({ activo: !u.activo }) });
          toast(u.activo ? 'Usuario desactivado' : 'Usuario activado'); viewUsuarios(app);
        } catch (e) { toast(e.message, true); }
      } }, u.activo ? '🚫 Desactivar' : '✅ Activar'),
      el('button', { class: 'btn danger sm', onclick: async () => {
        if (!confirm('¿Eliminar a ' + (u.nombre || u.username) + '? Se pierde la trazabilidad de '
                     + 'sus fotos; para quitarle el acceso basta con desactivarlo.')) return;
        try { await api('/users/' + u.id, { method: 'DELETE' }); toast('Usuario eliminado'); viewUsuarios(app); }
        catch (e) { toast(e.message, true); }
      } }, '🗑')) : null));
}

/* ---------- Ajuste de color (solo admin) ---------- */
async function viewColor(app) {
  app.innerHTML = '';
  const p = await api('/image-params').catch(() => ({ r_gain: 0.9, g_gain: 1.25, b_gain: 0.9 }));
  const state = { r: p.r_gain, g: p.g_gain, b: p.b_gain };
  const corrected = el('img', { class: 'color-preview' });
  const raw = el('img', { class: 'color-preview', src: `${API}/image-params/preview?raw=true&t=${Date.now()}` });
  let deb;
  const refresh = () => {
    clearTimeout(deb);
    deb = setTimeout(() => {
      corrected.src = `${API}/image-params/preview?r=${state.r}&g=${state.g}&b=${state.b}&t=${Date.now()}`;
    }, 110);
  };
  const sliders = {};
  const mk = (label, key, color) => {
    const val = el('b', {}, state[key].toFixed(2));
    const inp = el('input', {
      type: 'range', min: '0.5', max: '1.6', step: '0.01', value: String(state[key]),
      oninput: e => { state[key] = parseFloat(e.target.value); val.textContent = state[key].toFixed(2); refresh(); },
    });
    sliders[key] = [inp, val];
    return el('div', { class: 'color-slider' },
      el('div', { class: 'row', style: 'justify-content:space-between' },
        el('span', { style: `color:${color};font-weight:800` }, label), val), inp);
  };
  const setDefaults = () => {
    const d = { r: 0.9, g: 1.25, b: 0.9 };
    for (const k in d) { state[k] = d[k]; sliders[k][0].value = String(d[k]); sliders[k][1].textContent = d[k].toFixed(2); }
    refresh();
  };
  const panel = el('div', { class: 'card' }, el('div', { class: 'body' },
    el('h2', { style: 'font-size:16px;margin-bottom:4px' }, 'Ganancia por canal'),
    el('p', { class: 'muted', style: 'margin:0 0 12px;font-size:13px' },
      'Corrige el tinte del sensor. Se aplica a las próximas capturas de la trampa.'),
    mk('Rojo', 'r', '#E04449'), mk('Verde', 'g', '#5C9A0E'), mk('Azul', 'b', '#2b6cb0'),
    // `flex-wrap`: los botones no parten su texto (`.btn{white-space:nowrap}`), asi que si no
    // pueden envolverse se salen de la tarjeta. Envolver es lo correcto: tres botones no caben
    // en una columna estrecha y bajar uno se lee mejor que recortarlo.
    el('div', { style: 'display:flex;gap:10px;margin-top:16px;flex-wrap:wrap' },
      el('button', {
        class: 'btn', onclick: async () => {
          try { await api('/image-params', { method: 'PUT', body: JSON.stringify({ r_gain: state.r, g_gain: state.g, b_gain: state.b }) }); toast('Ajuste de color guardado'); }
          catch (e) { toast(e.message, true); }
        }
      }, 'Guardar'),
      el('button', { class: 'btn ghost', onclick: setDefaults }, 'Restablecer'),
      el('button', {
        class: 'btn ghost', onclick: () => {
          const d = { r: 1, g: 1, b: 1 };
          for (const k in d) { state[k] = d[k]; sliders[k][0].value = '1'; sliders[k][1].textContent = '1.00'; }
          refresh();
        }
      }, 'Sin corrección'))));
  const compare = el('div', { class: 'color-compare' },
    el('figure', {}, raw, el('figcaption', { class: 'muted' }, 'Original (sin corregir)')),
    el('figure', {}, corrected, el('figcaption', { class: 'muted' }, 'Corregido · en vivo')));
  app.append(el('div', { class: 'view-head' }, el('h1', {}, 'Ajuste de color')),
    el('div', { class: 'color-layout' }, panel, compare));
  const camaras = el('section', { style: 'margin-top:24px' },
    el('h2', {}, 'Parámetros de cámara por trampa'),
    el('p', { class: 'muted' },
      'Enfoque, balance de blancos, exposición y calidad de las próximas fotos.'));
  app.append(camaras);
  try {
    const trampas = (await api('/traps')).filter(t => !t.es_movil);
    if (!camaras.isConnected) return;
    if (!trampas.length) camaras.append(el('p', { class: 'muted' }, 'No hay trampas configuradas.'));
    for (const t of trampas) {
      const cfg = t.config || {};
      camaras.append(el('div', { class: 'card', style: 'margin-top:12px' },
        el('div', { class: 'body' },
          el('h3', {}, t.alias || t.device_id),
          el('p', { class: 'muted' },
            `Enfoque: ${nombreEnfoque(cfg.enfoque)} · Balance: ${nombreBalance(cfg.balance)}`),
          el('div', { style: 'display:flex;gap:10px;flex-wrap:wrap' },
            el('button', { class: 'btn', onclick: () => trapConfig(t, 'camara') },
              'Editar parámetros'),
            el('button', { class: 'btn ghost', onclick: async () => {
              const nota = prompt('¿Por qué es buena esta configuración?', '');
              if (nota === null) return;
              try {
                await api(`/traps/${t.id}/perfil`, { method: 'POST', body: JSON.stringify({ nota }) });
                toast('Perfil completo guardado, incluidos modo y horarios.');
              } catch (e) { toast(e.message, true); }
            } }, 'Guardar perfil completo'),
            el('button', { class: 'btn ghost', onclick: async () => {
              let perfil;
              try { perfil = await api(`/traps/${t.id}/perfil`); }
              catch { perfil = null; }
              if (!perfil?.guardado) { toast('Esta trampa no tiene perfil guardado.', true); return; }
              if (!confirm(`¿Restaurar el perfil del ${fmtDate(perfil.guardado)} de ${t.alias || t.device_id}?`
                + '\nTambién cambiará el modo y los horarios guardados.')) return;
              try {
                await api(`/traps/${t.id}/perfil/restaurar`, { method: 'POST' });
                toast('Perfil completo restaurado.');
                invalidateApiCache();
                viewColor(app);
              } catch (e) { toast(e.message, true); }
            } }, 'Restaurar perfil completo')))));
    }
  } catch {
    camaras.append(el('p', { role: 'alert' }, 'No se pudieron cargar las trampas.'));
  }
  refresh();
}

/* ---------- Sensores en directo (con cámara o sin ella) ----------
   Antonio, 2026-09-25: «si estoy en el panel quiero ver los datos instantáneos de voltaje y
   corriente», y «a veces si no hay cámara no actualiza el estado de los sensores». Lo segundo
   tenía causa: lo que hacía informar deprisa a la trampa era el latido del VÍDEO, y sin cámara
   el visor se rinde a los 40 s y lo para. Esta caja lleva su latido propio
   (`POST /traps/<id>/sensores`), que no enciende la cámara, y se actualiza EN SU SITIO: nada de
   `router()`, que repinta la vista entera y corta el vídeo.
   Cada número dice su edad, y los huecos se dicen como huecos: aquí un 0 es una medida. */
function cajaSensores(id, onCorriente = null) {
  const PERIODO_MS = 2000;
  const v = (x, dec, u) => (x == null ? 'sin dato' : `${x.toFixed(dec)} ${u}`);
  const fila = (k) => {
    const b = el('b', {}, '—');
    return { nodo: el('div', { class: 'ctrl-stat' }, el('span', { class: 'muted' }, k), b), b };
  };
  const f = {
    celda: fila('Batería · tensión INA219'),
    salida: fila('Batería · corriente INA219'),
    soc: fila('Batería · % estimado por tensión'),
    potencia: fila('Estado de carga'),
    icelda: fila('RTC PCF8523'),
  };
  const pie = el('div', { class: 'muted', style: 'font-size:12px;margin-top:6px' }, 'Leyendo sensores…');
  const grafica = el('div', { class: 'muted', style: 'margin-top:8px' }, 'Histórico: sin datos');
  const caja = el('div', { class: 'datos-caja' },
    el('div', { class: 'row', style: 'justify-content:space-between;align-items:baseline' },
      el('b', {}, '📟 Sensores en directo')),
    ...Object.values(f).map(x => x.nodo), pie, grafica);

  const edadTxt = (s) => (s == null ? 'sin lectura' : s < 90 ? `hace ${Math.round(s)} s` : `hace ${Math.round(s / 60)} min`);
  const horasTxt = (h) => {
    if (h == null) return null;
    if (h < 1) return `${Math.round(h * 60)} min`;
    return `${Math.floor(h)} h ${String(Math.round((h % 1) * 60)).padStart(2, '0')} min`;
  };

  let timer = null, enVuelo = false, directoRecibido = false;
  const pinta = (d) => {
    const fresco = d.edad_hw_s != null && d.edad_hw_s <= 120;
    f.celda.b.textContent = v(fresco ? d.ina219_v : null, 3, 'V');
    f.salida.b.textContent = v(fresco ? d.ina219_ma : null, 1, 'mA');
    f.soc.b.textContent = fresco && d.soc_estimado != null ? `≈${d.soc_estimado} %` : 'sin dato';
    if (onCorriente) onCorriente(fresco ? d.ina219_ma : null, fresco ? d : null);
    f.icelda.b.textContent = d.edad_rtc_s != null && d.edad_rtc_s <= 120 && d.rtc_hora
      ? aFecha(d.rtc_hora).toLocaleString('es-ES', { timeZone: 'Europe/Madrid' }) : 'sin dato';
    const estado = d.carga && d.carga.estado ? d.carga.estado : null;
    f.potencia.b.textContent = fresco ? (estado || 'sin dato') : 'sin dato';
    pie.textContent = `INA219 ${edadTxt(d.edad_hw_s)}`
      + (d.rapido ? '' : ' · por 4G no se acelera: llega con cada informe de la trampa');
  };
  const historial = async () => {
    try {
      const datos = await api(`/traps/${id}/hardware?horas=24`);
      const lecturas = datos.lecturas || [];
      const ultima = lecturas.at(-1);
      if (ultima && !directoRecibido) pinta({...ultima, ina219_v: ultima.tension_v, ina219_ma: ultima.corriente_ma,
        edad_hw_s: (Date.now() - Date.parse(ultima.instante)) / 1000, carga: {estado: null}, rapido: true});
      grafica.replaceChildren(el('b', {}, 'Últimas 24 h · INA219'),
        ...[['tension_v', 'Tensión', 'V'], ['corriente_ma', 'Corriente', 'mA']].map(([clave, nombre, unidad]) => {
          const validas = lecturas.filter(x => x[clave] != null);
          if (!validas.length) return el('div', {}, `${nombre}: sin dato`);
          const valores = validas.map(x => x[clave]);
          const min = Math.min(...valores), max = Math.max(...valores), amplitud = max - min || 1;
          const inicio = Date.parse(lecturas[0].instante), fin = Date.parse(lecturas.at(-1).instante);
          const puntos = validas.map(x => `${((Date.parse(x.instante)-inicio)/(fin-inicio || 1)*300).toFixed(1)},${(75-(x[clave]-min)/amplitud*65).toFixed(1)}`).join(' ');
          return el('div', {}, el('div', {}, `${nombre}: ${valores.at(-1).toFixed(2)} ${unidad} · ${min.toFixed(2)}–${max.toFixed(2)} ${unidad}`),
            el('svg', {viewBox:'0 0 300 85', style:'width:100%;height:85px'},
              el('polyline', {points:puntos, fill:'none', stroke:'currentColor', 'stroke-width':'2'})));
        }));
    } catch { grafica.textContent = 'Histórico: no disponible'; }
  };
  const pide = async () => {
    // Fuera de la página, se salta la vuelta pero NO se para el temporizador: la caja se crea
    // ANTES de colgarla de la vista, y parar aquí la mataba en la primera vuelta — se quedaba
    // en «Leyendo sensores…» para siempre (2026-09-25). Quien la para es `onLeave`.
    if (!document.body.contains(caja)) return;
    if (enVuelo) return;
    enVuelo = true;
    try {
      // `fetch` y no `api()`: `api()` vacía la caché de la web en cada POST, y esto va cada 2 s.
      const r = await fetch(`${API}/traps/${id}/sensores`, { method: 'POST' });
      if (r.ok) { directoRecibido = true; pinta(await r.json()); }
      else {
        pie.textContent = `No se pudieron leer los sensores (${r.status}).`;
        if (onCorriente) onCorriente(null);
      }
    } catch {
      pie.textContent = 'Sin conexión con el servidor.';
      if (onCorriente) onCorriente(null);
    }
    enVuelo = false;
  };
  timer = setInterval(pide, PERIODO_MS);
  historial();
  const histTimer = setInterval(historial, 60000);
  setTimeout(pide, 0);                  // en cuanto la vista la haya colgado, sin esperar 2 s
  onLeave(() => { clearInterval(timer); clearInterval(histTimer); });
  return caja;
}

async function viewSensores(app, id) {
  app.innerHTML = '';
  if (!id) { location.hash = '#/trampas'; return; }
  let t;
  try { t = await api('/traps/' + id); }
  catch (e) { app.append(emptyState('⚠️', 'No se pudo cargar la trampa', e.message)); return; }
  const online = trapOnline(t);
  app.append(
    el('div', { class: 'view-head' },
      el('button', { class: 'btn ghost sm', onclick: () => { location.hash = '#/control/' + id; } }, '← Panel'),
      el('h1', { style: 'font-size:22px' }, `${t.alias} · sensores`),
      el('span', { class: online ? 'tag' : 'tag gray', style: 'font-size:12px' }, textoEstado(t))),
    el('div', { class: 'card', style: 'max-width:560px' }, el('div', { class: 'body' },
      el('div', { class: 'muted', style: 'font-size:13px' },
        'Sin cámara: esta vista no pide vídeo, solo mantiene a la trampa informando mientras está abierta.'),
      cajaSensores(id))));
}

/* ---------- Panel de control por trampa (estilo grodi REMOTE) ---------- */
async function viewControl(app, id) {
  app.innerHTML = '';
  if (!id) { location.hash = '#/trampas'; return; }
  let t;
  try { t = await api('/traps/' + id); }
  catch (e) { app.append(emptyState('⚠️', 'No se pudo cargar la trampa', e.message)); return; }
  // Tarjeta adhesiva puesta ahora mismo. Es lo que convierte «insectos en la foto» en algo
  // que un agricultor puede usar: un conteo referido a un periodo de exposición.
  const tarj = await api(`/traps/${id}/tarjeta`).catch(() => null);
  const cfg = t.config || {};

  // Si la trampa está colgada del cable a la Pi, el puente serie SOLO transporta fotos: no
  // hay canal de vuelta, así que la vista en vivo no puede funcionar. Decirlo es mucho mejor
  // que dejar un recuadro negro girando para siempre, que es lo que hacía antes.
  if (!cfg.deep_sleep && t.red === 'usb') {
    app.append(el('div', { class: 'st-alert aviso', style: 'margin-bottom:14px' },
      '🔌 Esta trampa está conectada por USB a la Raspberry Pi. El puente serie solo lleva '
      + 'fotos hacia el servidor, así que NO hay vista en vivo por esta vía. Para verla en '
      + 'directo necesita WiFi o 4G.'));
  } else if (!cfg.deep_sleep && t.red === 'lte') {
    app.append(el('div', { class: 'st-alert aviso', style: 'margin-bottom:14px' },
      '📡 Esta trampa va por red móvil. El vídeo solo se pide al pulsar «Abrir streaming»; '
      + 'puede tardar unos segundos en arrancar y cada minuto abierto consume datos del plan.'));
  }

  // ---------- EL VISOR ----------
  // La última foto guardada se muestra al entrar; MJPEG solo arranca al pulsar el botón.
  //
  // Lo que había aquí era el peor fallo de la vista y lo cazó Codex mirando la captura: un
  // rectángulo enorme con «conectando…» mientras la cabecera decía ONLINE. Antonio lo
  // describió mejor: «la primera vez aparece una imagen estática y al recargar ya se abre
  // directo». Abrir el vídeo pide vídeo, pero la trampa no se entera hasta que le contesta su
  // long polling; el servidor espera 30 s al primer fotograma y, si no llega, cierra. Y una
  // vez caído al snapshot, el latido llevaba `if (!usingStream) return`: **no se volvía al
  // vídeo en toda la visita**. Había que recargar, que es justo lo que hacía Antonio.
  //
  // LO QUE SE MIDIÓ AQUÍ, Y TIRÓ ABAJO UNA SUPOSICIÓN QUE LLEVABA MESES ESCRITA. El código
  // decía —y `live.py` repetía— que «un multipart/x-mixed-replace dispara `load` por CADA
  // fotograma, así que la hora del último prueba que el vídeo sigue llegando». Es falso en
  // Chromium: medido con un contador sobre el <img>, **0 eventos en 60 s mientras la imagen
  // cambiaba**, y el servidor con fotogramas de 0-12 s de edad. `load` se dispara UNA VEZ por
  // apertura, no por fotograma. Consecuencia de la que nadie se había enterado: la
  // reconexión «si no llega un fotograma en 12 s» **reabría el stream cada 12 s** siempre que
  // el servidor emitiera, y cada reapertura cuesta ~3 s de arranque de cámara.
  //
  // Así que aquí no se dice «En vivo». `edad_frame` demuestra que el SERVIDOR recibe imágenes
  // recientes, no que este visor las esté pintando, y afirmar lo segundo con la prueba de lo
  // primero es la clase de mentira que este proyecto paga cara (Codex). Se dice lo que se
  // mide: la edad de la última señal. Y como un stream muerto a media emisión no se puede
  // detectar desde el navegador, se ACOTA: cada 90 s se renueva el stream si el servidor está
  // recibiendo. No detecta el atasco; limita cuánto puede durar.
  const snap = el('img', { class: 'ctrl-cam' });        // el directo (MJPEG)
  const fondo = el('img', { class: 'cam-fondo' });      // lo que se ve mientras no hay directo
  const camEstado = el('div', { class: 'cam-estado' }, 'Última imagen');
  const camAviso = el('div', { class: 'cam-aviso' }, '');
  const btnReintentar = el('button', { class: 'btn sm cam-retry' }, '↻ Reintentar vídeo');
  const btnStreaming = el('button', { class: 'btn ghost sm', type: 'button' }, 'Abrir streaming');
  fondo.hidden = true;
  snap.hidden = true;
  btnReintentar.hidden = true;
  let ultimaImagenId = t.ultima_imagen?.id ?? null;
  let ultimaImagenCapturada = t.ultima_imagen?.capturada ?? null;
  const fotoGuardada = () => ultimaImagenId == null ? '' : `${API}/images/${ultimaImagenId}/file`;

  const hace = (sg) => {
    if (sg < 90) return `hace ${Math.round(sg)} s`;
    if (sg < 5400) return `hace ${Math.round(sg / 60)} min`;
    if (sg < 172800) return `hace ${Math.round(sg / 3600)} h`;
    return `hace ${Math.round(sg / 86400)} días`;
  };

  // Segundos desde la última comunicación de la trampa. Es la única prueba admisible para
  // decir que está en línea: no depende del vídeo. Si se dedujera del propio stream, «la
  // trampa está en línea» sería otra forma de decir «el vídeo va», que es lo que se discute.
  const desdeContacto = () => {
    if (!t.ultimo_visto) return null;
    const sg = (Date.now() - aFecha(t.ultimo_visto).getTime()) / 1000;
    return sg >= 0 ? sg : null;
  };

  // Qué imagen se ve, separado a propósito de lo que DICE la etiqueta: el sondeo puede traer
  // una foto recién hecha (que es cámara) o una del archivo (que no), y se pintan igual.
  const capa = (cual) => {
    snap.hidden = cual !== 'directo';
    fondo.hidden = cual !== 'sondeo' || !fondo.getAttribute('src');
  };

  // La edad de la foto se calcula, no se guarda hecha: si se dejara escrita, «hace 2 min»
  // seguiría diciendo 2 min diez minutos después. Se refresca el texto sin volver a pedir la
  // imagen, que fue lo que pidió Codex al bajar el sondeo a 5 s.
  let edadAlLlegar = t.ultima_imagen?.capturada
    ? Math.max(0, (Date.now() - aFecha(t.ultima_imagen.capturada).getTime()) / 1000) : 0;
  let tomadaEn = Date.now(), modoActual = 'cerrado';
  let edadServidor = null;              // `edad_frame` del último latido: null = sin dato
  let viaServidor = null;               // 'empuje' | 'directo' | null, según el servidor
  let comprobandoSenal = false, falloSenal = false, pingGen = 0;
  const edadAhora = () => edadAlLlegar + (tomadaEn ? (Date.now() - tomadaEn) / 1000 : 0);

  // La etiqueta del vídeo dice LA EDAD DE LA SEÑAL, que es lo medido, y nunca «en vivo».
  // Los tramos los fijó Codex.
  //
  // Y NO SE JUZGA LA VÍA QUE NO SE MIDE. Por el relay DIRECTO por IP los bytes van de la
  // trampa al navegador sin pasar por el servidor, así que `edad_frame` se queda viejo aunque
  // el vídeo vaya perfecto: medido el 2026-09-19, «Sin señal reciente · hace 2 min» con la
  // imagen cambiando delante. Un <img> no puede leer cabeceras para distinguirlo, así que
  // ahora lo dice el servidor en el latido (`via`), que es quien elige el camino.
  const etiquetaDirecto = () => {
    if (comprobandoSenal) return { txt: 'Comprobando señal…', clase: ' archivo' };
    if (falloSenal) return { txt: 'No se pudo comprobar la señal', clase: ' archivo' };
    if (viaServidor === 'directo') return { txt: '● Conexión directa', clase: '' };
    if (edadServidor == null) return { txt: 'Sin datos de señal', clase: ' archivo' };
    const s = Math.round(edadServidor);
    if (edadServidor < 6) return { txt: `● Señal ${hace(s)}`, clase: '' };
    if (edadServidor <= 20) return { txt: `Señal ${hace(s)}`, clase: '' };
    return { txt: `Sin señal reciente · ${hace(s)}`, clase: ' archivo' };
  };

  const pintaVisor = (modo) => {        // 'cerrado' | 'directo' | 'esperando' | 'archivo' | 'nada'
    modoActual = modo;
    const hayFoto = !!fondo.getAttribute('src');
    const sg = desdeContacto();
    const senal = sg != null ? ` Última señal de la trampa ${hace(sg)}.` : '';
    fondo.classList.toggle('vieja', modo !== 'directo');
    if (modo === 'cerrado') {
      camEstado.className = 'cam-estado ' + (hayFoto ? 'archivo' : 'sin');
      camEstado.textContent = hayFoto ? `Última imagen · ${hace(edadAhora())}` : 'Sin imágenes';
      camAviso.textContent = '';
    } else if (modo === 'directo') {
      const e = etiquetaDirecto();
      camEstado.className = 'cam-estado' + e.clase;
      camEstado.textContent = e.txt;
      camAviso.textContent = '';
    } else if (modo === 'nada') {
      camEstado.className = 'cam-estado sin';
      camEstado.textContent = 'Vídeo no disponible';
      camAviso.textContent = 'Esta trampa no ha subido ninguna imagen todavía.' + senal;
    } else {
      camEstado.className = 'cam-estado archivo';
      camEstado.textContent = hayFoto ? `Última imagen · ${hace(edadAhora())}` : 'Solicitando vídeo en directo…';
      camAviso.textContent = (modo === 'esperando'
        ? (hayFoto ? 'Solicitando vídeo en directo…' : '')
        : 'Vídeo no disponible.') + senal;
    }
    // Mientras se está intentando no hay nada que reintentar: el botón solo sale cuando ya se
    // ha dejado de intentar. Y sale sobre la última imagen guardada, para recuperar el vídeo
    // sin esperar a que falle también la foto vieja (Codex).
    btnReintentar.hidden = modo === 'cerrado' || modo === 'directo' || modo === 'esperando';
  };

  const ARRANQUE_MS = 8000;     // lo que se espera al vídeo antes de enseñar lo guardado
  const RENDIRSE_MS = 40000;    // sin un solo fotograma: se suelta todo y se para el latido
  const RENUEVA_MS = 90000;     // techo de lo viejo que puede estar un stream atascado
  const SONDEO_MS = 5000;       // no 1,2 s: eran ~50 peticiones por minuto para nada (Codex)
  const LATIDO_MS = 10000;
  const RELOJ_MS = 5000;        // solo repinta el texto; no pide nada a la red

  let usingStream = false, hayFotograma = false, rendido = true, cargando = false;
  let snapTimer = null, pingTimer = null, relojTimer = null, arranque = null, rendicion = null;
  let abiertoEn = Date.now();

  // Generación del sondeo. `clearInterval` para los ticks FUTUROS, pero no cancela la
  // petición que ya está en vuelo: si esa termina después de que arranque el vídeo, pinta la
  // foto vieja ENCIMA del directo recién recuperado. Y como `load` no se repite por
  // fotograma, esa imagen fija se quedaría ahí mientras el MJPEG sigue gastando datos por
  // detrás. Al parar se cambia de generación, y lo que llegue tarde se tira. Lo vio Codex.
  let sondeoGen = 0;
  const pararSondeo = () => {
    sondeoGen++;
    if (snapTimer) { clearInterval(snapTimer); snapTimer = null; }
  };
  const paraLatido = () => { if (pingTimer) { clearInterval(pingTimer); pingTimer = null; } };

  // `fetch` y no `new Image()`: un <img> no puede leer las cabeceras, y son ellas las que
  // dicen si esto es la cámara o el archivo. Los tres caminos del snapshot —el fotograma que
  // empuja la trampa, la foto directa por WiFi y la última guardada— salían con un 200
  // idéntico, y quien encuadraba la trampa creía ver la cámara mirando una foto de horas.
  const pideSnapshot = async () => {
    if (!document.body.contains(snap)) { pararSondeo(); return; }
    if (cargando) return;
    cargando = true;
    const gen = sondeoGen;                   // si cambia, esta respuesta ya no interesa
    try {
      const r = await fetch(`${API}/traps/${id}/snapshot?t=${Date.now()}`);
      if (r.ok) {
        const blob = await r.blob();
        // Se comprueba DESPUÉS de los `await` y ANTES de tocar las capas: entre la petición y
        // la respuesta puede haber arrancado el vídeo, y pintar esto encima sería tapar el
        // directo con una foto vieja.
        if (gen !== sondeoGen) { cargando = false; return; }
        edadAlLlegar = parseFloat(r.headers.get('X-Entomira-Edad-Seg') || '0') || 0;
        tomadaEn = Date.now();
        const url = URL.createObjectURL(blob);
        const previo = fondo.getAttribute('src') || '';
        fondo.src = url;
        if (previo.startsWith('blob:')) URL.revokeObjectURL(previo);
        capa('sondeo');
        pintaVisor(rendido ? 'archivo' : 'esperando');
      } else if (gen === sondeoGen) {
        // No hay ninguna imagen de esta trampa. No es un fallo del vídeo: es que no hay nada
        // que enseñar, y decirlo evita buscar una avería donde no la hay. Con la misma
        // guarda: un 404 que llega tarde tampoco puede tapar un vídeo que ya arrancó.
        capa('sondeo');            // y se retira el <img> del directo, que está vacío
        pintaVisor('nada');
      }
    } catch (e) { /* se reintenta en el siguiente tick */ }
    cargando = false;
  };

  const sondear = () => {
    if (snapTimer) return;
    snapTimer = setInterval(pideSnapshot, SONDEO_MS);
    pideSnapshot();
  };

  // Se rinde: ni vídeo ni promesas. Corta el MJPEG, PARA EL LATIDO --que es lo que mantiene a
  // la trampa transmitiendo-- y deja de sondear. Lo único que sigue vivo es el reloj del
  // texto, que no pide nada a la red. Desde aquí solo se vuelve con el botón.
  const rendirse = () => {
    if (hayFotograma) return;
    rendido = true;
    usingStream = false;
    // `removeAttribute` y no `src = ''`: la cadena vacía se resuelve contra la URL de la
    // página, así que el navegador se descarga el HTML y lo intenta pintar como imagen —
    // error garantizado y, en algunos navegadores, el icono de imagen rota.
    snap.removeAttribute('src');
    paraLatido();
    pararSondeo();
    pintaVisor(fondo.getAttribute('src') ? 'archivo' : 'nada');
  };

  const armaEspera = () => {
    clearTimeout(arranque); clearTimeout(rendicion);
    // Sin cortar el vídeo: si el MJPEG acaba arrancando, su fotograma manda y el sondeo se
    // para solo. Mientras tanto en pantalla hay algo real en vez de un hueco gris.
    arranque = setTimeout(() => { if (!hayFotograma) sondear(); }, ARRANQUE_MS);
    rendicion = setTimeout(rendirse, RENDIRSE_MS);
  };

  const abrirStream = () => {
    usingStream = true; rendido = false; hayFotograma = false;
    abiertoEn = Date.now();
    snap.src = `${API}/traps/${id}/stream?t=${Date.now()}`;
    armaEspera();
  };

  const cerrarStream = () => {
    rendido = true; usingStream = false; hayFotograma = false;
    pingGen++;                         // descarta cualquier latido que aún esté en vuelo
    clearTimeout(arranque); clearTimeout(rendicion);
    paraLatido();
    pararSondeo();                     // descarta también snapshots pendientes
    snap.removeAttribute('src');
    const previo = fondo.getAttribute('src') || '';
    if (previo.startsWith('blob:')) URL.revokeObjectURL(previo);
    const guardada = fotoGuardada();
    if (previo !== guardada) {
      if (guardada) fondo.src = guardada;
      else fondo.removeAttribute('src');
    }
    edadAlLlegar = ultimaImagenCapturada
      ? Math.max(0, (Date.now() - aFecha(ultimaImagenCapturada).getTime()) / 1000) : 0;
    tomadaEn = Date.now();
    capa('sondeo');
    pintaVisor('cerrado');
    btnStreaming.textContent = 'Abrir streaming';
  };

  // `load` se dispara UNA VEZ por apertura —medido arriba—, así que sirve para exactamente
  // una cosa: saber que el stream ARRANCÓ. Para eso se usa, y para nada más.
  snap.onload = () => {
    if (!usingStream || hayFotograma) return;
    hayFotograma = true; rendido = false;
    comprobandoSenal = true; falloSenal = false;
    clearTimeout(arranque); clearTimeout(rendicion);
    pararSondeo();                      // manda el vídeo: sondear sería gastar por gastar
    capa('directo');
    pintaVisor('directo');
    // Preguntar después del primer fotograma: el latido previo puede preceder al arranque.
    // Esto no prueba que el navegador siga recibiendo imágenes.
    ping();
  };
  // Error explícito del stream: no hay que esperar los 8 s para enseñar lo que haya. Y el
  // stream queda MARCADO COMO CERRADO, que es lo que faltaba: dejándolo por abierto, el
  // latido no entraba nunca en la rama que lo reabre, así que si la trampa empezaba a emitir
  // a los 15 s el visor seguía enseñando fotos fijas hasta rendirse. Hallazgo de Codex.
  snap.onerror = () => {
    if (!usingStream) return;
    usingStream = false;
    sondear();
  };

  btnReintentar.onclick = () => {
    pintaVisor('esperando');
    arrancaLatido();
    abrirStream();
    sondear();                          // que se vea algo mientras el vídeo arranca
  };
  btnStreaming.onclick = () => {
    if (modoActual !== 'cerrado') { cerrarStream(); return; }
    btnStreaming.textContent = 'Cerrar streaming';
    pintaVisor('esperando');
    arrancaLatido();
    abrirStream();
    sondear();
  };

  // Latido mientras el panel está abierto. La trampa solo transmite si alguien lo pide, y
  // quien lo pide tiene que ser EL QUE MIRA: antes lo renovaba el servidor desde el propio
  // stream, y un navegador cerrado sin avisar dejaba a la trampa transmitiendo para nadie,
  // vaciando la batería. Si esta pestaña se cierra, el latido para y la trampa también.
  const ping = async () => {
    const gen = ++pingGen;
    let r = null;
    try { r = await api(`/traps/${id}/live-ping`, { method: 'POST' }); } catch {
      if (gen !== pingGen || rendido || !document.body.contains(snap)) return;
      comprobandoSenal = false; falloSenal = true;
      if (modoActual === 'directo') pintaVisor('directo');
      return;
    }
    // Una respuesta anterior al fotograma no puede desmentir la consulta nueva.
    if (gen !== pingGen || rendido || !document.body.contains(snap)) return;
    comprobandoSenal = false; falloSenal = false;
    edadServidor = (r && typeof r.edad_frame === 'number') ? r.edad_frame : null;
    viaServidor = (r && typeof r.via === 'string') ? r.via : null;
    if (modoActual === 'directo') pintaVisor('directo');   // la etiqueta sale de este dato
    // Por la vía directa `edad_frame` no mide nada, así que no se usa para decidir: ni
    // para reenganchar ni para renovar. Con el vídeo yendo por IP, renovar sería un hipo
    // de ~3 s cada 90 s sin ningún motivo medido.
    const servidorRecibe = viaServidor !== 'directo' && edadServidor != null && edadServidor < 6;
    if (!servidorRecibe) return;
    // LA RECUPERACIÓN QUE FALTABA, y el caso de Antonio: el vídeo que nunca arrancó. Antes
    // esta función se iba por `if (!usingStream) return` y no se volvía sin recargar.
    if (!usingStream) { abrirStream(); return; }
    // Y el techo del atasco. Un stream muerto a media emisión no da error ni deja de pintar
    // el último fotograma, y desde el navegador no hay forma fiable de saberlo: la que había
    // se apoyaba en un evento que no ocurre. Renovar cada 90 s no lo detecta —esto no
    // pretende otra cosa—, pero acota cuánto puede durar. Arreglo provisional acordado con
    // Codex; el definitivo pide que el servidor diga por qué vía va cada stream.
    if (hayFotograma && Date.now() - abiertoEn > RENUEVA_MS) abrirStream();
  };
  const arrancaLatido = () => {
    if (pingTimer) return;
    pingTimer = setInterval(ping, LATIDO_MS);
    ping();
  };

  // La foto archivada no pide /stream, /snapshot ni /live-ping. Solo el botón los inicia.
  if (fotoGuardada()) fondo.src = fotoGuardada();
  capa('sondeo');
  pintaVisor('cerrado');
  relojTimer = setInterval(() => { if (modoActual !== 'directo') pintaVisor(modoActual); }, RELOJ_MS);

  onLeave(() => {                       // corta el MJPEG, el sondeo, el latido y los relojes
    // LOS MANEJADORES PRIMERO. Quitar la imagen dispara `error`, y `onerror` arranca el
    // sondeo: al salir de la vista se creaba un temporizador que ya no iba a parar nadie.
    snap.onload = null; snap.onerror = null;
    clearTimeout(arranque); clearTimeout(rendicion);
    clearInterval(relojTimer);
    paraLatido();
    pararSondeo();
    const b = fondo.getAttribute('src') || '';
    if (b.startsWith('blob:')) URL.revokeObjectURL(b);
    snap.removeAttribute('src');
    fondo.removeAttribute('src');
  });

  // Sin selector de resolución ni de compresión: la trampa dispara SIEMPRE a máxima
  // resolución. La decisión es del proyecto y tiene razón de ser — el problema declarado es
  // que la mosca sale a ~28 px, y bajar resolución solo empeora eso. Un mando que nunca
  // conviene tocar es un mando que sobra, y además una config equivocada no da error: sigue
  // haciendo fotos, peores, en silencio (fue el bug del 2026-09-11).
  // La gráfica de consumo (INA226) se quitó el 26/09 a petición de Antonio: en el banco no
  // aporta nada. LOS DATOS SE SIGUEN GUARDANDO (ventanas de 5 min en la BD, `ina_svc`) y el
  // endpoint `/traps/{id}/consumo-ina` sigue vivo, para mirarlos cuando esté en el invernadero.

  // REFRESCO SUAVE, igual que la galería. Sin esto, `vigilarCambios` llamaba a `router()`
  // para CUALQUIER cambio, y en esta vista eso es doblemente malo: `router()` empieza por
  // `_runTeardowns()`, que **corta el stream MJPEG**, y luego reconstruye con
  // `app.innerHTML = ''`. Como la trampa sube una foto cada 30 s y cada `/ingest` anuncia un
  // cambio, la página se quedaba en blanco y el vídeo se cortaba **cada 30 segundos**.
  // La imagen ya se refresca sola cada 1,2 s; el estado se actualiza sin repintar la vista.
  let refrescaEstado = () => {};
  window.__ctrlSuave = () => { refrescaEstado(); };
  onLeave(() => { if (window.__ctrlSuave) delete window.__ctrlSuave; });
  const lbl = (txt, node) => el('label', { class: 'field' }, el('span', {}, txt), node);
  const corrienteDato = el('b', {}, 'sin dato');
  const lecturaHora = el('div', { class: 'muted ctrl-reading-time' }, '');
  const left = el('div', { class: 'card ctrl-card' }, el('div', { class: 'body' },
    el('div', { class: 'row', style: 'margin-bottom:10px;gap:12px;flex-wrap:wrap' },
      el('b', {}, 'Última captura'), ...(cfg.deep_sleep ? [] : [btnStreaming])),
    // El orden importa: `fondo` va DEBAJO del directo, para que al reconectar no se vea el
    // hueco gris entre que se pide el stream y llega su primer fotograma.
    el('div', { class: 'cam-caja' }, fondo, snap, camEstado, camAviso, btnReintentar),
    el('div', { class: 'ctrl-controls' },
      // La resolución sale de la CONFIG, no de una constante. Decía «Máxima (5 MP)», que
      // era el OV5640 del ESP32; con el IMX708 el máximo son 12 MP (4608×2592) y además ya
      // no es fija: `ancho` y `alto` se eligen en el formulario de config. Un número a mano
      // aquí volvería a quedarse viejo en el próximo cambio de cámara.
      lbl('Resolución', el('div', { class: 'fijo' },
        `📷 ${cfg.ancho || 4608}×${cfg.alto || 2592}`,
        el('span', { class: 'muted' },
          ` · ${(((cfg.ancho || 4608) * (cfg.alto || 2592)) / 1e6).toFixed(1)} MP`)))),
    el('div', { style: 'margin-top:12px' },
      el('b', {}, cfg.deep_sleep ? 'Modo invernadero' : 'Modo banco'),
      el('span', { class: 'muted', style: 'margin-left:8px' }, cfg.deep_sleep
        ? `Despierta a las ${(cfg.horas_despertar || [cfg.hora_despertar || '08:00']).join(', ')} · hora de Madrid; sube una foto y corta alimentación`
        : 'Siempre encendida'),
      el('button', { class: 'btn ghost sm', style: 'margin-left:10px',
                     onclick: () => trapConfig(t, 'modo') }, 'Cambiar modo')),
    el('div', { style: 'display:flex;gap:10px;align-items:flex-start;margin-top:12px;flex-wrap:wrap' },
      botonTomarFoto(id),
      ...(cfg.deep_sleep ? [] : [
        ...(t.device_id?.startsWith('zero-') ? [] : [botonReposo(id)]),
        botonReiniciarPlaca(id), botonApagarPlaca(id, t.device_id?.startsWith('zero-'))])),
    ...(cfg.deep_sleep ? [] : [cajaSensores(id, (ma, d) => { if (d) enDirecto(d); })])));

  // La Zero tiene una estimación orientativa por tensión, separada del SOC medido.
  const cargaLbl = cargaTexto(t);
  const voltDato = el('b', {}, 'sin dato');
  const batDato = el('b', {
    title: 'Estimación por tensión de una celda Li-ion; varía con la carga, temperatura y edad.'
  }, bateriaTexto(t));
  const cargaDato = el('div', {
    style: `margin-top:8px;font-size:13px;font-weight:700;color:${cargaLbl.color}`
  }, cargaLbl.texto);
  const stat = (k, v, cls = '') => el('div', { class: 'ctrl-stat' + (cls ? ` ${cls}` : '') },
    el('span', { class: 'muted' }, k), el('b', {}, v));
  // EN LÍNEA SE REFRESCA EN CALIENTE. Se pintaba una vez al entrar, y una trampa que se callaba
  // seguía «En línea» hasta recargar. El servidor ya avisa cuando una trampa cruza el margen
  // (services/en_linea.py); aquí se vuelve a pedir en el refresco suave.
  const estadoStat = stat('Estado', textoEstado(t));
  const cabeceraEstado = el('span', { class: 'tag gray', style: 'font-size:12px' });
  const pintaEstado = tr => {
    const s = textoEstado(tr);
    estadoStat.querySelector('b').textContent = s;
    estadoStat.classList.toggle('ctrl-error', s === 'No contactó tras despertar');
    cabeceraEstado.textContent = s;
    cabeceraEstado.className = s === 'No contactó tras despertar' ? 'tag ctrl-alert' : s === 'En línea' ? 'tag' : 'tag gray';
  };
  pintaEstado(t);
  const horaMadrid = s => s ? aFecha(s).toLocaleTimeString('es-ES',
    { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Madrid' }) : '—';
  const contactoStat = stat('Último contacto', t.ultimo_visto
    ? `${horaMadrid(t.ultimo_visto)} (${haceDesde(t.ultimo_visto)})` : 'sin dato');
  const redStat = stat('Red', 'sin dato');
  const fotoStat = el('div', { class: 'ctrl-photo' });
  const pintaFoto = tr => {
    fotoStat.replaceChildren(tr.ultima_imagen ?
      el('a', { href: '#/galeria', class: 'ctrl-photo-link' },
        el('img', { src: `${API}/images/${tr.ultima_imagen.id}/thumb`, alt: 'Última foto', loading: 'lazy' }),
        el('span', {}, `Última foto · ${horaMadrid(tr.ultima_imagen.capturada)} · Ver galería`)) :
      el('span', { class: 'muted' }, 'Última foto · sin dato'));
  };
  pintaFoto(t);
  // LATENCIA. El histórico guarda como mucho una lectura por minuto y llega con cada informe;
  // en banco, la caja de sensores pide lecturas en directo cada pocos segundos (también por
  // 4G desde el 15/10). Mientras lleguen, mandan ellas y el histórico no las pisa.
  let directoHasta = 0;
  const num = (n, d) => `${Number(n) < 0 ? '−' : ''}${Math.abs(Number(n)).toLocaleString('es-ES', {minimumFractionDigits:d, maximumFractionDigits:d})}`;
  const enDirecto = d => {
    directoHasta = Date.now() + 30000;
    voltDato.textContent = d.ina219_v == null ? 'sin dato' : `${num(d.ina219_v, 3)} V`;
    corrienteDato.textContent = d.ina219_ma == null ? 'sin dato' : `${num(d.ina219_ma, 0)} mA`;
    lecturaHora.textContent = d.edad_hw_s == null ? '' : `en directo · hace ${Math.round(d.edad_hw_s)} s`;
  };
  const pintaHardware = async () => {
    if (Date.now() < directoHasta) return;
    try {
      const datos = await api(`/traps/${id}/hardware?horas=168`);
      const lectura = datos.lecturas?.at(-1);
      if (!lectura) return;
      if (Date.now() < directoHasta) return;
      voltDato.textContent = lectura.tension_v == null ? 'sin dato' : `${num(lectura.tension_v, 3)} V`;
      corrienteDato.textContent = lectura.corriente_ma == null ? 'sin dato' : `${num(lectura.corriente_ma, 0)} mA`;
      lecturaHora.textContent = `medida a las ${horaMadrid(lectura.instante)}`;
    } catch { /* conserva la última lectura visible */ }
  };
  pintaHardware();
  const pintaRed = tr => {
    contactoStat.querySelector('b').textContent = tr.ultimo_visto
      ? `${horaMadrid(tr.ultimo_visto)} (${haceDesde(tr.ultimo_visto)})` : 'sin dato';
    redStat.querySelector('b').textContent = `${tr.red === 'lte' ? '4G' : tr.red === 'wifi' ? 'WiFi' : tr.red === 'usb' ? 'USB' : 'sin dato'}${tr.rssi != null ? ` · ${tr.rssi} dBm` : ''}`;
  };
  pintaRed(t);
  refrescaEstado = async () => {
    let t2;
    try { t2 = await api(`/traps/${id}`); } catch { return; }
    if ((t2.ultima_imagen?.id ?? null) !== ultimaImagenId) {
      ultimaImagenId = t2.ultima_imagen?.id ?? null;
      ultimaImagenCapturada = t2.ultima_imagen?.capturada ?? null;
      if (modoActual === 'cerrado') cerrarStream();
    }
    pintaEstado(t2);
    pintaRed(t2);
    pintaFoto(t2);
    pintaHardware();
    pintaLogs();
    batDato.textContent = bateriaTexto(t2);
    const cargaNueva = cargaTexto(t2);
    cargaDato.textContent = cargaNueva.texto;
    cargaDato.style.color = cargaNueva.color;
  };
  const logFecha = nombre => {
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})_/.exec(nombre || '');
    return m ? fmtDate(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`) : '';
  };
  const verLog = el('button', { class: 'btn ghost sm', disabled: 'disabled', onclick: async () => {
    try { await abrirLog(); } catch (e) { toast(e.message, true); }
  } }, 'Ver último log');
  const logAviso = el('span', { class: 'muted' },
    cfg.deep_sleep ? 'Llegará en su próximo contacto' : 'En banco llega en unos segundos y se abre solo');
  const abrirLog = async () => {
    const res = await fetch(`${API}/traps/${id}/logs/ultimo`);
    if (!res.ok) throw new Error('No se pudo abrir el log');
    modal({ title: 'Último log', body: el('pre', { class: 'ctrl-log' }, await res.text()) });
  };
  // En banco la petición suelta la espera de la telemetría y el log sube en segundos: se
  // espera a que aparezca uno NUEVO y se abre. Dormida, llegará al despertar.
  let vistaViva = true;
  onLeave(() => { vistaViva = false; });     // el sondeo para al salir (revisión de Codex)
  const pedirLog = el('button', { class: 'btn ghost sm', onclick: async () => {
    pedirLog.disabled = true;
    try {
      const antes = (await api(`/traps/${id}/logs`).catch(() => []))[0]?.nombre ?? null;
      await api(`/traps/${id}/log/pedir`, { method: 'POST' });
      const fin = Date.now() + 90000;
      logAviso.textContent = 'Log pedido, esperando a la trampa…';
      while (vistaViva && Date.now() < fin) {
        await new Promise(r => setTimeout(r, 2000));
        if (!vistaViva) return;
        const ahora = (await api(`/traps/${id}/logs`).catch(() => []))[0]?.nombre ?? null;
        if (ahora && ahora !== antes) {
          logAviso.textContent = '';
          pintaLogs();
          await abrirLog();
          return;
        }
      }
      logAviso.textContent = 'Aún no ha llegado: lo subirá en su próximo contacto';
    } catch (e) { toast(e.message, true); }
    finally { pedirLog.disabled = false; }
  } }, 'Pedir log');
  const pintaLogs = () => api(`/traps/${id}/logs`).then(logs => {
    if (logs.length) { verLog.disabled = false; verLog.textContent = `Ver último log · ${logFecha(logs[0].nombre)}`; }
  }).catch(() => {});
  pintaLogs();
  const estadoTimer = setInterval(() => refrescaEstado(), 60000);
  onLeave(() => clearInterval(estadoTimer));
  const right = el('div', { class: 'card ctrl-card' }, el('div', { class: 'body' },
    // Con medidor: el bloque de siempre. Sin medidor: una linea que DICE por que no hay dato,
    // en vez de un hueco que parece un fallo de la web. El dia que se compre el medidor y
    // empiece a llegar `bateria`, el bloque vuelve solo: no hay nada que reprogramar.
    // El panel se titula por lo que CONTIENE. Se llamaba «BATERÍA» y nueve de sus diez
    // filas hablan de otra cosa: conexión, señal, firmware, fotos, conteo. Hallazgo de Codex
    // mirando la captura, 2026-09-19.
    el('div', { class: 'row', style: 'margin-bottom:10px' },
      el('span', { class: 'muted', style: 'font-weight:700;letter-spacing:.04em' },
         'ESTADO DE LA TRAMPA')),
    el('div', { class: 'ctrl-batbox' },
      el('div', { class: 'row' }, el('span', { class: 'muted' }, 'Batería · INA219'),
        voltDato),
      el('div', { class: 'row' }, el('span', { class: 'muted' }, 'Corriente · INA219'),
        corrienteDato),
      lecturaHora,
      el('div', { class: 'row' }, el('span', { class: 'muted' }, 'Porcentaje'),
        batDato),
      ...(cfg.deep_sleep ? [] : [cargaDato])),
    el('hr', { class: 'ctrl-hr' }),
    estadoStat,
    contactoStat,
    redStat,
    stat('Modo', cfg.deep_sleep ? 'Invernadero' : 'Banco'),
    stat('Horario', (cfg.horas_despertar?.length ? cfg.horas_despertar : [cfg.hora_despertar || '08:00']).join(', ')),
    fotoStat,
    // El ritmo de carga sale del MISMO medidor que la bateria: sin el, esta fila era un «—»
    // permanente que solo ocupaba sitio. Vuelve sola cuando llegue el dato.
    // Qué firmware corre la placa. Sin esto no se sabe si una actualización OTA ha entrado.
    stat('Software', t.fw_version || '—'),
    el('div', { class: 'ctrl-log-actions' }, verLog, pedirLog, logAviso),
    // AQUI NO VAN LOS DATOS DEL SERVIDOR. Estaban «Disco libre», «Disco usado»,
    // «Imágenes», «Base de datos» y «Temp. Pi», que salen de `/system/health` y hablan de la
    // Raspberry del SERVIDOR, no de esta trampa. Se miraba la ficha de una trampa y te
    // contaban el disco de otra máquina: cinco de las doce filas no iban de lo que estabas
    // mirando. Viven en «Trampas», que es la vista de sistema. Mentoría de diseño con Codex,
    // 2026-09-19: «una vista, una tarea principal».
    stat('Último conteo', t.ultimo_conteo != null ? `🐛 ${t.ultimo_conteo}` : '—'),
    // TARJETA. Una trampa adhesiva acumula: los insectos siguen pegados de una foto a la
    // siguiente. Sin la fecha en que se puso el cartón, el conteo no es comparable entre
    // trampas ni entre semanas, y al cambiarlo la caída a cero parecería una plaga que se va.
    ...(tarj ? [
      el('hr', { class: 'ctrl-hr' }),
      el('div', { class: 'row' }, el('span', { class: 'muted', style: 'font-weight:700' }, 'TARJETA'),
        el('span', { class: 'muted', style: 'font-size:12px' }, `puesta el ${fmtDate(tarj.colocada)}`)),
      stat('Días expuesta', `${Math.floor(tarj.dias_expuesta)} días`),
      // «Insectos/día» y no «Capturas/día». `capturas_por_dia` divide el conteo de INSECTOS
      // entre los días, pero en el resto del sistema una «captura» es una FOTO --el cliente
      // escribe «captura de 3546 KB»--. La misma palabra para dos cosas distintas, y aquí
      // justo al lado de «Fotos de esta tarjeta». Lo vio Codex en la captura.
      stat('Insectos/día', tarj.capturas_por_dia != null ? `🐛 ${Number(tarj.capturas_por_dia).toLocaleString('es-ES')}`
        // Dice CUANDO habra dato, en vez de un 'aun no' que no compromete a nada.
        : 'disponible tras 12 h'),
      stat('Fotos de esta tarjeta', String(tarj.num_imagenes)),
      el('button', { class: 'btn ghost sm', style: 'margin-top:10px;width:100%',
        title: 'Cierra el periodo actual y empieza a contar desde cero',
        onclick: async () => {
          if (!confirm(`¿Has cambiado la tarjeta de «${t.alias}»?  Se cierra el periodo `
            + `actual (${tarj.dias_expuesta} d, ${tarj.conteo ?? '—'} insectos) y el conteo `
            + 'vuelve a empezar.')) return;
          const nota = prompt('Nota del cambio (opcional)', '') ?? '';
          try {
            const r = await api(`/traps/${id}/tarjeta`, { method: 'POST', body: JSON.stringify({ nota }) });
            toast(`Tarjeta nueva. La anterior estuvo ${r.anterior.dias_expuesta} d puesta.`);
            invalidateApiCache(); router();
          } catch (e) { toast(e.message, true); }
        } }, '🔖 Tarjeta nueva'),
    ] : [])));

  app.append(
    el('div', { class: 'view-head' },
      el('button', { class: 'btn ghost sm', onclick: () => { location.hash = '#/trampas'; } }, '← Trampas'),
      el('h1', { style: 'font-size:22px' }, t.alias),
      cabeceraEstado,
      el('span', { class: 'muted', style: 'font-size:13px' },
        (t.invernadero_nombre || 'Sin invernadero') + (t.sector ? ` · ${t.sector}` : '') + (t.linea ? ` · L${t.linea}` : '')),
    ),
    el('div', { class: 'ctrl-layout' + (cfg.deep_sleep ? ' ctrl-sleep' : '') }, left, right));
}

/* ---------- teardown: cortar timers/streams de la vista anterior al navegar ---------- */
let _teardowns = [];
function onLeave(fn) { _teardowns.push(fn); }
function _runTeardowns() { const fns = _teardowns; _teardowns = []; fns.forEach(f => { try { f(); } catch {} }); }

/* ---------- router ---------- */
const routes = {
  galeria: viewGaleria, trampas: viewTrampas, invernaderos: viewInvernaderos,
  mapa: viewMapa, plagas: viewPlagas, usuarios: viewUsuarios, color: viewColor,
  control: viewControl, sensores: viewSensores, demo: viewDemo,
};
async function router() {
  _runTeardowns();   // corta timers/streams de la vista anterior (sin arrastre)
  const parts = (location.hash || '#/galeria').replace('#/', '').split('/');
  const hash = parts[0];
  let name = routes[hash] ? hash : 'galeria';
  // El agricultor no accede a rutas de admin aunque teclee el hash.
  if (ME && ME.rol !== 'admin' && ADMIN_ROUTES.includes(name)) name = 'galeria';
  $$('.mainnav a').forEach(a => {
    a.classList.toggle('active', a.dataset.route === name);
    if (a.dataset.route === name) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  const app = $('#app');
  try { await routes[name](app, parts[1]); }
  catch (e) { app.innerHTML = ''; app.append(emptyState('⚠️', 'Error al cargar', e.message)); }
}

let ME = null;
const ADMIN_ROUTES = ['trampas', 'invernaderos', 'plagas', 'usuarios', 'color', 'control', 'sensores', 'demo'];

async function loadMe() {
  // Con un tope: una petición colgada no puede dejar la página oculta (auth-pendiente);
  // sin respuesta en 8 s se trata como sin sesión y sale el login.
  const tope = new Promise((_, no) => setTimeout(() => no(new Error('sin respuesta')), 8000));
  try { ME = await Promise.race([api('/auth/me'), tope]); } catch (e) { ME = null; }
  return !!(ME && ME.authenticated);
}
function applyRole() {
  const admin = ME.rol === 'admin';
  const pu = $('#pill-user');
  pu.textContent = '👤 ' + (ME.nombre || ME.username);
  pu.hidden = false;
  $$('.admin-only').forEach(n => n.hidden = !admin);
  const g = $('#nav-galeria');
  if (g) g.textContent = admin ? 'Galería' : 'Mis fotos';
}
function renderLogin() {
  document.body.classList.add('logged-out');
  const app = $('#app'); app.innerHTML = '';
  const f = {};
  const err = el('div', { class: 'login-err', hidden: true });
  const submit = async () => {
    err.hidden = true;
    if (!f.u || !f.p) { err.textContent = 'Escribe usuario y contraseña.'; err.hidden = false; return; }
    try {
      const r = await fetch(API + '/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: f.u, password: f.p }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        err.textContent = j.detail || 'Usuario o contraseña incorrectos.'; err.hidden = false; return;
      }
      location.hash = ''; location.reload();
    } catch (e) { err.textContent = 'No se pudo conectar con el servidor.'; err.hidden = false; }
  };
  const onEnter = e => { if (e.key === 'Enter') submit(); };
  const card = el('div', { class: 'login-card' },
    el('img', { src: '/assets/logo-icon.svg', class: 'login-logo', alt: 'ENTOMIRA' }),
    el('div', { class: 'login-brand' }, 'ENTOMIRA'),
    el('h1', { class: 'login-title' }, 'Inicio de sesión'),
    el('input', { class: 'login-input', placeholder: 'Usuario', autofocus: true,
      oninput: e => f.u = e.target.value.trim(), onkeydown: onEnter }),
    el('input', { class: 'login-input', type: 'password', placeholder: 'Contraseña',
      oninput: e => f.p = e.target.value, onkeydown: onEnter }),
    err,
    el('button', { class: 'btn login-btn', onclick: submit }, 'Iniciar sesión'),
    el('a', {
      class: 'login-forgot', href: '#', onclick: e => {
        e.preventDefault();
        toast('¿Eres agricultor? Pide al administrador que te restablezca la contraseña (la gestiona en la pestaña Usuarios).');
      }
    }, 'Si has olvidado tu contraseña, recupérala'));
  app.append(el('div', { class: 'login-wrap' }, card));
}
async function boot() {
  // Vista de cliente (solo lectura): /cliente/<token>
  const m = location.pathname.match(/^\/cliente\/(.+)$/);
  if (m) { document.body.classList.add('client-mode'); document.body.classList.remove('auth-pendiente'); viewCliente($('#app'), m[1]); return; }
  const authed = await loadMe();
  // Ya se sabe si hay sesión: se enseña lo que toca y nunca antes (ver index.html).
  try { localStorage.setItem('entomira_sesion', authed ? '1' : '0'); } catch (e) { /* sin almacenamiento */ }
  document.documentElement.classList.toggle('pre-login', !authed);
  if (!authed) { renderLogin(); document.body.classList.remove('auth-pendiente'); return; }
  document.body.classList.remove('logged-out', 'auth-pendiente');
  applyRole();
  router(); refreshStatus(); setInterval(refreshStatus, 15000);
  vigilarCambios();
}

/* ---------- la web se entera sola: sondeo del contador de cambios ----------
   El servidor sube un contador cuando entra una foto, se procesa una inferencia, cambia la
   config o llega batería nueva (ver services/cambios.py). Aquí se sondea cada 4 s y SOLO se
   repinta si ha cambiado, así no hay que recargar la página para ver lo nuevo.
   Se sondea un contador en vez de mantener un WebSocket porque la trampa y el servidor van
   por redes que se caen: una petición corta se recupera sola, un socket muerto no. */
let _ultimaVersion = null;
let _ultimosTipos = {};

/* QUÉ LE IMPORTA A CADA VISTA.
   Antes se repintaba la vista ENTERA ante cualquier cambio, y `router()` no es inocente:
   empieza por `_runTeardowns()` (corta los streams) y reconstruye con `app.innerHTML = ''`.
   Como la trampa sube una foto cada 30 s y cada `/ingest` anuncia un cambio de tipo
   `imagen`, TODAS las pestañas se quedaban en blanco un instante cada 30 segundos —
   incluida «Color», que no enseña ni una foto y no tenía nada que repintar.
   Una vista que no está aquí se repinta como siempre: quien añada una nueva no se queda
   sin refresco por olvidarse de esta tabla. */
const TIPOS_POR_VISTA = {
  galeria: ['imagen', 'inferencia', 'tarjeta'],
  control: ['imagen', 'bateria', 'config'],
  sensores: [],               // se refresca sola cada 2 s, en su sitio
  trampas: ['imagen', 'bateria', 'config'],
  color: ['config'],          // ganancia por canal: solo la toca la config
  invernaderos: ['config'],
  plagas: ['inferencia'],
  usuarios: [],               // no depende de nada que anuncie la trampa
  // La demo sondea ella sola su resultado; repintarla por su propia foto nueva o su inferencia
  // la reiniciaba a mitad de análisis.
  demo: [],
};

function vigilarCambios() {
  setInterval(async () => {
    if (document.hidden) return;                 // pestaña en segundo plano: no gastar
    let c;
    try { c = await api('/cambios'); } catch { return; }
    if (_ultimaVersion === null) { _ultimaVersion = c.v; _ultimosTipos = c.tipos || {}; return; }
    if (c.v === _ultimaVersion) return;
    _ultimaVersion = c.v;

    // Qué tipos han subido su contador desde el último sondeo. Se guarda SIEMPRE, incluso
    // cuando no se repinta: si no, la próxima vez se compararía contra un estado viejo y
    // el cambio se anunciaría dos veces.
    const tipos = c.tipos || {};
    const subieron = Object.keys(tipos).filter(k => (tipos[k] || 0) > (_ultimosTipos[k] || 0));
    _ultimosTipos = tipos;

    const vista = ((location.hash || '#/galeria').replace('#/', '').split('/')[0]) || 'galeria';
    const importan = TIPOS_POR_VISTA[vista];
    if (importan && !subieron.some(x => importan.includes(x))) return;   // no va con esta vista

    // No repintar por encima del usuario: si está escribiendo, tiene un menú abierto o hay
    // texto seleccionado, se deja para el siguiente sondeo. Repintar en medio le borraría
    // lo que estuviera haciendo, que es peor que ver un dato con cuatro segundos de retraso.
    const escribiendo = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName);
    const menuAbierto = !!document.querySelector('.modal');
    const seleccionando = (window.getSelection?.().toString() || '').length > 0;
    if (escribiendo || menuAbierto || seleccionando) return;

    invalidateApiCache();
    // Las vistas con medios en vivo se actualizan EN CALIENTE, sin fogonazo. `router()`
    // reconstruye la vista entera y además corta los streams (`_runTeardowns`), así que
    // usarlo aquí es lo que hacía parpadear la web y cortar el vídeo cada 30 s, que es lo
    // que tarda la trampa en subir una foto.
    const ruta = location.hash || '#/galeria';
    if (ruta.startsWith('#/galeria') && window.__galSuave) window.__galSuave();
    else if (ruta.startsWith('#/control') && window.__ctrlSuave) window.__ctrlSuave();
    else if (ruta.startsWith('#/trampas') && window.__trampasSuave) window.__trampasSuave();
    else router();
  }, 4000);
}
window.addEventListener('hashchange', () => {
  if (!document.body.classList.contains('client-mode') && ME && ME.authenticated) router();
});
window.addEventListener('DOMContentLoaded', boot);
