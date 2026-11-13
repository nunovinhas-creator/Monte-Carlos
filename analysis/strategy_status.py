"""Conservative classification: current historical reports cannot validate a strategy."""


def scientific_status(summary, integrity_ok=True):
    if not integrity_ok:
        return 'NO-GO'
    if not summary['n']:
        return 'EXPERIMENTAL'
    interval = summary.get('roi_ci95')
    if interval is not None and interval[1] < 0:
        return 'NO-GO'
    return 'INCONCLUSIVO'
