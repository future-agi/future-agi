"""The only caller-controlled headers generated tools may map."""

ALLOWED_REQUEST_HEADERS = frozenset({"Idempotency-Key", "X-Context-Resolution"})


def validate_header_mapping(names: object) -> list[str]:
    if (
        not isinstance(names, list)
        or any(
            not isinstance(name, str) or name not in ALLOWED_REQUEST_HEADERS
            for name in names
        )
        or len(set(names)) != len(names)
    ):
        raise ValueError("Only Idempotency-Key and X-Context-Resolution may be mapped.")
    return names
