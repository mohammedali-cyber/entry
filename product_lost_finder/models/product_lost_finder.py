import io
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare
from odoo.tools.misc import xlsxwriter


class ProductLostFinder(models.AbstractModel):
    """Backend of the "Lost Sales" product search page.

    Read-only service: it finds products and reports, for every variant, the
    On Hand quantity in each warehouse (branch) of the user's companies. A
    variant whose quantity is zero or negative in the user's branch is lost.
    """
    _name = 'product.lost.finder'
    _description = 'Lost Sales Product Finder'

    _CHOICE_LIMIT = 20

    # ------------------------------------------------------------------
    # Access and branches
    # ------------------------------------------------------------------

    def _check_user_access(self):
        if not self.env.user.has_group('stock.group_stock_user'):
            raise AccessError(_("Only inventory users can search for lost products."))

    def _is_manager(self):
        return self.env.user.has_group('product_lost_finder.group_lost_finder_manager')

    def _allowed_warehouses(self):
        # Record rules already limit warehouses to the user's companies; the explicit
        # domain also keeps the list to the companies currently selected in the switcher.
        return self.env['stock.warehouse'].search([('company_id', 'in', self.env.companies.ids)])

    def _user_branch(self, warehouses):
        branch = self.env.user.lost_finder_warehouse_id
        return branch if branch in warehouses else self.env['stock.warehouse']

    def _get_branch(self, warehouse_id):
        """Warehouse the user works for: their own branch, or any allowed one for managers."""
        warehouses = self._allowed_warehouses()
        if self._is_manager():
            branch = warehouses.filtered(lambda w: w.id == warehouse_id)
        else:
            branch = self._user_branch(warehouses)
        if not branch:
            raise UserError(_("Your branch is not set. Ask your manager to set it on your user."))
        return branch

    @api.model
    def get_context(self):
        self._check_user_access()
        warehouses = self._allowed_warehouses()
        branch = self._user_branch(warehouses)
        is_manager = self._is_manager()
        if is_manager and not branch:
            branch = warehouses.filtered(lambda w: w.company_id == self.env.company)[:1] or warehouses[:1]
        return {
            'is_manager': is_manager,
            'warehouses': [{'id': w.id, 'name': w.name} for w in warehouses],
            'branch_id': branch.id or False,
        }

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _product_domain(self, term):
        # display_name uses Odoo's standard product search (name, internal reference,
        # exact barcode); the extra clause also matches part of a barcode.
        return [
            ('is_storable', '=', True),
            '|',
            ('display_name', 'ilike', term),
            ('barcode', 'ilike', term),
        ]

    @staticmethod
    def _natural_key(name):
        """Sort key putting names that start with a number first, in numeric order
        ("21160003- ..." < "21210001- ..." < "22030022- ..."), then the others A-Z."""
        name = (name or '').strip()
        match = re.match(r'(\d+)', name)
        if match:
            return (0, int(match.group(1)), name.lower())
        return (1, 0, name.lower())

    def _sorted_templates(self, term, limit):
        """Storable product templates matching the text (all of them when empty),
        in natural order: returns (first `limit` templates, total count)."""
        domain = [('is_storable', '=', True)]
        term = (term or '').strip()
        if term:
            domain = [('product_variant_ids', 'any', self._product_domain(term))]
        rows = self.env['product.template'].search_read(domain, ['name'])
        rows.sort(key=lambda row: self._natural_key(row['name']))
        return self.env['product.template'].browse([row['id'] for row in rows[:limit]]), len(rows)

    def _template_choices(self, templates):
        return [{
            'id': t.id,
            'name': t.name,
            'default_code': t.default_code or '',
            'variant_count': t.product_variant_count,
        } for t in templates]

    @api.model
    def search_templates(self, term):
        """Products (templates) matching a name, internal reference or barcode."""
        self._check_user_access()
        if not (term or '').strip():
            return {'templates': [], 'total': 0}
        templates, total = self._sorted_templates(term, self._CHOICE_LIMIT)
        return {'templates': self._template_choices(templates), 'total': total}

    @api.model
    def list_templates(self, term=''):
        """Product list of the detailed report search box: ALL the storable products
        (or all those matching the text), numbered names first. One light query
        (id, name, reference) so that long lists stay fast."""
        self._check_user_access()
        domain = [('is_storable', '=', True)]
        term = (term or '').strip()
        if term:
            domain = [('product_variant_ids', 'any', self._product_domain(term))]
        rows = self.env['product.template'].search_read(domain, ['name', 'default_code'])
        rows.sort(key=lambda row: self._natural_key(row['name']))
        templates = [{'id': r['id'], 'name': r['name'], 'default_code': r['default_code'] or ''} for r in rows]
        return {'templates': templates, 'total': len(templates)}

    def _variant_name(self, product):
        return product.product_template_attribute_value_ids._get_combination_name() or product.display_name

    def _quantities(self, products, warehouses):
        """{warehouse: {product_id: On Hand quantity}} for every warehouse."""
        return {
            warehouse: dict(zip(products.ids, products.with_context(warehouse_id=warehouse.id).mapped('qty_available')))
            for warehouse in warehouses
        }

    @api.model
    def get_product_tables(self, template_ids, warehouse_id):
        """Variants of each product with their quantity in every branch."""
        self._check_user_access()
        branch = self._get_branch(warehouse_id)
        warehouses = branch | self._allowed_warehouses()
        templates = self.env['product.template'].browse(template_ids).exists().filtered('is_storable')
        variants = templates.product_variant_ids
        qties = self._quantities(variants, warehouses)

        tables = []
        for template in templates:
            rows = []
            for product in template.product_variant_ids:
                rounding = product.uom_id.rounding
                by_warehouse = {str(w.id): qties[w][product.id] for w in warehouses}
                rows.append({
                    'id': product.id,
                    'name': self._variant_name(product),
                    'default_code': product.default_code or '',
                    'barcode': product.barcode or '',
                    'qty_by_warehouse': by_warehouse,
                    'total': sum(by_warehouse.values()),
                    'is_lost': float_compare(qties[branch][product.id], 0.0, precision_rounding=rounding) <= 0,
                })
            # Available variants first; the stable sort keeps Odoo's order inside each group.
            rows.sort(key=lambda r: r['is_lost'])
            tables.append({
                'id': template.id,
                'name': template.name,
                'default_code': template.default_code or '',
                'uom_name': template.uom_id.name,
                'variants': rows,
            })
        return {
            'branch_id': branch.id,
            'warehouses': [{'id': w.id, 'name': w.name} for w in warehouses],
            'tables': tables,
        }

    # ------------------------------------------------------------------
    # Excel export
    # ------------------------------------------------------------------

    @api.model
    def export_xlsx(self, product_ids, warehouse_id):
        """Excel file of the selected variants: returns (file name, content)."""
        self._check_user_access()
        branch = self._get_branch(warehouse_id)
        warehouses = branch | self._allowed_warehouses()
        products = self.env['product.product'].browse(product_ids).exists().filtered('is_storable')
        if not products:
            raise UserError(_("Select at least one product to export."))
        qties = self._quantities(products, warehouses)

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet(_("Selected Products"))
        lang = self.env['res.lang']._lang_get(self.env.user.lang)
        if lang.direction == 'rtl':
            sheet.right_to_left()

        title = workbook.add_format({'bold': True, 'font_size': 14})
        info = workbook.add_format({'font_color': '#555555'})
        head = workbook.add_format({'bold': True, 'bg_color': '#1F6E43', 'font_color': 'white',
                                    'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True})
        cell = workbook.add_format({'border': 1, 'align': 'center'})
        text = workbook.add_format({'border': 1, 'align': 'center', 'num_format': '@'})
        price = workbook.add_format({'border': 1, 'align': 'center', 'num_format': '#,##0.00'})
        qty = workbook.add_format({'border': 1, 'align': 'center', 'num_format': '#,##0.##'})
        branch_qty = workbook.add_format({'border': 1, 'align': 'center', 'num_format': '#,##0.##', 'bg_color': '#FFF8E1'})
        lost = workbook.add_format({'border': 1, 'align': 'center', 'bold': True, 'font_color': '#DC3545'})
        available = workbook.add_format({'border': 1, 'align': 'center', 'bold': True, 'font_color': '#198754'})

        now = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        sheet.write(0, 0, _("Selected Products"), title)
        sheet.write(1, 0, _("Branch: %(branch)s    Date: %(date)s    Employee: %(user)s",
                            branch=branch.name, date=now.strftime('%Y-%m-%d %H:%M'), user=self.env.user.name), info)

        headers = [_("Product Number"), _("Product Name"), _("Variant"), _("Barcode"), _("Sales Price")]
        headers += [_("%s (your branch)", w.name) if w == branch else w.name for w in warehouses]
        headers += [_("Total"), _("Status in your branch")]
        header_row = 3
        sheet.write_row(header_row, 0, headers, head)
        sheet.set_row(header_row, 30)

        first_qty_col = 5
        row = header_row
        for product in products:
            row += 1
            sheet.write_string(row, 0, product.default_code or '', text)
            sheet.write(row, 1, product.name, cell)
            sheet.write(row, 2, self._variant_name(product), cell)
            sheet.write_string(row, 3, product.barcode or '', text)
            sheet.write_number(row, 4, product.lst_price, price)
            for col, warehouse in enumerate(warehouses, start=first_qty_col):
                sheet.write_number(row, col, qties[warehouse][product.id], branch_qty if warehouse == branch else qty)
            sheet.write_number(row, first_qty_col + len(warehouses),
                               sum(qties[w][product.id] for w in warehouses), qty)
            is_lost = float_compare(qties[branch][product.id], 0.0, precision_rounding=product.uom_id.rounding) <= 0
            sheet.write(row, first_qty_col + len(warehouses) + 1,
                        _("Lost") if is_lost else _("Available"), lost if is_lost else available)

        sheet.set_column(0, 0, 22)
        sheet.set_column(1, 1, 26)
        sheet.set_column(2, 2, 18)
        sheet.set_column(3, 4, 13)
        sheet.set_column(first_qty_col, first_qty_col + len(warehouses), 14)
        sheet.set_column(first_qty_col + len(warehouses) + 1, first_qty_col + len(warehouses) + 1, 18)
        sheet.freeze_panes(header_row + 1, 0)
        sheet.autofilter(header_row, 0, row, len(headers) - 1)
        workbook.close()

        # ASCII file name on purpose: browsers may drop non-Latin names of blob downloads.
        filename = 'Lost_Products_%s.xlsx' % now.strftime('%Y-%m-%d_%H%M')
        return filename, output.getvalue()
