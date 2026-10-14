from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Fill the employees' daily sales (navitta.employee.sales) from the daily reports saved before it existed."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["navitta.daily.report"].search([])._sync()
