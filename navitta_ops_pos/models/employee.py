from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class NavittaEmployee(models.Model):
    _inherit = "navitta.employee"

    hr_employee_id = fields.Many2one("hr.employee", "Her Point of Sale cashier account", copy=False, index=True, ondelete="set null",
                                     help="Optional, for the points of sale without an owner: the existing cashier (Odoo employee) "
                                          "she picks in the Point of Sale (“Change Cashier”). Her sales there are counted with this "
                                          "account. Her own device (Point of Sale → «Owner employee») needs no cashier account")
    pos_config_owned_ids = fields.One2many("pos.config", "navitta_employee_id", string="Her own device")

    @api.constrains("hr_employee_id")
    def _check_hr_employee(self):
        for e in self.filtered("hr_employee_id"):
            other = self.sudo().with_context(active_test=False).search(
                [("id", "!=", e.id), ("hr_employee_id", "=", e.hr_employee_id.id)], limit=1)
            if other:
                raise ValidationError(_("The cashier account “%(c)s” is already linked to %(o)s", c=e.hr_employee_id.name, o=other.name))

    def write(self, vals):
        old_hr = {e.id: e.hr_employee_id for e in self}
        res = super().write(vals)
        if {"branch_id", "active"} & set(vals):
            # her own device stays in her old branch: it no longer belongs to her
            Cfg = self.env["pos.config"].sudo()
            for e in self:
                Cfg.search([("navitta_employee_id", "=", e.id)]).filtered(
                    lambda c: not e.active or c.navitta_branch_id != e.branch_id).write({"navitta_employee_id": False})
        if {"hr_employee_id", "branch_id", "active"} & set(vals):
            self.env.registry.clear_cache()         # the branch devices' access rules (their branch's cashiers)
        if "hr_employee_id" in vals:
            # the orders of the old and the new cashier account count for someone else now: their days are counted again
            hr = set().union(*[{old_hr[e.id].id, e.hr_employee_id.id} for e in self]) - {False}
            if hr:
                self.env["navitta.branch"].sudo().search([("pos_config_ids", "!=", False)])._pos_backfill(hr_ids=hr)
        return res
