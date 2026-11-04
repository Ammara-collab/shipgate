def discount(price, pct):
    """Return price after pct% discount, rounded to 2dp."""
    return round(price * (1 - pct / 100), 2)
