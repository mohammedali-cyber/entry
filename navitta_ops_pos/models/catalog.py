"""Model search («🔎 Model search», the shortage list's item chooser) in the Odoo products first.

The products are named with the 8-digit model number first, e.g. «21160003- حذاء نسائي»; their variants carry a color
and a size attribute and each variant has its barcode. The search answers with the same structure as the models file
(navitta.catalog.item._group / _item_dict), so the screens show a product like a model of the file:

- 8 digits: the model(s) whose name starts with that number; a shorter / longer run of digits: a scanned barcode
  (exact, also with the leading zeros stripped or added) gives that variant (its color and size) and its whole model,
  else the models whose number starts with those digits (up to 6);
- text: the products whose name contains it (up to 6 models).

When no product matches, the models file is searched as before."""
import re

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
        return res if res is not None else super()._find(raw)

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
