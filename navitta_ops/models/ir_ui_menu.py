from odoo import api, models, tools


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    @api.model
    @tools.ormcache("frozenset(self.env.user.groups_id.ids)", "debug")
    def _visible_menu_ids(self, debug=False):
        """A branch device sees the branch screen only, like the old app: none of Odoo's other apps
        (Discuss, Employees, Dashboards…). Management and system administrators are not affected.
        The cache key is the user's groups, so the condition below depends on groups only."""
        visible = super()._visible_menu_ids(debug)
        user = self.env.user
        if (not user.has_group("navitta_ops.group_navitta_branch") or user.has_group("navitta_ops.group_navitta_admin")
                or user.has_group("base.group_system")):
            return visible
        root = self.env.ref("navitta_ops.menu_root", raise_if_not_found=False)
        if not root:
            return visible
        keep = self.sudo().browse(visible).filtered(lambda m: m.parent_path and m.parent_path.startswith("%d/" % root.id))
        return set(keep.ids)
