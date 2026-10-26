/* jobscout front-end. A client of jobscout/api.py -- no rules live here.
 *
 * The bridge only exists after `pywebviewready`; calling pywebview.api before
 * that throws. Everything after that is plain DOM work over one loaded array,
 * so filtering never round-trips to Python.
 */
'use strict';

let ALL = [];                 // every match, unfiltered
let STATS = {};               // header numbers
/* "active" is the default rather than "new": once the logger exists, marking
 * something applied is the START of a relationship you are tracking, and a
 * board that hides it at that moment loses the role you care most about. */
const F = { q: '', band: 'all', source: 'all', status: 'active', fresh: false,
            text: false, closing: false };
let TEXT = null;              // Map of id -> matched context, when searching text
let TEXT_NOTE = '';           // what to say about the text search in the footer
let TEXT_TIMER = null;        // debounce for typing
let SETTINGS_ROWS = [];       // the last settings read, for the pay view's defaults
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
    loadAppsCount();            // the header badge, async and never blocking
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
  loadAppsCount();               // the badge, which must never block the grid
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
  $('closing').addEventListener('change', (e) => { F.closing = e.target.checked; render(); });

  for (const b of document.querySelectorAll('#band button')) {
    b.addEventListener('click', () => {
      for (const x of document.querySelectorAll('#band button')) x.classList.remove('on');
      b.classList.add('on');
      F.band = b.dataset.v;
      render();
    });
  }
}

/* "Closing soon": the advert states a deadline and it is close.

   The deadline is read from the full advert text by the scan, so this is the
   one filter whose results GROW over time without anything new being posted. */
function closingDays() {
  const d = Number(STATS.closing_days ?? 14);
  return isFinite(d) && d > 0 ? d : 14;
}

function closingSoon(r) {
  const days = r.closes_in_days;
  return days !== null && days !== undefined && days >= 0 && days <= closingDays();
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
    if (F.status === 'active' ? r.status === 'dismissed'
        : F.status !== 'all' && r.status !== F.status) return false;
    if (F.source !== 'all' && r.source !== F.source) return false;
    if (F.band === 'strong' && r.score < strongTh) return false;
    if (F.band === 'match' && r.score < minSc) return false;
    if (F.fresh && !freshFromSource(r)) return false;
    if (F.closing && !closingSoon(r)) return false;
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
  if (F.closing) {
    // soonest deadline first: with the filter on, the order IS the answer
    rows.sort((a, b) => (Number(a.closes_in_days ?? 1e9) - Number(b.closes_in_days ?? 1e9)));
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
  const nClosing = ALL.filter(closingSoon).length;
  $('closing-text').textContent = nClosing
    ? 'Closing soon (' + nClosing + ')'
    : 'Closing soon';

  // Never let a ceiling be silent: if the window was handed fewer rows than the
  // board holds, a filter can be hiding something without saying so.
  const total = Number(STATS.matches || 0);
  const cut = total > ALL.length;
  $('footer-note').textContent = (cut
    ? ALL.length + ' of ' + total + ' matches loaded \u00b7 raise \u201cHow many ' +
      'matches to load\u201d under Matching \u2192 Advanced \u00b7 '
    : ALL.length + ' loaded \u00b7 ') +
    (F.text && TEXT_NOTE ? TEXT_NOTE + ' in the advert text \u00b7 ' : '') +
    'click a card for details \u00b7 S/A/X triage';
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
    const c2 = el('span', 'chip also', (tw.parent ? 'first seen on ' : 'also on ') + tw.source);
    c2.title = 'Another board carried this role on ' + String(tw.seen_at || '').slice(0, 10) + '.';
    top.appendChild(c2);
  }
  // A stated deadline, worded once in Python. The tooltip carries the words it
  // came from, so a wrong one can be spotted rather than guessed at.
  if (r.deadline_text) {
    const gone = (r.closes_in_days ?? 0) < 0;
    const chip = el('span', 'chip deadline' + (closingSoon(r) ? ' soon' : '') + (gone ? ' gone' : ''),
                    r.deadline_text);
    chip.title = r.deadline_note
      ? 'From the advert: “' + r.deadline_note + '”'
      : 'Deadline stated in the advert.';
    top.appendChild(chip);
  }
  if (r.salary) top.appendChild(el('span', 'chip salary', r.salary));
  // The logger's chip below says "applied · interview" with the stage, which
  // is strictly more than the bare triage status. Showing both would print
  // "applied" twice on the same card.
  if (r.status && r.status !== 'new' && !(r.application && r.status === 'applied')) {
    top.appendChild(el('span', 'chip st-' + r.status, r.status));
  }
  // The logger's own state, so the board answers "what did I already send?"
  // without opening anything. The chip names the stage and, once it is over,
  // the outcome.
  if (r.application) {
    const stage = appStageLabel(r.application.stage || 'applied');
    const out = r.application.outcome ? appOutcomeLabel(r.application.outcome) : '';
    const chip = el('span', 'chip apply', out ? stage + ' · ' + out : stage);
    chip.title = 'You applied on ' + shortDate(r.application.applied_at) +
      (out ? ' · outcome: ' + out : '');
    top.appendChild(chip);
  }
  if ((r.verify_status || '') === 'dead') top.appendChild(el('span', 'chip', '⚠ dead'));
  // One canonical word ("permanent" / "fixed-term") for a fact that adverts
  // say five different ways; api.posting_dict decides it in Python, and the
  // phrase that matched stays in the reasons, visible in the "why" list.
  if (r.contract) top.appendChild(el('span', 'chip', r.contract));
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

/* The pay report: what the postings in this board advertise, per year.

   Every caveat the backend computes is shown, because a pay median is the
   easiest number here to mislead with: how many postings carry one at all, and
   which board they came from, are part of the answer. */
let PAY_TIMER = null;

function openPay() {
  $('pay').hidden = false;
  $('detail').hidden = true;
  $('settings').hidden = true;
  $('scrim').hidden = false;
  const rows = (SETTINGS_ROWS || []).find((r) => r.key === 'preferences.title_terms');
  if (!$('pay-words').value && rows && Array.isArray(rows.value)) {
    $('pay-words').value = rows.value.slice(0, 3).join(' ');
  }
  loadPay();
}

function wirePay() {
  // an ARRAY, not ('a', 'b'): a parenthesised pair is a comma expression, and
  // iterating it walks the string character by character, so $('#p') is null
  // and the whole pane silently fails to open
  for (const id of ['pay-words', 'pay-city']) {
    $(id).addEventListener('input', () => {
      clearTimeout(PAY_TIMER);
      PAY_TIMER = setTimeout(loadPay, 300);
    });
  }
  $('pay-btn').addEventListener('click', openPay);
  $('pay-close').addEventListener('click', closePanes);
}

async function loadPay() {
  const body = $('pay-body');
  body.textContent = '';
  body.appendChild(el('p', 'loading', 'Counting…'));
  let rep = null;
  try {
    rep = await pywebview.api.salary_report($('pay-words').value, $('pay-city').value);
  } catch (e) {
    body.textContent = '';
    body.appendChild(el('p', 's-err', 'Could not read the salaries: ' + String(e)));
    return;
  }
  body.textContent = '';
  renderPay(rep || {});
}

function money(cur, v) {
  const n = Number(v);
  if (!isFinite(n)) return '';
  return (cur ? cur + ' ' : '') + new Intl.NumberFormat().format(Math.round(n));
}

function renderPay(rep) {
  const body = $('pay-body');
  const cur = rep.currency || '';
  const asked = rep.asked || {};

  // What the report actually filtered on. With an empty box the backend uses
  // the profile's own job titles, and saying so (and filling the box) is the
  // difference between a default and a mystery.
  if (asked.words && asked.words.length && !$('pay-words').value) {
    $('pay-words').value = asked.words.join(' ');
  }
  body.appendChild(el('p', 's-help',
    'titles matching “' + ((asked.words || []).join(' ') || 'everything') + '”' +
    (asked.city ? ' in ' + asked.city : '')));

  if (!rep.with_salary) {
    body.appendChild(el('p', 's-help',
      'None of the ' + (rep.postings || 0) + ' postings in that set carry a figure. ' +
      'Only about a sixth of what is stored does, and mostly on the boards that ' +
      'require it, so try broader words.'));
    return;
  }

  const top = rep.max_annual || {};
  const few = rep.with_salary < 5;
  const big = el('p', 'pay-median');
  big.appendChild(el('b', null, money(cur, top.median)));
  big.append(' median advertised maximum, per year');
  body.appendChild(big);

  if (!few) {
    // a bar, because a range is easier to trust than three numbers
    const span = Math.max(1, top.max - top.min);
    const pct = (v) => Math.round(100 * (v - top.min) / span);
    const track = el('div', 'pay-track');
    const band = el('div', 'pay-band');
    band.style.left = pct(top.p25) + '%';
    band.style.width = Math.max(2, pct(top.p75) - pct(top.p25)) + '%';
    const mark = el('div', 'pay-mark');
    mark.style.left = pct(top.median) + '%';
    track.append(band, mark);
    body.appendChild(track);
    body.appendChild(el('p', 's-help',
      'half of them sit between ' + money(cur, top.p25) + ' and ' + money(cur, top.p75) +
      '  ·  lowest ' + money(cur, top.min) + ', highest ' + money(cur, top.max)));
    if (rep.min_annual) {
      body.appendChild(el('p', 's-help',
        'the bottom of the advertised bands, median: ' + money(cur, rep.min_annual.median)));
    }
  } else {
    body.appendChild(el('p', 's-help',
      'Only ' + rep.with_salary + ' posting' + (rep.with_salary === 1 ? '' : 's') +
      ' in that set ' + (rep.with_salary === 1 ? 'carries' : 'carry') +
      ' a figure, which is too few to describe a range. Try broader words.'));
  }

  body.appendChild(el('p', 's-help',
    rep.with_salary + ' of ' + rep.postings + ' postings in that set carry a number.'));
  const others = Object.keys(rep.currencies || {}).filter((c) => c !== cur);
  if (others.length) {
    body.appendChild(el('p', 's-help',
      'left out of these numbers, because mixing currencies is not a number: ' +
      others.map((c) => (c === '?' ? 'unlabelled' : c) + ' ' + rep.currencies[c])
        .join(', ')));
  }
  const srcs = rep.by_source || [];
  if (srcs.length) {
    const biggest = srcs[0];
    const share = Math.round(100 * biggest.count / rep.with_salary);
    body.appendChild(el('p', 's-help',
      'where they come from: ' + srcs.map((s) => s.source + ' ' + s.count).join(', ') +
      (share > 50 ? '  ·  ' + biggest.source + ' alone is ' + share +
        '% of them, so read this as what that board advertises' : '')));
  }
}

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
  $('apps-btn').addEventListener('click', openApps);
  $('apps-close').addEventListener('click', closePanes);
  wirePay();
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
  $('apps').hidden = true;
  $('pay').hidden = true;
  $('scrim').hidden = true;
}

/* ---------- detail ---------- */

async function openDetail(r, refresh) {
  $('pay').hidden = true;
  CURRENT = r;
  $('detail').hidden = false;
  $('settings').hidden = true;
  $('apps').hidden = true;
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
  put('Found on', d.source);
  put('Posted', (d.posted_at || '').slice(0, 10));
  // Where the vacancy comes from, and whether the head start is still there.
  // The card shows the short version; here it can be said in words.
  if (d.direct) {
    put('From', (d.direct_note || 'the employer\u2019s own board') +
      (freshFromSource(d) ? ' \u00b7 fresh, and no other board has it yet' : ''));
  }
  for (const tw of (d.republished || [])) {
    put(tw.parent ? 'First seen on' : 'Also on',
        tw.source + ' since ' + String(tw.seen_at || '').slice(0, 10));
  }
  const sal = d.salary && (d.salary.text || d.salary.raw);
  put('Salary', sal);
  if (d.liveness && d.liveness.status) {
    put('Liveness', d.liveness.status +
      (d.liveness.note ? ' → ' + d.liveness.note : '') +
      (d.liveness.checked_at ? ' · checked ' + d.liveness.checked_at.slice(0, 10) : ''));
  }
  if (d.deadline_text) {
    put('Deadline', d.deadline_text +
      (d.deadline_note ? '  ·  from the advert: “' + d.deadline_note + '”' : ''));
  }
  // No "Status" row here: the Application block below owns that fact now, and
  // two places saying it is how they end up disagreeing after a save.
  body.appendChild(dl);

  const why = el('div', 'd-why');
  for (const [label, pts] of (d.reasons || [])) {
    why.appendChild(el('span', null, label + ' +' + pts));
  }
  if (why.childNodes.length) body.appendChild(why);

  // Where you stand with this one, above the advert: the logger answers
  // "did I send this, and what came of it"; the description is the reference.
  const appSlot = el('div', 'app-slot');
  body.appendChild(appSlot);
  applicationBlock(d).then((node) => appSlot.replaceWith(node));

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

/* Six questions, one at a time, instead of one long scroll.
 *
 * The pane used to render every setting in a single column, which meant 23
 * rows, a wall of 51 board checkboxes and a paragraph of help under each one:
 * everything true, nothing findable. The rows still come from
 * `api.read_settings` (a setting cannot be missing here), but each one now
 * carries a `section`, so the window can show one question per pane and put
 * the rare knobs behind "Advanced".
 *
 * The board list is the part that needed the most work: 51 checkboxes at once
 * is not a choice, it is a chore. Boards are now cards under a market tab,
 * their per-board limits open only when the board is on, and the whole market
 * can be switched at once.
 */

const SET_SECTIONS = [
  { id: 'profile',  title: 'Profile',        hint: 'The workspace you are in' },
  { id: 'boards',   title: 'Job boards',     hint: 'Where jobscout looks' },
  { id: 'wanted',   title: 'What you want',  hint: 'Titles, skills, what to reject' },
  { id: 'where',    title: 'Where',          hint: 'Countries and places' },
  { id: 'matching', title: 'Matching',       hint: 'How strict, how much' },
  { id: 'updates',  title: 'Updates',        hint: 'Version and install' },
];

const SET_NOTE_DEFAULT = 'Changes save to config.yaml as you make them. ' +
  'Your comments in that file are kept.';

/* Units shown next to a number, so the help text does not have to be "In days." */
const SET_UNITS = {
  'preferences.min_score': 'points',
  'preferences.strong_threshold': 'points',
  'preferences.max_age_days': 'days',
  'preferences.closing_days': 'days',
  'preferences.insider_days': 'days',
  'preferences.scan_if_older_hours': 'hours',
  'preferences.max_results': 'matches',
  'preferences.query_budget': 'queries',
};

let WATCH = [];                       // employers followed, read with the pane
const SET = { rows: [], info: null, section: 'boards', query: '', market: 'all' };
const SET_TIMERS = new Map();         // per key, so one field cannot cancel another

function setNote(text, bad) {
  const n = $('settings-note');
  if (!n) return;
  n.textContent = text || SET_NOTE_DEFAULT;
  n.classList.toggle('warn', !!bad);
}

async function openSettings() {
  $('settings').hidden = false;
  $('detail').hidden = true;
  $('apps').hidden = true;
  $('pay').hidden = true;
  $('scrim').hidden = false;
  setNote('');
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
  SETTINGS_ROWS = rows;
  SET.rows = rows;
  SET.info = info;
  SET.query = '';
  renderSettings();
}

function renderSettings() {
  const body = $('settings-body');
  body.textContent = '';
  const shell = el('div', 'set');
  const rail = el('nav', 'set-rail');
  rail.setAttribute('aria-label', 'Settings sections');

  const search = document.createElement('input');
  search.type = 'search';
  search.className = 'set-search';
  search.placeholder = 'Search settings';
  search.setAttribute('aria-label', 'Search settings');
  search.value = SET.query;
  search.addEventListener('input', () => {
    SET.query = search.value.trim();
    paintPanes(panes, rail);
  });
  rail.appendChild(search);

  paintRail(rail);

  const foot = el('div', 'set-rail-foot');
  foot.appendChild(el('span', null, 'jobscout ' + ((SET.info && SET.info.version) || '')));
  rail.appendChild(foot);

  const panes = el('div', 'set-panes');
  shell.append(rail, panes);
  body.appendChild(shell);
  paintPanes(panes, rail);
}

function paintRail(rail) {
  // keep the search box; rebuild only the tabs
  for (const old of rail.querySelectorAll('.set-tab')) old.remove();
  const foot = rail.querySelector('.set-rail-foot');
  const [on, total] = boardCounts();
  for (const s of SET_SECTIONS) {
    const tab = el('button', 'set-tab');
    tab.type = 'button';
    tab.dataset.section = s.id;
    tab.title = s.hint;
    tab.appendChild(el('span', 'set-tab-name', s.title));
    if (s.id === 'boards') {
      tab.appendChild(el('span', 'set-badge' + (on === 0 ? ' warn' : ''), on + '/' + total));
    }
    if (s.id === SET.section && !SET.query) tab.classList.add('on');
    tab.addEventListener('click', () => {
      SET.section = s.id;
      SET.query = '';
      const box = rail.querySelector('.set-search');
      if (box) box.value = '';
      paintRail(rail);
      paintPanes(rail.parentNode.querySelector('.set-panes'), rail);
      const pane = rail.parentNode.querySelector('.set-panes');
      if (pane) pane.scrollTop = 0;
    });
    if (foot) rail.insertBefore(tab, foot); else rail.appendChild(tab);
  }
}

function paintPanes(panes, rail) {
  panes.textContent = '';
  if (SET.query) {
    panes.appendChild(searchResults());
    return;
  }
  const build = {
    profile: profilePane,
    boards: boardsPane,
    wanted: wantedPane,
    where: wherePane,
    matching: matchingPane,
    updates: updatesPane,
  }[SET.section] || boardsPane;
  panes.appendChild(build());
}

function boardRow() {
  return SET.rows.find((r) => r.key === 'sources.enabled');
}

function boardCounts() {
  const row = boardRow();
  if (!row || !row.options) return [0, 0];
  return [row.options.filter((o) => o.checked).length, row.options.length];
}

function rowsFor(section, advanced) {
  return SET.rows.filter((r) => !r.source_knob && r.section === section &&
    !!r.advanced === !!advanced);
}

/* ---------- panes ---------- */

function profilePane() {
  const wrap = el('section', 'set-pane-body');
  wrap.appendChild(el('h3', 'set-h3', 'Profile'));
  wrap.appendChild(el('p', 'set-lead',
    'Each profile is a separate workspace: its own settings, job boards and ' +
    'saved postings. Nothing is shared between them.'));

  const current = (PROFILES.profiles || []).find((p) => p.active);
  const card = el('div', 'set-card');
  card.appendChild(el('p', 'set-card-title', 'Using: ' + (current ? current.label : 'Default')));
  const btn = el('button', 'ghost small', 'Switch or create…');
  btn.type = 'button';
  btn.addEventListener('click', async () => {
    const st = await pywebview.api.launch();   // fresh list, same endpoint
    openPicker(st);
  });
  card.appendChild(btn);
  wrap.appendChild(card);
  return wrap;
}

function boardsPane() {
  const wrap = el('section', 'set-pane-body');
  const row = boardRow();
  if (!row) {
    wrap.appendChild(el('h3', 'set-h3', 'Job boards'));
    wrap.appendChild(el('p', 'set-lead', 'This config has no board list.'));
    return wrap;
  }
  const knobs = SET.rows.filter((r) => r.source_knob);
  const markets = [];
  for (const o of row.options) if (!markets.includes(o.group)) markets.push(o.group);

  wrap.appendChild(el('h3', 'set-h3', 'Job boards'));
  wrap.appendChild(el('p', 'set-lead',
    'A board you switch off keeps everything it already found — it just stops ' +
    'being scanned. Fewer boards is a faster scan.'));

  const tabs = el('div', 'set-markets');
  const list = el('div', 'set-boards');
  const foot = el('div', 'set-boardfoot');
  const tally = el('span', 'set-tally');
  const bulk = el('div', 'set-bulk');

  const visible = () => (SET.market === 'all'
    ? row.options
    : row.options.filter((o) => o.group === SET.market));

  const paintTabs = () => {
    tabs.textContent = '';
    const entries = [['all', 'All boards']].concat(markets.map((m) => [m, m]));
    for (const [id, label] of entries) {
      const opts = id === 'all' ? row.options : row.options.filter((o) => o.group === id);
      const on = opts.filter((o) => o.checked).length;
      const b = el('button', 'set-market' + (SET.market === id ? ' on' : ''));
      b.type = 'button';
      b.appendChild(el('span', null, label));
      b.appendChild(el('span', 'set-market-count', on + '/' + opts.length));
      b.addEventListener('click', () => { SET.market = id; paintTabs(); paintList(); });
      tabs.appendChild(b);
    }
  };

  const paintList = () => {
    list.textContent = '';
    for (const opt of visible()) list.appendChild(boardCard(opt, knobs, row, () => {
      paintTabs();
      paintFoot();
      paintRail(document.querySelector('.set-rail'));
    }));
    paintFoot();
  };

  const paintFoot = () => {
    const [on, total] = boardCounts();
    tally.textContent = on + ' of ' + total + ' boards on' +
      (on === 0 ? ' — nothing will be scanned' : '');
    tally.classList.toggle('warn', on === 0);
    bulk.textContent = '';
    if (SET.market === 'all') return;
    const opts = visible();
    const allOn = opts.every((o) => o.checked);
    const b = el('button', 'ghost small', allOn ? 'Switch this market off' : 'Switch this market on');
    b.type = 'button';
    b.addEventListener('click', () => {
      const target = !allOn;
      for (const o of opts) o.checked = target;
      paintList();
      paintTabs();
      paintRail(document.querySelector('.set-rail'));
      commitBoards(row);
    });
    bulk.appendChild(b);
  };

  paintTabs();
  paintList();
  wrap.append(tabs, list);
  foot.append(tally, bulk);
  wrap.appendChild(foot);

  wrap.appendChild(followBlock());
  return wrap;
}

function boardCard(opt, knobs, row, changed) {
  const card = el('div', 'board' + (opt.checked ? ' on' : ''));
  const head = el('label', 'board-head');
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = !!opt.checked;
  cb.setAttribute('aria-label', 'Scan ' + opt.label);
  cb.addEventListener('change', () => {
    opt.checked = cb.checked;
    card.classList.toggle('on', cb.checked);
    // the limits are only worth showing on a board that runs
    const box = card.querySelector('.board-knobs');
    if (box) box.hidden = !cb.checked;
    if (changed) changed();
    commitBoards(row);
  });
  head.appendChild(cb);
  head.appendChild(el('span', 'board-name', opt.label));
  // The config id is a tooltip, not a column: it is noise for everyone except
  // the person editing config.yaml, and it was squeezing long board names.
  if (opt.label.toLowerCase() !== opt.value) {
    card.title = opt.value;
  }
  card.appendChild(head);

  const mine = knobs.filter((k) => k.source === opt.value);
  if (mine.length) {
    const box = el('div', 'board-knobs');
    box.hidden = !opt.checked;
    for (const k of mine) box.appendChild(knobControl(k));
    card.appendChild(box);
  }
  return card;
}

function knobControl(k) {
  const line = el('label', 'knob');
  line.appendChild(el('span', 'knob-name', k.label));
  const input = document.createElement('input');
  input.type = 'number';
  input.min = '1';
  input.step = '1';
  input.value = k.value != null ? k.value : '';
  input.id = 'set-' + k.key;
  const err = el('span', 'field-err');
  err.hidden = true;
  input.addEventListener('change', () => {
    err.hidden = true;
    const v = Number(input.value);
    if (!isFinite(v)) {
      err.textContent = 'Not a number';
      err.hidden = false;
      return;
    }
    commitSoon(k, v, err);
  });
  line.appendChild(input);
  line.appendChild(err);
  return line;
}

/* The board list is one config key, so a burst of ticks is one write. The
 * sequence in the file is the scan order, which is why the selection keeps the
 * order captured when the pane opened rather than the order boxes were ticked. */
function commitBoards(row) {
  const ticked = row.options.filter((o) => o.checked).map((o) => o.value);
  const order = Array.isArray(row.value) ? row.value.slice() : [];
  const rank = (v) => {
    const i = order.indexOf(v);
    return i === -1 ? order.length + ticked.indexOf(v) : i;
  };
  commitSoon(row, ticked.slice().sort((a, b) => rank(a) - rank(b)));
}

function wantedPane() {
  const wrap = el('section', 'set-pane-body');
  wrap.appendChild(el('h3', 'set-h3', 'What you want'));
  wrap.appendChild(el('p', 'set-lead',
    'These three lists decide what is a match at all, and what rises to the top.'));
  for (const r of rowsFor('wanted', false)) wrap.appendChild(fieldRow(r));
  return wrap;
}

function wherePane() {
  const wrap = el('section', 'set-pane-body');
  wrap.appendChild(el('h3', 'set-h3', 'Where'));
  wrap.appendChild(el('p', 'set-lead',
    'Countries decide what is allowed; places decide what ranks higher. ' +
    'Leave both empty to look everywhere.'));
  for (const r of rowsFor('where', false)) wrap.appendChild(fieldRow(r));
  return wrap;
}

function matchingPane() {
  const wrap = el('section', 'set-pane-body');
  wrap.appendChild(el('h3', 'set-h3', 'Matching'));
  wrap.appendChild(el('p', 'set-lead',
    'How strict the board is, and how much it loads.'));

  const card = el('div', 'set-card');
  for (const r of rowsFor('matching', false)) card.appendChild(fieldRow(r, true));
  wrap.appendChild(card);

  const advRows = rowsFor('matching', true);
  if (advRows.length) {
    const det = document.createElement('details');
    det.className = 'set-adv';
    det.appendChild(el('summary', null, 'Advanced'));
    for (const r of advRows) det.appendChild(fieldRow(r, true));
    wrap.appendChild(det);
  }
  return wrap;
}

function updatesPane() {
  const wrap = el('section', 'set-pane-body');
  wrap.appendChild(el('h3', 'set-h3', 'Updates'));
  wrap.appendChild(el('p', 'set-lead',
    'jobscout asks GitHub, and never replaces a working build without a ' +
    'verified download.'));
  const card = el('div', 'set-card');
  for (const r of rowsFor('updates', false)) card.appendChild(fieldRow(r, true));
  wrap.appendChild(card);
  wrap.appendChild(checkNowRow(SET.info));
  return wrap;
}

/* ---------- one field ---------- */

const SET_UNIT_KEYS = new Set(Object.keys(SET_UNITS));

function fieldRow(r, compact) {
  const f = el('div', 'field' + (compact ? ' compact' : ''));
  const left = el('div', 'field-text');
  const label = el('label', 'field-label', r.label);
  label.htmlFor = 'set-' + r.key;
  left.appendChild(label);
  left.appendChild(el('p', 'field-help', r.help));
  const right = el('div', 'field-control');
  const err = el('div', 'field-err');
  err.hidden = true;

  let control;
  if (r.kind === 'bool') {
    control = switchControl(r, err);
  } else if (r.kind === 'str_int_map') {
    control = skillsControl(r, err);
  } else if (r.kind === 'str_list') {
    control = chipsControl(r, err);
  } else {
    control = numberControl(r, err);
  }
  right.appendChild(control);
  right.appendChild(err);
  f.append(left, right);
  return f;
}

function numberControl(r, err) {
  const box = el('div', 'num');
  const input = document.createElement('input');
  input.type = 'number';
  input.step = 'any';
  input.id = 'set-' + r.key;
  input.value = r.value != null ? r.value : '';
  input.addEventListener('input', () => {
    const v = Number(input.value);
    if (input.value.trim() !== '' && isFinite(v)) commitSoon(r, v, err);
  });
  input.addEventListener('change', () => {
    err.hidden = true;
    const v = Number(input.value);
    if (!isFinite(v)) {
      err.textContent = 'Not a number';
      err.hidden = false;
      return;
    }
    commitSoon(r, v, err, 0);
  });
  box.appendChild(input);
  const unit = SET_UNITS[r.key];
  if (unit) box.appendChild(el('span', 'num-unit', unit));
  return box;
}

function switchControl(r, err) {
  const line = el('label', 'switch');
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.id = 'set-' + r.key;
  input.checked = r.value === true;
  input.setAttribute('aria-label', r.label);
  const track = el('span', 'switch-track');
  line.append(input, track);
  input.addEventListener('change', () => commitSoon(r, input.checked, err, 0));
  return line;
}

/* A list as chips: typing a title and pressing Enter is one gesture, and the
 * current list stays readable at a glance instead of being a textarea of lines. */
function chipsControl(r, err) {
  let values = Array.isArray(r.value) ? r.value.slice() : [];
  const box = el('div', 'chips');
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'chip-input';
  input.id = 'set-' + r.key;
  input.placeholder = 'Add and press Enter';
  input.setAttribute('aria-label', 'Add to ' + r.label);

  const paint = () => {
    for (const old of box.querySelectorAll('.chip')) old.remove();
    for (const v of values) {
      const chip = el('span', 'chip');
      chip.appendChild(el('span', 'chip-text', v));
      const x = el('button', 'chip-x', '\u00d7');
      x.type = 'button';
      x.title = 'Remove ' + v;
      x.setAttribute('aria-label', 'Remove ' + v);
      x.addEventListener('click', () => {
        values = values.filter((item) => item !== v);
        paint();
        commitSoon(r, values.slice(), err);
      });
      chip.appendChild(x);
      box.insertBefore(chip, input);
    }
  };

  const add = (text) => {
    let changed = false;
    for (const piece of String(text).split(/[\n,]/)) {
      const v = piece.trim();
      if (v && !values.includes(v)) { values.push(v); changed = true; }
    }
    if (changed) {
      paint();
      commitSoon(r, values.slice(), err);
    }
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      add(input.value);
      input.value = '';
    } else if (e.key === 'Backspace' && !input.value && values.length) {
      values = values.slice(0, -1);
      paint();
      commitSoon(r, values.slice(), err);
    }
  });
  input.addEventListener('blur', () => {
    if (input.value.trim()) { add(input.value); input.value = ''; }
  });
  input.addEventListener('paste', (e) => {
    const text = (e.clipboardData || window.clipboardData).getData('text');
    if (/[\n,]/.test(text)) {
      e.preventDefault();
      add(text);
      input.value = '';
    }
  });
  box.appendChild(input);
  paint();
  return box;
}

/* Skills are a name and a weight, so they are rows rather than "name: 3" lines
 * nobody should have to spell. */
function skillsControl(r, err) {
  let entries = Object.entries(r.value || {});
  const box = el('div', 'skills');

  const commit = () => {
    const out = {};
    for (const [name, weight] of entries) if (name) out[name] = weight;
    commitSoon(r, out, err);
  };

  const paint = () => {
    box.textContent = '';
    for (const [name, weight] of entries) {
      const line = el('div', 'skill');
      const nameInput = document.createElement('input');
      nameInput.type = 'text';
      nameInput.value = name;
      nameInput.className = 'skill-name';
      nameInput.setAttribute('aria-label', 'Skill');
      nameInput.addEventListener('change', () => {
        const next = nameInput.value.trim();
        const i = entries.findIndex(([n]) => n === name);
        if (i >= 0) entries[i] = [next, weight];
        paint();
        commit();
      });
      const select = document.createElement('select');
      select.className = 'skill-w';
      select.setAttribute('aria-label', 'How much you want ' + name);
      for (const [value, label] of [[3, '3 · strong'], [2, '2 · good'], [1, '1 · nice']]) {
        const o = document.createElement('option');
        o.value = String(value);
        o.textContent = label;
        if (Number(weight) === value) o.selected = true;
        select.appendChild(o);
      }
      select.addEventListener('change', () => {
        const i = entries.findIndex(([n]) => n === name);
        if (i >= 0) entries[i] = [name, Number(select.value)];
        commit();
      });
      const x = el('button', 'chip-x', '\u00d7');
      x.type = 'button';
      x.title = 'Remove ' + name;
      x.addEventListener('click', () => {
        entries = entries.filter(([n]) => n !== name);
        paint();
        commit();
      });
      line.append(nameInput, select, x);
      box.appendChild(line);
    }
    const addLine = el('div', 'skill add');
    const addInput = document.createElement('input');
    addInput.type = 'text';
    addInput.placeholder = 'Add a skill';
    addInput.className = 'skill-name';
    addInput.setAttribute('aria-label', 'Add a skill');
    const addBtn = el('button', 'ghost small', 'Add');
    addBtn.type = 'button';
    const doAdd = () => {
      const v = addInput.value.trim();
      if (!v) return;
      if (!entries.some(([n]) => n === v)) entries.push([v, 2]);
      addInput.value = '';
      paint();
      commit();
    };
    addBtn.addEventListener('click', doAdd);
    addInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); doAdd(); }
    });
    addLine.append(addInput, addBtn);
    box.appendChild(addLine);
  };

  paint();
  return box;
}

/* ---------- saving ---------- */

function commitSoon(r, value, err, delay) {
  clearTimeout(SET_TIMERS.get(r.key));
  if (err) err.hidden = true;
  const wait = delay == null ? 500 : delay;
  SET_TIMERS.set(r.key, setTimeout(() => commitRow(r, value, err), wait));
}

async function commitRow(r, value, err) {
  try {
    const res = await pywebview.api.save_setting(r.key, value);
    r.value = value;
    setNote(res && res.changed ? 'Saved' : '');
    if (res && res.changed) setTimeout(() => setNote(''), 1400);
    // strictness changes rescore the list on next scan; the header must agree
    STATS = await pywebview.api.stats();
    renderStats();
  } catch (e) {
    setNote('Could not save ' + r.label + '.', true);
    if (err) {
      err.textContent = String(e && e.message ? e.message : e);
      err.hidden = false;
    }
  }
}

/* ---------- search ---------- */

function searchResults() {
  const q = SET.query.toLowerCase();
  const hits = SET.rows.filter((r) => {
    const hay = [r.label, r.help, r.key, r.group, r.source_label || ''].join(' ').toLowerCase();
    return hay.includes(q);
  });
  const wrap = el('section', 'set-pane-body');
  wrap.appendChild(el('h3', 'set-h3',
    hits.length + (hits.length === 1 ? ' setting matches ' : ' settings match ') +
    '\u201c' + SET.query + '\u201d'));
  if (!hits.length) {
    wrap.appendChild(el('p', 'set-lead', 'Try a word like score, boards, titles or updates.'));
    return wrap;
  }
  for (const r of hits) {
    if (r.key === 'sources.enabled') {
      const jump = el('button', 'set-jump');
      jump.type = 'button';
      const [on, total] = boardCounts();
      jump.textContent = 'Job boards \u2014 ' + on + ' of ' + total + ' on. Open them.';
      jump.addEventListener('click', () => {
        SET.section = 'boards';
        SET.query = '';
        renderSettings();
      });
      wrap.appendChild(jump);
      continue;
    }
    wrap.appendChild(fieldRow(r));
  }
  return wrap;
}

/* ---------- updates: asking now ---------- */

function checkNowRow(info) {
  const card = el('div', 'set-card');
  if (info && info.version) {
    card.appendChild(el('p', 'set-card-title',
      'You are running jobscout ' + info.version +
      (info.frozen ? '' : ' (a source checkout, which cannot install updates)')));
  }
  const line = el('div', 'set-check');
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
  line.append(btn, out);
  card.appendChild(line);
  card.appendChild(el('p', 'field-help', 'Asks GitHub now instead of waiting for the daily check.'));
  if (info && info.log) {
    card.appendChild(el('p', 'field-help', 'Every check is written to ' + info.log + '.'));
  }
  return card;
}

/* ---------- companies you follow ---------- */

/* Each board belongs to one employer and is their own hiring system, so a
 * vacancy is here when it is published and usually nowhere else for a while.
 * Adding one needs no account: the employer's public board URL is enough, and
 * it is validated against the board's own API before anything is written. */

function followBlock() {
  const sec = el('section', 'set-sub');
  sec.id = 'follow-group';
  sec.appendChild(el('h3', 'set-h3', 'Companies you follow'));
  sec.appendChild(el('p', 'set-lead',
    'Their own hiring system, so a role appears here the moment they publish ' +
    'it. Most never reach a big board at all.'));

  const lead = STATS.lead || {};
  if (lead.pairs && lead.comparable) {
    sec.appendChild(el('p', 'field-help',
      'Measured so far: ' + lead.pairs + ' role' + (lead.pairs === 1 ? '' : 's') +
      ' turned up on two boards. In ' + lead.comparable + ' of those one board had ' +
      'republished the other\u2019s advert: the employer\u2019s own board was first in ' +
      lead.direct_first + ', the republishing board in ' + lead.aggregator_first +
      (lead.median_days !== null && lead.median_days !== undefined
        ? ' (median gap ' + lead.median_days + ' days)' : '') + '.'));
  } else {
    sec.appendChild(el('p', 'field-help',
      'No role has turned up on two boards yet, so the head start is simply ' +
      'that these boards are the employer\u2019s own.'));
  }

  const add = el('div', 'set-check');
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
    say('Now following ' + res.token + ' on ' + res.board + '. It joins the next scan.', false);
    refreshFollow();
  });
  add.append(input, btn, note);
  sec.appendChild(add);

  for (const b of WATCH) {
    const det = document.createElement('details');
    det.className = 'w-board';
    det.appendChild(el('summary', null,
      b.label + ' \u00b7 ' + b.count + (b.count === 1 ? ' company' : ' companies') +
      (b.enabled ? '' : ' \u00b7 board switched off')));
    const box = el('div', 'w-tokens');
    if (!b.count) box.appendChild(el('span', 'field-help', 'none yet'));
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
    const failures = (s && s.errors) || [];
    if (s && s.fetched === 0 && failures.length) {
      // Not "no new postings": a scan that could not read a single page. It
      // must not look like a quiet day, and it must not disappear after six
      // seconds -- this is how the app lost five days of scans to a TLS bug
      // that the summary reported as "0 new".
      $('scan-fill').style.width = '100%';
      $('scan-bar').classList.add('scan-failed');
      $('scan-label').textContent =
        'scan failed \u2014 no board could be read (' + failures.length +
        (failures.length === 1 ? ' error' : ' errors') + '). First: ' + failures[0];
      $('scan-btn').disabled = false;
      await reload();
      return;
    }
    if (s) {
      $('scan-fill').style.width = '100%';
      $('scan-label').textContent =
        s.new + ' new · ' + s.matches + ' matches this run' +
        (((s.skipped_fresh || []).length)
          ? ' · ' + s.skipped_fresh.length + ' sources were fresh' : '') +
        (failures.length ? ' · ' + failures.length + ' sources failed' : '') +
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

/* ---------- the job logger ---------- */

/* An application is a fact about the person, not about the advert: the board
 * can withdraw the posting, re-publish it under a new id, or deduplicate it,
 * and the record of "I applied on the 12th, they replied on the 20th, it ended
 * in an offer" has to survive all of that. The backend keeps it in its own
 * tables; this file only draws it.
 *
 * Two places show it: a block at the bottom of the detail pane (where you log
 * one), and the Applications pane (where you read the whole search as a list).
 */

const APP_STAGES = [
  ['applied', 'Applied'],
  ['screening', 'Screening'],
  ['interview', 'Interview'],
  ['task', 'Task / case study'],
  ['offer', 'Offer'],
];
const APP_OUTCOMES = [
  ['', 'Still running'],
  ['hired', 'Offer accepted'],
  ['declined', 'Offer declined'],
  ['rejected', 'Rejected'],
  ['withdrawn', 'Withdrew'],
  ['no_reply', 'No reply'],
];

/* Where the application actually went. Deliberately NOT the board jobscout
 * found it on: most adverts end at the employer's own careers page, and logging
 * "linkedin" because that is where the advert was seen was simply wrong. The
 * board stays visible as "Found on". */
const APP_CHANNELS = [
  ['employer', "Employer\u2019s own site"],
  ['linkedin', 'LinkedIn'],
  ['xing', 'XING'],
  ['stepstone', 'StepStone'],
  ['email', 'Email'],
  ['referral', 'Referral'],
  ['recruiter', 'Recruiter'],
  ['other', 'Other'],
];

const appStageLabel = (v) => (APP_STAGES.find(([k]) => k === v) || [v, v])[1];
const appOutcomeLabel = (v) => (APP_OUTCOMES.find(([k]) => k === v) || [v, v])[1];
const appChannelLabel = (v) => (APP_CHANNELS.find(([k]) => k === v) || [v, v])[1];

function todayISO() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}

function shortDate(iso) {
  if (!iso) return '';
  const d = new Date(String(iso).slice(0, 10) + 'T00:00:00');
  if (isNaN(d)) return String(iso).slice(0, 10);
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

function relDay(iso) {
  if (!iso) return '';
  const then = new Date(String(iso).slice(0, 10) + 'T00:00:00');
  if (isNaN(then)) return '';
  const days = Math.round((new Date(todayISO() + 'T00:00:00') - then) / 86400000);
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 0) return 'in ' + (-days) + ' days';
  return days + ' days ago';
}

/* The state of an application, stated once and large. Everything here is also
 * in the fields below; this is the sentence you should be able to read from
 * across the room. */
function appBand(app) {
  const band = el('div', 'app-now');
  const col = el('div', 'app-now-text');

  // The stage belongs to the sentence, not to a separate column: floating to
  // the far edge of a 850px band read as an unrelated badge.
  const row = el('div', 'app-now-row');
  row.appendChild(el('span', 'app-now-title',
    'Applied ' + (relDay(app.applied_at) || 'on an unrecorded date')));
  row.appendChild(el('span', 'app-stage-pill s-' + (app.stage || 'applied'),
    appStageLabel(app.stage || 'applied')));
  if (app.outcome) {
    row.appendChild(el('span', 'app-stage-pill o-' + app.outcome,
      appOutcomeLabel(app.outcome)));
  }
  col.appendChild(row);

  const bits = [];
  if (app.applied_at) bits.push(shortDate(app.applied_at));
  bits.push('via ' + appChannelLabel(app.channel || 'employer'));
  if (app.follow_up_at) bits.push('follow-up ' + shortDate(app.follow_up_at));
  col.appendChild(el('span', 'app-now-sub', bits.join(' · ')));
  band.appendChild(col);
  return band;
}

function textInput(value, placeholder, aria) {
  const i = document.createElement('input');
  i.type = 'text';
  i.value = value || '';
  if (placeholder) i.placeholder = placeholder;
  if (aria) i.setAttribute('aria-label', aria);
  return i;
}

async function refreshAppBlock(d) {
  const old = document.getElementById('app-block');
  if (!old) return;
  const fresh = await applicationBlock(d);
  old.replaceWith(fresh);
}

/* The block in the detail pane. Everything saves on change: a logger with a
 * Save button loses the note you typed when you close the pane. */
async function applicationBlock(d) {
  const wrap = el('section', 'app-block');
  wrap.id = 'app-block';
  let app = {};
  try {
    app = (await pywebview.api.application(d.id)) || {};
  } catch (e) {
    wrap.appendChild(el('p', 's-err', 'Could not read the application log: ' + String(e)));
    return wrap;
  }
  const has = !!(app && app.id);
  const head = el('div', 'app-head');
  // "Not applied yet" describes the person; "No application logged" describes
  // the record, which is the only thing this window can actually know.
  head.appendChild(el('h3', 'app-h3', has ? 'Application' : 'No application logged'));
  wrap.appendChild(head);

  if (!has) {
    wrap.appendChild(el('p', 'field-help',
      'Logging an application records today’s date and the salary the advert ' +
      'stated, so the advert can change or vanish and your record still holds.'));
    // The label says what the button DOES. "I applied" asserted something
    // about the person before they had done it.
    const btn = el('button', 'app-cta', 'Log an application');
    btn.type = 'button';
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        await pywebview.api.save_application(d.id, {
          applied_at: todayISO(),
          // The employer's own site is what most applications are, whatever
          // board the advert was found on. The board is recorded separately.
          channel: 'employer',
          salary_advertised: (d.salary && (d.salary.text || d.salary.raw)) || '',
        });
        await refreshAppBlock(d);
        await reload();              // the card's chip must appear now, not later
        loadAppsCount();
      } catch (e) {
        wrap.appendChild(el('p', 's-err', 'Could not save: ' + String(e)));
      } finally {
        btn.disabled = false;
      }
    });
    wrap.appendChild(btn);
    return wrap;
  }

  wrap.appendChild(appBand(app));

  const grid = el('div', 'app-grid');
  const save = (fields) => pywebview.api.save_application(d.id, fields)
    .then(() => refreshAppBlock(d))
    .then(() => { reload(); loadAppsCount(); });

  const field = (label, control) => {
    const box = el('label', 'app-field');
    box.appendChild(el('span', 'app-label', label));
    box.appendChild(control);
    grid.appendChild(box);
  };

  const applied = document.createElement('input');
  applied.type = 'date';
  applied.value = app.applied_at || '';
  applied.addEventListener('change', () => save({ applied_at: applied.value }));
  field('Applied on', applied);

  const stage = document.createElement('select');
  for (const [v, t] of APP_STAGES) {
    const o = document.createElement('option');
    o.value = v; o.textContent = t;
    if (v === (app.stage || 'applied')) o.selected = true;
    stage.appendChild(o);
  }
  stage.addEventListener('change', () => save({ stage: stage.value }));
  field('Stage', stage);

  const outcome = document.createElement('select');
  for (const [v, t] of APP_OUTCOMES) {
    const o = document.createElement('option');
    o.value = v; o.textContent = t;
    if (v === (app.outcome || '')) o.selected = true;
    outcome.appendChild(o);
  }
  outcome.addEventListener('change', () => save({ outcome: outcome.value }));
  field('Outcome', outcome);

  const channel = document.createElement('select');
  const options = APP_CHANNELS.slice();
  if (app.channel && !options.some(([k]) => k === app.channel)) {
    options.push([app.channel, app.channel]);      // never lose an old value
  }
  for (const [v, t] of options) {
    const o = document.createElement('option');
    o.value = v; o.textContent = t;
    if (v === (app.channel || 'employer')) o.selected = true;
    channel.appendChild(o);
  }
  channel.addEventListener('change', () => save({ channel: channel.value }));
  field('Applied via', channel);

  const salary = textInput(app.salary_advertised || app.salary_from_posting,
                           'as the advert stated it', 'Salary advertised');
  salary.addEventListener('change', () => save({ salary_advertised: salary.value }));
  field('Advertised salary', salary);

  const contact = textInput(app.contact, 'recruiter, name, email…', 'Contact');
  contact.addEventListener('change', () => save({ contact: contact.value }));
  field('Contact', contact);

  const follow = document.createElement('input');
  follow.type = 'date';
  follow.value = app.follow_up_at || '';
  follow.addEventListener('change', () => save({ follow_up_at: follow.value }));
  field('Follow up on', follow);

  wrap.appendChild(grid);
  wrap.appendChild(el('p', 'field-help',
    'Found on ' + (d.source || 'an unknown board') +
    ' — that is where jobscout saw it. “Applied via” is where you actually sent it.'));

  const noteBox = el('div', 'app-addnote');
  const noteInput = textInput('', 'What was said, who said it, what happens next…',
                              'Add a note');
  const noteBtn = el('button', 'ghost small', 'Add note');
  noteBtn.type = 'button';
  const addNote = async () => {
    const text = noteInput.value.trim();
    if (!text) return;
    noteBtn.disabled = true;
    try {
      await pywebview.api.add_application_note(d.id, text);
      noteInput.value = '';
      await refreshAppBlock(d);
    } finally {
      noteBtn.disabled = false;
    }
  };
  noteBtn.addEventListener('click', addNote);
  noteInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addNote(); }
  });
  noteBox.append(noteInput, noteBtn);
  wrap.appendChild(noteBox);

  const timeline = el('ol', 'app-timeline');
  const events = (app.timeline || []).slice().reverse();   // newest first
  for (const e of events) {
    const li = el('li', 'app-event app-' + e.kind);
    li.appendChild(el('span', 'app-when', shortDate(e.at)));
    let text = e.detail || '';
    if (e.kind === 'stage') text = 'Moved to ' + appStageLabel(e.detail);
    else if (e.kind === 'outcome') text = 'Outcome: ' + appOutcomeLabel(e.detail);
    else if (e.kind === 'applied') {
      text = 'Applied' + (e.detail ? ' · via ' + appChannelLabel(e.detail) : '');
    } else if (e.kind === 'follow_up') text = 'Follow-up set for ' + shortDate(e.detail);
    li.appendChild(el('span', 'app-what', text));
    timeline.appendChild(li);
  }
  if (events.length) wrap.appendChild(timeline);

  // Reversible on purpose: a tracker you are afraid to touch is a tracker you
  // never use. This removes the log AND puts the posting back to New, so a
  // test on a real role leaves nothing behind.
  const kill = el('button', 'ghost small danger', 'Delete this log');
  kill.type = 'button';
  kill.title = 'Removes the application and its history, and puts the posting ' +
    'back to New. The posting itself stays in your board.';
  kill.addEventListener('click', async () => {
    if (!confirm('Delete this application log?\n\nThe posting stays in your board ' +
                 'and goes back to New, exactly as before you applied.')) return;
    await pywebview.api.delete_application(d.id);
    await refreshAppBlock(d);
    await reload();
    loadAppsCount();
  });
  wrap.appendChild(kill);
  return wrap;
}

/* ---------- the Applications pane ---------- */

let APPS_FILTER = 'open';

async function loadAppsCount() {
  try {
    const stats = await pywebview.api.application_stats();
    const btn = $('apps-btn');
    if (btn) {
      const n = (stats && stats.total) || 0;
      btn.textContent = n ? 'Applications ' + n : 'Applications';
      btn.classList.toggle('warn', !!(stats && stats.due));
      btn.title = n
        ? n + ' logged · ' + stats.open + ' still running' +
          (stats.due ? ' · ' + stats.due + ' follow-up' + (stats.due === 1 ? '' : 's') + ' due' : '')
        : 'Every application you logged, and what came of it';
    }
  } catch (e) { /* a count must never break the window */ }
}

async function openApps() {
  $('apps').hidden = false;
  $('detail').hidden = true;
  $('settings').hidden = true;
  $('pay').hidden = true;
  $('scrim').hidden = false;
  renderApps();
}

async function renderApps() {
  const body = $('apps-body');
  body.textContent = '';
  body.appendChild(el('p', 'loading', 'Loading…'));
  let rows = [];
  let stats = {};
  try {
    [rows, stats] = await Promise.all([
      pywebview.api.applications(false),
      pywebview.api.application_stats(),
    ]);
  } catch (e) {
    body.textContent = '';
    body.appendChild(el('p', 's-err', 'Could not read the application log: ' + String(e)));
    return;
  }
  body.textContent = '';

  const strip = el('div', 'apps-stats');
  const stat = (n, label, cls) => {
    const s = el('div', 'apps-stat' + (cls ? ' ' + cls : ''));
    s.appendChild(el('b', null, String(n)));
    s.appendChild(el('span', null, label));
    strip.appendChild(s);
  };
  stat(stats.total || 0, 'logged');
  stat(stats.open || 0, 'still running');
  stat(stats.offers || 0, 'offers');
  stat(stats.rejected || 0, 'rejected');
  stat((stats.response_rate || 0) + '%', 'ever replied');
  if (stats.median_reply_days != null) stat(stats.median_reply_days + 'd', 'median reply');
  body.appendChild(strip);

  if (!rows.length) {
    body.appendChild(el('p', 'set-lead',
      'Nothing logged yet. Open a posting you applied to and press “Log an application” — ' +
      'the date, the advertised salary and every stage after it stay here.'));
    return;
  }

  const tabs = el('div', 'set-markets');
  const counts = {
    all: rows.length,
    open: rows.filter((r) => !r.outcome).length,
    closed: rows.filter((r) => !!r.outcome).length,
  };
  for (const [id, label] of [['open', 'Still running'], ['closed', 'Finished'], ['all', 'All']]) {
    const b = el('button', 'set-market' + (APPS_FILTER === id ? ' on' : ''));
    b.type = 'button';
    b.appendChild(el('span', null, label));
    b.appendChild(el('span', 'set-market-count', String(counts[id])));
    b.addEventListener('click', () => { APPS_FILTER = id; renderApps(); });
    tabs.appendChild(b);
  }
  body.appendChild(tabs);

  const list = el('div', 'apps-list');
  const shown = rows.filter((r) => (APPS_FILTER === 'all' ? true
    : APPS_FILTER === 'open' ? !r.outcome : !!r.outcome));
  for (const r of shown) list.appendChild(appRow(r));
  body.appendChild(list);
  if (!shown.length) body.appendChild(el('p', 'set-lead', 'Nothing in this filter.'));
}

function appRow(r) {
  const row = el('div', 'app-row');
  row.tabIndex = 0;
  row.setAttribute('role', 'button');

  const main = el('div', 'app-row-main');
  main.appendChild(el('b', 'app-row-title', r.title || '(untitled)'));
  const sub = el('span', 'app-row-sub',
    (r.company || '—') + (r.location ? ' · ' + r.location : '') +
    (r.channel ? ' · via ' + appChannelLabel(r.channel) : ''));
  main.appendChild(sub);
  row.appendChild(main);

  const meta = el('div', 'app-row-meta');
  const stage = el('span', 'app-chip st-' + (r.stage || 'applied'), appStageLabel(r.stage));
  meta.appendChild(stage);
  if (r.outcome) {
    meta.appendChild(el('span', 'app-chip out-' + r.outcome, appOutcomeLabel(r.outcome)));
  }
  meta.appendChild(el('span', 'app-date', 'applied ' + shortDate(r.applied_at)));
  if (r.salary_advertised) {
    const s = el('span', 'app-sal', r.salary_advertised);
    s.title = 'What the advert stated when you logged it';
    meta.appendChild(s);
  }
  if (r.follow_up_at && !r.outcome) {
    const due = r.follow_up_at <= todayISO();
    const f = el('span', 'app-follow' + (due ? ' due' : ''),
                 'follow up ' + shortDate(r.follow_up_at));
    meta.appendChild(f);
  }
  row.appendChild(meta);

  const open = () => openDetail({ id: r.posting_id, title: r.title });
  row.addEventListener('click', open);
  row.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
  });
  return row;
}
