from . import models
from . import wizard
from . import controllers


def post_init_hook(env):
    """Schedule the nightly summary at 23:45 in the company time zone."""
    env["navitta.api"]._set_report_cron_time()
