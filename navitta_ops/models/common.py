"""Shared helpers: local time in the company time zone, PIN hashing and the fixed choice lists."""
import math
import secrets
from datetime import datetime, timedelta

import pytz
from passlib.context import CryptContext

from odoo import fields
from odoo.tools.translate import LazyGettext, LazyTranslate

_lt = LazyTranslate(__name__)

CRYPT = CryptContext(schemes=["pbkdf2_sha512"], deprecated="auto")

WEEKDAYS = [
    ("0", "Sunday"), ("1", "Monday"), ("2", "Tuesday"), ("3", "Wednesday"),
    ("4", "Thursday"), ("5", "Friday"), ("6", "Saturday"),
]

# why a customer did not buy (same order as the branch screen)
LOST_REASONS = [
    ("size_na", "Size not available"),
    ("color_na", "Color not available"),
    ("price", "Price too high"),
    ("design", "Design not suitable"),
    ("quality", "Didn't like the quality"),
    ("browsing", "Just browsing"),
    ("other", "Other"),
    ("manufacture", "Needs manufacturing"),
]
OUT_OF_STOCK = ("size_na", "color_na")

LEAVE_TYPES = [("annual", "Annual"), ("sick", "Sick"), ("emergency", "Emergency"), ("other", "Other")]
TOPICS = [
    ("personal", "Personal request"), ("salary", "Salary or advance"), ("work", "Work problem"),
    ("health", "Health matter"), ("suggestion", "Suggestion"), ("other", "Other"),
]
SUPPLIES = [("roll_invoice", "Receipt roll"), ("roll_card", "Card roll"), ("bags", "Bags"), ("other", "Other")]
STOCK_SUPPLIES = ("roll_invoice", "roll_card", "bags")
# not a selection field: the label is written into the request's free-text reason, so translate it lazily
GOODS_REASONS = [("asked", _lt("Customer asked, not available")), ("sold_out", _lt("Sold out at the branch")),
                 ("size_missing", _lt("Missing size")), ("other", _lt("Other"))]
EXPENSE_CATEGORIES = [
    ("delivery", "Goods delivery"), ("worker", "Worker's salary"), ("cleaning", "Cleaning"), ("hospitality", "Hospitality"),
    ("maintenance", "Maintenance"), ("cover", "Employee cover"), ("refund", "Customer refund"), ("other", "Other"),
]
EXCHANGE_REASONS = [("size", "Wrong size"), ("defect", "Product defect"), ("mind", "Changed her mind"), ("color", "Color not as expected")]
SHELF = [("excellent", "Excellent"), ("good", "Good"), ("needs", "Needs tidying")]
EMERGENCIES = [("none", "None"), ("system", "Computer or system failure"), ("power", "Power outage"), ("security", "Security issue"), ("other", "Other")]


# Stored values: the cash register sync (navitta_ops_pos) writes these into navitta.employee.sales.cashier for sales
# made without a cashier, and the lines are compared with them. They stay as they are (Arabic) in the database;
# cashier_text() shows them in the user's language.
CASHIER_SHARED = "الحساب العام: %s"        # + the POS user names
CASHIER_NONE = "بدون كاشير"


def cashier_text(env, cashier):
    """Display text of a stored cashier name: the two stored markers above are translated, real names kept."""
    if not cashier or cashier == CASHIER_NONE:
        return env._("No cashier")
    prefix = CASHIER_SHARED % ""
    if cashier.startswith(prefix):
        return env._("Shared account: %s", cashier[len(prefix):])
    return cashier


def is_shared_cashier(cashier):
    return bool(cashier) and cashier.startswith(CASHIER_SHARED % "")


def label(selection, key, env=None):
    """Label of `key` in a (key, label) table; lazy (_lt) labels are translated (in env's language if given)."""
    v = dict(selection).get(key)
    if v is None:
        return key or ""
    if isinstance(v, LazyGettext):
        return env._(v) if env is not None else str(v)
    return v


def choices(env, selection):
    """A (key, label) table as JSON-ready [key, label] pairs with translated labels (for the screens)."""
    return [[k, label(selection, k, env)] for k, _v in selection]


def selection(env, model, field):
    """[key, label] pairs of a selection field, labels in env's language."""
    return [[k, v] for k, v in env[model]._fields[field]._description_selection(env)]


def sel_label(env, model, field, key):
    """Translated label of one value of a selection field (env's language)."""
    return dict(env[model]._fields[field]._description_selection(env)).get(key, key or "")


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


AM, PM = _lt("AM"), _lt("PM")


def fmt_time(m, env=None):
    """690 -> '11:30 AM', 1410 -> '11:30 PM' (AM/PM translated in env's language if given)."""
    h = (m // 60) % 24
    suffix = PM if h >= 12 else AM
    return "%d:%02d %s" % (h % 12 or 12, m % 60, env._(suffix) if env is not None else str(suffix))


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


def differs(counted, system):
    """The counted cash / card settlement doesn't match the system's figure: as on the site, both rounded to the riyal
    (a difference under half a riyal is no difference)."""
    return math.floor((counted or 0.0) + 0.5) != math.floor((system or 0.0) + 0.5)
