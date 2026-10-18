from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # for the record rules of a branch device (they are evaluated as the device, which can't read the points of sale)
    navitta_hr_employee_ids = fields.Many2many("hr.employee", string="Navitta: the branch's Odoo employees",
                                               compute="_compute_navitta_hr_employee_ids", compute_sudo=True,
                                               help="The cashier accounts of the branch's employees and the cashiers of the "
                                                    "branch's points of sale")

    @api.depends("navitta_branch_id")
    def _compute_navitta_hr_employee_ids(self):
        for u in self:
            b = u.sudo().navitta_branch_id
            u.navitta_hr_employee_ids = (b.employee_ids.hr_employee_id | b.pos_config_ids.basic_employee_ids
                                         | b.pos_config_ids.advanced_employee_ids)
