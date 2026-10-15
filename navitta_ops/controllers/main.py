from odoo import http
from odoo.http import request


class NavittaPhoneUpload(http.Controller):
    """Opened on an employee's phone from the QR code on the branch device: take one photo and send it.

    No login: the 10-minute random token is the only key, and it only lets the phone add one photo
    to that branch (it can't read anything).
    """

    @http.route("/navitta/r", type="http", auth="public", methods=["GET"], sitemap=False)
    def page(self, t=None, **kw):
        tok = request.env["navitta.upload.token"]._valid(t)
        # the page is public: show it in the language of the user who made the code (the branch device),
        # else in the company's language
        company = tok.branch_id.company_id if tok else request.env.company
        lang = (tok and tok.sudo().create_uid.lang) or company.sudo()._navitta_lang()
        if not request.env["res.lang"]._get_data(code=lang).code:
            lang = company.sudo()._navitta_lang()
        request.update_context(lang=lang)
        direction = request.env["res.lang"]._get_data(code=lang).direction or "ltr"
        return request.render("navitta_ops.phone_upload_page", {
            "token": t if tok else "", "branch": tok.branch_id.name if tok else "", "done": bool(tok and tok.photo_id),
            "page_lang": (lang or "en_US").replace("_", "-"), "page_dir": direction,
        })

    @http.route("/navitta/r/upload", type="json", auth="public", methods=["POST"], csrf=False, sitemap=False)
    def upload(self, token=None, data=None, mime="image/jpeg", **kw):
        tok = request.env["navitta.upload.token"]._valid(token)
        if not tok:
            return {"error": "expired"}
        photo, err = request.env["navitta.photo"].sudo()._from_b64(tok.branch_id, data, mime)
        if err:
            return {"error": err}
        tok.sudo().photo_id = photo.id
        return {"ok": True}
