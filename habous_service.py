# -*- coding: utf-8 -*-
"""
Official Moroccan prayer times and Hijri dates from the Ministry of Habous and Islamic Affairs
(وزارة الأوقاف والشؤون الإسلامية) — https://habous.gov.ma/prieres/

The Ministry publishes, for each of its 191 cities, the timetable of the CURRENT Hijri month
(«الحصة الشهرية») at:
    https://habous.gov.ma/prieres/horaire_hijri_2.php?ville=<ID>
as an HTML table (id="horaire") whose rows are:
    اليوم | اليوم الهجري | التاريخ الميلادي | الفجر | الشروق | الظهر | العصر | المغرب | العشاء

This module downloads that page, parses and validates it, and caches the result.
It uses only the Python standard library + `requests` (no extra dependencies).
"""

import re
import json
import time
import logging
import threading
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

import requests

logger = logging.getLogger("noor_bot.habous")

HABOUS_MONTH_URLS = (
    "https://habous.gov.ma/prieres/horaire_hijri_2.php?ville={hid}",
    "https://www.habous.gov.ma/prieres/horaire_hijri_2.php?ville={hid}",
)
HABOUS_PUBLIC_URL = "https://habous.gov.ma/prieres/horaire_hijri_2.php?ville={hid}"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Mobile Safari/537.36 NoorAlMaghrib/1.0",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ar,fr;q=0.8",
}

# ---------------------------------------------------------------------------
# The Ministry's official city list (ID -> Arabic name), as used on habous.gov.ma/prieres
# ---------------------------------------------------------------------------
HABOUS_CITIES = {
    1: "الرباط", 2: "الخميسات", 3: "تيفلت", 4: "الرماني", 5: "والماس", 6: "بوزنيقة",
    7: "القنيطرة", 8: "سيدي قاسم", 9: "سيدي يحيى الغرب", 10: "سيدي سليمان", 11: "سوق أربعاء الغرب",
    12: "عرباوة", 13: "مولاي بوسلهام", 14: "طنجة", 15: "تطوان", 16: "العرائش", 17: "أصيلة",
    18: "شفشاون", 19: "مرتيل", 20: "المضيق", 21: "القصر الكبير", 22: "القصر الصغير", 23: "الحسيمة",
    24: "سبتة", 25: "الفنيدق", 26: "الجبهة", 27: "واد لاو", 28: "باب برد", 29: "وزان", 30: "بوسكور",
    31: "وجدة", 32: "بركان", 33: "فكيك", 34: "بوعرفة", 35: "كرسيف", 36: "جرادة", 37: "عين الشعير",
    38: "تاوريرت", 39: "الناظور", 40: "مليلية", 41: "دبدو", 42: "سلوان", 43: "بني أنصار", 44: "فرخانة",
    45: "تالسينت", 46: "تندرارة", 47: "العيون الشرقية", 48: "بني ادرار", 49: "السعيدية", 50: "رأس الماء",
    51: "تافوغالت", 52: "فزوان", 53: "أحفير", 54: "زايو", 55: "دريوش", 56: "بني تجيت", 57: "بوعنان",
    58: "الدار البيضاء", 59: "المحمدية", 60: "بن سليمان", 61: "سطات", 62: "الكارة", 63: "البروج",
    64: "ابن أحمد", 65: "برشيد", 66: "الجديدة", 67: "أزمور", 68: "سيدي بنور", 69: "خميس الزمامرة",
    70: "خنيفرة", 71: "مولاي بوعزة", 72: "زاوية أحنصال", 73: "بني ملال", 74: "أزيلال", 75: "الفقيه بنصالح",
    76: "دمنات", 77: "القصيبة", 78: "قصبة تادلة", 79: "خريبكة", 80: "وادي زم", 81: "فاس", 82: "صفرو",
    83: "مولاي يعقوب", 84: "بولمان", 85: "ميسور", 86: "رباط الخير", 87: "المنزل بني يازغة", 88: "إموزار كندر",
    89: "تازة", 90: "تاونات", 91: "أكنول", 92: "تيزي وسلي", 93: "بورد", 94: "تاهلة", 95: "تيسة",
    96: "قرية با محمد", 97: "كتامة", 98: "واد أمليل", 99: "مكناس", 100: "يفرن", 101: "الحاجب", 102: "زرهون",
    103: "آزرو", 104: "مراكش", 105: "قلعة السراغنة", 106: "الصويرة", 107: "شيشاوة", 108: "بنجرير",
    109: "الرحامنة", 110: "تمنار", 111: "آسفي", 112: "الوليدية", 113: "اليوسفية", 114: "تسلطانت",
    115: "تامصلوحت", 116: "قطارة", 117: "أكادير", 118: "تارودانت", 119: "تزنيت", 120: "إغرم", 121: "تالوين",
    122: "تافراوت", 123: "طاطا", 124: "أقا", 125: "فم لحصن", 126: "بويكرة", 127: "أولاد تايمة",
    128: "الرشيدية", 129: "الريصاني", 130: "أرفود", 131: "تنديت", 132: "كلميمة", 133: "إملشيل",
    134: "تنجداد", 135: "الريش", 136: "ميدلت", 137: "زاكورة", 138: "ورزازات", 139: "تنغير", 140: "هسكورة",
    141: "قلعة مكونة", 142: "أكدز", 143: "بومالن دادس", 144: "النيف", 145: "أسول", 146: "أمسمرير",
    147: "تازارين", 148: "سيدي إفني", 149: "كلميم", 150: "أسا", 151: "الزاك", 152: "طانطان",
    153: "بويزكارن", 154: "المحبس", 155: "لمسيد", 156: "العيون", 157: "السمارة", 158: "بوجدور",
    159: "طرفاية", 160: "تفاريتي", 161: "بوكراع", 162: "كلتة زمور", 163: "أمكالة", 164: "أخفنير",
    165: "الداخلة", 166: "الكويرة", 167: "أوسرد", 168: "بئر كندوز", 169: "بئر أنزاران",
    301: "خميس سيدي عبد الجليل", 302: "أولاد عياد", 303: "تاهلة", 304: "مطماطة", 305: "إيمنتانوت",
    306: "سيدي غانم", 307: "تفنتان", 308: "آيت القاق", 309: "أكدال أملشيل", 310: "اكودال املشيل ميدلت",
    311: "أكايوار", 312: "عين العودة", 313: "أسكين", 314: "آيت ورير", 315: "زاوية مولاي ابراهيم",
    316: "تولكولت", 317: "إيكس", 318: "كرس", 319: "تيسنت", 320: "فم زكيد", 321: "قصر إيش", 322: "إيمين ثلاث",
}

# Manual links for app cities whose name differs from the Ministry's list.
# Key: the city "id" used in morocco_data.MOROCCO_CITIES  ->  Ministry city ID.
# Salé and Rabat face each other across the Bou Regreg and share the same official timetable.
HABOUS_CITY_OVERRIDES = {
    "rabat": 1, "sale": 1, "salé": 1, "casablanca": 58, "oujda": 31, "fes": 81, "fez": 81,
    "meknes": 99, "marrakech": 104, "marrakesh": 104, "tangier": 14, "tanger": 14, "agadir": 117,
    "tetouan": 15, "kenitra": 7, "nador": 39,
}

HIJRI_MONTHS_MA = {
    1: "محرم", 2: "صفر", 3: "ربيع الأول", 4: "ربيع الثاني",
    5: "جمادى الأولى", 6: "جمادى الثانية", 7: "رجب", 8: "شعبان",
    9: "رمضان", 10: "شوال", 11: "ذو القعدة", 12: "ذو الحجة",
}
GREG_MONTHS_MA = {
    1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "ماي", 6: "يونيو",
    7: "يوليوز", 8: "غشت", 9: "شتنبر", 10: "أكتوبر", 11: "نونبر", 12: "دجنبر",
}
WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]  # date.weekday()

PRAYER_KEYS = ["Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha"]


# ---------------------------------------------------------------------------
# Arabic text helpers
# ---------------------------------------------------------------------------
_AR_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalize_ar(text: str) -> str:
    """Normalize Arabic for matching: drop diacritics/tatweel, unify alef/ya/ta-marbuta, collapse spaces."""
    t = _AR_DIACRITICS.sub("", str(text or ""))
    t = t.translate(_DIGITS)
    t = re.sub("[أإآٱ]", "ا", t).replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    t = re.sub(r"[\-_/.,،()]+", " ", t)
    return " ".join(t.split()).strip().lower()


def _clean_cell(text: str) -> str:
    t = str(text or "").replace("\xa0", " ").translate(_DIGITS)
    t = re.sub(r"\s*:\s*", ":", t)
    return " ".join(t.split()).strip()


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _norm_time(cell: str):
    m = _TIME_RE.match(_clean_cell(cell))
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return None
    return f"{hh:02d}:{mm:02d}"


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# ---------------------------------------------------------------------------
# City matching (app city -> Ministry city ID)
# ---------------------------------------------------------------------------
_HABOUS_BY_NAME = {}
for _hid, _name in HABOUS_CITIES.items():
    _HABOUS_BY_NAME.setdefault(normalize_ar(_name), _hid)


def match_habous_id(city: dict):
    """Return the Ministry city ID for an app city dict ({id, ar, en, ...}) or None if not in the official list."""
    if not city:
        return None
    for key in (str(city.get("id", "")).lower(), str(city.get("en", "")).lower()):
        if key in HABOUS_CITY_OVERRIDES:
            return HABOUS_CITY_OVERRIDES[key]
    if city.get("habous_id"):
        try:
            return int(city["habous_id"])
        except (TypeError, ValueError):
            pass
    name = normalize_ar(city.get("ar", ""))
    if not name:
        return None
    if name in _HABOUS_BY_NAME:
        return _HABOUS_BY_NAME[name]
    # Tolerate an extra/missing «ال» or a «مدينة» prefix.
    variants = {name.replace("مدينه ", ""), name[2:] if name.startswith("ال") else "ال" + name}
    for v in variants:
        if v in _HABOUS_BY_NAME:
            return _HABOUS_BY_NAME[v]
    return None


# ---------------------------------------------------------------------------
# Tabular (arithmetical) Islamic calendar — used ONLY to identify the month/year number of the
# Ministry's table (taken at mid-month, where it cannot be off by a whole month). Day numbers
# always come from the Ministry itself.
# ---------------------------------------------------------------------------
_ISLAMIC_EPOCH = 1948439.5


def _greg_to_jd(d: date) -> float:
    return d.toordinal() + 1721424.5


def _islamic_to_jd(y: int, m: int, d: int) -> float:
    return d + int(-(-29.5 * (m - 1) // 1)) + (y - 1) * 354 + (3 + 11 * y) // 30 + _ISLAMIC_EPOCH - 1


def tabular_hijri(d: date):
    jd = int(_greg_to_jd(d) - 0.5) + 0.5
    y = int((30 * (jd - _ISLAMIC_EPOCH) + 10646) // 10631)
    m = min(12, int(-(-(jd - (29 + _islamic_to_jd(y, 1, 1))) // 29.5)) + 1)
    m = max(1, m)
    dd = int(jd - _islamic_to_jd(y, m, 1) + 1)
    return y, m, dd


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------
class _TableCollector(HTMLParser):
    """Collect every <table> (with attributes) and the text of its rows/cells; tolerant to sloppy HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.page_text = []
        self._stack = []
        self._row = None
        self._cell = None
        self._skip = 0

    def _close_cell(self):
        if self._cell is not None and self._row is not None:
            self._row.append(_clean_cell("".join(self._cell)))
        self._cell = None

    def _close_row(self):
        self._close_cell()
        if self._row is not None and self._stack:
            self.tables[self._stack[-1]]["rows"].append(self._row)
        self._row = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "table":
            self._close_row()
            self.tables.append({"attrs": {k.lower(): (v or "") for k, v in attrs}, "rows": []})
            self._stack.append(len(self.tables) - 1)
        elif tag == "tr" and self._stack:
            self._close_row()
            self._row = []
        elif tag in ("td", "th") and self._stack:
            if self._row is None:
                self._row = []
            self._close_cell()
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()
        elif tag == "table" and self._stack:
            self._close_row()
            self._stack.pop()

    def handle_data(self, data):
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        self.page_text.append(data)


_HIJRI_NAME_PATTERNS = [
    (3, r"ربيع\s*(?:الاول|الاولي|اول|1)\b"), (4, r"ربيع\s*(?:الثاني|الثانيه|الاخر|اخر|2)\b"),
    (5, r"جمادي\s*(?:الاولي|الاول|اولي|1)\b"), (6, r"جمادي\s*(?:الثانيه|الثاني|الاخره|الاخر|2)\b"),
    (11, r"ذ[وي]\s*القعده"), (12, r"ذ[وي]\s*الحجه"),
    (1, r"محرم"), (2, r"\bصفر\b"), (7, r"\bرجب\b"), (8, r"شعبان"), (9, r"رمضان"), (10, r"شوال"),
    # French spellings used on the French version of the page
    (3, r"rabi[a']?\s*(?:al[- ]?)?(?:awal|aoual|awwal|i)\b"), (4, r"rabi[a']?\s*(?:al[- ]?)?(?:th?ani|akhir|ii)\b"),
    (5, r"jou?mada\s*(?:al[- ]?)?(?:oula|ula|i)\b"), (6, r"jou?mada\s*(?:al[- ]?)?(?:th?aa?nia|th?ania|akhira|ii)\b"),
    (11, r"d[ho]u\s*(?:al[- ]?)?(?:qi'?i?da|kaada|qa'?da)"), (12, r"d[ho]u\s*(?:al[- ]?)?(?:hij+a|hijja)"),
    (1, r"mou?harram"), (7, r"rajab"), (8, r"cha'?a?bane?|sha'?ban"), (9, r"ramad[ah]n"), (10, r"chaou?al|shawwal|chawwal"),
]


def find_hijri_month(text: str):
    t = normalize_ar(text)
    for num, pat in _HIJRI_NAME_PATTERNS:
        if re.search(pat, t):
            return num
    return None


_GREG_NAME_TO_NUM = {}
for _n, _names in {
    1: ["يناير", "janvier", "jan", "january", "كانون الثاني"], 2: ["فبراير", "fevrier", "février", "fev", "fév", "feb", "february"],
    3: ["مارس", "mars", "mar", "march"], 4: ["ابريل", "أبريل", "avril", "avr", "apr", "april"],
    5: ["ماي", "مايو", "mai", "may"], 6: ["يونيو", "يونيه", "juin", "jun", "june"],
    7: ["يوليوز", "يوليو", "يوليه", "juillet", "juil", "jul", "july"], 8: ["غشت", "اغسطس", "أغسطس", "aout", "août", "aug", "august"],
    9: ["شتنبر", "سبتمبر", "septembre", "sept", "sep", "september"], 10: ["اكتوبر", "أكتوبر", "octobre", "oct", "october"],
    11: ["نونبر", "نوفمبر", "novembre", "nov", "november"], 12: ["دجنبر", "ديسمبر", "decembre", "décembre", "dec", "déc", "december"],
}.items():
    for _nm in _names:
        _GREG_NAME_TO_NUM[normalize_ar(_nm)] = _n


def _parse_greg_cell(cell: str):
    """Return (day, month_or_None, year_or_None) from cells like «11 نونبر», «11 Nov», «11/11/2026»."""
    c = _clean_cell(cell)
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", c)
    if m:
        y = int(m.group(3))
        return int(m.group(1)), int(m.group(2)), (y + 2000 if y < 100 else y)
    m = re.search(r"(\d{1,2})", c)
    if not m:
        return None, None, None
    day = int(m.group(1))
    rest = normalize_ar(c[m.end():])
    month = None
    for word in rest.split():
        if word in _GREG_NAME_TO_NUM:
            month = _GREG_NAME_TO_NUM[word]
            break
    ym = re.search(r"(20\d{2})", c)
    return day, month, (int(ym.group(1)) if ym else None)


def _decode_html(raw: bytes, declared: str = None) -> str:
    candidates = []
    if declared:
        candidates.append(declared)
    m = re.search(rb'charset=["\']?([A-Za-z0-9_\-]+)', raw[:4000])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "cp1256"]
    for enc in candidates:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


class HabousParseError(ValueError):
    pass


def parse_habous_month_html(html: str, today: date) -> dict:
    """Parse the Ministry's monthly Hijri table. Raises HabousParseError if the page is not as expected."""
    p = _TableCollector()
    p.feed(html)
    p.close()

    def table_rows(tbl):
        out = []
        for cells in tbl["rows"]:
            times_idx = [i for i, c in enumerate(cells) if _norm_time(c)]
            if len(times_idx) >= 6:
                out.append((cells, times_idx))
        return out

    tables = p.tables
    chosen = next((t for t in tables if t["attrs"].get("id", "").lower() == "horaire"), None)
    if chosen is None or not table_rows(chosen):
        chosen = max(tables, key=lambda t: len(table_rows(t)), default=None)
    if chosen is None:
        raise HabousParseError("no table found")
    rows = table_rows(chosen)
    if len(rows) < 25:
        raise HabousParseError(f"only {len(rows)} timetable rows found")

    parsed = []
    for cells, tidx in rows:
        times = [_norm_time(cells[i]) for i in tidx[:6]]
        pre = cells[:tidx[0]]
        hijri_day, greg_cell = None, None
        for j, c in enumerate(pre):
            if re.fullmatch(r"\d{1,2}", c) and 1 <= int(c) <= 30:
                hijri_day = int(c)
                greg_cell = pre[j + 1] if j + 1 < len(pre) else None
                break
        if hijri_day is None:
            raise HabousParseError(f"no Hijri day in row {cells}")
        gd, gm, gy = _parse_greg_cell(greg_cell) if greg_cell else (None, None, None)
        mins = [_to_minutes(t) for t in times]
        if any(b <= a for a, b in zip(mins, mins[1:])):
            raise HabousParseError(f"prayer times not increasing in row {cells}")
        parsed.append({"hijri_day": hijri_day, "gd": gd, "gm": gm, "gy": gy, "times": times})

    # Hijri days must be consecutive (1, 2, 3, …).
    for a, b in zip(parsed, parsed[1:]):
        if b["hijri_day"] != a["hijri_day"] + 1:
            raise HabousParseError("Hijri days are not consecutive")

    # Anchor the Gregorian dates: rows are consecutive days; pick the start date (near today) that
    # best matches the day numbers printed by the Ministry, using printed month/year when present.
    best, best_score = None, -1
    for offset in range(-45, 8):
        start = today + timedelta(days=offset)
        score = 0
        for i, r in enumerate(parsed):
            d = start + timedelta(days=i)
            if r["gd"] is not None and r["gd"] == d.day:
                score += 2
                if r["gm"] is not None and r["gm"] == d.month:
                    score += 1
                if r["gy"] is not None and r["gy"] == d.year:
                    score += 1
        if score > best_score:
            best, best_score = start, score
    with_day = sum(1 for r in parsed if r["gd"] is not None)
    if with_day < len(parsed) * 0.8 or best_score < 2 * with_day:
        raise HabousParseError("Gregorian dates could not be matched")

    # Identify the Hijri month & year: name printed by the Ministry (header/title), cross-checked
    # with the arithmetical calendar at mid-month.
    month_start = best - timedelta(days=parsed[0]["hijri_day"] - 1)
    ty, tm, _ = tabular_hijri(month_start + timedelta(days=14))
    header_text = " ".join(" ".join(cells) for cells in chosen["rows"] if not any(_norm_time(c) for c in cells))
    # At mid-month the arithmetical calendar cannot be off by a whole month (it differs from the
    # observed calendar by 1–2 days at most), so it reliably gives the month number and year.
    # The month name printed in the Ministry's table header is used as a safety check.
    h_month, h_year = tm, ty
    named_in_table = find_hijri_month(header_text)
    if named_in_table and named_in_table != tm:
        raise HabousParseError(f"Hijri month name {named_in_table} inconsistent with calendar {tm}")

    days = []
    for i, r in enumerate(parsed):
        d = best + timedelta(days=i)
        days.append({
            "day": d.day,
            "date_iso": d.isoformat(),
            "gregorian": d.strftime("%d-%m-%Y"),
            "greg_label": f"{d.day} {GREG_MONTHS_MA[d.month]}",
            "weekday_ar": WEEKDAYS_AR[d.weekday()],
            "hijri_day": str(r["hijri_day"]),
            "hijri_month": HIJRI_MONTHS_MA[h_month],
            "hijri_month_num": h_month,
            "hijri_year": str(h_year),
            "timings": dict(zip(PRAYER_KEYS, r["times"])),
        })
    return {
        "hijri_month": HIJRI_MONTHS_MA[h_month],
        "hijri_month_num": h_month,
        "hijri_year": str(h_year),
        "title": f"{HIJRI_MONTHS_MA[h_month]} {h_year} هـ",
        "days": days,
    }


# ---------------------------------------------------------------------------
# Fetching + caching
# ---------------------------------------------------------------------------
_MEM_CACHE = {}
_FAIL_UNTIL = {}    # after a failed download, wait before retrying (keeps requests fast if the site is down)
FAIL_BACKOFF_SEC = 300
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_cache_get = None   # optional persistent cache (e.g. Upstash Redis): fn(key) -> str|None
_cache_set = None   # fn(key, value_str, ttl_seconds) -> None


def configure_cache(get_fn=None, set_fn=None):
    global _cache_get, _cache_set
    _cache_get, _cache_set = get_fn, set_fn


def _lock_for(hid):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(hid, threading.Lock())


def _table_for_today(table: dict, today: date):
    iso = today.isoformat()
    return next((d for d in (table or {}).get("days", []) if d["date_iso"] == iso), None)


def _fresh_enough(table: dict, today: date, now_ts: float) -> bool:
    row = _table_for_today(table, today)
    if not row:
        return False
    age = now_ts - float(table.get("fetched_at", 0))
    # Near the end of the Hijri month the Ministry switches to the new month after the moon sighting:
    # refresh every 30 minutes then; otherwise every 6 hours.
    max_age = 1800 if int(row["hijri_day"]) >= 28 else 6 * 3600
    return age < max_age


def _download(hid: int) -> str:
    started = time.monotonic()
    last_err = None
    for i, tmpl in enumerate(HABOUS_MONTH_URLS):
        if i > 0 and time.monotonic() - started > 3.5:
            break
        url = tmpl.format(hid=hid)
        for verify in (True, False):
            try:
                resp = requests.get(url, headers=REQUEST_HEADERS, timeout=(3.5, 6), verify=verify)
                resp.raise_for_status()
                declared = None
                ctype = resp.headers.get("Content-Type", "")
                m = re.search(r"charset=([\w\-]+)", ctype)
                if m:
                    declared = m.group(1)
                return _decode_html(resp.content, declared)
            except requests.exceptions.SSLError as e:
                # Some government servers ship an incomplete certificate chain; the data is public and
                # read-only, and is validated by the parser before use.
                last_err = e
                if verify:
                    try:
                        import urllib3
                        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    except Exception:
                        pass
                    continue
                break
            except Exception as e:
                last_err = e
                break
    raise RuntimeError(f"habous download failed: {last_err}")


def get_month_table(hid: int, today: date, force: bool = False):
    """Return the Ministry's current Hijri-month table for city `hid`, or None if unavailable.

    The returned dict has: title, hijri_month, hijri_year, days[], source_url, fetched_at, stale (bool).
    """
    if not hid:
        return None
    now_ts = time.time()
    key = f"noor:habous:v1:{hid}"

    cached = _MEM_CACHE.get(hid)
    if cached is None and _cache_get:
        try:
            raw = _cache_get(key)
            if raw:
                cached = json.loads(raw)
                _MEM_CACHE[hid] = cached
        except Exception:
            cached = None
    if cached and not force and _fresh_enough(cached, today, now_ts):
        return cached

    def _stale_or_none():
        if cached and _table_for_today(cached, today):
            stale = dict(cached)
            stale["stale"] = True
            return stale
        return None

    if not force and _FAIL_UNTIL.get(hid, 0) > now_ts:
        return _stale_or_none()

    with _lock_for(hid):
        cached2 = _MEM_CACHE.get(hid)  # another request may have refreshed it while we waited
        if cached2 and not force and _fresh_enough(cached2, today, time.time()):
            return cached2
        try:
            html = _download(hid)
            table = parse_habous_month_html(html, today)
            table.update({
                "hid": hid,
                "city_ar": HABOUS_CITIES.get(hid, ""),
                "source_url": HABOUS_PUBLIC_URL.format(hid=hid),
                "fetched_at": now_ts,
                "stale": False,
            })
            _MEM_CACHE[hid] = table
            _FAIL_UNTIL.pop(hid, None)
            if _cache_set:
                try:
                    _cache_set(key, json.dumps(table, ensure_ascii=False), 35 * 86400)
                except Exception:
                    pass
            return table
        except Exception as e:
            logger.error("Habous fetch/parse failed for city %s: %s", hid, e)
            _FAIL_UNTIL[hid] = time.time() + FAIL_BACKOFF_SEC
            # Keep serving the last official table we have, as long as it still covers today.
            return _stale_or_none()
