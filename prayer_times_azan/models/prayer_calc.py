"""Astronomical prayer times calculation.

Pure Python, no Odoo dependency, so it can be unit tested on its own.

The sun position uses the low-precision formulas published by the U.S. Naval
Observatory (accurate to about one arc-minute for 1950-2050), which is more
than enough for prayer times rounded to the minute.

All returned times are *local* decimal hours (e.g. 13.5 == 13:30) for the
given UTC offset.
"""
import math

# Sun altitude at sunrise / sunset: refraction (34') + sun semi-diameter (16').
SUNRISE_ANGLE = 0.833

# Calculation methods: Fajr angle, Isha angle or fixed minutes after Maghrib.
METHODS = {
    'umm_al_qura': {'fajr_angle': 18.5, 'isha_minutes': 90},
    'mwl': {'fajr_angle': 18.0, 'isha_angle': 17.0},
    'isna': {'fajr_angle': 15.0, 'isha_angle': 15.0},
    'egypt': {'fajr_angle': 19.5, 'isha_angle': 17.5},
    'karachi': {'fajr_angle': 18.0, 'isha_angle': 18.0},
    'gulf': {'fajr_angle': 19.5, 'isha_minutes': 90},
    'kuwait': {'fajr_angle': 18.0, 'isha_angle': 17.5},
    'qatar': {'fajr_angle': 18.0, 'isha_minutes': 90},
}

# Umm Al-Qura: Isha is 120 minutes after Maghrib during Ramadan.
UMM_AL_QURA_RAMADAN_ISHA_MINUTES = 120

PRAYER_KEYS = ('fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib', 'isha')


# ---------------------------------------------------------------------------
# Degree based trigonometry helpers
# ---------------------------------------------------------------------------
def _dsin(d):
    return math.sin(math.radians(d))


def _dcos(d):
    return math.cos(math.radians(d))


def _dtan(d):
    return math.tan(math.radians(d))


def _darcsin(x):
    return math.degrees(math.asin(x))


def _darccos(x):
    return math.degrees(math.acos(x))


def _darctan2(y, x):
    return math.degrees(math.atan2(y, x))


def _darccot(x):
    return math.degrees(math.atan(1.0 / x))


def _fix(a, b):
    a = a - b * math.floor(a / b)
    return a + b if a < 0 else a


def _fix_angle(a):
    return _fix(a, 360.0)


def _fix_hour(h):
    return _fix(h, 24.0)


def _time_diff(t1, t2):
    return _fix_hour(t2 - t1)


def julian_day(year, month, day):
    if month <= 2:
        year -= 1
        month += 12
    a = math.floor(year / 100)
    b = 2 - a + math.floor(a / 4)
    return (math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1))
            + day + b - 1524.5)


def sun_position(jd):
    """Return (declination, equation of time) for a Julian day."""
    d = jd - 2451545.0
    g = _fix_angle(357.529 + 0.98560028 * d)
    q = _fix_angle(280.459 + 0.98564736 * d)
    lon = _fix_angle(q + 1.915 * _dsin(g) + 0.020 * _dsin(2 * g))
    e = 23.439 - 0.00000036 * d
    ra = _darctan2(_dcos(e) * _dsin(lon), _dcos(lon)) / 15.0
    eqt = q / 15.0 - _fix_hour(ra)
    decl = _darcsin(_dsin(e) * _dsin(lon))
    return decl, eqt


class PrayerTimesCalculator:
    """Compute the daily prayer times for one geographic location.

    :param latitude: degrees, north positive
    :param longitude: degrees, east positive
    :param method: key of METHODS or 'custom'
    :param fajr_angle / isha_angle / isha_minutes: used by 'custom' method
    :param asr_factor: shadow length factor (1 = standard, 2 = Hanafi)
    :param high_lat_rule: 'none', 'middle_night', 'one_seventh', 'angle_based'
    :param ramadan: Umm Al-Qura Isha becomes 120 minutes after Maghrib
    :param offsets: dict prayer key -> minutes added to the computed time
    """

    def __init__(self, latitude, longitude, method='umm_al_qura', fajr_angle=None,
                 isha_angle=None, isha_minutes=None, asr_factor=1,
                 high_lat_rule='angle_based', ramadan=False, offsets=None):
        self.lat = latitude
        self.lng = longitude
        if method == 'custom':
            params = {'fajr_angle': fajr_angle}
            if isha_minutes:
                params['isha_minutes'] = isha_minutes
            else:
                params['isha_angle'] = isha_angle
        else:
            params = dict(METHODS[method])
            if method == 'umm_al_qura' and ramadan:
                params['isha_minutes'] = UMM_AL_QURA_RAMADAN_ISHA_MINUTES
        if not params.get('fajr_angle'):
            raise ValueError("A Fajr angle is required.")
        if not params.get('isha_minutes') and not params.get('isha_angle'):
            raise ValueError("An Isha angle or a number of minutes after Maghrib is required.")
        self.params = params
        self.asr_factor = asr_factor
        self.high_lat_rule = high_lat_rule
        self.offsets = offsets or {}

    # -- astronomical primitives (t is a fraction of the day) --------------
    def _mid_day(self, jd, t):
        eqt = sun_position(jd + t)[1]
        return _fix_hour(12 - eqt)

    def _sun_angle_time(self, jd, angle, t, ccw=False):
        """Time at which the sun reaches `angle` degrees below the horizon.

        Returns NaN when the sun never reaches that angle (high latitudes).
        """
        decl = sun_position(jd + t)[0]
        noon = self._mid_day(jd, t)
        cos_h = (-_dsin(angle) - _dsin(decl) * _dsin(self.lat)) / (_dcos(decl) * _dcos(self.lat))
        if cos_h < -1 or cos_h > 1:
            return float('nan')
        h = _darccos(cos_h) / 15.0
        return noon - h if ccw else noon + h

    def _asr_time(self, jd, t):
        decl = sun_position(jd + t)[0]
        angle = -_darccot(self.asr_factor + _dtan(abs(self.lat - decl)))
        return self._sun_angle_time(jd, angle, t)

    # -- high latitude handling --------------------------------------------
    def _night_portion(self, angle, night):
        if self.high_lat_rule == 'angle_based':
            portion = angle / 60.0
        elif self.high_lat_rule == 'one_seventh':
            portion = 1.0 / 7.0
        else:  # middle_night
            portion = 0.5
        return portion * night

    def _adjust_high_lat(self, time, base, angle, night, ccw=False):
        portion = self._night_portion(angle, night)
        if math.isnan(time):
            return base + (-portion if ccw else portion)
        diff = _time_diff(time, base) if ccw else _time_diff(base, time)
        if diff > portion:
            time = base + (-portion if ccw else portion)
        return time

    # -- public API -----------------------------------------------------------
    def compute(self, day, utc_offset):
        """Return a dict prayer key -> local decimal hours for `day`.

        :param day: datetime.date
        :param utc_offset: UTC offset of the location on that day, in hours
        """
        jd = julian_day(day.year, day.month, day.day) - self.lng / (15.0 * 24.0)
        p = self.params

        # One refinement pass from rough initial guesses (in day fractions).
        fajr = self._sun_angle_time(jd, p['fajr_angle'], 5 / 24.0, ccw=True)
        sunrise = self._sun_angle_time(jd, SUNRISE_ANGLE, 6 / 24.0, ccw=True)
        dhuhr = self._mid_day(jd, 12 / 24.0)
        asr = self._asr_time(jd, 13 / 24.0)
        sunset = self._sun_angle_time(jd, SUNRISE_ANGLE, 18 / 24.0)
        isha = (self._sun_angle_time(jd, p['isha_angle'], 18 / 24.0)
                if p.get('isha_angle') else float('nan'))

        shift = utc_offset - self.lng / 15.0
        fajr, sunrise, dhuhr, asr, sunset, isha = (
            x + shift for x in (fajr, sunrise, dhuhr, asr, sunset, isha))

        if self.high_lat_rule != 'none' and not math.isnan(sunrise) and not math.isnan(sunset):
            night = _time_diff(sunset, sunrise)
            fajr = self._adjust_high_lat(fajr, sunrise, p['fajr_angle'], night, ccw=True)
            if p.get('isha_angle'):
                isha = self._adjust_high_lat(isha, sunset, p['isha_angle'], night)

        maghrib = sunset
        if p.get('isha_minutes'):
            isha = maghrib + p['isha_minutes'] / 60.0

        times = {
            'fajr': fajr, 'sunrise': sunrise, 'dhuhr': dhuhr,
            'asr': asr, 'maghrib': maghrib, 'isha': isha,
        }
        for key, value in times.items():
            times[key] = value + (self.offsets.get(key) or 0) / 60.0
        return times


def round_to_minute(hours):
    """Round decimal hours to the nearest whole minute (NaN preserved)."""
    if hours is None or math.isnan(hours):
        return hours
    return round(hours * 60) / 60.0
