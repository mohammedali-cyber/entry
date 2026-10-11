# Lost Products (`product_lost_finder`) — Odoo 18 Community

A home-screen app (**المنتجات الضائعة**) with a simple, Google-like search page.
Inventory users type a product name, internal reference or barcode, and every
matching storable product is listed with its **On Hand** quantity in the selected
warehouse (branch):

| On Hand quantity in the warehouse | Result shown            |
|-----------------------------------|-------------------------|
| `<= 0`                            | 🔴 المنتج ضائع (lost)   |
| `> 0`                             | 🟢 المنتج متوفر          |

If nothing matches: "لا يوجد منتج بهذا الاسم".

## Behaviour

- **Quantity**: standard `qty_available` (On Hand) computed by Odoo with the
  `warehouse_id` context, so only the stock locations of that warehouse are counted.
- **Warehouse (branch)**: dropdown limited to warehouses of the companies currently
  selected in the company switcher. Default: the user's *Default Warehouse*
  (`property_warehouse_id`, from `sale_stock`, when installed), else the first
  warehouse of the current company. The last choice is remembered in the browser.
- **Other branches**: under each product, the warehouses (among the user's active
  companies) where it still has stock are listed, e.g. "Available in other
  warehouses: Jeddah (6.00)". Note that the product form's *On Hand* button sums
  all these warehouses, while this page judges "lost" on the selected one only.
- **Order**: available products first (over all matches, not only the first page),
  then lost products.
- **Search**: Odoo's standard product search (name, internal reference, exact
  barcode) plus partial barcode match.
- **Excluded**: services, combos and consumables without inventory tracking
  (`is_storable = False`), and archived products.
- At most 50 results are shown; the page asks to refine the search beyond that.
- Clicking a result opens the product form.

## Technical

- Depends only on `stock`.
- `product.lost.finder` (AbstractModel, no table) exposes two RPC methods:
  `get_warehouses()` and `search_products(term, warehouse_id)`.
- Frontend: OWL client action `product_lost_finder.search_page`
  (`static/src/`), menu `menu_product_lost_finder_root`.
- **Read-only**: no write on stock, moves, quants or accounting.

## Security

- Menu restricted to `stock.group_stock_user`; both RPC methods re-check the group
  server side (AccessError otherwise).
- Products and warehouses are read with the user's own rights, so multi-company
  record rules apply. A warehouse id outside the user's active companies is refused.

## Installation

1. Copy `product_lost_finder/` into a folder of your Odoo `addons_path`.
2. Restart Odoo, then *Apps → Update Apps List*, search "Lost Products", Install.
3. Arabic interfaces need `rtlcss` on the server (`npm install -g rtlcss`), which is
   already required by Odoo for any right-to-left language.

## Tests

```
odoo-bin -d <db> -i product_lost_finder --test-enable --test-tags=/product_lost_finder --stop-after-init
```
