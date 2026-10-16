from datetime import timedelta

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """26.3: today's float and today's tasks replace the cash custody count and the closing tasks (no data carried over).

    - The old counts are dropped (Odoo removes the model's rights, views and menus at the end of the update, but not
      its table) and so is the old on/off setting.
    - The missed-tasks check starts with tomorrow: yesterday's and today's periods count as already checked, so the
      first run doesn't alert management about tasks that didn't exist yet.
    """
    if not version:
        return
    cr.execute("DROP TABLE IF EXISTS navitta_cash_count CASCADE")
    cr.execute("ALTER TABLE res_company DROP COLUMN IF EXISTS navitta_cash_tasks")
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.navitta_ops.models.common import app_today
    today = app_today(env)
    Alert = env["navitta.task.alert"]
    for b in env["navitta.branch"].with_context(active_test=False).search([]):
        for day in (today - timedelta(days=1), today):
            for phase in ("start", "mid", "end"):
                if not Alert.search_count([("branch_id", "=", b.id), ("day", "=", day), ("phase", "=", phase)]):
                    Alert.create({"branch_id": b.id, "day": day, "phase": phase})
