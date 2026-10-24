from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Fill the Hijri date of the existing timetable lines (no network call)."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['prayer.location'].with_context(prayer_no_api=True).search([])._generate_timetable()
