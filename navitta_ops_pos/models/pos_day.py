from odoo import fields, models


class NavittaPosDay(models.Model):
    """One point of sale (device) on one local day: its counted orders' figures, written by the sales sync.

    Used for the «Device» filter of the management screens, and to know which days are synced already: `orders`,
    `sales` and `last_order_write` are compared with the orders to find the days that changed since."""
    _name = "navitta.pos.day"
    _description = "Point of Sale figures per device and day"
    _order = "day desc, branch_id, config_id"
    _rec_name = "day"

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="cascade")
    config_id = fields.Many2one("pos.config", "Point of Sale", required=True, index=True, ondelete="cascade")
    day = fields.Date("Day", required=True, index=True)
    orders = fields.Integer("Orders")
    sales = fields.Float("Sales (incl. VAT)", digits=(12, 2))
    tax = fields.Float("VAT", digits=(12, 2))
    invoices = fields.Integer("Invoices")
    items = fields.Integer("Items sold")
    returns = fields.Integer("Returns (items)")
    returns_amount = fields.Float("Returns amount", digits=(12, 2))
    cash = fields.Float("Cash", digits=(12, 2))
    card = fields.Float("Card", digits=(12, 2))
    last_order_write = fields.Datetime("Last order change", help="The latest change of the day's orders when they were synced")
    synced_at = fields.Datetime("Synced at")

    _sql_constraints = [("config_day_uniq", "unique(config_id, day)", "One row per point of sale and day")]
