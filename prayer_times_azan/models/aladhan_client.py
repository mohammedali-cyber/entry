"""Minimal client for the public Aladhan prayer times API (https://aladhan.com).

No API key is needed. Only the monthly calendar by coordinates is used:
    GET https://api.aladhan.com/v1/calendar
        ?latitude=&longitude=&year=&month=&method=&school=
         &latitudeAdjustmentMethod=&timezonestring=
Each day of the response contains ``timings`` ("Fajr": "04:29 (+03)", ...)
and ``date.gregorian.date`` ("DD-MM-YYYY") / ``date.hijri.month.number``.
"""
from datetime import datetime

import requests

ALADHAN_CALENDAR_URL = 'https://api.aladhan.com/v1/calendar'
TIMEOUT = 15

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

RAMADAN_MONTH = 9


class AladhanError(Exception):
    """The API could not be reached or returned unusable data."""


def _parse_time(value):
    """'04:29 (+03)' -> 4.4833 (local decimal hours)."""
    hours, minutes = value.split()[0].split(':')
    return int(hours) + int(minutes) / 60.0


def fetch_month(latitude, longitude, year, month, method, timezone, school=0,
                lat_adjustment=None, session=None):
    """Fetch one month of prayer times.

    :return: {date: {'times': {prayer key: local decimal hours}, 'hijri_month': int}}
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
    try:
        response = (session or requests).get(
            ALADHAN_CALENDAR_URL, params=params, timeout=TIMEOUT,
            headers={'User-Agent': 'Odoo prayer_times_azan'})
    except requests.RequestException as error:
        raise AladhanError("Cannot connect to api.aladhan.com (%s)" % type(error).__name__) from error
    if response.status_code != 200:
        raise AladhanError("HTTP %s from the Aladhan API" % response.status_code)
    try:
        payload = response.json()
        days = payload['data']
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
            result[day_date] = {
                'times': times,
                'hijri_month': int(day['date']['hijri']['month']['number']),
            }
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise AladhanError("Unexpected answer from the Aladhan API: %s" % error) from error
    if not result:
        raise AladhanError("Empty answer from the Aladhan API")
    return result
