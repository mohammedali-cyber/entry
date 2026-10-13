#!/usr/bin/env python3
"""Pretends to be a ZKTeco fingerprint terminal in "Cloud Server / ADMS" mode, to try the link with Odoo
before (or without) a real device. Uses only the Python standard library.

    python3 zk_simulator.py https://odoo.example.com TESTSN001 5
        -> connects as device TESTSN001 and sends one punch for user 5 at the current local time
    python3 zk_simulator.py https://odoo.example.com TESTSN001 5 "2026-10-13 15:31:00" "2026-10-13 23:32:10"
        -> sends punches at these device times (YYYY-MM-DD HH:MM:SS, the branch's local time)

The first run adds the device in Odoo as "pending": approve it (فروع نافيتا ← الإعدادات ← أجهزة البصمة),
pick its branch, write 5 in an employee's «رقم البصمة في الجهاز», and run again.
"""
import sys
import urllib.error
import urllib.request
from datetime import datetime


def call(method, url, body=None):
    req = urllib.request.Request(url, data=body.encode() if body is not None else None, method=method,
                                 headers={"Content-Type": "text/plain", "User-Agent": "iClock Proxy/1.09"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    base, sn, uid = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
    times = sys.argv[4:] or [datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    st, txt = call("GET", f"{base}/iclock/cdata?SN={sn}&options=all&pushver=2.4.1&language=69")
    print(f"handshake -> {st}\n{txt}")
    lines = "".join(f"{uid}\t{t}\t0\t1\t0\t0\t0\n" for t in times)
    st, txt = call("POST", f"{base}/iclock/cdata?SN={sn}&table=ATTLOG&Stamp=9999", lines)
    print(f"punches ({len(times)}) -> {st} {txt}")
    if st == 403:
        print("Device not approved yet: approve it in Odoo, choose its branch, then run again.")
    st, txt = call("GET", f"{base}/iclock/getrequest?SN={sn}")
    print(f"heartbeat -> {st} {txt}")


if __name__ == "__main__":
    main()
