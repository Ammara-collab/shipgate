def discount(price, pct):
    """Return price after pct% discount, capped at 50%, rounded to 2dp."""
    pct = min(pct, 50)
    return round(price * (1 - pct / 100), 2)
