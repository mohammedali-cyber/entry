from odoo import _, api, fields, models


class NavittaDailySales(models.Model):
    _inherit = "navitta.daily.sales"

    source = fields.Selection(selection_add=[("pos", "نقطة البيع")], ondelete={"pos": "set null"})


class NavittaEmployeeSales(models.Model):
    _inherit = "navitta.employee.sales"

    source = fields.Selection(selection_add=[("pos", "نقطة البيع")], ondelete={"pos": "cascade"})


class NavittaApi(models.AbstractModel):
    _inherit = "navitta.api"

    @api.model
    def admin_setup(self):
        """First-time setup: the points of sale and cashiers, too."""
        res = super().admin_setup()
        S = self.env
        branches = S["navitta.branch"].sudo().search([])
        no_pos = branches.filtered(lambda b: not b.pos_config_ids)
        no_cashier = S["navitta.employee"].sudo().search([("active", "=", True), ("hr_employee_id", "=", False),
                                                          ("branch_id.pos_config_ids", "!=", False)])
        res["items"] += [
            {"key": "pos_branch", "done": not no_pos, "title": _("ربط الفروع بنقطة البيع"),
             "text": _("فروع بدون نقطة بيع: %s", "، ".join(no_pos.mapped("name"))) if no_pos else _("كل فرع مربوط بنقطة بيعه"),
             "action": "point_of_sale.action_pos_config_kanban", "button": _("نقاط البيع")},
            {"key": "pos_cashier", "done": not no_cashier, "title": _("حسابات الكاشير للموظفات"),
             "text": _("%s موظفة بدون حساب كاشير (مبيعاتها تروح للحساب العام)", len(no_cashier)) if no_cashier
             else _("كل موظفة لها حساب كاشير"),
             "action": "navitta_ops.action_branches", "button": _("الفروع ← ربط الكاشيرات")},
        ]
        res["done"] = all(i["done"] for i in res["items"])
        return res
