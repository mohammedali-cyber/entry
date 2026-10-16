"""Shipments from management to a branch (models, supplies and expense cash), received on the branch device by scanning,
and defective items reported by the branch (Navitta Pro 26.3).

Shipments are Navitta records only: like the site, they don't create stock pickings or touch Odoo inventory.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .common import DEFECT_SOURCES, DEFECT_TYPES, STOCK_SUPPLIES, add_workdays, app_today, local_dt, sel_label

MISSING_SUPPLIES = STOCK_SUPPLIES + ("cash",)
MAX_LINES = 2000
MAX_QTY = 9999


def _txt(v):
    """A cell or JSON value as clean text ('' for nothing; 38.0 -> '38')."""
    if v is None or v is False:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


class NavittaShipment(models.Model):
    _name = "navitta.shipment"
    _description = "Shipment to a branch"
    _order = "shipped_at desc, id desc"

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="restrict")
    state = fields.Selection([("shipped", "On the way"), ("received", "Arrived")], "Status", default="shipped", required=True, index=True)
    short = fields.Boolean("Arrived short", help="Received with missing items")
    line_ids = fields.One2many("navitta.shipment.line", "shipment_id", "Models")
    roll_invoice = fields.Integer("Receipt rolls")
    roll_card = fields.Integer("Card rolls")
    bags = fields.Integer("Bags")
    cash = fields.Float("Expense cash (SAR)", digits=(12, 2), help="Added to the branch's cash box balance when the shipment is received")
    note = fields.Text("Note to the branch")
    request_ids = fields.Many2many("navitta.branch.request", "navitta_shipment_request_rel", "shipment_id", "request_id",
                                   "Branch requests in the shipment")
    eta_from = fields.Date("Expected from")
    eta_to = fields.Date("Expected by")
    shipped_at = fields.Datetime("Shipping time", required=True, default=fields.Datetime.now)
    shipped_by = fields.Many2one("res.users", "Shipped by", ondelete="set null")
    received_at = fields.Datetime("Receipt time")
    received_day = fields.Date("Receipt day", help="Local day of the receipt (the cash box counts the cash from that day)")
    received_by = fields.Many2one("navitta.employee", "Received by", ondelete="set null")
    received_photo_id = fields.Many2one("navitta.photo", "Photo of the rolls and bags", ondelete="set null")
    received_photo = fields.Binary(related="received_photo_id.image", string="Receipt photo")
    issue_note = fields.Text("Employee's note on what's missing")
    issue_at = fields.Datetime("Shortage reported at")
    missing_roll_invoice = fields.Integer("Missing receipt rolls")
    missing_roll_card = fields.Integer("Missing card rolls")
    missing_bags = fields.Integer("Missing bags")
    cash_missing = fields.Boolean("Expense cash missing", help="The cash didn't arrive, so it isn't added to the cash box")
    pieces = fields.Integer("Pieces", compute="_compute_pieces")
    missing_pieces = fields.Integer("Missing pieces", compute="_compute_pieces")

    _sql_constraints = [
        ("supplies_pos", "CHECK(roll_invoice >= 0 AND roll_card >= 0 AND bags >= 0 AND cash >= 0)", "Quantities can't be negative"),
    ]

    @api.depends("line_ids.qty", "line_ids.missing_qty")
    def _compute_pieces(self):
        for s in self:
            s.pieces = sum(s.line_ids.mapped("qty"))
            s.missing_pieces = sum(s.line_ids.mapped("missing_qty"))

    @api.depends("branch_id", "shipped_at")
    def _compute_display_name(self):
        for s in self:
            s.display_name = "%s · %s" % (s.branch_id.name or "", s.shipped_at and fields.Date.to_string(local_dt(s.env, s.shipped_at).date()) or "")

    # ------------------------------------------------------------------ building a shipment (management)
    @api.model
    def _match(self, rows):
        """Rows of a shipment file (or of the screen) matched with the models file, as on the site: by barcode (ALU,
        without leading zeros), or by item number + color + size, falling back to the item number alone.
        Rows found nowhere keep what was written and are flagged in_catalog = False."""
        clean = []
        for o in rows or []:
            o = o or {}
            r = {k: _txt(o.get(k)) for k in ("item_no", "alu", "model", "color", "size")}
            r["alu"] = r["alu"].lstrip("0")
            if not (r["alu"] or r["item_no"] or r["model"]):
                continue
            r["qty"] = max(1, _int(o.get("qty"), 1))
            clean.append(r)
        Item = self.env["navitta.catalog.item"].sudo()
        alus = list({r["alu"] for r in clean if r["alu"]})
        nos = list({r["item_no"] for r in clean if r["item_no"]})
        by_alu = {i.alu: i for i in Item.search([("alu", "in", alus)])} if alus else {}
        by_no = {}
        for i in (Item.search([("item_no", "in", nos)]) if nos else Item):
            by_no.setdefault(i.item_no, []).append(i)
        out = []
        for r in clean:
            if r["alu"]:
                c = by_alu.get(r["alu"])
            else:
                c = next((x for x in by_no.get(r["item_no"], []) if (not r["color"] or x.color == r["color"])
                          and (not r["size"] or x.size == r["size"])), None)
            m = c or (by_no.get(r["item_no"]) or [None])[0]
            out.append({
                "item_no": (c and c.item_no) or r["item_no"] or "", "alu": (c and c.alu) or r["alu"] or "",
                "model": (c and c.model) or (m and m.model) or r["model"] or r["item_no"] or r["alu"],
                "color": (c and c.color) or r["color"] or "", "size": (c and c.size) or r["size"] or "",
                "qty": r["qty"], "price": (c and c.price) or (m and m.price) or 0.0, "in_catalog": bool(c or m),
            })
        return out

    @api.model
    def _ship(self, branch, vals):
        """Management sends a shipment to `branch`: the picked open requests of the branch, more models (already matched
        with the models file), supplies, expense cash and a note. Returns (shipment, error_code)."""
        vals = vals or {}
        nums = {k: vals.get(k) or 0 for k in ("roll_invoice", "roll_card", "bags", "cash")}
        try:
            rolls = {k: int(nums[k]) for k in STOCK_SUPPLIES}
            cash = round(float(nums["cash"]), 2)
        except (TypeError, ValueError):
            return self, "bad_qty"
        if min(list(rolls.values()) + [cash]) < 0 or max(rolls.values()) > MAX_QTY:
            return self, "bad_qty"
        ids = [_int(x) for x in (vals.get("request_ids") or [])]
        reqs = self.env["navitta.branch.request"].sudo().search(
            [("id", "in", ids), ("branch_id", "=", branch.id), ("state", "in", ("pending", "approved"))]) if ids else \
            self.env["navitta.branch.request"]
        items = vals.get("items") or []
        if not isinstance(items, list) or len(items) > MAX_LINES:
            return self, "bad_qty"
        if any(_int((x or {}).get("qty"), 1) > MAX_QTY for x in items):
            return self, "bad_qty"
        # the goods requests, matched with the models file too (so their barcode can be scanned when they arrive)
        goods = reqs.filtered(lambda q: q.kind == "goods")
        none = {"alu": "", "color": "", "size": "", "price": 0.0, "in_catalog": False}
        found = [(self._match([{"item_no": r.item_no, "model": r.model, "color": r.color, "size": r.size, "qty": r.qty}]) or [none])[0]
                 for r in goods]
        lines = [(0, 0, {"item_no": r.item_no or False, "alu": x["alu"] if x["in_catalog"] and x["color"] == (r.color or "") and x["size"] == (r.size or "") else False,
                         "model": r.model or r.item_no or "?", "color": r.color or False, "size": r.size or False, "qty": r.qty,
                         "price": x["price"], "request_id": r.id, "in_catalog": x["in_catalog"] if r.item_no else False, "sequence": i})
                 for i, (r, x) in enumerate(zip(goods, found))]
        for x in self._match(items):
            lines.append((0, 0, {"item_no": x["item_no"] or False, "alu": x["alu"] or False, "model": x["model"],
                                 "color": x["color"] or False, "size": x["size"] or False, "qty": x["qty"], "price": x["price"],
                                 "in_catalog": x["in_catalog"], "sequence": len(lines)}))
        if not lines and not sum(rolls.values()) and not cash:
            return self, "empty_shipment"
        today = app_today(self.env)
        s = self.sudo().create(dict(rolls, branch_id=branch.id, cash=cash, note=(vals.get("note") or "").strip() or False,
                                    request_ids=[(6, 0, reqs.ids)], line_ids=lines, shipped_by=self.env.uid,
                                    eta_from=add_workdays(today, 1), eta_to=add_workdays(today, 4)))
        reqs.write({"state": "shipped", "shipped_at": fields.Datetime.now()})
        s._email_staff()
        return s, None

    def _email_staff(self):
        """Every active employee of the branch gets the shipment (full text, in the company's language): what is in it,
        when it should arrive and how to receive it. It also shows in her «My alerts»."""
        self.ensure_one()
        env = self.with_context(lang=self.env.company._navitta_lang()).env
        s = self.with_env(env).sudo()
        dmy = lambda d: d.strftime("%d-%m-%Y")
        parts = []
        if s.pieces:
            models_n = len({l.item_no or l.model for l in s.line_ids})
            parts.append(env._("• 👠 %(n)s pieces (%(m)s models)", n=s.pieces, m=models_n))
        for k, label in (("roll_invoice", env._("🧾 Receipt roll")), ("roll_card", env._("💳 Card roll")), ("bags", env._("🛍️ Bags"))):
            if s[k] > 0:
                parts.append("• %s × %s" % (label, s[k]))
        if s.cash > 0:
            parts.append(env._("• 💵 Expense cash %s SAR", ("%.2f" % s.cash).rstrip("0").rstrip(".")))
        body = env._("A new shipment was sent to your branch 🚚\n\n📅 Shipping date: %(d)s\n"
                     "⏱️ Shipping takes 1 to 4 working days · it reaches you between %(a)s and %(b)s\n\n📦 Shipment contents:",
                     d=dmy(local_dt(env, s.shipped_at).date()), a=dmy(s.eta_from), b=dmy(s.eta_to))
        body += "\n" + "\n".join(parts)
        if s.note:
            body += "\n\n" + env._("💬 Management note: %s", s.note)
        body += "\n\n" + env._("✅ When it arrives:\n1. Open “📦 Branch requests” on the branch device and tap “Start receiving”.\n"
                               "2. Scan the barcode of every piece; it moves from “In the shipment” to “Arrived”.\n"
                               "3. Photograph the rolls and bags.\n"
                               "4. If everything arrived tap “Received”; if something is missing tap “Something's missing”.")
        Notif = env["navitta.notification"]
        for e in s.branch_id.employee_ids.filtered("active"):
            Notif._notify_employee(e, env._("🚚 A new shipment is on its way to your branch"), env._("Hello %s 👋", e.name) + "\n" + body)

    # ------------------------------------------------------------------ receiving (branch device)
    def _receive(self, employee, missing=None, note=None, photo=None):
        """The branch received the shipment (signed with the employee's PIN). `missing` lists what didn't arrive:
        [{"line_id", "qty"}] for models and [{"supply": roll_invoice|roll_card|bags|cash, "qty"}] for the rest.
        A photo is required when rolls or bags are in it, and a note when something is missing. Returns an error code or None."""
        self.ensure_one()
        s = self.sudo()
        if s.state == "received" or s.branch_id != employee.branch_id:
            return "not_found"
        if (s.roll_invoice + s.roll_card + s.bags) > 0 and not photo:
            return "photo_required"
        lines, sup = {}, {}
        for m in missing or []:
            m = m or {}
            qty = _int(m.get("qty"))
            if m.get("supply"):
                k = m["supply"]
                if k not in MISSING_SUPPLIES:
                    return "bad_qty"
                sup[k] = sup.get(k, 0) + (1 if k == "cash" else qty)
            else:
                line = s.line_ids.filtered(lambda l: l.id == _int(m.get("line_id")))
                if not line:
                    return "bad_qty"
                lines[line.id] = lines.get(line.id, 0) + qty
            if qty < 1 and m.get("supply") != "cash":
                return "bad_qty"
        if any(q > s.line_ids.filtered(lambda l: l.id == lid).qty for lid, q in lines.items()) \
                or any(q > s[k] for k, q in sup.items() if k != "cash") or (sup.get("cash") and not s.cash):
            return "bad_qty"
        short = bool(lines or sup)
        note = (note or "").strip()
        if short and not note:
            return "reason_required"
        now = fields.Datetime.now()
        today = app_today(self.env)
        s.write({"state": "received", "received_at": now, "received_day": today, "received_by": employee.id,
                 "received_photo_id": photo.id if photo else False, "short": short,
                 "issue_note": note if short else s.issue_note, "issue_at": now if short else s.issue_at,
                 "missing_roll_invoice": sup.get("roll_invoice", 0), "missing_roll_card": sup.get("roll_card", 0),
                 "missing_bags": sup.get("bags", 0), "cash_missing": bool(sup.get("cash"))})
        for line in s.line_ids:
            if lines.get(line.id):
                line.missing_qty = lines[line.id]
        # what arrived goes to the branch's supply balance; the expense cash to its cash box (see navitta.branch._cash_summary)
        for k in STOCK_SUPPLIES:
            got = s[k] - sup.get(k, 0)
            if got > 0:
                self.env["navitta.supply.move"].sudo().create({"branch_id": s.branch_id.id, "day": today, "supply": k, "qty": got,
                                                               "kind": "received", "shipment_id": s.id})
        # the requests in the shipment are received with it, except those of models that didn't arrive
        missing_reqs = s.line_ids.filtered(lambda l: l.missing_qty and l.request_id).mapped("request_id")
        (s.request_ids - missing_reqs).filtered(lambda r: r.state not in ("received", "rejected")).write(
            {"state": "received", "received_at": now, "received_by": employee.id})
        Notif = self.env["navitta.notification"]
        if short:
            Notif._notify_admin(_("⚠️ Shipment arrived short: %s", s.branch_id.name),
                                _("%(n)s received the shipment with missing items:\n%(l)s\nHer note: %(t)s",
                                  n=employee.name, l="\n".join(s._missing_lines()), t=note))
        else:
            Notif._notify_admin(_("Shipment arrived: %s", s.branch_id.name), _("%s received the full shipment.", employee.name))
        return None

    def _missing_lines(self):
        """'• what × n' for everything that didn't arrive (in the current language)."""
        self.ensure_one()
        out = ["• %s × %s" % (l._label(), l.missing_qty) for l in self.line_ids if l.missing_qty]
        for k in STOCK_SUPPLIES:
            if self["missing_" + k]:
                out.append("• %s × %s" % (sel_label(self.env, "navitta.supply.move", "supply", k), self["missing_" + k]))
        if self.cash_missing:
            out.append("• %s × 1" % _("Expense cash"))
        return out

    def _received_cash(self):
        return 0.0 if self.cash_missing else self.cash


class NavittaShipmentLine(models.Model):
    _name = "navitta.shipment.line"
    _description = "Shipment model line"
    _order = "shipment_id, sequence, id"
    _rec_name = "model"

    shipment_id = fields.Many2one("navitta.shipment", "Shipment", required=True, ondelete="cascade", index=True)
    branch_id = fields.Many2one(related="shipment_id.branch_id", store=True, index=True, string="Branch")
    sequence = fields.Integer()
    item_no = fields.Char("Item number")
    alu = fields.Char("Barcode (ALU)", help="Without leading zeros")
    model = fields.Char("Model", required=True)
    color = fields.Char("Color")
    size = fields.Char("Size")
    qty = fields.Integer("Quantity", required=True, default=1)
    price = fields.Float("Price incl. VAT", digits=(10, 2))
    request_id = fields.Many2one("navitta.branch.request", "Branch request", ondelete="set null")
    in_catalog = fields.Boolean("In the models file")
    missing_qty = fields.Integer("Didn't arrive")

    _sql_constraints = [("qty_pos", "CHECK(qty > 0 AND missing_qty >= 0 AND missing_qty <= qty)", "Invalid quantity")]

    def _label(self):
        self.ensure_one()
        model = self.model if self.model != self.item_no else False
        return " · ".join(x for x in (self.item_no, model, self.color, self.size) if x)


class NavittaDefect(models.Model):
    """A defective item reported by the branch (photo, defect type, where it came from), answered by management."""
    _name = "navitta.defect"
    _description = "Defective item"
    _order = "create_date desc, id desc"
    _rec_name = "model"

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="restrict")
    employee_id = fields.Many2one("navitta.employee", "Reported by", ondelete="set null")
    item_no = fields.Char("Item number", index=True)
    alu = fields.Char("Barcode (ALU)")
    model = fields.Char("Model")
    color = fields.Char("Color")
    size = fields.Char("Size")
    qty = fields.Integer("Quantity", required=True, default=1)
    defect = fields.Selection(DEFECT_TYPES, "Defect type", required=True)
    source = fields.Selection(DEFECT_SOURCES, "Where it came from", required=True)
    details = fields.Text("Details")
    photo_id = fields.Many2one("navitta.photo", "Defect photo", ondelete="restrict", required=True)
    photo = fields.Binary(related="photo_id.image", string="Photo")
    state = fields.Selection([("pending", "Waiting for management"), ("approved", "Approved"), ("rejected", "Rejected")],
                             "Status", default="pending", required=True, index=True)
    admin_note = fields.Text("Management reply")
    decided_at = fields.Datetime("Decision time")

    _sql_constraints = [("qty_pos", "CHECK(qty > 0)", "The quantity must be greater than zero")]

    def _what(self):
        self.ensure_one()
        model = self.model if self.model != self.item_no else False
        return " · ".join(x for x in (self.item_no, model, self.color, self.size) if x)

    def _decide(self, ok, note=None):
        """Management: approve, or reject with a reason (as the site's admin_defect_decide). The employee is notified."""
        self.ensure_one()
        x = self.sudo()
        note = (note or "").strip()
        if not ok and not note:
            return "reason_required"
        if x.state != "pending":
            return "not_found"
        x.write({"state": "approved" if ok else "rejected", "admin_note": note or False, "decided_at": fields.Datetime.now()})
        if x.employee_id:
            text = (_("✅ Approved") if ok else _("✕ Rejected")) + ": " + x._what()
            if note:
                text += "\n" + _("Management reply: %s", note)
            x.employee_id._notify(_("🩹 Management's reply about the defective item"), text)
        return None

    def action_approve(self):
        for x in self:
            if x._decide(True, x.admin_note):
                raise UserError(_("This defective item has already been answered"))

    def action_reject(self):
        for x in self:
            err = x._decide(False, x.admin_note)
            if err == "reason_required":
                raise UserError(_("Write the rejection reason in the management reply"))
            if err:
                raise UserError(_("This defective item has already been answered"))
