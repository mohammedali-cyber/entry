from odoo import api, fields, models

PRAYER_USER_FIELDS = [
    'prayer_location_id', 'prayer_notify', 'prayer_play_azan',
    'prayer_reminder_minutes', 'prayer_azan_volume',
]


class ResUsers(models.Model):
    _inherit = 'res.users'

    prayer_location_id = fields.Many2one(
        'prayer.location', string='Prayer Location',
        help="Location used for your prayer times. Empty: the first location of your company.")
    prayer_notify = fields.Boolean(string='Prayer Notifications', default=True)
    prayer_play_azan = fields.Boolean(string='Play Azan Sound', default=True)
    prayer_reminder_minutes = fields.Integer(
        string='Reminder Before Azan (minutes)', default=0,
        help="Show a reminder this many minutes before each azan. 0 disables the reminder.")
    prayer_azan_volume = fields.Integer(string='Azan Volume (%)', default=100)

    _sql_constraints = [
        ('prayer_reminder_minutes_range',
         'CHECK(prayer_reminder_minutes >= 0 AND prayer_reminder_minutes <= 120)',
         'The prayer reminder must be between 0 and 120 minutes.'),
        ('prayer_azan_volume_range',
         'CHECK(prayer_azan_volume >= 0 AND prayer_azan_volume <= 100)',
         'The azan volume must be between 0 and 100.'),
    ]

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + PRAYER_USER_FIELDS

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + PRAYER_USER_FIELDS

    @api.model
    def set_prayer_preferences(self, values):
        """Lightweight write from the systray (only the prayer preferences)."""
        allowed = {key: values[key] for key in PRAYER_USER_FIELDS if key in values}
        if allowed:
            self.env.user.write(allowed)
        return True
