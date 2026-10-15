from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class NavittaEmployee(models.Model):
    _inherit = "navitta.employee"

    hr_employee_id = fields.Many2one("hr.employee", "Her Point of Sale cashier account", copy=False, index=True, ondelete="set null",
                                     help="The cashier she picks in the Point of Sale (“Change Cashier”). Her sales are counted with this account, "
                                          "and it appears only in the cashier list of her branch's point of sale")

    @api.constrains("hr_employee_id")
    def _check_hr_employee(self):
        for e in self.filtered("hr_employee_id"):
            other = self.sudo().with_context(active_test=False).search(
                [("id", "!=", e.id), ("hr_employee_id", "=", e.hr_employee_id.id)], limit=1)
            if other:
                raise ValidationError(_("The cashier account “%(c)s” is already linked to %(o)s", c=e.hr_employee_id.name, o=other.name))

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs.mapped("branch_id")._navitta_sync_cashiers()
        return recs

    def write(self, vals):
        before = self.mapped("branch_id")
        res = super().write(vals)
        if {"hr_employee_id", "branch_id", "active"} & set(vals):
            (before | self.mapped("branch_id"))._navitta_sync_cashiers()
        return res
