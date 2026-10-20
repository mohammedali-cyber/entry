"""«🔎 المعروض والجرد» (Navitta Pro 26.6): the display check and the stock count of a branch, from the Odoo stock.

The branch's stock is the stock location of the warehouse of each of its points of sale (and the locations under it), as
in the model search (catalog.py). The branch device never reads stock.quant itself: the screen API (api_stock.py)
reads it with sudo and never sends a quantity to the device.

- Display check («👀 فحص المعروض»), one per branch and day: every model (product template) with stock > 0 at the branch
  must be on display. The staff mark each model «on display» or «not on display» with a reason, save as they go (the
  check can be resumed), and send it with a PIN; management is alerted with the models missing from the display.
- Stock count («📦 جرد المخزن»), blind: the staff scan barcodes (+1 each) or type the quantities of each variant (the whole
  stock or chosen models), save as they go and send it with a PIN. The server then compares the counts with the Odoo
  quantity at that moment and keeps one line per variant (expected, counted, difference). Odoo's stock changes only when
  management approves: the counted figure is the truth, the difference is taken against the quantity at approval and
  applied as a standard inventory adjustment (stock.quant inventory_quantity + action_apply_inventory) at the branch's
  stock location, in the branch's company. Valuation entries follow Odoo's normal behaviour for the product's costing
  method (automated valuation: an accounting entry per adjustment; manual: none). A count older than 7 days, or already
  decided, can't be approved. Reject / recount leave the stock untouched."""
from collections import defaultdict
from datetime import timedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.navitta_ops.models.common import app_today, iso

DISPLAY_STATUS = [("shown", "On display"), ("missing", "Not on display")]
DISPLAY_REASONS = [("no_space", "No space on the display"), ("damaged", "Damaged"), ("store_only", "Only in the store room"),
                   ("other", "Other")]
COUNT_SCOPES = [("all", "The whole stock"), ("models", "Chosen models")]
COUNT_STATES = [("draft", "Counting"), ("submitted", "Waiting for management"), ("approved", "Approved and applied"),
                ("rejected", "Rejected")]
MAX_AGE_DAYS = 7          # a count older than this can't be approved (the stock moved on since): ask for a recount
MAX_QTY = 100000


class NavittaBranchStock(models.Model):
    """Stock helpers on the branch: its stock locations and the quantities on hand there."""
    _inherit = "navitta.branch"

    def _stock_roots(self):
        """The branch's stock locations, the first one being where an adjustment is applied: the stock location of the
        warehouse of each of its points of sale (by point of sale), without duplicates."""
        self.ensure_one()
        out = self.env["stock.location"]
        for c in self.env["pos.config"].sudo().search([("navitta_branch_id", "=", self.id)], order="id"):
            loc = c.picking_type_id.warehouse_id.lot_stock_id
            if loc and loc not in out:
                out |= loc
        return out.sudo()

    def _stock_on_hand(self, product_ids=None):
        """{product id: quantity} at the branch's stock locations and their sub-locations (one grouped read)."""
        self.ensure_one()
        roots = self._stock_roots()
        if not roots:
            return {}
        dom = [("location_id", "child_of", roots.ids)]
        if product_ids is not None:
            if not product_ids:
                return {}
            dom.append(("product_id", "in", list(product_ids)))
        return {p.id: q for p, q in self.env["stock.quant"].sudo()._read_group(dom, ["product_id"], ["quantity:sum"])}


class NavittaDisplayCheck(models.Model):
    _name = "navitta.display.check"
    _description = "Display check"
    _order = "day desc, id desc"
    _rec_name = "day"

    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="cascade")
    day = fields.Date("Day", required=True, index=True)
    state = fields.Selection([("draft", "In progress"), ("done", "Sent")], "Status", required=True, default="draft")
    employee_id = fields.Many2one("navitta.employee", "Sent by", ondelete="set null")
    submitted_at = fields.Datetime("Sent at")
    line_ids = fields.One2many("navitta.display.check.line", "check_id", "Models")
    total_count = fields.Integer("Models in stock", compute="_compute_counts", store=True)
    shown_count = fields.Integer("On display", compute="_compute_counts", store=True)
    missing_count = fields.Integer("Not on display", compute="_compute_counts", store=True)

    _sql_constraints = [("display_check_uniq", "unique(branch_id, day)", "One display check per branch and day")]

    @api.depends("line_ids.status")
    def _compute_counts(self):
        for c in self:
            c.total_count = len(c.line_ids)
            c.shown_count = len(c.line_ids.filtered(lambda l: l.status == "shown"))
            c.missing_count = len(c.line_ids.filtered(lambda l: l.status == "missing"))

    @api.depends("branch_id", "day")
    def _compute_display_name(self):
        for c in self:
            c.display_name = "%s – %s" % (c.branch_id.name or "", c.day or "")

    def _refresh(self):
        """A check in progress follows the stock: a model that came into stock is added, a model no longer in stock and
        not marked yet is dropped (a marked one stays), and the colors in stock are updated."""
        Catalog = self.env["navitta.catalog.item"]
        for c in self.filtered(lambda x: x.state == "draft"):
            qty = c.branch_id._stock_on_hand()
            prods = self.env["product.product"].sudo().browse([p for p, q in qty.items() if q > 0]).exists()
            ok = self.env["product.template"].sudo().search(Catalog._product_domain() + [("id", "in", prods.product_tmpl_id.ids)])
            colors = defaultdict(list)
            for p in prods.filtered(lambda p: p.product_tmpl_id in ok):
                col = Catalog._variant_color(p)
                if col and col not in colors[p.product_tmpl_id.id]:
                    colors[p.product_tmpl_id.id].append(col)
            have = {l.product_tmpl_id.id: l for l in c.line_ids}
            new = []
            for t in ok:
                no, model = Catalog._split(t.name)
                vals = {"colors": ", ".join(colors.get(t.id, [])) or False}
                if t.id in have:
                    if have[t.id].colors != vals["colors"]:
                        have[t.id].colors = vals["colors"]
                    continue
                new.append(dict(vals, check_id=c.id, product_tmpl_id=t.id, item_no=no or t.default_code or "", model=model))
            gone = c.line_ids.filtered(lambda l: l.product_tmpl_id not in ok and not l.status)
            if gone:
                gone.unlink()
            if new:
                self.env["navitta.display.check.line"].sudo().create(new)


class NavittaDisplayCheckLine(models.Model):
    _name = "navitta.display.check.line"
    _description = "Display check model"
    _order = "item_no, model, id"
    _rec_name = "model"

    check_id = fields.Many2one("navitta.display.check", "Display check", required=True, index=True, ondelete="cascade")
    branch_id = fields.Many2one(related="check_id.branch_id", store=True, index=True, string="Branch")
    day = fields.Date(related="check_id.day", store=True, string="Day")
    product_tmpl_id = fields.Many2one("product.template", "Product", ondelete="set null")
    item_no = fields.Char("Model number")
    model = fields.Char("Model")
    colors = fields.Char("Colors in stock")
    status = fields.Selection(DISPLAY_STATUS, "Display")
    reason = fields.Selection(DISPLAY_REASONS, "Why not on display")
    note = fields.Char("Note")


class NavittaStockCount(models.Model):
    _name = "navitta.stock.count"
    _description = "Stock count"
    _order = "id desc"

    # not stored: shown in the reader's language («Navitta stock count #12 – branch»)
    name = fields.Char("Reference", compute="_compute_name")
    branch_id = fields.Many2one("navitta.branch", "Branch", required=True, index=True, ondelete="cascade")
    day = fields.Date("Started on", required=True, default=lambda self: app_today(self.env))
    scope = fields.Selection(COUNT_SCOPES, "What is counted", required=True, default="all")
    product_tmpl_ids = fields.Many2many("product.template", string="Chosen models")
    state = fields.Selection(COUNT_STATES, "Status", required=True, default="draft", index=True)
    employee_id = fields.Many2one("navitta.employee", "Sent by", ondelete="set null")
    submitted_at = fields.Datetime("Sent at")
    location_ids = fields.Many2many("stock.location", string="Stock locations", help="The branch's stock locations when it was sent")
    company_id = fields.Many2one("res.company", "Company")
    line_ids = fields.One2many("navitta.stock.count.line", "count_id", "Lines")
    diff_count = fields.Integer("Lines with a difference", compute="_compute_totals", store=True, groups="navitta_ops.group_navitta_admin")
    counted_total = fields.Float("Counted pieces", compute="_compute_totals", store=True)
    recount_count = fields.Integer("Recounts", readonly=True)
    recount_note = fields.Text("Recount request")
    admin_note = fields.Text("Management note")
    decided_by = fields.Many2one("res.users", "Decided by", ondelete="set null")
    decided_at = fields.Datetime("Decided at")
    move_ids = fields.Many2many("stock.move", string="Inventory adjustments", readonly=True, groups="navitta_ops.group_navitta_admin")

    @api.depends("branch_id")
    @api.depends_context("lang")
    def _compute_name(self):
        for c in self:
            c.name = _("Navitta stock count #%(id)s – %(b)s", id=c.id or "", b=c.branch_id.name or "")

    @api.depends("line_ids.difference", "line_ids.counted")
    def _compute_totals(self):
        for c in self:
            c.diff_count = len(c.line_ids.filtered(lambda l: l.difference))
            c.counted_total = sum(c.line_ids.mapped("counted"))

    def _line(self, product, create=True):
        """The count's line of a variant (created at 0 when missing)."""
        self.ensure_one()
        line = self.line_ids.filtered(lambda l: l.product_id == product)[:1]
        if not line and create:
            info = self.env["navitta.catalog.item"]._variant_info(product)
            line = self.env["navitta.stock.count.line"].sudo().create(dict(info, count_id=self.id, product_id=product.id))
        return line

    def _scope_products(self):
        """The variants the count covers: every variant of the chosen models, or (whole stock) None = all of them."""
        self.ensure_one()
        if self.scope == "all":
            return None
        return self.product_tmpl_ids.with_context(active_test=False).product_variant_ids

    def _submit(self, employee):
        """Compares the counts with the Odoo quantity now: a line per variant counted or in stock in the scope, with the
        expected and the counted quantity (a variant in stock that wasn't counted counts 0)."""
        self.ensure_one()
        b = self.branch_id
        roots = b._stock_roots()
        if not roots:
            return "no_stock"
        scope = self._scope_products()
        ids = None if scope is None else set(scope.ids) | set(self.line_ids.product_id.ids)
        qty = b._stock_on_hand(ids)
        have = {l.product_id.id: l for l in self.line_ids}
        Catalog, Prod = self.env["navitta.catalog.item"], self.env["product.product"].sudo()
        new = [dict(Catalog._variant_info(p), count_id=self.id, product_id=p.id)
               for p in Prod.browse([pid for pid, q in qty.items() if round(q, 4) and pid not in have]).exists()]
        if new:
            self.env["navitta.stock.count.line"].sudo().create(new)
        for l in self.line_ids:
            l.expected = round(qty.get(l.product_id.id, 0.0), 4)
        self.line_ids.filtered(lambda l: not l.counted and not l.expected).unlink()
        self.write({"state": "submitted", "employee_id": employee.id, "submitted_at": fields.Datetime.now(),
                    "location_ids": [(6, 0, roots.ids)], "company_id": (roots[0].company_id or self.env.company).id})
        return None

    def _apply(self):
        """Management approved: the difference of each line against the Odoo quantity NOW (the counted figure is the
        truth) is applied as a standard inventory adjustment in the branch's company: a surplus goes into the branch's
        (first) stock location, a shortage is taken from the branch's locations that hold the variant (that one first).
        Returns an error code or None."""
        self.ensure_one()
        b = self.branch_id
        roots = b._stock_roots()
        if not roots:
            return "no_stock"
        main = roots[0]
        company = main.company_id or self.company_id or self.env.company
        cur = b._stock_on_hand(self.line_ids.product_id.ids)
        diffs = [(l, round(l.counted - cur.get(l.product_id.id, 0.0), 4)) for l in self.line_ids]
        if any(d and l.product_id.tracking != "none" for l, d in diffs):
            return "count_tracked"
        ref = _("Navitta stock count #%(id)s – %(b)s", id=self.id, b=b.name)
        Quant = self.env["stock.quant"].with_user(SUPERUSER_ID).with_company(company).with_context(
            inventory_mode=True, inventory_name=ref)
        try:
            with self.env.cr.savepoint():
                targets = {}
                for l, d in diffs:
                    l.write({"qty_at_approval": round(cur.get(l.product_id.id, 0.0), 4), "applied_diff": d})
                    if not d:
                        continue
                    here = Quant._gather(l.product_id, main, strict=True)
                    if d > 0:
                        q = here[:1] or Quant.create({"product_id": l.product_id.id, "location_id": main.id})
                        targets[q] = targets.get(q, q.quantity) + d
                        continue
                    need = -d
                    held = Quant.search([("product_id", "=", l.product_id.id), ("location_id", "child_of", roots.ids), ("quantity", ">", 0)])
                    for q in held.sorted(lambda q: (q.location_id != main, q.location_id.id, q.id)):
                        take = min(q.quantity, need)
                        targets[q] = q.quantity - take
                        need = round(need - take, 4)
                        if need <= 0:
                            break
                    if need > 0:
                        q = here[:1] or Quant.create({"product_id": l.product_id.id, "location_id": main.id})
                        targets[q] = targets.get(q, q.quantity) - need
                quants = Quant.browse()
                for q, target in targets.items():
                    q.inventory_quantity = target
                    quants |= q
                if quants:
                    res = quants.action_apply_inventory()
                    if isinstance(res, dict):        # Odoo asks a question (conflict / lots): nothing is applied
                        raise UserError(res.get("name") or "inventory")
        except UserError:
            return "count_tracked"
        moves = self.env["stock.move"].sudo().search([("is_inventory", "=", True), ("name", "=", ref), ("state", "=", "done")])
        self.write({"move_ids": [(6, 0, moves.ids)]})
        return None


class NavittaStockCountLine(models.Model):
    _name = "navitta.stock.count.line"
    _description = "Stock count line"
    _order = "item_no, model, color, size, id"
    _rec_name = "product_id"

    count_id = fields.Many2one("navitta.stock.count", "Stock count", required=True, index=True, ondelete="cascade")
    branch_id = fields.Many2one(related="count_id.branch_id", store=True, index=True, string="Branch")
    product_id = fields.Many2one("product.product", "Variant", required=True, ondelete="restrict")
    item_no = fields.Char("Model number")
    model = fields.Char("Model")
    color = fields.Char("Color")
    size = fields.Char("Size")
    barcode = fields.Char("Barcode")
    counted = fields.Float("Counted", digits=(12, 2))
    # the Odoo quantities are management-only: the count is blind for the branch device
    expected = fields.Float("Odoo quantity when sent", digits=(12, 2), groups="navitta_ops.group_navitta_admin")
    difference = fields.Float("Difference", digits=(12, 2), compute="_compute_difference", store=True, groups="navitta_ops.group_navitta_admin",
                              help="Counted − Odoo quantity when the count was sent: negative = missing, positive = more than in Odoo")
    qty_at_approval = fields.Float("Odoo quantity at approval", digits=(12, 2), groups="navitta_ops.group_navitta_admin")
    applied_diff = fields.Float("Adjustment applied", digits=(12, 2), groups="navitta_ops.group_navitta_admin", help="Counted − Odoo quantity at approval")

    _sql_constraints = [("stock_count_line_uniq", "unique(count_id, product_id)", "One line per variant in a count"),
                        ("counted_pos", "CHECK(counted >= 0)", "A count cannot be negative")]

    @api.depends("counted", "expected", "count_id.state")
    def _compute_difference(self):
        for l in self:
            l.difference = round(l.counted - l.expected, 4) if l.count_id.state != "draft" else 0.0


class NavittaCatalogVariant(models.Model):
    _inherit = "navitta.catalog.item"

    @api.model
    def _variant_color(self, v):
        for ptav in v.product_template_attribute_value_ids:
            if self._attr_kind(ptav.attribute_id) == "color":
                return ptav.name or ""
        return ""

    @api.model
    def _variant_info(self, v):
        """A variant as a count line shows it: model number, model, color, size, barcode (no price, no stock)."""
        no, model = self._split(v.product_tmpl_id.name)
        color = size = ""
        for ptav in v.product_template_attribute_value_ids:
            kind = self._attr_kind(ptav.attribute_id)
            if kind == "color" and not color:
                color = ptav.name or ""
            elif kind == "size" and not size:
                size = ptav.name or ""
        return {"item_no": no or v.default_code or "", "model": model, "color": color or False, "size": size or False,
                "barcode": v.barcode or False}


def count_age_ok(count):
    """A count can be approved within MAX_AGE_DAYS of being sent."""
    return bool(count.submitted_at) and count.submitted_at >= fields.Datetime.now() - timedelta(days=MAX_AGE_DAYS)


__all__ = ["DISPLAY_REASONS", "MAX_AGE_DAYS", "count_age_ok", "iso"]
