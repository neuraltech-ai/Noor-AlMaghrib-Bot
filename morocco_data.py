# -*- coding: utf-8 -*-
"""
Moroccan Cities Database & Qibla Calculator
Covers 55+ major cities across all 12 regions of Morocco with exact coordinates.
"""

import math

KAABA_LAT = 21.422487
KAABA_LON = 39.826206

MOROCCO_CITIES = [
    {"id": "oujda", "ar": "وجدة", "en": "Oujda", "region": "الشرق", "lat": 34.6814, "lon": -1.9086},
    {"id": "casablanca", "ar": "الدار البيضاء", "en": "Casablanca", "region": "الدار البيضاء-سطات", "lat": 33.5731, "lon": -7.5898},
    {"id": "rabat", "ar": "الرباط", "en": "Rabat", "region": "الرباط-سلا-القنيطرة", "lat": 34.0209, "lon": -6.8416},
    {"id": "fes", "ar": "فاس", "en": "Fes", "region": "فاس-مكناس", "lat": 34.0181, "lon": -5.0078},
    {"id": "marrakech", "ar": "مراكش", "en": "Marrakech", "region": "مراكش-آسفي", "lat": 31.6295, "lon": -7.9811},
    {"id": "tangier", "ar": "طنجة", "en": "Tangier", "region": "طنجة-تطوان-الحسيمة", "lat": 35.7595, "lon": -5.8340},
    {"id": "agadir", "ar": "أكادير", "en": "Agadir", "region": "سوس-ماسة", "lat": 30.4278, "lon": -9.5981},
    {"id": "meknes", "ar": "مكناس", "en": "Meknes", "region": "فاس-مكناس", "lat": 33.8935, "lon": -5.5473},
    {"id": "tetouan", "ar": "تطوان", "en": "Tetouan", "region": "طنجة-تطوان-الحسيمة", "lat": 35.5889, "lon": -5.3626},
    {"id": "nador", "ar": "الناظور", "en": "Nador", "region": "الشرق", "lat": 35.1681, "lon": -2.9335},
    {"id": "berkane", "ar": "بركان", "en": "Berkane", "region": "الشرق", "lat": 34.9200, "lon": -2.3200},
    {"id": "kenitra", "ar": "القنيطرة", "en": "Kenitra", "region": "الرباط-سلا-القنيطرة", "lat": 34.2610, "lon": -6.5802},
    {"id": "sale", "ar": "سلا", "en": "Salé", "region": "الرباط-سلا-القنيطرة", "lat": 34.0531, "lon": -6.7985},
    {"id": "temara", "ar": "تمارة", "en": "Temara", "region": "الرباط-سلا-القنيطرة", "lat": 33.9287, "lon": -6.9066},
    {"id": "mohammedia", "ar": "المحمدية", "en": "Mohammedia", "region": "الدار البيضاء-سطات", "lat": 33.6866, "lon": -7.3830},
    {"id": "el_jadida", "ar": "الجديدة", "en": "El Jadida", "region": "الدار البيضاء-سطات", "lat": 33.2316, "lon": -8.5007},
    {"id": "safi", "ar": "آسفي", "en": "Safi", "region": "مراكش-آسفي", "lat": 32.2994, "lon": -9.2372},
    {"id": "beni_mellal", "ar": "بني ملال", "en": "Beni Mellal", "region": "بني ملال-خنيفرة", "lat": 32.3373, "lon": -6.3498},
    {"id": "khouribga", "ar": "خريبكة", "en": "Khouribga", "region": "بني ملال-خنيفرة", "lat": 32.8811, "lon": -6.9063},
    {"id": "taza", "ar": "تازة", "en": "Taza", "region": "فاس-مكناس", "lat": 34.2100, "lon": -4.0100},
    {"id": "settat", "ar": "سطات", "en": "Settat", "region": "الدار البيضاء-سطات", "lat": 33.0010, "lon": -7.6166},
    {"id": "larache", "ar": "العرائش", "en": "Larache", "region": "طنجة-تطوان-الحسيمة", "lat": 35.1932, "lon": -6.1557},
    {"id": "ksar_el_kebir", "ar": "القصر الكبير", "en": "Ksar El Kebir", "region": "طنجة-تطوان-الحسيمة", "lat": 35.0004, "lon": -5.9038},
    {"id": "al_hoceima", "ar": "الحسيمة", "en": "Al Hoceima", "region": "طنجة-تطوان-الحسيمة", "lat": 35.2517, "lon": -3.9372},
    {"id": "chefchaouen", "ar": "شفشاون", "en": "Chefchaouen", "region": "طنجة-تطوان-الحسيمة", "lat": 35.1688, "lon": -5.2636},
    {"id": "essaouira", "ar": "الصويرة", "en": "Essaouira", "region": "مراكش-آسفي", "lat": 31.5085, "lon": -9.7595},
    {"id": "ouarzazate", "ar": "ورزازات", "en": "Ouarzazate", "region": "درعة-تافيلالت", "lat": 30.9189, "lon": -6.8934},
    {"id": "errachidia", "ar": "الرشيدية", "en": "Errachidia", "region": "درعة-تافيلالت", "lat": 31.9314, "lon": -4.4266},
    {"id": "taourirt", "ar": "تاوريرت", "en": "Taourirt", "region": "الشرق", "lat": 34.4073, "lon": -2.8973},
    {"id": "guercif", "ar": "جرسيف", "en": "Guercif", "region": "الشرق", "lat": 34.2257, "lon": -3.3536},
    {"id": "jerada", "ar": "جرادة", "en": "Jerada", "region": "الشرق", "lat": 34.3100, "lon": -2.1600},
    {"id": "figuig", "ar": "فجيج", "en": "Figuig", "region": "الشرق", "lat": 32.1092, "lon": -1.2298},
    {"id": "driouch", "ar": "الدريوش", "en": "Driouch", "region": "الشرق", "lat": 34.9765, "lon": -3.3886},
    {"id": "taroudant", "ar": "تارودانت", "en": "Taroudant", "region": "سوس-ماسة", "lat": 30.4703, "lon": -8.8770},
    {"id": "tiznit", "ar": "تيزنيت", "en": "Tiznit", "region": "سوس-ماسة", "lat": 29.6974, "lon": -9.7316},
    {"id": "guelmim", "ar": "كلميم", "en": "Guelmim", "region": "كلميم-واد نون", "lat": 28.9870, "lon": -10.0574},
    {"id": "tan_tan", "ar": "طانطان", "en": "Tan-Tan", "region": "كلميم-واد نون", "lat": 28.4380, "lon": -11.1032},
    {"id": "laayoune", "ar": "العيون", "en": "Laayoune", "region": "العيون-الساقية الحمراء", "lat": 27.1253, "lon": -13.1625},
    {"id": "smara", "ar": "السمارة", "en": "Smara", "region": "العيون-الساقية الحمراء", "lat": 26.7384, "lon": -11.6719},
    {"id": "boujdour", "ar": "بوجدور", "en": "Boujdour", "region": "العيون-الساقية الحمراء", "lat": 26.1258, "lon": -14.4842},
    {"id": "dakhla", "ar": "الداخلة", "en": "Dakhla", "region": "الداخلة-وادي الذهب", "lat": 23.6848, "lon": -15.9580},
    {"id": "ifrane", "ar": "إفران", "en": "Ifrane", "region": "فاس-مكناس", "lat": 33.5228, "lon": -5.1110},
    {"id": "sefrou", "ar": "صفرو", "en": "Sefrou", "region": "فاس-مكناس", "lat": 33.8318, "lon": -4.8353},
    {"id": "azrou", "ar": "أزرو", "en": "Azrou", "region": "فاس-مكناس", "lat": 33.4344, "lon": -5.2213},
    {"id": "khenifra", "ar": "خنيفرة", "en": "Khenifra", "region": "بني ملال-خنيفرة", "lat": 32.9391, "lon": -5.6675},
    {"id": "fqih_ben_salah", "ar": "الفقيه بن صالح", "en": "Fquih Ben Salah", "region": "بني ملال-خنيفرة", "lat": 32.5021, "lon": -6.6877},
    {"id": "berrechid", "ar": "برشيد", "en": "Berrechid", "region": "الدار البيضاء-سطات", "lat": 33.2655, "lon": -7.5875},
    {"id": "sidi_kacem", "ar": "سيدي قاسم", "en": "Sidi Kacem", "region": "الرباط-سلا-القنيطرة", "lat": 34.2215, "lon": -5.7077},
    {"id": "sidi_slimane", "ar": "سيدي سليمان", "en": "Sidi Slimane", "region": "الرباط-سلا-القنيطرة", "lat": 34.2648, "lon": -5.9260},
    {"id": "ouazzane", "ar": "وزان", "en": "Ouazzane", "region": "طنجة-تطوان-الحسيمة", "lat": 34.7972, "lon": -5.5786},
    {"id": "midelt", "ar": "ميدلت", "en": "Midelt", "region": "درعة-تافيلالت", "lat": 32.6852, "lon": -4.7451},
    {"id": "tinghir", "ar": "تنغير", "en": "Tinghir", "region": "درعة-تافيلالت", "lat": 31.5147, "lon": -5.5328},
    {"id": "zagora", "ar": "زاكورة", "en": "Zagora", "region": "درعة-تافيلالت", "lat": 30.3324, "lon": -5.8384},
    {"id": "tata", "ar": "طاطا", "en": "Tata", "region": "سوس-ماسة", "lat": 29.7428, "lon": -7.9725},
    {"id": "youssoufia", "ar": "اليوسفية", "en": "Youssoufia", "region": "مراكش-آسفي", "lat": 32.2463, "lon": -8.5292},
]

CITY_BY_ID = {c["id"]: c for c in MOROCCO_CITIES}


def find_city(query: str):
    """Find a Moroccan city by id, Arabic name, or English name (fuzzy)."""
    if not query:
        return CITY_BY_ID["oujda"]
    q = query.strip().lower()
    if q in CITY_BY_ID:
        return CITY_BY_ID[q]
    for c in MOROCCO_CITIES:
        if q == c["ar"] or q == c["en"].lower():
            return c
    for c in MOROCCO_CITIES:
        if q in c["ar"] or q in c["en"].lower():
            return c
    return None


def calculate_qibla(lat: float, lon: float) -> float:
    """Calculate Qibla bearing in degrees (0-360, clockwise from North)."""
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.radians(KAABA_LAT)
    lon2 = math.radians(KAABA_LON)
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(y, x))
    return round((bearing + 360) % 360, 1)


def qibla_direction_ar(angle: float) -> str:
    """Human-readable Arabic compass direction for Qibla."""
    if 67.5 <= angle < 112.5:
        return "شرقاً (يميل قليلاً للشرق)"
    if 112.5 <= angle < 157.5:
        return "جنوب شرق"
    if 22.5 <= angle < 67.5:
        return "شمال شرق"
    return "اتجاه القبلة"
