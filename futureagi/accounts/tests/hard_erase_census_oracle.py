"""Independent completeness oracle: no production matcher or regex imports.

The contract is spelled out here deliberately. Inventory storage is explicit;
adding/changing a production regex cannot silently weaken this oracle.
"""

import json
from pathlib import Path

INVENTORY = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "services/hard_erase_credential_inventory.json"
    ).read_text()
)


def requires_protection(module, name, table, fields, exclusions):
    if any(row["module"] == module and row["model"] == name for row in INVENTORY):
        return True
    if any(
        word in name.casefold()
        for word in (
            "identity",
            "membership",
            "grant",
            "permission",
            "authorization",
            "credential",
        )
    ):
        return True
    for field in fields:
        # Name AND db_column encryption are unconditional even on homonym tables.
        if any(
            "encrypt" in str(value).casefold()
            for value in (
                field.name,
                getattr(field, "db_column", ""),
                *(cls.__name__ for cls in type(field).__mro__),
            )
        ):
            return True
        if field.name in exclusions.get(table.casefold(), ()):
            continue
        descriptions = (field.name, str(getattr(field, "verbose_name", "")))
        if any(
            word in text.casefold()
            for text in descriptions
            for word in (
                "token",
                "secret",
                "key",
                "password",
                "credential",
                "oauth",
                "signing",
                "bearer",
                "session",
                "identity",
                "membership",
                "grant",
                "authorization",
            )
        ):
            return True
    return False
