"""18.0.2.3.0 (Navitta Pro 26.6 screens): the float is counted with the site's denominations 200/100/50/20/10/5/2/1/0.50/0.25.

The counts are one column per denomination, and the columns of the old list (n100, n50, n10, n5, n1) are kept as they
were, so the floats counted before stay readable with the same totals; the new columns (n200, n20, n2, n050, n025) of
those rows are set to 0. The visits get an optional customer satisfaction (0 = not rated)."""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""UPDATE navitta_float SET n200 = COALESCE(n200, 0), n20 = COALESCE(n20, 0), n2 = COALESCE(n2, 0),
                         n050 = COALESCE(n050, 0), n025 = COALESCE(n025, 0)
                   WHERE n200 IS NULL OR n20 IS NULL OR n2 IS NULL OR n050 IS NULL OR n025 IS NULL""")
    cr.execute("UPDATE navitta_visit SET sat = 0 WHERE sat IS NULL")
