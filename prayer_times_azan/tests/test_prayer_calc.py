import math
from datetime import date

from odoo.tests import BaseCase, tagged

from ..models.prayer_calc import PrayerTimesCalculator, round_to_minute


def hhmm(hours):
    hours = round_to_minute(hours)
    return '%02d:%02d' % (int(hours), round((hours - int(hours)) * 60))


@tagged('post_install', '-at_install')
class TestPrayerCalc(BaseCase):

    def test_makkah_umm_al_qura(self):
        times = PrayerTimesCalculator(21.4225, 39.8262).compute(date(2026, 6, 21), 3)
        result = {key: hhmm(value) for key, value in times.items()}
        self.assertEqual(result, {
            'fajr': '04:11', 'sunrise': '05:39', 'dhuhr': '12:22',
            'asr': '15:42', 'maghrib': '19:06', 'isha': '20:36',
        })

    def test_riyadh_order_and_isha_90(self):
        times = PrayerTimesCalculator(24.7136, 46.6753).compute(date(2026, 10, 4), 3)
        ordered = [times[k] for k in ('fajr', 'sunrise', 'dhuhr', 'asr', 'maghrib', 'isha')]
        self.assertEqual(ordered, sorted(ordered))
        self.assertAlmostEqual(times['isha'] - times['maghrib'], 1.5)
        self.assertEqual(hhmm(times['dhuhr']), '11:42')

    def test_ramadan_isha_120(self):
        times = PrayerTimesCalculator(24.7136, 46.6753, ramadan=True).compute(date(2026, 3, 1), 3)
        self.assertAlmostEqual(times['isha'] - times['maghrib'], 2.0)

    def test_hanafi_asr_later(self):
        day = date(2026, 10, 4)
        standard = PrayerTimesCalculator(24.7136, 46.6753).compute(day, 3)
        hanafi = PrayerTimesCalculator(24.7136, 46.6753, asr_factor=2).compute(day, 3)
        self.assertGreater(hanafi['asr'], standard['asr'] + 0.5)

    def test_offsets(self):
        day = date(2026, 10, 4)
        base = PrayerTimesCalculator(24.7136, 46.6753).compute(day, 3)
        tuned = PrayerTimesCalculator(24.7136, 46.6753, offsets={'dhuhr': 3}).compute(day, 3)
        self.assertAlmostEqual(tuned['dhuhr'] - base['dhuhr'], 3 / 60)
        self.assertAlmostEqual(tuned['fajr'], base['fajr'])

    def test_high_latitude(self):
        # Oslo in June: the sun never reaches 18 degrees below the horizon.
        day = date(2026, 6, 21)
        raw = PrayerTimesCalculator(59.91, 10.75, method='mwl', high_lat_rule='none').compute(day, 2)
        self.assertTrue(math.isnan(raw['fajr']))
        adjusted = PrayerTimesCalculator(59.91, 10.75, method='mwl').compute(day, 2)
        for value in adjusted.values():
            self.assertFalse(math.isnan(value))

    def test_custom_method_requires_angles(self):
        with self.assertRaises(ValueError):
            PrayerTimesCalculator(24.7, 46.6, method='custom', fajr_angle=0, isha_angle=17)
