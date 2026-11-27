# -*- coding: utf-8 -*-
"""
Official Moroccan prayer times and Hijri dates from the Ministry of Habous and Islamic Affairs
(وزارة الأوقاف والشؤون الإسلامية) — https://www.habous.gov.ma

Since its 2026 redesign, the Ministry's portal publishes its data as static files, read by its own
page script (assets/js/prieres.js):
  • https://www.habous.gov.ma/data/horaires-YYYY-MM.js  — one file per GREGORIAN month:
      (window.HORAIRES_MOIS=…).push({"annee":2026,"mois":11,"fuseau":"GMT",
          "cols":["Fajr","Chorouq","Dhuhr","Asr","Maghrib","Ishae"],
          "villes":[{"c":1,"n":"الرباط","h":[["05:17","06:44",…], …]}, …]})
      city = numeric code "c" (same IDs as the old /prieres pages); h[d-1] = day d; legal Moroccan time.
  • https://www.habous.gov.ma/data/hijri.js — the official start date and length of each Hijri month,
      updated by the Ministry after each moon-sighting announcement:
      window.HIJRI = { noms: [...], mois: [ { debut: '2026-11-11', mois: 6, annee: 1448, duree: null }, … ] }

The Hijri date of a day is computed exactly like the Ministry's page does it: latest month whose
`debut` ≤ day, Hijri day = days since `debut` + 1; beyond `duree` (30 if unknown) → unknown; day 30 of a
month whose length is not yet announced → uncertain («حسب ثبوت رؤية الهلال»).

Only the Python standard library + `requests` are used.
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

HABOUS_BASE = "https://www.habous.gov.ma"
HIJRI_URL = HABOUS_BASE + "/data/hijri.js"
TIMES_URL = HABOUS_BASE + "/data/horaires-{y:04d}-{m:02d}.js"
HABOUS_PUBLIC_URL = HABOUS_BASE + "/?ville={hid}"
CITY_CODE_ALIAS = {303: 94}   # same alias as the Ministry's own page script

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Mobile Safari/537.36 NoorAlMaghrib/1.0",
    "Accept": "application/javascript,text/javascript,*/*",
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
    # Same cities, spelled differently in the Ministry's list:
    "guercif": 35,         # جرسيف = كرسيف
    "figuig": 33,          # فجيج = فكيك
    "tiznit": 119,         # تيزنيت = تزنيت
    "ifrane": 100,         # إفران = يفرن
    "fqih_ben_salah": 75,  # الفقيه بن صالح = الفقيه بنصالح
    # Not published separately by the Ministry; nearest official city (≈10 km):
    "temara": 1,           # تمارة -> الرباط
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
# Download + cache helpers
# ---------------------------------------------------------------------------
_MEM = {}           # key -> {"value": ..., "fetched_at": ts}
LAST_ERROR = {}     # resource -> {"type", "error", "at"} (shown on /api/habous/status)
LAST_FETCH = {}     # resource -> {"url", "status", "bytes", "elapsed", "tls_verified"}
_FAIL_UNTIL = {}    # resource -> ts (wait before retrying after a failure)
FAIL_BACKOFF_SEC = 300
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_cache_get = None   # optional persistent cache (Upstash Redis): fn(key) -> str|None
_cache_set = None   # fn(key, value_str, ttl_seconds)


def configure_cache(get_fn=None, set_fn=None):
    global _cache_get, _cache_set
    _cache_get, _cache_set = get_fn, set_fn


def _lock_for(key):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class HabousNotPublished(Exception):
    """The Ministry has not published this file (HTTP 404)."""


def _http_get(resource: str, url: str) -> str:
    last_err = None
    for verify in (True, False):
        t0 = time.monotonic()
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=(4, 7), verify=verify)
            LAST_FETCH[resource] = {"url": resp.url, "status": resp.status_code, "bytes": len(resp.content),
                                    "elapsed": round(time.monotonic() - t0, 2), "tls_verified": verify}
            if resp.status_code == 404:
                raise HabousNotPublished(url)
            resp.raise_for_status()
            return resp.content.decode("utf-8", "replace")
        except requests.exceptions.SSLError as e:
            # Some government servers ship an incomplete certificate chain; the data is public and
            # read-only, and is fully validated before use.
            last_err = e
            if verify:
                try:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                except Exception:
                    pass
                continue
            break
        except HabousNotPublished:
            raise
        except Exception as e:
            last_err = e
            LAST_FETCH.setdefault(resource, {"url": url, "status": None,
                                             "elapsed": round(time.monotonic() - t0, 2), "tls_verified": verify})
            break
    raise RuntimeError(f"{type(last_err).__name__}: {last_err}")


def _cached(resource: str, max_age: float, loader, force: bool = False):
    """Return a fresh cached value, or reload it; on failure keep serving the last good value."""
    now_ts = time.time()
    entry = _MEM.get(resource)
    if entry is None and _cache_get:
        try:
            raw = _cache_get(f"noor:habous:v2:{resource}")
            if raw:
                entry = json.loads(raw)
                _MEM[resource] = entry
        except Exception:
            entry = None
    if entry and not force and now_ts - entry["fetched_at"] < max_age:
        return entry["value"], entry["fetched_at"], False
    if not force and _FAIL_UNTIL.get(resource, 0) > now_ts:
        return (entry["value"], entry["fetched_at"], True) if entry else (None, None, True)
    with _lock_for(resource):
        e2 = _MEM.get(resource)
        if e2 and not force and time.time() - e2["fetched_at"] < max_age:
            return e2["value"], e2["fetched_at"], False
        try:
            value = loader()
            entry = {"value": value, "fetched_at": time.time()}
            _MEM[resource] = entry
            _FAIL_UNTIL.pop(resource, None)
            LAST_ERROR.pop(resource, None)
            if _cache_set:
                try:
                    _cache_set(f"noor:habous:v2:{resource}", json.dumps(entry, ensure_ascii=False, separators=(",", ":")), 40 * 86400)
                except Exception:
                    pass
            return value, entry["fetched_at"], False
        except Exception as e:
            logger.error("Habous %s failed: %s", resource, e)
            LAST_ERROR[resource] = {"type": type(e).__name__, "error": str(e)[:400], "at": time.time()}
            _FAIL_UNTIL[resource] = time.time() + (1800 if isinstance(e, HabousNotPublished) else FAIL_BACKOFF_SEC)
            if isinstance(e, HabousNotPublished):
                # A month not yet published is a valid answer, not a stale one.
                return None, None, False
            return (entry["value"], entry["fetched_at"], True) if entry else (None, None, True)


# ---------------------------------------------------------------------------
# Parsers (strict: anything unexpected raises, so wrong data is never shown)
# ---------------------------------------------------------------------------
class HabousParseError(ValueError):
    pass


_HIJRI_ENTRY = re.compile(
    r"""\{\s*["']?debut["']?\s*:\s*["'](\d{4}-\d{2}-\d{2})["']\s*,\s*["']?mois["']?\s*:\s*(\d{1,2})\s*,"""
    r"""\s*["']?annee["']?\s*:\s*(\d{4})\s*,\s*["']?duree["']?\s*:\s*(null|\d{2})\s*,?\s*\}""")


def parse_hijri_js(text: str) -> list:
    body = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    body = re.sub(r"(?m)^\s*//.*$", " ", body)
    months = []
    for debut, mois, annee, duree in _HIJRI_ENTRY.findall(body):
        m, y = int(mois), int(annee)
        d = None if duree == "null" else int(duree)
        if not (1 <= m <= 12 and 1400 <= y <= 1600) or d not in (None, 29, 30):
            raise HabousParseError(f"invalid Hijri entry {debut} {mois} {annee} {duree}")
        datetime.strptime(debut, "%Y-%m-%d")
        months.append({"debut": debut, "mois": m, "annee": y, "duree": d})
    if not months:
        raise HabousParseError("no Hijri month entries found")
    months.sort(key=lambda x: x["debut"])
    for a, b in zip(months, months[1:]):   # consecutive months must follow each other
        gap = (date.fromisoformat(b["debut"]) - date.fromisoformat(a["debut"])).days
        if gap not in (29, 30) or (a["duree"] and gap != a["duree"]):
            raise HabousParseError(f"inconsistent Hijri months {a} -> {b}")
    return months


def hijri_for(day: date, months: list):
    """Hijri date of `day` exactly as computed by the Ministry's page; None if not yet announced."""
    for e in reversed(months or []):
        start = date.fromisoformat(e["debut"])
        if start > day:
            continue
        n = (day - start).days + 1
        limit = e["duree"] or 30
        if n > limit:
            return None
        return {"day": n, "month": e["mois"], "year": e["annee"], "uncertain": e["duree"] is None and n == 30}
    return None


_COL_KEYS = {"fajr": "Fajr", "chorouq": "Sunrise", "chourouq": "Sunrise", "shuruq": "Sunrise", "sunrise": "Sunrise",
             "dhuhr": "Dhuhr", "dohr": "Dhuhr", "asr": "Asr", "maghrib": "Maghrib", "ishae": "Isha", "isha": "Isha"}


def parse_times_js(text: str, year: int, month: int) -> dict:
    i = text.find(".push(")
    j = text.rfind("}")
    if i < 0 or j < 0:
        raise HabousParseError("unexpected times file format")
    data = json.loads(text[text.index("{", i): j + 1])
    if int(data.get("annee", 0)) != year or int(data.get("mois", 0)) != month:
        raise HabousParseError(f"file is for {data.get('annee')}-{data.get('mois')}, expected {year}-{month}")
    cols = [_COL_KEYS.get(str(c).strip().lower()) for c in data.get("cols", [])]
    if sorted(c for c in cols if c) != sorted(PRAYER_KEYS) or len(cols) != 6:
        raise HabousParseError(f"unexpected columns {data.get('cols')}")
    order = [cols.index(k) for k in PRAYER_KEYS]
    ndays = (date(year + (month == 12), month % 12 + 1, 1) - date(year, month, 1)).days
    villes, names = {}, {}
    for v in data.get("villes", []):
        rows = []
        for r in v.get("h", []):
            if len(r) != 6:
                raise HabousParseError(f"bad row for city {v.get('c')}")
            t = [_norm_time(r[k]) for k in order]
            if None in t:
                raise HabousParseError(f"bad time for city {v.get('c')}: {r}")
            mins = [_to_minutes(x) for x in t]
            if any(b <= a for a, b in zip(mins, mins[1:])):
                raise HabousParseError(f"times not increasing for city {v.get('c')}: {r}")
            rows.append(t)
        if rows and len(rows) != ndays:
            raise HabousParseError(f"city {v.get('c')} has {len(rows)} days, expected {ndays}")
        villes[str(int(v["c"]))] = rows
        names[str(int(v["c"]))] = str(v.get("n", ""))
    if len(villes) < 50:
        raise HabousParseError(f"only {len(villes)} cities in file")
    return {"annee": year, "mois": month, "fuseau": data.get("fuseau", ""), "villes": villes, "names": names}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_hijri(force: bool = False):
    value, fetched_at, stale = _cached("hijri", 1800, lambda: parse_hijri_js(_http_get("hijri", HIJRI_URL)), force)
    return value, stale


def load_times(year: int, month: int, today: date, force: bool = False):
    key = f"times-{year:04d}-{month:02d}"
    current = (year, month) == (today.year, today.month)
    max_age = 6 * 3600 if current else 3 * 86400
    value, fetched_at, stale = _cached(
        key, max_age, lambda: parse_times_js(_http_get(key, TIMES_URL.format(y=year, m=month)), year, month), force)
    return value, stale


def _city_code(times: dict, hid: int):
    code = str(CITY_CODE_ALIAS.get(hid, hid))
    if code in times["villes"]:
        return code
    want = normalize_ar(HABOUS_CITIES.get(hid, ""))
    for c, n in times["names"].items():
        if want and normalize_ar(n) == want:
            return c
    return None


def _row(d: date, t6: list, hij) -> dict:
    return {
        "day": d.day,
        "date_iso": d.isoformat(),
        "gregorian": d.strftime("%d-%m-%Y"),
        "greg_label": f"{d.day} {GREG_MONTHS_MA[d.month]}",
        "weekday_ar": WEEKDAYS_AR[d.weekday()],
        "hijri_day": str(hij["day"]) if hij else "",
        "hijri_month": HIJRI_MONTHS_MA[hij["month"]] if hij else "",
        "hijri_month_num": hij["month"] if hij else 0,
        "hijri_year": str(hij["year"]) if hij else "",
        "hijri_uncertain": bool(hij and hij["uncertain"]),
        "timings": dict(zip(PRAYER_KEYS, t6)),
    }


def official_day(hid: int, day: date, today: date = None):
    """One day's official row (times + Hijri date), or None if not published."""
    today = today or day
    times, _ = load_times(day.year, day.month, today)
    if not times:
        return None
    code = _city_code(times, hid)
    rows = times["villes"].get(code) if code else None
    if not rows or day.day > len(rows):
        return None
    months, _ = load_hijri()
    return _row(day, rows[day.day - 1], hijri_for(day, months))


def get_month_table(hid: int, today: date, force: bool = False):
    """Current Hijri month («الحصة الشهرية») for Ministry city `hid`, or None if unavailable.

    Returns: title, hijri_month, hijri_month_num, hijri_year, days[], source_url, fetched_at, stale, city_ar.
    """
    if not hid:
        return None
    months, hijri_stale = load_hijri(force)
    cur = hijri_for(today, months) if months else None
    if cur:
        entry = next(e for e in months if e["mois"] == cur["month"] and e["annee"] == cur["year"]
                     and date.fromisoformat(e["debut"]) <= today)
        start = date.fromisoformat(entry["debut"])
        dates = [start + timedelta(days=i) for i in range(entry["duree"] or 30)]
        title = f"{HIJRI_MONTHS_MA[cur['month']]} {cur['year']} هـ"
    else:
        # Hijri month not announced yet (or hijri.js unavailable): show the official Gregorian month.
        first = today.replace(day=1)
        nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        dates = [first + timedelta(days=i) for i in range((nxt - first).days)]
        title = f"شهر {GREG_MONTHS_MA[today.month]} {today.year} م"

    stale = bool(hijri_stale)
    loaded = {}
    days = []
    for d in dates:
        ym = (d.year, d.month)
        if ym not in loaded:
            loaded[ym] = load_times(d.year, d.month, today, force)
        times, t_stale = loaded[ym]
        if not times:
            continue
        stale = stale or bool(t_stale)
        code = _city_code(times, hid)
        rows = times["villes"].get(code) if code else None
        if not rows or d.day > len(rows):
            continue
        days.append(_row(d, rows[d.day - 1], hijri_for(d, months) if months else None))

    if not any(r["date_iso"] == today.isoformat() for r in days):
        return None
    return {
        "title": title,
        "hijri_month": HIJRI_MONTHS_MA[cur["month"]] if cur else "",
        "hijri_month_num": cur["month"] if cur else 0,
        "hijri_year": str(cur["year"]) if cur else "",
        "days": days,
        "hid": hid,
        "city_ar": HABOUS_CITIES.get(hid, ""),
        "source_url": HABOUS_PUBLIC_URL.format(hid=hid),
        "fetched_at": max((v["fetched_at"] for k, v in _MEM.items()), default=time.time()),
        "stale": stale,
        "hijri_known": bool(cur),
    }
