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
let CURRENT = null;           // posting shown in the detail pane
let POLL = null;              // scan poll timer

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
    wirePanes();
    render();
    checkForUpdates();          // async; never blocks the grid
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

async function reload() {
  const [matches, stats] = await Promise.all([
    pywebview.api.matches(400), pywebview.api.stats(),
  ]);
  ALL = matches || [];
  STATS = stats || {};
  renderStats();
  render();
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
    if (sel.querySelector('option[value="' + s.source + '"]')) continue;
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

  $('footer-note').textContent = ALL.length + ' loaded · click a card for details · ' +
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
  c.addEventListener('click', () => openDetail(r));
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

/* ---------- panes ---------- */

function wirePanes() {
  $('scrim').addEventListener('click', closePanes);
  $('detail-close').addEventListener('click', closePanes);
  $('settings-close').addEventListener('click', closePanes);
  $('detail-open').addEventListener('click', () => {
    if (CURRENT && CURRENT.url) pywebview.api.open_url(CURRENT.url);
  });
  $('detail-refresh').addEventListener('click', () => {
    if (CURRENT) openDetail(CURRENT, true);
  });
  $('settings-btn').addEventListener('click', openSettings);
  $('scan-btn').addEventListener('click', startScan);
  $('update-dismiss').addEventListener('click', () => { $('update-bar').hidden = true; });
  $('update-install').addEventListener('click', installUpdate);
  $('scan-cancel').addEventListener('click', () => pywebview.api.cancel_scan());

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closePanes();
  });
}

function closePanes() {
  $('detail').hidden = true;
  $('settings').hidden = true;
  $('scrim').hidden = true;
}

/* ---------- detail ---------- */

async function openDetail(r, refresh) {
  CURRENT = r;
  $('detail').hidden = false;
  $('settings').hidden = true;
  $('scrim').hidden = false;
  $('detail-title').textContent = r.title || '(untitled)';

  const body = $('detail-body');
  body.textContent = '';
  body.appendChild(el('p', 'loading', refresh ? 'Re-fetching the full text…' : 'Loading…'));

  let d = null;
  try {
    d = await pywebview.api.detail(r.id, !!refresh);
  } catch (e) {
    body.textContent = '';
    body.appendChild(el('p', 's-err', 'Could not load this posting: ' + String(e)));
    return;
  }
  renderDetail(d || r);
}

function renderDetail(d) {
  const body = $('detail-body');
  body.textContent = '';

  const dl = el('dl', 'd-meta');
  const put = (label, value) => {
    if (!value) return;
    dl.appendChild(el('dt', null, label));
    dl.appendChild(el('dd', null, String(value)));
  };
  put('Company', d.company);
  put('Location', d.location);
  put('Score', d.score != null ? Math.round(d.score) : null);
  put('Source', d.source);
  put('Posted', (d.posted_at || '').slice(0, 10));
  const sal = d.salary && (d.salary.text || d.salary.raw);
  put('Salary', sal);
  if (d.liveness && d.liveness.status) {
    put('Liveness', d.liveness.status +
      (d.liveness.note ? ' → ' + d.liveness.note : '') +
      (d.liveness.checked_at ? ' · checked ' + d.liveness.checked_at.slice(0, 10) : ''));
  }
  if (d.status) put('Status', d.status);
  body.appendChild(dl);

  const why = el('div', 'd-why');
  for (const [label, pts] of (d.reasons || [])) {
    why.appendChild(el('span', null, label + ' +' + pts));
  }
  if (why.childNodes.length) body.appendChild(why);

  const desc = el('div', 'd-desc');
  desc.textContent = d.description || '(no description available)';
  body.appendChild(desc);
}

/* ---------- settings ---------- */


async function openSettings() {
  $('settings').hidden = false;
  $('detail').hidden = true;
  $('scrim').hidden = false;
  const body = $('settings-body');
  body.textContent = '';
  body.appendChild(el('p', 'loading', 'Loading…'));

  let rows;
  try {
    rows = await pywebview.api.settings();
  } catch (e) {
    body.textContent = '';
    body.appendChild(el('p', 's-err', 'Could not read config.yaml: ' + String(e)));
    return;
  }
  body.textContent = '';

  // group by the `group` field, keeping the order api.SETTINGS declares
  const groups = new Map();
  for (const r of rows) {
    if (!groups.has(r.group)) groups.set(r.group, []);
    groups.get(r.group).push(r);
  }
  for (const [name, items] of groups) {
    const g = el('section', 's-group');
    g.appendChild(el('h3', null, name));
    for (const r of items) g.appendChild(settingRow(r));
    body.appendChild(g);
  }
}

function settingRow(r) {
  const wrap = el('div', 's-row');
  let timer = null;                 // per row, so one row cannot cancel another
  // Captured ONCE, before any save can overwrite it. The config's order is
  // meaningful ("sources run in this order"), and reading it back off r.value
  // each time loses it the moment a save writes an empty or partial list.
  const order = Array.isArray(r.value) ? r.value.slice() : [];
  const isBool = r.kind === 'bool';
  const label = el('label', null, r.label);
  label.htmlFor = 'set-' + r.key;
  wrap.appendChild(label);
  wrap.appendChild(el('p', 's-help', r.help));

  let input;
  let checkboxes = null;

  if (r.options) {
    // A list whose possible values the backend knows (job boards). Checkboxes
    // rather than a textarea: nobody should have to spell 'smartrecruiters'.
    checkboxes = [];
    const box = el('div', 's-choices');
    for (const opt of r.options) {
      const line = el('label', 's-choice');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !!opt.checked;
      cb.dataset.value = opt.value;
      line.appendChild(cb);
      line.appendChild(el('span', 's-choice-label', opt.label));
      if (opt.label !== opt.value) line.appendChild(el('span', 's-choice-key', opt.value));
      box.appendChild(line);
      checkboxes.push(cb);
    }
    wrap.appendChild(box);
    const tally = el('div', 's-tally');
    wrap.appendChild(tally);

    const updateTally = () => {
      const n = checkboxes.filter((c) => c.checked).length;
      tally.textContent = n + ' of ' + checkboxes.length + ' selected' +
        (n === 0 ? ' — nothing will be scanned' : '');
      tally.classList.toggle('warn', n === 0);
    };
    updateTally();
    for (const cb of checkboxes) cb.addEventListener('change', updateTally);
  } else if (isBool) {
    const line = el('div', 's-check');
    input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = r.value === true;
    line.appendChild(input);
    line.appendChild(document.createTextNode('enabled'));
    wrap.appendChild(line);
  } else if (r.kind === 'str_int_map') {
    // one `name: weight` per line -- matches how the config file reads
    input = document.createElement('textarea');
    input.value = Object.entries(r.value || {}).map(([k, v]) => k + ': ' + v).join('\n');
    input.spellcheck = false;
    wrap.appendChild(input);
  } else if (r.kind === 'str_list') {
    input = document.createElement('textarea');
    input.value = (r.value || []).join('\n');
    input.spellcheck = false;
    wrap.appendChild(input);
  } else {
    input = document.createElement('input');
    input.type = 'number';
    input.step = 'any';
    input.value = r.value != null ? r.value : '';
    wrap.appendChild(input);
  }
  if (input) input.id = 'set-' + r.key;

  const note = el('span', 's-saved', '');
  const err = el('div', 's-err', '');
  err.hidden = true;

  const collect = () => {
    if (checkboxes) {
      // The config says "sources run in this order", so keep it. Ranking
      // against the order captured at render time means the sequence survives
      // even an untick-everything-then-retick cycle, which a filter against the
      // current value cannot do: that information is already gone by then.
      const ticked = checkboxes.filter((c) => c.checked).map((c) => c.dataset.value);
      const rank = (v) => {
        const i = order.indexOf(v);
        return i === -1 ? order.length + ticked.indexOf(v) : i;
      };
      return ticked.slice().sort((a, b) => rank(a) - rank(b));
    }
    if (isBool) return input.checked;
    if (r.kind === 'str_list') {
      return input.value.split('\n').map((s) => s.trim()).filter(Boolean);
    }
    if (r.kind === 'str_int_map') {
      const out = {};
      for (const line of input.value.split('\n')) {
        const t = line.trim();
        if (!t) continue;
        const i = t.lastIndexOf(':');
        if (i < 0) throw new Error('expected  name: weight');
        const k = t.slice(0, i).trim();
        const v = Number(t.slice(i + 1).trim());
        if (!k) throw new Error('expected  name: weight');
        if (!isFinite(v)) throw new Error('weight for "' + k + '" is not a number');
        out[k] = v;
      }
      return out;
    }
    const v = Number(input.value);
    if (!isFinite(v)) throw new Error('not a number');
    return v;
  };

  const save = async () => {
    err.hidden = true;
    let value;
    try {
      value = collect();
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
      return;
    }
    try {
      const res = await pywebview.api.save_setting(r.key, value);
      r.value = value;
      note.textContent = res && res.changed ? 'saved' : '';
      if (note.textContent) setTimeout(() => { note.textContent = ''; }, 1600);
      // strictness changes rescore the list on next scan; refresh the header
      STATS = await pywebview.api.stats();
      renderStats();
    } catch (e) {
      err.textContent = String(e && e.message ? e.message : e);
      err.hidden = false;
    }
  };

  // Debounce everything that can fire in a burst. Without this, "untick all"
  // fired 25 writes back to back; each is a read-modify-write of the file, and
  // they raced each other into corrupting config.yaml.
  const saveSoon = (ms) => {
    clearTimeout(timer);
    timer = setTimeout(save, ms);
  };

  if (checkboxes) {
    for (const cb of checkboxes) cb.addEventListener('change', () => saveSoon(400));
  } else if (isBool) {
    input.addEventListener('change', () => saveSoon(200));
  } else {
    input.addEventListener('input', () => saveSoon(700));
    input.addEventListener('blur', () => {
      clearTimeout(timer);
      save();
    });
  }
  wrap.appendChild(note);
  wrap.appendChild(err);
  return wrap;
}

/* ---------- update ---------- */

function showUpdate(text) {
  const bar = $('update-bar');
  $('update-text').innerHTML = text;
  bar.hidden = false;
}

async function checkForUpdates(force) {
  let info;
  try {
    info = await pywebview.api.update_check(!!force);
  } catch (e) {
    return;                                   // a check must never break boot
  }
  if (!info || info.disabled || !info.ok) return;
  if (info.update_available) {
    const size = info.asset && info.asset.size
      ? ' (' + Math.round(info.asset.size / 1048576) + ' MB)' : '';
    showUpdate('<b>jobscout ' + info.latest + '</b> is available' +
      ' \u00b7 you have ' + info.current + size);
  }
}

async function installUpdate() {
  const btn = $('update-install');
  btn.disabled = true;
  btn.textContent = 'Downloading\u2026';
  $('update-text').textContent = 'checking the download\u2026';

  let res;
  try {
    res = await pywebview.api.update_install();
  } catch (e) {
    res = { ok: false, error: String(e) };
  }
  if (!res || !res.ok) {
    btn.disabled = false;
    btn.textContent = 'Install and restart';
    showUpdate('<span class="up-err">Update failed: ' +
      String((res && res.error) || 'unknown error') + '</span>');
    return;
  }
  // The helper waits for this process to exit before swapping the build, so
  // the window MUST close now -- holding it open blocks the update.
  btn.textContent = 'Restarting\u2026';
  showUpdate('Installing <b>' + (res.staged_version || 'the update') +
    '</b> \u2014 jobscout will restart itself.');
  setTimeout(() => pywebview.api.quit(), 900);
}

/* ---------- scan ---------- */

async function startScan() {
  const btn = $('scan-btn');
  btn.disabled = true;
  $('scan-bar').hidden = false;
  $('scan-label').textContent = 'starting…';
  $('scan-fill').style.width = '0';

  // Scan only what you are looking at: with a source selected, refresh just
  // that one instead of all 25. Cleared filter means scan everything.
  const only = F.source === 'all' ? '' : F.source;
  let res;
  try {
    res = await pywebview.api.start_scan(only);
  } catch (e) {
    $('scan-label').textContent = 'scan failed: ' + String(e);
    btn.disabled = false;
    return;
  }
  if (!res || !res.started) {
    $('scan-label').textContent = (res && res.reason) || 'could not start';
    btn.disabled = false;
    return;
  }
  clearInterval(POLL);
  POLL = setInterval(pollScan, 600);
}

async function pollScan() {
  let p;
  try {
    p = await pywebview.api.scan_progress();
  } catch (e) {
    clearInterval(POLL);
    $('scan-label').textContent = 'scan failed: ' + String(e);
    $('scan-btn').disabled = false;
    return;
  }
  for (const e of (p.events || [])) applyScanEvent(e);

  if (p.error) {
    clearInterval(POLL);
    $('scan-label').textContent = 'scan failed: ' + p.error;
    $('scan-btn').disabled = false;
    return;
  }
  if (!p.running) {
    clearInterval(POLL);
    const s = p.summary;
    if (s) {
      $('scan-fill').style.width = '100%';
      $('scan-label').textContent =
        s.new + ' new · ' + s.matches + ' matches this run' +
        (s.cancelled ? '  (cancelled early)' : '');
    }
    $('scan-btn').disabled = false;
    await reload();
    setTimeout(() => { $('scan-bar').hidden = true; }, 6000);
  }
}

function applyScanEvent(e) {
  if (e.kind === 'start') {
    $('scan-label').textContent = 'run #' + e.run_id + ' · ' + e.count + ' sources';
  } else if (e.kind === 'source_start') {
    $('scan-fill').style.width = Math.round((e.index / e.of) * 100) + '%';
    $('scan-label').textContent = 'fetching ' + e.name + ' (' + (e.index + 1) + '/' + e.of + ')';
  } else if (e.kind === 'source_done') {
    $('scan-fill').style.width = Math.round(((e.index + 1) / e.of) * 100) + '%';
    $('scan-label').textContent = e.name + ': ' + e.fetched + ' fetched · ' +
      e.new + ' new · ' + e.matches + ' matches';
  } else if (e.kind === 'rescored') {
    $('scan-label').textContent = 'profile changed — rescored ' + e.total + ' postings';
  } else if (e.kind === 'not_enabled') {
    $('scan-label').textContent = e.names.join(', ') + ' is not enabled — ' +
      'tick it in Settings';
  } else if (e.kind === 'cancelled') {
    $('scan-label').textContent = 'cancelling after ' + e.after + ' of ' + e.of + ' sources';
  }
}

/* keyboard: s / a / x triage the first visible card that is still "new".
 * The id lookup matters -- the first VISIBLE row is not always the first card
 * in the DOM, so querying '.card' would act on the wrong posting. */
document.addEventListener('keydown', (e) => {
  const tag = e.target.tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
  if ($('settings').hidden === false || $('detail').hidden === false) return;
  const key = e.key.toLowerCase();
  if (!'sax'.includes(key)) return;
  const target = visible().find((r) => r.status === 'new');
  if (!target) return;
  const status = key === 's' ? 'saved' : key === 'a' ? 'applied' : 'dismissed';
  setStatus(target, status, document.querySelector('.card[data-id="' + target.id + '"]'));
});
