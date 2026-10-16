def migrate(cr, version):
    """26.3 automatic visits («sold without the counter»): the orders made before this update count as already matched
    with the counter, so the update doesn't add visits (and block check-outs) for sales of the past."""
    if not version:
        return
    cr.execute("UPDATE pos_order SET navitta_checked = TRUE WHERE navitta_checked IS NOT TRUE")
