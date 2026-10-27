"""Deterministic parsing of values as written on payroll documents."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

ZERO_MARKS = {"0", "ø", "Ø", "⊘", "∅", "θ", "-", "—", "–", "--", "x", "X", "nw", "NW", "none", "off", "OFF"}
SALARY_MARKS = re.compile(r"^\s*(pay(\s+salary)?|salary|sal|s|yes,?\s*pay\s*salary|reg(ular)?\s*pay|✓|✔|√)\s*\.?$", re.I)
FREQ_WORD = {"W": "Weekly", "B": "Biweekly", "S": "Semimonthly", "M": "Monthly", "Q": "Quarterly"}
FREQ_LETTER = {v.lower(): k for k, v in FREQ_WORD.items()} | {"bi-weekly": "B", "semi-monthly": "S", "semi monthly": "S"}


@dataclass
class Parsed:
    value: float | None
    unit: str | None          # hours | days | None
    kind: str                 # number | zero_mark | salary_marker | text | empty
    raw: str
    uncertain: bool = False


def parse_quantity(text: str | None) -> Parsed:
    """Parse a value as written. Handles $, commas, ( )/< > negatives, mixed fractions, slashed zero,
    'days', trailing '?', and 'h:mm' time notation."""
    raw = "" if text is None else str(text)
    s = raw.strip()
    if not s:
        return Parsed(None, None, "empty", raw)
    uncertain = "?" in s
    s = s.replace("?", "").strip()
    if s in ZERO_MARKS or s.lower() in ZERO_MARKS:
        return Parsed(0.0, None, "zero_mark", raw, uncertain)
    if SALARY_MARKS.match(s):
        return Parsed(None, None, "salary_marker", raw, uncertain)
    unit = None
    m = re.match(r"^(.*?)\s*(days?|d)\.?$", s, re.I)
    if m and re.search(r"\d", m.group(1)):
        unit, s = "days", m.group(1).strip()
    m = re.match(r"^(.*?)\s*(hrs?|hours?|h)\.?$", s, re.I)
    if m and re.search(r"\d", m.group(1)):
        unit, s = "hours", m.group(1).strip()
    neg = False
    if (s.startswith("(") and s.endswith(")")) or (s.startswith("<") and s.endswith(">")):
        neg, s = True, s[1:-1].strip()
    if s.startswith("-"):
        neg, s = True, s[1:].strip()
    s = s.replace("$", "").strip()
    if re.fullmatch(r"\d+,\d{1,2}", s):   # handwritten decimal comma: "19,25" -> 19.25
        s = s.replace(",", ".")
    s = s.replace(",", "")
    # h:mm(:ss) time notation -> decimal hours
    m = re.fullmatch(r"(\d+):(\d{2})(?::(\d{2}))?", s)
    if m:
        v = int(m.group(1)) + int(m.group(2)) / 60 + (int(m.group(3) or 0) / 3600)
        return Parsed(round(-v if neg else v, 4), "hours", "number", raw, uncertain)
    m = re.fullmatch(r"(\d+)?\s*(\d+)/(\d+)", s)
    try:
        if m and int(m.group(3)) != 0:
            v = (int(m.group(1)) if m.group(1) else 0) + int(m.group(2)) / int(m.group(3))
        else:
            v = float(s)
    except ValueError:
        return Parsed(None, unit, "text", raw, uncertain)
    return Parsed(round(-v if neg else v, 4), unit, "number", raw, uncertain)


def parse_amount(text: str | None) -> float | None:
    p = parse_quantity(text)
    return p.value if p.kind in ("number", "zero_mark") else None


def parse_date(text: str | None, default_year: int | None = None) -> tuple[str | None, bool]:
    """Return (ISO date, inferred_year). Accepts M/D/YY(YY), M-D-YY, YYYY-MM-DD, M/D (with default_year)."""
    if not text:
        return None, False
    s = str(text).replace("?", "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2}).*", s)
    if m:
        y, mo, d = map(int, m.groups())
        return _iso(y, mo, d), False
    m = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", s)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        return _iso(y, mo, d), False
    m = re.fullmatch(r"(\d{1,2})[/\-](\d{1,2})", s)
    if m and default_year:
        return _iso(default_year, int(m.group(1)), int(m.group(2))), True
    return None, False


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def freq_letter(text: str | None) -> str | None:
    if not text:
        return None
    s = str(text).strip()
    if s[:1].upper() in FREQ_WORD and len(s) <= 2:
        return s[:1].upper()
    return FREQ_LETTER.get(s.lower())


def split_name(full: str) -> dict:
    full = re.sub(r"\s+", " ", (full or "").replace("?", "")).strip()
    if "," in full:
        last, first = [p.strip() for p in full.split(",", 1)]
    else:
        parts = full.split(" ")
        first, last = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else (full, None)
    return {"full": full, "first": first or None, "last": last or None}


def name_key(full: str) -> str:
    s = re.sub(r"[^a-z ]", " ", (full or "").lower())
    toks = sorted(t for t in s.split() if len(t) > 1)
    return " ".join(toks)


def digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def snake(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    aliases = {"rate_1": "rate_1", "rate1": "rate_1", "ytd_gross": "ytd_gross", "last_rs": "last_raise_date",
               "start_dt": "start_date", "birth_dt": "birth_date", "ww": "work_week", "vac_hr": "vac_hours_balance",
               "sick_hr": "sick_hours_balance", "vac_wages": "vac_wages", "sick_wages": "sick_wages", "dept_loc": "dept_loc"}
    return aliases.get(s, s)


def token_ratio(a: str, b: str) -> float:
    """Order-insensitive name similarity in [0, 1] (no external deps)."""
    from difflib import SequenceMatcher
    stop = {"inc", "llc", "co", "corp", "corporation", "company", "ltd", "the", "of", "dba", "pc", "p", "c", "d", "m", "dds", "ms"}
    def norm(x):
        return " ".join(sorted(t for t in re.sub(r"[^a-z0-9 ]", " ", (x or "").lower()).split() if t not in stop))
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()
