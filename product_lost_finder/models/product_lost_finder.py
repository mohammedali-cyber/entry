from odoo import _, api, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare


class ProductLostFinder(models.AbstractModel):
    """Backend of the "Lost Products" search page.

    Read-only service: it searches storable products and reports their On Hand
    quantity in one warehouse (branch). A product whose quantity is zero or
    negative is reported as lost.
    """
    _name = 'product.lost.finder'
    _description = 'Lost Products Finder'

    _RESULT_LIMIT = 50

    def _check_user_access(self):
        if not self.env.user.has_group('stock.group_stock_user'):
            raise AccessError(_("Only inventory users can search for lost products."))

    def _allowed_warehouses(self):
        # Record rules already limit warehouses to the user's companies; the explicit
        # domain also keeps the list to the companies currently selected in the switcher.
        return self.env['stock.warehouse'].search([('company_id', 'in', self.env.companies.ids)])

    def _default_warehouse(self, warehouses):
        user = self.env.user
        # Default warehouse of the user (field added by sale_stock, company dependent).
        if 'property_warehouse_id' in user._fields and user.property_warehouse_id in warehouses:
            return user.property_warehouse_id
        return warehouses.filtered(lambda w: w.company_id == self.env.company)[:1] or warehouses[:1]

    @api.model
    def get_warehouses(self):
        self._check_user_access()
        warehouses = self._allowed_warehouses()
        default = self._default_warehouse(warehouses)
        return {
            'warehouses': [{'id': w.id, 'name': w.display_name} for w in warehouses],
            'default_warehouse_id': default.id or False,
        }

    @api.model
    def search_products(self, term, warehouse_id):
        self._check_user_access()
        term = (term or '').strip()
        if not term:
            return {'products': [], 'total': 0}

        warehouses = self._allowed_warehouses()
        warehouse = warehouses.filtered(lambda w: w.id == warehouse_id)
        if not warehouse:
            raise UserError(_("Please select a warehouse."))

        # display_name uses Odoo's standard product search (name, internal reference,
        # exact barcode); the extra clause also matches part of a barcode.
        domain = [
            ('is_storable', '=', True),
            '|',
            ('display_name', 'ilike', term),
            ('barcode', 'ilike', term),
        ]
        Product = self.env['product.product'].with_context(warehouse_id=warehouse.id)
        total = Product.search_count(domain)

        # Available products first, over all matches and not only the first page.
        available_domain = domain + [('qty_available', '>', 0)]
        products = Product.search(available_domain, limit=self._RESULT_LIMIT)
        if len(products) < self._RESULT_LIMIT:
            products |= Product.search(
                domain + [('id', 'not in', Product._search(available_domain))],
                limit=self._RESULT_LIMIT - len(products),
            )

        # Stock of the same products in the user's other warehouses (branches).
        other_qties = {
            other: dict(zip(products.ids, products.with_context(warehouse_id=other.id).mapped('qty_available')))
            for other in warehouses - warehouse
        }

        results = []
        for product in products:
            rounding = product.uom_id.rounding
            qty = product.qty_available
            results.append({
                'id': product.id,
                'name': product.display_name,
                'default_code': product.default_code or '',
                'barcode': product.barcode or '',
                'qty_available': qty,
                'uom_name': product.uom_id.name,
                'is_lost': float_compare(qty, 0.0, precision_rounding=rounding) <= 0,
                'other_warehouses': [
                    {'name': other.name, 'qty_available': qties[product.id]}
                    for other, qties in other_qties.items()
                    if float_compare(qties[product.id], 0.0, precision_rounding=rounding) > 0
                ],
            })
        results.sort(key=lambda r: r['is_lost'])
        return {'products': results, 'total': total}
