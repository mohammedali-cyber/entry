{
    'name': 'Lost Products',
    'version': '18.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Search products and flag the ones with no stock in the branch warehouse as lost',
    'description': """
Lost Products
=============
A simple search page (app icon on the home screen) where inventory users type a
product name, internal reference or barcode. Every storable product found is
shown with its On Hand quantity in the selected warehouse (branch):

* quantity <= 0 : the product is flagged as "Lost"
* quantity > 0  : the product is shown as available with its quantity

The module is read-only: it never changes stock, moves or accounting data.
""",
    'author': 'Mohammed Ali',
    'license': 'LGPL-3',
    'depends': ['stock'],
    'data': [
        'views/product_lost_finder_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'product_lost_finder/static/src/**/*',
        ],
    },
    'installable': True,
    'application': True,
}
