"""Salary parsing: pull pay ranges out of compensation fields and descriptions.

Heuristic but deterministic: ranges and numbers are grouped per currency, then
the currency mentioned first wins. No FX conversion happens — the currency is
stored and shown, numbers are compared as-is when filtering.
"""
from __future__ import annotations

import re

_SYMBOL = {"EUR": "€", "USD": "$", "GBP": "£", "PLN": "PLN ", "CHF": "CHF "}

_CUR_RE = re.compile(
    r"€|\$|£|zł|\b(?:EUR|USD|GBP|PLN|CHF|CAD|AUD|SGD|INR|MXN|SEK|NOK|DKK|BRL|JPY|"
    r"CZK|HUF|RON|ZAR|NZD)\b", re.I)
_NUM = r"(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?)[kK]?"
_NUM_RE = re.compile(_NUM)
_RANGE_RE = re.compile(rf"(?P<a>{_NUM})\s*(?:-|–|—|\bto\b|\bbis\b)\s*(?P<b>{_NUM})", re.I)
_CURRENCIES = {"€": "EUR", "eur": "EUR", "$": "USD", "usd": "USD", "us$": "USD",
               "£": "GBP", "gbp": "GBP", "zł": "PLN", "pln": "PLN", "chf": "CHF",
               # explicit ISO codes from global sources (no symbols needed:
               # fmt() falls back to "<CODE> " when a currency has no symbol)
               "cad": "CAD", "aud": "AUD", "sgd": "SGD", "inr": "INR", "mxn": "MXN",
               "sek": "SEK", "nok": "NOK", "dkk": "DKK", "brl": "BRL", "jpy": "JPY",
               "czk": "CZK", "huf": "HUF", "ron": "RON", "zar": "ZAR", "nzd": "NZD"}

_PERIOD_RES = [
    ("hour", re.compile(r"(?:/\s?h\b|/\s?hr\b|per\s+hour|hourly|pro\s+stunde|stündlich|/hour)", re.I)),
    ("day", re.compile(r"(?:/\s?day|per\s+day|daily|pro\s+tag|täglich|tagessatz|day\s+rate)", re.I)),
    ("month", re.compile(r"(?:/\s?mo\b|/\s?month|per\s+month|monthly|pro\s+monat|monatlich|monatsgehalt)", re.I)),
    ("year", re.compile(r"(?:/\s?(?:yr|year|ja)\b|per\s+year|per\s+annum|p\.\s?a\.|annual|yearly|jährlich|jahresgehalt)", re.I)),
]
_HINT_RE = re.compile(
    r"(salary|compensation|gehalt|remuneration|vergütung|lohn|entgelt|"
    r"we\s+pay|pay\s+range|brutto|\brate\b|\bpay\b|\bgross\b)", re.I)
_RETIREMENT_RE = re.compile(r"40[13]\s?\(?[kKbB]\)?")   # 401k / 403(b) are plans, not pay
# digits split by a thousands space: "15 600" / "27 000" / "4 500"
_SPACED_NUM_RE = re.compile(r"(?<=\d)[ \u00a0\u202f](?=\d{3}(?!\d))")
# money mentioned in a clearly non-pay context (revenue, funding, budgets)
_NONPAY_RE = re.compile(
    r"(revenue|turnover|\bgmv\b|transacting|raised|funding|budget for|valuation|"
    r"invest(?:ment|ors|ing)?|portfolio|gross margin|arr\b|savings)", re.I)
_PAYWORD_RE = re.compile(
    r"(salary|compensation|gehalt|remuneration|vergütung|we\s+pay|pay\s+range|"
    r"brutto|\brate\b|lohn|entgelt|\bpay\b|ote\b|per year|per hour|per month|"
    r"monatlich|jährlich|annual|hourly|salary range)", re.I)
# Money that belongs to a BUDGET, not to the salary: "€30 monthly mobility
# budget", "€1,000 annual training budget", "an annual development budget of
# €2,000". An advert that publishes no salary at all still lists these, and
# grouping every EUR amount in the text turned two of them into a 30-1000 EUR
# monthly "range" (the Gymondo advert, which says "Salary band: shared before
# your first interview").
#
# The word has to sit NEXT TO the amount: within three words after it, or
# before it as "budget of €". A wide window is how this rule first went wrong --
# matching the plain word "benefit" within 60 characters cleared 753 real
# salaries, because "£80k + benefits" and "benefits of our compensation package"
# are pay. Only the budget families qualify, and a pay word next to the amount
# still wins: "the salary budget for this role is €70,000" is pay.
_BUDGET_WORD = (r"(?:budget|weiterbild\w*|fortbild\w*|bildungsbudget|"
                r"entwicklungsbudget|trainingsbudget)")
_BUDGET_BEFORE = re.compile(_BUDGET_WORD + r"\s*(?:of|von|:|-|\u2013)?\s*$", re.I)
_BUDGET_AFTER = re.compile(r"^\s*(?:\w+[\s,]+){0,2}?" + _BUDGET_WORD, re.I)

# above this, a currency-less number only counts when the word is unambiguous pay
_STRONG_HINT_RE = re.compile(
    r"(salary|gehalt|compensation|remuneration|vergütung|lohn|entgelt|brutto|"
    r"\bote\b|\bearnings?\b|\bearn\b|base\s+pay|total\s+pay|pay\s+range|salary\s+range)",
    re.I)

_ANNUAL = {"hour": 2080.0, "day": 260.0, "month": 12.0, "year": 1.0}
# upper sanity bound per period; anything above is a parse mistake, not pay
_MAX = {"hour": 500.0, "day": 3000.0, "month": 60000.0, "year": 1_000_000.0}
# ...but those bounds are EUR-scale. Currencies with smaller units carry larger
# numbers for a normal salary, so their ceiling gets a multiplier. This is a
# sanity bound, not a conversion: amounts are never transformed.
_MAX_MULT = {"JPY": 40.0, "INR": 15.0, "HUF": 50.0, "MXN": 5.0, "CZK": 3.0,
             "RON": 2.0, "ZAR": 3.0, "BRL": 2.0, "SEK": 1.5, "NOK": 1.5, "DKK": 1.5}

# heuristics when no explicit period marker exists
_ASSUME_YEAR_MIN = 20000.0         # above this, a bare number is almost certainly annual
_ASSUME_MONTH = (1500.0, 12000.0)  # plausible monthly gross range (DE/AT/CH-ish)


def _to_number(tok: str) -> float | None:
    t = tok.strip().lower()
    mult = 1000.0 if t.endswith("k") else 1.0
    if mult != 1.0:
        t = t[:-1]
    if not t:
        return None
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", t):
        t = re.sub(r"[.,]", "", t)      # 60,000 / 4.363 / 1,234,567
    else:
        t = t.replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return v * mult if v > 0 else None


def _currency_of(tok: str) -> str:
    return _CURRENCIES.get(tok.lower(), "")


def _unit_currency(text: str, start: int, end: int, span: int = 16) -> str:
    """Currency attached to a number/range: one right after it wins over one before."""
    after_best = after_d = None
    before_best = before_d = None
    for m in _CUR_RE.finditer(text):
        code = _currency_of(m.group(0))
        if m.start() >= end:
            d = m.start() - end
            if d <= span and (after_d is None or d < after_d):
                after_best, after_d = code, d
        elif m.end() <= start:
            d = start - m.end()
            if d <= span and (before_d is None or d < before_d):
                before_best, before_d = code, d
    return after_best or before_best or ""


def _units(text: str) -> list[tuple[int, int, float, float, str]]:
    """Numbers and ranges as (start, end, lo, hi, currency) units."""
    units: list[tuple[int, int, float, float, str]] = []
    spans: list[tuple[int, int]] = []
    for m in _RANGE_RE.finditer(text):
        a_raw, b_raw = m.group("a"), m.group("b")
        a, b = _to_number(a_raw), _to_number(b_raw)
        if a is None or b is None:
            continue
        if not a_raw.lower().endswith("k") and b_raw.lower().endswith("k") and a * 1000 <= b:
            a *= 1000                    # "60-100k" means 60k-100k
        lo, hi = min(a, b), max(a, b)
        if hi > 10_000_000:
            continue                     # ids, timestamps - not pay
        cur = _unit_currency(text, m.start(), m.end(), span=16)
        if not cur and 1900 <= lo and hi <= 2100 \
                and not a_raw.lower().endswith("k") and not b_raw.lower().endswith("k"):
            continue                     # "2024-2026" is a year range, not pay
        spans.append((m.start(), m.end()))
        units.append((m.start(), m.end(), lo, hi, cur))
    for m in _NUM_RE.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue                     # already inside a range
        if _RETIREMENT_RE.match(text[m.start(): m.end() + 2]):
            continue                     # "401(k)" / "401k" retirement plans
        v = _to_number(m.group())
        if v is None:
            continue
        has_k = m.group().lower().endswith("k")
        if not has_k and 1900 <= v <= 2100:
            continue                     # a bare year, not pay
        if v > 10_000_000:
            continue                     # an id, a timestamp, a funding round
        cur = _unit_currency(text, m.start(), m.end(), span=6)
        if not cur and not has_k and v < 1000:
            continue                     # noise like "40h", "5+ years"
        if not cur and v > 1_000_000:
            continue                     # a bare number this big is never pay
        if cur and v < 10:
            continue                     # "$1.6" style noise sitting next to a symbol
        units.append((m.start(), m.end(), v, v, cur))
    return units


def _is_benefit(text: str, start: int, end: int) -> bool:
    """True when the money is budget money rather than pay.

    Both sides are read, because "€30 monthly mobility budget" puts the word
    after the number and "development budget of €2,000" puts it before. The pay
    word check comes first so a "salary budget" still counts as pay.
    """
    before = text[max(0, start - 45): start]
    hit = _BUDGET_BEFORE.search(before)
    if hit:
        # "salary budget of €70,000" is pay even though it says budget; the
        # word in front of "budget" is what decides, not the distance to any
        # pay word, because "Salary €60,000 ... €1,000 training budget" has
        # "salary" 30 characters away from a training budget and would keep it.
        head = before[:hit.start()]
        if re.search(r"(?:salary|gehalt|verg\u00fctung|compensation|pay)s?[\s:,-]*$", head, re.I):
            return False
        return True
    return bool(_BUDGET_AFTER.search(text[end: end + 45]))


def _parse_text(text: str) -> dict | None:
    """Best salary reading from one piece of text, or None."""
    if not text:
        return None
    text = _SPACED_NUM_RE.sub("", text)   # "15 600" -> "15600"
    units = [u for u in _units(text) if not _is_benefit(text, u[0], u[1])]
    if not units:
        return None

    by_cur: dict[str, list] = {}
    for u in units:
        by_cur.setdefault(u[4], []).append(u)

    def position(code: str) -> int:
        if code == "":
            return min(u[0] for u in by_cur[code])
        for m in _CUR_RE.finditer(text):
            if _currency_of(m.group(0)) == code:
                return m.start()
        return 10 ** 9

    chosen = None
    for code in sorted(by_cur, key=position):
        group = by_cur[code]
        if code:
            chosen = (code, group)
            break
        # bare numbers count only when an unambiguous pay word sits right next to them
        if any(_STRONG_HINT_RE.search(text[max(0, s - 70): e + 70]) for s, e, *_ in group):
            chosen = (code, group)
            break
    if chosen is None:
        return None
    code, group = chosen

    top = max(u[3] for u in group)
    scale = [u for u in group if u[3] > top / 100.0] or group   # drop outlier amounts
    lo = min(u[2] for u in scale)
    hi = max(u[3] for u in scale)
    start = min(u[0] for u in scale)
    end = max(u[1] for u in scale)
    window = text[max(0, start - 70): min(len(text), end + 70)]
    if _NONPAY_RE.search(window) and not _PAYWORD_RE.search(window):
        return None                      # revenue / funding talk, not pay

    period = None
    for name, rx in _PERIOD_RES:
        if rx.search(window):
            period = name
            break
    hint = _HINT_RE.search(window)
    if period is None:
        if hi >= _ASSUME_YEAR_MIN:
            period = "year"
        elif hint and _ASSUME_MONTH[0] <= hi <= _ASSUME_MONTH[1]:
            period = "month"
        else:
            return None
    if period == "year":
        floor = 10000.0 if hint else _ASSUME_YEAR_MIN
        if hi < floor:
            return None                  # nobody posts a yearly pay below this
    if period != "hour" and hi < 400:
        return None                      # "€9 monthly" is a subscription, not a salary
    if hi > _MAX[period] * _MAX_MULT.get(code, 1.0):
        return None                      # implausible for the period: a parse mistake

    mult = _ANNUAL[period]
    return {
        "min": round(lo, 2), "max": round(hi, 2),
        "currency": code, "period": period,
        "annual_min": round(lo * mult, 2), "annual_max": round(hi * mult, 2),
        "raw": re.sub(r"\s+", " ", window).strip()[:110],
    }


def parse(title: str, description: str, compensation: str) -> dict | None:
    """Try the dedicated compensation field first, then the description."""
    for text in (compensation, description):
        got = _parse_text(text)
        if got:
            return got
    return None


def fill(p) -> dict | None:
    """Parse for a Posting and copy the fields onto it (returns the dict)."""
    got = parse(p.title, p.description, p.compensation)
    if got:
        p.salary_min = got["min"]
        p.salary_max = got["max"]
        p.salary_currency = got["currency"]
        p.salary_period = got["period"]
        p.salary_annual_max = got["annual_max"]
        p.salary_raw = got["raw"]
    return got


def annual_min(row) -> float | None:
    """The bottom of the advertised band, per year, when there is one.

    `salary_annual_max` is stored; the minimum is not, so it is derived here
    with the same multipliers. Unknown period means no number rather than a
    guess: a monthly figure read as annual is a 12x error in a pay report.
    """
    low = row["salary_min"] if hasattr(row, "keys") else None
    period = (row["salary_period"] or "") if hasattr(row, "keys") else ""
    if low is None or not period:
        return None
    mult = _ANNUAL.get(period)
    return float(low) * mult if mult else None


def fmt(min_v, max_v, currency: str = "", period: str = "") -> str:
    """Compact chip like '€60k–80k/yr', '$45–70/hr', 'CHF 100k/yr'."""
    if not min_v and not max_v:
        return ""
    cur = _SYMBOL.get(currency or "", (currency + " ") if currency else "")
    unit = {"hour": "/hr", "day": "/day", "month": "/mo"}.get(period or "year", "/yr")

    def n(v) -> str:
        if v is None:
            return "?"
        if v >= 10000 or (v >= 2000 and v % 1000 == 0):
            return f"{v / 1000:g}k"
        return f"{v:g}"

    body = n(min_v) if not max_v or min_v == max_v else f"{n(min_v)}–{n(max_v)}"
    return f"{cur}{body}{unit}"


def fmt_row(row) -> str:
    """fmt() for a stored posting row (missing columns tolerated)."""
    try:
        keys = row.keys()
    except AttributeError:
        return ""
    if "salary_min" not in keys:
        return ""
    return fmt(row["salary_min"], row["salary_max"],
               row["salary_currency"] or "", row["salary_period"] or "")
