#!/usr/bin/env python3
"""Render the self-hosting pages of docs.futureagi.com from this repository.

The configuration reference, the telemetry payloads and settings, and the
image tables on https://docs.futureagi.com/docs/self-hosting are generated
from data files that live next to the code they describe, where tests compare
them with it:

    deploy/env-reference.toml                               every environment variable
    futureagi/tfc/deployment_telemetry/wire_reference.toml  what telemetry sends
    deploy/images.toml, scripts/image_size_budget.json      the published images

    python3 scripts/docs_site.py render reference            # print one page or block
    python3 scripts/docs_site.py sync ../docs                # write them into a future-agi/docs checkout
    python3 scripts/docs_site.py sync ../docs --check        # exit 1 if that checkout is out of date

`sync` writes the configuration reference page whole and replaces what sits
between the `generated:begin NAME` and `generated:end NAME` markers of the
telemetry and images pages; a missing marker is an error. `--ref` (default
`main`) is the git ref that `repo:` links point at; a release sync passes its
tag. Output is deterministic, so a sync that changes nothing leaves the docs
checkout clean.

Standard library only (Python 3.11 or newer, for tomllib).
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_REFERENCE = ROOT / "deploy" / "env-reference.toml"
WIRE_REFERENCE = (
    ROOT / "futureagi" / "tfc" / "deployment_telemetry" / "wire_reference.toml"
)
IMAGES = ROOT / "deploy" / "images.toml"
SIZE_BUDGET = ROOT / "scripts" / "image_size_budget.json"

REPO_URL = "https://github.com/future-agi/future-agi"
SITE = "https://docs.futureagi.com"
REFERENCE_PATH = "/docs/self-hosting/configuration/reference"

# Docs-repo files the generated content goes to.
REFERENCE_PAGE = "src/pages/docs/self-hosting/configuration/reference.mdx"
TELEMETRY_PAGE = "src/pages/docs/self-hosting/configuration/telemetry.mdx"
IMAGES_PAGE = "src/pages/docs/self-hosting/images.mdx"

DEFAULT_COLUMNS = ["Key", "Default", "Setups", "What it does"]

LINK = re.compile(r"\]\(([^)\s]+)\)")
FENCE = re.compile(r"^```.*?^```[ \t]*$", re.MULTILINE | re.DOTALL)
CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.DOTALL)
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def slug(heading: str) -> str:
    """The anchor github-slugger (and so GitHub and the docs site) gives a heading."""
    text = CODE_SPAN.sub(lambda match: match.group(2), heading).lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def unique_slugs(headings: list[str]) -> list[str]:
    """Slugs as github-slugger numbers repeats: title, title-1, title-2."""
    seen: dict[str, int] = {}
    result = []
    for heading in headings:
        base = slug(heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append(base if count == 0 else f"{base}-{count}")
    return result


def markdown_headings(text: str) -> list[str]:
    """Headings of a markdown file, outside fenced code."""
    return [match.group(2) for match in HEADING.finditer(FENCE.sub("", text))]


def without_code(text: str) -> str:
    return CODE_SPAN.sub("", FENCE.sub("", text))


def text_problems(text: str, where: str) -> list[str]:
    """What would break MDX or the docs style guide in a piece of prose."""
    problems = []
    if "—" in text:
        problems.append(f"{where}: em dash (the docs style guide has none)")
    prose = without_code(text)
    if "<http" in prose:
        problems.append(f"{where}: <http...> autolink; write [url](url)")
    for char in "{}<":
        if char in prose:
            problems.append(f"{where}: {char!r} outside code breaks MDX")
    return problems


def repo_link_problem(target: str) -> str | None:
    """Why a repo:PATH[#anchor] link would be broken, or None."""
    path, _, anchor = target.removeprefix("repo:").partition("#")
    file = ROOT / path
    if not file.exists():
        return f"{target}: no such file in this repository"
    if anchor and file.suffix == ".md":
        headings = markdown_headings(file.read_text(encoding="utf-8"))
        if anchor not in unique_slugs(headings):
            return f"{target}: {path} has no heading with that anchor"
    return None


def data_problems(texts, anchors: set[str] | None = None) -> list[str]:
    """MDX, style and link problems of (where, text) pairs from a data file.

    anchors: the headings a #anchor link may name, or None to not check them."""
    problems = []
    for where, text in texts:
        text = str(text)
        problems += text_problems(text, where)
        for target in links(text):
            if target.startswith("#"):
                if anchors is not None and target[1:] not in anchors:
                    problems.append(f"{where}: {target} is not a heading of the page")
            elif target.startswith("repo:"):
                problem = repo_link_problem(target)
                if problem:
                    problems.append(f"{where}: {problem}")
            elif not target.startswith(("/docs/", "https://", "http://")):
                problems.append(
                    f"{where}: link {target!r}: use /docs/..., #anchor, repo:PATH or https://"
                )
    return problems


def links(text: str) -> list[str]:
    """Targets of the markdown links in text, code excluded."""
    return LINK.findall(without_code(text))


def expand_links(text: str, ref: str) -> str:
    """repo:PATH[#anchor] -> a GitHub link at ref."""
    return re.sub(r"\]\(repo:([^)\s]+)\)", rf"]({REPO_URL}/blob/{ref}/\1)", text)


def cell(text: str) -> str:
    return text.replace("|", "\\|")


def table(columns: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    lines += ["| " + " | ".join(cell(value) for value in row) + " |" for row in rows]
    return "\n".join(lines)


def paragraphs(*chunks: str | None) -> str:
    return "\n\n".join(chunk.strip("\n") for chunk in chunks if chunk and chunk.strip())


def marker_begin(name: str, source: str) -> str:
    return f"{{/* generated:begin {name}: from future-agi {source}; edit there, not here */}}"


def marker_end(name: str) -> str:
    return f"{{/* generated:end {name} */}}"


def framed(name: str, source: Path, body: str) -> str:
    # Blank lines around the body, so a table never runs into a marker.
    return "\n\n".join(
        (
            marker_begin(name, source.relative_to(ROOT).as_posix()),
            body,
            marker_end(name),
        )
    )


# --------------------------------------------------------------------------
# Configuration reference (deploy/env-reference.toml)
# --------------------------------------------------------------------------


def key_cell(row: dict) -> str:
    return row.get("label") or ", ".join(f"`{key}`" for key in row["keys"])


def default_cell(value) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{group}: {text}" for group, text in value.items())
    return value


def row_cells(row: dict, width: int) -> list[str]:
    if width == 2:
        return [key_cell(row), row["text"]]
    return [
        key_cell(row),
        default_cell(row["default"]),
        " ".join(row["setups"]),
        row["text"],
    ]


def is_public(row: dict) -> bool:
    return row.get("audience", "public") == "public"


def reference_body(data: dict, public_only: bool, spaced: bool = False) -> list[str]:
    """The ## and ### blocks of the parts, in page order."""
    out = []
    for part in data["part"]:
        out.append(paragraphs(f"## {part['heading']}", part.get("body")))
        for section in part.get("section", []):
            columns = section.get("columns", DEFAULT_COLUMNS)
            rows = [
                row_cells(row, len(columns))
                for row in section.get("row", [])
                if is_public(row) or not public_only
            ]
            body = table(columns, rows) if rows else None
            if body and spaced:
                body = body.replace(
                    "|" + "|".join("---" for _ in columns) + "|",
                    "| " + " | ".join("---" for _ in columns) + " |",
                    1,
                )
            out.append(
                paragraphs(
                    f"### {section['heading']}" if section.get("heading") else None,
                    section.get("body"),
                    body,
                    section.get("after"),
                )
            )
    return [chunk for chunk in out if chunk]


def reference_headings(data: dict) -> list[str]:
    """Every heading of the rendered page, in order."""
    headings = ["In this page", "How configuration works"]
    for part in data["part"]:
        headings.append(part["heading"])
        headings += [s["heading"] for s in part.get("section", []) if s.get("heading")]
    return headings + ["Production", "Dive deeper"]


def reference_texts(data: dict):
    """(where, text) for every piece of prose and every cell of the data."""
    for key in (
        "title",
        "description",
        "intro",
        "tldr",
        "how",
        "how_after",
        "production",
        "note",
    ):
        yield key, data.get(key, "")
    for setup in data.get("setup", []):
        for key in ("description", "files"):
            yield f"setup {setup.get('code')}.{key}", setup.get(key, "")
    for index, card in enumerate(data.get("card", [])):
        for key in ("title", "text"):
            yield f"card {index}.{key}", card.get(key, "")
    for part in data.get("part", []):
        yield f"part {part['heading']!r}", part["heading"] + "\n" + part.get("body", "")
        for section in part.get("section", []):
            where = f"section {section.get('heading') or part['heading']!r}"
            for key in ("heading", "body", "after"):
                yield f"{where}.{key}", section.get(key) or ""
            for row in section.get("row", []):
                row_where = f"{where} row {key_cell(row)}"
                yield row_where + ".label", row.get("label", "")
                yield row_where + ".default", default_cell(row.get("default", ""))
                yield row_where + ".text", row.get("text", "")


def render_reference(data: dict, ref: str = "main") -> str:
    """The whole configuration reference page, as MDX."""
    header = "\n".join(
        (
            "---",
            f'title: "{data["title"]}"',
            f'description: "{data["description"]}"',
            "---",
        )
    )
    comment = (
        f"{{/* Generated from deploy/env-reference.toml in future-agi/future-agi ({ref}) "
        "by scripts/docs_site.py. Edit that file, not this page. */}"
    )
    setups = table(
        ["Code", "Setup", "Files"],
        [[s["code"], s["description"], s["files"]] for s in data["setup"]],
    )
    cards = "\n".join(
        f'  <Card title="{card["title"]}" icon="{card["icon"]}" href="{card["href"]}">\n'
        f"    {card['text']}\n  </Card>"
        for card in data["card"]
    )
    page = paragraphs(
        header,
        comment,
        "## In this page",
        data["intro"],
        f"<TLDR>\n{data['tldr'].strip()}\n</TLDR>",
        "## How configuration works",
        data["how"],
        setups,
        data.get("how_after"),
        *reference_body(data, public_only=True),
        "## Production",
        data["production"],
        f"<Note>\n{data['note'].strip()}\n</Note>",
        "## Dive deeper",
        f"<CardGroup cols={{{len(data['card'])}}}>\n{cards}\n</CardGroup>",
    )
    return expand_links(page, ref) + "\n"


def render_reference_markdown(data: dict, ref: str = "main") -> str:
    """Parts 1 to 5 and the legacy keys as GitHub markdown, internal rows included.

    Not published: used to prove a conversion of the reference lost nothing,
    by diffing it with the markdown it came from."""
    return (
        expand_links("\n\n".join(reference_body(data, False, spaced=True)), ref) + "\n"
    )


# --------------------------------------------------------------------------
# Telemetry (wire_reference.toml, and the env-reference rows tagged telemetry)
# --------------------------------------------------------------------------


def registration_example(wire: dict) -> dict:
    example = {}
    for field in wire["registration"]["field"]:
        if field["name"] == "users":
            example["users"] = [{f["name"]: f["example"] for f in wire["user_field"]}]
        else:
            example[field["name"]] = field["example"]
    return example


def heartbeat_example(wire: dict) -> dict:
    example = {f["name"]: f["example"] for f in wire["heartbeat"]["field"]}
    example.update({c["name"]: c["example"] for c in wire["heartbeat"]["count"]})
    return example


def json_block(value: dict) -> str:
    return "```json\n" + json.dumps(value, indent=2) + "\n```"


def render_telemetry_registration(wire: dict) -> str:
    rows = [[f"`{f['name']}`", f["meaning"]] for f in wire["registration"]["field"]] + [
        [f"`users[].{f['name']}`", f["meaning"]] for f in wire["user_field"]
    ]
    return paragraphs(
        wire["registration"].get("intro"),
        json_block(registration_example(wire)),
        table(["Field", "Meaning"], rows),
        wire["registration"].get("after"),
    )


def render_telemetry_heartbeat(wire: dict) -> str:
    heartbeat = wire["heartbeat"]
    return paragraphs(
        heartbeat.get("intro"),
        json_block(heartbeat_example(wire)),
        table(
            ["Field", "Meaning"],
            [[f"`{f['name']}`", f["meaning"]] for f in heartbeat["field"]],
        ),
        heartbeat.get("counts_intro"),
        table(
            ["Field", "Counts"],
            [[f"`{c['name']}`", c["counts"]] for c in heartbeat["count"]],
        ),
        heartbeat.get("after"),
    )


def wire_texts(wire: dict):
    for section in ("registration", "heartbeat"):
        for key, value in wire.get(section, {}).items():
            if isinstance(value, str):
                yield f"{section}.{key}", value
    for kind in (
        "registration.field",
        "user_field",
        "heartbeat.field",
        "heartbeat.count",
    ):
        head, _, tail = kind.partition(".")
        entries = wire.get(head, {}).get(tail, []) if tail else wire.get(head, [])
        for entry in entries:
            for key in ("meaning", "counts"):
                if key in entry:
                    yield f"{kind} {entry['name']}.{key}", entry[key]


def telemetry_rows(env: dict) -> list[dict]:
    return [
        row
        for part in env["part"]
        for section in part.get("section", [])
        for row in section.get("row", [])
        if row.get("tag") == "telemetry"
    ]


def render_telemetry_settings(env: dict, ref: str = "main") -> str:
    rows = [row_cells(row, 4) for row in telemetry_rows(env) if is_public(row)]
    body = table(DEFAULT_COLUMNS, rows)
    # Relative to the telemetry page itself.
    body = body.replace("(/docs/self-hosting/configuration/telemetry#", "(#")
    return expand_links(body, ref)


# --------------------------------------------------------------------------
# Images (deploy/images.toml and scripts/image_size_budget.json)
# --------------------------------------------------------------------------


def budget_text(entry: dict) -> str:
    """How the images page states one budget entry: 549, 520 amd64, 480 arm64, 1525, reported only."""

    def mb(value) -> str:
        if isinstance(value, dict):
            return ", ".join(f"{value[arch]:g} {arch}" for arch in value)
        return f"{value:g}"

    text = mb(entry["budget_mb"])
    if not entry.get("enforce", True):
        text += ", reported only"
    if entry.get("measured_mb") is not None:
        text += f" (measured {mb(entry['measured_mb'])})"
    return text


def budget_entries(budget: dict) -> dict[tuple[str, str], dict]:
    return {(e["image"], e.get("tag_suffix", "")): e for e in budget["images"]}


def image_name(image: dict) -> str:
    suffix = image.get("tag_suffix", "")
    return f"`{image['name']}:*{suffix}`" if suffix else f"`{image['name']}`"


def render_images_at_a_glance(images: dict, budget: dict, ref: str = "main") -> str:
    entries = budget_entries(budget)
    rows = []
    for image in images["image"]:
        entry = entries.get((image["name"], image.get("tag_suffix", "")))
        rows.append(
            [
                image_name(image),
                image["runs"],
                image["setups"],
                image.get("ports", ""),
                image.get("user", ""),
                image.get("health_check", ""),
                ", ".join(image["architectures"]),
                budget_text(entry) if entry else image["size"],
            ]
        )
    install = budget["standalone_install"]
    downloads = [f"`{install['app'].removeprefix('futureagi/')}`"] + [
        f"`{name}`" for name in install["with"]
    ]
    sentence = (
        f"A fresh Standalone install downloads {', '.join(downloads[:-1])} and "
        f"{downloads[-1]}: {install['budget_mb']:g} MB at most (measured "
        f"{install['measured_mb']:g})."
    )
    columns = [
        "Image",
        "What it runs",
        "Setups",
        "Ports",
        "User",
        "Health check",
        "Architectures",
        "Size budget (MB)",
    ]
    return expand_links(
        paragraphs(images.get("glance_intro"), table(columns, rows), sentence), ref
    )


def image_texts(images: dict):
    yield "glance_intro", images.get("glance_intro", "")
    for image in images.get("image", []):
        for key, value in image.items():
            if isinstance(value, str):
                yield f"image {image_name(image)}.{key}", value
    for label in images.get("label", []):
        yield f"label {label['name']}", label.get("value", "")


def render_images_labels(images: dict, ref: str = "main") -> str:
    return expand_links(
        table(
            ["Label", "Value"],
            [[f"`{label['name']}`", label["value"]] for label in images["label"]],
        ),
        ref,
    )


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _telemetry_registration(ref: str) -> str:
    return render_telemetry_registration(load_toml(WIRE_REFERENCE))


def _telemetry_heartbeat(ref: str) -> str:
    return render_telemetry_heartbeat(load_toml(WIRE_REFERENCE))


def _telemetry_settings(ref: str) -> str:
    return render_telemetry_settings(load_toml(ENV_REFERENCE), ref)


def _images_at_a_glance(ref: str) -> str:
    budget = json.loads(SIZE_BUDGET.read_text(encoding="utf-8"))
    return render_images_at_a_glance(load_toml(IMAGES), budget, ref)


def _images_labels(ref: str) -> str:
    return render_images_labels(load_toml(IMAGES), ref)


# Block name -> (docs-repo page, data file, renderer).
BLOCKS = {
    "telemetry-registration": (TELEMETRY_PAGE, WIRE_REFERENCE, _telemetry_registration),
    "telemetry-heartbeat": (TELEMETRY_PAGE, WIRE_REFERENCE, _telemetry_heartbeat),
    "telemetry-settings": (TELEMETRY_PAGE, ENV_REFERENCE, _telemetry_settings),
    "images-at-a-glance": (IMAGES_PAGE, IMAGES, _images_at_a_glance),
    "images-labels": (IMAGES_PAGE, IMAGES, _images_labels),
}


def block(name: str, ref: str) -> str:
    """One block with its generated:begin and generated:end markers."""
    _, source, renderer = BLOCKS[name]
    return framed(name, source, renderer(ref))


def render(name: str, ref: str) -> str:
    if name == "reference":
        return render_reference(load_toml(ENV_REFERENCE), ref)
    return block(name, ref) + "\n"


def replace_block(text: str, name: str, content: str) -> str | None:
    begin = re.escape(f"{{/* generated:begin {name}:")
    end = re.escape(marker_end(name))
    pattern = re.compile(rf"^{begin}.*?^{end}[ \t]*$", re.MULTILINE | re.DOTALL)
    if not pattern.search(text):
        return None
    return pattern.sub(lambda _: content, text, count=1)


def sync(docs: Path, ref: str, check: bool) -> int:
    wanted: dict[str, str] = {
        REFERENCE_PAGE: render_reference(load_toml(ENV_REFERENCE), ref)
    }
    problems = []
    for name, (page, _, _) in BLOCKS.items():
        content = block(name, ref)
        path = docs / page
        text = wanted.get(page)
        if text is None:
            if not path.is_file():
                problems.append(f"{page}: missing (needs the {name} markers)")
                continue
            text = path.read_text(encoding="utf-8")
        replaced = replace_block(text, name, content)
        if replaced is None:
            problems.append(
                f"{page}: no {marker_begin(name, '...')} ... {marker_end(name)} block"
            )
            continue
        wanted[page] = replaced
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 2
    changed = []
    for page, text in wanted.items():
        path = docs / page
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            changed.append(page)
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
    for page in changed:
        print(f"{'out of date' if check else 'wrote'}: {page}")
    return 1 if check and changed else 0


def tracked_files() -> list[Path]:
    """Files git tracks, or every file when git is unavailable."""
    try:
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            check=True,
            capture_output=True,
        ).stdout.decode()
        return [ROOT / name for name in listed.split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        skipped = {".git", "node_modules", ".venv", "venv", "__pycache__"}
        return [
            path
            for path in ROOT.rglob("*")
            if path.is_file() and not skipped & set(path.relative_to(ROOT).parts)
        ]


@functools.lru_cache(maxsize=8)
def links_into(page_url: str) -> list[tuple[str, str]]:
    """(file, anchor) for every page_url#anchor written in a tracked file."""
    pattern = re.compile(re.escape(page_url) + r"#([\w-]+)")
    needle = page_url.encode()
    found = []
    for path in tracked_files():
        try:
            if path.stat().st_size > 2_000_000:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if needle not in raw:
            continue
        text = raw.decode("utf-8", errors="ignore")
        found += [
            (path.relative_to(ROOT).as_posix(), anchor)
            for anchor in pattern.findall(text)
        ]
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    render_parser = commands.add_parser("render", help="print one page or block")
    render_parser.add_argument(
        "name",
        choices=["reference", *BLOCKS],
    )
    render_parser.add_argument("--ref", default="main")
    sync_parser = commands.add_parser(
        "sync", help="write into a future-agi/docs checkout"
    )
    sync_parser.add_argument("docs", type=Path, help="the future-agi/docs checkout")
    sync_parser.add_argument("--ref", default="main")
    sync_parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 1 if anything would change",
    )
    args = parser.parse_args()
    if args.command == "render":
        sys.stdout.write(render(args.name, args.ref))
        return 0
    return sync(args.docs, args.ref, args.check)


if __name__ == "__main__":
    sys.exit(main())
