import base64
import calendar
from datetime import timedelta
from unittest.mock import patch

import requests

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models import aladhan_client

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
        calls.append(params)
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
                    'hijri': {'month': {'number': hijri_month}},
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

    def test_parse_time(self):
        self.assertAlmostEqual(aladhan_client._parse_time('04:29 (+03)'), 4 + 29 / 60)
