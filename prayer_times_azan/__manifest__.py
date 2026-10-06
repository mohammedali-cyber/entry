{
    'name': 'Prayer Times & Azan',
    'version': '18.0.1.1.0',
    'category': 'Productivity',
    'summary': 'Daily prayer times with azan sound and notifications in the Odoo backend',
    'description': """
Prayer Times & Azan
===================
* Prayer times from the free Aladhan API (api.aladhan.com), with automatic
  fallback to an offline astronomical calculation (Umm Al-Qura by default).
* Several locations (branches / cities), multi-company aware.
* Upload your own azan audio files (MP3...) and assign them per location,
  with a dedicated Fajr azan.
* Systray widget with the next prayer and a countdown.
* At prayer time: Odoo notification, desktop notification and azan audio.
""",
    'license': 'LGPL-3',
    'depends': ['web', 'bus'],
    'data': [
        'security/prayer_security.xml',
        'security/ir.model.access.csv',
        'data/prayer_location_data.xml',
        'data/ir_cron_data.xml',
        'views/prayer_azan_sound_views.xml',
        'views/prayer_location_views.xml',
        'views/prayer_timetable_views.xml',
        'views/res_users_views.xml',
        'views/prayer_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'prayer_times_azan/static/src/js/*.js',
            'prayer_times_azan/static/src/xml/*.xml',
            'prayer_times_azan/static/src/scss/*.scss',
        ],
    },
    'installable': True,
    'application': True,
}
