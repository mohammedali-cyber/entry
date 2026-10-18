{
    "name": "Navitta Branch Operations - Point of Sale",
    "summary": "Link Navitta branches to the Point of Sale: sales, VAT, cash and each employee's sales from the actual orders",
    "description": """
Links each Navitta branch to its Odoo point(s) of sale and each point of sale to its owner employee (her own device):
sales, tax, invoices, items, returns, cash/card and each employee's sales come from the paid POS orders (the whole
history, per branch and per device); who opened the register and when; the model search finds the Odoo products.
The Point of Sale's own settings and cashier lists are never changed.
""",
    "version": "18.0.1.4.0",
    "category": "Sales/Point of Sale",
    "author": "Navitta",
    "license": "LGPL-3",
    "depends": ["navitta_ops", "point_of_sale", "pos_hr"],
    "data": [
        "security/navitta_pos_security.xml",
        "security/ir.model.access.csv",
        "data/navitta_pos_cron.xml",
        "views/navitta_pos_views.xml",
    ],
    "auto_install": True,
    "installable": True,
}
