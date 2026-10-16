def migrate(cr, version):
    """26.3: alerts sent before «تنبيهاتي» showed them count as already read (so they don't all show as new)."""
    if not version:
        return
    cr.execute("UPDATE navitta_notification SET read_at = create_date WHERE read_at IS NULL")
