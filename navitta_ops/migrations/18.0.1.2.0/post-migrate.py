def migrate(cr, version):
    """Check-ins recorded before photos existed are not «بدون صورة»."""
    if version:
        cr.execute("UPDATE navitta_attendance SET no_photo = false WHERE in_photo_id IS NULL AND out_photo_id IS NULL")
