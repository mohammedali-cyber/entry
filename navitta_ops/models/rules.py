"""Branch rules the employees read and sign in «تنبيهاتي» (site: tables rules / rule_acks)."""
from odoo import api, fields, models


class NavittaRule(models.Model):
    _name = "navitta.rule"
    _description = "Branch rule"
    _order = "sequence, id"
    _rec_name = "body"

    body = fields.Text("Rule", required=True)
    sequence = fields.Integer("Order", default=10)
    active = fields.Boolean(default=True)
    updated_at = fields.Datetime("Last change", default=fields.Datetime.now, required=True, readonly=True,
                                 help="A signature counts only if it is newer than the last change of the rules")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals["updated_at"] = fields.Datetime.now()
        return super().create(vals_list)

    def write(self, vals):
        if "body" in vals or vals.get("active"):
            vals = dict(vals, updated_at=fields.Datetime.now())        # the employees sign again
        return super().write(vals)

    @api.model
    def _last_change(self):
        """Time of the last change of the active rules (False when there are none)."""
        last = self.sudo().search([], order="updated_at desc", limit=1)
        return last.updated_at

    @api.model
    def _status(self, employees):
        """{employee_id: {'ack_at', 'current'}}: current = she signed after the last change of the rules."""
        last = self._last_change()
        acks = {a.employee_id.id: a.ack_at for a in self.env["navitta.rule.ack"].sudo().search([("employee_id", "in", employees.ids)])}
        return {e.id: {"ack_at": acks.get(e.id) or False, "current": bool(acks.get(e.id) and (not last or acks[e.id] >= last))}
                for e in employees}


class NavittaRuleAck(models.Model):
    _name = "navitta.rule.ack"
    _description = "Branch rules signature"
    _order = "ack_at desc"
    _rec_name = "employee_id"

    employee_id = fields.Many2one("navitta.employee", "Employee", required=True, index=True, ondelete="cascade")
    branch_id = fields.Many2one(related="employee_id.branch_id", store=True, index=True, string="Branch")
    ack_at = fields.Datetime("Signed", required=True, default=fields.Datetime.now)

    _sql_constraints = [("emp_uniq", "unique(employee_id)", "One signature per employee")]

    @api.model
    def _sign(self, employee):
        a = self.sudo().search([("employee_id", "=", employee.id)], limit=1)
        if a:
            a.ack_at = fields.Datetime.now()
        else:
            self.sudo().create({"employee_id": employee.id})
