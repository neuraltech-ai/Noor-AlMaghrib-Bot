# -*- coding: utf-8 -*-
"""
Noor Al-Maghrib (نور المغرب) - Islamic Telegram Bot & Mini App (Moroccan Warsh Edition)
24/7 Production Server for Render.com (Flask + pyTelegramBotAPI + SQLite + Scheduler)
Official Moroccan Prayer Times via AlAdhan API (Method 21: Ministry of Habous & Islamic Affairs)
Full Quran Text & Audio in Riwayat Warsh A'n Nafi' (رواية ورش عن نافع).
"""

import io
import os
import json
import time
import random
import sqlite3
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import telebot
from telebot import types
from flask import Flask, render_template, jsonify, request, Response

from morocco_data import MOROCCO_CITIES, CITY_BY_ID, find_city, calculate_qibla, qibla_direction_ar
from islamic_content import (
    ADHKAR_DATA,
    DUAS_DATA,
    DAILY_INSPIRATIONS,
    ASMA_AL_HUSNA,
    QURAN_RADIOS,
    SURAHS_LIST,
    ADHAN_VOICES,
    HIZB_SURAH_MAP,
    SEERAH_DATA,
    PROPHETS_STORIES_DATA,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("noor_bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip().rstrip("/")
DB_PATH = os.environ.get("DB_PATH", "bot_data.db")
MOROCCO_TZ = ZoneInfo("Africa/Casablanca")

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML") if BOT_TOKEN else None

BOT_USERNAME = ""
if bot:
    try:
        me = bot.get_me()
        BOT_USERNAME = me.username or ""
    except Exception:
        BOT_USERNAME = ""

MONTH_CACHE = {}
SURAH_WARSH_CACHE = {}
CACHE_LOCK = threading.Lock()
DB_LOCK = threading.Lock()

PRAYER_NAMES_AR = {
    "Fajr": "الفجر",
    "Sunrise": "الشروق",
    "Dhuhr": "الظهر",
    "Asr": "العصر",
    "Maghrib": "المغرب",
    "Isha": "العشاء",
}

MONTH_NAMES_AR = {
    1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل",
    5: "ماي", 6: "يونيو", 7: "يوليوز", 8: "غشت",
    9: "شتنبر", 10: "أكتوبر", 11: "نونبر", 12: "دجنبر"
}

ALERT_PRAYERS = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]


# ---------------------------------------------------------------------------
# Database Layer (SQLite)
# ---------------------------------------------------------------------------
def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                city_id TEXT DEFAULT 'oujda',
                alerts_enabled INTEGER DEFAULT 1,
                pre_alert_mins INTEGER DEFAULT 10,
                adhkar_alerts INTEGER DEFAULT 1,
                kahf_alert INTEGER DEFAULT 1,
                fajr_alert INTEGER DEFAULT 1,
                dhuhr_alert INTEGER DEFAULT 1,
                asr_alert INTEGER DEFAULT 1,
                maghrib_alert INTEGER DEFAULT 1,
                isha_alert INTEGER DEFAULT 1,
                tasbih_count INTEGER DEFAULT 0,
                tasbih_phrase_idx INTEGER DEFAULT 0,
                updated_at TEXT
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_alerts (
                chat_id INTEGER,
                alert_key TEXT,
                created_at TEXT,
                PRIMARY KEY (chat_id, alert_key)
            )
            """
        )
        conn.commit()
        conn.close()


def get_user(chat_id: int) -> dict:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
        row = c.fetchone()
        if not row:
            now_str = datetime.now(MOROCCO_TZ).isoformat()
            c.execute(
                "INSERT INTO users (chat_id, city_id, updated_at) VALUES (?, 'oujda', ?)",
                (chat_id, now_str),
            )
            conn.commit()
            c.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
            row = c.fetchone()
        data = dict(row)
        conn.close()
        return data


def update_user(chat_id: int, **kwargs):
    if not kwargs:
        return
    get_user(chat_id)
    allowed = {
        "city_id",
        "alerts_enabled",
        "pre_alert_mins",
        "adhkar_alerts",
        "kahf_alert",
        "fajr_alert",
        "dhuhr_alert",
        "asr_alert",
        "maghrib_alert",
        "isha_alert",
        "tasbih_count",
        "tasbih_phrase_idx",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = datetime.now(MOROCCO_TZ).isoformat()
    cols = ", ".join(f"{k} = ?" for k in updates.keys())
    vals = list(updates.values()) + [chat_id]
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(f"UPDATE users SET {cols} WHERE chat_id = ?", vals)
        conn.commit()
        conn.close()


def get_all_subscribed_users() -> list:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM users WHERE alerts_enabled = 1").fetchall()
        conn.close()
        return [dict(r) for r in rows]


def mark_alert_sent(chat_id: int, alert_key: str) -> bool:
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                "INSERT INTO sent_alerts (chat_id, alert_key, created_at) VALUES (?, ?, ?)",
                (chat_id, alert_key, datetime.now(MOROCCO_TZ).isoformat()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Prayer Times Service & Warsh Quran Service
# ---------------------------------------------------------------------------
def clean_time(t_str: str) -> str:
    return t_str.split(" ")[0].strip() if t_str else "--:--"


def fetch_monthly_times(city_id: str, year: int, month: int) -> list:
    city = CITY_BY_ID.get(city_id) or CITY_BY_ID["oujda"]
    key = (city["id"], year, month)
    with CACHE_LOCK:
        if key in MONTH_CACHE:
            return MONTH_CACHE[key]

    url = f"https://api.aladhan.com/v1/calendar/{year}/{month}"
    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "method": 21,  # Morocco - Ministry of Habous and Islamic Affairs
        "timezonestring": "Africa/Casablanca",
    }
    try:
        resp = requests.get(url, params=params, timeout=12)
        resp.raise_for_status()
        payload = resp.json()
        raw_days = payload.get("data", [])
        parsed = []
        for item in raw_days:
            timings = item.get("timings", {})
            date_info = item.get("date", {})
            greg = date_info.get("gregorian", {})
            hijri = date_info.get("hijri", {})
            parsed.append(
                {
                    "day": int(greg.get("day", 1)),
                    "gregorian": greg.get("date", ""),
                    "weekday_ar": hijri.get("weekday", {}).get("ar", ""),
                    "hijri_day": hijri.get("day", ""),
                    "hijri_month": hijri.get("month", {}).get("ar", ""),
                    "hijri_year": hijri.get("year", ""),
                    "timings": {
                        "Fajr": clean_time(timings.get("Fajr", "")),
                        "Sunrise": clean_time(timings.get("Sunrise", "")),
                        "Dhuhr": clean_time(timings.get("Dhuhr", "")),
                        "Asr": clean_time(timings.get("Asr", "")),
                        "Maghrib": clean_time(timings.get("Maghrib", "")),
                        "Isha": clean_time(timings.get("Isha", "")),
                    },
                }
            )
        if parsed:
            with CACHE_LOCK:
                MONTH_CACHE[key] = parsed
        return parsed
    except Exception as e:
        logger.error("Failed to fetch AlAdhan monthly calendar for %s: %s", city_id, e)
        return []


def fetch_warsh_surah(surah_num: int) -> dict:
    """Fetch Surah verses in authentic Riwayat Warsh A'n Nafi' from jsDelivr CDN."""
    surah_num = max(1, min(114, int(surah_num)))
    with CACHE_LOCK:
        if surah_num in SURAH_WARSH_CACHE:
            return SURAH_WARSH_CACHE[surah_num]

    url = f"https://cdn.jsdelivr.net/gh/fawazahmed0/quran-api@1/editions/ara-quranwarsh/{surah_num}.min.json"
    s_name = dict(SURAHS_LIST).get(surah_num, f"سورة {surah_num}")
    audio_kazabri = f"https://cdn.mp3quran.net/audio/omar-kazabri/r1/{surah_num:03d}.mp3"

    try:
        resp = requests.get(url, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        verses = data.get("chapter", [])
        result = {
            "number": surah_num,
            "name": s_name,
            "riwaya": "رواية ورش عن نافع",
            "audio_url": audio_kazabri,
            "reciter": "الشيخ عمر القزابري (برواية ورش)",
            "verses": [{"verse": v.get("verse"), "text": v.get("text")} for v in verses],
        }
        with CACHE_LOCK:
            SURAH_WARSH_CACHE[surah_num] = result
        return result
    except Exception as e:
        logger.error("Failed to fetch Warsh Surah %s: %s", surah_num, e)
        return {
            "number": surah_num,
            "name": s_name,
            "riwaya": "رواية ورش عن نافع",
            "audio_url": audio_kazabri,
            "reciter": "الشيخ عمر القزابري (برواية ورش)",
            "verses": [],
        }


def get_today_times(city_id: str) -> dict:
    now = datetime.now(MOROCCO_TZ)
    days = fetch_monthly_times(city_id, now.year, now.month)
    for d in days:
        if d["day"] == now.day:
            return d
    return days[0] if days else {}


def get_next_prayer(timings: dict, now: datetime) -> tuple:
    if not timings:
        return ("Fajr", "الفجر", "--:--", "--:--")
    order = ["Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha"]
    for p in order:
        t_str = timings.get(p, "")
        if ":" not in t_str:
            continue
        hh, mm = map(int, t_str.split(":"))
        p_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if p_dt > now:
            diff = int((p_dt - now).total_seconds())
            hrs, rem = divmod(diff, 3600)
            mins, _ = divmod(rem, 60)
            return (p, PRAYER_NAMES_AR[p], t_str, f"{hrs:02d} س {mins:02d} د")
    f_str = timings.get("Fajr", "05:30")
    hh, mm = map(int, f_str.split(":"))
    p_dt = (now + timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    diff = int((p_dt - now).total_seconds())
    hrs, rem = divmod(diff, 3600)
    mins, _ = divmod(rem, 60)
    return ("Fajr", "الفجر", f_str, f"{hrs:02d} س {mins:02d} د")


def get_bot_share_url() -> str:
    global BOT_USERNAME
    if not BOT_USERNAME and bot:
        try:
            BOT_USERNAME = bot.get_me().username or ""
        except Exception:
            pass
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}"
    return WEBAPP_URL or "https://t.me/"


# ---------------------------------------------------------------------------
# Telegram UI Builders
# ---------------------------------------------------------------------------
TASBIH_PHRASES = [
    "سُبْحَانَ اللَّهِ",
    "الْحَمْدُ لِلَّهِ",
    "اللَّهُ أَكْبَرُ",
    "لَا إِلَهَ إِلَّا اللَّهُ",
    "أَسْتَغْفِرُ اللَّهَ الْعَظِيمَ",
    "اللَّهُمَّ صَلِّ وَسَلِّمْ عَلَى نَبِيِّنَا مُحَمَّدٍ",
]


def main_menu_markup(chat_id: int = None):
    kb = types.InlineKeyboardMarkup(row_width=2)
    if WEBAPP_URL:
        kb.add(
            types.InlineKeyboardButton(
                "✨ فتح التطبيق التفاعلي الكامل",
                web_app=types.WebAppInfo(url=f"{WEBAPP_URL}/?chat_id={chat_id or ''}"),
            )
        )
    kb.add(
        types.InlineKeyboardButton("🕌 مواقيت اليوم", callback_data="today"),
        types.InlineKeyboardButton("📅 الحصة الشهرية", callback_data="month_1"),
    )
    kb.add(
        types.InlineKeyboardButton("📖 المصحف (رواية ورش)", callback_data="warsh_menu_0"),
        types.InlineKeyboardButton("📻 إذاعات ورش", callback_data="radio_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("📜 السيرة النبوية الشريفة", callback_data="seerah_menu"),
        types.InlineKeyboardButton("🌟 قصص الأنبياء في القرآن", callback_data="prophets_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("📿 الأذكار اليومية", callback_data="adhkar_menu"),
        types.InlineKeyboardButton("🤲 الأدعية المختارة", callback_data="duas_menu"),
    )
    kb.add(
        types.InlineKeyboardButton("📿 المسبحة الإلكترونية", callback_data="tasbih_view"),
        types.InlineKeyboardButton("🧭 اتجاه القبلة", callback_data="qibla"),
    )
    kb.add(
        types.InlineKeyboardButton("🔔 منبه الصلاة", callback_data="alerts_menu"),
        types.InlineKeyboardButton("📍 تغيير المدينة", callback_data="cities_page_0"),
    )
    share_link = get_bot_share_url()
    share_text = "🌿 بوت نور المغرب الإسلامي: مواقيت الصلاة الرسمية لكل المدن المغربية، المصحف برواية ورش عن نافع، الحصة الشهرية، منبه الأذان، والأذكار:"
    tg_share = f"https://t.me/share/url?url={requests.utils.quote(share_link)}&text={requests.utils.quote(share_text)}"
    kb.add(
        types.InlineKeyboardButton("🌟 آية وحديث", callback_data="daily_insp"),
        types.InlineKeyboardButton("📤 مشاركة البوت", url=tg_share),
    )
    return kb


def format_today_message(user: dict) -> str:
    city = CITY_BY_ID.get(user["city_id"], CITY_BY_ID["oujda"])
    now = datetime.now(MOROCCO_TZ)
    day_data = get_today_times(city["id"])
    if not day_data:
        return "⚠️ تعذر جلب المواقيت حالياً، يرجى المحاولة بعد قليل."

    t = day_data["timings"]
    _, next_ar, next_t, rem_str = get_next_prayer(t, now)
    hijri_str = f"{day_data['weekday_ar']} {day_data['hijri_day']} {day_data['hijri_month']} {day_data['hijri_year']} هـ"

    return (
        f"🕌 <b>مواقيت الصلاة — مدينة {city['ar']}</b>\n"
        f"📆 {hijri_str}\n"
        f"🗓 {day_data['gregorian']}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌌 <b>الفجر:</b>      <code>{t['Fajr']}</code>\n"
        f"🌅 <b>الشروق:</b>    <code>{t['Sunrise']}</code>\n"
        f"☀️ <b>الظهر:</b>      <code>{t['Dhuhr']}</code>\n"
        f"🌤 <b>العصر:</b>      <code>{t['Asr']}</code>\n"
        f"🌇 <b>المغرب:</b>    <code>{t['Maghrib']}</code>\n"
        f"🌙 <b>العشاء:</b>    <code>{t['Isha']}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏳ <b>الصلاة القادمة:</b> {next_ar} ({next_t}) — متبقي <b>{rem_str}</b>\n"
        f"<i>وفق توقيت وزارة الأوقاف والشؤون الإسلامية بالمغرب</i>"
    )


def format_month_chunk(city_id: str, chunk_idx: int) -> tuple:
    now = datetime.now(MOROCCO_TZ)
    city = CITY_BY_ID.get(city_id, CITY_BY_ID["oujda"])
    days = fetch_monthly_times(city_id, now.year, now.month)
    if not days:
        return ("⚠️ تعذر تحميل الحصة الشهرية.", None)

    ranges = [(1, 10), (11, 20), (21, 31)]
    chunk_idx = max(1, min(3, chunk_idx))
    start_d, end_d = ranges[chunk_idx - 1]
    subset = [d for d in days if start_d <= d["day"] <= end_d]

    lines = [
        f"📅 <b>الحصة الشهرية لمواقيت الصلاة — {city['ar']}</b>",
        f"🗓 شهر {now.month:02d}/{now.year} (الأيام {start_d} - {min(end_d, len(days))})",
        "<code>يوم والتاريخ | فجر  | شروق | ظهر  | عصر  | مغرب | عشاء</code>",
        "<code>─────────────┼──────┼──────┼──────┼──────┼──────┼─────</code>",
    ]
    for d in subset:
        t = d["timings"]
        mark = "◀" if d["day"] == now.day else ""
        wday = (d.get("weekday_ar") or "")[:7]
        lines.append(
            f"<b>{d['day']:02d} {wday}{mark}</b>\n"
            f"<code>{t['Fajr']} |{t['Sunrise']} |{t['Dhuhr']} |{t['Asr']} |{t['Maghrib']} |{t['Isha']}</code>"
        )

    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("1 - 10", callback_data="month_1"),
        types.InlineKeyboardButton("11 - 20", callback_data="month_2"),
        types.InlineKeyboardButton("21 - 31", callback_data="month_3"),
    )
    kb.add(
        types.InlineKeyboardButton("📥 إرسال جدول الشهر للطباعة", callback_data="send_print_month"),
    )
    if WEBAPP_URL:
        kb.add(
            types.InlineKeyboardButton(
                "📊 عرض الجدول التفاعلي الكامل",
                web_app=types.WebAppInfo(url=f"{WEBAPP_URL}/?tab=prayers"),
            )
        )
    kb.add(types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home"))
    return ("\n".join(lines), kb)




# Pure-Python Arabic Contextual Shaper & RTL BiDi Reorderer (Zero C/Rust dependencies)
_AR_FORMS = {
    0x0621: (0xFE80, 0xFE80, 0xFE80, 0xFE80),  # ء
    0x0622: (0xFE81, 0xFE82, 0xFE81, 0xFE82),  # آ
    0x0623: (0xFE83, 0xFE84, 0xFE83, 0xFE84),  # أ
    0x0624: (0xFE85, 0xFE86, 0xFE85, 0xFE86),  # ؤ
    0x0625: (0xFE87, 0xFE88, 0xFE87, 0xFE88),  # إ
    0x0626: (0xFE89, 0xFE8A, 0xFE8B, 0xFE8C),  # ئ
    0x0627: (0xFE8D, 0xFE8E, 0xFE8D, 0xFE8E),  # ا
    0x0628: (0xFE8F, 0xFE90, 0xFE91, 0xFE92),  # ب
    0x0629: (0xFE93, 0xFE94, 0xFE93, 0xFE94),  # ة
    0x062A: (0xFE95, 0xFE96, 0xFE97, 0xFE98),  # ت
    0x062B: (0xFE99, 0xFE9A, 0xFE9B, 0xFE9C),  # ث
    0x062C: (0xFE9D, 0xFE9E, 0xFE9F, 0xFEA0),  # ج
    0x062D: (0xFEA1, 0xFEA2, 0xFEA3, 0xFEA4),  # ح
    0x062E: (0xFEA5, 0xFEA6, 0xFEA7, 0xFEA8),  # خ
    0x062F: (0xFEA9, 0xFEAA, 0xFEA9, 0xFEAA),  # د
    0x0630: (0xFEAB, 0xFEAC, 0xFEAB, 0xFEAC),  # ذ
    0x0631: (0xFEAD, 0xFEAE, 0xFEAD, 0xFEAE),  # ر
    0x0632: (0xFEAF, 0xFEB0, 0xFEAF, 0xFEB0),  # ز
    0x0633: (0xFEB1, 0xFEB2, 0xFEB3, 0xFEB4),  # س
    0x0634: (0xFEB5, 0xFEB6, 0xFEB7, 0xFEB8),  # ش
    0x0635: (0xFEB9, 0xFEBA, 0xFEBB, 0xFEBC),  # ص
    0x0636: (0xFEBD, 0xFEBE, 0xFEBF, 0xFEC0),  # ض
    0x0637: (0xFEC1, 0xFEC2, 0xFEC3, 0xFEC4),  # ط
    0x0638: (0xFEC5, 0xFEC6, 0xFEC7, 0xFEC8),  # ظ
    0x0639: (0xFEC9, 0xFECA, 0xFECB, 0xFECC),  # ع
    0x063A: (0xFECD, 0xFECE, 0xFECF, 0xFED0),  # غ
    0x0640: (0x0640, 0x0640, 0x0640, 0x0640),  # ـ
    0x0641: (0xFED1, 0xFED2, 0xFED3, 0xFED4),  # ف
    0x0642: (0xFED5, 0xFED6, 0xFED7, 0xFED8),  # ق
    0x0643: (0xFED9, 0xFEDA, 0xFEDB, 0xFEDC),  # ك
    0x0644: (0xFEDD, 0xFEDE, 0xFEDF, 0xFEE0),  # ل
    0x0645: (0xFEE1, 0xFEE2, 0xFEE3, 0xFEE4),  # م
    0x0646: (0xFEE5, 0xFEE6, 0xFEE7, 0xFEE8),  # ن
    0x0647: (0xFEE9, 0xFEEA, 0xFEEB, 0xFEEC),  # ه
    0x0648: (0xFEED, 0xFEEE, 0xFEED, 0xFEEE),  # و
    0x0649: (0xFEEF, 0xFEF0, 0xFEEF, 0xFEF0),  # ى
    0x064A: (0xFEF1, 0xFEF2, 0xFEF3, 0xFEF4),  # ي
}
_RIGHT_ONLY = {0x0621, 0x0622, 0x0623, 0x0624, 0x0625, 0x0627, 0x0629, 0x062F, 0x0630, 0x0631, 0x0632, 0x0648, 0x0649}
_HARAKAT = set(range(0x064B, 0x065F)) | {0x0670}
_LAM_ALEF = {
    0x0622: (0xFEF5, 0xFEF6),
    0x0623: (0xFEF7, 0xFEF8),
    0x0625: (0xFEF9, 0xFEFA),
    0x0627: (0xFEFB, 0xFEFC),
}


def _can_connect_left(ch_code: int) -> bool:
    return (ch_code in _AR_FORMS) and (ch_code not in _RIGHT_ONLY) and (ch_code != 0x0621)


def _can_connect_right(ch_code: int) -> bool:
    return (ch_code in _AR_FORMS) and (ch_code != 0x0621)


def shape_ar(text: str) -> str:
    """Connect Arabic letters (including Lam-Alef ligatures) and reorder RTL runs for Pillow."""
    if not text:
        return ""
    try:
        from PIL import features
        if features.check("raqm"):
            return str(text)
    except Exception:
        pass

    # Strip harakat for clean tabular PDF rendering
    chars = [c for c in str(text) if ord(c) not in _HARAKAT]
    # 1. Contextual letter shaping + Lam-Alef ligature
    shaped = []
    i = 0
    n = len(chars)
    while i < n:
        c = ord(chars[i])
        prev_c = ord(chars[i - 1]) if i > 0 else 0
        conn_prev = _can_connect_left(prev_c)

        # Check Lam-Alef ligature
        if c == 0x0644 and (i + 1 < n) and (ord(chars[i + 1]) in _LAM_ALEF):
            alef_c = ord(chars[i + 1])
            iso_lig, fin_lig = _LAM_ALEF[alef_c]
            shaped.append(chr(fin_lig if conn_prev else iso_lig))
            i += 2
            continue

        if c in _AR_FORMS:
            next_c = ord(chars[i + 1]) if (i + 1 < n) else 0
            conn_next = _can_connect_left(c) and _can_connect_right(next_c)
            iso, fin, ini, med = _AR_FORMS[c]
            if conn_prev and conn_next:
                shaped.append(chr(med))
            elif conn_prev and not conn_next:
                shaped.append(chr(fin))
            elif not conn_prev and conn_next:
                shaped.append(chr(ini))
            else:
                shaped.append(chr(iso))
        else:
            shaped.append(chars[i])
        i += 1

    s_str = "".join(shaped)
    # If string has no Arabic characters, return as-is
    if not any((0x0600 <= ord(ch) <= 0x06FF) or (0xFE70 <= ord(ch) <= 0xFEFF) for ch in s_str):
        return s_str

    # 2. BiDi RTL line reordering: reverse character stream while keeping LTR digit/Latin runs intact
    mirror = {"(": ")", ")": "(", "[": "]", "]": "["}
    tokens = []
    buf = []
    buf_is_ltr = None
    for ch in s_str:
        code = ord(ch)
        is_ltr = ("0" <= ch <= "9") or ("a" <= ch.lower() <= "z") or (ch in ":/.")
        if buf_is_ltr is None:
            buf_is_ltr = is_ltr
            buf.append(ch)
        elif is_ltr == buf_is_ltr:
            buf.append(ch)
        else:
            tokens.append((buf_is_ltr, "".join(buf)))
            buf = [ch]
            buf_is_ltr = is_ltr
    if buf:
        tokens.append((buf_is_ltr, "".join(buf)))

    out_parts = []
    for is_ltr, tok in reversed(tokens):
        if is_ltr:
            out_parts.append(tok)
        else:
            rev_chars = [mirror.get(c, c) for c in reversed(tok)]
            out_parts.append("".join(rev_chars))
    return "".join(out_parts)


def generate_single_page_a4_pdf(city_id: str, year: int, month: int) -> bytes:
    """Render a crisp 1-page A4 Portrait PDF (1240x1754 px @ 150 DPI) of the monthly timetable."""
    from PIL import Image, ImageDraw, ImageFont

    city = CITY_BY_ID.get(city_id, CITY_BY_ID["oujda"])
    days = fetch_monthly_times(city_id, year, month)
    m_name = MONTH_NAMES_AR.get(month, str(month))

    W, H = 1240, 1754
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    font_paths = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    fp = next((p for p in font_paths if os.path.exists(p)), None)
    f_title = ImageFont.truetype(fp, 28) if fp else ImageFont.load_default()
    f_sub = ImageFont.truetype(fp, 19) if fp else ImageFont.load_default()
    f_th = ImageFont.truetype(fp, 18) if fp else ImageFont.load_default()
    f_td = ImageFont.truetype(fp, 17) if fp else ImageFont.load_default()

    # Header Box
    draw.rounded_rectangle([40, 30, W - 40, 135], radius=14, fill=(4, 36, 28), outline=(212, 175, 55), width=3)
    draw.text((W // 2, 62), shape_ar(f"الحصة الشهرية لمواقيت الصلاة — مدينة {city['ar']} ({city['region']})"), fill=(243, 220, 138), font=f_title, anchor="mm")
    hijri_span = f"{days[0]['hijri_month']} {days[0]['hijri_year']} هـ" if days else ""
    draw.text((W // 2, 105), shape_ar(f"شهر {m_name} {year} م  |  {hijri_span} — وزارة الأوقاف والشؤون الإسلامية بالمغرب"), fill=(230, 245, 238), font=f_sub, anchor="mm")

    # Table Columns (Right-to-Left layout)
    col_Bounds = [
        (1110, 1200, "اليوم"),
        (980, 1110, "الأيام"),
        (800, 980, "التاريخ الهجري"),
        (675, 800, "الفجر"),
        (550, 675, "الشروق"),
        (425, 550, "الظهر"),
        (300, 425, "العصر"),
        (170, 300, "المغرب"),
        (40, 170, "العشاء"),
    ]

    top_y = 150
    th_h = 44
    draw.rectangle([40, top_y, W - 40, top_y + th_h], fill=(8, 51, 40), outline=(4, 36, 28), width=2)
    for x1, x2, label in col_Bounds:
        cx = (x1 + x2) // 2
        draw.text((cx, top_y + th_h // 2), shape_ar(label), fill=(243, 220, 138), font=f_th, anchor="mm")
        draw.line([(x1, top_y), (x1, top_y + th_h)], fill=(212, 175, 55), width=1)

    row_h = 46
    cur_y = top_y + th_h
    for idx, d in enumerate(days[:31]):
        bg = (255, 255, 255) if idx % 2 == 0 else (241, 246, 244)
        draw.rectangle([40, cur_y, W - 40, cur_y + row_h], fill=bg, outline=(203, 213, 225), width=1)
        t = d["timings"]
        vals = [
            str(d["day"]),
            shape_ar(str(d["weekday_ar"])),
            shape_ar(f"{d['hijri_day']} {d['hijri_month']}"),
            t["Fajr"],
            t["Sunrise"],
            t["Dhuhr"],
            t["Asr"],
            t["Maghrib"],
            t["Isha"],
        ]
        for (x1, x2, _), val in zip(col_Bounds, vals):
            cx = (x1 + x2) // 2
            draw.text((cx, cur_y + row_h // 2), val, fill=(4, 36, 28), font=f_td, anchor="mm")
            draw.line([(x1, cur_y), (x1, cur_y + row_h)], fill=(203, 213, 225), width=1)
        cur_y += row_h

    draw.text((W // 2, H - 30), shape_ar("تطبيق وبوت نور المغرب الإسلامي — المواقيت الرسمية ومصحف رواية ورش عن نافع"), fill=(100, 116, 139), font=f_td, anchor="mm")

    out = io.BytesIO()
    img.save(out, format="PDF", resolution=150.0)
    return out.getvalue()


def build_printable_monthly_html(city_id: str, year: int, month: int) -> str:
    city = CITY_BY_ID.get(city_id, CITY_BY_ID["oujda"])
    days = fetch_monthly_times(city_id, year, month)
    m_name = MONTH_NAMES_AR.get(month, str(month))
    hijri_title = ""
    if days:
        hijri_title = f"{days[0]['hijri_month']} {days[0]['hijri_year']} هـ"

    rows_html = []
    for d in days:
        t = d["timings"]
        rows_html.append(
            f"<tr>"
            f"<td><b>{d['day']}</b></td>"
            f"<td>{d['weekday_ar']}</td>"
            f"<td>{d['hijri_day']} {d['hijri_month']}</td>"
            f"<td>{t['Fajr']}</td>"
            f"<td>{t['Sunrise']}</td>"
            f"<td>{t['Dhuhr']}</td>"
            f"<td>{t['Asr']}</td>"
            f"<td>{t['Maghrib']}</td>"
            f"<td>{t['Isha']}</td>"
            f"</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<title>الحصة الشهرية لمواقيت الصلاة - {city['ar']} - {m_name} {year}</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
<style>
  @page {{ size: A4 portrait; margin: 8mm; }}
  body {{ font-family: 'Cairo', sans-serif; color: #04241c; background: #fff; margin: 0; padding: 12px; }}
  .header {{ text-align: center; border: 2px solid #04241c; border-radius: 12px; padding: 10px 16px; margin-bottom: 12px; background: #f7faf8; }}
  .header h1 {{ margin: 0; font-size: 20px; color: #04241c; }}
  .header p {{ margin: 4px 0 0; font-size: 13px; color: #374151; font-weight: 600; }}
  .actions {{ text-align: center; margin-bottom: 12px; }}
  .btn {{ background: #04241c; color: #f3dc8a; border: none; padding: 8px 20px; border-radius: 8px; font-family: 'Cairo', sans-serif; font-weight: 700; cursor: pointer; font-size: 14px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; text-align: center; }}
  th {{ background: #04241c; color: #f3dc8a; padding: 6px 4px; border: 1px solid #04241c; }}
  td {{ padding: 4px; border: 1px solid #cbd5e1; }}
  tr:nth-child(even) {{ background: #f1f5f9; }}
  .footer {{ text-align: center; font-size: 11px; color: #64748b; margin-top: 10px; }}
  @media print {{ .actions {{ display: none; }} body {{ padding: 0; }} }}
</style>
</head>
<body>
  <div class="actions">
    <button class="btn" onclick="downloadSinglePagePDF()">📥 تحميل الحصة الشهرية PDF (صفحة واحدة A4)</button>
    <button class="btn" style="background:#083328;margin-right:8px;" onclick="window.print()">🖨 طباعة مباشرة</button>
  </div>
  <div id="a4PageContainer" style="width:190mm;margin:0 auto;background:#fff;padding:2mm;">
  <div class="header">
    <h1>🕌 الحصة الشهرية لمواقيت الصلاة — مدينة {city['ar']} ({city['region']})</h1>
    <p>شهر {m_name} {year} م  |  {hijri_title} — وفق توقيت وزارة الأوقاف والشؤون الإسلامية بالمغرب</p>
  </div>
  <table>
    <thead>
      <tr>
        <th>اليوم</th><th>الأيام</th><th>التاريخ الهجري</th>
        <th>الفجر</th><th>الشروق</th><th>الظهر</th><th>العصر</th><th>المغرب</th><th>العشاء</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows_html)}
    </tbody>
  </table>
  <div class="footer">تم التوليد عبر تطبيق وبوت نور المغرب الإسلامي — رواية ورش عن نافع</div>
  </div>
  <script>
    function downloadSinglePagePDF() {{
      const el = document.getElementById('a4PageContainer');
      const opt = {{
        margin: [4, 4, 4, 4],
        filename: 'Imsakiya_{city["en"]}_{year}_{month:02d}_A4.pdf',
        image: {{ type: 'jpeg', quality: 0.98 }},
        html2canvas: {{ scale: 2, useCORS: true }},
        jsPDF: {{ unit: 'mm', format: 'a4', orientation: 'portrait' }},
        pagebreak: {{ mode: ['avoid-all'] }}
      }};
      html2pdf().set(opt).from(el).save();
    }}
    window.addEventListener('load', () => {{
      setTimeout(() => {{ try {{ downloadSinglePagePDF(); }} catch(e){{}} }}, 500);
    }});
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Telegram Bot Handlers
# ---------------------------------------------------------------------------
if bot:

    @bot.message_handler(commands=["start", "help", "menu"])
    def handle_start(message):
        user = get_user(message.chat.id)
        text = (
            "🌿 <b>أهلاً بك في بوت نور المغرب الإسلامي</b>\n"
            "رفيقك اليومي لمواقيت الصلاة الرسمية في كل المدن المغربية، "
            "المصحف الشريف برواية ورش عن نافع، منبه الأذان، الأذكار، والقبلة.\n\n"
            + format_today_message(user)
        )
        bot.send_message(message.chat.id, text, reply_markup=main_menu_markup(message.chat.id))

    @bot.message_handler(commands=["city"])
    def handle_city_cmd(message):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            bot.send_message(
                message.chat.id,
                "📍 اكتب اسم المدينة بعد الأمر، مثال:\n<code>/city وجدة</code> أو <code>/city الدار البيضاء</code>",
            )
            return
        found = find_city(parts[1])
        if not found:
            bot.send_message(message.chat.id, "❌ لم يتم العثور على هذه المدينة. جرب البحث بالاسم العربي أو الإنجليزي.")
            return
        update_user(message.chat.id, city_id=found["id"])
        user = get_user(message.chat.id)
        bot.send_message(
            message.chat.id,
            f"✅ تم تغيير مدينتك إلى <b>{found['ar']}</b> ({found['region']}).\n\n" + format_today_message(user),
            reply_markup=main_menu_markup(message.chat.id),
        )

    @bot.message_handler(commands=["month"])
    def handle_month_cmd(message):
        user = get_user(message.chat.id)
        text, kb = format_month_chunk(user["city_id"], 1)
        bot.send_message(message.chat.id, text, reply_markup=kb)

    @bot.callback_query_handler(func=lambda call: True)
    def handle_callbacks(call):
        chat_id = call.message.chat.id
        msg_id = call.message.message_id
        data = call.data
        user = get_user(chat_id)

        try:
            if data == "home" or data == "today":
                bot.edit_message_text(
                    format_today_message(user),
                    chat_id,
                    msg_id,
                    reply_markup=main_menu_markup(chat_id),
                )
            elif data.startswith("month_"):
                idx = int(data.split("_")[1])
                text, kb = format_month_chunk(user["city_id"], idx)
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb)

            elif data == "send_print_month":
                now = datetime.now(MOROCCO_TZ)
                city = CITY_BY_ID.get(user["city_id"], CITY_BY_ID["oujda"])
                bot.answer_callback_query(call.id, "📥 جاري توليد ملف PDF (صفحة واحدة A4)...")
                pdf_bytes = generate_single_page_a4_pdf(city["id"], now.year, now.month)
                doc = io.BytesIO(pdf_bytes)
                doc.name = f"Imsakiya_{city['en']}_{now.year}_{now.month:02d}_A4.pdf"
                bot.send_document(
                    chat_id,
                    doc,
                    caption=f"📄 <b>الحصة الشهرية لمواقيت الصلاة (PDF صفحة واحدة A4) — مدينة {city['ar']}</b>",
                )
                return

            elif data.startswith("warsh_menu_"):
                page = int(data.split("_")[2])
                per_page = 15
                start = page * per_page
                chunk = SURAHS_LIST[start : start + per_page]
                kb = types.InlineKeyboardMarkup(row_width=3)
                btns = [types.InlineKeyboardButton(f"{num}. {name}", callback_data=f"wsurah_{num}_0") for num, name in chunk]
                kb.add(*btns)
                nav = []
                if page > 0:
                    nav.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"warsh_menu_{page - 1}"))
                if start + per_page < len(SURAHS_LIST):
                    nav.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"warsh_menu_{page + 1}"))
                if nav:
                    kb.add(*nav)
                if WEBAPP_URL:
                    kb.add(
                        types.InlineKeyboardButton(
                            "📖 فتح المصحف الكامل والصوتيات في التطبيق",
                            web_app=types.WebAppInfo(url=f"{WEBAPP_URL}/?tab=quran"),
                        )
                    )
                kb.add(types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home"))
                bot.edit_message_text(
                    "📖 <b>المصحف الشريف برواية ورش عن نافع</b>\nاختر السورة لقراءتها أو الاستماع إليها بصوت الشيخ عمر القزابري:",
                    chat_id,
                    msg_id,
                    reply_markup=kb,
                )

            elif data.startswith("wsurah_"):
                _, snum_str, p_str = data.split("_")
                snum = int(snum_str)
                page = int(p_str)
                sdata = fetch_warsh_surah(snum)
                verses = sdata.get("verses", [])
                per_p = 10
                total_p = max(1, (len(verses) + per_p - 1) // per_p)
                page = max(0, min(total_p - 1, page))
                v_chunk = verses[page * per_p : (page + 1) * per_p]

                body = " ".join(f"{v['text']} ﴿{v['verse']}﴾" for v in v_chunk)
                text = (
                    f"📖 <b>سورة {sdata['name']} — رواية ورش عن نافع</b> (صفحة {page + 1}/{total_p})\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{body}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🎧 <a href=\"{sdata['audio_url']}\">استماع وتحميل بصوت الشيخ عمر القزابري (ورش)</a>"
                )
                kb = types.InlineKeyboardMarkup(row_width=2)
                nav = []
                if page > 0:
                    nav.append(types.InlineKeyboardButton("⬅️ الآيات السابقة", callback_data=f"wsurah_{snum}_{page - 1}"))
                if page < total_p - 1:
                    nav.append(types.InlineKeyboardButton("الآيات التالية ➡️", callback_data=f"wsurah_{snum}_{page + 1}"))
                if nav:
                    kb.add(*nav)
                kb.add(types.InlineKeyboardButton("🔙 فهرس سور مصحف ورش", callback_data=f"warsh_menu_{(snum - 1) // 15}"))
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb, disable_web_page_preview=True)

            elif data == "seerah_menu":
                kb = types.InlineKeyboardMarkup(row_width=1)
                for idx, item in enumerate(SEERAH_DATA):
                    kb.add(types.InlineKeyboardButton(f"📜 {idx + 1}. {item['title']}", callback_data=f"seerah_{idx}"))
                if WEBAPP_URL:
                    kb.add(
                        types.InlineKeyboardButton(
                            "📱 فتح جناح السيرة وقصص الأنبياء في التطبيق",
                            web_app=types.WebAppInfo(url=f"{WEBAPP_URL}/?tab=seerah"),
                        )
                    )
                kb.add(types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home"))
                bot.edit_message_text(
                    "📜 <b>جناح السيرة النبوية الشريفة</b>\n"
                    "<i>معتمدة ومحققة من الصحيحين (البخاري ومسلم)، سيرة ابن هشام، زاد المعاد لابن القيم، والفصول في سيرة الرسول للحافظ ابن كثير:</i>",
                    chat_id,
                    msg_id,
                    reply_markup=kb,
                )

            elif data.startswith("seerah_"):
                idx = int(data.split("_")[1])
                idx = max(0, min(len(SEERAH_DATA) - 1, idx))
                item = SEERAH_DATA[idx]
                text = (
                    f"📜 <b>{item['title']}</b>\n"
                    f"🕰 <i>{item['era']}</i>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{item['summary']}\n\n"
                    f"📖 <b>الدليل من القرآن والسنة الصحيحة:</b>\n{item['evidence']}\n\n"
                    f"💎 <b>من فقه السيرة والعبر:</b>\n{item['lessons']}\n\n"
                    f"📚 <b>المصادر المعتمدة:</b> {item['scholars_ref']}"
                )
                kb = types.InlineKeyboardMarkup(row_width=2)
                nav = []
                if idx > 0:
                    nav.append(types.InlineKeyboardButton("⬅️ الفصل السابق", callback_data=f"seerah_{idx - 1}"))
                if idx < len(SEERAH_DATA) - 1:
                    nav.append(types.InlineKeyboardButton("الفصل التالي ➡️", callback_data=f"seerah_{idx + 1}"))
                if nav:
                    kb.add(*nav)
                kb.add(types.InlineKeyboardButton("🔙 فهرس السيرة النبوية", callback_data="seerah_menu"))
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb)

            elif data == "prophets_menu":
                kb = types.InlineKeyboardMarkup(row_width=1)
                for idx, p in enumerate(PROPHETS_STORIES_DATA):
                    kb.add(types.InlineKeyboardButton(f"🌟 {p['name']}", callback_data=f"prophet_{idx}"))
                if WEBAPP_URL:
                    kb.add(
                        types.InlineKeyboardButton(
                            "📱 فتح قصص الأنبياء في التطبيق التفاعلي",
                            web_app=types.WebAppInfo(url=f"{WEBAPP_URL}/?tab=seerah"),
                        )
                    )
                kb.add(types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home"))
                bot.edit_message_text(
                    "🌟 <b>قصص الأنبياء في القرآن الكريم</b>\n"
                    "<i>صافية خالية من الإسرائيليات والأكاذيب — معتمدة على آيات القرآن الكريم والصحيحين وقصص الأنبياء للحافظ ابن كثير وتفسير الشيخ السعدي:</i>",
                    chat_id,
                    msg_id,
                    reply_markup=kb,
                )

            elif data.startswith("prophet_"):
                idx = int(data.split("_")[1])
                idx = max(0, min(len(PROPHETS_STORIES_DATA) - 1, idx))
                p = PROPHETS_STORIES_DATA[idx]
                text = (
                    f"🌟 <b>{p['name']}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{p['story']}\n\n"
                    f"📖 <b>من آيات القصة في القرآن الكريم (برواية ورش):</b>\n{p['quran_ayah']}\n\n"
                    f"💎 <b>العبر والفوائد العقدية والتربوية:</b>\n{p['benefit']}\n\n"
                    f"📚 <b>المواضع القرآنية والمصادر:</b> {p['surahs_ref']}"
                )
                kb = types.InlineKeyboardMarkup(row_width=2)
                nav = []
                if idx > 0:
                    nav.append(types.InlineKeyboardButton("⬅️ القصة السابقة", callback_data=f"prophet_{idx - 1}"))
                if idx < len(PROPHETS_STORIES_DATA) - 1:
                    nav.append(types.InlineKeyboardButton("القصة التالية ➡️", callback_data=f"prophet_{idx + 1}"))
                if nav:
                    kb.add(*nav)
                kb.add(types.InlineKeyboardButton("🔙 فهرس قصص الأنبياء", callback_data="prophets_menu"))
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb)

            elif data == "adhkar_menu":
                kb = types.InlineKeyboardMarkup(row_width=2)
                for key, val in ADHKAR_DATA.items():
                    kb.add(types.InlineKeyboardButton(f"{val['icon']} {val['title']}", callback_data=f"adhkar_{key}_0"))
                kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="home"))
                bot.edit_message_text("📿 <b>اختر قسم الأذكار:</b>", chat_id, msg_id, reply_markup=kb)

            elif data.startswith("adhkar_"):
                _, cat, idx_str = data.split("_")
                idx = int(idx_str)
                section = ADHKAR_DATA.get(cat, ADHKAR_DATA["morning"])
                items = section["items"]
                idx = max(0, min(len(items) - 1, idx))
                item = items[idx]
                text = (
                    f"{section['icon']} <b>{section['title']}</b> ({idx + 1}/{len(items)})\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{item['text']}\n\n"
                    f"🔢 <b>التكرار:</b> {item['count']} مرة\n"
                    f"💡 <i>{item['fadl']}</i>"
                )
                kb = types.InlineKeyboardMarkup(row_width=2)
                nav = []
                if idx > 0:
                    nav.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"adhkar_{cat}_{idx - 1}"))
                if idx < len(items) - 1:
                    nav.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"adhkar_{cat}_{idx + 1}"))
                if nav:
                    kb.add(*nav)
                kb.add(types.InlineKeyboardButton("🔙 أقسام الأذكار", callback_data="adhkar_menu"))
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb)

            elif data == "duas_menu":
                kb = types.InlineKeyboardMarkup(row_width=2)
                for i, cat in enumerate(DUAS_DATA):
                    kb.add(types.InlineKeyboardButton(f"{cat['icon']} {cat['category']}", callback_data=f"dua_{i}"))
                kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="home"))
                bot.edit_message_text("🤲 <b>اختر باب الدعاء:</b>", chat_id, msg_id, reply_markup=kb)

            elif data.startswith("dua_"):
                idx = int(data.split("_")[1])
                cat = DUAS_DATA[idx]
                duas_text = "\n\n".join(f"• {d}" for d in cat["duas"])
                text = f"{cat['icon']} <b>{cat['category']}</b>\n━━━━━━━━━━━━━━━━━━\n{duas_text}"
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("🔙 أبواب الأدعية", callback_data="duas_menu"))
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb)

            elif data == "tasbih_view" or data.startswith("tasbih_"):
                count = user["tasbih_count"]
                p_idx = user["tasbih_phrase_idx"] % len(TASBIH_PHRASES)
                if data == "tasbih_inc":
                    count += 1
                    update_user(chat_id, tasbih_count=count)
                elif data == "tasbih_reset":
                    count = 0
                    update_user(chat_id, tasbih_count=0)
                elif data == "tasbih_next":
                    p_idx = (p_idx + 1) % len(TASBIH_PHRASES)
                    update_user(chat_id, tasbih_phrase_idx=p_idx)

                text = (
                    f"📿 <b>المسبحة الإلكترونية</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"✨ <b>الذكر الحالي:</b>\n{TASBIH_PHRASES[p_idx]}\n\n"
                    f"🔢 <b>العداد:</b> <code>{count}</code>"
                )
                kb = types.InlineKeyboardMarkup(row_width=2)
                kb.add(types.InlineKeyboardButton("➕ سبّح (+1)", callback_data="tasbih_inc"))
                kb.add(
                    types.InlineKeyboardButton("🔄 تغيير الذكر", callback_data="tasbih_next"),
                    types.InlineKeyboardButton("🗑 تصفير", callback_data="tasbih_reset"),
                )
                kb.add(types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home"))
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb)

            elif data == "qibla":
                city = CITY_BY_ID.get(user["city_id"], CITY_BY_ID["oujda"])
                angle = calculate_qibla(city["lat"], city["lon"])
                dir_ar = qibla_direction_ar(angle)
                text = (
                    f"🧭 <b>اتجاه القبلة — مدينة {city['ar']}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📐 <b>الزاوية من الشمال الجغرافي:</b> <code>{angle}°</code>\n"
                    f"🧭 <b>الاتجاه العام:</b> {dir_ar}\n\n"
                    f"<i>توجّه نحو الشمال ثم استدر بزاوية {angle}° باتجاه عقارب الساعة (نحو الشرق).</i>"
                )
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home"))
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb)

            elif data == "radio_menu":
                lines = ["📻 <b>إذاعات القرآن الكريم برواية ورش عن نافع (بث مباشر 24/7):</b>\n"]
                for r in QURAN_RADIOS:
                    lines.append(f"• <a href=\"{r['url']}\">{r['name']}</a>")
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home"))
                bot.edit_message_text("\n".join(lines), chat_id, msg_id, reply_markup=kb, disable_web_page_preview=True)

            elif data == "daily_insp":
                item = random.choice(DAILY_INSPIRATIONS)
                text = (
                    f"🌟 <b>{item['type']} اليوم</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{item['text']}\n\n"
                    f"📚 <i>{item['ref']}</i>"
                )
                kb = types.InlineKeyboardMarkup()
                kb.add(
                    types.InlineKeyboardButton("🔄 آية أو حديث آخر", callback_data="daily_insp"),
                    types.InlineKeyboardButton("🔙 الرئيسية", callback_data="home"),
                )
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb)

            elif data == "alerts_menu" or data.startswith("toggle_"):
                if data == "toggle_all":
                    update_user(chat_id, alerts_enabled=0 if user["alerts_enabled"] else 1)
                elif data == "toggle_adhkar":
                    update_user(chat_id, adhkar_alerts=0 if user["adhkar_alerts"] else 1)
                elif data == "toggle_kahf":
                    update_user(chat_id, kahf_alert=0 if user["kahf_alert"] else 1)
                elif data == "toggle_pre":
                    new_pre = 0 if user["pre_alert_mins"] > 0 else 10
                    update_user(chat_id, pre_alert_mins=new_pre)
                user = get_user(chat_id)

                st_all = "✅ مفعّل" if user["alerts_enabled"] else "❌ معطّل"
                st_pre = f"✅ قبل {user['pre_alert_mins']} د" if user["pre_alert_mins"] > 0 else "❌ معطّل"
                st_adhkar = "✅ مفعّل" if user["adhkar_alerts"] else "❌ معطّل"
                st_kahf = "✅ مفعّل" if user["kahf_alert"] else "❌ معطّل"

                text = (
                    f"🔔 <b>إعدادات منبه الصلاة والتنبيهات</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"• تنبيهات الأذان: {st_all}\n"
                    f"• تذكير قبل الأذان: {st_pre}\n"
                    f"• أذكار الصباح والمساء: {st_adhkar}\n"
                    f"• تذكير سورة الكهف (الجمعة): {st_kahf}"
                )
                kb = types.InlineKeyboardMarkup(row_width=1)
                kb.add(
                    types.InlineKeyboardButton(f"🔔 تنبيهات الأذان: {st_all}", callback_data="toggle_all"),
                    types.InlineKeyboardButton(f"⏳ تذكير قبل الأذان: {st_pre}", callback_data="toggle_pre"),
                    types.InlineKeyboardButton(f"📿 أذكار الصباح والمساء: {st_adhkar}", callback_data="toggle_adhkar"),
                    types.InlineKeyboardButton(f"📖 سورة الكهف يوم الجمعة: {st_kahf}", callback_data="toggle_kahf"),
                    types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home"),
                )
                bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb)

            elif data.startswith("cities_page_"):
                page = int(data.split("_")[2])
                per_page = 12
                start = page * per_page
                chunk = MOROCCO_CITIES[start : start + per_page]
                kb = types.InlineKeyboardMarkup(row_width=3)
                btns = [types.InlineKeyboardButton(c["ar"], callback_data=f"setcity_{c['id']}") for c in chunk]
                kb.add(*btns)
                nav = []
                if page > 0:
                    nav.append(types.InlineKeyboardButton("⬅️ السابق", callback_data=f"cities_page_{page - 1}"))
                if start + per_page < len(MOROCCO_CITIES):
                    nav.append(types.InlineKeyboardButton("التالي ➡️", callback_data=f"cities_page_{page + 1}"))
                if nav:
                    kb.add(*nav)
                kb.add(types.InlineKeyboardButton("🔙 الرئيسية", callback_data="home"))
                bot.edit_message_text(
                    "📍 <b>اختر مدينتك المغربية:</b>\n(أو أرسل <code>/city اسم_المدينة</code>)",
                    chat_id,
                    msg_id,
                    reply_markup=kb,
                )

            elif data.startswith("setcity_"):
                cid = data.split("_", 1)[1]
                if cid in CITY_BY_ID:
                    update_user(chat_id, city_id=cid)
                    user = get_user(chat_id)
                    bot.answer_callback_query(call.id, f"✅ تم اختيار {CITY_BY_ID[cid]['ar']}")
                    bot.edit_message_text(
                        format_today_message(user),
                        chat_id,
                        msg_id,
                        reply_markup=main_menu_markup(chat_id),
                    )
                    return

            bot.answer_callback_query(call.id)
        except Exception as e:
            logger.debug("Callback warning: %s", e)


# ---------------------------------------------------------------------------
# 24/7 Background Prayer Alarm Scheduler
# ---------------------------------------------------------------------------
def scheduler_loop():
    logger.info("Prayer alarm scheduler started.")
    while True:
        try:
            if bot:
                now = datetime.now(MOROCCO_TZ)
                now_hm = now.strftime("%H:%M")
                today_str = now.strftime("%Y-%m-%d")
                users = get_all_subscribed_users()

                for u in users:
                    chat_id = u["chat_id"]
                    city_id = u.get("city_id", "oujda")
                    city = CITY_BY_ID.get(city_id, CITY_BY_ID["oujda"])
                    day_data = get_today_times(city_id)
                    if not day_data:
                        continue
                    timings = day_data.get("timings", {})

                    for p_key in ALERT_PRAYERS:
                        col = f"{p_key.lower()}_alert"
                        if not u.get(col, 1):
                            continue
                        p_time = timings.get(p_key, "")
                        if ":" not in p_time:
                            continue

                        if p_time == now_hm:
                            akey = f"{today_str}_{p_key}_exact"
                            if mark_alert_sent(chat_id, akey):
                                msg = (
                                    f"🕌 <b>حان الآن موعد أذان {PRAYER_NAMES_AR[p_key]}</b>\n"
                                    f"📍 بتوقيت مدينة <b>{city['ar']}</b> ({p_time})\n"
                                    f"🤲 <i>اللهم رب هذه الدعوة التامة والصلاة القائمة آت محمداً الوسيلة والفضيلة...</i>"
                                )
                                try:
                                    bot.send_message(chat_id, msg)
                                except Exception:
                                    pass

                        pre_mins = u.get("pre_alert_mins", 10)
                        if pre_mins > 0:
                            hh, mm = map(int, p_time.split(":"))
                            p_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
                            pre_dt = p_dt - timedelta(minutes=pre_mins)
                            if pre_dt.strftime("%H:%M") == now_hm:
                                akey = f"{today_str}_{p_key}_pre{pre_mins}"
                                if mark_alert_sent(chat_id, akey):
                                    msg = (
                                        f"⏳ <b>تذكير قبل الصلاة</b>\n"
                                        f"تبقى <b>{pre_mins} دقائق</b> على أذان <b>{PRAYER_NAMES_AR[p_key]}</b> "
                                        f"في مدينة {city['ar']} ({p_time})."
                                    )
                                    try:
                                        bot.send_message(chat_id, msg)
                                    except Exception:
                                        pass

                    if u.get("adhkar_alerts", 1):
                        if now_hm == "07:30" and mark_alert_sent(chat_id, f"{today_str}_morning_adhkar"):
                            try:
                                bot.send_message(chat_id, "🌅 <b>تذكير:</b> لا تنسَ قراءة أذكار الصباح لحفظ يومك وبركته.")
                            except Exception:
                                pass
                        if now_hm == "17:30" and mark_alert_sent(chat_id, f"{today_str}_evening_adhkar"):
                            try:
                                bot.send_message(chat_id, "🌙 <b>تذكير:</b> حان وقت أذكار المساء، ألا بذكر الله تطمئن القلوب.")
                            except Exception:
                                pass

                    if u.get("kahf_alert", 1) and now.weekday() == 4 and now_hm == "10:00":
                        if mark_alert_sent(chat_id, f"{today_str}_kahf"):
                            try:
                                bot.send_message(
                                    chat_id,
                                    "🕌 <b>جمعة مباركة!</b>\nلا تنسَ قراءة <b>سورة الكهف</b> (برواية ورش عن نافع) والإكثار من الصلاة على النبي ﷺ.",
                                )
                            except Exception:
                                pass
        except Exception as e:
            logger.error("Scheduler loop error: %s", e)
        time.sleep(30)


def bot_polling_loop():
    if not bot:
        logger.warning("BOT_TOKEN not set; Telegram polling disabled.")
        return
    while True:
        try:
            logger.info("Starting Telegram bot polling...")
            bot.infinity_polling(timeout=60, long_polling_timeout=30, skip_pending=True)
        except Exception as e:
            logger.error("Polling error, restarting in 5s: %s", e)
            time.sleep(5)


# ---------------------------------------------------------------------------
# Flask Routes & Mini App REST APIs
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", bot_share_url=get_bot_share_url())


@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now(MOROCCO_TZ).isoformat()}), 200


@app.route("/print-monthly")
def print_monthly():
    city_id = request.args.get("city", "oujda")
    now = datetime.now(MOROCCO_TZ)
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    chat_id = request.args.get("chat_id", type=int)
    send_tg = request.args.get("send_tg", "0") == "1"

    if request.args.get("format") == "pdf":
        city = CITY_BY_ID.get(city_id, CITY_BY_ID["oujda"])
        pdf_bytes = generate_single_page_a4_pdf(city_id, year, month)
        fname = f"Imsakiya_{city['en']}_{year}_{month:02d}_A4.pdf"
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    html_str = build_printable_monthly_html(city_id, year, month)
    if send_tg and chat_id and bot:
        try:
            city = CITY_BY_ID.get(city_id, CITY_BY_ID["oujda"])
            pdf_bytes = generate_single_page_a4_pdf(city_id, year, month)
            doc = io.BytesIO(pdf_bytes)
            doc.name = f"Imsakiya_{city['en']}_{year}_{month:02d}_A4.pdf"
            bot.send_document(
                chat_id,
                doc,
                caption=f"📄 <b>الحصة الشهرية لمواقيت الصلاة (PDF صفحة واحدة A4) — مدينة {city['ar']}</b>",
            )
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    return Response(html_str, mimetype="text/html; charset=utf-8")


@app.route("/api/cities")
def api_cities():
    return jsonify({"cities": MOROCCO_CITIES})


@app.route("/api/prayer-times")
def api_prayer_times():
    city_id = request.args.get("city", "oujda")
    if city_id not in CITY_BY_ID:
        city_id = "oujda"
    city = CITY_BY_ID[city_id]
    now = datetime.now(MOROCCO_TZ)
    month_days = fetch_monthly_times(city_id, now.year, now.month)
    today = next((d for d in month_days if d["day"] == now.day), month_days[0] if month_days else {})
    qibla = calculate_qibla(city["lat"], city["lon"])
    return jsonify(
        {
            "city": city,
            "qibla_angle": qibla,
            "qibla_dir": qibla_direction_ar(qibla),
            "today": today,
            "monthly": month_days,
            "server_now": now.isoformat(),
            "bot_share_url": get_bot_share_url(),
        }
    )


@app.route("/api/monthly")
def api_monthly():
    city_id = request.args.get("city", "oujda")
    now = datetime.now(MOROCCO_TZ)
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    days = fetch_monthly_times(city_id, year, month)
    return jsonify({"city": CITY_BY_ID.get(city_id, CITY_BY_ID["oujda"]), "year": year, "month": month, "days": days})


@app.route("/api/quran/warsh/<int:surah_num>")
def api_quran_warsh(surah_num):
    return jsonify(fetch_warsh_surah(surah_num))


@app.route("/api/content")
def api_content():
    return jsonify(
        {
            "adhkar": ADHKAR_DATA,
            "duas": DUAS_DATA,
            "inspirations": DAILY_INSPIRATIONS,
            "asma": ASMA_AL_HUSNA,
            "radios": QURAN_RADIOS,
            "adhan_voices": ADHAN_VOICES,
            "seerah": SEERAH_DATA,
            "prophets_stories": PROPHETS_STORIES_DATA,
            "hizb_map": {str(k): {"surah": v[0], "label": v[1]} for k, v in HIZB_SURAH_MAP.items()},
            "surahs": [{"number": n, "name": name} for n, name in SURAHS_LIST],
        }
    )


@app.route("/icon.svg")
def app_icon_svg():
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
      <defs>
        <radialGradient id="bg" cx="50%" cy="30%" r="75%">
          <stop offset="0%" stop-color="#0a3d30"/>
          <stop offset="100%" stop-color="#031914"/>
        </radialGradient>
        <linearGradient id="gold" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stop-color="#f3dc8a"/>
          <stop offset="50%" stop-color="#d4af37"/>
          <stop offset="100%" stop-color="#9a7215"/>
        </linearGradient>
      </defs>
      <rect width="512" height="512" rx="110" fill="url(#bg)"/>
      <circle cx="256" cy="256" r="210" fill="none" stroke="url(#gold)" stroke-width="10" opacity="0.85"/>
      <path d="M225 140 h62 v220 h-62 z" fill="url(#gold)"/>
      <path d="M238 105 h36 v35 h-36 z" fill="url(#gold)"/>
      <circle cx="256" cy="92" r="10" fill="#f3dc8a"/>
      <path d="M160 360 h192 v24 h-192 z" fill="url(#gold)"/>
      <path d="M180 215 a85 85 0 1 0 140 90 a70 70 0 1 1 -140 -90 z" fill="#f3dc8a" opacity="0.85"/>
    </svg>'''
    return Response(svg, mimetype="image/svg+xml")


@app.route("/manifest.json")
def pwa_manifest():
    manifest = {
        "name": "نور المغرب — المواقيت ومصحف ورش",
        "short_name": "نور المغرب",
        "description": "مواقيت الصلاة الرسمية بالمغرب، الحصة الشهرية PDF، المصحف برواية ورش، الأذان، والأذكار",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#031914",
        "theme_color": "#04241c",
        "lang": "ar",
        "dir": "rtl",
        "icons": [
            {"src": "/icon.svg", "sizes": "192x192 512x512", "type": "image/svg+xml", "purpose": "any maskable"}
        ],
    }
    return jsonify(manifest)


@app.route("/sw.js")
def pwa_sw():
    sw_js = """const CACHE_NAME = 'noor-maghrib-v5';
const ASSETS = ['/', '/manifest.json', '/icon.svg', '/api/cities', '/api/content'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(self.clients.claim());
});
self.addEventListener('fetch', e => {
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
"""
    return Response(sw_js, mimetype="application/javascript")


@app.route("/api/user/settings", methods=["GET", "POST"])
def api_user_settings():
    if request.method == "GET":
        chat_id = request.args.get("chat_id", type=int)
        if not chat_id:
            return jsonify({"user": {"city_id": "oujda", "alerts_enabled": 1, "pre_alert_mins": 10, "adhkar_alerts": 1, "kahf_alert": 1}})
        return jsonify({"user": get_user(chat_id)})

    body = request.get_json(silent=True) or {}
    chat_id = body.get("chat_id")
    if chat_id:
        update_user(int(chat_id), **body)
        return jsonify({"ok": True, "user": get_user(int(chat_id))})
    return jsonify({"ok": True})


init_db()
threading.Thread(target=scheduler_loop, daemon=True).start()
threading.Thread(target=bot_polling_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
