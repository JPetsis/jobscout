/* jobscout front-end. A client of jobscout/api.py -- no rules live here.
 *
 * The bridge only exists after `pywebviewready`; calling pywebview.api before
 * that throws. Everything after that is plain DOM work over one loaded array,
 * so filtering never round-trips to Python.
 */
'use strict';

let ALL = [];                 // every match, unfiltered
let STATS = {};               // header numbers
const F = { q: '', band: 'all', source: 'all', status: 'new', fresh: false, text: false };
let TEXT = null;              // Map of id -> matched context, when searching text
let TEXT_NOTE = '';           // what to say about the text search in the footer
let TEXT_TIMER = null;        // debounce for typing
let CURRENT = null;           // posting shown in the detail pane
let POLL = null;              // scan poll timer

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

/* ---------- theme ---------- */

const DARK_MQ = window.matchMedia('(prefers-color-scheme: dark)');
let THEME = 'system';                 // system | light | dark, as stored in config

function applyTheme(mode) {
  /* The resolver lives HERE rather than in a @media query, so styles.css keeps
     one copy of each palette instead of a dark block that has to be written
     twice (once for the media query, once for an explicit override) and then
     drifts. A theme that cannot be saved still applies: this is a display
     preference, not a command. */
  const dark = mode === 'dark' || (mode === 'system' && DARK_MQ.matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  for (const b of document.querySelectorAll('#theme button')) {
    b.classList.toggle('on', b.dataset.v === mode);
  }
}

function wireTheme(initial) {
  THEME = initial || 'system';
  applyTheme(THEME);
  for (const b of document.querySelectorAll('#theme button')) {
    b.addEventListener('click', async () => {
      const mode = b.dataset.v;
      const previous = THEME;
      THEME = mode;
      applyTheme(mode);                       // instant; do not wait for disk
      try {
        THEME = await pywebview.api.set_theme(mode);
      } catch (e) {
        THEME = previous;                     // did not save: show the truth
        applyTheme(previous);
      }
    });
  }
  // "system" keeps following the OS while the window is open
  DARK_MQ.addEventListener('change', () => {
    if (THEME === 'system') applyTheme('system');
  });
}

/* ---------- boot ---------- */

window.addEventListener('pywebviewready', boot);

async function boot() {
  try {
    const [matches, stats, sources, theme, launch] = await Promise.all([
      pywebview.api.matches(),
      pywebview.api.stats(),
      pywebview.api.sources(),
      pywebview.api.theme(),
      pywebview.api.launch(),
    ]);
    ALL = matches || [];
    STATS = stats || {};
    fillSources(sources || []);
    renderStats();
    wireTheme(theme);
    wireProfiles(launch);
    wireFilters();
    wirePanes();
    render();
    checkForUpdates();          // async; never blocks the grid
    maybeAutoScan();            // async; the backend decides whether it is due
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
    pywebview.api.matches(), pywebview.api.stats(),
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
    box.appendChild(el('span', 'muted', scanLine(s.run)));
  }
}

/* What the last look at the boards did, in words.

   A run number answers nothing: when it happened and what it brought in is the
   whole question for somebody coming back to the window after a day. */
function scanLine(run) {
  const when = timeAgo(run.finished_at || run.started_at);
  let stats = {};
  try { stats = JSON.parse(run.stats || '{}') || {}; } catch (e) { stats = {}; }
  const brought = (stats.new != null) ? ' · brought in ' + stats.new + ' new' : '';
  const partial = run.finished_at ? '' : ' (interrupted)';
  return 'last scan ' + when + brought + partial;
}

function timeAgo(iso) {
  if (!iso) return 'at an unknown time';
  const t = Date.parse(iso);
  if (!isFinite(t)) return 'at an unknown time';
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 90) return mins + ' min ago';
  const hours = Math.round(mins / 60);
  if (hours < 36) return hours + (hours === 1 ? ' hour ago' : ' hours ago');
  const days = Math.round(hours / 24);
  return days + (days === 1 ? ' day ago' : ' days ago');
}

/* Look for new openings when the window opens, when the last look is old.

   The decision belongs to the backend (preferences.scan_if_older_hours); this
   only drives the same bar the button drives, so an automatic scan is not a
   second kind of scan. It also never blocks the window: a scan that cannot
   start leaves a grid that still works. */
async function maybeAutoScan() {
  let res = null;
  try {
    res = await pywebview.api.boot_scan();
  } catch (e) {
    return;
  }
  if (!res || !res.started) {
    // say why not, on the button's tooltip, so "it did not scan" is answerable
    if (res && res.reason) $('scan-btn').title = 'Scan now (' + res.reason + ')';
    return;
  }
  $('scan-btn').disabled = true;
  $('scan-bar').hidden = false;
  $('scan-label').textContent = res.hours == null
    ? 'looking for new openings…'
    : 'looking for new openings (last one was ' + Math.round(res.hours) + 'h ago)…';
  $('scan-fill').style.width = '0';
  clearInterval(POLL);
  POLL = setInterval(pollScan, 600);
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

/* "Fresh from the source": the employer publishes into these boards itself, so
   a young posting there is the earliest public appearance -- and while no other
   board carries it, it is also the moment when applying costs the least. All
   three parts are data the API sends: r.direct, r.age_days, r.republished. */
function freshFromSource(r) {
  if (!r.direct) return false;
  const days = Number(STATS.insider_days ?? 7);
  if (r.age_days === null || r.age_days === undefined) return false;
  if (Number(r.age_days) > days) return false;
  return !(r.republished || []).length;
}

function fmtAge(days) {
  const d = Number(days);
  if (!isFinite(d)) return '';
  if (d < 1) return 'today';
  return Math.floor(d) + 'd';
}

function wireFilters() {
  $('q').addEventListener('input', (e) => {
    F.q = e.target.value.trim().toLowerCase();
    maybeTextSearch();          // debounced; only does work when switched on
    render();
  });
  $('qtext').addEventListener('change', (e) => {
    F.text = e.target.checked;
    maybeTextSearch();
    render();
  });
  $('source').addEventListener('change', (e) => { F.source = e.target.value; render(); });
  $('status').addEventListener('change', (e) => { F.status = e.target.value; render(); });
  $('fresh').addEventListener('change', (e) => { F.fresh = e.target.checked; render(); });

  for (const b of document.querySelectorAll('#band button')) {
    b.addEventListener('click', () => {
      for (const x of document.querySelectorAll('#band button')) x.classList.remove('on');
      b.classList.add('on');
      F.band = b.dataset.v;
      render();
    });
  }
}

/* Search inside the advert text.

   The text lives in the database, not in the page: 20k postings and tens of
   megabytes of prose, which is the part of this store nothing else could look
   inside. Debounced, because it is a round trip, and cancelled by switching the
   box off so the grid goes back to being entirely local. */
function maybeTextSearch() {
  clearTimeout(TEXT_TIMER);
  if (!F.text) { TEXT = null; TEXT_NOTE = ''; render(); return; }
  const q = $('q').value.trim();
  if (q.length < 2) { TEXT = new Map(); TEXT_NOTE = 'type at least two letters'; render(); return; }
  TEXT_TIMER = setTimeout(async () => {
    let res = null;
    try {
      res = await pywebview.api.search_text(q);
    } catch (e) {
      TEXT = new Map();
      TEXT_NOTE = 'the text search failed: ' + String(e);
      render();
      return;
    }
    const hits = (res && res.hits) || [];
    TEXT = new Map(hits.map((h) => [h.id, h.around || '']));
    TEXT_NOTE = res && res.limited
      ? 'the first ' + hits.length + ' postings that match'
      : hits.length + (hits.length === 1 ? ' posting' : ' postings') + ' match';
    render();
  }, 250);
}

function visible() {
  const strongTh = Number(STATS.strong_threshold ?? 10);
  const minSc = Number(STATS.min_score ?? 6);
  return ALL.filter((r) => {
    if (TEXT && !TEXT.has(r.id)) return false;
    if (F.status !== 'all' && r.status !== F.status) return false;
    if (F.source !== 'all' && r.source !== F.source) return false;
    if (F.band === 'strong' && r.score < strongTh) return false;
    if (F.band === 'match' && r.score < minSc) return false;
    if (F.fresh && !freshFromSource(r)) return false;
    if (F.q) {
      const hay = ((r.title || '') + ' ' + (r.company || '') + ' ' +
                   (r.location || '') + ' ' + (r.source || '')).toLowerCase();
      if (!hay.includes(F.q)) return false;
    }
    return true;
  });
}

function render() {
  const rows = visible();
  const grid = $('grid');
  grid.textContent = '';
  $('count').textContent = rows.length + ' shown';
  $('empty').hidden = rows.length > 0;
  $('empty-msg').textContent = (F.text && TEXT && TEXT.size === 0)
    ? 'Nothing in the advert text matches that.'
    : 'No matches.';

  // The freshness view is about being early, so it sorts by how recent it is
  // rather than by score: the best match is worth less by the time it is a week
  // old. Every other view keeps score order.
  if (F.fresh) {
    rows.sort((a, b) => (Number(a.age_days ?? 1e9) - Number(b.age_days ?? 1e9)));
  }

  const strongTh = Number(STATS.strong_threshold ?? 10);
  const frag = document.createDocumentFragment();
  for (const r of rows) frag.appendChild(card(r, strongTh));
  grid.appendChild(frag);

  // say how many there are, so the switch is worth flipping before you flip it
  const nFresh = ALL.filter(freshFromSource).length;
  $('fresh-text').textContent = nFresh
    ? 'Fresh from the source (' + nFresh + ')'
    : 'Fresh from the source';

  // Never let a ceiling be silent: if the window was handed fewer rows than the
  // board holds, a filter can be hiding something without saying so.
  const total = Number(STATS.matches || 0);
  const cut = total > ALL.length;
  $('footer-note').textContent = (cut
    ? ALL.length + ' of ' + total + ' matches loaded \u00b7 raise \u201cHow many results ' +
      'to load\u201d in Settings to see the rest \u00b7 '
    : ALL.length + ' loaded \u00b7 ') +
    (F.text && TEXT_NOTE ? TEXT_NOTE + ' in the advert text \u00b7 ' : '') +
    'click a card for details \u00b7 S / A / X to triage';
}

function card(r, strongTh) {
  const c = el('div', 'card' + (r.status !== 'new' ? ' done' : ''));
  c.dataset.id = r.id;
  c.title = (r.reasons || []).map(([l, p]) => l + ' +' + p).join(' · ') || '';

  const top = el('div', 'top');
  top.appendChild(el('span', 'score' + (r.score >= strongTh ? ' strong' : ''), String(Math.round(r.score))));
  top.appendChild(el('span', 'chip', r.source));
  // Where the vacancy comes from, and how young. "fresh" is the badge that
  // means something: the employer's own board, recent, nobody republishing it.
  if (r.direct) {
    const isFresh = freshFromSource(r);
    const age = (r.age_days === null || r.age_days === undefined) ? '' : ' · ' + fmtAge(r.age_days);
    const chip = el('span', 'chip direct' + (isFresh ? ' fresh' : ''),
                    (isFresh ? 'fresh from the source' : 'direct') + age);
    chip.title = isFresh
      ? 'Fresh from the source: posted on ' + (r.direct_note || 'the employer\u2019s own board') +
        '. No other board has carried it yet.'
      : 'Posted on ' + (r.direct_note || 'the employer\u2019s own board') + ', but no longer new.';
    top.appendChild(chip);
  }
  // A republisher picked it up. Worth showing rather than hiding: it is the
  // clock running out on the head start, and the date says how fast.
  for (const tw of (r.republished || [])) {
    const c2 = el('span', 'chip also', 'also on ' + tw.source);
    c2.title = 'Another board carried this role on ' + String(tw.seen_at || '').slice(0, 10) + '.';
    top.appendChild(c2);
  }
  if (r.salary) top.appendChild(el('span', 'chip salary', r.salary));
  if (r.status && r.status !== 'new') {
    top.appendChild(el('span', 'chip st-' + r.status, r.status));
  }
  if ((r.verify_status || '') === 'dead') top.appendChild(el('span', 'chip', '⚠ dead'));
  const cmark = (r.reasons || []).map(x => String(x[0])).find(l => l.startsWith('perm:') || l.startsWith('tmp:'));
  if (cmark) top.appendChild(el('span', 'chip', cmark.slice(5)));
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

  // When a text search put this card here, show the words that did it: the
  // reason to believe a result is the result, not the ranking.
  if (TEXT && TEXT.has(r.id)) {
    const around = String(TEXT.get(r.id) || '').replace(/\s+/g, ' ').trim();
    if (around) c.appendChild(el('div', 'snippet', around));
  }

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
  $('detail').addEventListener('click', (ev) => {
    // only the backdrop, not the card: a click inside must not close it
    if (ev.target === $('detail')) closePanes();
  });
  $('detail-open').addEventListener('click', () => {
    if (CURRENT && CURRENT.url) pywebview.api.open_url(CURRENT.url);
  });
  $('detail-refresh').addEventListener('click', () => {
    if (CURRENT) openDetail(CURRENT, true);
  });
  $('settings-btn').addEventListener('click', openSettings);
  $('scan-btn').addEventListener('click', startScan);
  $('update-dismiss').addEventListener('click', () => { $('update-bar').hidden = true; });
  // dismissing keeps a verified download on disk; the next check re-offers it
  $('update-install').addEventListener('click', onUpdateClick);
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
  // Where the vacancy comes from, and whether the head start is still there.
  // The card shows the short version; here it can be said in words.
  if (d.direct) {
    put('From', (d.direct_note || 'the employer\u2019s own board') +
      (freshFromSource(d) ? ' \u00b7 fresh, and no other board has it yet' : ''));
  }
  for (const tw of (d.republished || [])) {
    put('Also on', tw.source + ' since ' + String(tw.seen_at || '').slice(0, 10));
  }
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

  if (d.description) {
    body.appendChild(renderDescription(d.description));
  } else {
    // Never an empty box: the backend says WHY there is nothing (withdrawn,
    // no link, or the board publishes no text), which are very different
    // answers to "should I keep looking at this?".
    body.appendChild(el('p', 'd-note',
      d.description_note || 'No description available for this listing.'));
  }
}

/* ---------- the description ---------- */

/* The extractors keep line breaks (clean_html_lines), so the text arrives with
   real paragraph and list structure. Turn that into elements. Everything is
   built with textContent/createTextNode: this text comes from job boards and
   is NOT trusted. */

const BULLET = /^\s*(?:[-*•·–—]|\d+[.)])\s+\S/;
/* Where one block of prose ends. The two shapes in the wild differ in how they
   use a bare newline: XING/ashby WRAP a sentence across lines, so a line not
   ending in punctuation continues the paragraph; HN puts one whole thought on
   each line with no blank line between them, so a completed sentence ends the
   paragraph. Joining everything makes a blob; breaking on every newline shreds
   a wrapped sentence into fragments. */
const SENTENCE_END = /[.!?:]["')\]]?$/;
const BARE_LINK = /^(?:https?:\/\/|www\.)\S+$/i;

function appendInline(node, text) {
  // Bare URLs in an advert are usually the application link, so make them
  // clickable -- through the same open_url the "Open ad" button uses.
  // the parens matter: the loop reads m[1], so this needs a capture group
  const re = /(https?:\/\/[^\s<>()\[\]"']+)/g;
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    let url = m[1];
    let tail = '';
    while (url && '.,;:!?'.includes(url[url.length - 1])) {
      tail = url[url.length - 1] + tail;
      url = url.slice(0, -1);
    }
    if (m.index > last) node.appendChild(document.createTextNode(text.slice(last, m.index)));
    if (url) {
      const a = el('a', null, url);
      a.href = '#';
      a.title = 'Open in your browser';
      a.addEventListener('click', (ev) => {
        ev.preventDefault();
        pywebview.api.open_url(url);
      });
      node.appendChild(a);
    } else {
      node.appendChild(document.createTextNode(m[1]));
    }
    if (tail) node.appendChild(document.createTextNode(tail));
    last = m.index + m[1].length;
  }
  if (last < text.length) node.appendChild(document.createTextNode(text.slice(last)));
}

/* Presentation-only fallback. Some boards ship the whole advert as ONE enormous
   paragraph -- measured: 2,975 characters with no line break at all from
   recruitee -- and no amount of CSS makes that readable. Splitting it at
   sentence boundaries changes no wording; it only stops a wall of text. A
   paragraph already under the threshold is left exactly as the source wrote it. */
const LONG_PARA = 620;
const PARA_TARGET = 420;

function splitSentences(text) {
  const parts = text.match(/[^.!?]+(?:[.!?]+["')\]]*\s*|$)/g) || [text];
  const out = [];
  let cur = '';
  for (const part of parts) {
    if (cur && (cur + part).length > PARA_TARGET) {
      out.push(cur.trim());
      cur = '';
    }
    cur += part;
  }
  if (cur.trim()) out.push(cur.trim());
  return out.length ? out : [text];
}

function pushParagraph(wrap, text) {
  for (const chunk of (text.length > LONG_PARA ? splitSentences(text) : [text])) {
    const p = el('p');
    appendInline(p, chunk);
    wrap.appendChild(p);
  }
}

function renderDescription(raw) {
  const wrap = el('div', 'd-desc');
  const lines = String(raw).split('\n');
  let para = [];
  let list = null;

  const flushPara = () => {
    if (!para.length) return;
    pushParagraph(wrap, para.join(' '));
    para = [];
  };
  const flushList = () => {
    if (!list) return;
    wrap.appendChild(list);
    list = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushPara();
      flushList();
      continue;
    }
    if (BULLET.test(line)) {
      flushPara();
      if (!list) list = el('ul');
      const li = el('li');
      appendInline(li, line.replace(/^\s*(?:[-*•·–—]|\d+[.)])\s+/, ''));
      list.appendChild(li);
      continue;
    }
    flushList();
    // A short line ending in a colon is a section label ("Your tasks:"), not a
    // sentence. Length-capped so a real sentence that happens to end in a colon
    // is not promoted to a heading.
    if (line.length <= 72 && line.endsWith(':') && !line.startsWith('http')) {
      flushPara();
      wrap.appendChild(el('h4', null, line.replace(/:$/, '')));
      continue;
    }
    para.push(line);
    if (SENTENCE_END.test(line) || BARE_LINK.test(line)) flushPara();
  }
  flushPara();
  flushList();
  return wrap;
}

/* ---------- profiles ---------- */

let PROFILES = { profiles: [], active: '' };

function pickerError(msg) {
  const e = $('picker-err');
  e.textContent = msg;
  e.hidden = false;
}

function openPicker(state) {
  PROFILES = state || PROFILES;
  const list = $('picker-list');
  list.textContent = '';
  for (const p of PROFILES.profiles || []) {
    const b = el('button', 'picker-item');
    b.type = 'button';
    if (p.active) b.setAttribute('aria-current', 'true');
    b.appendChild(el('span', 'pi-name', p.label));
    // "empty" covers both no database at all and a database with nothing in
    // it: to the person looking, those are the same thing.
    const n = p.postings;
    b.appendChild(el('span', 'pi-meta',
      Number.isInteger(n) && n > 0 ? n.toLocaleString() + ' postings'
                                   : 'no postings yet'));
    b.addEventListener('click', () => chooseProfile(p));
    list.appendChild(b);
  }
  const current = (PROFILES.profiles || []).find((p) => p.active);
  const cancel = $('picker-cancel');
  cancel.textContent = current ? 'Continue with ' + current.label : 'Close';
  cancel.hidden = false;
  $('picker-err').hidden = true;
  $('picker-name').value = '';
  $('picker').hidden = false;
  // Focus the CURRENT row, not the first available one. Focusing a different
  // row puts a second highlighted-looking row on screen, and the user cannot
  // tell which one is in use -- reported from a render of this dialog.
  const focus = list.querySelector('.picker-item[aria-current="true"]')
             || list.querySelector('.picker-item');
  if (focus) focus.focus();
}

function closePicker() { $('picker').hidden = true; }

async function chooseProfile(p) {
  if (p.active) { closePicker(); return; }   // already here: nothing to switch
  let out;
  try {
    out = await pywebview.api.switch_profile(p.name);
  } catch (e) {
    return pickerError(String(e));
  }
  if (!out || !out.ok) return pickerError((out && out.error) || 'could not switch');
  // Reload rather than patching the page: the whole view is derived from the
  // workspace, so a fresh boot is both simpler and guaranteed consistent.
  window.location.reload();
}

async function createProfile(name) {
  let out;
  try {
    out = await pywebview.api.create_profile(name, true);
  } catch (e) {
    return pickerError(String(e));
  }
  if (!out || !out.ok) return pickerError((out && out.error) || 'could not create it');
  window.location.reload();
}

function wireProfiles(launch) {
  $('picker-new').addEventListener('submit', (ev) => {
    ev.preventDefault();
    const name = $('picker-name').value.trim();
    if (!name) return pickerError('a profile needs a name');
    createProfile(name);
  });
  $('picker-cancel').addEventListener('click', closePicker);
  if (launch && launch.ask) openPicker(launch);
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
  let info = null;
  try {
    [rows, info] = await Promise.all([
      pywebview.api.settings(), pywebview.api.app_info(),
    ]);
  } catch (e) {
    body.textContent = '';
    body.appendChild(el('p', 's-err', 'Could not read config.yaml: ' + String(e)));
    return;
  }
  // The employers this profile follows: fetched separately so a failure there
  // cannot take the whole pane down with it.
  try {
    WATCH = await pywebview.api.watchlist();
  } catch (e) {
    WATCH = [];
  }
  body.textContent = '';
  body.appendChild(profileBlock());

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
    // The two switches govern what the app does ON ITS OWN. This is the other
    // half: asking right now, which is a different question and works even
    // when they are both off.
    if (items.some((r) => r.key.startsWith('updates.'))) {
      g.appendChild(checkNowRow(info));
    }
    body.appendChild(g);
    // the companies you follow sit with the question they answer: where to look
    if (name === 'Where to look') body.appendChild(followBlock());
  }
}

function checkNowRow(info) {
  const row = el('div', 's-row');
  if (info && info.version) {
    // Which build you are on, without a terminal and without a network call.
    row.appendChild(el('p', 's-help',
      'You are running jobscout ' + info.version +
      (info.frozen ? '' : ' (a source checkout, which cannot install updates)')));
  }
  const line = el('div', 's-check');
  const btn = el('button', 'ghost small', 'Check now');
  btn.type = 'button';
  const out = el('span', 'up-note', '');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    out.className = 'up-note';
    out.textContent = 'checking\u2026';
    const res = await checkForUpdates(true);   // true = skip the cache
    btn.disabled = false;
    if (!res || res.error) {
      out.className = 'up-note up-err';
      out.textContent = 'Could not check: ' +
        String((res && res.error) || 'unknown error');
    } else if (!res.ok) {
      out.className = 'up-note up-err';
      out.textContent = 'Could not check for updates.';
    } else if (res.update_available) {
      out.textContent = 'jobscout ' + res.latest + ' is available';
    } else {
      out.textContent = 'Up to date (jobscout ' + res.current + ').';
    }
  });
  line.appendChild(btn);
  line.appendChild(out);
  row.appendChild(line);
  row.appendChild(el('p', 's-help',
    'Asks GitHub now instead of waiting for the daily check.'));
  if (info && info.log) {
    row.appendChild(el('p', 's-help',
      'Every check and what came of it is written to ' + info.log + '.'));
  }
  return row;
}

/* The per-company boards: the employers this profile follows. This is where the
   "before everyone else" position actually comes from -- each board belongs to
   one employer and is their own hiring system, so a vacancy is here when it is
   published and usually nowhere else for a while. Adding one needs no account:
   the employer's public board URL is enough, and it is validated against the
   board's own API before anything is written to the config. */
let WATCH = [];

function followBlock() {
  const sec = el('section', 's-group');
  sec.id = 'follow-group';
  sec.appendChild(el('h3', null, 'Companies you follow'));
  sec.appendChild(el('p', 's-help',
    'One employer each, and it is their own system, so a vacancy appears here ' +
    'the moment they publish it. Most of these roles never reach a big board ' +
    'at all. Paste a careers page to follow one more.'));

  // The app checking its own claim, in numbers rather than adjectives. It is
  // thin data and it says so: what makes it mean more is more scans, and this
  // line is the only thing that will ever show whether the head start is real.
  const lead = STATS.lead || {};
  if (lead.pairs && lead.comparable) {
    sec.appendChild(el('p', 's-help',
      'Measured so far: ' + lead.pairs + ' role' + (lead.pairs === 1 ? '' : 's') +
      ' turned up on two boards. In ' + lead.comparable + ' of those one board had ' +
      'republished the other\u2019s advert: the employer\u2019s own board was first in ' +
      lead.direct_first + ', the republishing board in ' + lead.aggregator_first +
      (lead.median_days !== null && lead.median_days !== undefined
        ? ' (median gap ' + lead.median_days + ' days)' : '') +
      '. A small sample says little yet, and it only grows with regular scans.'));
  } else {
    sec.appendChild(el('p', 's-help',
      'No role has turned up on two boards yet, so nothing here is measured: ' +
      'the head start is simply that these boards are the employer\u2019s own, and ' +
      'a board that republishes somebody else\u2019s advert is a step behind them.'));
  }

  const add = el('div', 's-check');
  const input = el('input', 'w-url');
  input.type = 'text';
  input.placeholder = 'https://company.com/careers';
  input.setAttribute('aria-label', 'Careers page or job listing URL');
  const btn = el('button', 'ghost small', 'Follow');
  btn.type = 'button';
  const note = el('span', 'up-note', '');
  const say = (text, bad) => {
    note.className = bad ? 'up-note up-err' : 'up-note';
    note.textContent = text;
  };
  btn.addEventListener('click', async () => {
    const url = (input.value || '').trim();
    if (!url) { say('Paste a careers page URL first.', true); return; }
    btn.disabled = true;
    say('looking for a job board behind that page\u2026', false);
    let res = null;
    try {
      res = await pywebview.api.watch_add(url);
    } catch (e) {
      res = { ok: false, error: String(e) };
    }
    btn.disabled = false;
    if (!res || !res.ok) {
      say(String((res && (res.hint || res.error)) || 'Could not read that page.'), true);
      return;
    }
    input.value = '';
    WATCH = await pywebview.api.watchlist();
    say('Now following ' + res.token + ' on ' + res.board + '. ' +
        'It joins the next scan.', false);
    refreshFollow();
  });
  add.append(input, btn, note);
  sec.appendChild(add);

  for (const b of WATCH) {
    const det = el('details', 'w-board');
    det.appendChild(el('summary', null,
      b.label + ' \u00b7 ' + b.count + (b.count === 1 ? ' company' : ' companies') +
      (b.enabled ? '' : ' \u00b7 board switched off')));
    const box = el('div', 'w-tokens');
    if (!b.count) {
      box.appendChild(el('span', 's-help', 'none yet'));
    }
    for (const t of b.tokens) {
      const chip = el('span', 'w-token');
      chip.appendChild(el('span', null, t));
      const x = el('button', 'ghost small w-x', '\u00d7');
      x.type = 'button';
      x.title = 'Stop following ' + t;
      x.setAttribute('aria-label', 'Stop following ' + t);
      x.addEventListener('click', async () => {
        const res = await pywebview.api.watch_remove(b.source, t);
        if (res && res.ok) {
          WATCH = await pywebview.api.watchlist();
          refreshFollow();
          const back = document.querySelector('#follow-group .up-note');
          if (back) {
            back.textContent = (res.status === 'missing')
              ? t + ' was not in the list any more.'
              : 'Stopped following ' + t + '.';
          }
        } else {
          say('Could not remove ' + t + '.', true);
        }
      });
      chip.appendChild(x);
      box.appendChild(chip);
    }
    det.appendChild(box);
    sec.appendChild(det);
  }
  return sec;
}

function refreshFollow() {
  const old = document.getElementById('follow-group');
  if (old) old.replaceWith(followBlock());
}

function profileBlock() {
  /* Which workspace you are in, and the way to a different one. Reuses the
     pane's own classes, and opens the same picker the app asks with at launch
     rather than a second, differently-behaving profile UI. */
  const sec = el('section', 's-group');
  sec.appendChild(el('h3', null, 'Profile'));
  const row = el('div', 's-row');
  const current = (PROFILES.profiles || []).find((p) => p.active);
  const label = el('label', null, 'Using: ' + (current ? current.label : 'Default'));
  row.appendChild(label);
  row.appendChild(el('p', 's-help',
    'Each profile is a separate workspace: its own settings, job boards and ' +
    'saved postings. Nothing is shared between them.'));
  const line = el('div', 's-check');
  const btn = el('button', 'ghost small', 'Switch or create…');
  btn.type = 'button';
  btn.addEventListener('click', async () => {
    const st = await pywebview.api.launch();   // fresh list, same endpoint
    openPicker(st);
  });
  line.appendChild(btn);
  row.appendChild(line);
  sec.appendChild(row);
  return sec;
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
    //
    // Grouped by the market each board serves, so "which of these are for
    // Germany" is answerable without knowing 47 board names. The order here is
    // display only -- the write below uses the `order` snapshot taken above, so
    // grouping can never rewrite the user's scan sequence.
    checkboxes = [];
    const byGroup = new Map();
    for (const opt of r.options) {
      const g = opt.group || 'Other';
      if (!byGroup.has(g)) byGroup.set(g, []);
      byGroup.get(g).push(opt);
    }
    for (const [group, opts] of byGroup) {
      const sec = el('div', 's-boardgroup');
      const head = el('div', 's-boardgroup-head');
      head.appendChild(el('span', 's-boardgroup-name', group));
      const on = opts.filter((o) => o.checked).length;
      head.appendChild(el('span', 's-boardgroup-count', on + '/' + opts.length));
      if (on === 0) head.classList.add('warn');
      sec.appendChild(head);

      const box = el('div', 's-choices');
      for (const opt of opts) {
        const line = el('label', 's-choice');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = !!opt.checked;
        cb.dataset.value = opt.value;
        cb.dataset.group = group;
        line.appendChild(cb);
        line.appendChild(el('span', 's-choice-label', opt.label));
        if (opt.label !== opt.value) line.appendChild(el('span', 's-choice-key', opt.value));
        box.appendChild(line);
        checkboxes.push(cb);
      }
      sec.appendChild(box);
      wrap.appendChild(sec);
    }
    const tally = el('div', 's-tally');
    wrap.appendChild(tally);

    const updateTally = () => {
      const n = checkboxes.filter((c) => c.checked).length;
      tally.textContent = n + ' of ' + checkboxes.length + ' selected' +
        (n === 0 ? ' — nothing will be scanned' : '');
      tally.classList.toggle('warn', n === 0);
      // and the per-group counts, so turning a whole market off is visible
      for (const head of wrap.querySelectorAll('.s-boardgroup-head')) {
        const box = head.nextElementSibling;
        const boxes = [...box.querySelectorAll('input[type=checkbox]')];
        const on = boxes.filter((c) => c.checked).length;
        head.querySelector('.s-boardgroup-count').textContent = on + '/' + boxes.length;
        head.classList.toggle('warn', on === 0);
      }
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

let STAGED = null;         // set once an update is downloaded and verified

async function checkForUpdates(force) {
  let info;
  try {
    info = await pywebview.api.update_check(!!force);
  } catch (e) {
    return null;                              // a check must never break boot
  }
  if (!info || info.disabled || !info.ok) return info || null;
  if (!info.update_available) return info;
  const size = info.asset && info.asset.size
    ? ' (' + Math.round(info.asset.size / 1048576) + ' MB)' : '';
  const blurb = '<b>jobscout ' + info.latest + '</b> is available' +
    ' \u00b7 you have ' + info.current + size;
  if (info.auto) { await autoInstall(info, blurb); return info; }
  showUpdate(blurb);
  return info;
}

/* Install without asking, because the setting says to.
 *
 * Deliberately at startup: that is the one moment nothing is half-done. The
 * window has just opened and the user has not begun anything, so a restart
 * cannot interrupt work. Nothing replaces the installed build until the
 * download has verified, and the swap keeps the previous build until it has
 * succeeded -- so every way this can fail ends with a working copy in place. */
async function autoInstall(info, blurb) {
  const btn = $('update-install');
  const dismiss = $('update-dismiss');
  btn.hidden = true;
  dismiss.hidden = true;
  showUpdate('Updating to <b>jobscout ' + info.latest + '</b> \u2014 ' +
    'downloading and verifying\u2026');

  let res;
  try {
    res = await pywebview.api.update_install();
  } catch (e) {
    res = { ok: false, error: String(e) };
  }
  if (!res || !res.ok || !res.staged) {
    // Hand it back as a normal offer instead of leaving a dead bar: an
    // automatic update that could not happen must still be startable by hand.
    btn.hidden = false;
    dismiss.hidden = false;
    btn.disabled = false;
    btn.textContent = 'Try again';
    showUpdate('<span class="up-err">Automatic update failed: ' +
      String((res && res.error) || 'unknown error') + '</span> \u00b7 ' + blurb);
    return;
  }
  STAGED = res.staged_version || info.latest;
  await quitToUpdate();
}

/* Two steps on purpose for the MANUAL path. Downloading is safe while the app
 * runs; INSTALLING means terminating jobscout, and that is the user's call to
 * make explicitly, not a side effect of pressing a button. `Not now` keeps the
 * verified copy, so coming back does not re-download it. The automatic path
 * skips both steps because the setting asked for that -- it runs only at
 * startup, and says in the bar what it is doing. */
async function onUpdateClick() {
  if (STAGED) { await quitToUpdate(); return; }
  await downloadUpdate();
}

async function downloadUpdate() {
  const btn = $('update-install');
  btn.disabled = true;
  btn.textContent = 'Downloading\u2026';
  $('update-text').textContent = 'downloading and verifying\u2026';

  let res;
  try {
    res = await pywebview.api.update_install();
  } catch (e) {
    res = { ok: false, error: String(e) };
  }
  if (!res || !res.ok) {
    btn.disabled = false;
    btn.textContent = 'Try again';
    showUpdate('<span class="up-err">Update failed: ' +
      String((res && res.error) || 'unknown error') + '</span>');
    return;
  }
  if (!res.staged) {                       // nothing to install after all
    btn.disabled = false;
    btn.textContent = 'Check again';
    showUpdate('jobscout is already up to date.');
    return;
  }
  offerQuit(res.staged_version || 'the update');
}

function offerQuit(version) {
  STAGED = version;
  const btn = $('update-install');
  btn.disabled = false;
  btn.textContent = 'Quit and update';
  btn.classList.add('primary');
  $('update-dismiss').textContent = 'Not now';
  $('update-text').innerHTML =
    '<b>jobscout ' + version + '</b> is downloaded and verified. ' +
    'jobscout has to quit so it can be replaced \u2014 it will reopen by itself.';
}

async function quitToUpdate() {
  const btn = $('update-install');
  btn.disabled = true;
  btn.textContent = 'Restarting\u2026';
  $('update-text').innerHTML = 'Closing jobscout and installing <b>' +
    STAGED + '</b>\u2026';
  let res;
  try {
    res = await pywebview.api.update_quit();     // closes this window
  } catch (e) {
    res = { ok: false, error: String(e) };
  }
  // It is still here, so the quit did NOT happen. Before this check the bar
  // kept saying "closing jobscout and installing" forever, which is how an
  // update that silently did nothing looked exactly like one in progress.
  if (res && res.ok === false) {
    btn.disabled = false;
    btn.textContent = 'Try again';
    $('update-dismiss').hidden = false;
    showUpdate('<span class="up-err">Could not install the update: ' +
      String(res.error || 'unknown error') + '</span>');
  }
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
