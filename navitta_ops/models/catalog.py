import re

from odoo import api, fields, models


class NavittaCatalogItem(models.Model):
    """One line of the models file: a model in one color and one size, with its barcode (ALU)."""
    _name = "navitta.catalog.item"
    _description = "قطعة في ملف الموديلات"
    _order = "sequence, id"
    _rec_name = "model"

    alu = fields.Char("الباركود (ALU)", required=True, index=True, help="بدون الأصفار في البداية")
    item_no = fields.Char("رقم المؤسسة", required=True, index=True)
    model = fields.Char("الموديل", required=True, index=True)
    color = fields.Char("اللون", required=True)
    size = fields.Char("المقاس")
    price = fields.Float("السعر شامل الضريبة", digits=(10, 2))
    dcs = fields.Char("DCS")
    heel = fields.Char("الكعب")
    material = fields.Char("الخامة")
    sequence = fields.Integer("ترتيب الملف")

    _sql_constraints = [("alu_uniq", "unique(alu)", "الباركود مكرر")]

    @staticmethod
    def _size_key(s):
        try:
            return float(s or 0)
        except ValueError:
            return 0.0

    @api.model
    def _group(self, items):
        """Items -> models with their colors and sizes (file order)."""
        out = {}
        for r in items.sorted(lambda x: (x.sequence, x.id)):
            g = out.setdefault(r.item_no, {
                "item_no": r.item_no, "model": r.model, "price": r.price or None, "dcs": r.dcs or None,
                "heel": r.heel or None, "material": r.material or None, "colors": [], "items": []})
            g["items"].append(self._item_dict(r))
            c = next((c for c in g["colors"] if c["color"] == r.color), None)
            if not c:
                c = {"color": r.color, "sizes": []}
                g["colors"].append(c)
            if r.size and r.size not in c["sizes"]:
                c["sizes"].append(r.size)
        for g in out.values():
            for c in g["colors"]:
                c["sizes"].sort(key=self._size_key)
        return list(out.values())

    @api.model
    def _item_dict(self, r):
        return {"alu": r.alu, "item_no": r.item_no, "model": r.model, "color": r.color, "size": r.size or None,
                "price": r.price or None, "dcs": r.dcs or None, "heel": r.heel or None, "material": r.material or None}

    @api.model
    def _find(self, raw):
        """A scanned barcode gives one item; an 8-digit number or a model name gives up to 6 whole models."""
        q = (raw or "").strip().upper()
        Item = self.sudo()
        if len(q) < 2:
            return {"models": []}
        if q.isdigit():
            alu = q.lstrip("0")
            if len(q) != 8 and alu:
                item = Item.search([("alu", "=", alu)], limit=1)
                if item:
                    return {"item": self._item_dict(item), "models": self._group(Item.search([("item_no", "=", item.item_no)]))}
            exact = Item.search([("item_no", "=", q)])
            if exact:
                return {"models": self._group(exact)}
            nos = Item.search([("item_no", "=like", q + "%")], limit=400).mapped("item_no")
        else:
            q = re.sub(r"[%_]", "", q)
            nos = Item.search([("model", "=ilike", q + "%")], limit=400).mapped("item_no")
        nos = list(dict.fromkeys(nos))[:6]
        return {"models": self._group(Item.search([("item_no", "in", nos)])) if nos else []}

    @api.model
    def _info(self):
        self.flush_model()
        self.env.cr.execute("SELECT count(*), count(DISTINCT item_no), max(write_date) FROM navitta_catalog_item")
        n, m, at = self.env.cr.fetchone()
        return {"items": n, "models": m, "updated_at": at and fields.Datetime.to_string(at)}
