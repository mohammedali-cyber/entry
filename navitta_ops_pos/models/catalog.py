"""Model search («🔎 Model search», the shortage list's item chooser) in the Odoo products first.

The products are named with the 8-digit model number first, e.g. «21160003- حذاء نسائي»; their variants carry a color
and a size attribute and each variant has its barcode. The search answers with the same structure as the models file
(navitta.catalog.item._group / _item_dict), so the screens show a product like a model of the file:

- 8 digits: the model(s) whose name starts with that number; a shorter / longer run of digits: a scanned barcode
  (exact, also with the leading zeros stripped or added) gives that variant (its color and size) and its whole model,
  else the models whose number starts with those digits (up to 6);
- text: the products whose name contains it (up to 6 models).

When no product matches, the models file is searched as before.

The products' result also carries the quantity on hand (stock.quant, one grouped read) of each variant at the branch's
own stock: the stock location of the warehouse of the branch's points of sale (and its sub-locations; several
warehouses are added up). A branch device sees its own branch and only the total of the other branches; management
also gets one row per branch. The true quantity is shown (it can be negative); 0 or less is «out of stock».
The models file has no stock."""
import re

from collections import defaultdict

from odoo import api, models

NUMBER = re.compile(r"^\s*(\d{8})(?!\d)\s*[-–—_:.,/\\|]*\s*(.*)$", re.S)
COLOR_WORDS = ("لون", "color", "colour")
SIZE_WORDS = ("مقاس", "size")
MAX_MODELS = 6


class NavittaCatalogItem(models.Model):
    _inherit = "navitta.catalog.item"

    @api.model
    def _find(self, raw):
        res = self._find_products(raw)
        if res is None:
            return super()._find(raw)
        self._add_stock(res)
        return res

    # ------------------------------------------------------------------ stock on hand
    @api.model
    def _stock_scope(self):
        """(the branch whose stock is «here» (may be empty), management?) for the user searching."""
        Api = self.env["navitta.api"]
        if Api._is_device():
            return self.env.user.navitta_branch_id.sudo(), False
        branch = self.env["navitta.branch"].sudo().browse(int(self.env.context.get("navitta_branch") or 0)).exists()
        return branch, self.env.user.has_group("navitta_ops.group_navitta_admin")

    @api.model
    def _branch_stock_roots(self):
        """{branch id: stock.location ids}: the stock locations of the warehouses of each branch's points of sale."""
        out = defaultdict(set)
        for c in self.env["pos.config"].sudo().search([("navitta_branch_id", "!=", False)]):
            loc = c.picking_type_id.warehouse_id.lot_stock_id
            if loc:
                out[c.navitta_branch_id.id].add(loc.id)
        return out

    @api.model
    def _add_stock(self, res):
        """Writes the on-hand quantities into the products' result: item["qty"] / item["qty_other"] and model["stock"]."""
        roots = self._branch_stock_roots()
        items = [i for g in res["models"] for i in g["items"] if i.get("product_id")]
        if res.get("item") and res["item"].get("product_id"):
            items.append(res["item"])
        if not roots or not items:
            return res
        branch, admin = self._stock_scope()
        Loc = self.env["stock.location"].sudo()
        root_ids = sorted({x for ids in roots.values() for x in ids})
        paths = {r.id: r.parent_path for r in Loc.browse(root_ids)}
        groups = self.env["stock.quant"].sudo()._read_group(
            [("product_id", "in", list({i["product_id"] for i in items})), ("location_id", "child_of", root_ids)],
            ["product_id", "location_id"], ["quantity:sum"])
        mine = roots.get(branch.id, set()) if branch else set()
        here, other, per_branch = defaultdict(float), defaultdict(float), defaultdict(float)
        for product, loc, qty in groups:
            under = {r for r, path in paths.items() if (loc.parent_path or "").startswith(path)}
            if under & mine:
                here[product.id] += qty
            elif under:
                other[product.id] += qty
            for b, rs in roots.items():
                if under & rs:
                    per_branch[(b, product.id)] += qty
        names = {b.id: b.name for b in self.env["navitta.branch"].sudo().browse(list(roots))}
        has_here = bool(mine)

        def num(x):
            x = round(x, 2)
            return int(x) if x == int(x) else x
        for i in items:
            i["qty"] = num(here[i["product_id"]]) if has_here else None
            i["qty_other"] = num(other[i["product_id"]])
        for g in res["models"]:
            pids = [i["product_id"] for i in g["items"] if i.get("product_id")]
            if not pids:
                continue
            by, colors = defaultdict(dict), defaultdict(float)
            for i in g["items"]:
                if has_here:
                    by[i["color"]][i["size"] or ""] = num(by[i["color"]].get(i["size"] or "", 0) + here[i["product_id"]])
                    colors[i["color"]] += here[i["product_id"]]
            g["stock"] = {
                "branch_id": branch.id or False, "branch": branch.name or "",
                "here": num(sum(here[p] for p in pids)) if has_here else None,
                "other": num(sum(other[p] for p in pids)),
                "by": dict(by), "colors": {c: num(q) for c, q in colors.items()},
                # management: one compact row per branch (a branch device sees only the other branches' total)
                "branches": sorted(({"id": b, "name": names.get(b, ""), "qty": num(sum(per_branch[(b, p)] for p in pids))}
                                    for b in roots if b in names), key=lambda x: (x["id"] != branch.id, x["name"], x["id"]))
                if admin else [],
            }
        return res

    # ------------------------------------------------------------------ products
    @api.model
    def _product_domain(self):
        return [("active", "=", True), "|", ("sale_ok", "=", True), ("available_in_pos", "=", True),
                ("company_id", "in", [False, self.env.company.id])]

    @staticmethod
    def _split(name):
        """«21160003- حذاء نسائي» -> ("21160003", "حذاء نسائي"); a name without the number -> (None, name)."""
        name = (name or "").strip()
        m = NUMBER.match(name)
        if m:
            return m.group(1), (m.group(2).strip() or m.group(1))
        return None, name

    @api.model
    def _find_products(self, raw):
        """The search in the products: the screen's structure, or None when no product matches."""
        q = (raw or "").strip()
        if len(q) < 2:
            return None
        Tmpl = self.env["product.template"].sudo()
        dom = self._product_domain()
        if q.isdigit():
            if len(q) != 8:
                variant = self._by_barcode(q)
                if variant:
                    return self._product_result(variant.product_tmpl_id, variant)
            cands = Tmpl.search(dom + [("name", "=like", q + "%")], order="name, id", limit=200)
            if len(q) == 8:
                tmpls = cands.filtered(lambda t: self._split(t.name)[0] == q)
                if not tmpls:
                    variant = self._by_barcode(q)        # an 8-digit (EAN-8) barcode
                    if variant:
                        return self._product_result(variant.product_tmpl_id, variant)
            else:
                tmpls = cands.filtered(lambda t: (self._split(t.name)[0] or "").startswith(q))
        else:
            variant = self._by_barcode(q)
            if variant:
                return self._product_result(variant.product_tmpl_id, variant)
            text = re.sub(r"[%_]", "", q)
            if len(text) < 2:
                return None
            tmpls = Tmpl.search(dom + [("name", "ilike", text)], order="name, id", limit=200)
        if not tmpls:
            return None
        nos = list(dict.fromkeys(self._split(t.name)[0] or "p%s" % t.id for t in tmpls))[:MAX_MODELS]
        tmpls = tmpls.filtered(lambda t: (self._split(t.name)[0] or "p%s" % t.id) in nos)
        return self._product_result(tmpls)

    @api.model
    def _by_barcode(self, q):
        """The variant with this barcode: as typed, without the leading zeros, or padded with zeros (EAN-8/UPC/EAN-13/14)."""
        q = q.strip()
        cands = {q}
        if q.isdigit():
            bare = q.lstrip("0")
            cands |= {bare} | {bare.zfill(n) for n in (8, 11, 12, 13, 14) if n > len(bare)}
        cands.discard("")
        prods = self.env["product.product"].sudo().search([("barcode", "in", list(cands))], limit=10)
        ok = self.env["product.template"].sudo().search(self._product_domain() + [("id", "in", prods.product_tmpl_id.ids)])
        return next((p for p in prods if p.product_tmpl_id in ok), None)

    @api.model
    def _product_result(self, tmpls, variant=None):
        groups = self._product_models(tmpls)
        if variant is not None:
            no = self._split(variant.product_tmpl_id.name)[0]
            if no:
                # the whole model: every product named with the same number
                same = self.env["product.template"].sudo().search(
                    self._product_domain() + [("name", "=like", no + "%")]).filtered(lambda t: self._split(t.name)[0] == no)
                groups = self._product_models(same | variant.product_tmpl_id)
            g = groups[0]
            item = next((i for i in g["items"] if i.get("product_id") == variant.id), None)
            if item is None:
                item = self._variant_dict(variant, g["item_no"], g["model"])
                item.pop("_key")
            return {"item": item, "models": groups, "source": "products"}
        return {"models": groups, "source": "products"}

    @api.model
    def _attr_kind(self, attribute):
        if attribute.display_type == "color":
            return "color"
        names = " ".join({attribute.name or "", attribute.with_context(lang="en_US").name or ""}).lower()
        if any(w in names for w in COLOR_WORDS):
            return "color"
        if any(w in names for w in SIZE_WORDS):
            return "size"
        return None

    @api.model
    def _price_incl(self, product):
        """Sale price of one piece including its taxes, in the company currency."""
        company = self.env.company
        price = product.lst_price
        taxes = product.taxes_id._filter_taxes_by_company(company)
        if taxes:
            price = taxes.compute_all(price, currency=company.currency_id, quantity=1.0, product=product)["total_included"]
        return round(price, 2)

    @api.model
    def _variant_dict(self, v, no, model):
        color, size, cseq, sseq = "", "", 0, 0
        for ptav in v.product_template_attribute_value_ids:
            kind = self._attr_kind(ptav.attribute_id)
            if kind == "color" and not color:
                color, cseq = ptav.name or "", ptav.product_attribute_value_id.sequence
            elif kind == "size" and not size:
                size, sseq = ptav.name or "", ptav.product_attribute_value_id.sequence
        return {"alu": v.barcode or "", "item_no": no, "model": model, "color": color, "size": size or None,
                "price": self._price_incl(v) or None, "dcs": None, "heel": None, "material": None,
                "product_id": v.id, "_key": (cseq, sseq, self._size_key(size), v.id)}

    @api.model
    def _product_models(self, tmpls):
        """Products -> models with their colors and sizes (the structure of _group)."""
        out = {}
        for t in tmpls.sorted(lambda x: (x.name or "", x.id)):
            no, model = self._split(t.name)
            no = no or t.default_code or model
            g = out.setdefault(no, {"item_no": no, "model": model, "price": None, "dcs": None, "heel": None, "material": None,
                                    "colors": [], "items": []})
            g["items"] += [self._variant_dict(v, no, model) for v in t.product_variant_ids]
        for g in out.values():
            g["items"].sort(key=lambda i: i.pop("_key"))
            prices = [i["price"] for i in g["items"] if i["price"]]
            g["price"] = min(prices) if prices else None
            for i in g["items"]:
                if not (i["color"] or i["size"]):
                    continue
                c = next((c for c in g["colors"] if c["color"] == i["color"]), None)
                if not c:
                    c = {"color": i["color"], "sizes": []}
                    g["colors"].append(c)
                if i["size"] and i["size"] not in c["sizes"]:
                    c["sizes"].append(i["size"])
        return list(out.values())
