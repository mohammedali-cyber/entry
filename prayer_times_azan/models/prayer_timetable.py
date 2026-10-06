from odoo import fields, models


class PrayerTimetable(models.Model):
    _name = 'prayer.timetable'
    _description = 'Prayer Timetable'
    _order = 'date, location_id'
    _rec_name = 'date'

    location_id = fields.Many2one('prayer.location', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='location_id.company_id', store=True, index=True)
    date = fields.Date(required=True, index=True)
    fajr = fields.Float(aggregator=None)
    sunrise = fields.Float(aggregator=None)
    dhuhr = fields.Float(aggregator=None)
    asr = fields.Float(aggregator=None)
    maghrib = fields.Float(aggregator=None)
    isha = fields.Float(aggregator=None)
    source = fields.Selection([
        ('aladhan', 'Aladhan API'),
        ('calculation', 'Built-in calculation'),
    ], required=True, default='calculation', readonly=True)

    _sql_constraints = [
        ('location_date_uniq', 'UNIQUE(location_id, date)',
         'There is already a timetable line for this location and date.'),
    ]
