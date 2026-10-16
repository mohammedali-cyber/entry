"""Screens API for shipments, the shortage list and defective items (Navitta Pro 26.3).

Branch device: incoming shipments received by scanning (emp_shipment_received), the shortage list sent with one PIN
(emp_request_batch) and defective items (emp_defect). Management: the shipment builder (admin_ship) with the shipment
Excel matched to the models file, and the answer to defective items (admin_defect_decide).
"""
import re

from odoo import _, api, fields, models

from .api_branch import PHONE_RE
from .common import DEFECT_SOURCES, DEFECT_TYPES, SHORTAGE_REASONS, add_workdays, app_today, iso, local_dt, sel_label, selection
from .shipment import MAX_QTY, _int, _txt

MAX_BATCH = 50          # lines in one shortage list
MAX_ITEMS = 2000        # rows read from a shipment file

# shipment file headers (as the site: English or Arabic, any case) -> field
SHIP_HEADERS = {
    "desc1": "item_no", "item no": "item_no", "item_no": "item_no", "رقم المؤسسة": "item_no", "alu": "alu", "barcode": "alu",
    "الباركود": "alu", "desc4": "model", "model": "model", "الموديل": "model", "attr": "color", "color": "color", "اللون": "color",
    "size": "size", "المقاس": "size", "qty": "qty", "quantity": "qty", "الكمية": "qty", "كمية": "qty",
}


class NavittaApiShipments(models.AbstractModel):
    _inherit = "navitta.api"

    # ------------------------------------------------------------------ dicts for the screens
    @api.model
    def _request_dict(self, r):
        d = super()._request_dict(r)
        d.update({
            "item_no": r.item_no or "", "reason_key": r.reason_key or "",
            "reason": sel_label(self.env, "navitta.branch.request", "reason_key", r.reason_key) if r.reason_key else (r.reason or ""),
            "contact_name": r.contact_name or "", "contact_phone": r.contact_phone or "",
            "in_shipment": r._in_open_shipment(),
        })
        return d

    @api.model
    def _shipment_dict(self, s):
        return {
            "id": s.id, "branch_id": s.branch_id.id, "state": s.state, "short": s.short,
            "lines": [{"id": l.id, "item_no": l.item_no or "", "alu": l.alu or "", "model": l.model or "", "color": l.color or "",
                       "size": l.size or "", "qty": l.qty, "price": l.price or 0.0, "request_id": l.request_id.id or False,
                       "in_catalog": l.in_catalog, "missing_qty": l.missing_qty} for l in s.line_ids],
            "roll_invoice": s.roll_invoice, "roll_card": s.roll_card, "bags": s.bags, "cash": s.cash, "note": s.note or "",
            "request_ids": s.request_ids.ids, "pieces": s.pieces,
            "eta_from": fields.Date.to_string(s.eta_from), "eta_to": fields.Date.to_string(s.eta_to),
            "shipped_at": iso(s.shipped_at), "received_at": iso(s.received_at), "received_by": s.received_by.name or "",
            "shipped_day": fields.Date.to_string(local_dt(self.env, s.shipped_at).date()), "received_day": fields.Date.to_string(s.received_day),
            "received_photo_id": s.received_photo_id.id or False, "issue_note": s.issue_note or "",
            "missing": [x[2:] for x in s._missing_lines()] if s.short else [],
        }

    @api.model
    def _defect_dict(self, x):
        return {
            "id": x.id, "branch_id": x.branch_id.id, "employee": x.employee_id.name or "", "employee_id": x.employee_id.id or False,
            "item_no": x.item_no or "", "model": x.model or "", "color": x.color or "", "size": x.size or "", "qty": x.qty,
            "what": x._what(), "defect": x.defect, "defect_label": sel_label(self.env, "navitta.defect", "defect", x.defect),
            "source": x.source, "source_label": sel_label(self.env, "navitta.defect", "source", x.source), "details": x.details or "",
            "photo_id": x.photo_id.id or False, "state": x.state, "admin_note": x.admin_note or "",
            "created_at": iso(x.create_date), "day": fields.Date.to_string(local_dt(self.env, x.create_date).date()), "decided_at": iso(x.decided_at),
        }

    @api.model
    def _ship_choices(self):
        return {"shortage_reasons": selection(self.env, "navitta.branch.request", "reason_key"),
                "defect_types": selection(self.env, "navitta.defect", "defect"),
                "defect_sources": selection(self.env, "navitta.defect", "source")}

    # ------------------------------------------------------------------ branch device
    @api.model
    def requests_state(self):
        res = super().requests_state()
        b = self._branch()
        ships = self.env["navitta.shipment"].sudo().search([("branch_id", "=", b.id), ("state", "!=", "received")], order="shipped_at")
        defects = self.env["navitta.defect"].sudo().search([("branch_id", "=", b.id)], limit=40)
        res.update(branch_id=b.id, shipments=[self._shipment_dict(s) for s in ships], defects=[self._defect_dict(x) for x in defects])
        res["choices"].update(self._ship_choices())
        return res

    @api.model
    def request_batch(self, emp_id, pin, items):
        """The shortage list: several out-of-stock items sent to management with one PIN (the site's emp_request_batch).
        Each item: {item_no, model, color, size, qty, reason (customer | sold_out | other), contact_name, contact_phone, note}.
        Identical lines of the list are merged (quantities added)."""
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        if not isinstance(items, list) or not items:
            return self._err("empty_list")
        if len(items) > MAX_BATCH:
            return self._err("too_many")
        merged = {}
        for x in items:
            x = x if isinstance(x, dict) else {}
            v = {k: _txt(x.get(k)) for k in ("item_no", "model", "color", "size", "reason", "contact_name", "note")}
            v["contact_phone"] = re.sub(r"\D", "", _txt(x.get("contact_phone")))
            if not v["model"] and not v["item_no"]:
                return self._err("model_required")
            qty = _int(x.get("qty"))
            if qty < 1 or qty > MAX_QTY:
                return self._err("bad_qty")
            if v["reason"] not in dict(SHORTAGE_REASONS):
                return self._err("reason_required")
            if v["reason"] == "customer" and (not PHONE_RE.match(v["contact_phone"]) or not v["contact_name"]):
                return self._err("contact_required")
            if v["reason"] == "other" and not v["note"]:
                return self._err("reason_required")
            if v["reason"] != "customer":
                v["contact_name"] = v["contact_phone"] = ""
            key = tuple(v[k] for k in ("item_no", "model", "color", "size", "reason", "contact_name", "contact_phone", "note"))
            if key in merged:
                merged[key]["qty"] = min(MAX_QTY, merged[key]["qty"] + qty)
            else:
                merged[key] = dict(v, qty=qty)
        b = e.branch_id
        Req = self.env["navitta.branch.request"].sudo()
        reqs = Req.create([{
            "branch_id": b.id, "kind": "goods", "employee_id": e.id, "item_no": v["item_no"] or False,
            "model": v["model"] or v["item_no"], "color": v["color"] or False, "size": v["size"] or False, "qty": v["qty"],
            "reason_key": v["reason"], "reason": sel_label(self.env, Req._name, "reason_key", v["reason"]),
            "note": v["note"] or False, "contact_name": v["contact_name"] or False, "contact_phone": v["contact_phone"] or False,
        } for v in merged.values()])
        lines = ["• %s × %s (%s)" % (r._what().rsplit(" × ", 1)[0], r.qty, r.reason) for r in reqs]
        self.env["navitta.notification"]._notify_admin(
            _("Shortage list from %s", b.name), _("%(n)s sent %(c)s requests:", n=e.name, c=len(reqs)) + "\n" + "\n".join(lines))
        return {"ok": True, "n": len(reqs), "ids": reqs.ids}

    @api.model
    def create_defect(self, emp_id, pin, vals, photo_id=None):
        """A defective item, with a photo, signed with the employee's PIN (the site's emp_defect)."""
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        vals = vals if isinstance(vals, dict) else {}
        b = e.branch_id
        photo = self._photo(b, photo_id)
        if not photo:
            return self._err("photo_required")
        v = {k: _txt(vals.get(k)) for k in ("item_no", "alu", "model", "color", "size", "defect", "source", "details")}
        if not v["model"] and not v["item_no"]:
            return self._err("model_required")
        if v["defect"] not in dict(DEFECT_TYPES) or v["source"] not in dict(DEFECT_SOURCES):
            return self._err("reason_required")
        if v["defect"] == "other" and not v["details"]:
            return self._err("reason_required")
        qty = _int(vals.get("qty"), 1)
        if qty > MAX_QTY:
            return self._err("bad_qty")
        x = self.env["navitta.defect"].sudo().create({
            "branch_id": b.id, "employee_id": e.id, "item_no": v["item_no"] or False, "alu": v["alu"].lstrip("0") or False,
            "model": v["model"] or v["item_no"], "color": v["color"] or False, "size": v["size"] or False, "qty": max(1, qty),
            "defect": v["defect"], "source": v["source"], "details": v["details"] or False, "photo_id": photo.id})
        text = " · ".join([x._what(), sel_label(self.env, x._name, "defect", x.defect), sel_label(self.env, x._name, "source", x.source)])
        if x.qty > 1:
            text += " × %s" % x.qty
        self.env["navitta.notification"]._notify_admin(
            _("🩹 Defective item from %s", b.name), "%s: %s%s" % (e.name, text, ("\n" + x.details) if x.details else ""))
        return {"ok": True, "id": x.id}

    @api.model
    def shipment_receive(self, emp_id, pin, ship_id, missing=None, note=None, photo_id=None):
        """Receiving a shipment after scanning (the site's emp_shipment_received): `missing` is what didn't arrive."""
        e, err = self._auth(emp_id, pin)
        if err:
            return self._err(err)
        s = self.env["navitta.shipment"].sudo().browse(int(ship_id or 0)).exists()
        if not s or s.branch_id != e.branch_id:
            return self._err("not_found")
        photo = self._photo(e.branch_id, photo_id)
        err = s._receive(e, missing if isinstance(missing, list) else [], note, photo)
        return self._err(err) if err else {"ok": True, "short": s.short}

    # ------------------------------------------------------------------ management
    @api.model
    def admin_requests(self):
        res = super().admin_requests()
        today = app_today(self.env)
        ships = self.env["navitta.shipment"].sudo().search([], limit=30)
        defects = self.env["navitta.defect"].sudo().search([], limit=80)
        waiting = self.env["navitta.lost.sale"].sudo().search([("waiting", "=", True)], limit=300)
        res.update(
            shipments=[self._shipment_dict(s) for s in ships], defects=[self._defect_dict(x) for x in defects],
            waiting=[{"branch_id": w.branch_id.id, "item_no": w.item_no or "", "color": w.color or "", "size": w.size or ""} for w in waiting],
            eta=[fields.Date.to_string(add_workdays(today, 1)), fields.Date.to_string(add_workdays(today, 4))],
        )
        res.setdefault("choices", {}).update(self._ship_choices())
        return res

    @api.model
    def admin_ship_file(self, data, filename):
        """Reads the shipment Excel (or CSV) and matches every row with the models file. Nothing is saved.
        Returns {items: [{item_no, alu, model, color, size, qty, price, in_catalog}], file, pieces, models, missing}."""
        self._admin()
        try:
            sheet = self._read_sheet(data, filename)
        except ValueError as e:
            return self._err(str(e))
        key = lambda c: SHIP_HEADERS.get(_txt(c).lower())
        head = next((i for i, r in enumerate(sheet[:10]) if len([c for c in r if key(c)]) >= 2), -1)
        if head < 0:
            return self._err("no_ship_columns")
        cols = [key(c) for c in sheet[head]]
        rows = []
        for r in sheet[head + 1:head + 1 + MAX_ITEMS]:
            o = {k: r[j] for j, k in enumerate(cols) if k and j < len(r) and _txt(r[j])}
            if o.get("alu") or o.get("item_no") or o.get("model"):
                rows.append(o)
        if not rows:
            return self._err("empty_file")
        items = self.env["navitta.shipment"]._match(rows)
        return {"items": items, "file": filename or "", "pieces": sum(x["qty"] for x in items),
                "models": len({x["item_no"] or x["model"] for x in items}), "missing": len([x for x in items if not x["in_catalog"]])}

    @api.model
    def admin_ship(self, branch_id, vals):
        """«Shipped · send to branch»: creates the shipment, marks its requests shipped and emails the branch staff."""
        self._admin()
        b = self.env["navitta.branch"].sudo().browse(_int(branch_id)).exists()
        if not b:
            return self._err("not_found")
        s, err = self.env["navitta.shipment"]._ship(b, vals)
        if err:
            return self._err(err)
        return {"ok": True, "id": s.id, "eta_from": fields.Date.to_string(s.eta_from), "eta_to": fields.Date.to_string(s.eta_to)}

    @api.model
    def admin_defect_decide(self, defect_id, ok, note=None):
        self._admin()
        x = self.env["navitta.defect"].sudo().browse(_int(defect_id)).exists()
        err = x._decide(bool(ok), note) if x else "not_found"
        return self._err(err) if err else {"ok": True}
