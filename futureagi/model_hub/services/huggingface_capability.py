"""Installed dependency shared by Hugging Face admission and capability reads."""

from tfc.utils import lazy_extras

HUGGINGFACE_IMPORT_UNAVAILABLE = (
    "This install lacks the datasets package needed for Hugging Face import."
)


def huggingface_import_available() -> bool:
    """Check the import, including dependencies, rather than only a package spec."""
    try:
        lazy_extras.load_extra("datasets", "ml")
    except ImportError:
        return False
    return True
