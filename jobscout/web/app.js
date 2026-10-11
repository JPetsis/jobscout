/* jobscout front-end. A client of jobscout/api.py -- no rules live here.
 *
 * The bridge only exists after `pywebviewready`; calling pywebview.api before
 * that throws. Everything after that is plain DOM work over one loaded array,
 * so filtering never round-trips to Python.
 */
'use strict';

let ALL = [];                 // every match, unfiltered
let STATS = {};               // header numbers
const F = { q: '', band: 'all', source: 'all', status: 'new' };

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

/* ---------- boot ---------- */

window.addEventListener('pywebviewready', boot);

async function boot() {
  try {
    const [matches, stats, sources] = await Promise.all([
      pywebview.api.matches(400),
      pywebview.api.stats(),
      pywebview.api.sources(),
    ]);
    ALL = matches || [];
    STATS = stats || {};
    fillSources(sources || []);
    renderStats();
    wireFilters();
    render();
  } catch (e) {
    fail(e);
  }
}

function fail(e) {
  const box = $('empty');
  box.hidden = false;
  $('empty-msg').innerHTML = '<span class="err">Could not load matches:\n' +
    String(e && e.message ? e.message : e) + '</span>';
}

/* ---------- header ---------- */

function renderStats() {
  const s = STATS || {};
  const box = $('stats');
  box.textContent = '';
  if (s.matches == null) { box.appendChild(el('span', 'muted', 'no data yet — run a scan')); return; }
  box.appendChild(el('b', null, String(s.matches)));
  box.append(' matches · ');
  box.appendChild(el('span', 'gold', String(s.strong)));
  box.append(' strong');
  if (s.run && s.run.id != null) {
    box.append('  ·  ');
    box.appendChild(el('span', 'muted', 'run #' + s.run.id));
  }
}

function fillSources(list) {
  const sel = $('source');
  for (const s of list) {
    const o = document.createElement('option');
    o.value = s.source;
    o.textContent = s.source + ' (' + s.count + ')';
    sel.appendChild(o);
  }
}

/* ---------- filters ---------- */

function wireFilters() {
  $('q').addEventListener('input', (e) => { F.q = e.target.value.trim().toLowerCase(); render(); });
  $('source').addEventListener('change', (e) => { F.source = e.target.value; render(); });
  $('status').addEventListener('change', (e) => { F.status = e.target.value; render(); });

  for (const b of document.querySelectorAll('#band button')) {
    b.addEventListener('click', () => {
      for (const x of document.querySelectorAll('#band button')) x.classList.remove('on');
      b.classList.add('on');
      F.band = b.dataset.v;
      render();
    });
  }
}

function visible() {
  const strongTh = Number(STATS.strong_threshold ?? 10);
  const minSc = Number(STATS.min_score ?? 6);
  return ALL.filter((r) => {
    if (F.status !== 'all' && r.status !== F.status) return false;
    if (F.source !== 'all' && r.source !== F.source) return false;
    if (F.band === 'strong' && r.score < strongTh) return false;
    if (F.band === 'match' && r.score < minSc) return false;
    if (F.q) {
      const hay = ((r.title || '') + ' ' + (r.company || '') + ' ' +
                   (r.location || '') + ' ' + (r.source || '')).toLowerCase();
      if (!hay.includes(F.q)) return false;
    }
    return true;
  });
}

/* ---------- render ---------- */

function render() {
  const rows = visible();
  const grid = $('grid');
  grid.textContent = '';
  $('count').textContent = rows.length + ' shown';
  $('empty').hidden = rows.length > 0;

  const strongTh = Number(STATS.strong_threshold ?? 10);
  const frag = document.createDocumentFragment();
  for (const r of rows) frag.appendChild(card(r, strongTh));
  grid.appendChild(frag);

  $('footer-note').textContent = ALL.length + ' loaded · click a card to open the ad · ' +
    'S / A / X to triage';
}

function card(r, strongTh) {
  const c = el('div', 'card' + (r.status !== 'new' ? ' done' : ''));
  c.dataset.id = r.id;
  c.title = (r.reasons || []).map(([l, p]) => l + ' +' + p).join(' · ') || '';

  const top = el('div', 'top');
  top.appendChild(el('span', 'score' + (r.score >= strongTh ? ' strong' : ''), String(Math.round(r.score))));
  top.appendChild(el('span', 'chip', r.source));
  if (r.salary) top.appendChild(el('span', 'chip salary', r.salary));
  if (r.status && r.status !== 'new') {
    top.appendChild(el('span', 'chip st-' + r.status, r.status));
  }
  if ((r.verify_status || '') === 'dead') top.appendChild(el('span', 'chip', '⚠ dead'));
  c.appendChild(top);

  c.appendChild(el('div', 'title', r.title || '(untitled)'));

  const co = el('div', 'company');
  co.appendChild(document.createTextNode(r.company || '—'));
  if (r.location) {
    // real text, not a CSS ::before -- so it is announced and copyable
    co.append(' · ');
    co.appendChild(el('span', 'loc', r.location));
  }
  c.appendChild(co);

  const why = el('div', 'why');
  const reasons = (r.reasons || []).slice(0, 4);
  reasons.forEach(([l, p], i) => {
    if (i) why.append(' · ');
    why.appendChild(el('span', 'w', l));
    why.append(' +' + p);
  });
  if (reasons.length) c.appendChild(why);

  c.appendChild(actions(r, c));
  c.addEventListener('click', () => openPosting(r));
  return c;
}

function actions(r, cardEl) {
  const box = el('div', 'actions');
  const mk = (label, status) => {
    const b = el('button', r.status === status ? 'on' : '', label);
    b.addEventListener('click', (ev) => {
      ev.stopPropagation();
      setStatus(r, status === r.status ? 'new' : status, cardEl);
    });
    return b;
  };
  box.appendChild(mk('Save', 'saved'));
  box.appendChild(mk('Applied', 'applied'));
  box.appendChild(mk('Dismiss', 'dismissed'));
  return box;
}

async function setStatus(r, status, cardEl) {
  const prev = r.status;
  r.status = status;                       // optimistic
  try {
    await pywebview.api.mark(r.id, status);
  } catch (e) {
    r.status = prev;                       // roll back on failure
    console.error('mark failed', e);
  }
  // Refresh that one card, and re-run the filter if the active filter could
  // now exclude it (e.g. dismissing while filtered to "New").
  if (cardEl && cardEl.parentNode) {
    cardEl.parentNode.replaceChild(card(r, Number(STATS.strong_threshold ?? 10)), cardEl);
  }
  if (F.status !== 'all') render();
}

function openPosting(r) {
  if (r.url) pywebview.api.open_url(r.url);
}

/* keyboard: s / a / x triage the first visible card that is still "new".
 * The id lookup matters -- the first VISIBLE row is not always the first card
 * in the DOM, so querying '.card' would act on the wrong posting. */
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  const key = e.key.toLowerCase();
  if (!'sax'.includes(key)) return;
  const target = visible().find((r) => r.status === 'new');
  if (!target) return;
  const status = key === 's' ? 'saved' : key === 'a' ? 'applied' : 'dismissed';
  setStatus(target, status, document.querySelector('.card[data-id="' + target.id + '"]'));
});
