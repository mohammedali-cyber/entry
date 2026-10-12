{
    'name': 'Lost Sales',
    'version': '18.0.3.2.0',
    'category': 'Inventory/Inventory',
    'summary': 'Product stock per branch, lost sales reports and Excel export',
    'description': """
Lost Sales
==========
Search page (app icon on the home screen) where shop employees add products by
number, name or barcode and see each variant's On Hand quantity in every branch.
Variants out of stock in the employee's branch can be recorded as lost sales,
reported per branch, and selected variants can be exported to Excel. A detailed
report breaks lost quantities down by product and color, filtered by any attribute.
""",
    'author': 'Mohammed Ali',
    'license': 'LGPL-3',
    'depends': ['stock'],
    'data': [
        'security/product_lost_finder_security.xml',
        'security/ir.model.access.csv',
        'views/product_lost_finder_views.xml',
        'views/product_lost_sale_views.xml',
        'views/res_users_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'product_lost_finder/static/src/**/*',
        ],
    },
    'installable': True,
    'application': True,
}
