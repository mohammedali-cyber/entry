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

        warehouse = self._allowed_warehouses().filtered(lambda w: w.id == warehouse_id)
        if not warehouse:
            raise UserError(_("Please select a warehouse."))

        domain = [
            ('is_storable', '=', True),
            '|', '|',
            ('name', 'ilike', term),
            ('default_code', 'ilike', term),
            ('barcode', '=', term),
        ]
        Product = self.env['product.product'].with_context(warehouse_id=warehouse.id)
        total = Product.search_count(domain)
        products = Product.search(domain, limit=self._RESULT_LIMIT)

        results = []
        for product in products:
            qty = product.qty_available
            results.append({
                'id': product.id,
                'name': product.display_name,
                'default_code': product.default_code or '',
                'barcode': product.barcode or '',
                'qty_available': qty,
                'uom_name': product.uom_id.name,
                'is_lost': float_compare(qty, 0.0, precision_rounding=product.uom_id.rounding) <= 0,
            })
        return {'products': results, 'total': total}
