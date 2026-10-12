import json

from odoo import http
from odoo.http import content_disposition, request


class ProductLostFinderController(http.Controller):

    @http.route('/product_lost_finder/export_xlsx', type='http', auth='user', methods=['POST'])
    def export_xlsx(self, product_ids, warehouse_id, **kwargs):
        filename, content = request.env['product.lost.finder'].export_xlsx(
            [int(pid) for pid in json.loads(product_ids)], int(warehouse_id))
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(filename)),
        ])

    @http.route('/product_lost_finder/export_detail_xlsx', type='http', auth='user', methods=['POST'])
    def export_detail_xlsx(self, filters, **kwargs):
        filename, content = request.env['product.lost.sale'].export_detail_xlsx(json.loads(filters))
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition(filename)),
        ])
