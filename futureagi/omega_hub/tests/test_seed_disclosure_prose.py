"""The Official seed disclosures read as plain sentences within the data's own limits.

No database needed. The Hub panel shows the first 300 characters of a part and a
Show all button for the rest (DisclosureText.jsx), so a part says where it sends or
writes before that cut.
"""

import re

from omega_hub import official_seed
from omega_hub.disclosure_shape import LENGTHS

PREVIEW_LENGTH = 300
OPENING = re.compile(r"^[(\"'“‘]+")
CLOSING = re.compile(r"[)\"',;:!?.…”’]+$")
# A host or a path: a URL, an absolute or relative path, an IPv4 address, a host or
# file name (api.openai.com, page.html, oauth2.googleapis.com/token), a dot folder
# (.omega), a relative path to a file (docs/*.txt), or a variable that holds a path or
# URL (OMEGA_TRACE_FILE, FI_BASE_URL).
PLACE = re.compile(
    r"""
      [a-z][a-z0-9+.-]*://\S+
    | (?:~|\.{1,2})?/[\w.*-]+(?:/[\w.*-]+)*/?
    | \d{1,3}(?:\.\d{1,3}){3}(?::\d+)?
    | [\w*-]+(?:\.[\w*-]+)*\.[A-Za-z][A-Za-z0-9]+(?:/[\w.*-]+)*/?
    | (?:[\w.-]+/)+[\w*.-]*\.[A-Za-z0-9]+
    | \.[A-Za-z][\w-]*
    | [A-Z][A-Z0-9_]*_(?:FILE|FILES|LOG|PATH|HOME|DIR|URL)
    """,
    re.X,
)
# Where a "/" may stand: inside a URL or a path (/tmp, ~/x, docs/*.txt,
# oauth2.googleapis.com/token), between two numbers (3/4), or between two exact names
# that both show in the code face (OPENAI_API_KEY/ANTHROPIC_API_KEY).
SLASH_PATH = re.compile(
    r"""
      (?:~|\.{1,2})?/[\w.*-]\S*
    | [a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[a-z0-9._*-]*)+
    | (?:[\w.-]+/)+[\w*.-]*\.[A-Za-z0-9]+
    """,
    re.X,
)
CODE_NAME = re.compile(r"[A-Za-z0-9][_.:][A-Za-z0-9]")
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def words(text):
    for word in text.split():
        core = CLOSING.sub("", OPENING.sub("", word))
        if core:
            yield core


def places(text):
    return {word for word in words(text) if PLACE.fullmatch(word)}


def preview(text):
    """What the panel shows before Show all: 300 characters, cut to a whole word."""
    if len(text) <= PREVIEW_LENGTH:
        return text
    return re.sub(r"\s+\S*$", "", text[:PREVIEW_LENGTH])


def slash_shorthand(text):
    found = []
    for word in words(text):
        if "/" not in word or "://" in word or SLASH_PATH.fullmatch(word):
            continue
        pieces = word.split("/")
        if not all(
            (NUMBER.fullmatch(left) and NUMBER.fullmatch(right))
            or (CODE_NAME.search(left) and CODE_NAME.search(right))
            for left, right in zip(pieces, pieces[1:], strict=False)
        ):
            found.append(word)
    return found


def seed_parts():
    for entry_id, row in official_seed.load()["entries"].items():
        for field, text in row["disclosure"].items():
            yield f"{entry_id}.{field}", field, text


def test_each_part_fits_its_limit_and_names_where_it_sends_or_writes_before_show_all():
    problems = []
    for where, field, text in seed_parts():
        low, high = LENGTHS[field]
        if not low <= len(text.strip()) <= high:
            problems.append(
                f"{where}: {len(text.strip())} characters, limit {low}-{high}"
            )
        if field not in ("network", "files", "credentials"):
            continue
        named, shown = places(text), places(preview(text))
        if named and not shown:
            problems.append(f"{where}: names no host or path before Show all")
        # A files part is about what the entry writes: every path it names shows first.
        if field == "files" and named - shown:
            problems.append(f"{where}: {sorted(named - shown)} only after Show all")
    assert not problems, "\n".join(problems)


def test_no_part_joins_plain_words_with_a_slash():
    problems = [
        f"{where}: {found}"
        for where, _, text in seed_parts()
        if (found := slash_shorthand(text))
    ]
    assert not problems, "\n".join(problems)


def test_every_seed_disclosure_passes_the_import_code_check():
    for entry_id, row in official_seed.load()["entries"].items():
        confirmed = official_seed.disclosure_for(entry_id, row["codeSha256"])
        assert confirmed == row["disclosure"], entry_id
