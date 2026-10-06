import logging
import math
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.addons.base.models.res_partner import _tz_get
from odoo.exceptions import ValidationError

from . import aladhan_client
from .prayer_calc import PRAYER_KEYS, PrayerTimesCalculator, round_to_minute

_logger = logging.getLogger(__name__)

AZAN_PRAYERS = ('fajr', 'dhuhr', 'asr', 'maghrib', 'isha')

# Fields that change the computed times: editing one regenerates the timetable.
CALC_FIELDS = {
    'time_source', 'latitude', 'longitude', 'tz', 'method', 'fajr_angle', 'isha_angle', 'isha_minutes',
    'asr_method', 'high_lat_rule', 'ramadan_mode',
    'fajr_offset', 'sunrise_offset', 'dhuhr_offset', 'asr_offset', 'maghrib_offset', 'isha_offset',
}


class PrayerLocation(models.Model):
    _name = 'prayer.location'
    _description = 'Prayer Times Location'
    _order = 'sequence, name, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company', index=True,
                                 default=lambda self: self.env.company,
                                 help="Leave empty to share this location between all companies.")

    # -- Geography -------------------------------------------------------------
    latitude = fields.Float(required=True, digits=(10, 6), help="North is positive.")
    longitude = fields.Float(required=True, digits=(10, 6), help="East is positive.")
    tz = fields.Selection(_tz_get, string='Timezone', required=True,
                          default=lambda self: self.env.user.tz or 'Asia/Riyadh')

    # -- Calculation -----------------------------------------------------------
    time_source = fields.Selection([
        ('aladhan', 'Aladhan API (internet)'),
        ('calculation', 'Built-in calculation (offline)'),
    ], string='Times Source', required=True, default='aladhan',
        help="Aladhan API: the times are downloaded every day from api.aladhan.com "
             "(the Odoo server needs internet access). If the API cannot be reached, "
             "the built-in calculation is used automatically.\n"
             "Built-in calculation: computed inside Odoo, no internet needed.")
    api_last_sync = fields.Datetime(string='Last API Sync', readonly=True, copy=False)
    api_last_error = fields.Char(string='Last API Error', readonly=True, copy=False)
    method = fields.Selection([
        ('umm_al_qura', 'Umm Al-Qura University, Makkah'),
        ('mwl', 'Muslim World League'),
        ('egypt', 'Egyptian General Authority of Survey'),
        ('karachi', 'University of Islamic Sciences, Karachi'),
        ('isna', 'Islamic Society of North America'),
        ('gulf', 'Gulf Region'),
        ('kuwait', 'Kuwait'),
        ('qatar', 'Qatar'),
        ('custom', 'Custom'),
    ], string='Calculation Method', required=True, default='umm_al_qura')
    fajr_angle = fields.Float(default=18.5, digits=(4, 2),
                              help="Sun angle below the horizon for Fajr (Custom method).")
    isha_angle = fields.Float(default=17.0, digits=(4, 2),
                              help="Sun angle below the horizon for Isha (Custom method). "
                                   "Ignored when 'Isha Minutes After Maghrib' is set.")
    isha_minutes = fields.Integer(string='Isha Minutes After Maghrib',
                                  help="Custom method: fixed delay of Isha after Maghrib. "
                                       "Leave 0 to use the Isha angle.")
    asr_method = fields.Selection([
        ('standard', 'Standard (Shafi, Maliki, Hanbali)'),
        ('hanafi', 'Hanafi'),
    ], string='Asr Calculation', required=True, default='standard')
    high_lat_rule = fields.Selection([
        ('none', 'None'),
        ('angle_based', 'Angle Based'),
        ('middle_night', 'Middle of the Night'),
        ('one_seventh', 'One Seventh of the Night'),
    ], string='High Latitude Rule', required=True, default='angle_based',
        help="Fallback for Fajr/Isha in places where the sun does not go deep enough "
             "below the horizon (latitudes above ~48°).")
    ramadan_mode = fields.Boolean(
        string='Ramadan (Isha +30 min)',
        help="Built-in calculation with Umm Al-Qura only: Isha is 120 minutes after Maghrib "
             "instead of 90. Enable it at the start of Ramadan and disable it after. "
             "With the Aladhan API, Ramadan is detected automatically.")

    fajr_offset = fields.Integer(string='Fajr Adjustment')
    sunrise_offset = fields.Integer(string='Sunrise Adjustment')
    dhuhr_offset = fields.Integer(string='Dhuhr Adjustment')
    asr_offset = fields.Integer(string='Asr Adjustment')
    maghrib_offset = fields.Integer(string='Maghrib Adjustment')
    isha_offset = fields.Integer(string='Isha Adjustment')

    # -- Azan --------------------------------------------------------------------
    azan_sound_id = fields.Many2one('prayer.azan.sound', string='Azan Sound', ondelete='set null')
    fajr_azan_sound_id = fields.Many2one('prayer.azan.sound', string='Fajr Azan Sound',
                                         ondelete='set null',
                                         help="Leave empty to use the main azan sound.")
    azan_fajr = fields.Boolean(string='Azan at Fajr', default=True)
    azan_dhuhr = fields.Boolean(string='Azan at Dhuhr', default=True)
    azan_asr = fields.Boolean(string='Azan at Asr', default=True)
    azan_maghrib = fields.Boolean(string='Azan at Maghrib', default=True)
    azan_isha = fields.Boolean(string='Azan at Isha', default=True)

    timetable_ids = fields.One2many('prayer.timetable', 'location_id', string='Timetable')

    # -- Today (computed, not stored) -----------------------------------------
    today_date = fields.Date(compute='_compute_today_times')
    today_fajr = fields.Float(compute='_compute_today_times', string='Fajr')
    today_sunrise = fields.Float(compute='_compute_today_times', string='Sunrise')
    today_dhuhr = fields.Float(compute='_compute_today_times', string='Dhuhr')
    today_asr = fields.Float(compute='_compute_today_times', string='Asr')
    today_maghrib = fields.Float(compute='_compute_today_times', string='Maghrib')
    today_isha = fields.Float(compute='_compute_today_times', string='Isha')

    _sql_constraints = [
        ('latitude_range', 'CHECK(latitude >= -90 AND latitude <= 90)',
         'The latitude must be between -90 and 90.'),
        ('longitude_range', 'CHECK(longitude >= -180 AND longitude <= 180)',
         'The longitude must be between -180 and 180.'),
    ]

    @api.constrains('method', 'fajr_angle', 'isha_angle', 'isha_minutes')
    def _check_custom_method(self):
        for location in self.filtered(lambda loc: loc.method == 'custom'):
            if not 0 < location.fajr_angle <= 30:
                raise ValidationError(self.env._("The Fajr angle must be between 0 and 30 degrees."))
            if location.isha_minutes < 0:
                raise ValidationError(self.env._("Isha minutes after Maghrib cannot be negative."))
            if not location.isha_minutes and not 0 < location.isha_angle <= 30:
                raise ValidationError(self.env._(
                    "Set either an Isha angle between 0 and 30 degrees or Isha minutes after Maghrib."))

    @api.constrains('time_source', 'method')
    def _check_api_method(self):
        for location in self:
            if location.time_source == 'aladhan' and location.method == 'custom':
                raise ValidationError(self.env._(
                    "The Custom method is only available with the built-in calculation."))

    @api.constrains(*['%s_offset' % key for key in PRAYER_KEYS])
    def _check_offsets(self):
        for location in self:
            for key in PRAYER_KEYS:
                if abs(location['%s_offset' % key]) > 120:
                    raise ValidationError(self.env._(
                        "Time adjustments must be between -120 and 120 minutes."))

    def _compute_today_times(self):
        for location in self:
            today = location._local_today()
            location.today_date = today
            try:
                times = location._compute_local_times(today)
            except ValueError:  # incomplete custom method while editing
                times = {}
            for key in PRAYER_KEYS:
                value = times.get(key)
                location['today_%s' % key] = 0.0 if value is None or math.isnan(value) else value

    # ------------------------------------------------------------------------
    # Calculation helpers
    # ------------------------------------------------------------------------
    def _get_calculator(self):
        self.ensure_one()
        return PrayerTimesCalculator(
            self.latitude, self.longitude,
            method=self.method,
            fajr_angle=self.fajr_angle,
            isha_angle=self.isha_angle,
            isha_minutes=self.isha_minutes,
            asr_factor=2 if self.asr_method == 'hanafi' else 1,
            high_lat_rule=self.high_lat_rule,
            ramadan=self.ramadan_mode,
            offsets={key: self['%s_offset' % key] for key in PRAYER_KEYS},
        )

    def _local_today(self):
        self.ensure_one()
        return datetime.now(pytz.timezone(self.tz or 'UTC')).date()

    def _utc_offset_hours(self, day):
        """UTC offset of the location at local noon of `day` (DST aware)."""
        tz = pytz.timezone(self.tz or 'UTC')
        offset = tz.localize(datetime.combine(day, time(12, 0))).utcoffset()
        return offset.total_seconds() / 3600.0

    def _compute_local_times(self, day):
        """Return {prayer key: local decimal hours rounded to the minute} for `day`.

        API locations use the downloaded timetable line of the day, and fall
        back to the built-in calculation when there is none.
        """
        self.ensure_one()
        if self.time_source == 'aladhan' and isinstance(self.id, int):
            line = self.env['prayer.timetable'].search([
                ('location_id', '=', self.id), ('date', '=', day), ('source', '=', 'aladhan'),
            ], limit=1)
            if line:
                return {key: line[key] for key in PRAYER_KEYS}
        times = self._get_calculator().compute(day, self._utc_offset_hours(day))
        return {key: round_to_minute(value) for key, value in times.items()}

    def _compute_utc_datetimes(self, day):
        """Return {prayer key: naive UTC datetime} for `day` (missing if not computable)."""
        self.ensure_one()
        tz = pytz.timezone(self.tz or 'UTC')
        result = {}
        for key, hours in self._compute_local_times(day).items():
            if hours is None or math.isnan(hours):
                continue
            local_naive = datetime.combine(day, time(0, 0)) + timedelta(minutes=round(hours * 60))
            local_dt = tz.localize(local_naive)
            result[key] = local_dt.astimezone(pytz.utc).replace(tzinfo=None)
        return result

    def _get_prayer_names(self):
        _ = self.env._
        return {
            'fajr': _("Fajr"),
            'sunrise': _("Sunrise"),
            'dhuhr': _("Dhuhr"),
            'asr': _("Asr"),
            'maghrib': _("Maghrib"),
            'isha': _("Isha"),
        }

    def _get_azan_sound(self, prayer):
        self.ensure_one()
        if prayer == 'fajr' and self.fajr_azan_sound_id:
            return self.fajr_azan_sound_id
        return self.azan_sound_id

    # ------------------------------------------------------------------------
    # Timetable
    # ------------------------------------------------------------------------
    def _generate_timetable(self, days=30, regenerate=False):
        """Make sure each location has timetable lines from today for `days` days.

        API locations download the times (overwriting older lines); days the
        API could not provide are filled with the built-in calculation.

        :param regenerate: recompute existing lines from today (after a config change)
        """
        Timetable = self.env['prayer.timetable']
        for location in self:
            today = location._local_today()
            days_range = [today + timedelta(days=offset) for offset in range(days)]
            api_times = {}
            if location.time_source == 'aladhan' and location._api_allowed():
                api_times = location._fetch_api_times(days_range)
            domain = [('location_id', '=', location.id), ('date', '>=', today)]
            if regenerate:
                Timetable.search(domain).unlink()
            elif api_times:
                Timetable.search(domain + [('date', 'in', list(api_times))]).unlink()
            existing = set(Timetable.search(domain).mapped('date'))
            vals_list = []
            for day in days_range:
                if day in existing:
                    continue
                if day in api_times:
                    times, source = api_times[day], 'aladhan'
                else:
                    times = location._get_calculator().compute(day, location._utc_offset_hours(day))
                    times = {key: round_to_minute(value) for key, value in times.items()}
                    source = 'calculation'
                vals = {'location_id': location.id, 'date': day, 'source': source}
                for key in PRAYER_KEYS:
                    value = times.get(key)
                    vals[key] = 0.0 if value is None or math.isnan(value) else value
                vals_list.append(vals)
            if vals_list:
                Timetable.create(vals_list)

    def _api_allowed(self):
        # No network call while the module data is loaded (install / update).
        return not self.env.context.get('install_mode') and not self.env.context.get('prayer_no_api')

    def _fetch_api_times(self, days):
        """Download the times of `days` from Aladhan, offsets applied.

        :return: {date: {prayer key: local decimal hours}}, empty on error
        """
        self.ensure_one()
        params = {
            'latitude': self.latitude,
            'longitude': self.longitude,
            'method': aladhan_client.ALADHAN_METHODS[self.method],
            'timezone': self.tz,
            'school': 1 if self.asr_method == 'hanafi' else 0,
            'lat_adjustment': aladhan_client.ALADHAN_LAT_ADJUSTMENT.get(self.high_lat_rule),
        }
        result = {}
        try:
            for year, month in sorted({(day.year, day.month) for day in days}):
                month_data = aladhan_client.fetch_month(year=year, month=month, **params)
                for day in days:
                    if day not in month_data:
                        continue
                    times = dict(month_data[day]['times'])
                    # Umm Al-Qura: Isha 120 minutes after Maghrib in Ramadan
                    # (applied here only if the API did not already do it).
                    if (self.method == 'umm_al_qura'
                            and month_data[day]['hijri_month'] == aladhan_client.RAMADAN_MONTH
                            and times['isha'] - times['maghrib'] < 2.0):
                        times['isha'] = times['maghrib'] + 2.0
                    for key in PRAYER_KEYS:
                        times[key] = round_to_minute(times[key] + self['%s_offset' % key] / 60.0)
                    result[day] = times
        except aladhan_client.AladhanError as error:
            _logger.warning("Prayer location %s (%s): Aladhan API error, built-in calculation "
                            "used instead: %s", self.id, self.name, error)
            self.write({'api_last_error': str(error)[:250]})
            return {}
        self.write({'api_last_sync': fields.Datetime.now(), 'api_last_error': False})
        return result

    def action_generate_timetable(self):
        self._generate_timetable(days=30, regenerate=True)
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Prayer Timetable'),
            'res_model': 'prayer.timetable',
            'view_mode': 'list',
            'domain': [('location_id', 'in', self.ids)],
            'context': {'search_default_upcoming': 1},
        }

    @api.model
    def _cron_generate_timetables(self, days=30, keep_days=90):
        """Daily: extend the timetables and purge old lines."""
        locations = self.search([])
        locations._generate_timetable(days=days)
        limit = fields.Date.context_today(self) - timedelta(days=keep_days)
        self.env['prayer.timetable'].search([('date', '<', limit)]).unlink()
        _logger.info("Prayer timetables generated for %s location(s).", len(locations))

    @api.model_create_multi
    def create(self, vals_list):
        locations = super().create(vals_list)
        locations._generate_timetable()
        return locations

    def write(self, vals):
        res = super().write(vals)
        if CALC_FIELDS.intersection(vals):
            self._generate_timetable(regenerate=True)
        return res

    # ------------------------------------------------------------------------
    # Web client API
    # ------------------------------------------------------------------------
    @api.model
    def _get_user_location(self):
        user = self.env.user
        location = user.prayer_location_id
        if location and location.active and location.has_access('read'):
            return location
        return (self.search([('company_id', '=', self.env.company.id)], limit=1)
                or self.search([('company_id', '=', False)], limit=1))

    @api.model
    def get_user_prayer_schedule(self):
        """Called by the web client: prayer events of today and tomorrow for the user."""
        user = self.env.user
        location = self._get_user_location()
        result = {
            'location': False,
            'notify': user.prayer_notify,
            'play_azan': user.prayer_play_azan,
            'reminder_minutes': user.prayer_reminder_minutes,
            'volume': user.prayer_azan_volume,
            'server_now': fields.Datetime.to_string(fields.Datetime.now()),
            'prayers': [],
        }
        if not location:
            return result
        names = location._get_prayer_names()
        today = location._local_today()
        sounds = {}
        for day in (today, today + timedelta(days=1)):
            for key, utc_dt in location._compute_utc_datetimes(day).items():
                azan = key in AZAN_PRAYERS and location['azan_%s' % key]
                sound = location._get_azan_sound(key) if azan else self.env['prayer.azan.sound']
                if sound:
                    sounds[sound.id] = sound.audio_url
                result['prayers'].append({
                    'key': key,
                    'name': names[key],
                    'date': fields.Date.to_string(day),
                    'time': fields.Datetime.to_string(utc_dt),
                    'azan': bool(azan),
                    'sound_url': sound and sounds[sound.id] or False,
                })
        result['location'] = {'id': location.id, 'name': location.name, 'tz': location.tz}
        return result
