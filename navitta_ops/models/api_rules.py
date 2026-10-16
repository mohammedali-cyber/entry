"""Server API of the management «📜 الأنظمة والمخالفات» (Rules and violations) screen."""
from datetime import timedelta

from odoo import api, fields, models

from .common import app_today, iso


class NavittaApiRules(models.AbstractModel):
    _inherit = "navitta.api"

    @api.model
    def admin_rules(self):
        """Shift rules, penalty table, branch rules with each employee's signature, the cases awaiting a decision
        and this month's cases (violations and penalties)."""
        self._admin()
        S = self.env
        day = app_today(self.env)
        m0 = day.replace(day=1)
        emps = S["navitta.employee"].sudo().search([("active", "=", True)])
        Rule = S["navitta.rule"]
        status = Rule._status(emps)
        month = S["navitta.late.case"].sudo().search([("day", ">=", m0), ("day", "<", (m0 + timedelta(days=32)).replace(day=1))])
        review = S["navitta.late.case"].sudo().search([("state", "=", "review")])
        return {
            "day": fields.Date.to_string(day), "month": fields.Date.to_string(m0), "settings": self._settings(),
            "branches": self._branches(), "employees": self._employees(),
            "tiers": [{"id": t.id, "max_min": t.max_min, "label": t.label, "p1": t.p1, "p2": t.p2, "p3": t.p3, "p4": t.p4}
                      for t in S["navitta.penalty.tier"].sudo().search([], order="max_min")],
            "rules": [{"id": r.id, "body": r.body, "updated_at": iso(r.updated_at)} for r in Rule.sudo().search([])],
            "acks": {e_id: {"ack_at": iso(x["ack_at"]), "current": x["current"]} for e_id, x in status.items()},
            "review": [self._case_review(c) for c in review],
            "cases": [self._case_dict(c) for c in month],
        }

    @api.model
    def admin_case_preview(self, case_id):
        self._admin()
        c = self.env["navitta.late.case"].sudo().browse(int(case_id or 0)).exists()
        return c._preview() if c else self._err("not_found")

    @api.model
    def admin_case_settle(self, case_id, on):
        """Mark a penalty as deducted from the salary (or undo)."""
        self._admin()
        c = self.env["navitta.late.case"].sudo().browse(int(case_id or 0)).exists()
        err = c._settle(bool(on)) if c else "not_found"
        return self._err(err) if err else {"ok": True}

    @api.model
    def admin_tiers_save(self, rows):
        """The penalty table edited on the screen: [{id, label, p1..p4}]."""
        self._admin()
        Tier = self.env["navitta.penalty.tier"].sudo()
        todo = []
        for x in rows or []:
            t = Tier.browse(int(x.get("id") or 0)).exists()
            vals = {k: str(x.get(k) or "").strip() for k in ("label", "p1", "p2", "p3", "p4")}
            if not t or not all(vals.values()):
                return self._err("tier_required")
            todo.append((t, vals))
        for t, vals in todo:
            t.write(vals)
        return {"ok": True}

    @api.model
    def admin_rule_save(self, rule_id, body):
        """Add (no id) or change a branch rule; the employees then sign again."""
        self._admin()
        body = (body or "").strip()
        if not body:
            return self._err("rule_required")
        Rule = self.env["navitta.rule"].sudo()
        if rule_id:
            r = Rule.browse(int(rule_id)).exists()
            if not r:
                return self._err("not_found")
            r.body = body
        else:
            last = Rule.search([], order="sequence desc", limit=1)
            r = Rule.create({"body": body, "sequence": (last.sequence or 0) + 1})
        return {"ok": True, "id": r.id}

