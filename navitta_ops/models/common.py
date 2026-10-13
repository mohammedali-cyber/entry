"""Shared helpers: local time in the company time zone, PIN hashing and the fixed choice lists."""
import secrets
from datetime import datetime, timedelta

import pytz
from passlib.context import CryptContext

from odoo import fields

CRYPT = CryptContext(schemes=["pbkdf2_sha512"], deprecated="auto")

WEEKDAYS = [
    ("0", "الأحد"), ("1", "الإثنين"), ("2", "الثلاثاء"), ("3", "الأربعاء"),
    ("4", "الخميس"), ("5", "الجمعة"), ("6", "السبت"),
]

# why a customer did not buy (same order as the branch screen)
LOST_REASONS = [
    ("size_na", "المقاس غير متوفر"),
    ("color_na", "اللون غير متوفر"),
    ("price", "السعر مرتفع"),
    ("design", "التصميم غير مناسب"),
    ("quality", "الجودة لم تعجبهم"),
    ("browsing", "يتصفح فقط"),
    ("other", "أخرى"),
    ("manufacture", "يحتاج تصنيع"),
]
OUT_OF_STOCK = ("size_na", "color_na")

LEAVE_TYPES = [("annual", "سنوية"), ("sick", "مرضية"), ("emergency", "اضطرارية"), ("other", "أخرى")]
TOPICS = [
    ("personal", "طلب شخصي"), ("salary", "راتب أو سلفة"), ("work", "مشكلة في العمل"),
    ("health", "ظرف صحي"), ("suggestion", "اقتراح"), ("other", "أخرى"),
]
SUPPLIES = [("roll_invoice", "رول فاتورة"), ("roll_card", "رول شبكة"), ("bags", "أكياس"), ("other", "أخرى")]
STOCK_SUPPLIES = ("roll_invoice", "roll_card", "bags")
GOODS_REASONS = [("asked", "زبونة طلبته وما توفر"), ("sold_out", "خلص من الفرع"), ("size_missing", "مقاس ناقص"), ("other", "أخرى")]
EXPENSE_CATEGORIES = [
    ("delivery", "توصيل بضاعة"), ("worker", "راتب العامل"), ("cleaning", "نظافة"), ("hospitality", "ضيافة"),
    ("maintenance", "صيانة"), ("cover", "تغطية موظفة"), ("refund", "استرجاع لزبونة"), ("other", "أخرى"),
]
EXCHANGE_REASONS = [("size", "المقاس غير مناسب"), ("defect", "عيب في المنتج"), ("mind", "غيّرت رأيها"), ("color", "اللون مختلف عن المتوقع")]
SHELF = [("excellent", "ممتاز"), ("good", "جيد"), ("needs", "يحتاج ترتيب")]
EMERGENCIES = [("none", "لا يوجد"), ("system", "عطل في الكمبيوتر أو النظام"), ("power", "انقطاع كهرباء"), ("security", "مشكلة أمنية"), ("other", "أخرى")]


def label(selection, key):
    return dict(selection).get(key, key or "")


def company_tz(env):
    return pytz.timezone(env.company.navitta_tz or "Asia/Riyadh")


def local_dt(env, dt=None):
    """A naive UTC datetime (Odoo storage) -> aware datetime in the company time zone."""
    dt = dt or fields.Datetime.now()
    return pytz.utc.localize(dt).astimezone(company_tz(env))


def app_today(env):
    return local_dt(env).date()


def app_minute(env, dt=None):
    """Minutes since local midnight."""
    d = local_dt(env, dt)
    return d.hour * 60 + d.minute


def day_bounds_utc(env, day):
    """[start, end) of a local day as naive UTC datetimes."""
    tz = company_tz(env)
    start = tz.localize(datetime(day.year, day.month, day.day))
    end = tz.normalize(start + timedelta(days=1))
    return start.astimezone(pytz.utc).replace(tzinfo=None), end.astimezone(pytz.utc).replace(tzinfo=None)


def dow(day):
    """0 = Sunday … 6 = Saturday (PostgreSQL extract(dow))."""
    return (day.weekday() + 1) % 7


def hours_to_min(h):
    return int(round((h or 0.0) * 60))


def fmt_time(m):
    """690 -> '11:30 ص', 1410 -> '11:30 م'."""
    h = (m // 60) % 24
    return "%d:%02d %s" % (h % 12 or 12, m % 60, "م" if h >= 12 else "ص")


def iso(dt):
    """Datetime for the browser: ISO in UTC with a Z, or None."""
    return dt and fields.Datetime.to_string(dt).replace(" ", "T") + "Z"


def new_pin():
    """4 random digits, not a run (1234/4321), at least 3 different digits, not a year (19xx/20xx)."""
    while True:
        c = "%04d" % secrets.randbelow(10000)
        d = [int(x) for x in c]
        up = all(d[i] - d[i - 1] == 1 for i in range(1, 4))
        down = all(d[i - 1] - d[i] == 1 for i in range(1, 4))
        if len(set(d)) >= 3 and not up and not down and not c.startswith(("19", "20")):
            return c
