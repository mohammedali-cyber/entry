# Lost Sales (`product_lost_finder`) — Odoo 18 Community

A home-screen app (**المبيعات الضائعة**) for shop employees.
The technical name stays `product_lost_finder` so that upgrades keep the recorded data.

1. Type a product number, name or barcode: the product is added to the page as a
   table. Rows are its variants (color / size…), columns are the branches
   (warehouses), with the On Hand quantity in each; the employee's branch comes first.
2. Several products can be added one after the other.
3. When a variant has no stock (quantity `<= 0`) in the employee's branch, a
   **Record lost sale** button appears on its row. It opens a small dialog
   (requested quantity, optional note); product, branch, employee, date and sales
   price are filled automatically.
4. **Lost Sales Report** (list, pivot, graph) lists every recorded lost sale.
5. Variants can be ticked and exported to **Excel** (number, name, variant,
   barcode, sales price, quantity per branch, total, status in the branch).
6. **Detailed Report**: lost quantities as a matrix. Filters: product, each
   attribute (color, size... read from the products automatically), branch,
   period. Columns are the values of one attribute (size by default, can be
   changed); rows are the products and/or the attribute values not filtered.
   Choosing a product shows all its colors × sizes; choosing a color shows every
   product having that color. Exportable to Excel.

Read-only for stock: nothing here changes quantities, moves or accounting. Lost
sales are follow-up records only.

## Branches and rights

| Who | Branch | Lost sales report |
|---|---|---|
| Inventory user (`stock.group_stock_user`) | Fixed: *Branch (Lost Sales)* set on the user | Own branch only |
| **Lost Sales / All Branches Manager** | Can switch branch in the page | All branches |

- Set each employee's branch in *Settings → Users → (user) → Preferences →
  Lost Sales*. An employee without a branch cannot use the page and sees no
  lost sales.
- The manager group is given to the administrator on install.
- Branch columns list the warehouses of the companies selected in the company switcher.
- The detailed report follows the same rule: employees see their branch only.
- Lost sales: employees can create and read (own branch), only managers can edit
  or delete. Record rules also enforce multi-company.
- The server re-checks everything: an employee always records for their own
  branch, and a lost sale is refused if the variant is in stock in that branch.

## Technical

- Depends only on `stock`.
- `product.lost.finder` (AbstractModel): `get_context`, `search_templates`,
  `get_product_tables`, `export_xlsx`.
- `product.lost.sale`: date, product, branch (`warehouse_id`), employee, quantity,
  sales price at recording time, amount, note.
  Detailed report: `get_detail_options`, `get_detail_report`, `export_detail_xlsx`
  (client action `product_lost_finder.detail_report`).
- `res.users.lost_finder_warehouse_id`: employee's branch.
- Quantities: standard `qty_available` with the `warehouse_id` context.
- Search: Odoo's product search (name, internal reference, exact barcode) plus
  partial barcode; services and non-tracked products are excluded.
- Excel: `/product_lost_finder/export_xlsx` and `/product_lost_finder/export_detail_xlsx` (xlsxwriter, shipped with Odoo); the
  sheet is right-to-left for RTL languages.
- Frontend: OWL client action `product_lost_finder.search_page`.

## Installation / upgrade

1. Copy `product_lost_finder/` into a folder of your Odoo `addons_path`.
2. Restart Odoo, then *Apps → Update Apps List*, search "Lost Sales" (or "product_lost_finder"),
   Install (or Upgrade).
3. Set the branch of each employee (see above).
4. Arabic interfaces need `rtlcss` on the server (`npm install -g rtlcss`).

## Tests

```
odoo-bin -d <db> -i product_lost_finder --test-enable --test-tags=/product_lost_finder --stop-after-init
```
