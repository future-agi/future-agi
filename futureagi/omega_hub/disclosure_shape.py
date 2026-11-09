"""Pure disclosure contract shared by review validation and the official seed loader."""

PARTS = ("network", "files", "shell", "background", "credentials")
FIELDS = ("summary", *PARTS)
SUMMARY_LENGTH = (20, 200)
PART_LENGTH = (4, 300)
DETAIL_LENGTH = (4, 1200)
LENGTHS = {
    "summary": SUMMARY_LENGTH,
    **dict.fromkeys(PARTS, PART_LENGTH),
    **dict.fromkeys(("network", "files", "credentials"), DETAIL_LENGTH),
}


def shape_errors(disclosure) -> list[str]:
    if not isinstance(disclosure, dict):
        return ["the disclosure must be an object"]
    errors = []
    extra = set(disclosure) - set(FIELDS)
    if extra:
        errors.append(f"unknown disclosure fields: {', '.join(sorted(extra))}")
    for field in FIELDS:
        low, high = LENGTHS[field]
        value = disclosure.get(field)
        if not isinstance(value, str) or not low <= len(value.strip()) <= high:
            errors.append(f"{field} must be {low}-{high} characters")
    return errors
