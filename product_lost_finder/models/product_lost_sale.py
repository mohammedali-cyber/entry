import io
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare
from odoo.tools.misc import xlsxwriter


class ProductLostSale(models.Model):
    """A sale missed because the product had no stock in the employee's branch.

    Follow-up record only: it never touches stock, sales or accounting.
    """
    _name = 'product.lost.sale'
    _description = 'Lost Sale'
    _order = 'date desc, id desc'
    _rec_name = 'product_id'
    _check_company_auto = True

    date = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True, index=True)
    product_id = fields.Many2one('product.product', string="Product", required=True, readonly=True,
                                 ondelete='restrict', index=True)
    product_tmpl_id = fields.Many2one(related='product_id.product_tmpl_id', string="Product Template", store=True)
    warehouse_id = fields.Many2one('stock.warehouse', string="Branch", required=True, readonly=True,
                                   ondelete='restrict', index=True, check_company=True)
    company_id = fields.Many2one(related='warehouse_id.company_id', store=True, index=True)
    user_id = fields.Many2one('res.users', string="Employee", required=True, readonly=True,
                              default=lambda self: self.env.user)
    quantity = fields.Float(digits='Product Unit of Measure', default=1.0, required=True)
    uom_id = fields.Many2one(related='product_id.uom_id', string="Unit")
    currency_id = fields.Many2one(related='company_id.currency_id')
    price_unit = fields.Monetary(string="Sales Price", readonly=True, aggregator=None,
                                 help="Sales price of the product when the lost sale was recorded.")
    amount = fields.Monetary(string="Lost Amount", compute='_compute_amount', store=True)
    note = fields.Char()

    @api.depends('quantity', 'price_unit')
    def _compute_amount(self):
        for sale in self:
            sale.amount = sale.quantity * sale.price_unit

    @api.constrains('quantity')
    def _check_quantity(self):
        if any(sale.quantity <= 0 for sale in self):
            raise ValidationError(_("The requested quantity must be greater than zero."))

    @api.model
    def record_lost_sale(self, product_id, warehouse_id, quantity=1.0, note=False):
        """Called by the search page button: record a lost sale for the user's branch."""
        Finder = self.env['product.lost.finder']
        Finder._check_user_access()
        branch = Finder._get_branch(warehouse_id)
        product = self.env['product.product'].browse(product_id).exists()
        if not product or not product.is_storable:
            raise UserError(_("This product does not exist or does not track inventory."))
        qty_in_branch = product.with_context(warehouse_id=branch.id).qty_available
        if float_compare(qty_in_branch, 0.0, precision_rounding=product.uom_id.rounding) > 0:
            raise UserError(_("%(product)s is available in %(branch)s: a lost sale can only be recorded "
                              "when the product is out of stock in your branch.",
                              product=product.display_name, branch=branch.name))
        sale = self.create({
            'product_id': product.id,
            'warehouse_id': branch.id,
            'quantity': quantity,
            'price_unit': product.lst_price,
            'note': note or False,
        })
        return sale.id

    # ------------------------------------------------------------------
    # Detailed report: lost quantities by product / attribute values
    # ------------------------------------------------------------------

    def _detail_attribute_values(self, product):
        return product.product_template_attribute_value_ids.product_attribute_value_id

    @api.model
    def get_detail_options(self):
        """Choices of the detailed report filters, built from the visible lost sales."""
        Finder = self.env['product.lost.finder']
        Finder._check_user_access()
        products = self.env['product.product'].union(
            *(product for [product] in self._read_group([], ['product_id'])))
        values = self._detail_attribute_values(products)
        attributes = values.attribute_id.sorted(lambda a: (a.sequence, a.id))
        column_attribute = attributes.filtered(
            lambda a: any(word in a.name.lower() for word in ('color', 'colour', 'لون')))[:1] or attributes[:1]
        is_manager = Finder._is_manager()
        warehouses = Finder._allowed_warehouses()
        if not is_manager:
            warehouses = Finder._user_branch(warehouses)
        return {
            'is_manager': is_manager,
            'warehouses': [{'id': w.id, 'name': w.name} for w in warehouses],
            'templates': [{'id': t.id, 'name': t.name}
                          for t in products.product_tmpl_id.sorted(lambda t: t.name or '')],
            'attributes': [{
                'id': attribute.id,
                'name': attribute.name,
                'values': [{'id': v.id, 'name': v.name} for v in
                           (values & attribute.value_ids).sorted(lambda v: (v.sequence, v.id))],
            } for attribute in attributes],
            'column_attribute_id': column_attribute.id or False,
        }

    def _detail_domain(self, filters):
        domain = []
        if filters.get('template_id'):
            domain.append(('product_tmpl_id', '=', filters['template_id']))
        for value_id in filters.get('value_ids') or []:
            domain.append(('product_id.product_template_attribute_value_ids.product_attribute_value_id', '=', value_id))
        Finder = self.env['product.lost.finder']
        if not Finder._is_manager():
            # Record rules already restrict employees to their branch; keep the filter explicit.
            domain.append(('warehouse_id', '=', self.env.user.lost_finder_warehouse_id.id))
        elif filters.get('warehouse_id'):
            domain.append(('warehouse_id', '=', filters['warehouse_id']))
        tz = pytz.timezone(self.env.user.tz or 'UTC')
        for key, operator, days in (('date_from', '>=', 0), ('date_to', '<', 1)):
            if filters.get(key):
                day = fields.Date.to_date(filters[key]) + timedelta(days=days)
                local_start = tz.localize(datetime.combine(day, time.min))
                domain.append(('date', operator, local_start.astimezone(pytz.utc).replace(tzinfo=None)))
        return domain

    @api.model
    def get_detail_report(self, filters):
        """Matrix of lost quantities: rows are the products, columns the values of one
        attribute (color by default). Other attributes (e.g. size) are not broken down:
        they only narrow the report when a value is chosen in the filters."""
        self.env['product.lost.finder']._check_user_access()
        filters = filters or {}
        groups = self._read_group(self._detail_domain(filters), ['product_id'], ['quantity:sum', 'amount:sum'])
        column_attribute = self.env['product.attribute'].browse(filters.get('column_attribute_id')).exists()

        columns, rows = {}, {}
        for product, quantity, amount in groups:
            column_value = self._detail_attribute_values(product).filtered(
                lambda v: v.attribute_id == column_attribute)[:1]
            column_key = column_value.id or 0
            columns.setdefault(column_key, column_value)
            template = product.product_tmpl_id
            row = rows.setdefault(template.id, {'label': template.name, 'cells': {}, 'total': 0.0, 'amount': 0.0})
            row['cells'][str(column_key)] = row['cells'].get(str(column_key), 0.0) + quantity
            row['total'] += quantity
            row['amount'] += amount

        no_value_label = _("Other") if column_attribute else _("Quantity")
        ordered_columns = sorted(columns.items(), key=lambda c: (not c[0], c[1].sequence, c[1].id))
        column_totals = {}
        for row in rows.values():
            for key, quantity in row['cells'].items():
                column_totals[key] = column_totals.get(key, 0.0) + quantity
        sorted_rows = sorted(rows.values(), key=lambda r: (-r['total'], r['label']))
        return {
            'columns': [{'key': str(key), 'name': value.name or no_value_label} for key, value in ordered_columns],
            'rows': sorted_rows,
            'column_totals': column_totals,
            'total': sum(r['total'] for r in sorted_rows),
            'amount': sum(r['amount'] for r in sorted_rows),
            'currency_id': self.env.company.currency_id.id,
        }

    @api.model
    def export_detail_xlsx(self, filters):
        """Excel file of the detailed report: returns (file name, content)."""
        report = self.get_detail_report(filters)
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet(_("Detailed Report"))
        if self.env['res.lang']._lang_get(self.env.user.lang).direction == 'rtl':
            sheet.right_to_left()
        title = workbook.add_format({'bold': True, 'font_size': 14})
        info = workbook.add_format({'font_color': '#555555'})
        head = workbook.add_format({'bold': True, 'bg_color': '#714B67', 'font_color': 'white',
                                    'border': 1, 'align': 'center', 'valign': 'vcenter'})
        label = workbook.add_format({'border': 1, 'bold': True})
        qty = workbook.add_format({'border': 1, 'align': 'center', 'num_format': '#,##0.##'})
        total = workbook.add_format({'border': 1, 'align': 'center', 'bold': True, 'bg_color': '#F2F2F2',
                                     'num_format': '#,##0.##'})
        money = workbook.add_format({'border': 1, 'align': 'center', 'num_format': '#,##0.00'})
        money_total = workbook.add_format({'border': 1, 'align': 'center', 'bold': True, 'bg_color': '#F2F2F2',
                                           'num_format': '#,##0.00'})

        sheet.write(0, 0, _("Lost Sales - Detailed Report"), title)
        sheet.write(1, 0, self._detail_filters_text(filters), info)
        header_row = 3
        headers = [_("Product")] + [c['name'] for c in report['columns']] + [_("Total"), _("Lost Amount")]
        sheet.write_row(header_row, 0, headers, head)
        row_index = header_row
        for row in report['rows']:
            row_index += 1
            sheet.write(row_index, 0, row['label'], label)
            for col, column in enumerate(report['columns'], start=1):
                sheet.write_number(row_index, col, row['cells'].get(column['key'], 0.0), qty)
            sheet.write_number(row_index, len(report['columns']) + 1, row['total'], total)
            sheet.write_number(row_index, len(report['columns']) + 2, row['amount'], money)
        row_index += 1
        sheet.write(row_index, 0, _("Total"), label)
        for col, column in enumerate(report['columns'], start=1):
            sheet.write_number(row_index, col, report['column_totals'].get(column['key'], 0.0), total)
        sheet.write_number(row_index, len(report['columns']) + 1, report['total'], total)
        sheet.write_number(row_index, len(report['columns']) + 2, report['amount'], money_total)
        sheet.set_column(0, 0, 40)
        sheet.set_column(1, len(headers) - 1, 12)
        sheet.freeze_panes(header_row + 1, 1)
        workbook.close()
        now = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        # ASCII file name on purpose: browsers may drop non-Latin names of blob downloads.
        return 'Lost_Sales_Detail_%s.xlsx' % now.strftime('%Y-%m-%d_%H%M'), output.getvalue()

    def _detail_filters_text(self, filters):
        parts = []
        if filters.get('template_id'):
            parts.append(self.env['product.template'].browse(filters['template_id']).name)
        parts += self.env['product.attribute.value'].browse(filters.get('value_ids') or []).mapped(
            lambda v: '%s: %s' % (v.attribute_id.name, v.name))
        warehouse_id = filters.get('warehouse_id') if self.env['product.lost.finder']._is_manager() \
            else self.env.user.lost_finder_warehouse_id.id
        parts.append(self.env['stock.warehouse'].browse(warehouse_id).name if warehouse_id else _("All branches"))
        if filters.get('date_from') or filters.get('date_to'):
            parts.append('%s → %s' % (filters.get('date_from') or '…', filters.get('date_to') or '…'))
        return '    '.join(parts)
