import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Owner's decisions (26.3 follow-up): the sales of every day come from the Point of Sale, the whole history, per
    branch and per device (navitta.pos.day, new). The days with orders are synced once now, so the screens open with all
    the figures; later the screens only sync the days whose orders changed. The cashier lists of the points of sale are
    no longer rewritten (nothing to undo: they stay as they are)."""
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    branches = env["navitta.branch"].search([("pos_config_ids", "!=", False)])
    n = branches._pos_backfill()
    _logger.info("Navitta POS: %s branch-days synced from the Point of Sale orders", n)
