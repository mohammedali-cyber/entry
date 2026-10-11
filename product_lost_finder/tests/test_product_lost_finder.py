from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestProductLostFinder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.wh_main = cls.env['stock.warehouse'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.wh_branch = cls.env['stock.warehouse'].create({
            'name': 'PLF Branch Warehouse',
            'code': 'PLFB',
            'company_id': cls.company.id,
        })
        Product = cls.env['product.product']
        cls.p_available = Product.create({'name': 'PLF Pencil', 'is_storable': True, 'default_code': 'PLF-001', 'barcode': '9990001'})
        cls.p_zero = Product.create({'name': 'PLF Eraser', 'is_storable': True})
        cls.p_negative = Product.create({'name': 'PLF Ruler', 'is_storable': True})
        cls.p_branch_only = Product.create({'name': 'PLF Stapler', 'is_storable': True})
        cls.p_service = Product.create({'name': 'PLF Delivery Service', 'type': 'service'})
        cls.p_consumable = Product.create({'name': 'PLF Paper Bag', 'type': 'consu', 'is_storable': False})

        Quant = cls.env['stock.quant']
        Quant._update_available_quantity(cls.p_available, cls.wh_main.lot_stock_id, 5)
        Quant._update_available_quantity(cls.p_negative, cls.wh_main.lot_stock_id, -2)
        Quant._update_available_quantity(cls.p_branch_only, cls.wh_branch.lot_stock_id, 3)

        cls.stock_user = new_test_user(cls.env, login='plf_stock_user', groups='stock.group_stock_user')
        cls.basic_user = new_test_user(cls.env, login='plf_basic_user', groups='base.group_user')
        cls.finder = cls.env['product.lost.finder'].with_user(cls.stock_user)

    def _search(self, term, warehouse):
        result = self.finder.search_products(term, warehouse.id)
        return {p['id']: p for p in result['products']}, result

    def test_lost_and_available_products(self):
        products, _result = self._search('PLF', self.wh_main)
        self.assertFalse(products[self.p_available.id]['is_lost'])
        self.assertEqual(products[self.p_available.id]['qty_available'], 5)
        self.assertTrue(products[self.p_zero.id]['is_lost'], "Zero quantity means lost")
        self.assertTrue(products[self.p_negative.id]['is_lost'], "Negative quantity means lost")
        self.assertEqual(products[self.p_negative.id]['qty_available'], -2)

    def test_non_storable_products_excluded(self):
        products, _result = self._search('PLF', self.wh_main)
        self.assertNotIn(self.p_service.id, products)
        self.assertNotIn(self.p_consumable.id, products)

    def test_quantity_is_per_warehouse(self):
        products, _result = self._search('Stapler', self.wh_main)
        self.assertTrue(products[self.p_branch_only.id]['is_lost'])
        products, _result = self._search('Stapler', self.wh_branch)
        self.assertFalse(products[self.p_branch_only.id]['is_lost'])
        self.assertEqual(products[self.p_branch_only.id]['qty_available'], 3)
        products, _result = self._search('Pencil', self.wh_branch)
        self.assertTrue(products[self.p_available.id]['is_lost'])

    def test_search_by_reference_and_barcode(self):
        products, _result = self._search('plf-001', self.wh_main)
        self.assertEqual(list(products), [self.p_available.id])
        products, _result = self._search('9990001', self.wh_main)
        self.assertEqual(list(products), [self.p_available.id])

    def test_not_found_and_empty_term(self):
        products, result = self._search('PLF-does-not-exist', self.wh_main)
        self.assertFalse(products)
        self.assertEqual(result['total'], 0)
        self.assertEqual(self.finder.search_products('   ', self.wh_main.id), {'products': [], 'total': 0})

    def test_warehouses_list(self):
        data = self.finder.get_warehouses()
        ids = [w['id'] for w in data['warehouses']]
        self.assertIn(self.wh_main.id, ids)
        self.assertIn(self.wh_branch.id, ids)
        self.assertIn(data['default_warehouse_id'], ids)

    def test_access_rights(self):
        finder = self.env['product.lost.finder'].with_user(self.basic_user)
        with self.assertRaises(AccessError):
            finder.get_warehouses()
        with self.assertRaises(AccessError):
            finder.search_products('PLF', self.wh_main.id)

    def test_warehouse_of_other_company_refused(self):
        other_company = self.env['res.company'].create({'name': 'PLF Other Company'})
        other_wh = self.env['stock.warehouse'].search([('company_id', '=', other_company.id)], limit=1)
        self.assertTrue(other_wh)
        with self.assertRaises(UserError):
            self.finder.search_products('PLF', other_wh.id)

    def test_available_products_listed_first(self):
        result = self.finder.search_products('PLF', self.wh_main.id)
        flags = [p['is_lost'] for p in result['products']]
        self.assertEqual(flags, sorted(flags), "Available products must come before lost ones")
        self.assertEqual(result['products'][0]['id'], self.p_available.id)

    def test_available_first_beyond_result_limit(self):
        self.patch(type(self.env['product.lost.finder']), '_RESULT_LIMIT', 2)
        result = self.finder.search_products('PLF', self.wh_branch.id)
        self.assertEqual(result['products'][0]['id'], self.p_branch_only.id)
        self.assertEqual(len(result['products']), 2)
        self.assertEqual(result['total'], 4)

    def test_stock_in_other_warehouses(self):
        products, _result = self._search('Stapler', self.wh_main)
        self.assertEqual(
            products[self.p_branch_only.id]['other_warehouses'],
            [{'name': self.wh_branch.name, 'qty_available': 3}],
        )
        products, _result = self._search('Eraser', self.wh_main)
        self.assertEqual(products[self.p_zero.id]['other_warehouses'], [])

    def test_search_by_partial_barcode(self):
        products, _result = self._search('99900', self.wh_main)
        self.assertEqual(list(products), [self.p_available.id])
