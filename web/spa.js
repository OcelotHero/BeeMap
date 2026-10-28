/* BeeMap Heilbronn: one public map. No logins; everything is read-only except
   anonymous "I planted something" (and hornet reports, if the city turns them on). */
'use strict';

const AREA = { center: [49.1427, 9.2109], zoom: 13 };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const ML = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
const DIRS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
const PLACES = [['Innenstadt', 49.1427, 9.219], ['Böckingen', 49.133, 9.183], ['Sontheim', 49.115, 9.208], ['Frankenbach', 49.15, 9.168],
  ['Neckargartach', 49.163, 9.193], ['Heilbronn-Ost', 49.146, 9.243], ['Südstadt', 49.13, 9.223], ['Klingenberg', 49.104, 9.174],
  ['Wartberg', 49.158, 9.242], ['Wertwiesenpark', 49.128, 9.213], ['Pfühlpark', 49.149, 9.232], ['Biberach', 49.105, 9.15],
  ['Kirchhausen', 49.148, 9.125], ['Horkheim', 49.1, 9.205]];
const LC_COLORS = { orchard: '#8DBF6A', meadow: '#B9D98C', vineyard: '#C6A6D0', forest: '#4F8A5B', arable: '#E9DFB4', rapeseed: '#F2D33A',
  urban: '#CFCAC4', gardens: '#A8D39B', park: '#98C98A', grass: '#D8E7C4', hedge: '#6E9E5E', flower_strip: '#EC8FBF', water: '#9CC3E6',
  lime_avenue: '#7DB36A', tree_row: '#5E8F4E' };
const LC_NAMES = { orchard: 'Orchards', meadow: 'Meadows', vineyard: 'Vineyards', forest: 'Forest', arable: 'Fields', rapeseed: 'Rapeseed',
  urban: 'Built-up', gardens: 'Gardens & allotments', park: 'Parks', grass: 'Lawns & verges', hedge: 'Scrub & hedges',
  flower_strip: 'Flower beds & strips', water: 'Water', lime_avenue: 'Lime avenues', tree_row: 'Tree rows' };

/* ================================================================ utils */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const h = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
const dirWord = (d) => DIRS[Math.round(((d % 360) + 360) % 360 / 45) % 8];
const fmtDist = (m) => (m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m / 10) * 10} m`);
const fmtArea = (m2) => (m2 >= 10000 ? `${(m2 / 10000).toFixed(1)} ha` : `${Math.round(m2)} m²`);
const now = () => Date.now() / 1000;
function fmtTime(ts) {
  const d = new Date(ts * 1000), n = new Date();
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === n.toDateString()) return `Today ${hm}`;
  const y = new Date(n); y.setDate(n.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return `Yesterday ${hm}`;
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
}
const ago = (ts) => { if (!ts) return 'never'; const s = now() - ts; return s < 90 ? 'just now' : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`; };
function toast(msg, err = false) { const t = $('#toast'); t.textContent = msg; t.className = 'show' + (err ? ' err' : ''); clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = ''), 3200); }
const M = 111320;
function dest(lat, lon, b, d) { const r = b * Math.PI / 180; return [lat + Math.cos(r) * d / M, lon + Math.sin(r) * d / (M * Math.cos(lat * Math.PI / 180))]; }
function distBearing(a, b) { const kx = M * Math.cos(a[0] * Math.PI / 180), x = (b[1] - a[1]) * kx, y = (b[0] - a[0]) * M; return { d: Math.hypot(x, y), b: (Math.atan2(x, y) * 180 / Math.PI + 360) % 360 }; }
function ellipsePts(n) {
  const o = n.orientation_deg * Math.PI / 180, ux = Math.sin(o), uy = Math.cos(o), vx = Math.cos(o), vy = -Math.sin(o), kx = M * Math.cos(n.lat * Math.PI / 180), pts = [];
  for (let i = 0; i <= 48; i++) { const t = i / 48 * 2 * Math.PI, A = n.semi_major_m * Math.cos(t), B = n.semi_minor_m * Math.sin(t); pts.push([n.lat + (A * uy + B * vy) / M, n.lon + (A * ux + B * vx) / kx]); }
  return pts;
}
const polyLatLngs = (polys) => polys.map((p) => [p.outer, ...(p.inner || [])]);
async function api(path, opts = {}) {
  let body = opts.body; const headers = {};
  if (body && !(body instanceof FormData)) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
  let r;
  try { r = await fetch(path, { ...opts, headers, body }); } catch { throw new Error('Can’t reach the BeeMap server.'); }
  if (!r.ok) { let m = `Server said ${r.status}`; try { const j = await r.json(); m = typeof j.detail === 'string' ? j.detail : m; } catch { } throw new Error(m); }
  return r.json();
}

/* ================================================================ state */
const NR_LOC = { tree: 'In a tree', building: 'On a building or under a roof', hedge: 'In a hedge or bush', ground: 'In the ground or a wall', other: 'Somewhere else' };
const NR_HEIGHT = { reach: 'within reach (under 2 m)', '2-5m': '2–5 m up', 'above-5m': 'higher than 5 m', unknown: 'height unknown' };
const NR_SIZE = { fist: 'smaller than a fist', football: 'about football-sized', bigger: 'bigger than a football', unknown: 'size unknown' };
const NR_STATUS = { pending: ['Waiting for a check', 'amber', '#C98A0B', '?'], asian_hornet: ['Asian hornet nest', 'red', '#C2481B', '!'],
  other_species: ['Not an Asian hornet', 'green', '#2F6B45', '✓'], not_found: ['Not found', 'grey', '#7C877F', '–'], removed: ['Removed', 'green', '#2F6B45', '✓'] };
const D = { reports: [], summary: {}, stations: [], detections: [], nests: [], lines: [], eco: null, boxes: [], planning: null, landcover: null, plants: null, live: false };
const ST = {
  tab: 'overview', at: null,             // at = clicked place [lat, lon]
  layers: {},                            // key -> { on, f: {filters} }
};

/* ================================================================ layers */
const thisMonth = () => Math.max(3, Math.min(10, new Date().getMonth() + 1));
const LAYERS = [
  { key: 'hornets', group: 'Asian hornets', label: 'Hornet alerts', on: true, sw: '#C2481B', round: true,
    filters: [{ key: 'days', type: 'select', label: 'Period', options: [['1', '24 hours'], ['7', '7 days'], ['14', '14 days'], ['30', '30 days']], value: '14' },
      { key: 'confirmed', type: 'check', label: 'Confirmed only', value: false }] },
  { key: 'lines', group: 'Asian hornets', label: 'Flight lines', on: true, sw: 'repeating-linear-gradient(90deg,#C2481B 0 4px,transparent 4px 7px)',
    filters: [{ key: 'corridors', type: 'check', label: 'Include direction-only corridors', value: true }] },
  { key: 'nests', group: 'Asian hornets', label: 'Nest areas', on: true, sw: 'transparent;border:2px dashed #C2481B;border-radius:50%',
    filters: [{ key: 'status', type: 'multi', options: [['suspected', 'Suspected'], ['assigned', 'Team on it'], ['removed', 'Removed']], value: ['suspected', 'assigned'] }] },
  { key: 'reports', group: 'Asian hornets', label: 'Reported nests', on: true, sw: '#C98A0B;border-radius:50%;border:2px solid #fff',
    filters: [{ key: 'status', type: 'multi', options: [['pending', 'Waiting for a check'], ['asian_hornet', 'Asian hornet nest'], ['other_species', 'Other species'], ['not_found', 'Not found'], ['removed', 'Removed']], value: ['pending', 'asian_hornet'] }] },
  { key: 'hives', group: 'Bees', label: 'Hives', on: true, sw: '#18211A',
    filters: [{ key: 'types', type: 'multi', options: [['smart', 'Smart hives'], ['planned', 'Planned'], ['registered', 'Registered apiaries']], value: ['smart', 'planned', 'registered'] },
      { key: 'radius', type: 'check', label: '800 m forage radius', value: false }] },
  { key: 'wild', group: 'Bees', label: 'Wild-bee zones', on: true, sw: 'rgba(47,107,69,.45);border:1.5px solid #2F6B45' },
  { key: 'spots', group: 'Bees', label: 'Good hive spots', on: false, sw: 'linear-gradient(90deg,#2F6B45,#C98A0B,#C2481B)' },
  { key: 'sites', group: 'Bees', label: 'Suggested smart-hive sites', on: false, sw: '#F2C230;border-radius:50%;border:2px solid #18211A' },
  { key: 'gaps', group: 'Flowers', label: 'Flower gaps', on: false, sw: 'linear-gradient(90deg,#C2481B,#C98A0B,rgba(47,107,69,.5))',
    filters: [{ key: 'month', type: 'select', label: 'Month', options: MONTHS.slice(2, 10).map((m, i) => [String(i + 3), m]), value: String(thisMonth()) }] },
  { key: 'landcover', group: 'Flowers', label: 'Land cover', on: false, sw: 'linear-gradient(90deg,#8DBF6A,#C6A6D0,#4F8A5B)',
    filters: [{ key: 'kinds', type: 'multi', options: Object.entries(LC_NAMES), value: Object.keys(LC_NAMES), compact: true }] },
  { key: 'plantings', group: 'Flowers', label: 'Plantings', on: true, sw: '#2F6B45;border-radius:50%;border:1.5px solid #fff' },
  { key: 'boxes', group: 'Sensors', label: 'SensoBoxes', on: true, sw: '#2F6B45;border-radius:3px',
    filters: [{ key: 'kind', type: 'select', label: 'Show', options: [['all', 'All boxes'], ['pollen', 'With pollen sensor'], ['weather', 'Weather only']], value: 'all' },
      { key: 'range', type: 'check', label: 'Where they correct the map', value: false }] },
];
const LBY = Object.fromEntries(LAYERS.map((l) => [l.key, l]));
LAYERS.forEach((l) => (ST.layers[l.key] = { on: l.on, f: Object.fromEntries((l.filters || []).map((f) => [f.key, Array.isArray(f.value) ? [...f.value] : f.value])) }));
const F = (k) => ST.layers[k].f;
const on = (k) => ST.layers[k].on;

/* ================================================================ map */
const map = L.map('map', { zoomControl: false, attributionControl: true, preferCanvas: false }).setView(AREA.center, AREA.zoom);
L.control.zoom({ position: 'topright' }).addTo(map);
L.tileLayer('https://{s}.basemap.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  maxZoom: 19, subdomains: 'abcd', attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
}).addTo(map);
[['lc', 350], ['grid', 360], ['zones', 380], ['nests', 400], ['lines', 410], ['pts', 450]].forEach(([n, z]) => { map.createPane(n).style.zIndex = z; });
const canvasLC = L.canvas({ pane: 'lc', padding: 0.3 });
const GROUPS = Object.fromEntries(LAYERS.map((l) => [l.key, L.layerGroup().addTo(map)]));
const gridCache = {};
let placeMarker = null;

function hexIcon(fill, ring, size = 20, badge = '') {
  return L.divIcon({ className: '', iconSize: [size, size + 2], iconAnchor: [size / 2, size / 2 + 1],
    html: `<div style="position:relative"><svg width="${size}" height="${size + 2}" viewBox="0 0 12 14"><path d="M6 0.7 L11.3 3.7 V10.3 L6 13.3 L0.7 10.3 V3.7 Z" fill="${fill}" stroke="${ring}" stroke-width="1.2" ${fill === '#fff' ? 'stroke-dasharray="1.6 1.2"' : ''}/></svg>${badge ? `<span style="position:absolute;top:-7px;right:-9px;font:600 10px var(--mono);background:#C2481B;color:#fff;border-radius:999px;padding:0 5px;line-height:15px">${badge}</span>` : ''}</div>` });
}
function boxIcon(pollen) {
  return L.divIcon({ className: '', iconSize: [22, 22], iconAnchor: [11, 11],
    html: `<div style="width:22px;height:22px;border-radius:6px;background:${pollen ? '#2F6B45' : '#55605A'};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.3);display:grid;place-items:center;color:#fff;font:700 10px var(--sans)">${pollen ? 'P' : 'W'}</div>` });
}
const hasPollen = (b) => b.monthly && b.monthly.some((v) => v != null);
function filteredDetections() {
  const f = F('hornets'), since = now() - (+f.days) * 86400;
  return D.detections.filter((d) => d.ts >= since && d.status !== 'rejected' && (!f.confirmed || d.status === 'confirmed')
    && d.lat != null);
}
function nestPlace(n) {
  let best = null;
  for (const s of D.stations) { if (s.lat == null || s.planned) continue; const db = distBearing([s.lat, s.lon], [n.lat, n.lon]); if (!best || db.d < best.d) best = { ...db, s }; }
  return n.place || (best ? `${fmtDist(best.d)} ${dirWord(best.b)} of ${best.s.name}` : `${n.lat.toFixed(4)}, ${n.lon.toFixed(4)}`);
}
const nestBadge = (n) => n.status === 'removed' ? '<span class="badge green">✓ Removed</span>' : n.located ? '<span class="badge red">📍 Nest found</span>'
  : n.status === 'assigned' ? '<span class="badge amber">▲ Team on it</span>' : '<span class="badge red">● Suspected</span>';
const LEVEL_WORD = { strong: 'fits well', fits: 'fits', near: 'is close to' };
function matchLine(m) {
  if (!m) return '';
  const hives = m.hives?.length ? m.hives.join(', ') : 'the smart hives';
  const txt = m.kind === 'nest'
    ? (m.level === 'near' ? `Close to the nest area where flight lines from ${esc(hives)} cross.` : `Fits the nest area where flight lines from ${esc(hives)} cross (${fmtDist(m.dist_m)} from its centre).`)
    : (m.level === 'near' ? `Close to the flight line of hornets from ${esc(hives)}.` : `Lies on the flight line of hornets from ${esc(hives)}, ${fmtDist(m.dist_m)} out.`);
  return `<span style="font-size:12px;color:#18211A">🐝 ${txt}</span>${m.note ? `<span class="muted" style="font-size:11px">Note: ${esc(m.note)}.</span>` : ''}`;
}

function verifyLine(r) {
  if (r.status === 'pending') return r.check?.reason ? `<span class="muted" style="font-size:12px">${esc(r.check.reason)}</span>` : '';
  if (r.verified_by === 'photo+flight lines') return `<span style="font-size:12px;font-weight:600">Confirmed by photo check${r.check?.p_asian != null ? ` (${Math.round(r.check.p_asian * 100)}%)` : ''} and the smart hives’ flight lines; a team will verify on site.</span>`;
  if (r.verified_by === 'photo') return `<span style="font-size:12px;font-weight:600">Confirmed by photo check${r.check?.p_asian != null ? ` (${Math.round(r.check.p_asian * 100)}% sure)` : ''}; a team will verify on site.</span>`;
  if (r.verified_by === 'manual') return '<span style="font-size:12px;font-weight:600">Checked by the city.</span>';
  return '';
}
function reportPopup(r) {
  const [label, , col] = NR_STATUS[r.status];
  return `<div class="pop"><span class="eyebrow" style="color:${col}">Reported nest · ${label}${r.species ? ': ' + esc(r.species) : ''}</span>
    <b>${NR_LOC[r.location] || 'Nest'}</b><span class="muted">${NR_HEIGHT[r.height]}, ${NR_SIZE[r.size]}</span>
    <span class="muted">Reported ${fmtTime(r.ts)}${r.reviewed_at ? ` · checked ${fmtTime(r.reviewed_at)}` : ''} · position shown to about 100 m</span>
    ${verifyLine(r)}
    ${matchLine(r.match)}
    ${['pending', 'asian_hornet'].includes(r.status) ? '<span style="font-size:12px;color:#9A3612;font-weight:600">Keep at least 5 m away.</span>' : ''}</div>`;
}
const DRAW = {
  reports(g) {
    for (const r of D.reports.filter((r) => F('reports').status.includes(r.status))) {
      const [, , col, glyph] = NR_STATUS[r.status];
      L.marker([r.lat, r.lon], { pane: 'pts', title: NR_STATUS[r.status][0], icon: L.divIcon({ className: '', iconSize: [24, 24], iconAnchor: [12, 12],
        html: `<div style="width:24px;height:24px;border-radius:50%;background:${col};border:2.5px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35);display:grid;place-items:center;color:#fff;font:800 12px var(--sans)">${glyph}</div>` }) })
        .bindPopup(() => reportPopup(r)).addTo(g);
    }
  },
  hornets(g) {
    for (const d of filteredDetections()) {
      L.circleMarker([d.lat, d.lon], { pane: 'pts', radius: d.source === 'sighting' ? 6 : 5, color: '#fff', weight: 1.5, fillColor: d.status === 'confirmed' ? '#C2481B' : '#E38A64', fillOpacity: 0.95 })
        .bindPopup(() => detectionPopup(d)).addTo(g);
    }
  },
  lines(g) {
    for (const Ln of D.lines) {
      if (Ln.corridor && !F('lines').corridors) continue;
      let len = Ln.corridor ? 1800 : 2000;
      if (!Ln.corridor) for (const n of D.nests) {
        const db = distBearing([Ln.lat, Ln.lon], [n.lat, n.lon]), off = Math.abs(((db.b - Ln.bearing + 540) % 360) - 180);
        if (off < Math.max(3 * Ln.sigma, 12) && db.d < 4000) { len = db.d + n.semi_major_m * 0.6; break; }
      }
      const col = Ln.corridor ? '#C98A0B' : '#C2481B', sig = Math.min(Ln.sigma, 25), wedge = [[Ln.lat, Ln.lon]];
      for (let a = -sig; a <= sig; a += sig / 4) wedge.push(dest(Ln.lat, Ln.lon, Ln.bearing + a, len));
      L.polygon(wedge, { pane: 'lines', stroke: false, fillColor: col, fillOpacity: 0.08, interactive: false }).addTo(g);
      L.polyline([[Ln.lat, Ln.lon], dest(Ln.lat, Ln.lon, Ln.bearing, len)], { pane: 'lines', color: col, weight: 1.6, opacity: 0.8, dashArray: Ln.corridor ? '8 6' : '3 5' })
        .bindTooltip(`${Ln.n} hornet${Ln.n > 1 ? 's' : ''} flew ${dirWord(Ln.bearing)} (${Math.round(Ln.bearing)}° ±${Math.round(Ln.sigma)}°)${Ln.corridor ? '<br>Direction only: a second hive to the side would locate the nest.' : ''}`, { sticky: true }).addTo(g);
    }
  },
  nests(g) {
    for (const n of D.nests.filter((n) => F('nests').status.includes(n.status))) {
      const col = n.status === 'removed' ? '#2F6B45' : n.status === 'assigned' ? '#C98A0B' : '#C2481B';
      const nrep = (n.reports || []).filter((x) => x.level !== 'near');
      const pop = () => `<div class="pop"><span class="eyebrow" style="color:${col}">${n.status === 'removed' ? 'Nest removed' : n.located ? 'Asian hornet nest found' : n.status === 'assigned' ? 'Removal team on it' : 'Suspected Asian hornet nest area'}</span><b>${esc(nestPlace(n))}</b>
        ${n.located ? '<span style="font-size:12px;font-weight:600">A reported nest inside this area is confirmed (pin).</span>'
          : nrep.length ? `<span style="font-size:12px;font-weight:600">${nrep.length} nest report${nrep.length > 1 ? 's' : ''} fit${nrep.length > 1 ? '' : 's'} this area: a good place to look first.</span>` : ''}<span class="muted">Search radius ± ${Math.round(n.radius_m)} m · from ${n.n_departures} departures at ${n.n_observers} ${n.n_observers === 1 ? 'spot' : 'hives'}</span>${n.confirmed_count ? '' : '<span class="muted">Based on camera alerts not yet confirmed</span>'}<span class="muted" style="font-size:12px">Seen a nest? Don’t go near it. Tell the city.</span></div>`;
      L.polygon(ellipsePts(n), { pane: 'nests', color: col, weight: 2, dashArray: '7 5', fillColor: col, fillOpacity: n.status === 'removed' ? 0.04 : 0.09 }).bindPopup(pop).addTo(g);
      L.circleMarker([n.lat, n.lon], { pane: 'nests', radius: 5, color: col, weight: 2.5, fillColor: '#fff', fillOpacity: 1 }).bindPopup(pop).addTo(g);
      if (n.located) {
        L.marker([n.located_at.lat, n.located_at.lon], { pane: 'pts', title: 'Asian hornet nest found', icon: L.divIcon({ className: '', iconSize: [28, 34], iconAnchor: [14, 32],
          html: '<svg width="28" height="34" viewBox="0 0 28 34"><path d="M14 33 C14 33 3 20 3 12 a11 11 0 0 1 22 0 C25 20 14 33 14 33 Z" fill="#C2481B" stroke="#fff" stroke-width="2"/><circle cx="14" cy="12" r="4.5" fill="#fff"/></svg>' }) })
          .bindPopup(pop).addTo(g);
      }
      // dotted links from matching reports to the area they fit
      for (const x of nrep) {
        const r = (D.reports || []).find((q) => q.id === x.report_id);
        if (r) L.polyline([[r.lat, r.lon], [n.lat, n.lon]], { pane: 'nests', color: '#18211A', weight: 1.5, opacity: 0.6, dashArray: '2 5', interactive: false }).addTo(g);
      }
    }
  },
  hives(g) {
    const t = F('hives').types, day = now() - 7 * 86400, r = D.eco?.radius_m || 800;
    const list = [];
    if (t.includes('smart') || t.includes('planned')) {
      for (const s of D.stations) {
        if (s.lat == null || (s.planned ? !t.includes('planned') : !t.includes('smart'))) continue;
        const visits = D.detections.filter((d) => d.station_id === s.id && d.ts > day && d.status !== 'rejected').length;
        list.push([s.lat, s.lon]);
        L.marker([s.lat, s.lon], { pane: 'pts', icon: s.planned ? hexIcon('#fff', '#18211A') : hexIcon(s.online ? '#18211A' : '#7C877F', '#fff', 20, visits || ''), title: s.name })
          .bindPopup(`<div class="pop"><span class="eyebrow">${s.planned ? 'Planned smart hive' : 'Smart hive'}</span><b>${esc(s.name)}</b><span class="muted">${s.planned ? 'Not installed yet' : `${s.online ? 'Camera online' : 'Camera offline, last seen ' + ago(s.last_seen)}`}</span>${s.planned ? '' : `<span>${visits} Asian hornet visit${visits === 1 ? '' : 's'} in 7 days</span>`}</div>`).addTo(g);
      }
    }
    if (t.includes('registered')) {
      for (const a of (D.eco?.apiaries || []).filter((a) => !a.camera)) {
        list.push([a.lat, a.lon]);
        L.marker([a.lat, a.lon], { pane: 'pts', icon: hexIcon('#55605A', '#fff', 16) })
          .bindPopup(`<div class="pop"><span class="eyebrow">Registered apiary</span><b>${a.colonies} colonies</b><span class="muted">Position shown to about 100 m</span></div>`).addTo(g);
      }
    }
    if (F('hives').radius) list.forEach((p) => L.circle(p, { pane: 'zones', radius: r, color: '#18211A', weight: 1, opacity: 0.35, fill: false, dashArray: '3 4', interactive: false }).addTo(g));
  },
  wild(g) {
    for (const z of D.eco?.wild_bee_zones || []) {
      L.polygon(polyLatLngs(z.polys), { pane: 'zones', color: '#2F6B45', weight: 1.5, fillColor: '#2F6B45', fillOpacity: 0.18 })
        .bindTooltip(`<b>${esc(z.name)}</b><br>${esc(z.note || 'Wild-bee zone')}`, { sticky: true }).addTo(g);
    }
  },
  plantings(g) {
    for (const p of D.eco?.plantings || []) {
      L.circleMarker([p.lat, p.lon], { pane: 'pts', radius: 4.5, color: '#fff', weight: 1.5, fillColor: '#2F6B45', fillOpacity: 1 })
        .bindTooltip(`${fmtArea(p.area_m2)} planted ${fmtTime(p.created)}`).addTo(g);
    }
  },
  boxes(g) {
    const k = F('boxes').kind;
    for (const b of D.boxes) {
      const p = hasPollen(b);
      if ((k === 'pollen' && !p) || (k === 'weather' && p)) continue;
      if (F('boxes').range && p) L.circle([b.lat, b.lon], { pane: 'zones', radius: 1500, color: '#2F6B45', weight: 1.5, dashArray: '4 5', fill: false, interactive: false }).addTo(g);
      L.marker([b.lat, b.lon], { pane: 'pts', icon: boxIcon(p) }).bindPopup(() => boxPopup(b)).addTo(g);
    }
  },
  async sites(g) {
    if (!D.planning) D.planning = await api('/api/planning');
    D.planning.targets.forEach((t) => t.suggestions.forEach((s, i) => {
      L.marker([s.lat, s.lon], { pane: 'pts', icon: L.divIcon({ className: '', iconSize: [22, 22], iconAnchor: [11, 11],
        html: `<div style="width:22px;height:22px;border-radius:50%;background:#F2C230;border:2px solid #18211A;display:grid;place-items:center;font:700 11px var(--sans)">${i + 1}</div>` }) })
        .bindPopup(`<div class="pop"><span class="eyebrow">Suggested smart-hive site</span><b>${t.kind === 'corridor' ? `For the corridor from ${esc(t.label)}` : `For nest area #${t.nest_id ?? ''}`}</b><span class="muted">Search area about ±${s.radius_m} m (${s.radius_low_m}–${s.radius_high_m} m), ${s.visit_chance >= .7 ? 'hornets likely to visit' : 'hornets may visit'}</span></div>`).addTo(g);
    }));
  },
  async landcover(g) {
    // only what's in view (plus a margin), fetched again as the map moves
    const b = map.getBounds().pad(0.25);
    const r = await api(`/api/eco/landcover?bbox=${[b.getSouth(), b.getWest(), b.getNorth(), b.getEast()].map((v) => v.toFixed(4)).join(',')}`);
    D.landcover = r;
    if (!on('landcover')) return;
    g.clearLayers();
    const kinds = new Set(F('landcover').kinds);
    for (const f of r.features) {
      if (!kinds.has(f.kind)) continue;
      L.polygon(polyLatLngs(f.polys), { renderer: canvasLC, pane: 'lc', stroke: false, fillColor: LC_COLORS[f.kind] || '#ccc', fillOpacity: f.kind === 'urban' || f.kind === 'arable' ? 0.35 : 0.55 })
        .bindTooltip(`${LC_NAMES[f.kind] || f.kind}${f.name ? ': ' + esc(f.name) : ''}`, { sticky: true }).addTo(g);
    }
    for (const l of r.lines || []) {
      if (!kinds.has(l.kind)) continue;
      L.polyline(l.coords, { renderer: canvasLC, pane: 'lc', color: LC_COLORS[l.kind] || '#5E8F4E', weight: l.kind === 'tree_row' ? 3 : 2 }).addTo(g);
    }
  },
  async gaps(g) { g.addLayer(await grid('gap', +F('gaps').month)); },
  async spots(g) { g.addLayer(await grid('hive', 0)); },
};
async function grid(kind, month) {
  const key = `${kind}:${month}`;
  if (!gridCache[key]) {
    const r = await api(`/api/eco/grid?kind=${kind}&month=${month}`);
    const c = document.createElement('canvas'); c.width = r.cols; c.height = r.rows;
    const ctx = c.getContext('2d'), img = ctx.createImageData(r.cols, r.rows);
    r.data.forEach((v, i) => {
      let px;
      if (kind === 'hive') px = v === 0 ? [47, 107, 69, 70] : v === 1 ? [201, 138, 11, 70] : v === 2 ? [194, 72, 27, 90] : [0, 0, 0, 0];
      else { const x = v / 255 * 2; px = x < 0.5 ? [194, 72, 27, 120] : x < 0.85 ? [201, 138, 11, 95] : x < 1.3 ? [47, 107, 69, 35] : [47, 107, 69, 70]; }
      img.data.set(px, i * 4);
    });
    ctx.putImageData(img, 0, 0);
    gridCache[key] = { url: c.toDataURL(), bounds: r.bounds, data: r.data };
  }
  return L.imageOverlay(gridCache[key].url, gridCache[key].bounds, { pane: 'grid', interactive: false });
}
async function drawLayer(k) {
  const g = GROUPS[k]; g.clearLayers();
  if (!on(k)) return;
  try { await DRAW[k](g); } catch (e) { toast(e.message, true); }
}
function drawAll(keys = LAYERS.map((l) => l.key)) { keys.forEach(drawLayer); renderLegend(); }

function detectionPopup(d) {
  return `<div class="pop">${d.image_url ? `<img src="${esc(d.image_url)}" alt="Camera snapshot of the hornet">` : ''}
    <span class="eyebrow" style="color:#9A3612">${d.source === 'camera' ? 'Smart-hive camera' : 'Reported sighting'} · ${d.status === 'confirmed' ? 'confirmed' : 'not yet confirmed'}</span>
    <b>${d.source === 'sighting' ? 'Asian hornet reported here' : `Asian hornet at ${esc(d.place)}`}</b><span class="muted">${fmtTime(d.ts)}${d.bearing_deg != null ? ` · flew off ${dirWord(d.bearing_deg)} (${Math.round(d.bearing_deg)}°)` : ''}</span></div>`;
}
function monthBars(vals) {
  const mx = Math.max(...vals.map((v) => v || 0), 1e-9);
  return `<div class="mbars">${ML.map((l, k) => `<span><i style="height:${vals[k] == null ? 0 : Math.max(3, vals[k] / mx * 36)}px"></i><em>${l}</em></span>`).join('')}</div>`;
}
function boxPopup(b) {
  const L_ = b.latest || {}, v = (x, u, d = 1) => (x == null ? '–' : `${(+x).toFixed(d)} ${u}`);
  return `<div class="pop"><span class="eyebrow">SensoBox · ${b.online ? 'online' : 'last data ' + ago(b.last_seen)}</span><b>${esc(b.name)}</b>
    <dl class="facts"><dt>Temperature</dt><dd>${v(L_.temp_c, '°C')}</dd><dt>Humidity</dt><dd>${v(L_.humidity, '%', 0)}</dd><dt>Pressure</dt><dd>${v(L_.pressure_hpa, 'hPa', 0)}</dd>
    <dt>Bee flight weather</dt><dd>${b.flight_share_7d == null ? '–' : Math.round(b.flight_share_7d * 100) + '% of daylight, 7 days'}</dd></dl>
    ${hasPollen(b) ? `<span class="eyebrow">Bee-relevant pollen by month</span>${monthBars(b.monthly)}` : `<span class="muted" style="font-size:12px">${b.untyped_only ? 'Counts total pollen only, which can’t show bee food.' : 'Weather sensors only.'}</span>`}</div>`;
}

/* ---------------------------------------------------------------- layers panel + legend */
function renderLayers() {
  const box = $('#layers'), groups = [...new Set(LAYERS.map((l) => l.group))];
  box.innerHTML = `<div class="row" style="justify-content:space-between"><b>Layers</b><button class="iconbtn" id="lclose" aria-label="Close layers">✕</button></div>` + groups.map((g) => `
    <div class="lgroup"><span class="eyebrow">${g}</span>${LAYERS.filter((l) => l.group === g).map((l) => `
      <div class="lrow ${on(l.key) ? 'on' : ''}"><label><input type="checkbox" data-layer="${l.key}" ${on(l.key) ? 'checked' : ''}><i class="sw" style="background:${l.sw}"></i>${l.label}</label>
      ${on(l.key) && l.filters ? `<div class="lfilters">${l.filters.map((f) => filterHTML(l.key, f)).join('')}</div>` : ''}</div>`).join('')}</div>`).join('') +
    `<span class="src">${dataSources()}</span>`;
  box.querySelector('#lclose').onclick = () => toggleLayersBox(false);
  box.querySelectorAll('[data-layer]').forEach((c) => (c.onchange = () => { ST.layers[c.dataset.layer].on = c.checked; renderLayers(); drawLayer(c.dataset.layer); renderLegend(); saveURL(); }));
  wireFilters(box);
}
function filterHTML(lk, f) {
  const v = F(lk)[f.key];
  if (f.type === 'select') return `<label class="check">${f.label ? `<span class="muted">${f.label}</span>` : ''}<select data-f="${lk}.${f.key}">${f.options.map(([k, t]) => `<option value="${k}" ${k === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label>`;
  if (f.type === 'check') return `<label class="check"><input type="checkbox" data-f="${lk}.${f.key}" ${v ? 'checked' : ''}>${f.label}</label>`;
  return f.options.map(([k, t]) => `<button class="chip" data-m="${lk}.${f.key}" data-v="${k}" aria-pressed="${v.includes(k)}" ${f.compact ? 'style="height:26px;font-size:11px"' : ''}>${f.compact ? `<i style="width:8px;height:8px;border-radius:2px;background:${LC_COLORS[k]}"></i>` : ''}${t}</button>`).join('');
}
function wireFilters(root) {
  root.querySelectorAll('[data-f]').forEach((el) => (el.onchange = () => {
    const [lk, fk] = el.dataset.f.split('.');
    F(lk)[fk] = el.type === 'checkbox' ? el.checked : el.value;
    filterChanged(lk);
  }));
  root.querySelectorAll('[data-m]').forEach((el) => (el.onclick = () => {
    const [lk, fk] = el.dataset.m.split('.'), arr = F(lk)[fk], v = el.dataset.v;
    const i = arr.indexOf(v); if (i >= 0) arr.splice(i, 1); else arr.push(v);
    el.setAttribute('aria-pressed', arr.includes(v));
    filterChanged(lk);
  }));
}
function filterChanged(lk) {
  if (!on(lk)) { ST.layers[lk].on = true; renderLayers(); }
  drawLayer(lk); renderLegend(); saveURL();
  if (!ST.at) renderPanel();
}
function renderLegend() {
  const items = LAYERS.filter((l) => on(l.key)).map((l) => `<span><i style="background:${l.sw}"></i>${l.label}${l.key === 'gaps' ? ' · ' + MONTHS[+F('gaps').month - 1] : ''}</span>`);
  if (on('gaps')) items.push('<span><i style="background:#C2481B"></i>far too few</span><span><i style="background:#C98A0B"></i>short</span><span><i style="background:rgba(47,107,69,.5)"></i>enough</span>');
  if (on('spots')) items.push('<span><i style="background:rgba(47,107,69,.6)"></i>good</span><span><i style="background:rgba(201,138,11,.6)"></i>with changes</span><span><i style="background:rgba(194,72,27,.6)"></i>not recommended</span>');
  $('#legend').innerHTML = items.join('');
}
function dataSources() {
  const s = D.eco?.source, st = D.status;
  const land = !s || s.kind === 'demo'
    ? `Land cover and wild-bee zones: demo shapes${st?.osm?.state === 'loading' ? ' while OpenStreetMap data loads' : ''}.`
    : `Land cover and nature areas: ${esc(s.attribution)}, fetched ${esc(s.fetched)}.`;
  const bloom = st?.bloom?.observed ? ` Bloom times: iNaturalist flowering photos for ${st.bloom.observed} of ${st.bloom.total} plants.` : '';
  return `${land}${bloom} Apiary register: demo data. Base map © OpenStreetMap contributors, © CARTO.`;
}
let statusTimer;
async function loadStatus() {
  try { D.status = await api('/api/eco/status'); } catch { return; }
  renderStatus();
  clearTimeout(statusTimer);
  if (['loading'].includes(D.status.osm.state) || D.status.bloom.state === 'loading') statusTimer = setTimeout(loadStatus, 4000);
}
function renderStatus() {
  const s = D.status, parts = [];
  if (!s) return;
  if (s.osm.state === 'loading') parts.push('<span class="spin"></span>Loading land cover from OpenStreetMap…');
  else if (s.osm.state === 'failed') parts.push('Couldn’t reach OpenStreetMap; using demo map data. Will retry.');
  if (s.bloom.state === 'loading') parts.push(`<span class="spin"></span>Flowering times from iNaturalist: ${s.bloom.checked} of ${s.bloom.total} plants`);
  else if (s.bloom.state === 'failed') parts.push('Couldn’t reach iNaturalist; using built-in bloom months. Will retry.');
  const el = $('#status');
  el.innerHTML = parts.map((p) => `<span>${p}</span>`).join('');
  el.hidden = !parts.length;
}
function toggleLayersBox(show) {
  const b = $('#layers'); show = show ?? b.hidden;
  b.hidden = !show; $('#layersbtn').setAttribute('aria-expanded', show);
  if (show) renderLayers();
}

/* ================================================================ panel */
const TABS = [['overview', 'Overview'], ['hornets', 'Hornets'], ['flowers', 'Flowers'], ['hives', 'Hives'], ['sensors', 'Sensors']];
function renderTabs() {
  const alerts = filteredDetections().length;
  $('#tabs').innerHTML = TABS.map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${!ST.at && ST.tab === k}">${l}${k === 'hornets' && alerts ? ` <span class="n">${alerts}</span>` : ''}</button>`).join('');
  $('#tabs').querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => { ST.tab = b.dataset.tab; closePlace(); renderPanel(); saveURL(); openSheet(); }));
}
function chips(lk, fk) {
  const f = LBY[lk].filters.find((x) => x.key === fk);
  return `<div class="filters">${filterHTML(lk, f)}</div>`;
}
const PANELS = {
  overview() {
    const s = D.summary, al = filteredDetections().length, nests = D.nests.filter((n) => n.status !== 'removed').length;
    return `<div><h2>Bees in Heilbronn</h2><span class="muted">A public map for honeybees and wild bees.</span></div>
      <div class="stats">
        <button class="stat ${al ? 'hot' : ''}" data-go="hornets"><span>Asian hornet alerts, ${F('hornets').days === '1' ? '24 h' : F('hornets').days + ' days'}</span><span class="n">${al}</span></button>
        <button class="stat" data-go="hornets"><span>Suspected nest areas</span><span class="n">${nests}</span></button>
        <button class="stat" data-go="hives"><span>Smart hives online</span><span class="n">${s.stations_online ?? '–'}/${s.stations ?? '–'}</span></button>
        <button class="stat" data-go="flowers"><span>Flowers planted by residents</span><span class="n">${fmtArea(s.plantings_m2 || 0)}</span></button></div>
      <div class="card"><h3>Click anywhere on the map</h3><span class="muted">See which months bees go hungry there and what to plant, whether it’s a fair spot for a hive, and any hornet activity nearby.</span></div>
      <div class="card"><h3>What you see</h3><span class="muted" style="font-size:13px">Smart hives with cameras spot Asian hornets and measure which way they fly home; where those lines cross is a suspected nest. Green areas are wild-bee zones. SensoBoxes measure weather and pollen. Use <b>Layers</b> (top right) to switch layers and filter them.</span></div>
      <span class="src">${dataSources()} Hornet alerts marked “not yet confirmed” come straight from the cameras.</span>`;
  },
  hornets() {
    const dets = filteredDetections().sort((a, b) => b.ts - a.ts);
    const nests = D.nests.filter((n) => F('nests').status.includes(n.status));
    const reps = D.reports.filter((r) => F('reports').status.includes(r.status));
    return `<div><h2>Asian hornets</h2><span class="muted">Alerts from smart-hive cameras, and nests reported by residents.</span></div>
      ${D.summary.reports_enabled ? '<div class="card"><b>Found a nest?</b><span class="muted">Report it, and keep at least 5 m away. The city checks whether it’s an Asian hornet nest.</span><button class="btn report" data-report style="align-self:flex-start">Report a nest</button></div>' : ''}
      <h3>Reported nests</h3>${chips('reports', 'status')}
      <div class="list">${reps.length ? reps.slice(0, 40).map((r) => { const [label, badge] = NR_STATUS[r.status];
        return `<button class="item" data-fly="${r.lat},${r.lon},16"><span class="body"><span class="row" style="justify-content:space-between"><b>${NR_LOC[r.location]}</b><span class="t">${fmtTime(r.ts)}</span></span>
        <span class="muted">${NR_HEIGHT[r.height]}, ${NR_SIZE[r.size]}</span><span class="badge ${badge}" style="align-self:flex-start">${label}${r.species ? ': ' + esc(r.species) : ''}</span>
        ${r.match && r.match.level !== 'near' ? `<span class="muted" style="font-size:11px">🐝 ${r.match.kind === 'nest' ? 'Fits a suspected nest area' : 'On a hornet flight line'} (${(r.match.hives || []).join(', ')})</span>` : ''}</span></button>`; }).join('')
        : '<div class="empty">No reported nests with these filters.</div>'}</div>
      <h3>Camera alerts</h3>
      <div class="filters">${LBY.hornets.filters.map((f) => filterHTML('hornets', f)).join('')}</div>
      <div class="list">${dets.length ? dets.slice(0, 60).map((d) => `<button class="item" data-fly="${d.lat},${d.lon}">
        ${d.image_url ? `<img class="thumb" src="${esc(d.image_url)}" alt="" loading="lazy">` : '<span class="thumb"></span>'}
        <span class="body"><span class="row" style="justify-content:space-between"><b>${esc(d.place)}</b><span class="t">${fmtTime(d.ts)}</span></span>
        <span class="muted">${d.bearing_deg != null ? `Flew off ${dirWord(d.bearing_deg)} (${Math.round(d.bearing_deg)}°)` : 'Direction not seen'}</span>
        ${d.status === 'confirmed' ? '<span class="badge green" style="align-self:flex-start">✓ Confirmed</span>' : '<span class="badge grey" style="align-self:flex-start">Not yet confirmed</span>'}</span></button>`).join('')
        : '<div class="empty">No alerts in this period.</div>'}</div>
      <h3>Nest areas</h3>${chips('nests', 'status')}
      <div class="list">${nests.length ? nests.map((n) => `<button class="item" data-fly="${n.lat},${n.lon},16"><span class="body"><span class="row" style="justify-content:space-between"><b>${esc(nestPlace(n))}</b>${nestBadge(n)}</span>
        <span class="muted">± ${Math.round(n.radius_m)} m · ${n.n_departures} departures, ${n.n_observers} ${n.n_observers === 1 ? 'spot' : 'hives'}</span></span></button>`).join('') : '<div class="empty">None with these filters.</div>'}</div>
      <span class="src">Flight lines: hornets fly home in a nearly straight line, so lines from different hives cross near the nest. The ellipse is where the nest is with about 95% certainty. Never approach a nest yourself.</span>`;
  },
  flowers() {
    const m = +F('gaps').month, g = gridCache[`gap:${m}`];
    let short = '–';
    if (g) { const land = g.data.filter((v) => v < 255); short = Math.round(land.filter((v) => v / 255 * 2 < 0.85).length / Math.max(land.length, 1) * 100) + '%'; }
    const PF = ST.plantFilter;
    let plants = (D.plants || []).filter((p) => (!PF.native || p.native) && (PF.setting === 'any' || p.settings?.includes(PF.setting) || PLANT_SETTINGS[p.id]?.includes(PF.setting)) && (PF.month === 'any' || p.months.includes(+PF.month)));
    return `<div><h2>Flowers for bees</h2><span class="muted">Where bees find too little to eat, month by month, within 800 m.</span></div>
      <div class="filters"><span class="muted">Flower gaps in</span>${MONTHS.slice(2, 10).map((mm, i) => `<button class="chip" data-gm="${i + 3}" aria-pressed="${on('gaps') && i + 3 === m}">${mm}</button>`).join('')}</div>
      <div class="stats"><div class="stat ${on('gaps') ? 'hot' : ''}"><span>Area short of flowers${on('gaps') ? ' in ' + MONTHS[m - 1] : ''}</span><span class="n">${on('gaps') ? short : '–'}</span></div>
        <div class="stat"><span>Plantings by residents</span><span class="n">${D.eco?.plantings?.length ?? 0}</span></div></div>
      <div class="card"><h3>What should I plant?</h3><span class="muted">Click the map where you’d plant: you get the plants that fill that area’s gap, and when to plant them.</span></div>
      <h3>Plant guide</h3>
      <div class="filters"><select id="pf-set" aria-label="Where">${[['any', 'Anywhere'], ['balcony', 'Balcony'], ['garden', 'Garden'], ['public', 'Public green'], ['hedge', 'Hedge'], ['farm', 'Farm field']].map(([k, t]) => `<option value="${k}" ${PF.setting === k ? 'selected' : ''}>${t}</option>`).join('')}</select>
        <select id="pf-month" aria-label="Flowering in"><option value="any">Flowering any time</option>${MONTHS.slice(1, 11).map((mm, i) => `<option value="${i + 2}" ${PF.month === String(i + 2) ? 'selected' : ''}>Flowering in ${mm}</option>`).join('')}</select>
        <label class="check"><input type="checkbox" id="pf-native" ${PF.native ? 'checked' : ''}>Native only</label></div>
      <div class="card" style="gap:0;padding:8px 12px">${D.plants ? `<div class="prow" style="border:0"><span class="pn"></span><span class="cal letters">${ML.map((l) => `<i>${l}</i>`).join('')}</span></div>${plants.map((p) => plantRow(p, [])).join('') || '<div class="empty">No plants match.</div>'}` : '<div class="empty">Loading…</div>'}</div>
      <span class="src">${bloomSrc()}</span>`;
  },
  hives() {
    const t = F('hives').types, st = D.stations.filter((s) => s.lat != null && (s.planned ? t.includes('planned') : t.includes('smart')));
    const reg = (D.eco?.apiaries || []).filter((a) => !a.camera);
    const day = now() - 7 * 86400;
    return `<div><h2>Hives</h2><span class="muted">Smart hives watch for Asian hornets; every apiary shares the flowers with wild bees.</span></div>
      ${chips('hives', 'types')}
      <div class="list">${st.map((s) => { const v = D.detections.filter((d) => d.station_id === s.id && d.ts > day && d.status !== 'rejected').length;
        return `<button class="item" data-fly="${s.lat},${s.lon},16"><span class="body"><span class="row" style="justify-content:space-between"><b>${esc(s.name)}</b>${s.planned ? '<span class="badge grey">Planned</span>' : s.online ? '<span class="badge green">● Online</span>' : '<span class="badge grey">Offline</span>'}</span>
        <span class="muted">${s.planned ? 'Smart hive not installed yet' : `${v} hornet visit${v === 1 ? '' : 's'} in 7 days`}</span></span></button>`; }).join('') || '<div class="empty">None with these filters.</div>'}</div>
      ${t.includes('registered') ? `<div class="card"><span><b>${reg.length}</b> registered apiaries (${reg.reduce((a, b) => a + b.colonies, 0)} colonies), shown to about 100 m.</span></div>` : ''}
      <div class="card"><h3>Is this a good place for a hive?</h3><span class="muted">Click the map: the check looks at wild-bee zones, other apiaries within 800 m, flowers through the season and hornet nests. Switch on <b>Good hive spots</b> under Layers for the whole city.</span>
        <button class="btn ghost sm" data-layeron="spots" style="align-self:flex-start">Show good hive spots</button></div>
      <div class="card"><h3>Where would new smart hives help?</h3><span class="muted">Sites where a new camera hive would turn a hornet’s direction into a nest location.</span>
        <button class="btn ghost sm" data-layeron="sites" style="align-self:flex-start">Show suggested sites</button></div>`;
  },
  sensors() {
    const k = F('boxes').kind, list = D.boxes.filter((b) => k === 'all' || (k === 'pollen') === hasPollen(b));
    return `<div><h2>SensoBoxes</h2><span class="muted">Weather and pollen sensors. Where a box identifies pollen types, the flower map uses what it measures.</span></div>
      ${chips('boxes', 'kind')}
      <div class="list">${list.map((b) => { const L_ = b.latest || {};
        return `<button class="item" data-fly="${b.lat},${b.lon},15"><span class="body"><span class="row" style="justify-content:space-between"><b>${esc(b.name)}</b>${hasPollen(b) ? '<span class="badge green">Pollen + weather</span>' : '<span class="badge grey">Weather</span>'}</span>
        <span class="muted">${L_.temp_c != null ? `${(+L_.temp_c).toFixed(1)} °C · ${Math.round(L_.humidity ?? 0)}% · ` : ''}${b.flight_share_7d != null ? `bees could fly ${Math.round(b.flight_share_7d * 100)}% of daylight this week` : 'no recent data'}</span>
        ${hasPollen(b) ? monthBars(b.monthly) : ''}</span></button>`; }).join('') || '<div class="empty">None with these filters.</div>'}</div>
      <span class="src">Most airborne pollen is grass, birch and pine, which bees hardly use, so only pollen sensors that tell types apart change the flower map.</span>`;
  },
};
const PLANT_SETTINGS = {};
ST.plantFilter = { setting: 'any', month: 'any', native: false };
function bloomSrc() {
  const b = D.plants?.find((p) => p.bloom_source === 'observed');
  return b ? `Bloom months: flowering photos on iNaturalist (${esc(b.bloom_place)}), where available; otherwise gardening references.` : 'Bloom months from gardening references.';
}
function plantRow(p, gaps) {
  const cells = ML.map((_, k) => { const m = k + 1, onm = p.months.includes(m), op = onm && p.share ? ` style="opacity:${(0.35 + 0.65 * p.share[k]).toFixed(2)}"` : '';
    return `<i class="${onm ? (gaps.includes(m) ? 'fill' : 'on') : ''} ${gaps.includes(m) ? 'gapm' : ''}"${op}></i>`; }).join('');
  const t = p.timing;
  return `<div class="prow" title="${esc(p.tip)}"><span class="pn"><b>${esc(p.name)}</b><small>${esc(p.german)}${p.native ? '' : ' · not native'}</small>${t?.step ? `<span class="when">${esc(t.step)} → ${esc(t.flowers)}</span>` : ''}</span><span class="cal">${cells}</span></div>`;
}
function renderPanel() {
  renderTabs();
  if (ST.at) return;
  const body = $('#pbody');
  body.innerHTML = PANELS[ST.tab]();
  wirePanel(body);
  if (ST.tab === 'flowers' && !D.plants) api('/api/eco/plant-list').then((p) => { D.plants = p; if (ST.tab === 'flowers' && !ST.at) renderPanel(); });
}
function wirePanel(body) {
  wireFilters(body);
  body.querySelectorAll('[data-go]').forEach((b) => (b.onclick = () => { ST.tab = b.dataset.go; renderPanel(); saveURL(); }));
  body.querySelectorAll('[data-report]').forEach((b) => (b.onclick = () => reportDialog(ST.at)));
  body.querySelectorAll('[data-fly]').forEach((b) => (b.onclick = () => { const [la, lo, z] = b.dataset.fly.split(',').map(Number); map.flyTo([la, lo], z || Math.max(map.getZoom(), 15)); shrinkSheet(); }));
  body.querySelectorAll('[data-layeron]').forEach((b) => (b.onclick = () => { ST.layers[b.dataset.layeron].on = true; drawLayer(b.dataset.layeron); renderLegend(); saveURL(); toast('Layer switched on. Use Layers to filter it.'); shrinkSheet(); }));
  body.querySelectorAll('[data-gm]').forEach((b) => (b.onclick = async () => { F('gaps').month = b.dataset.gm; ST.layers.gaps.on = true; await drawLayer('gaps'); renderLegend(); saveURL(); renderPanel(); }));
  const ps = $('#pf-set', body), pm = $('#pf-month', body), pn = $('#pf-native', body);
  if (ps) { ps.onchange = () => { ST.plantFilter.setting = ps.value; renderPanel(); }; pm.onchange = () => { ST.plantFilter.month = pm.value; renderPanel(); }; pn.onchange = () => { ST.plantFilter.native = pn.checked; renderPanel(); }; }
}

/* ================================================================ place card (click anywhere) */
map.on('click', (e) => {
  if (ST.picking) { const cb = ST.picking; ST.picking = null; $('#pickbar').hidden = true; cb([e.latlng.lat, e.latlng.lng]); return; }
  openPlace(e.latlng.lat, e.latlng.lng);
});
function closePlace() { ST.at = null; if (placeMarker) { placeMarker.remove(); placeMarker = null; } }
async function openPlace(lat, lon, { fly = false } = {}) {
  ST.at = [lat, lon]; saveURL(); renderTabs();
  if (placeMarker) placeMarker.setLatLng([lat, lon]); else placeMarker = L.circleMarker([lat, lon], { pane: 'pts', radius: 8, color: '#fff', weight: 3, fillColor: '#18211A', fillOpacity: 1 }).addTo(map);
  if (fly) map.flyTo([lat, lon], Math.max(map.getZoom(), 15));
  const body = $('#pbody');
  body.innerHTML = `<div class="row" style="justify-content:space-between;align-items:flex-start"><div><span class="eyebrow">This spot</span><h2 id="pl-name">…</h2><span class="muted mono" style="font-size:11px">${lat.toFixed(5)}, ${lon.toFixed(5)}</span></div>
      <div class="row"><button class="iconbtn" id="pl-share" aria-label="Copy link to this spot"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#18211A" stroke-width="1.8"><path d="M10 14 L14 10 M8 16 a4 4 0 0 1 0-6 l2-2 M16 8 a4 4 0 0 1 0 6 l-2 2"/></svg></button>
      <button class="iconbtn" id="pl-close" aria-label="Close">✕</button></div></div>
    <div id="pl-flowers" class="card"><span class="muted">Looking at the flowers around here…</span></div>
    <div id="pl-hive" class="card"><span class="muted">Checking this spot for hives…</span></div>
    <div id="pl-hornets"></div><div id="pl-box"></div><div id="pl-site"></div>`;
  openSheet();
  $('#pl-close').onclick = () => { closePlace(); renderPanel(); saveURL(); };
  $('#pl-share').onclick = async () => { try { await navigator.clipboard.writeText(location.href); toast('Link copied.'); } catch { toast('Copy the address bar to share this spot.'); } };
  const at = ST.at;
  const still = () => ST.at === at;
  api(`/api/eco/place?lat=${lat}&lon=${lon}`).then((p) => { if (still()) $('#pl-name').textContent = p.place; }).catch(() => { if (still()) $('#pl-name').textContent = 'Heilbronn'; });
  placeFlowers(lat, lon, 'garden', false, still);
  api(`/api/eco/spot?lat=${lat}&lon=${lon}&colonies=2`).then((s) => still() && placeHive(s)).catch((e) => still() && ($('#pl-hive').innerHTML = `<span class="muted">${esc(e.message)}</span>`));
  placeHornets(lat, lon);
  placeBox(lat, lon);
  api('/api/planning/evaluate', { method: 'POST', body: { lat, lon } }).then((ev) => still() && placeSite(ev)).catch(() => { });
}
async function placeFlowers(lat, lon, setting, natives, still) {
  let P;
  try { P = await api(`/api/eco/plants?lat=${lat}&lon=${lon}&setting=${setting}&natives_only=${natives}`); } catch (e) { if (still()) $('#pl-flowers').innerHTML = `<span class="muted">${esc(e.message)}</span>`; return; }
  if (!still()) return;
  const ev = P.evidence || {};
  const evText = ev.kind === 'measured' ? `📡 Measured by SensoBox ${esc(ev.box)}${ev.distance_m > 50 ? ` (${fmtDist(ev.distance_m)} away)` : ''}: ${{ less: 'less bee pollen than the map suggests', more: 'more bee pollen than the map suggests', 'about as': 'about what the map suggests' }[ev.vs_model]}.`
    : '🗺️ Estimated from the land-cover map.';
  const pct = P.goal_m2 ? Math.min(100, Math.round(P.planted_m2 / P.goal_m2 * 100)) : 100;
  const el = $('#pl-flowers');
  el.className = 'card';
  el.innerHTML = `<div class="card gap ${P.gap_found ? '' : 'ok'}" style="margin:-12px -14px 0;border-radius:14px 14px 0 0;border-width:0 0 1px"><span class="eyebrow" style="color:${P.gap_found ? '#6B4900' : '#1F4A2F'}">${P.gap_found ? 'FOOD GAP' : 'NO SERIOUS GAP'}</span><b>${esc(P.message)}</b><span class="evid">${evText}</span></div>
    <div class="filters"><select id="pl-set" aria-label="Where would you plant?">${[['balcony', 'Balcony'], ['garden', 'Garden'], ['public', 'Public green'], ['hedge', 'Hedge'], ['farm', 'Farm field']].map(([k, t]) => `<option value="${k}" ${k === setting ? 'selected' : ''}>${t}</option>`).join('')}</select>
      <label class="check"><input type="checkbox" id="pl-nat" ${natives ? 'checked' : ''}>Native only</label></div>
    <div><div class="prow" style="border:0"><span class="pn"><b style="font-size:11px">Fills the gap</b></span><span class="cal letters">${ML.map((l, k) => `<i class="${P.gap_months.includes(k + 1) ? 'gapm' : ''}">${l}</i>`).join('')}</span></div>
      ${P.fills_gap.map((p) => plantRow(p, P.gap_months)).join('') || '<div class="empty">No plants for this setting.</div>'}
      ${P.also_good.length ? `<div class="prow"><span class="pn"><b style="font-size:11px">Also good</b></span></div>${P.also_good.slice(0, 3).map((p) => plantRow(p, P.gap_months)).join('')}` : ''}</div>
    ${P.goal_m2 ? `<div><span class="muted" style="font-size:12px">Goal for 800 m around here: about ${fmtArea(P.goal_m2)} more flowers · ${fmtArea(P.planted_m2)} planted so far</span><div style="height:6px;border-radius:3px;background:#ECEEE7;margin-top:4px"><div style="height:6px;border-radius:3px;background:#2F6B45;width:${pct}%"></div></div></div>` : ''}
    <div class="row"><button class="btn sm" id="pl-planted">I planted something here</button>${D.summary.reports_enabled ? '<button class="btn ghost sm" id="pl-report">Report a nest here</button>' : ''}</div>
    <span class="src">${esc(P.avoid)}</span>`;
  $('#pl-set').onchange = (e) => placeFlowers(lat, lon, e.target.value, $('#pl-nat').checked, still);
  $('#pl-nat').onchange = (e) => placeFlowers(lat, lon, $('#pl-set').value, e.target.checked, still);
  $('#pl-planted').onclick = () => plantedDialog(lat, lon, [...P.fills_gap, ...P.also_good], setting, () => placeFlowers(lat, lon, setting, natives, still));
  const rep = $('#pl-report'); if (rep) rep.onclick = () => reportDialog([lat, lon]);
}
function placeHive(s) {
  const ic = { bad: '✕', warn: '▲', ok: '✓' };
  const alt = s.alternative;
  $('#pl-hive').innerHTML = `<div class="verdict"><span class="v ${s.verdict}">${s.verdict === 'green' ? '✓' : s.verdict === 'red' ? '✕' : '▲'}</span><span><span class="vlabel">${esc(s.label)}</span><br><b>${esc(s.headline)}</b> <span class="muted" style="font-size:12px">(as a hive spot)</span></span></div>
    ${s.checks.map((c) => `<div class="ck"><i class="${c.status}">${ic[c.status]}</i><span><b>${esc(c.title)}</b><small>${esc(c.detail)}</small></span></div>`).join('')}
    ${alt ? `<button class="btn ghost sm" id="pl-alt" style="align-self:flex-start">Show the ${alt.verdict === 'green' ? 'green' : 'better'} spot ${fmtDist(alt.distance_m)} ${alt.direction}</button>` : ''}`;
  if (alt) $('#pl-alt').onclick = () => openPlace(alt.lat, alt.lon, { fly: true });
}
function placeHornets(lat, lon) {
  const since = now() - 14 * 86400;
  const near = D.detections.filter((d) => d.lat != null && d.ts >= since && d.status !== 'rejected' && distBearing([lat, lon], [d.lat, d.lon]).d <= 2000);
  const nest = D.nests.filter((n) => n.status !== 'removed').map((n) => ({ n, d: Math.max(0, distBearing([lat, lon], [n.lat, n.lon]).d - n.radius_m) })).sort((a, b) => a.d - b.d)[0];
  if (!near.length && (!nest || nest.d > 3000)) { $('#pl-hornets').innerHTML = ''; return; }
  $('#pl-hornets').innerHTML = `<div class="card"><h3>Asian hornets nearby</h3><span class="muted">${near.length} alert${near.length === 1 ? '' : 's'} within 2 km in 14 days.${nest ? ` ${nest.d === 0 ? 'Inside a suspected nest area.' : `Nearest suspected nest area ${fmtDist(nest.d)} away.`}` : ''}</span></div>`;
}
function placeBox(lat, lon) {
  const b = D.boxes.map((b) => ({ b, d: distBearing([lat, lon], [b.lat, b.lon]).d })).sort((x, y) => x.d - y.d)[0];
  if (!b || b.d > 3000) { $('#pl-box').innerHTML = ''; return; }
  const L_ = b.b.latest || {};
  $('#pl-box').innerHTML = `<div class="card"><h3>Nearest SensoBox · ${esc(b.b.name)}</h3><span class="muted">${fmtDist(b.d)} away${L_.temp_c != null ? ` · ${(+L_.temp_c).toFixed(1)} °C, ${Math.round(L_.humidity ?? 0)}% humidity` : ''}${b.b.flight_share_7d != null ? ` · bees could fly ${Math.round(b.b.flight_share_7d * 100)}% of daylight this week` : ''}</span>
    ${hasPollen(b.b) ? `<span class="eyebrow">Bee-relevant pollen by month</span>${monthBars(b.b.monthly)}` : ''}</div>`;
}
function placeSite(ev) {
  if (!ev.effects?.length) { $('#pl-site').innerHTML = ''; return; }
  $('#pl-site').innerHTML = `<div class="card"><h3>A smart hive here would help find nests</h3>${ev.effects.map((x) => `<span class="muted" style="font-size:13px">${x.kind === 'corridor' ? `Turns the direction-only corridor from ${esc(x.label)} into a search area of about ±${x.radius_m} m.` : `Narrows nest area #${x.nest_id ?? ''} from ±${x.with_planned_radius_m} m to about ±${x.radius_m} m.`}</span>`).join('')}</div>`;
}

/* ---------------------------------------------------------------- anonymous contributions */
function dialog(title, inner) {
  const d = h(`<dialog><div class="dh"><b>${esc(title)}</b><button class="iconbtn" aria-label="Close">✕</button></div>${inner}</dialog>`);
  document.body.append(d); d.querySelector('.dh button').onclick = () => d.close(); d.addEventListener('close', () => d.remove()); d.showModal(); return d;
}
function plantedDialog(lat, lon, plants, setting, done) {
  const farm = setting === 'farm', sizes = farm ? [['0.1 ha', 1000], ['0.5 ha', 5000], ['1 ha', 10000]] : [['Balcony box', 1], ['Small bed', 5], ['Large bed', 20]];
  const d = dialog('What did you plant?', `<form><label>Plant<select name="plant">${plants.map((p) => `<option value="${p.id}">${esc(p.name)} (${esc(p.german)})</option>`).join('')}</select></label>
    <label>How much?<select name="m2">${sizes.map(([l, v]) => `<option value="${v}">${l}</option>`).join('')}<option value="25">One shrub or small tree</option></select></label>
    <span class="src">Anonymous. Saved at the spot you clicked; it counts towards this area’s goal right away.</span><button class="btn">Add to the map</button></form>`);
  d.querySelector('form').onsubmit = async (e) => {
    e.preventDefault();
    try { await api('/api/eco/plantings', { method: 'POST', body: { lat, lon, plant_id: e.target.plant.value, area_m2: +e.target.m2.value } }); d.close(); toast('Thank you! Your planting is on the map.'); await loadEco(); drawAll(['plantings']); done(); }
    catch (err) { toast(err.message, true); }
  };
}
const NEST_GUIDE = `<details class="guide"><summary>Is it an Asian hornet nest?</summary><div class="compare">
  <div style="background:#FBE7DE"><b style="color:#7A2A0E">Asian hornet</b><span>Usually round or pear-shaped, often high in a tree. The entrance is a small hole <b>on the side</b>. Hornets: mostly black, one orange band, yellow leg tips.</span></div>
  <div style="background:#EEF0EA"><b>European hornet</b><span>Usually hidden in a cavity: hollow trees, bird boxes, attics. The nest is <b>open at the bottom</b>. Hornets: reddish-brown and yellow. Protected.</span></div></div>
  <span class="muted" style="font-size:12px">Wasp nests are smaller and often underground or in sheds. Not sure? Report it anyway: the city checks.</span></details>`;
const choiceRow = (name, opts, val) => `<div class="seg wrap" role="group">${Object.entries(opts).map(([k, t]) => `<button type="button" data-${name}="${k}" aria-pressed="${k === val}">${t}</button>`).join('')}</div>`;

function reportDialog(at, keep = {}) {
  const st = { at: at || keep.at || null, location: keep.location || 'tree', height: keep.height || 'unknown', size: keep.size || 'unknown', note: keep.note || '' };
  const d = dialog('Report a nest', `<form id="rf">
    <div class="safety">Keep at least 5 m away and don’t try to remove it. Asian hornets defend their nest.</div>
    <div class="field"><span class="flabel">Where is it?</span><span id="rf-where" class="muted">${st.at ? `${st.at[0].toFixed(5)}, ${st.at[1].toFixed(5)}` : 'Not set yet'}</span>
      <div class="row" style="flex-wrap:wrap"><button type="button" class="btn ghost sm" id="rf-gps">Use my location</button><button type="button" class="btn ghost sm" id="rf-pick">Pick on the map</button></div></div>
    <div class="field"><span class="flabel">What is it in or on?</span>${choiceRow('loc', { tree: 'Tree', building: 'Building or roof', hedge: 'Hedge or bush', ground: 'Ground or wall', other: 'Other' }, st.location)}</div>
    <div class="field"><span class="flabel">How high up?</span>${choiceRow('hgt', { reach: 'Within reach', '2-5m': '2–5 m', 'above-5m': 'Higher than 5 m', unknown: 'Can’t tell' }, st.height)}</div>
    <div class="field"><span class="flabel">About how big?</span>${choiceRow('sz', { fist: 'Smaller than a fist', football: 'Football', bigger: 'Bigger', unknown: 'Can’t tell' }, st.size)}</div>
    <label>Photo <small class="muted">Optional, taken from a safe distance. A clear, sharp photo of the whole nest can be checked automatically; otherwise the city checks on site. Only the city sees it; location and camera details are removed.</small><input type="file" name="photo" accept="image/*" capture="environment"></label>
    <label>Anything that helps find it? <small class="muted">Optional, e.g. “in the oak behind the playground”. Only the city sees it.</small><input type="text" name="note" maxlength="300" value="${esc(st.note)}"></label>
    ${NEST_GUIDE}
    <span class="src">Anonymous. The map shows it near the spot (to about 100 m) as “waiting for a check” until the city confirms what it is.</span>
    <button class="btn report" id="rf-send">Send report</button></form>`);
  const setWhere = (p) => { st.at = p; $('#rf-where', d).textContent = `${p[0].toFixed(5)}, ${p[1].toFixed(5)}`; };
  for (const [attr, key] of [['loc', 'location'], ['hgt', 'height'], ['sz', 'size']]) {
    d.querySelectorAll(`[data-${attr}]`).forEach((b) => (b.onclick = () => {
      st[key] = b.dataset[attr]; d.querySelectorAll(`[data-${attr}]`).forEach((x) => x.setAttribute('aria-pressed', x === b));
    }));
  }
  $('#rf-gps', d).onclick = () => {
    if (!navigator.geolocation) return toast('This browser can’t share its location. Pick on the map instead.', true);
    navigator.geolocation.getCurrentPosition((p) => setWhere([p.coords.latitude, p.coords.longitude]),
      () => toast('Location blocked. Allow it in the browser, or pick on the map.', true), { enableHighAccuracy: true, timeout: 10000 });
  };
  $('#rf-pick', d).onclick = () => {
    const keepSt = { location: st.location, height: st.height, size: st.size, note: $('#rf', d).note.value };
    d.close(); $('#pickbar').hidden = false; shrinkSheet();
    ST.picking = (p) => reportDialog(p, keepSt);
  };
  $('#rf', d).onsubmit = async (e) => {
    e.preventDefault();
    if (!st.at) return toast('Add where the nest is: your location or a spot on the map.', true);
    const fd = new FormData();
    fd.append('lat', st.at[0]); fd.append('lon', st.at[1]);
    fd.append('location', st.location); fd.append('height', st.height); fd.append('size', st.size);
    fd.append('note', e.target.note.value);
    if (e.target.photo.files[0]) fd.append('image', e.target.photo.files[0]);
    $('#rf-send', d).disabled = true;
    try {
      const res = await api('/api/nest-reports', { method: 'POST', body: fd });
      d.close();
      toast(res.status === 'asian_hornet' ? 'Photo check: this looks like an Asian hornet nest. The city has it. Please keep your distance.'
        : 'Thank you! The city will check it. Please keep your distance.');
      ST.layers.reports.on = true; F('reports').status.includes('pending') || F('reports').status.push('pending');
      refreshSoon(['reports']);
    } catch (err) { toast(err.message, true); $('#rf-send', d).disabled = false; }
  };
}

/* ================================================================ search */
$('#search').onsubmit = async (e) => {
  e.preventDefault();
  const q = $('#q').value.trim(); if (!q) return;
  const hit = PLACES.find(([n]) => n.toLowerCase().includes(q.toLowerCase()));
  if (hit) { map.flyTo([hit[1], hit[2]], 15); openPlace(hit[1], hit[2]); return; }
  try {
    const j = await (await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=de&viewbox=9.12,49.20,9.31,49.08&bounded=1&q=${encodeURIComponent(q)}`)).json();
    if (j[0]) { map.flyTo([+j[0].lat, +j[0].lon], 16); openPlace(+j[0].lat, +j[0].lon); } else toast('No match in Heilbronn. Try a street or district.', true);
  } catch { toast('Address search needs internet. Try a district name like Böckingen.', true); }
};

/* ================================================================ URL state (shareable views) */
let urlTimer;
function saveURL() {
  clearTimeout(urlTimer);
  urlTimer = setTimeout(() => {
    const c = map.getCenter(), p = new URLSearchParams();
    p.set('map', `${c.lat.toFixed(5)},${c.lng.toFixed(5)},${map.getZoom()}`);
    p.set('layers', LAYERS.filter((l) => on(l.key)).map((l) => l.key).join(','));
    if (ST.tab !== 'overview') p.set('tab', ST.tab);
    if (on('gaps')) p.set('month', F('gaps').month);
    if (ST.at) p.set('at', `${ST.at[0].toFixed(5)},${ST.at[1].toFixed(5)}`);
    history.replaceState(null, '', '#' + p.toString().replace(/%2C/g, ','));
  }, 200);
}
function loadURL() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get('map')) { const [a, b, z] = p.get('map').split(',').map(Number); if (!isNaN(a)) map.setView([a, b], z || AREA.zoom); }
  if (p.has('layers')) { const ks = p.get('layers').split(','); LAYERS.forEach((l) => (ST.layers[l.key].on = ks.includes(l.key))); }
  if (p.get('tab') && PANELS[p.get('tab')]) ST.tab = p.get('tab');
  if (p.get('month')) F('gaps').month = p.get('month');
  return p.get('at') ? p.get('at').split(',').map(Number) : null;
}
map.on('moveend', saveURL);
let lcTimer;
map.on('moveend', () => { if (on('landcover')) { clearTimeout(lcTimer); lcTimer = setTimeout(() => drawLayer('landcover'), 350); } });

/* ================================================================ sheet / panel chrome */
function openSheet() { $('#panel').classList.remove('collapsed'); $('#collapse').classList.remove('out'); $('#collapse').textContent = '‹'; }
function shrinkSheet() { if (innerWidth <= 760) $('#panel').classList.remove('expanded'); }
$('#grab').onclick = () => { const p = $('#panel'); if (p.classList.contains('collapsed')) p.classList.remove('collapsed'); else p.classList.toggle('expanded'); };
$('#collapse').onclick = () => { const p = $('#panel'), c = p.classList.toggle('collapsed'); $('#collapse').classList.toggle('out', c); $('#collapse').textContent = c ? '›' : '‹'; };
$('#layersbtn').onclick = () => toggleLayersBox();
$('#reportbtn').onclick = () => reportDialog(ST.at);
$('#pickcancel').onclick = () => { ST.picking = null; $('#pickbar').hidden = true; };
function renderReportBtn() { $('#reportbtn').hidden = !D.summary.reports_enabled; }

/* ================================================================ data + live updates */
async function loadEco() { [D.eco, D.boxes] = await Promise.all([api('/api/eco/layers'), api('/api/sensoboxes')]); }
async function loadHornets() { [D.detections, D.nests, D.lines, D.stations] = await Promise.all([api('/api/detections?limit=1000'), api('/api/nests?include_removed=true'), api('/api/lines'), api('/api/stations')]); }
async function loadReports() { D.reports = await api('/api/nest-reports'); }
async function loadAll() { D.summary = await api('/api/summary'); await Promise.all([loadHornets(), loadEco(), loadReports()]); }
let pending = new Set(), refreshT;
function refreshSoon(what) {
  what.forEach((w) => pending.add(w));
  clearTimeout(refreshT);
  refreshT = setTimeout(async () => {
    const w = pending; pending = new Set();
    try {
      D.summary = await api('/api/summary'); renderReportBtn();
      if (w.has('hornets')) { await loadHornets(); D.planning = null; }
      if (w.has('reports')) { await Promise.all([loadReports(), loadHornets()]); drawLayer('reports'); }
      if (w.has('eco')) { await loadEco(); Object.keys(gridCache).forEach((k) => delete gridCache[k]); D.plants = null; loadStatus(); }
      drawAll(w.has('eco') ? undefined : ['hornets', 'lines', 'nests', 'hives', 'sites', 'reports']);
      if (!ST.at) renderPanel(); else renderTabs();
    } catch { }
  }, 1200);
}
function connect(delay = 1000) {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = () => { D.live = true; delay = 1000; };
  ws.onclose = () => { D.live = false; setTimeout(() => connect(Math.min(delay * 2, 30000)), delay); };
  ws.onmessage = (e) => {
    const { type, data } = JSON.parse(e.data);
    if (['detection', 'nests', 'lines', 'station', 'station_deleted', 'planning_changed'].includes(type)) refreshSoon(['hornets']);
    if (type === 'detection' && data?.status === 'pending' && data?.source === 'camera') toast(`Asian hornet seen at ${data.place}`);
    if (['eco_changed', 'settings'].includes(type)) refreshSoon(['eco']);
    if (type === 'nest_reports') refreshSoon(['reports']);
  };
  setInterval(() => ws.readyState === 1 && ws.send('ping'), 25000);
}
setInterval(() => refreshSoon(['hornets']), 120000);   // fallback if live updates are blocked

(async () => {
  const at = loadURL();
  try { await loadAll(); } catch (e) { toast(e.message, true); }
  loadStatus();
  drawAll();
  renderReportBtn();
  if (at) openPlace(at[0], at[1]); else renderPanel();
  if (innerWidth <= 760 && !at) $('#panel').classList.remove('expanded');
  connect();
})();
