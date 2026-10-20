"""Screen API of «🔎 المعروض والجرد» (display check and stock count) and the counter's «🏆 الأكثر طلبًا هالأسبوع».

Branch device: everything is scoped to its own branch and read with sudo; a device never gets an Odoo quantity (the
display check only lists the models in stock, the count is blind). The display check and the count are saved as the
staff go (no PIN: like the counter's visits) and sent with an employee's PIN. Management decides on a count from the
admin screens («📦 Branch requests» → «🔎 Display & stock»)."""
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.tools import SQL

from odoo.addons.navitta_ops.models.common import app_today, day_bounds_utc, iso, sel_label, selection

from .branch import COUNTED_STATES
from .stock_check import MAX_AGE_DAYS, MAX_QTY, count_age_ok

TOP_N = 5


class NavittaApiStock(models.AbstractModel):
    _inherit = "navitta.api"

    # ------------------------------------------------------------------ hooks of the core screens
    @api.model
    def _stock_on(self, branch):
        return bool(branch) and bool(branch.sudo()._stock_roots())

    @api.model
    def _week_top(self, branch, day):
        """The models the branch sold most (pieces, returns deducted) in the 7 days up to `day`, from its paid orders."""
        cfgs = self.env["pos.config"].sudo().search([("navitta_branch_id", "=", branch.id)])
        if not cfgs:
            return None
        start, end = day_bounds_utc(self.env, day - timedelta(days=6))[0], day_bounds_utc(self.env, day)[1]
        self.env["pos.order"].flush_model()
        self.env["pos.order.line"].flush_model()
        rows = self.env.execute_query(SQL(
            """SELECT p.product_tmpl_id, SUM(l.qty) FROM pos_order_line l
                 JOIN pos_order o ON o.id = l.order_id
                 JOIN product_product p ON p.id = l.product_id
                WHERE o.config_id IN %s AND o.state IN %s AND o.date_order >= %s AND o.date_order < %s
                GROUP BY 1""", tuple(cfgs.ids), COUNTED_STATES, start, end))
        Catalog = self.env["navitta.catalog.item"]
        sold = dict(rows)
        by = {}
        for tmpl in self.env["product.template"].sudo().browse(list(sold)).exists():
            q = sold[tmpl.id]
            no, model = Catalog._split(tmpl.name)
            key = no or "p%s" % tmpl.id
            x = by.setdefault(key, {"item_no": no or tmpl.default_code or model, "model": model, "qty": 0.0})
            x["qty"] += q or 0.0
        top = sorted((x for x in by.values() if x["qty"] > 0), key=lambda x: (-x["qty"], x["item_no"]))[:TOP_N]
        for x in top:
            x["qty"] = int(x["qty"]) if x["qty"] == int(x["qty"]) else round(x["qty"], 2)
        return top

    # ------------------------------------------------------------------ display check (branch device)
    @api.model
    def _display_line_dict(self, l):
        return {"id": l.id, "tmpl_id": l.product_tmpl_id.id or False, "item_no": l.item_no or "", "model": l.model or "",
                "colors": [c for c in (l.colors or "").split(", ") if c], "status": l.status or "", "reason": l.reason or "",
                "note": l.note or ""}

    @api.model
    def _display_dict(self, c):
        return {"id": c.id, "day": fields.Date.to_string(c.day), "state": c.state, "employee": c.employee_id.name or "",
                "submitted_at": iso(c.submitted_at), "total": c.total_count, "shown": c.shown_count, "missing": c.missing_count,
                "lines": [self._display_line_dict(l) for l in c.line_ids]}

    @api.model
    def _display_today(self, branch):
        Check = self.env["navitta.display.check"].sudo()
        day = app_today(self.env)
        c = Check.search([("branch_id", "=", branch.id), ("day", "=", day)], limit=1)
        if not c:
            c = Check.create({"branch_id": branch.id, "day": day})
        c._refresh()
        return c

    @api.model
    def display_state(self):
        b = self._branch()
        if not self._stock_on(b):
            return {"available": False}
        c = self._display_today(b)
        emps = self.env["navitta.employee"].sudo().search([("branch_id", "=", b.id), ("active", "=", True)], order="slot")
        return {"available": True, "check": self._display_dict(c), "reasons": selection(self.env, "navitta.display.check.line", "reason"),
                "employees": [{"id": e.id, "name": e.name} for e in emps]}

    @api.model
    def display_mark(self, line_id, status, reason=None, note=None):
        """Marks a model of today's check (saved as she goes, the check can be resumed): «shown», «missing» (with its
        reason), or "" to clear."""
        b = self._branch()
        l = self._rec("navitta.display.check.line", line_id, b)
        if l.check_id.state != "draft" or l.check_id.day != app_today(self.env):
            return self._err("check_sent")
        if status not in ("shown", "missing", "", None, False):
            return self._err("not_found")
        reason = reason if reason in dict(self.env["navitta.display.check.line"]._fields["reason"].selection) else False
        l.write({"status": status or False, "reason": reason if status == "missing" else False,
                 "note": ((note or "").strip()[:200] or False) if status == "missing" else False})
        return {"ok": True, "line": self._display_line_dict(l)}

    @api.model
    def display_find(self, code):
        """A scanned barcode (or a typed model number) -> the line of today's check it belongs to."""
        b = self._branch()
        c = self._display_today(b)
        code = (code or "").strip()
        Catalog = self.env["navitta.catalog.item"]
        tmpl = self.env["product.template"]
        v = Catalog._by_barcode(code) if code else None
        if v:
            tmpl = v.product_tmpl_id
        line = c.line_ids.filtered(lambda l: l.product_tmpl_id == tmpl)[:1] if tmpl else c.line_ids.filtered(
            lambda l: code and (l.item_no == code or (l.model or "").lower() == code.lower()))[:1]
        if not line:
            return dict(self._err("unknown_code"), in_catalog=bool(v))
        return {"ok": True, "line_id": line.id}

    @api.model
    def display_submit(self, emp_id, pin):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        b = e.branch_id
        c = self._display_today(b)
        if c.state != "draft":
            return self._err("check_sent")
        if not c.line_ids:
            return self._err("no_stock")
        if c.line_ids.filtered(lambda l: not l.status):
            return self._err("not_marked")
        if c.line_ids.filtered(lambda l: l.status == "missing" and (not l.reason or (l.reason == "other" and not l.note))):
            return self._err("display_reason")
        c.write({"state": "done", "employee_id": e.id, "submitted_at": fields.Datetime.now()})
        miss = c.line_ids.filtered(lambda l: l.status == "missing")
        lines = [_("%(e)s checked the display: %(n)s models in stock, %(s)s on display, %(m)s not on display.",
                   e=e.name, n=c.total_count, s=c.shown_count, m=c.missing_count)]
        for l in miss:
            lines.append("• %s%s%s — %s%s" % (l.item_no or "", " %s" % l.model if l.model and l.model != l.item_no else "",
                                             " (%s)" % l.colors if l.colors else "",
                                             sel_label(self.env, "navitta.display.check.line", "reason", l.reason),
                                             ": %s" % l.note if l.note else ""))
        subject = _("👀 Display check: %(b)s · %(m)s not on display", b=b.name, m=c.missing_count) if miss else \
            _("👀 Display check: %s · everything on display ✓", b.name)
        self.env["navitta.notification"]._notify_admin(subject, "\n".join(lines))
        return {"ok": True, "missing": c.missing_count}

    # ------------------------------------------------------------------ stock count (branch device, blind)
    @api.model
    def _count_line_dict(self, l):
        # never the Odoo quantity: the count is blind
        return {"id": l.id, "product_id": l.product_id.id, "tmpl_id": l.product_id.product_tmpl_id.id, "item_no": l.item_no or "",
                "model": l.model or "", "color": l.color or "", "size": l.size or "", "barcode": l.barcode or "",
                "counted": int(l.counted) if l.counted == int(l.counted) else l.counted}

    @api.model
    def _count_dict(self, c, lines=True):
        return {"id": c.id, "name": c.name, "state": c.state, "scope": c.scope, "day": fields.Date.to_string(c.day),
                "employee": c.employee_id.name or "", "submitted_at": iso(c.submitted_at), "decided_at": iso(c.decided_at),
                "recount_note": c.recount_note or "", "recounts": c.recount_count, "admin_note": c.admin_note or "",
                "models": [{"id": t.id, "item_no": self.env["navitta.catalog.item"]._split(t.name)[0] or t.default_code or "",
                            "model": self.env["navitta.catalog.item"]._split(t.name)[1]} for t in c.product_tmpl_ids],
                "pieces": int(sum(c.line_ids.mapped("counted"))),
                "lines": [self._count_line_dict(l) for l in c.line_ids.filtered(lambda l: l.counted or c.state == "draft")] if lines else []}

    @api.model
    def _count_open(self, branch):
        return self.env["navitta.stock.count"].sudo().search([("branch_id", "=", branch.id), ("state", "in", ("draft", "submitted"))], limit=1)

    @api.model
    def _count_draft(self, branch, create=True, scope="all"):
        c = self._count_open(branch)
        if c and c.state != "draft":
            return None
        if not c and create:
            c = self.env["navitta.stock.count"].sudo().create({"branch_id": branch.id, "scope": scope})
        return c

    @api.model
    def count_state(self):
        b = self._branch()
        if not self._stock_on(b):
            return {"available": False}
        c = self._count_open(b)
        past = self.env["navitta.stock.count"].sudo().search([("branch_id", "=", b.id), ("state", "in", ("approved", "rejected"))], limit=5)
        emps = self.env["navitta.employee"].sudo().search([("branch_id", "=", b.id), ("active", "=", True)], order="slot")
        return {"available": True, "count": self._count_dict(c) if c else False,
                "history": [self._count_dict(x, lines=False) for x in past],
                "employees": [{"id": e.id, "name": e.name} for e in emps]}

    @api.model
    def count_start(self, scope="all"):
        b = self._branch()
        if not self._stock_on(b):
            return self._err("no_stock")
        c = self._count_open(b)
        if c and c.state != "draft":
            return self._err("count_open")
        scope = scope if scope in ("all", "models") else "all"
        if c:
            c.scope = scope
        else:
            c = self._count_draft(b, scope=scope)
        return {"ok": True, "count": self._count_dict(c)}

    @api.model
    def count_scan(self, code):
        """A scanned barcode: +1 on that variant (its model joins the chosen models)."""
        b = self._branch()
        c = self._count_draft(b)
        if c is None:
            return self._err("count_open")
        v = self.env["navitta.catalog.item"]._by_barcode((code or "").strip()) if (code or "").strip() else None
        if not v:
            return self._err("unknown_code")
        if c.scope == "models" and v.product_tmpl_id not in c.product_tmpl_ids:
            c.product_tmpl_ids = [(4, v.product_tmpl_id.id)]
        line = c._line(v)
        if line.counted + 1 > MAX_QTY:
            return self._err("bad_qty")
        line.counted += 1
        return {"ok": True, "line": self._count_line_dict(line)}

    @api.model
    def count_add(self, q):
        """Adds a model (8-digit number, name or a barcode) with all its variants at 0, to type their quantities."""
        b = self._branch()
        c = self._count_draft(b)
        if c is None:
            return self._err("count_open")
        res = self.env["navitta.catalog.item"]._find_products(q)
        pids = {i["product_id"] for g in (res or {}).get("models", []) for i in g["items"] if i.get("product_id")}
        tmpls = self.env["product.product"].sudo().browse(list(pids)).exists().product_tmpl_id
        if not tmpls:
            return self._err("unknown_code")
        if c.scope == "models":
            c.product_tmpl_ids = [(4, t.id) for t in tmpls]
        for v in tmpls.product_variant_ids:
            c._line(v)
        return {"ok": True, "count": self._count_dict(c), "tmpl_ids": tmpls.ids}

    @api.model
    def count_set(self, product_id, qty):
        """Types the counted quantity of a variant of the count."""
        b = self._branch()
        c = self._count_draft(b, create=False)
        if not c:
            return self._err("count_open")
        try:
            qty = float(qty or 0)
        except (TypeError, ValueError):
            return self._err("bad_qty")
        if qty < 0 or qty > MAX_QTY or qty != int(qty):
            return self._err("bad_qty")
        p = self.env["product.product"].sudo().browse(int(product_id or 0)).exists()
        line = c.line_ids.filtered(lambda l: l.product_id == p)[:1]
        if not line:
            return self._err("not_found")
        line.counted = qty
        return {"ok": True, "line": self._count_line_dict(line)}

    @api.model
    def count_drop_model(self, tmpl_id):
        """Takes a model (and its counts) out of the count being made."""
        b = self._branch()
        c = self._count_draft(b, create=False)
        if not c:
            return self._err("count_open")
        c.line_ids.filtered(lambda l: l.product_id.product_tmpl_id.id == int(tmpl_id or 0)).unlink()
        c.product_tmpl_ids = [(3, int(tmpl_id or 0))]
        return {"ok": True, "count": self._count_dict(c)}

    @api.model
    def count_submit(self, emp_id, pin):
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        b = e.branch_id
        c = self._count_open(b)
        if not c or c.state != "draft":
            return self._err("count_open" if c else "count_empty")
        # an empty count is refused, except chosen models counted at 0 (“none of these is here”)
        if not any(c.line_ids.mapped("counted")) and not (c.scope == "models" and c.product_tmpl_ids):
            return self._err("count_empty")
        err = c._submit(e)
        if err:
            return self._err(err)
        self.env["navitta.notification"]._notify_admin(
            _("📦 Stock count waiting for your review: %s", b.name),
            _("%(e)s sent %(n)s: %(p)s pieces counted, %(d)s lines differ from Odoo. Review it in Branch requests → Display & stock;"
              " Odoo's stock changes only when you approve it.", e=e.name, n=c.name, p=int(c.counted_total), d=c.diff_count))
        return {"ok": True, "count": self._count_dict(c)}

    # ------------------------------------------------------------------ management
    @api.model
    def _admin_count_dict(self, c, now_qty=None):
        lines = c.line_ids
        cur = now_qty if now_qty is not None else {}
        rows = []
        for l in lines:
            now = cur.get(l.product_id.id, 0.0) if c.state == "submitted" else l.qty_at_approval
            diff_now = round(l.counted - now, 4) if c.state == "submitted" else l.applied_diff
            if not (l.difference or diff_now or (c.state == "submitted" and round(now - l.expected, 4))):
                continue
            rows.append({"id": l.id, "product_id": l.product_id.id, "item_no": l.item_no or "", "model": l.model or "",
                         "color": l.color or "", "size": l.size or "", "expected": l.expected, "counted": l.counted,
                         "difference": l.difference, "now": round(now, 4), "diff_now": diff_now})
        d = self._count_dict(c, lines=False)
        d.update({"branch_id": c.branch_id.id, "branch": c.branch_id.name, "lines_total": len(lines), "diff_count": c.diff_count,
                  "expected_total": round(sum(lines.mapped("expected")), 2), "counted_total": c.counted_total,
                  "rows": rows, "decided_by": c.decided_by.name or "", "moves": len(c.move_ids),
                  "stale": c.state == "submitted" and not count_age_ok(c),
                  "scope_label": sel_label(self.env, "navitta.stock.count", "scope", c.scope),
                  "state_label": sel_label(self.env, "navitta.stock.count", "state", c.state)})
        return d

    @api.model
    def admin_stock_reviews(self, branch_id=False):
        """The display checks (last 30 days) and the stock counts (sent, and the latest decided) for the admin screen."""
        self._admin()
        S = self.env
        dom = [("branch_id", "=", int(branch_id))] if branch_id else []
        since = app_today(S) - timedelta(days=30)
        checks = S["navitta.display.check"].sudo().search(dom + [("day", ">=", since), "|", ("state", "=", "done"),
                                                                ("day", "=", app_today(S))], limit=60)
        counts = S["navitta.stock.count"].sudo().search(dom + [("state", "=", "submitted")])
        counts |= S["navitta.stock.count"].sudo().search(dom + [("state", "in", ("approved", "rejected"))], limit=15)
        counts |= S["navitta.stock.count"].sudo().search(dom + [("state", "=", "draft"), ("recount_count", ">", 0)], limit=15)
        out = []
        for c in counts.sorted(lambda x: (x.state != "submitted", -x.id)):
            qty = c.branch_id._stock_on_hand(c.line_ids.product_id.ids) if c.state == "submitted" else None
            out.append(self._admin_count_dict(c, qty))
        return {
            "available": any(self._stock_on(b) for b in S["navitta.branch"].sudo().search([])), "max_age": MAX_AGE_DAYS,
            "checks": [dict(self._display_dict(c), branch_id=c.branch_id.id, branch=c.branch_id.name,
                            lines=[dict(self._display_line_dict(l), reason_label=sel_label(S, "navitta.display.check.line", "reason", l.reason))
                                   for l in c.line_ids.filtered(lambda l: l.status == "missing")]) for c in checks],
            "counts": out,
        }

    @api.model
    def admin_count_decide(self, count_id, decision, note=None):
        """approve: applies the differences to Odoo's stock (against the quantity now); reject / recount: the stock is
        untouched (recount: the branch counts again, the count goes back to it)."""
        self._admin()
        c = self.env["navitta.stock.count"].sudo().browse(int(count_id or 0)).exists()
        if not c or c.state == "draft":
            return self._err("not_found")
        if c.state != "submitted":
            return self._err("count_done")
        note = (note or "").strip()[:1000]
        if decision not in ("approve", "reject", "recount"):
            return self._err("bad_decision")
        if decision in ("reject", "recount") and not note:
            return self._err("note_needed")
        if decision == "approve":
            if not count_age_ok(c):
                return self._err("count_stale")
            err = c._apply()
            if err:
                return self._err(err)
            c.write({"state": "approved", "decided_by": self.env.uid, "decided_at": fields.Datetime.now(), "admin_note": note or False})
            subject = _("📦 Your stock count was approved")
            body = _("Management approved %(n)s; Odoo's stock now matches your count.%(x)s", n=c.name, x=("\n" + note) if note else "")
        elif decision == "reject":
            c.write({"state": "rejected", "decided_by": self.env.uid, "decided_at": fields.Datetime.now(), "admin_note": note})
            subject = _("📦 Your stock count was rejected")
            body = _("Management rejected %(n)s: %(x)s", n=c.name, x=note)
        else:
            c.line_ids.filtered(lambda l: not l.counted).unlink()
            c.line_ids.write({"expected": 0.0, "qty_at_approval": 0.0, "applied_diff": 0.0})
            c.write({"state": "draft", "recount_count": c.recount_count + 1, "recount_note": note, "submitted_at": False,
                     "decided_by": self.env.uid, "decided_at": fields.Datetime.now()})
            subject = _("🔁 Management asks for a recount")
            body = _("Count again and send %(n)s: %(x)s", n=c.name, x=note)
        if c.employee_id:
            c.employee_id._notify(subject, body)
        return {"ok": True}
