"""NLTK data lookup, with a download fallback that images turn off.

The backend images bake the NLTK data into NLTK_DATA at build time
(bin/install_nltk_data.py) and set ``NLTK_DOWNLOAD_MISSING=false``, so a
server never reaches raw.githubusercontent.com at run time (air-gapped
installs, and no network call on a request path). A checkout used for local
development or CI has no baked data; there ``NLTK_DOWNLOAD_MISSING`` is unset
and a missing resource is downloaded on first use, into NLTK's default
location, as ``nltk.download()`` always did.

Callers name the resources they are about to use, right before using them:

    ensure_nltk_data("stopwords")
    stopwords.words("english")

Import this module freely: nltk itself is imported only inside the function
(importing nltk pulls scipy and scikit-learn; see
tfc/tests/test_startup_import_graph.py).
"""

from __future__ import annotations

import os
import threading

# name -> (path that nltk.data.find() resolves on nltk>=3.9, downloader id).
# The trailing slash matters: it lets find() also match the archive layout
# nltk.download() leaves behind for some packages (wordnet stays a zip,
# corpora/wordnet.zip/wordnet/), next to the extracted tree the images bake.
RESOURCES: dict[str, tuple[str, str]] = {
    # stopwords.words("english")
    "stopwords": ("corpora/stopwords/", "stopwords"),
    # nltk.word_tokenize / sent_tokenize (English)
    "punkt_tab": ("tokenizers/punkt_tab/english/", "punkt_tab"),
    # nltk.pos_tag
    "averaged_perceptron_tagger_eng": (
        "taggers/averaged_perceptron_tagger_eng/",
        "averaged_perceptron_tagger_eng",
    ),
    # WordNetLemmatizer, METEOR
    "wordnet": ("corpora/wordnet/", "wordnet"),
}

DOWNLOAD_ENV = "NLTK_DOWNLOAD_MISSING"
_FALSE = {"0", "false", "no", "off"}

_lock = threading.Lock()
_present: set[str] = set()


def download_allowed() -> bool:
    """True unless NLTK_DOWNLOAD_MISSING is set to a false value."""
    return os.environ.get(DOWNLOAD_ENV, "true").strip().lower() not in _FALSE


def ensure_nltk_data(*names: str) -> None:
    """Make sure each named resource (a key of RESOURCES) can be loaded.

    Raises LookupError when a resource is missing and downloading is off
    (every backend image), or when the download itself fails.
    """
    missing = [name for name in names if name not in _present]
    if not missing:
        return

    import nltk

    with _lock:
        for name in missing:
            if name in _present:
                continue
            path, package = RESOURCES[name]
            try:
                nltk.data.find(path)
            except LookupError:
                if not download_allowed():
                    raise LookupError(
                        f"NLTK resource {package!r} ({path}) is not installed. "
                        "Backend images install it at build time with "
                        "bin/install_nltk_data.py; set "
                        f"{DOWNLOAD_ENV}=true to download it at run time "
                        "instead."
                    ) from None
                if not nltk.download(package, quiet=True, raise_on_error=True):
                    raise LookupError(f"NLTK could not download {package!r}") from None
                nltk.data.find(path)
            _present.add(name)
