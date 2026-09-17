"""Bounded Observe-Users custom-attribute contract."""

UNSUPPORTED_USER_ATTRIBUTE_PREFIXES = (
    "raw.",
    "llm.input_messages",
    "llm.output_messages",
    "input.value",
    "output.value",
)


def unsupported_user_attribute_keys(keys) -> tuple[str, ...]:
    """Return reserved payload-heavy keys in deterministic order."""

    return tuple(
        sorted(
            {
                str(key)
                for key in keys
                if key and str(key).startswith(UNSUPPORTED_USER_ATTRIBUTE_PREFIXES)
            }
        )
    )
