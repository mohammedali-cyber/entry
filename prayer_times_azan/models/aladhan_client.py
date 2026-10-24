"""Minimal client for the public Aladhan prayer times API (https://aladhan.com).

No API key is needed. Only the monthly calendar by coordinates is used:
    GET {base}/calendar
        ?latitude=&longitude=&year=&month=&method=&school=
         &latitudeAdjustmentMethod=&timezonestring=&calendarMethod=
Each day of the response contains ``timings`` ("Fajr": "04:29 (+03)", ...),
``date.gregorian.date`` ("DD-MM-YYYY") and ``date.hijri`` (day, month.number,
year). On error the API answers e.g. HTTP 400 with ``data`` as a text message.
"""
import logging
from datetime import datetime

import requests

_logger = logging.getLogger(__name__)

# Main server first, then the mirrors listed in the official OpenAPI file.
ALADHAN_BASE_URLS = (
    'https://api.aladhan.com/v1',
    'https://aladhan.api.islamic.network/v1',
    'https://aladhan.api.alislam.ru/v1',
)
TIMEOUT = (5, 20)  # connect, read (seconds)

# Our calculation method key -> Aladhan method id.
ALADHAN_METHODS = {
    'karachi': 1,
    'isna': 2,
    'mwl': 3,
    'umm_al_qura': 4,
    'egypt': 5,
    'gulf': 8,
    'kuwait': 9,
    'qatar': 10,
}

# Our high latitude rule -> Aladhan latitudeAdjustmentMethod ('none' has no
# equivalent: the API default, angle based, is used).
ALADHAN_LAT_ADJUSTMENT = {
    'middle_night': 1,
    'one_seventh': 2,
    'angle_based': 3,
}

TIMING_KEYS = {
    'fajr': 'Fajr',
    'sunrise': 'Sunrise',
    'dhuhr': 'Dhuhr',
    'asr': 'Asr',
    'maghrib': 'Maghrib',
    'isha': 'Isha',
}

# Hijri calendar used by the API for ``date.hijri`` (HJCoSA, UAQ, DIYANET or
# MATHEMATICAL). UAQ = Umm Al-Qura, the official calendar of Saudi Arabia.
UMM_AL_QURA_CALENDAR = 'UAQ'

RAMADAN_MONTH = 9


class AladhanError(Exception):
    """The API could not be reached or returned unusable data."""


def _parse_time(value):
    """'04:29 (+03)' -> 4.4833 (local decimal hours)."""
    hours, minutes = value.split()[0].split(':')
    return int(hours) + int(minutes) / 60.0


def _get(path, params, session=None):
    """GET `path` on the main server, falling back to the mirrors on network
    errors, rate limiting (429) and server errors (5xx).

    :return: the ``data`` of the JSON answer
    :raise AladhanError: when no server answered, or on a request error (4xx)
    """
    errors = []
    for base_url in ALADHAN_BASE_URLS:
        host = base_url.split('/')[2]
        try:
            response = (session or requests).get(
                base_url + path, params=params, timeout=TIMEOUT,
                headers={'User-Agent': 'Odoo prayer_times_azan', 'Accept-Encoding': 'gzip'})
        except requests.RequestException as error:
            errors.append("%s: %s" % (host, type(error).__name__))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            errors.append("%s: HTTP %s" % (host, response.status_code))
            continue
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code != 200:
            # Bad request: the same request would fail on the mirrors too.
            message = payload.get('data') if isinstance(payload, dict) else None
            if not isinstance(message, str):
                message = "HTTP %s" % response.status_code
            raise AladhanError("Aladhan API: %s" % message)
        if not isinstance(payload, dict) or 'data' not in payload:
            raise AladhanError("Unexpected answer from the Aladhan API")
        if errors:
            _logger.info("Aladhan API answered from %s after: %s", host, "; ".join(errors))
        return payload['data']
    raise AladhanError("Cannot connect to the Aladhan API (%s)" % "; ".join(errors))


def fetch_month(latitude, longitude, year, month, method, timezone, school=0,
                lat_adjustment=None, calendar_method=None, session=None):
    """Fetch one month of prayer times.

    :return: {date: {'times': {prayer key: local decimal hours},
                     'hijri': (day, month, year)}}
    :raise AladhanError: on network, HTTP or format errors
    """
    params = {
        'latitude': latitude,
        'longitude': longitude,
        'year': year,
        'month': month,
        'method': method,
        'school': school,
        'timezonestring': timezone,
    }
    if lat_adjustment:
        params['latitudeAdjustmentMethod'] = lat_adjustment
    if calendar_method:
        params['calendarMethod'] = calendar_method
    days = _get('/calendar', params, session=session)
    try:
        result = {}
        for day in days:
            day_date = datetime.strptime(day['date']['gregorian']['date'], '%d-%m-%Y').date()
            times = {}
            previous = None
            for key, api_key in TIMING_KEYS.items():
                hours = _parse_time(day['timings'][api_key])
                # Isha after midnight (high latitudes): keep the order of the day.
                if previous is not None and hours < previous:
                    hours += 24
                times[key] = previous = hours
            hijri = day['date']['hijri']
            result[day_date] = {
                'times': times,
                'hijri': (int(hijri['day']), int(hijri['month']['number']), int(hijri['year'])),
            }
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise AladhanError("Unexpected answer from the Aladhan API: %s" % error) from error
    if not result:
        raise AladhanError("Empty answer from the Aladhan API")
    return result
