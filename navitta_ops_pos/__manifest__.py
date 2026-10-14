{
    "name": "Navitta Branch Operations - Point of Sale",
    "summary": "ربط فروع نافيتا بنقطة البيع: المبيعات والضريبة والنقدية ومبيعات كل كاشير من الفواتير الفعلية",
    "description": """
Links each Navitta branch to its Odoo point(s) of sale and each employee to her cashier (pos_hr employee):
sales, tax, invoices, items, returns, cash/card and each cashier's sales come from the paid POS orders;
the cashier list of a branch's point of sale shows that branch's employees; who opened the register and when.
""",
    "version": "18.0.1.0.1",
    "category": "Sales/Point of Sale",
    "author": "Navitta",
    "license": "LGPL-3",
    "depends": ["navitta_ops", "point_of_sale", "pos_hr"],
    "data": [
        "data/navitta_pos_cron.xml",
        "views/navitta_pos_views.xml",
    ],
    "auto_install": True,
    "installable": True,
}
