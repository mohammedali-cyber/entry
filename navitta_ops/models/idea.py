"""«💡 الأفكار»: management's backlog of improvement ideas for the next releases, grouped by release (site: ideas)."""
import base64
import binascii

from odoo import api, fields, models

from .request import MAX_PHOTO_BYTES

APP_VERSION = "26.3"
IDEA_VERSIONS = ["26.2", "26.3", "26.4", "later"]
IDEA_STATES = [("todo", "⏳ Not yet"), ("done", "✅ Done"), ("no", "✕ No")]


class NavittaIdea(models.Model):
    _name = "navitta.idea"
    _description = "Idea"
    _order = "create_date desc, id desc"
    _rec_name = "title"

    title = fields.Char("Idea", required=True, translate=True)
    details = fields.Text("Details", translate=True)
    photo = fields.Binary("Photo", attachment=True)
    photo_mime = fields.Char(default="image/jpeg")
    state = fields.Selection(IDEA_STATES, "Status", required=True, default="todo")
    version = fields.Char("Release", required=True, default="later",
                          help="Navitta Pro release the idea is planned for (26.2, 26.3, 26.4…), or «later»")
    updated_at = fields.Datetime("Last update", default=fields.Datetime.now)

    @api.model
    def _check_photo(self, data):
        """(base64, mime) of an attached photo, or (None, error code). Same limits as the branch photos."""
        try:
            raw = base64.b64decode(data or "", validate=True)
        except (binascii.Error, ValueError):
            return None, "bad_file"
        if len(raw) < 100 or len(raw) > MAX_PHOTO_BYTES:
            return None, "bad_file"
        sig = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png", b"RIFF": "image/webp"}
        mime = next((m for k, m in sig.items() if raw.startswith(k)), None)
        if not mime:
            return None, "bad_file"
        return mime, None
