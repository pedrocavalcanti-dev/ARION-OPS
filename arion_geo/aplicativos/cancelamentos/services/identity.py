def normalize_ticket_number(value):
    """Normalize presentation boundaries without changing operational identity."""
    # Never int(), remove zeroes, punctuation or internal whitespace.
    return str(value or "").strip().removeprefix("#").strip()
