"""Management screens for the sales days (Navitta Pro 26.3): correcting a day by hand with an edit log, the figures that
changed after the branch closed the day, and the target file (Excel / CSV) with a preview before it is applied."""
import base64
import io
import logging
import re
from datetime import date, datetime, timedelta

from odoo import _, api, fields, models

from .common import differs, iso, sel_label
from .sales import EDIT_FIELDS

_logger = logging.getLogger(__name__)

# file headers (Arabic as in the site's template, or English) -> column
HEADERS = {"الفرع": "branch", "branch": "branch", "التاريخ": "day", "اليوم": "day", "date": "day", "day": "day",
           "التارقت": "target", "target": "target", "المبيعات": "sales", "sales": "sales", "الفواتير": "invoices",
           "invoices": "invoices", "القطع": "items", "items": "items"}
MAX_FILE = 5 * 1024 * 1024
MAX_ROWS = 5000


def _day(v):
    """An Excel date, YYYY-MM-DD / YYYY/MM/DD or DD-MM-YYYY / DD/MM/YYYY -> date, else None."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v if v is not None else "").strip()
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", s)
        if not m:
            return None
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _num(v):
    """'' for an empty cell, else the text without thousands separators and spaces."""
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return repr(float(v)) if not float(v).is_integer() else str(int(v))
    return re.sub(r"[,\s٬]", "", str(v))


def _is_num(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


class NavittaApiSales(models.AbstractModel):
    _inherit = "navitta.api"

    # ------------------------------------------------------------------ a day corrected by management
    @api.model
    def _edit_log_rows(self, branch, day):
        logs = self.env["navitta.sales.edit.log"].sudo().search([("branch_id", "=", branch.id), ("day", "=", day)])
        return [{"at": iso(l.create_date), "field": l.field,
                 "label": sel_label(self.env, "navitta.sales.edit.log", "field", l.field), "old": l.old_value, "new": l.new_value,
                 "reason": l.reason, "by": l.user_id.name or ""} for l in logs]

    @api.model
    def admin_day_get(self, branch_id, day):
        """The day's figures for the «✏️ Edit» window, with the edit log."""
        self._admin()
        b = self.env["navitta.branch"].sudo().browse(int(branch_id or 0)).exists()
        if not b:
            return self._err("not_found")
        d = self._d(day)
        row = self.env["navitta.daily.sales"].sudo().search([("branch_id", "=", b.id), ("day", "=", d)], limit=1)
        return {"branch_id": b.id, "branch": b.name, "day": fields.Date.to_string(d),
                "values": {k: (row[k] if row else 0) for k in EDIT_FIELDS},
                "source": row.source or False, "system": bool(row) and row._is_system(), "has_system": b._has_system(),
                "log": self._edit_log_rows(b, d)}

    @api.model
    def admin_day_edit(self, branch_id, day, vals, reason):
        """Saves management's correction of a day (reason required, logged)."""
        self._admin()
        b = self.env["navitta.branch"].sudo().browse(int(branch_id or 0)).exists()
        if not b:
            return self._err("not_found")
        err = self.env["navitta.daily.sales"]._day_edit(b, self._d(day), vals or {}, reason)
        return self._err(err) if err else {"ok": True}

    @api.model
    def admin_day_changes(self, day, branch_id=False):
        """«⚠️ Changed after closing»: the figures of the day that changed after the daily report was sent."""
        self._admin()
        dom = [("day", "=", self._d(day))] + ([("branch_id", "=", int(branch_id))] if branch_id else [])
        rows = self.env["navitta.sales.change.log"].sudo().search(dom)
        return {"rows": [{"branch": r.branch_id.name, "branch_id": r.branch_id.id, "field": r.field,
                          "label": sel_label(self.env, "navitta.sales.change.log", "field", r.field),
                          "old": r.old_value, "new": r.new_value, "source": r.source,
                          "source_label": sel_label(self.env, "navitta.sales.change.log", "source", r.source),
                          "at": iso(r.create_date)} for r in rows]}

    @api.model
    def _day_marks(self, d0, d1, branch_id=False):
        """{(branch_id, day): {"changed": n, "report": report}} for the sales and day-log screens."""
        dom = [("day", ">=", d0), ("day", "<=", d1)] + ([("branch_id", "=", int(branch_id))] if branch_id else [])
        res = {}
        for c in self.env["navitta.sales.change.log"].sudo().search_read(dom, ["branch_id", "day"]):
            k = (c["branch_id"][0], c["day"])
            res.setdefault(k, {"changed": 0, "report": None})["changed"] += 1
        for r in self.env["navitta.daily.report"].sudo().search(dom):
            res.setdefault((r.branch_id.id, r.day), {"changed": 0, "report": None})["report"] = r
        return res

    @api.model
    def _cash_check(self, rep, row):
        """The counted cash / card of the daily report against the system's current figures (None without a system)."""
        if not rep or not row or not (rep.has_system or row._is_system()):
            return None
        dc, dk = round(rep.cash - row.cash, 2), round(rep.card - row.card, 2)
        return {"cash": rep.cash, "card": rep.card, "sys_cash": row.cash, "sys_card": row.card,
                "cash_diff": dc if differs(rep.cash, row.cash) else 0.0, "card_diff": dk if differs(rep.card, row.card) else 0.0,
                "cash_note": rep.cash_note or "", "card_note": rep.card_note or ""}

    # ------------------------------------------------------------------ target file
    @api.model
    def _read_sheet(self, data, filename):
        """[[cell, …], …] of a .csv or .xlsx file (base64)."""
        raw = base64.b64decode(data or "")
        if len(raw) > MAX_FILE:
            raise ValueError("bad_target_file")
        if (filename or "").lower().endswith(".csv"):
            for enc in ("utf-8-sig", "cp1256"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError("bad_target_file")
            return [re.split(r"[,;\t]", line) for line in text.splitlines() if line.strip()]
        if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError("file_locked")          # an encrypted Office file (password) is an OLE container, not a zip
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            return [list(r) for _i, r in zip(range(MAX_ROWS + 20), ws.iter_rows(values_only=True))]
        except Exception as e:  # noqa: BLE001 - any broken file is reported the same way
            _logger.info("Navitta target file not read: %s", e)
            raise ValueError("bad_target_file") from e

    @api.model
    def _find_branch(self, value, branches):
        s = str(value if value is not None else "").strip()
        if not s:
            return None
        bare = re.sub(r"^فرع\s*", "", s).strip().lower()
        for b in branches:
            names = {b.name.strip().lower(), re.sub(r"^فرع\s*", "", b.name).strip().lower(), (b.code or "").strip().lower(), str(b.id)}
            if s.lower() in names or bare in names:
                return b
        return None

    @api.model
    def admin_targets_preview(self, data, filename):
        """Reads a target file and compares it with the current targets. Nothing is saved.
        Returns {rows: [{branch_id, bname, day, target, sales, invoices, items, err, old}], totals}."""
        self._admin()
        try:
            sheet = self._read_sheet(data, filename)
        except ValueError as e:
            return self._err(str(e))
        head = next((i for i, r in enumerate(sheet[:10])
                     if len([c for c in r if HEADERS.get(str(c if c is not None else "").strip().lower())]) >= 3), -1)
        if head < 0:
            return self._err("no_target_columns")
        cols = [HEADERS.get(str(c if c is not None else "").strip().lower()) for c in sheet[head]]

        def col(r, k):
            return r[cols.index(k)] if k in cols and cols.index(k) < len(r) else None
        branches = self.env["navitta.branch"].sudo().search([])
        out = []
        for r in sheet[head + 1:head + 1 + MAX_ROWS]:
            if not any(str(c if c is not None else "").strip() for c in r):
                continue
            b, d, target = self._find_branch(col(r, "branch"), branches), _day(col(r, "day")), _num(col(r, "target"))
            row = {"branch_id": b.id if b else False, "bname": b.name if b else str(col(r, "branch") or ""),
                   "day": fields.Date.to_string(d) if d else str(col(r, "day") or ""), "target": target,
                   "sales": _num(col(r, "sales")), "invoices": _num(col(r, "invoices")), "items": _num(col(r, "items")), "old": None}
            row["err"] = (_("Branch not found") if not b else _("Wrong date") if not d else
                          _("Wrong target") if target == "" or not _is_num(target) or float(target) < 0 else
                          _("Wrong sales figure") if any(row[k] != "" and (not _is_num(row[k]) or float(row[k]) < 0)
                                                         for k in ("sales", "invoices", "items")) else False)
            if row["err"] or target != "":
                out.append(row)
        if not out:
            return self._err("empty_file")
        ok = [x for x in out if not x["err"]]
        if ok:
            days = sorted(x["day"] for x in ok)
            have = self.env["navitta.daily.sales"].sudo().search_read(
                [("branch_id", "in", list({x["branch_id"] for x in ok})), ("day", ">=", days[0]), ("day", "<=", days[-1])],
                ["branch_id", "day", "target"])
            old = {(h["branch_id"][0], fields.Date.to_string(h["day"])): h["target"] for h in have}
            for x in ok:
                x["old"] = old.get((x["branch_id"], x["day"]))
        return {"rows": out, "file": filename or ""}

    @api.model
    def admin_targets_load(self, rows, filename):
        """Applies the approved rows of the target file (the rows are checked again)."""
        self._admin()
        good = [x for x in (rows or []) if not x.get("err")]
        n = self.env["navitta.daily.sales"]._targets_load(good, filename)
        return {"ok": True, "rows": n}

    @api.model
    def admin_targets_template(self):
        """The template rows: every branch and every day of next month, the figures empty."""
        self._admin()
        today = self._d(False)
        m1 = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        days = []
        d = m1
        while d.month == m1.month:
            days.append(fields.Date.to_string(d))
            d = d + timedelta(days=1)
        rows = [["الفرع", "التاريخ", "التارقت", "المبيعات", "الفواتير", "القطع"]]
        rows += [[b.name, x, "", "", "", ""] for b in self.env["navitta.branch"].sudo().search([]) for x in days]
        return {"name": "targets-%s.csv" % m1.strftime("%Y-%m"), "rows": rows}
