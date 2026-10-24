import base64
import calendar
from datetime import date, timedelta
from unittest.mock import patch

import requests

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models import aladhan_client, prayer_location

# Minimal MPEG audio frame header, enough for the constraint (extension based).
FAKE_MP3 = base64.b64encode(b'ID3\x03\x00\x00\x00\x00\x00\x00' + b'\xff\xfb\x90\x00' * 64)


@tagged('post_install', '-at_install')
class TestPrayerTimes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sound = cls.env['prayer.azan.sound'].create({
            'name': 'Test Azan', 'audio_file': FAKE_MP3, 'audio_filename': 'azan.mp3',
        })
        cls.fajr_sound = cls.env['prayer.azan.sound'].create({
            'name': 'Test Fajr Azan', 'audio_file': FAKE_MP3, 'audio_filename': 'fajr.mp3',
        })
        cls.location = cls.env['prayer.location'].create({
            'name': 'Jeddah Test',
            'time_source': 'calculation',
            'latitude': 21.5433,
            'longitude': 39.1728,
            'tz': 'Asia/Riyadh',
            'company_id': cls.env.company.id,
            'azan_sound_id': cls.sound.id,
            'fajr_azan_sound_id': cls.fajr_sound.id,
            'azan_maghrib': False,
        })
        cls.user = new_test_user(cls.env, login='prayer_user', groups='base.group_user')

    def test_timetable_generated(self):
        lines = self.location.timetable_ids
        self.assertEqual(len(lines), 30)
        today = self.location._local_today()
        self.assertEqual(min(lines.mapped('date')), today)
        self.assertEqual(max(lines.mapped('date')), today + timedelta(days=29))
        self.assertTrue(all(0 < line.fajr < line.sunrise < line.dhuhr < line.asr
                            < line.maghrib < line.isha < 24 for line in lines))

    def test_timetable_regenerated_on_change(self):
        today = self.location._local_today()
        line = self.location.timetable_ids.filtered(lambda l: l.date == today)
        fajr = line.fajr
        self.location.fajr_offset = 5
        new_line = self.location.timetable_ids.filtered(lambda l: l.date == today)
        self.assertEqual(len(self.location.timetable_ids), 30)
        self.assertAlmostEqual(new_line.fajr, fajr + 5 / 60, places=4)

    def test_cron(self):
        self.location.timetable_ids.unlink()
        with patch.object(requests, 'get', side_effect=requests.ConnectionError('offline')):
            self.env['prayer.location']._cron_generate_timetables()
        self.assertEqual(len(self.location.timetable_ids), 30)

    def test_user_schedule(self):
        self.user.prayer_location_id = self.location
        schedule = self.env['prayer.location'].with_user(self.user).get_user_prayer_schedule()
        self.assertEqual(schedule['location']['id'], self.location.id)
        self.assertEqual(len(schedule['prayers']), 12)
        by_key = {p['key']: p for p in schedule['prayers'][:6]}
        self.assertFalse(by_key['sunrise']['azan'])
        self.assertFalse(by_key['maghrib']['azan'])
        self.assertFalse(by_key['maghrib']['sound_url'])
        self.assertTrue(by_key['dhuhr']['azan'])
        self.assertIn('/prayer.azan.sound/%s/' % self.sound.id, by_key['dhuhr']['sound_url'])
        self.assertIn('/prayer.azan.sound/%s/' % self.fajr_sound.id, by_key['fajr']['sound_url'])
        times = [p['time'] for p in schedule['prayers']]
        self.assertEqual(times, sorted(times))

    def test_user_default_location(self):
        schedule = self.env['prayer.location'].with_user(self.user).get_user_prayer_schedule()
        self.assertTrue(schedule['location'])

    def test_user_preferences(self):
        self.env['res.users'].with_user(self.user).set_prayer_preferences({
            'prayer_play_azan': False, 'prayer_reminder_minutes': 10, 'login': 'hacked',
        })
        self.assertFalse(self.user.prayer_play_azan)
        self.assertEqual(self.user.prayer_reminder_minutes, 10)
        self.assertEqual(self.user.login, 'prayer_user')

    def test_access_rights(self):
        Location = self.env['prayer.location'].with_user(self.user)
        self.assertTrue(Location.search([]))
        with self.assertRaises(AccessError):
            Location.create({'name': 'X', 'latitude': 1, 'longitude': 1, 'tz': 'UTC'})
        with self.assertRaises(AccessError):
            self.sound.with_user(self.user).write({'name': 'X'})

    def test_multi_company_rule(self):
        other_company = self.env['res.company'].create({'name': 'Other Co'})
        other_location = self.env['prayer.location'].create({
            'name': 'Other', 'latitude': 26.42, 'longitude': 50.08, 'tz': 'Asia/Riyadh',
            'time_source': 'calculation',
            'company_id': other_company.id,
        })
        visible = self.env['prayer.location'].with_user(self.user).search([])
        self.assertNotIn(other_location, visible)

    def test_sound_mimetype(self):
        attachment = self.env['ir.attachment'].search([
            ('res_model', '=', 'prayer.azan.sound'), ('res_field', '=', 'audio_file'),
            ('res_id', '=', self.sound.id),
        ])
        self.assertEqual(attachment.mimetype, 'audio/mpeg')
        self.sound.write({'audio_file': FAKE_MP3, 'audio_filename': 'azan.ogg'})
        attachment = self.env['ir.attachment'].search([
            ('res_model', '=', 'prayer.azan.sound'), ('res_field', '=', 'audio_file'),
            ('res_id', '=', self.sound.id),
        ])
        self.assertEqual(attachment.mimetype, 'audio/ogg')

    def test_sound_extension(self):
        with self.assertRaises(ValidationError):
            self.env['prayer.azan.sound'].create({
                'name': 'Bad', 'audio_file': FAKE_MP3, 'audio_filename': 'azan.exe',
            })

    def test_custom_method_constraint(self):
        with self.assertRaises(ValidationError):
            self.location.write({'method': 'custom', 'fajr_angle': 0})


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def fake_aladhan(hijri_month=4, isha='19:40 (+03)'):
    """Fake GET /v1/calendar: same times every day of the requested month."""
    calls = []

    def get(url, params=None, timeout=None, headers=None):
        calls.append(dict(params, _url=url))
        year, month = params['year'], params['month']
        days = []
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            days.append({
                'timings': {
                    'Fajr': '04:30 (+03)', 'Sunrise': '05:50 (+03)', 'Dhuhr': '11:45 (+03)',
                    'Asr': '15:10 (+03)', 'Sunset': '17:40 (+03)', 'Maghrib': '17:40 (+03)',
                    'Isha': isha, 'Imsak': '04:20 (+03)', 'Midnight': '23:45 (+03)',
                },
                'date': {
                    'gregorian': {'date': '%02d-%02d-%04d' % (day, month, year)},
                    'hijri': {'day': str(day), 'month': {'number': hijri_month}, 'year': '1448'},
                },
            })
        return FakeResponse(200, {'code': 200, 'status': 'OK', 'data': days})
    return get, calls


@tagged('post_install', '-at_install')
class TestAladhanApi(TransactionCase):

    def _create_location(self, **vals):
        values = {
            'name': 'Riyadh API', 'latitude': 24.7136, 'longitude': 46.6753,
            'tz': 'Asia/Riyadh', 'time_source': 'aladhan', 'company_id': self.env.company.id,
        }
        values.update(vals)
        return self.env['prayer.location'].create(values)

    def test_api_times_used(self):
        get, calls = fake_aladhan()
        with patch.object(requests, 'get', side_effect=get):
            location = self._create_location(dhuhr_offset=2, asr_method='hanafi')
        self.assertTrue(calls)
        self.assertEqual(calls[0]['method'], 4)
        self.assertEqual(calls[0]['school'], 1)
        self.assertEqual(calls[0]['timezonestring'], 'Asia/Riyadh')
        self.assertEqual(calls[0]['calendarMethod'], 'UAQ')
        lines = location.timetable_ids
        self.assertEqual(len(lines), 30)
        self.assertEqual(set(lines.mapped('source')), {'aladhan'})
        line = lines[0]
        self.assertAlmostEqual(line.fajr, 4.5)
        self.assertAlmostEqual(line.dhuhr, 11 + 47 / 60, places=4)  # +2 min offset
        self.assertAlmostEqual(line.isha, 19 + 40 / 60, places=4)
        self.assertTrue(location.api_last_sync)
        self.assertFalse(location.api_last_error)
        # The web client schedule uses the downloaded times.
        today = location._local_today()
        self.assertAlmostEqual(location._compute_local_times(today)['fajr'], 4.5)

    def test_api_error_fallback(self):
        with patch.object(requests, 'get', side_effect=requests.ConnectionError('offline')):
            location = self._create_location()
        self.assertEqual(len(location.timetable_ids), 30)
        self.assertEqual(set(location.timetable_ids.mapped('source')), {'calculation'})
        self.assertIn('ConnectionError', location.api_last_error)
        self.assertTrue(location.today_fajr)

    def test_api_http_error_and_recovery(self):
        with patch.object(requests, 'get', return_value=FakeResponse(500, {})):
            location = self._create_location()
        self.assertIn('500', location.api_last_error)
        # Next daily run: the API answers again, calculated lines are replaced.
        get, _calls = fake_aladhan()
        with patch.object(requests, 'get', side_effect=get):
            self.env['prayer.location']._cron_generate_timetables()
        self.assertEqual(set(location.timetable_ids.mapped('source')), {'aladhan'})
        self.assertFalse(location.api_last_error)

    def test_api_calendar_method_only_umm_al_qura(self):
        get, calls = fake_aladhan()
        with patch.object(requests, 'get', side_effect=get):
            self._create_location(method='mwl')
        self.assertTrue(calls)
        self.assertEqual(calls[0]['method'], 3)
        self.assertNotIn('calendarMethod', calls[0])

    def test_api_ramadan_isha(self):
        get, _calls = fake_aladhan(hijri_month=9, isha='19:10 (+03)')
        with patch.object(requests, 'get', side_effect=get):
            location = self._create_location()
        line = location.timetable_ids[0]
        self.assertAlmostEqual(line.isha - line.maghrib, 2.0, places=4)

    def test_api_ramadan_already_applied(self):
        get, _calls = fake_aladhan(hijri_month=9, isha='19:40 (+03)')
        with patch.object(requests, 'get', side_effect=get):
            location = self._create_location()
        line = location.timetable_ids[0]
        self.assertAlmostEqual(line.isha - line.maghrib, 2.0, places=4)

    def test_api_custom_method_refused(self):
        with patch.object(requests, 'get', side_effect=requests.ConnectionError('offline')):
            with self.assertRaises(ValidationError):
                self._create_location(method='custom', fajr_angle=18, isha_angle=17)

    def test_mirror_after_connection_error(self):
        get, calls = fake_aladhan()
        attempts = []

        def flaky_get(url, **kwargs):
            attempts.append(url)
            if len(attempts) == 1:
                raise requests.ConnectionError('down')
            return get(url, **kwargs)
        with patch.object(requests, 'get', side_effect=flaky_get):
            location = self._create_location()
        self.assertTrue(attempts[0].startswith('https://api.aladhan.com/v1/calendar'))
        self.assertTrue(attempts[1].startswith('https://aladhan.api.islamic.network/v1/calendar'))
        self.assertEqual(set(location.timetable_ids.mapped('source')), {'aladhan'})
        self.assertFalse(location.api_last_error)

    def test_mirror_after_rate_limit(self):
        get, _calls = fake_aladhan()
        answers = iter([FakeResponse(429, {})])

        def limited_get(url, **kwargs):
            return next(answers, None) or get(url, **kwargs)
        with patch.object(requests, 'get', side_effect=limited_get):
            location = self._create_location()
        self.assertEqual(set(location.timetable_ids.mapped('source')), {'aladhan'})

    def test_all_servers_down(self):
        with patch.object(requests, 'get', side_effect=requests.ConnectionError('down')) as mock:
            location = self._create_location()
        # 2 months x 3 servers would be 6 calls, but the first failing month stops the sync.
        self.assertEqual(mock.call_count, 3)
        for host in ('api.aladhan.com', 'aladhan.api.islamic.network', 'aladhan.api.alislam.ru'):
            self.assertIn(host, location.api_last_error)

    def test_bad_request_message(self):
        message = ("The geographical coordinates (latitude and longitude) that you specified "
                   "or we were able to calculate from the address or city are invalid.")
        with patch.object(requests, 'get',
                          return_value=FakeResponse(400, {'code': 400, 'data': message})) as mock:
            location = self._create_location()
        self.assertEqual(mock.call_count, 1)  # no point asking the mirrors
        self.assertIn('geographical coordinates', location.api_last_error)

    def test_hijri_date_from_api(self):
        get, _calls = fake_aladhan(hijri_month=5)
        with patch.object(requests, 'get', side_effect=get):
            location = self._create_location()
        line = location.timetable_ids[0]
        self.assertEqual((line.hijri_day, line.hijri_month, line.hijri_year),
                         (line.date.day, 5, 1448))
        self.assertEqual(line.hijri_date, '%s Jumada al-Ula 1448 AH' % line.date.day)
        self.env.user.prayer_location_id = location
        schedule = self.env['prayer.location'].get_user_prayer_schedule()
        self.assertEqual(schedule['hijri_date'], line.hijri_date)

    def test_hijri_date_from_calendar(self):
        if prayer_location.Gregorian is None:
            self.skipTest("hijridate is not installed")
        location = self._create_location(time_source='calculation')
        line = location.timetable_ids[0]
        hijri = prayer_location.Gregorian.fromdate(line.date).to_hijri()
        self.assertEqual((line.hijri_day, line.hijri_month, line.hijri_year),
                         (hijri.day, hijri.month, hijri.year))
        self.assertTrue(line.hijri_date.endswith('%s AH' % hijri.year))

    def test_hijri_backfill(self):
        if prayer_location.Gregorian is None:
            self.skipTest("hijridate is not installed")
        location = self._create_location(time_source='calculation')
        location.timetable_ids.write({'hijri_day': 0, 'hijri_month': 0, 'hijri_year': 0})
        location._generate_timetable()
        self.assertFalse(location.timetable_ids.filtered(lambda line: not line.hijri_month))

    def test_test_connection(self):
        with patch.object(requests, 'get', side_effect=requests.ConnectionError('down')):
            location = self._create_location()
        lines_before = location.timetable_ids
        get, _calls = fake_aladhan(hijri_month=5)
        with patch.object(requests, 'get', side_effect=get):
            action = location.action_test_api_connection()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'success')
        self.assertIn('04:30', action['params']['message'])
        self.assertIn('Jumada al-Ula', action['params']['message'])
        self.assertEqual(location.timetable_ids, lines_before)  # nothing saved
        with patch.object(requests, 'get', side_effect=requests.ConnectionError('down')):
            action = location.action_test_api_connection()
        self.assertEqual(action['params']['type'], 'danger')
        self.assertIn('api.aladhan.com', action['params']['message'])

    def test_parse_time(self):
        self.assertAlmostEqual(aladhan_client._parse_time('04:29 (+03)'), 4 + 29 / 60)


@tagged('post_install', '-at_install')
class TestRamadanDetection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.location = cls.env['prayer.location'].create({
            'name': 'Riyadh Calc', 'latitude': 24.7136, 'longitude': 46.6753,
            'tz': 'Asia/Riyadh', 'time_source': 'calculation', 'method': 'umm_al_qura',
        })

    def _isha_after_maghrib(self, day):
        times = self.location._compute_local_times(day)
        return round((times['isha'] - times['maghrib']) * 60)

    def test_automatic_with_hijridate(self):
        if prayer_location.Gregorian is None:
            self.skipTest("hijridate is not installed")
        from hijridate import Hijri
        ramadan_day = Hijri(1448, 9, 10).to_gregorian()
        shawwal_day = Hijri(1448, 10, 10).to_gregorian()
        self.assertTrue(self.location.ramadan_auto)
        self.assertEqual(self._isha_after_maghrib(ramadan_day), 120)
        self.assertEqual(self._isha_after_maghrib(shawwal_day), 90)
        # The manual switch is ignored when the calendar is available.
        self.location.ramadan_mode = True
        self.assertEqual(self._isha_after_maghrib(shawwal_day), 90)

    def test_only_umm_al_qura(self):
        if prayer_location.Gregorian is None:
            self.skipTest("hijridate is not installed")
        from hijridate import Hijri
        self.location.method = 'gulf'  # also Isha 90 min, no Ramadan rule
        self.assertEqual(self._isha_after_maghrib(Hijri(1448, 9, 10).to_gregorian()), 90)

    def test_manual_without_hijridate(self):
        day = date(2027, 2, 18)
        with patch.object(prayer_location, 'Gregorian', None):
            self.location.invalidate_recordset(['ramadan_auto'])
            self.assertFalse(self.location.ramadan_auto)
            self.assertEqual(self._isha_after_maghrib(day), 90)
            self.location.ramadan_mode = True
            self.assertEqual(self._isha_after_maghrib(day), 120)
