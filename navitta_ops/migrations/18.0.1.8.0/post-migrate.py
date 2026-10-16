def migrate(cr, version):
    """26.3 daily report: the cash and the card-terminal settlement are now counted by the employee and compared with
    the Point of Sale. Until now a report of a Point of Sale day saved the system's cash / card as they were, so the
    existing reports of those days get the same figures as «per Point of Sale» (no difference)."""
    if not version:
        return
    cr.execute("""
        UPDATE navitta_daily_report r
           SET has_system = TRUE, sys_cash = COALESCE(r.cash, 0), sys_card = COALESCE(r.card, 0), cash_diff = 0, card_diff = 0
          FROM navitta_daily_sales s
         WHERE s.branch_id = r.branch_id AND s.day = r.day
           AND s.source IS NOT NULL AND s.source NOT IN ('report', 'manual', 'admin', 'file')""")
