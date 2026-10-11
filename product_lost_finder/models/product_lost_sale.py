from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


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
