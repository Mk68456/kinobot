import re


def episode_order(row):
    """Recognize episode labels, without treating a year in a title as an episode."""
    name = str(row[1] or '').strip()
    match = re.match(r'^(?:серия|эпизод|episode|ep)[\s._:#-]*(\d+)(?!\d)', name, re.IGNORECASE)
    if not match:
        match = re.match(r'^S\d+[ ._-]*E(\d+)(?!\d)', name, re.IGNORECASE)
    if not match:
        match = re.match(r'^(\d+)(?:\s*[-.:)]|\s+(?:серия|эпизод)\b)', name, re.IGNORECASE)
    return (0, int(match[1]), row[0]) if match else (1, 0, row[0])
