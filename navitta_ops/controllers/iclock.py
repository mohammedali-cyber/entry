"""Receiving side of the ZKTeco "PUSH" (ADMS / Cloud Server) protocol.

On the device: Menu > Comm. > Cloud Server Setting > server address = this Odoo's address (domain or IP), port = 80/443
(or the port Odoo is published on), HTTPS on if the address uses https. The device then calls:

- GET  /iclock/cdata?SN=...&options=all   first contact: we answer with the upload options
- POST /iclock/cdata?SN=...&table=ATTLOG  punches, one per line: user id TAB "YYYY-MM-DD HH:MM:SS" (device time) TAB
                                          status TAB verify mode TAB work code ...; we answer "OK: <lines read>"
- POST /iclock/cdata?SN=...&table=...     other tables (OPERLOG, USERINFO, ATTPHOTO ...): accepted, not used
- GET  /iclock/getrequest?SN=...          heartbeat / command poll: we have no commands, we answer "OK"
- POST /iclock/devicecmd?SN=...           command results: "OK"

A device the system doesn't know is added as "pending"; its punches are refused (the device keeps them and sends them
again) until management approves it and picks its branch.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

MAX_BODY = 2 * 1024 * 1024


def _text(body, status=200):
    return request.make_response(body, headers=[("Content-Type", "text/plain")], status=status)


class NavittaIclock(http.Controller):

    def _device(self):
        """The device record, in its company's language: punch messages, late cases and the emails they send are
        read by staff, while the public user of these requests usually runs in English."""
        ip = request.httprequest.remote_addr or ""
        Device = request.env["navitta.device"].sudo()
        Device = Device.with_context(lang=Device.env.company._navitta_lang())
        dev = Device._from_request(request.params.get("SN"), ip)
        if dev:
            dev = dev.with_context(lang=(dev.branch_id.company_id or dev.company_id)._navitta_lang())
        return dev, ip

    @http.route(["/iclock/cdata", "/iclock/cdata.aspx"], type="http", auth="public", methods=["GET", "POST"],
                csrf=False, save_session=False, sitemap=False)
    def cdata(self, **kw):
        dev, ip = self._device()
        if not dev:
            return _text("FAIL: unknown device", 400)
        if request.httprequest.method == "GET":
            if kw.get("options"):
                return _text(self._options(dev))
            return _text("OK")
        if request.httprequest.content_length and request.httprequest.content_length > MAX_BODY:
            return _text("FAIL: too large", 413)
        body = request.httprequest.get_data(as_text=True) or ""
        table = (kw.get("table") or "").upper()
        if table == "ATTLOG":
            if not dev._accepts(ip):
                _logger.info("Navitta: punches from device %s (%s) refused: %s", dev.serial_number, ip, dev.state)
                return _text("FAIL: device not approved", 403)
            n = dev._receive_attlog(body)
            if kw.get("Stamp"):
                dev.attlog_stamp = kw["Stamp"]
            return _text("OK: %d" % n)
        if not table and body.strip():
            dev.info = body[:2000]
        return _text("OK")

    @http.route(["/iclock/getrequest", "/iclock/getrequest.aspx"], type="http", auth="public", methods=["GET"],
                csrf=False, save_session=False, sitemap=False)
    def getrequest(self, **kw):
        dev, _ip = self._device()
        if not dev:
            return _text("FAIL: unknown device", 400)
        if kw.get("INFO"):
            dev.info = kw["INFO"][:2000]
        return _text("OK")

    @http.route(["/iclock/devicecmd", "/iclock/devicecmd.aspx"], type="http", auth="public", methods=["POST"],
                csrf=False, save_session=False, sitemap=False)
    def devicecmd(self, **kw):
        dev, _ip = self._device()
        return _text("OK" if dev else "FAIL: unknown device", 200 if dev else 400)

    def _options(self, dev):
        """Upload options for the device: send attendance in real time, check in every 10 seconds."""
        offset = 3
        try:
            from datetime import datetime
            off = datetime.now(dev._tz()).utcoffset()
            offset = int(off.total_seconds() // 3600) if off is not None else 3
        except Exception:  # noqa: BLE001 - the default (Saudi time) is fine
            pass
        return "\n".join([
            "GET OPTION FROM: %s" % dev.serial_number,
            "ATTLOGStamp=%s" % (dev.attlog_stamp or "None"),
            "OPERLOGStamp=9999",
            "ATTPHOTOStamp=None",
            "ErrorDelay=30",
            "Delay=10",
            "TransTimes=00:00;14:05",
            "TransInterval=1",
            "TransFlag=TransData AttLog",
            "TimeZone=%s" % offset,
            "Realtime=1",
            "Encrypt=None",
        ]) + "\n"
