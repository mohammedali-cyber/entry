import base64
from datetime import timedelta

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

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
