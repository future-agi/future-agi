"""bin/update's release side and repository wiring, checked without a network.

The signed manifest is produced by .github/workflows/release-update-manifest.yml
(called by the update-manifest job of release-images.yml) and trusted by
deploy/update/settings.py and trust.py. These tests pin the two sides to each
other: triggers, permissions, action pins, the signer identity and issuer,
asset names, the components and their repositories, the chart reference, the
cosign release, the schema, the README, .gitignore, the upgrade floor, and the
files that must never carry updater settings.

PyYAML is not a dependency, so the workflows are read with a small YAML-subset
parser that fails loudly on anything it does not understand. The bash of the
steps that decide what gets resolved, generated, signed and attached runs
against fake gh, cosign, docker and oras binaries (no network, no Docker).
"""

from __future__ import annotations

import ast
import copy
import datetime
import functools
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update import trust  # noqa: E402
from deploy.update.compatibility import (  # noqa: E402
    CompatibilityError, evaluate_automatic_apply, validate_release_proof,
)
from deploy.update.contracts import ReasonCode  # noqa: E402
from deploy.update.manifest import (  # noqa: E402
    BUNDLE_ASSET, CHANNELS, COMPONENT_REPOSITORIES, COMPONENT_TAG_SUFFIX, MANIFEST_ASSET, PRODUCT,
    SCHEMA_VERSION, ManifestError, component_tag, is_prerelease, is_version, parse_image_ref,
    parse_version, read_deployment_file_list, read_upgrade_floor, validate_manifest,
)
from deploy.update.receipt import STATE_DIR  # noqa: E402
from deploy.update.settings import (  # noqa: E402
    CONFIG_FILE, DEFAULT_COSIGN_IMAGE, DEFAULT_MANIFEST_URL, DEFAULT_TRUSTED_IDENTITY,
    DEFAULT_TRUSTED_ISSUER, DEFAULTS, load_settings, read_config_file,
)
from deploy.update.trust import Verifier, fetch_manifest  # noqa: E402

WORKFLOWS = ROOT / ".github" / "workflows"
UPDATE_WORKFLOW = WORKFLOWS / "release-update-manifest.yml"
RELEASE_IMAGES = WORKFLOWS / "release-images.yml"
HELM_RELEASE = WORKFLOWS / "helm-release.yml"
BUILD_MULTIARCH = WORKFLOWS / "build-image-multiarch.yml"
HELM_TOOLS = ROOT / ".github" / "actions" / "setup-helm-tools" / "action.yml"
UPDATE_DIR = ROOT / "deploy" / "update"
README = UPDATE_DIR / "README.md"
SCHEMA = UPDATE_DIR / "release-manifest.schema.json"
MAIN = UPDATE_DIR / "__main__.py"
APPLY = UPDATE_DIR / "apply.py"
BIN_UPDATE = ROOT / "bin" / "update"
GENERATOR = ROOT / "scripts" / "generate_update_manifest.py"

CANONICAL_REPOSITORY = "future-agi/future-agi"
HELM_CHART = "oci://ghcr.io/future-agi/charts/futureagi"
INPUT_VERSION = "${{ inputs.version }}"
# owner/repo[/path]@<40 hex>: a full commit SHA, never a tag or a branch.
PINNED_ACTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._/-]+)?@[0-9a-f]{40}$")
# Which release-images.yml job builds each manifest component's image.
COMPONENT_JOBS = {
    "standalone": "standalone",
    "backend": "backend",
    "frontend": "build",
    "fi_collector": "build",
    "agentcc_gateway": "build",
    "serving": "build",
    "serving_gpu": "serving-gpu",
    "code_executor": "code-executor",
    "simulation_runner": "simulation-runner",
}
# Triggers that would let something other than a deliberate call or a dispatch
# from the release tag sign a manifest.
FORBIDDEN_TRIGGERS = ("push", "release", "pull_request", "pull_request_target", "schedule", "workflow_run",
                      "repository_dispatch", "issue_comment", "create", "registry_package", "merge_group")


# ===================================================== a YAML-subset parser --

class YamlSubsetError(ValueError):
    """The text uses YAML this parser does not understand (or is not YAML)."""


_BLOCK_SCALAR_RE = re.compile(r"^([|>])([+-]?)([1-9]?)([+-]?)$")
_ANCHOR_RE = re.compile(r"^&([A-Za-z0-9_.-]+)(?: +|$)")


def _strip_comment(text: str) -> str:
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if quote == '"' and ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"" and (i == 0 or text[i - 1] in " [{,:"):
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] == " "):
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return json.loads(text)
        except ValueError as exc:
            raise YamlSubsetError(f"unsupported double-quoted scalar {text!r}") from exc
    if text[:1] in ("'", '"'):
        raise YamlSubsetError(f"unterminated quoted scalar {text!r}")
    return text


def _split_key(text: str) -> Optional[Tuple[str, str]]:
    """`key: value` -> (key, value); None when the text is not a mapping entry."""

    if text[:1] in ("'", '"'):
        end = text.find(text[0], 1)
        if end > 0 and text[end + 1:end + 2] == ":" and text[end + 2:end + 3] in ("", " "):
            return _unquote(text[:end + 1]), text[end + 2:].strip()
        return None
    if text[:1] in ("[", "{"):
        return None
    for i, ch in enumerate(text):
        if ch == ":" and (i + 1 == len(text) or text[i + 1] == " "):
            key = text[:i].strip()
            if not key:
                return None
            return key, text[i + 1:].strip()
    return None


def _split_top(text: str) -> List[str]:
    parts: List[str] = []
    depth, quote, start, i = 0, None, 0, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if quote == '"' and ch == "\\":
                i += 2
                continue
            if ch == quote:
                if quote == "'" and text[i + 1:i + 2] == "'":
                    i += 2
                    continue
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def _flow_balanced(text: str) -> bool:
    depth, quote, i = 0, None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if quote == '"' and ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth < 0:
                raise YamlSubsetError(f"unbalanced flow collection {text!r}")
        i += 1
    return depth == 0 and quote is None


def _flow_scalar(text: str) -> Any:
    text = text.strip()
    if text[:1] in ("[", "{"):
        return _parse_flow(text)
    return _unquote(text)


def _parse_flow(text: str) -> Any:
    text = text.strip()
    if text.startswith("["):
        if not text.endswith("]"):
            raise YamlSubsetError(f"trailing text after a flow sequence: {text!r}")
        items = [part.strip() for part in _split_top(text[1:-1])]
        if items and items[-1] == "":
            items.pop()
        if any(item == "" for item in items):
            raise YamlSubsetError(f"empty item in flow sequence {text!r}")
        return [_flow_scalar(item) for item in items]
    if text.startswith("{"):
        if not text.endswith("}"):
            raise YamlSubsetError(f"trailing text after a flow mapping: {text!r}")
        result: Dict[str, Any] = {}
        for part in _split_top(text[1:-1]):
            part = part.strip()
            if not part:
                continue
            split = _split_key(part)
            if split is None:
                raise YamlSubsetError(f"flow mapping entry without a key: {part!r}")
            key, value = split
            if key in result:
                raise YamlSubsetError(f"duplicate key {key!r} in {text!r}")
            result[key] = _flow_scalar(value) if value else None
        return result
    raise YamlSubsetError(f"not a flow collection: {text!r}")


class MiniYaml:
    """Block mappings and sequences, plain/quoted/block scalars, single and
    multi-line flow collections, anchors and aliases. Every scalar stays a
    string (GitHub reads `on:` as the key "on", not as a boolean)."""

    def __init__(self, text: str) -> None:
        self.raw = text.splitlines()
        self.n = len(self.raw)
        self.indent: List[int] = []
        self.content: List[str] = []
        for number, line in enumerate(self.raw, 1):
            stripped = line.lstrip(" ")
            if stripped.startswith("\t"):
                raise YamlSubsetError(f"line {number}: tab indentation")
            self.indent.append(len(line) - len(stripped))
            self.content.append("" if stripped.startswith("#") else _strip_comment(stripped))
        self.anchors: Dict[str, Any] = {}

    def parse(self) -> Any:
        value, i = self._node(0, 0)
        i = self._skip(i)
        if i < self.n:
            raise YamlSubsetError(f"line {i + 1}: unexpected {self.raw[i]!r}")
        return value

    def _error(self, i: int, message: str) -> YamlSubsetError:
        return YamlSubsetError(f"line {i + 1}: {message}: {self.raw[i]!r}")

    def _skip(self, i: int) -> int:
        while i < self.n and not self.content[i]:
            i += 1
        return i

    def _is_item(self, i: int) -> bool:
        return self.content[i] == "-" or self.content[i].startswith("- ")

    def _node(self, i: int, min_indent: int) -> Tuple[Any, int]:
        i = self._skip(i)
        if i >= self.n or self.indent[i] < min_indent:
            return None, i
        if self._is_item(i):
            return self._seq(i, self.indent[i])
        return self._map(i, self.indent[i])

    def _map(self, i: int, indent: int) -> Tuple[Dict[str, Any], int]:
        result: Dict[str, Any] = {}
        while True:
            i = self._skip(i)
            if i >= self.n or self.indent[i] < indent:
                return result, i
            if self.indent[i] > indent:
                raise self._error(i, "unexpected indentation")
            if self._is_item(i):
                raise self._error(i, "sequence item where a mapping key was expected")
            split = _split_key(self.content[i])
            if split is None:
                raise self._error(i, "expected `key: value`")
            key, rest = split
            if key in result:
                raise self._error(i, f"duplicate key {key!r}")
            result[key], i = self._value(rest, i, indent)

    def _seq(self, i: int, indent: int) -> Tuple[List[Any], int]:
        items: List[Any] = []
        while True:
            i = self._skip(i)
            if i >= self.n or self.indent[i] < indent:
                return items, i
            if self.indent[i] == indent and not self._is_item(i):
                return items, i  # a key of the mapping that holds this same-indent sequence
            if self.indent[i] > indent or not self._is_item(i):
                raise self._error(i, "unexpected line in a sequence")
            content = self.content[i]
            rest = content[1:].lstrip(" ")
            if not rest:
                value, i = self._node(i + 1, indent + 1)
                items.append(value)
                continue
            column = self.indent[i] + len(content) - len(rest)
            self.indent[i], self.content[i] = column, rest
            if rest[:1] in ("[", "{", "|", ">", "*", "&", "'", '"') or _split_key(rest) is None:
                value, i = self._value(rest, i, indent)
            else:
                value, i = self._map(i, column)
            items.append(value)

    def _value(self, rest: str, i: int, indent: int) -> Tuple[Any, int]:
        anchor = None
        match = _ANCHOR_RE.match(rest)
        if match:
            anchor, rest = match.group(1), rest[match.end():]
        if rest.startswith("*"):
            name = rest[1:].strip()
            if name not in self.anchors:
                raise self._error(i, f"unknown alias *{name}")
            value, nxt = self.anchors[name], i + 1
        elif rest == "":
            j = self._skip(i + 1)
            if j < self.n and self.indent[j] == indent and self._is_item(j):
                value, nxt = self._seq(j, indent)
            else:
                value, nxt = self._node(i + 1, indent + 1)
        elif _BLOCK_SCALAR_RE.match(rest):
            value, nxt = self._block(rest, i, indent)
        elif rest[0] in "[{":
            value, nxt = self._flow(rest, i)
        else:
            value, nxt = self._plain(rest, i, indent)
        if anchor:
            self.anchors[anchor] = value
        return value, nxt

    def _flow(self, rest: str, i: int) -> Tuple[Any, int]:
        text, j = rest, i + 1
        while not _flow_balanced(text):
            if j >= self.n:
                raise self._error(i, "unterminated flow collection")
            text += " " + self.content[j]
            j += 1
        return _parse_flow(text), j

    def _plain(self, rest: str, i: int, indent: int) -> Tuple[str, int]:
        parts, j = [rest], i + 1
        while (j < self.n and self.raw[j].strip() and not self.raw[j].lstrip().startswith("#")
               and self.indent[j] > indent):
            if _split_key(self.content[j]) is not None or self._is_item(j):
                raise self._error(j, "unexpected indentation (a mapping or item inside a plain scalar)")
            parts.append(self.content[j])
            j += 1
        text = " ".join(parts)
        if text[:1] in ("'", '"') and len(parts) > 1:
            raise self._error(i, "multi-line quoted scalars are not supported")
        return _unquote(text), j

    def _block(self, indicator: str, i: int, indent: int) -> Tuple[str, int]:
        match = _BLOCK_SCALAR_RE.match(indicator)
        style, chomp = match.group(1), match.group(2) or match.group(4)
        j, lines = i + 1, []
        while j < self.n and (not self.raw[j].strip() or self.indent[j] > indent):
            lines.append(self.raw[j])
            j += 1
        trailing = 0
        while lines and not lines[-1].strip():
            lines.pop()
            trailing += 1
        if not lines:
            return "", j
        base = len(lines[0]) - len(lines[0].lstrip(" "))
        body = []
        for line in lines:
            if not line.strip():
                body.append("")
            elif len(line) - len(line.lstrip(" ")) < base:
                raise self._error(i, "a block scalar line is less indented than its first line")
            else:
                body.append(line[base:])
        if style == "|":
            text = "\n".join(body)
        else:
            runs: List[str] = []
            current: List[str] = []
            for line in body:
                if line:
                    current.append(line)
                else:
                    runs.append(" ".join(current))
                    current = []
            runs.append(" ".join(current))
            text = "\n".join(runs)
        if chomp == "-":
            return text, j
        if chomp == "+":
            return text + "\n" * (trailing + 1), j
        return text + "\n", j


def parse_yaml(text: str) -> Any:
    return MiniYaml(text).parse()


@functools.lru_cache(maxsize=None)
def _parsed(path: str) -> Any:
    return parse_yaml(Path(path).read_text(encoding="utf-8"))


def load(path: Path) -> Any:
    # A deep copy: MiniYaml results are plain dicts/lists/strings.
    return json.loads(json.dumps(_parsed(str(path))))


def walk(node: Any) -> Iterator[Tuple[str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def needs_of(job: Dict[str, Any]) -> List[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def one_step(job: Dict[str, Any], predicate) -> Dict[str, Any]:
    steps = [step for step in job.get("steps") or [] if predicate(step)]
    if len(steps) != 1:
        raise AssertionError(f"expected exactly one matching step, found {len(steps)}")
    return steps[0]


def runs_containing(text: str):
    return lambda step: text in (step.get("run") or "")


_EXPRESSION_RE = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def expand(value: str, context: Dict[str, str]) -> str:
    """Replace ${{ expr }} with context values; an expression without one fails."""

    def substitute(match: "re.Match[str]") -> str:
        if match.group(1) not in context:
            raise KeyError(f"no test value for ${{{{ {match.group(1)} }}}} in {value!r}")
        return context[match.group(1)]

    return _EXPRESSION_RE.sub(substitute, value)


def step_env(document: Dict[str, Any], job: Dict[str, Any], step: Dict[str, Any],
             context: Dict[str, str]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    for layer in (document.get("env") or {}, job.get("env") or {}, step.get("env") or {}):
        for key, value in layer.items():
            env[key] = expand(str(value), context)
    return env


def bash_major() -> int:
    bash = shutil.which("bash")
    if not bash:
        return 0
    result = subprocess.run([bash, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True)
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def run_step(step: Dict[str, Any], env: Dict[str, str], cwd: Path, scratch: Path) -> subprocess.CompletedProcess:
    """Run a step's `run:` the way GitHub runs it (shell: bash or the default)."""

    script = scratch / f"step-{len(list(scratch.glob('step-*.sh')))}.sh"
    script.write_text(step["run"], encoding="utf-8")
    bash = shutil.which("bash")
    if step.get("shell") == "bash":
        command = [bash, "--noprofile", "--norc", "-eo", "pipefail", str(script)]
    else:
        command = [bash, "-e", str(script)]
    return subprocess.run(command, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120)


def write_fake(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def calls(log: Path) -> List[List[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def git(*args: str) -> Optional[subprocess.CompletedProcess]:
    if not shutil.which("git"):
        return None
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def next_version_after_floor() -> str:
    major, minor, _patch, _pre, _num = parse_version(read_upgrade_floor(ROOT))
    return f"v{major}.{minor + 1}.0"


def fake_digest(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


# Faithful CLI boundary for the extracted image-publisher step. No Docker daemon
# or registry is contacted. Buildx dry-run prints manifest bytes plus ONE newline.
FAKE_IMAGE_PUBLISHER = '''
import json, os, re, sys
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps(['docker'] + args) + '\\n')
assert args[:2] == ['buildx', 'imagetools'], args
if args[2:4] == ['create', '--dry-run']:
    assert all(re.fullmatch(r'.+@sha256:[a-f0-9]{64}', ref) for ref in args[4:]), args
    if os.environ.get('FAKE_DRY_RUN_FAIL') == '1':
        sys.exit(1)
    sys.stdout.write(os.environ['FAKE_CANDIDATE'] + '\\n')
elif args[2] == 'inspect':
    assert args[4:] == ['--format', '{{json .Manifest}}'], args
    state = os.environ.get('FAKE_EXISTING', 'missing')
    if state == 'missing':
        print('ERROR: ' + args[3] + ': not found', file=sys.stderr)
        sys.exit(1)
    if state == 'error':
        print(os.environ['FAKE_INSPECT_ERROR'], file=sys.stderr)
        sys.exit(1)
    print(json.dumps({'digest': state}))
elif args[2] == 'create':
    assert '--dry-run' not in args and '-t' in args, args
else:
    raise AssertionError(args)
'''


FAKE_GH = '''
import fnmatch, json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["gh"] + args) + "\\n")
def values(name):
    return [args[i + 1] for i in range(len(args) - 1) if args[i] == name]
release = Path(os.environ["FAKE_RELEASE"])
if len(args) < 3 or args[0] != "release" or args[2] != os.environ["EXPECT_VERSION"]:
    sys.exit(90)
if values("--repo") != [os.environ["EXPECT_REPOSITORY"]]:
    sys.exit(91)
if args[1] == "view":
    if values("--json") != ["assets"]:
        sys.exit(92)
    for asset in sorted(p.name for p in release.iterdir()):
        print(asset)
elif args[1] == "download":
    patterns = values("--pattern")
    found = [p for p in sorted(release.iterdir()) if any(fnmatch.fnmatchcase(p.name, x) for x in patterns)]
    if not found:
        print("release " + args[2] + ": no assets match " + " ".join(patterns), file=sys.stderr)
        sys.exit(1)
    if values("--output"):
        shutil.copyfile(str(found[0]), values("--output")[0])
    else:
        for path in found:
            shutil.copyfile(str(path), str(Path(values("--dir")[0]) / path.name))
elif args[1] == "upload":
    if "--clobber" in args:
        sys.exit(93)
    files, skip = [], False
    for arg in args[3:]:
        if skip:
            skip = False
        elif arg == "--repo":
            skip = True
        else:
            files.append(arg)
    for name in files:
        target = release / Path(name).name
        if target.exists():
            print("asset " + target.name + " already exists", file=sys.stderr)
            sys.exit(1)
        shutil.copyfile(name, str(target))
else:
    sys.exit(94)
'''

FAKE_COSIGN = '''
import hashlib, json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["cosign"] + args) + "\\n")
def value(name):
    return args[args.index(name) + 1]
digest = hashlib.sha256(Path(args[-1]).read_bytes()).hexdigest()
if args[0] == "sign-blob":
    if "--yes" not in args or "--key" in args:
        sys.exit(80)
    Path(value("--bundle")).write_text(json.dumps(
        {"identity": os.environ["FAKE_SIGNER"], "issuer": os.environ["FAKE_ISSUER"], "sha256": digest}))
elif args[0] == "verify-blob":
    try:
        bundle = json.loads(Path(value("--bundle")).read_text())
    except ValueError:
        sys.exit(1)
    ok = (bundle.get("sha256") == digest and bundle.get("identity") == value("--certificate-identity")
          and bundle.get("issuer") == value("--certificate-oidc-issuer"))
    sys.exit(0 if ok else 1)
else:
    sys.exit(81)
'''

FAKE_DOCKER = '''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["docker"] + args) + "\\n")
if args[:3] != ["buildx", "imagetools", "inspect"] or args[4:] != ["--format", "{{json .Manifest}}"]:
    sys.exit(70)
digests = json.loads(os.environ["FAKE_DIGESTS"])
if args[3] not in digests:
    print("ERROR: " + args[3] + ": not found", file=sys.stderr)
    sys.exit(1)
print(json.dumps({"mediaType": "application/vnd.oci.image.index.v1+json", "digest": digests[args[3]], "size": 1}))
'''

FAKE_ORAS = '''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["oras"] + args) + "\\n")
if len(args) != 2 or args[0] != "resolve":
    sys.exit(60)
print(os.environ["FAKE_CHART_DIGEST"])
'''

FAKE_RECORDER = '''
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps([os.path.basename(sys.argv[0])] + sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_EXIT_" + os.path.basename(sys.argv[0]).upper(), "0")))
'''

FAKE_GIT_TAGS = '''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["git"] + args) + "\\n")
if args[:3] != ["ls-remote", "--tags", "--refs"]:
    sys.exit(50)
for tag in os.environ.get("FAKE_TAGS", "").split():
    print("0" * 40 + "\\trefs/tags/" + tag)
'''


# ============================================================ parser tests --

class MiniYamlTests(unittest.TestCase):
    """The parser the invariants below rely on, so a mis-parse cannot pass vacuously."""

    def test_nested_mappings_and_scalars(self):
        doc = parse_yaml("a:\n  b: one\n  c: 'two'\n  d: \"three\"\non:\n  x: y\n")
        self.assertEqual(doc, {"a": {"b": "one", "c": "two", "d": "three"}, "on": {"x": "y"}})

    def test_comments_are_dropped_outside_quotes_only(self):
        doc = parse_yaml("a: write # why\nb: 'x # y'\nc: \"p # q\"\n# whole line\nd: e#f\n")
        self.assertEqual(doc, {"a": "write", "b": "x # y", "c": "p # q", "d": "e#f"})

    def test_sequence_of_mappings_indented_and_same_indent(self):
        doc = parse_yaml("steps:\n  - name: one\n    run: x\n  - uses: a/b@c\nlist:\n- p\n- q\n")
        self.assertEqual(doc, {"steps": [{"name": "one", "run": "x"}, {"uses": "a/b@c"}], "list": ["p", "q"]})

    def test_literal_block_scalar_keeps_lines_and_clips_the_end(self):
        doc = parse_yaml("run: |\n  set -e\n  # bash comment\n\n  echo \"a: b\"\n\nnext: 1\n")
        self.assertEqual(doc, {"run": "set -e\n# bash comment\n\necho \"a: b\"\n", "next": "1"})

    def test_block_scalar_chomping(self):
        self.assertEqual(parse_yaml("a: |-\n  x\n  y\n")["a"], "x\ny")
        self.assertEqual(parse_yaml("a: |+\n  x\n\n")["a"], "x\n\n")
        self.assertEqual(parse_yaml("a: >-\n  one\n  two\n\n  three\n")["a"], "one two\nthree")

    def test_flow_collections_single_and_multi_line(self):
        doc = parse_yaml(
            "needs: [guard, 'approve', \"build\"]\n"
            "m: { required: false, default: '[\"amd64\",\"arm64\"]' }\n"
            "matrix:\n  - {\n      name: x,\n      image: \"ghcr.io/a:b\",\n    }\n"
            "tags: [\"v[0-9]+.[0-9]+\"]\n"
        )
        self.assertEqual(doc["needs"], ["guard", "approve", "build"])
        self.assertEqual(doc["m"], {"required": "false", "default": '["amd64","arm64"]'})
        self.assertEqual(doc["matrix"], [{"name": "x", "image": "ghcr.io/a:b"}])
        self.assertEqual(doc["tags"], ["v[0-9]+.[0-9]+"])

    def test_anchor_and_alias_share_the_block(self):
        doc = parse_yaml("a:\n  run: &guard |\n    check\nb:\n  run: *guard\n")
        self.assertEqual(doc["a"]["run"], "check\n")
        self.assertEqual(doc["b"]["run"], doc["a"]["run"])

    def test_plain_multi_line_scalar_folds(self):
        self.assertEqual(parse_yaml("if: a &&\n  b\nnext: c\n"), {"if": "a && b", "next": "c"})

    def test_duplicate_keys_are_refused(self):
        with self.assertRaisesRegex(YamlSubsetError, "duplicate key 'permissions'"):
            parse_yaml("permissions:\n  contents: read\npermissions:\n  contents: write\n")

    def test_bad_indentation_is_refused(self):
        with self.assertRaisesRegex(YamlSubsetError, "unexpected indentation"):
            parse_yaml("a:\n  b: 1\n    c: 2\n")

    def test_unknown_alias_unterminated_flow_and_quotes_are_refused(self):
        with self.assertRaisesRegex(YamlSubsetError, "unknown alias"):
            parse_yaml("a: *nope\n")
        with self.assertRaisesRegex(YamlSubsetError, "unterminated flow"):
            parse_yaml("a: [x, y\n")
        with self.assertRaisesRegex(YamlSubsetError, "unterminated quoted"):
            parse_yaml("a: 'x\n")

    def test_tabs_are_refused(self):
        with self.assertRaisesRegex(YamlSubsetError, "tab"):
            parse_yaml("a:\n\tb: 1\n")

    def test_the_wired_files_parse(self):
        for path in (UPDATE_WORKFLOW, RELEASE_IMAGES, HELM_TOOLS, HELM_RELEASE, BUILD_MULTIARCH):
            with self.subTest(path=path.name):
                self.assertIsInstance(load(path), dict)

    def test_parsed_uses_match_every_uses_line_of_the_text(self):
        for path in (UPDATE_WORKFLOW, RELEASE_IMAGES, HELM_TOOLS):
            with self.subTest(path=path.name):
                raw = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)", path.read_text(encoding="utf-8"), re.M)
                parsed = [value for key, value in walk(load(path)) if key == "uses"]
                self.assertEqual(sorted(parsed), sorted(raw))


# ================================================ release-update-manifest.yml --

class UpdateManifestWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = UPDATE_WORKFLOW.read_text(encoding="utf-8")
        self.doc = load(UPDATE_WORKFLOW)
        self.jobs = self.doc["jobs"]
        self.generate = self.jobs["generate"]
        self.publish = self.jobs["publish"]

    # ---- triggers

    def test_triggers_are_only_workflow_call_and_workflow_dispatch(self):
        self.assertEqual(sorted(self.doc["on"]), ["workflow_call", "workflow_dispatch"])

    def test_raw_on_block_has_no_other_trigger(self):
        block = re.search(r"^on:\n((?:(?:[ ].*)?\n)*)", self.text, re.M).group(1)
        triggers = re.findall(r"^  ([A-Za-z_]+):", block, re.M)
        self.assertEqual(sorted(triggers), ["workflow_call", "workflow_dispatch"])
        for trigger in FORBIDDEN_TRIGGERS:
            with self.subTest(trigger=trigger):
                self.assertIsNone(re.search(rf"^\s*{trigger}:", block, re.M))

    def test_both_triggers_require_a_string_version_and_take_nothing_else(self):
        for trigger in ("workflow_call", "workflow_dispatch"):
            with self.subTest(trigger=trigger):
                inputs = self.doc["on"][trigger]["inputs"]
                self.assertEqual(sorted(inputs), ["version"])
                self.assertEqual(inputs["version"]["required"], "true")
                self.assertEqual(inputs["version"]["type"], "string")
        self.assertNotIn("secrets", self.doc["on"]["workflow_call"])

    def test_no_secret_is_used(self):
        # Keyless signing needs no secret; the only credential is github.token.
        self.assertNotIn("secrets.", self.text)

    # ---- the version guard

    def test_guard_is_the_first_step_and_runs_before_any_checkout(self):
        first = self.generate["steps"][0]
        self.assertNotIn("uses", first)
        self.assertEqual(first["env"], {"VERSION": INPUT_VERSION})
        self.assertIn('[ "$GITHUB_REF" != "refs/tags/$VERSION" ]', first["run"])

    def test_guard_exits_when_the_ref_is_not_the_version_tag(self):
        run = self.generate["steps"][0]["run"]
        self.assertRegex(run, r'if \[ "\$GITHUB_REF" != "refs/tags/\$VERSION" \]; then\n(?:.*\n)*?\s*exit 1\n\s*fi')
        self.assertRegex(run, r'if \[\[ ! "\$VERSION" =~ .* \]\]; then\n(?:.*\n)*?\s*exit 1\n\s*fi')

    def test_guard_version_pattern_accepts_exact_supported_release_tags(self):
        pattern = re.search(r'=~ (\S+) \]\]', self.generate["steps"][0]["run"]).group(1)
        regex = re.compile(pattern.replace("$", r"\Z"))
        for value in ("v1.2.3", "v0.0.0", "v10.20.30", "v1.2.3-rc.1", "v1.2.3-beta.0"):
            self.assertIsNotNone(regex.search(value), value)
            self.assertTrue(is_version(value))
        for value in ("1.2.3", "v1.2", "v1.2.3-rc.01", "v1.2.3-beta", "v1.2.3 ", " v1.2.3", "v1.2.3\nx",
                      "vv1.2.3", "v1.2.3.4", "refs/tags/v1.2.3"):
            self.assertIsNone(regex.search(value), value)

    def test_the_publish_job_cannot_run_without_the_generate_job(self):
        self.assertEqual(needs_of(self.publish), ["generate", "rehearse"])
        self.assertEqual(needs_of(self.jobs["rehearse"]), ["generate"])
        self.assertEqual(self.jobs["rehearse"]["uses"], "./.github/workflows/update-compatibility-proof.yml")
        self.assertEqual(needs_of(self.generate), [])

    # ---- permissions

    def test_top_level_permissions_are_contents_read(self):
        self.assertEqual(self.doc["permissions"], {"contents": "read"})

    def test_generate_job_is_read_only(self):
        self.assertEqual(self.generate["permissions"], {"contents": "read"})

    def test_publish_job_has_exactly_contents_write_and_id_token_write(self):
        self.assertEqual(self.publish["permissions"], {"contents": "write", "id-token": "write"})

    def test_every_job_declares_its_permissions_and_only_publish_writes(self):
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertIsInstance(job.get("permissions"), dict)
                writes = sorted(k for k, v in job["permissions"].items() if v != "read")
                self.assertEqual(writes, ["contents", "id-token"] if name == "publish" else [])

    def test_no_blanket_permission_strings(self):
        for key, value in walk(self.doc):
            if key == "permissions":
                self.assertIsInstance(value, dict, f"permissions: {value!r}")

    # ---- third-party code

    def test_every_remote_action_is_pinned_to_a_full_commit_sha(self):
        for value in [v for k, v in walk(self.doc) if k == "uses"]:
            with self.subTest(uses=value):
                if value.startswith("./"):
                    self.assertTrue((ROOT / value / "action.yml").is_file() or (ROOT / value).is_file())
                elif value.startswith("docker://"):
                    self.assertRegex(value, r"@sha256:[0-9a-f]{64}$")
                else:
                    self.assertRegex(value, PINNED_ACTION_RE)

    def test_every_action_the_local_composite_action_runs_is_pinned(self):
        # setup-helm-tools installs cosign in the job that holds id-token: write.
        for value in [v for k, v in walk(load(HELM_TOOLS)) if k == "uses"]:
            with self.subTest(uses=value):
                self.assertRegex(value, PINNED_ACTION_RE)

    def test_checkouts_never_persist_the_token(self):
        for name, job in self.jobs.items():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    with self.subTest(job=name):
                        self.assertEqual(step.get("with", {}).get("persist-credentials"), "false")

    def test_local_actions_are_checked_out_before_use(self):
        for name, job in self.jobs.items():
            checked_out: Optional[List[str]] = None
            for step in job.get("steps", []):
                uses = str(step.get("uses", ""))
                if uses.startswith("actions/checkout@"):
                    sparse = step.get("with", {}).get("sparse-checkout")
                    checked_out = sparse.split() if sparse else [""]
                elif uses.startswith("./"):
                    with self.subTest(job=name, uses=uses):
                        self.assertIsNotNone(checked_out, f"{uses} is used before any checkout")
                        path = uses[2:]
                        self.assertTrue(any(path == p or path.startswith(p.rstrip("/") + "/") or p == ""
                                            for p in checked_out), f"{uses} is outside sparse-checkout {checked_out}")

    def test_no_expression_is_interpolated_into_a_script(self):
        for name, job in self.jobs.items():
            for step in job.get("steps", []):
                if "run" in step:
                    with self.subTest(job=name, step=step.get("name")):
                        self.assertNotIn("${{", step["run"])

    # ---- never replace

    def test_no_clobber_anywhere(self):
        self.assertNotIn("--clobber", self.text)

    def test_delete_asset_only_appears_in_the_error_message(self):
        for line in self.text.splitlines():
            if "delete-asset" in line:
                self.assertRegex(line.strip(), r'^echo "::error::')

    def test_upload_happens_once_after_signing_and_verifying(self):
        # Round 4 (S12): the bundle, then the manifest, one upload each; a
        # rerun that finds only a verifying bundle uploads the manifest.
        run = one_step(self.publish, runs_containing("gh release upload"))["run"]
        self.assertEqual(run.count('gh release upload "$VERSION" "new/$BUNDLE" --repo'), 1)
        self.assertEqual(run.count('gh release upload "$VERSION" "new/$MANIFEST" --repo'), 2)
        self.assertEqual(run.count("gh release upload"), 4)  # optional proof, then bundle/manifest
        self.assertNotIn('gh release upload "$VERSION" "new/$MANIFEST" "new/$BUNDLE"', run)
        self.assertEqual(run.count("cosign sign-blob"), 1)
        sign = run.index("cosign sign-blob")
        verify = run.index('verify "new/$MANIFEST" "new/$BUNDLE"')
        upload = run.index('gh release upload "$VERSION" "new/$BUNDLE"')
        self.assertLess(sign, verify)
        self.assertLess(verify, upload)
        self.assertLess(upload, run.rindex('gh release upload "$VERSION" "new/$MANIFEST"'))
        self.assertLess(run.index("exit 0"), sign, "the already-published path must stop before signing")

    def test_signing_is_keyless_and_logged(self):
        run = one_step(self.publish, runs_containing("cosign sign-blob"))["run"]
        line = next(candidate for candidate in run.splitlines() if "cosign sign-blob" in candidate)
        self.assertIn("--yes", line)
        self.assertIn('--bundle "new/$BUNDLE"', line)
        for forbidden in ("--key", "--tlog-upload=false", "--use-signing-config=false", "--insecure"):
            self.assertNotIn(forbidden, line)

    def test_verify_uses_the_pinned_identity_and_issuer(self):
        run = one_step(self.publish, runs_containing("cosign verify-blob"))["run"]
        self.assertIn('--certificate-identity "$CERT_IDENTITY"', run)
        self.assertIn('--certificate-oidc-issuer "$OIDC_ISSUER"', run)
        self.assertNotIn("--certificate-identity-regexp", run)

    def test_concurrency_never_cancels_a_running_publish(self):
        concurrency = self.doc["concurrency"]
        self.assertEqual(concurrency["cancel-in-progress"], "false")
        self.assertIn(INPUT_VERSION, concurrency["group"])

    # ---- identity, issuer, assets

    def test_cert_identity_is_the_updaters_default_trusted_identity(self):
        identity = self.publish["env"]["CERT_IDENTITY"]
        self.assertEqual(identity.count("${{ github.repository }}"), 1)
        self.assertTrue(identity.endswith("@refs/tags/" + INPUT_VERSION), identity)
        self.assertEqual(identity.replace("${{ github.repository }}", CANONICAL_REPOSITORY),
                         DEFAULT_TRUSTED_IDENTITY.replace("{version}", INPUT_VERSION))

    def test_cert_identity_names_this_workflow_file(self):
        identity = self.publish["env"]["CERT_IDENTITY"]
        self.assertIn("/" + UPDATE_WORKFLOW.relative_to(ROOT).as_posix() + "@", identity)

    def test_oidc_issuer_is_the_updaters_default_trusted_issuer(self):
        self.assertEqual(self.doc["env"]["OIDC_ISSUER"], DEFAULT_TRUSTED_ISSUER)
        for job in self.jobs.values():
            self.assertNotIn("OIDC_ISSUER", job.get("env") or {})

    def test_the_manifests_informational_identity_is_the_trusted_one(self):
        # trust.py refuses a manifest whose signature.identity differs from the pinned identity.
        run = one_step(self.generate, runs_containing("scripts/generate_update_manifest.py"))["run"]
        identity = re.search(r'--signature-identity "([^"]+)"', run).group(1)
        self.assertEqual(identity.replace("${GITHUB_REPOSITORY}", CANONICAL_REPOSITORY).replace("${VERSION}", "{version}"),
                         DEFAULT_TRUSTED_IDENTITY)
        self.assertIn('--signature-issuer "$OIDC_ISSUER"', run)

    def test_asset_names_are_the_updaters(self):
        self.assertEqual(self.publish["env"]["MANIFEST"], MANIFEST_ASSET)
        self.assertEqual(self.publish["env"]["BUNDLE"], BUNDLE_ASSET)

    def test_generated_upload_and_download_paths_carry_the_manifest_asset(self):
        generate_run = one_step(self.generate, runs_containing("scripts/generate_update_manifest.py"))["run"]
        output = re.search(r'--output "([^"]+)"', generate_run).group(1)
        self.assertEqual(output.rsplit("/", 1)[-1], MANIFEST_ASSET)
        upload = one_step(self.generate, lambda s: s.get("with", {}).get("name") == "update-candidate")
        self.assertEqual(upload["with"]["path"].rsplit("/", 1)[-1], MANIFEST_ASSET)
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertEqual(output.replace("$RUNNER_TEMP", "${{ runner.temp }}"), upload["with"]["path"])
        download = one_step(self.publish, lambda s: s.get("with", {}).get("name") == "update-manifest")
        self.assertEqual(download["with"]["name"], "update-manifest")
        rehearsal = load(WORKFLOWS / "update-compatibility-proof.yml")["jobs"]["proof"]
        candidate = one_step(rehearsal, lambda s: str(s.get("uses", "")).startswith("actions/download-artifact@"))
        self.assertEqual(candidate["with"]["name"], upload["with"]["name"])
        qualified = one_step(rehearsal, lambda s: s.get("with", {}).get("name") == "update-manifest")
        self.assertEqual(qualified["with"]["if-no-files-found"], "error")
        self.assertEqual(download["with"]["path"], "${{ runner.temp }}/new")
        sign = one_step(self.publish, runs_containing("gh release upload"))["run"]
        self.assertIn('cd "$RUNNER_TEMP"', sign)

    def test_the_generator_is_not_given_another_bundle_name(self):
        run = one_step(self.generate, runs_containing("scripts/generate_update_manifest.py"))["run"]
        bundle = re.search(r'--bundle-name "?([^"\s]+)"?', run)
        if bundle:
            self.assertEqual(bundle.group(1), BUNDLE_ASSET)
        self.assertIn('default=BUNDLE_ASSET', GENERATOR.read_text(encoding="utf-8"))

    # ---- what gets resolved and generated

    def _images(self) -> List[Tuple[str, str]]:
        run = one_step(self.generate, runs_containing("declare -A images"))["run"]
        block = re.search(r"declare -A images=\(\n(.*?)\n\s*\)\n", run, re.S).group(1)
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        entries = [re.fullmatch(r"\[([A-Za-z0-9_]+)\]=(\S+)", line) for line in lines]
        self.assertTrue(all(entries), f"unparsed images entries: {lines}")
        return [(m.group(1), m.group(2)) for m in entries]

    def test_resolved_components_are_exactly_the_manifest_components(self):
        names = [name for name, _ in self._images()]
        self.assertEqual(len(names), len(set(names)), "a bash associative array silently drops a duplicate")
        self.assertEqual(sorted(names), sorted(COMPONENT_REPOSITORIES))

    def test_each_component_resolves_its_repository_at_the_release_tag(self):
        for name, template in self._images():
            with self.subTest(component=name):
                self.assertEqual(template.count("$VERSION"), 1)
                self.assertTrue(template.endswith("$VERSION" + COMPONENT_TAG_SUFFIX.get(name, "")), template)
                ref = parse_image_ref(template.replace("$VERSION", "v1.2.3"))
                self.assertEqual(ref.repository, COMPONENT_REPOSITORIES[name])
                self.assertEqual(ref.tag, component_tag({"version": "v1.2.3"}, name))
                self.assertIsNone(ref.digest)

    def test_serving_gpu_resolves_the_gpu_tag_of_the_serving_repository(self):
        images = dict(self._images())
        self.assertEqual(images["serving_gpu"], images["serving"] + "-gpu")
        self.assertEqual(COMPONENT_TAG_SUFFIX, {"serving_gpu": "-gpu"})

    def test_images_name_their_registry_like_the_charts_images_lock(self):
        # list-images.sh writes registry/repository:tag@digest; the lock cross-check
        # compares the text before "@", so an image without its registry never matches.
        for name, template in self._images():
            with self.subTest(component=name):
                self.assertTrue(template.startswith("docker.io/"), template)

    def test_generator_is_called_with_the_published_helm_chart(self):
        run = one_step(self.generate, runs_containing("scripts/generate_update_manifest.py"))["run"]
        self.assertRegex(run, r"--helm-chart oci://ghcr\.io/future-agi/charts/futureagi \\\n")
        self.assertEqual(re.findall(r"--helm-chart (\S+)", run), [HELM_CHART])

    def test_the_chart_digest_is_resolved_from_the_same_chart(self):
        run = one_step(self.generate, runs_containing("oras resolve"))["run"]
        ref = re.search(r'oras resolve "([^"]+)"', run).group(1)
        self.assertEqual(ref, HELM_CHART[len("oci://"):] + ":${VERSION#v}")
        self.assertEqual(load(HELM_RELEASE)["env"]["CHART_REF"], HELM_CHART[len("oci://"):])

    def test_the_images_lock_is_the_asset_helm_release_attaches(self):
        run = one_step(self.generate, runs_containing("gh release download"))["run"]
        self.assertIn('--pattern "futureagi-images-${VERSION#v}.txt"', run)
        helm = HELM_RELEASE.read_text(encoding="utf-8")
        self.assertIn('"futureagi-images-$CHART_VERSION.txt"', helm)
        self.assertIn('echo "chart-version=${VERSION#v}" >> "$GITHUB_OUTPUT"', helm)

    def test_generator_flags_exist_and_required_flags_are_given(self):
        run = one_step(self.generate, runs_containing("scripts/generate_update_manifest.py"))["run"]
        command = re.search(r"python3 scripts/generate_update_manifest\.py \\\n(?:.*\\\n)*.*\n", run).group(0)
        command = re.sub(r"\$\([^)]*\)", "", command)
        used = set(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", command))
        source = GENERATOR.read_text(encoding="utf-8")
        known = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', source))
        required = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"[^)]*required=True', source))
        self.assertTrue(required)
        self.assertLessEqual(used, known)
        self.assertLessEqual(required, used)
        self.assertIn('"${components[@]}"', command)
        self.assertIn('components+=(--component "$line")', run)

    def test_published_at_and_source_sha_come_from_the_tag_commit(self):
        run = one_step(self.generate, runs_containing("scripts/generate_update_manifest.py"))["run"]
        self.assertIn('--source-sha "$(git rev-parse HEAD)"', run)
        self.assertIn('--published-at "$(TZ=UTC git show -s --format=%cd --date=format-local:%Y-%m-%dT%H:%M:%SZ HEAD)"',
                      run)

    def test_the_tag_commits_time_and_sha_are_accepted_by_the_validator(self):
        head = git("rev-parse", "HEAD")
        if head is None or head.returncode != 0:
            self.skipTest("not a git checkout")
        run = one_step(self.generate, runs_containing("scripts/generate_update_manifest.py"))["run"]
        show = re.search(r'--published-at "\$\(TZ=UTC git (show [^)]*)\)"', run).group(1).split()
        env = dict(os.environ, TZ="UTC")
        stamp = subprocess.run(["git", "-C", str(ROOT), *show], capture_output=True, text=True, env=env)
        self.assertEqual(stamp.returncode, 0, stamp.stderr)
        self.assertRegex(stamp.stdout.strip(), r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
        self.assertRegex(head.stdout.strip(), r"^[0-9a-f]{40}$")

    def test_the_publish_job_installs_the_pinned_cosign(self):
        install = one_step(self.publish, lambda s: s.get("uses") == "./.github/actions/setup-helm-tools")
        self.assertEqual(install["with"].get("cosign"), "true")
        self.assertNotIn("cosign-version", install["with"])


# ============================================================ the step scripts --

class _StepHarness(unittest.TestCase):
    def setUp(self):
        if not shutil.which("bash"):
            self.skipTest("bash is not available")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "calls.jsonl"
        self.scratch = self.tmp / "scripts"
        self.scratch.mkdir()
        self.doc = load(UPDATE_WORKFLOW)
        self.version = "v1.60.0"

    def base_env(self, **extra: str) -> Dict[str, str]:
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.tmp),
            "FAKE_LOG": str(self.log),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        env.update(extra)
        return env

    def tools(self) -> List[str]:
        return [call[0] for call in calls(self.log)]


class ImagePublisherWiringTests(_StepHarness):
    def test_immutable_check_is_in_the_tag_creation_step_after_sources(self):
        doc = load(BUILD_MULTIARCH)
        job = doc['jobs']['publish']
        self.assertEqual(needs_of(job), ['prepare', 'build'])
        self.assertEqual(doc['permissions'], {'contents': 'read'})
        self.assertNotIn('permissions', job)  # publisher gains no GitHub write scope
        step = one_step(job, runs_containing('Immutable prerelease'))
        script = step['run']
        self.assertLess(script.index('sources+=('), script.index('create --dry-run'))
        self.assertLess(script.index('create --dry-run'), script.index('imagetools inspect "$ref"'))
        self.assertLess(script.index('imagetools inspect "$ref"'), script.index('"$existing" != "$candidate"'))
        self.assertLess(script.index('"$existing" != "$candidate"'),
                        script.index('docker buildx imagetools create "${tags[@]}"'))
        self.assertEqual(step['env']['VERSION'], '${{ needs.prepare.outputs.version }}')
        self.assertEqual(step['env']['SUFFIX'], '${{ inputs.tag-suffix }}')
        self.assertNotIn('${{', script)  # all untrusted inputs stay in env
        self.assertFalse(any('id-token' in (node or {}) for node in [doc['permissions'], job.get('permissions')]))

    def test_stable_tag_creation_and_alias_options_are_unchanged(self):
        step = one_step(load(BUILD_MULTIARCH)['jobs']['publish'], runs_containing('Immutable prerelease'))
        write_fake(self.bin, 'docker', FAKE_IMAGE_PUBLISHER)
        digests = self.tmp / 'digests'
        digests.mkdir()
        for digest in ('a' * 64, 'b' * 64):
            (digests / digest).touch()
        for suffix in ('', '-slim', '-gpu'):
            for latest in ('false', 'true'):
                with self.subTest(suffix=suffix, latest=latest):
                    self.log.unlink(missing_ok=True)
                    image = 'docker.io/futureagi/synthetic'
                    env = self.base_env(IMAGE=image, VERSION='v1.60.0', MINOR='v1.60', SUFFIX=suffix,
                                        PUSH_LATEST=latest, FAKE_EXISTING='error', FAKE_INSPECT_ERROR='unreachable')
                    result = run_step(step, env, digests, self.scratch)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    commands = calls(self.log)
                    self.assertEqual(len(commands), 1)  # no new stable existence check
                    command = commands[0]
                    self.assertEqual(command[:4], ['docker', 'buildx', 'imagetools', 'create'])
                    tags = [command[i + 1] for i, arg in enumerate(command) if arg == '-t']
                    expected = [image + ':v1.60.0' + suffix, image + ':v1.60' + suffix]
                    if latest == 'true':
                        expected.append(image + ':latest' + suffix)
                    self.assertEqual(tags, expected)
                    self.assertEqual(command[-2:], [image + '@sha256:' + c * 64 for c in ('a', 'b')])


class VersionGuardStepTests(_StepHarness):
    def run_guard(self, version: str, ref: str) -> subprocess.CompletedProcess:
        job = self.doc["jobs"]["generate"]
        step = job["steps"][0]
        env = self.base_env(**step_env(self.doc, job, step, {"inputs.version": version}), GITHUB_REF=ref,
                            GITHUB_REPOSITORY=CANONICAL_REPOSITORY)
        return run_step(step, env, self.tmp, self.scratch)

    def test_the_version_tag_passes(self):
        result = self.run_guard("v1.2.3", "refs/tags/v1.2.3")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_branch_is_refused(self):
        result = self.run_guard("v1.2.3", "refs/heads/main")
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::run this workflow from the tag v1.2.3", result.stdout)

    def test_another_tag_is_refused(self):
        for ref in ("refs/tags/v1.2.4", "refs/tags/v1.2.3-rc.1", "refs/tags/v1.2.3/x", "refs/heads/v1.2.3",
                    "refs/pull/1/merge", "v1.2.3", ""):
            with self.subTest(ref=ref):
                self.assertEqual(self.run_guard("v1.2.3", ref).returncode, 1)

    def test_an_unsupported_version_is_refused_even_from_its_own_tag(self):
        for version in ("v1.2.3-alpha.1", "1.2.3", "v1.2", "v1.2.3\n", "v1.2.3 ", "latest", "", "v1.2.3;id"):
            with self.subTest(version=version):
                result = self.run_guard(version, f"refs/tags/{version}")
                self.assertEqual(result.returncode, 1)
                self.assertIn("is not a platform tag", result.stdout)


class ResolveDigestsStepTests(_StepHarness):
    def setUp(self):
        super().setUp()
        if bash_major() < 4:
            self.skipTest("the step needs bash 4 (declare -A)")
        if not shutil.which("jq") or not shutil.which("awk"):
            self.skipTest("jq and awk are needed")
        for name, body in (("docker", FAKE_DOCKER), ("gh", FAKE_GH), ("oras", FAKE_ORAS), ("sleep", FAKE_RECORDER)):
            write_fake(self.bin, name, body)
        self.release = self.tmp / "release"
        self.release.mkdir()
        self.runner = self.tmp / "runner"
        self.runner.mkdir()
        self.output = self.tmp / "github-output"
        self.output.write_text("")
        self.images = {
            name: f"docker.io/{repository}:{self.version}{COMPONENT_TAG_SUFFIX.get(name, '')}"
            for name, repository in COMPONENT_REPOSITORIES.items()
        }
        self.digests = {image: fake_digest(image) for image in self.images.values()}
        self.chart_digest = fake_digest("chart")
        self.lock = self.release / f"futureagi-images-{self.version[1:]}.txt"
        self.lock.write_text(
            f"docker.io/futureagi/future-agi:{self.version}@{self.digests[self.images['backend']]}\n"
            f"docker.io/futureagi/frontend:{self.version}@{self.digests[self.images['frontend']]}\n"
            f"docker.io/library/postgres:16@{fake_digest('postgres')}\n"
        )

    def run_resolve(self, digests: Optional[Dict[str, str]] = None, chart: Optional[str] = None):
        job = self.doc["jobs"]["generate"]
        step = one_step(job, runs_containing("declare -A images"))
        env = self.base_env(
            **step_env(self.doc, job, step, {"inputs.version": self.version, "github.token": "fake-token"}),
            GITHUB_REPOSITORY=CANONICAL_REPOSITORY, RUNNER_TEMP=str(self.runner), GITHUB_OUTPUT=str(self.output),
            FAKE_RELEASE=str(self.release), FAKE_DIGESTS=json.dumps(self.digests if digests is None else digests),
            FAKE_CHART_DIGEST=self.chart_digest if chart is None else chart,
            EXPECT_VERSION=self.version, EXPECT_REPOSITORY=CANONICAL_REPOSITORY,
        )
        return run_step(step, env, self.tmp, self.scratch)

    def components(self) -> Dict[str, str]:
        lines = (self.runner / "components.txt").read_text().splitlines()
        result = dict(line.split("=", 1) for line in lines)
        self.assertEqual(len(result), len(lines))
        return result

    def test_every_component_is_written_with_its_digest(self):
        result = self.run_resolve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        components = self.components()
        self.assertEqual(sorted(components), sorted(COMPONENT_REPOSITORIES))
        for name, ref in components.items():
            with self.subTest(component=name):
                self.assertEqual(ref, f"{self.images[name]}@{self.digests[self.images[name]]}")
        self.assertIn(f"chart={self.chart_digest}\n", self.output.read_text())

    def test_it_reads_the_charts_lock_and_resolves_the_chart_at_its_version(self):
        self.assertEqual(self.run_resolve().returncode, 0)
        downloads = [c for c in calls(self.log) if c[:3] == ["gh", "release", "download"]]
        self.assertEqual(len(downloads), 1)
        self.assertIn(f"futureagi-images-{self.version[1:]}.txt", downloads[0])
        self.assertIn(["oras", "resolve", f"{HELM_CHART[len('oci://'):]}:{self.version[1:]}"], calls(self.log))
        inspected = sorted(c[4] for c in calls(self.log) if c[0] == "docker")
        self.assertEqual(inspected, sorted(self.images.values()))

    def test_a_digest_that_moved_since_the_chart_lock_fails(self):
        digests = dict(self.digests)
        digests[self.images["backend"]] = fake_digest("rebuilt")
        result = self.run_resolve(digests)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"::error::{self.images['backend']} is {fake_digest('rebuilt')} now but "
                      f"{self.digests[self.images['backend']]} in the chart's images lock", result.stdout)
        self.assertNotIn("chart=", self.output.read_text())

    def test_lock_lines_of_other_tags_are_not_compared(self):
        self.lock.write_text(f"docker.io/futureagi/future-agi:v1.0.0@{fake_digest('old')}\n"
                             f"docker.io/futureagi/future-agi:{self.version}-slim@{fake_digest('slim')}\n")
        result = self.run_resolve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_missing_images_lock_fails(self):
        self.lock.unlink()
        self.assertNotEqual(self.run_resolve().returncode, 0)
        self.assertFalse((self.runner / "components.txt").exists() and self.components())

    def test_an_unpublished_image_fails_after_retrying(self):
        digests = dict(self.digests)
        del digests[self.images["serving_gpu"]]
        result = self.run_resolve(digests)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"::error::cannot resolve {self.images['serving_gpu']}: is it published?", result.stderr)
        self.assertEqual([c for c in calls(self.log) if c[0] == "sleep"],
                         [["sleep", str(10 * attempt)] for attempt in range(1, 6)])

    def test_a_malformed_registry_digest_is_never_written(self):
        for bad in ("sha256:" + "A" * 64, "sha512:" + "0" * 128, "sha256:" + "0" * 63, "", "latest",
                    "sha256:" + "0" * 64 + " x", "sha256:" + "0" * 65):
            with self.subTest(digest=bad):
                digests = dict(self.digests)
                digests[self.images["standalone"]] = bad
                result = self.run_resolve(digests)
                self.assertEqual(result.returncode, 1, bad)
                self.assertIn(f"::error::cannot resolve {self.images['standalone']}", result.stderr)
                written = (self.runner / "components.txt").read_text().splitlines()
                for line in written:
                    self.assertRegex(line, r"^[a-z_]+=docker\.io/[a-z0-9/-]+:v[0-9.]+(?:-gpu)?@sha256:[0-9a-f]{64}$")
                self.assertFalse(any(line.startswith("standalone=") for line in written))

    def test_a_malformed_chart_digest_fails(self):
        for bad in ("", "latest", "sha256:" + "z" * 64, "sha256:" + "0" * 64 + "x"):
            with self.subTest(digest=bad):
                self.output.write_text("")
                result = self.run_resolve(chart=bad)
                self.assertEqual(result.returncode, 1)
                self.assertIn("unexpected chart digest", result.stdout)
                self.assertNotIn("chart=", self.output.read_text())


class GeneratedManifestTests(unittest.TestCase):
    """The workflow's generate step, run for real against this checkout."""

    @classmethod
    def setUpClass(cls):
        cls.skip_reason = None
        head = git("rev-parse", "HEAD")
        if not shutil.which("bash"):
            cls.skip_reason = "bash is not available"
            return
        if head is None or head.returncode != 0:
            cls.skip_reason = "not a git checkout"
            return
        cls.head = head.stdout.strip()
        cls._temporary = tempfile.TemporaryDirectory()
        tmp = Path(cls._temporary.name)
        fakebin = tmp / "bin"
        fakebin.mkdir()
        wrapper = fakebin / "python3"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        wrapper.chmod(0o755)
        cls.runner = tmp / "runner"
        cls.runner.mkdir()
        cls.version = next_version_after_floor()
        cls.chart_digest = fake_digest("chart")
        cls.images = {
            name: f"docker.io/{repository}:{cls.version}{COMPONENT_TAG_SUFFIX.get(name, '')}"
            for name, repository in COMPONENT_REPOSITORIES.items()
        }
        (cls.runner / "components.txt").write_text(
            "".join(f"{name}={image}@{fake_digest(image)}\n" for name, image in cls.images.items()))
        doc = load(UPDATE_WORKFLOW)
        cls.doc = doc
        job = doc["jobs"]["generate"]
        step = one_step(job, runs_containing("scripts/generate_update_manifest.py"))
        env = step_env(doc, job, step, {"inputs.version": cls.version, "steps.digests.outputs.chart": cls.chart_digest})
        env.update(PATH=f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}", HOME=str(tmp),
                   GITHUB_REPOSITORY=CANONICAL_REPOSITORY, RUNNER_TEMP=str(cls.runner), PYTHONDONTWRITEBYTECODE="1")
        cls.result = run_step(step, env, ROOT, tmp)
        output = cls.runner / "out" / MANIFEST_ASSET
        cls.raw = output.read_bytes() if output.exists() else b""
        cls.manifest = json.loads(cls.raw) if cls.raw else None

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_temporary", None):
            cls._temporary.cleanup()

    def setUp(self):
        if self.skip_reason:
            self.skipTest(self.skip_reason)
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)
        self.assertIsNotNone(self.manifest)

    def test_the_step_prints_what_it_wrote(self):
        self.assertEqual(self.result.stdout.encode("utf-8"), self.raw)

    def test_the_manifest_validates(self):
        self.assertEqual(validate_manifest(self.manifest), self.manifest)

    def test_it_conforms_to_the_published_schema(self):
        self.assertEqual(schema_errors(self.manifest), [])

    def test_workflow_generation_never_certifies_by_default(self):
        self.assertEqual(self.manifest["schema_version"], 2)
        self.assertEqual(self.manifest["compatibility"], uncertified_compatibility(2))
        self.assertEqual(self.manifest["migration"], {"requires_bootstrap": True, "rollback": "manual"})
        policy = json.loads((UPDATE_DIR / "release-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy, {"schema_version": 1, "compatibility": uncertified_compatibility(2),
                                  "migration": {"requires_bootstrap": True, "rollback": "manual"}})
        # This executes the ordinary workflow invocation, not just an isolated
        # policy fixture: publishing a release is not itself certification.

    def test_explicit_legacy_generation_remains_valid_for_manual_use(self):
        command = [sys.executable, str(GENERATOR), "--schema-version", "1", "--version", self.version,
                   "--source-sha", self.head, "--published-at", self.manifest["published_at"],
                   "--helm-chart", HELM_CHART, "--helm-digest", self.chart_digest]
        for name, image in self.images.items():
            command += ["--component", f"{name}={image}@{fake_digest(image)}"]
        result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertNotIn("compatibility", manifest)
        self.assertEqual(validate_manifest(manifest), manifest)
        self.assertEqual(schema_errors(manifest), [])
        self.assertEqual(manifest["migration"], {"requires_bootstrap": True, "rollback": "manual"})

    def test_version_channel_and_floor(self):
        self.assertEqual(self.manifest["version"], self.version)
        self.assertEqual(self.manifest["channel"], "stable")
        self.assertEqual(self.manifest["minimum_version"], read_upgrade_floor(ROOT))

    def test_source_sha_and_published_at_are_the_tag_commits(self):
        self.assertEqual(self.manifest["source_sha"], self.head)
        stamp = subprocess.run(["git", "-C", str(ROOT), "show", "-s", "--format=%ct", "HEAD"],
                               capture_output=True, text=True).stdout.strip()
        expected = datetime.datetime.fromtimestamp(int(stamp), tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(self.manifest["published_at"], expected)

    def test_components_are_the_resolved_digests(self):
        self.assertEqual(sorted(self.manifest["components"]), sorted(COMPONENT_REPOSITORIES))
        for name, component in self.manifest["components"].items():
            with self.subTest(component=name):
                self.assertEqual(component["image"], self.images[name])
                self.assertEqual(component["digest"], fake_digest(self.images[name]))

    def test_signature_names_the_bundle_asset_and_the_trusted_signer(self):
        self.assertEqual(self.manifest["signature"], {
            "bundle": BUNDLE_ASSET,
            "identity": DEFAULT_TRUSTED_IDENTITY.replace("{version}", self.version),
            "issuer": DEFAULT_TRUSTED_ISSUER,
        })

    def test_helm_chart_reference(self):
        self.assertEqual(self.manifest["helm"], {"chart": HELM_CHART, "version": self.version[1:],
                                                 "digest": self.chart_digest})

    def test_deployment_files_are_the_checked_in_list(self):
        self.assertEqual(sorted(self.manifest["deployment"]["files"]), sorted(read_deployment_file_list(ROOT)))

    def test_release_notes_point_at_the_release_of_the_trusted_repository(self):
        self.assertEqual(self.manifest["release_notes"],
                         f"https://github.com/{CANONICAL_REPOSITORY}/releases/tag/{self.version}")

    def test_platforms_match_the_architectures_release_images_builds(self):
        jobs = load(RELEASE_IMAGES)["jobs"]
        default = json.loads(load(BUILD_MULTIARCH)["on"]["workflow_call"]["inputs"]["arches"]["default"])
        for name, component in self.manifest["components"].items():
            with self.subTest(component=name):
                arches = json.loads(jobs[COMPONENT_JOBS[name]].get("with", {}).get("arches") or json.dumps(default))
                self.assertEqual(sorted(component["platforms"]), sorted(f"linux/{a}" for a in arches))

    def test_default_settings_verify_it_with_the_publish_jobs_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            tmp = Path(temporary)
            (tmp / "checkout").mkdir()
            release = tmp / "release"
            release.mkdir()
            (release / MANIFEST_ASSET).write_bytes(self.raw)
            (release / BUNDLE_ASSET).write_text("{}")
            commands: List[List[str]] = []

            def runner(command, **_kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            settings = load_settings(tmp / "checkout", environ={})
            fetched = fetch_manifest(str(release / MANIFEST_ASSET), tmp / "work", cwd=tmp)
            verification = Verifier(settings, runner=runner,
                                    which=lambda name: "/fake/cosign" if name == "cosign" else None).verify(fetched)
        self.assertTrue(verification.verified, verification.error)
        command = commands[0]
        publish_env = self.doc["jobs"]["publish"]["env"]
        expected = expand(publish_env["CERT_IDENTITY"],
                          {"github.repository": CANONICAL_REPOSITORY, "inputs.version": self.version})
        self.assertEqual(command[command.index("--certificate-identity") + 1], expected)
        self.assertEqual(command[command.index("--certificate-oidc-issuer") + 1], self.doc["env"]["OIDC_ISSUER"])
        self.assertNotIn("--certificate-identity-regexp", command)

    def test_the_default_url_fetches_the_bundle_from_the_release_it_was_attached_to(self):
        bundle_url = f"https://github.com/{CANONICAL_REPOSITORY}/releases/download/{self.version}/{BUNDLE_ASSET}"
        served = {DEFAULT_MANIFEST_URL: self.raw, bundle_url: b"{}"}
        requested: List[str] = []

        def download(url, timeout=30.0):
            requested.append(url)
            if url not in served:
                raise ManifestError(f"unexpected download {url}")
            return served[url]

        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(trust, "_download", download):
            fetched = fetch_manifest(DEFAULT_MANIFEST_URL, Path(temporary) / "work", cwd=Path(temporary))
            self.assertIsNone(fetched.bundle_error)
        self.assertEqual(requested, [DEFAULT_MANIFEST_URL, bundle_url])
        upload = one_step(self.doc["jobs"]["publish"], runs_containing("gh release upload"))["run"]
        self.assertIn('gh release upload "$VERSION"', upload)


class PublishStepTests(_StepHarness):
    """The sign-and-attach step against fake gh and cosign: never replace."""

    def setUp(self):
        super().setUp()
        if not shutil.which("cmp"):
            self.skipTest("cmp is needed")
        write_fake(self.bin, "gh", FAKE_GH)
        write_fake(self.bin, "cosign", FAKE_COSIGN)
        self.release = self.tmp / "release"
        self.release.mkdir()
        self.runner = self.tmp / "runner"
        (self.runner / "new").mkdir(parents=True)
        self.manifest = b'{"version": "v1.60.0"}\n'
        (self.runner / "new" / MANIFEST_ASSET).write_bytes(self.manifest)
        self.summary = self.tmp / "summary.md"
        # What Fulcio certifies when this workflow runs at the release tag.
        self.signer = DEFAULT_TRUSTED_IDENTITY.replace("{version}", self.version)

    def bundle_for(self, data: bytes, identity: Optional[str] = None) -> str:
        return json.dumps({"identity": identity or self.signer, "issuer": DEFAULT_TRUSTED_ISSUER,
                           "sha256": hashlib.sha256(data).hexdigest()})

    def run_publish(self, signer: Optional[str] = None) -> subprocess.CompletedProcess:
        job = self.doc["jobs"]["publish"]
        step = one_step(job, runs_containing("gh release upload"))
        context = {"inputs.version": self.version, "github.repository": CANONICAL_REPOSITORY,
                   "github.token": "fake-token"}
        env = self.base_env(
            **step_env(self.doc, job, step, context),
            GITHUB_REPOSITORY=CANONICAL_REPOSITORY, RUNNER_TEMP=str(self.runner),
            GITHUB_STEP_SUMMARY=str(self.summary), FAKE_RELEASE=str(self.release),
            FAKE_SIGNER=signer or self.signer, FAKE_ISSUER=DEFAULT_TRUSTED_ISSUER,
            EXPECT_VERSION=self.version, EXPECT_REPOSITORY=CANONICAL_REPOSITORY,
        )
        return run_step(step, env, self.tmp, self.scratch)

    def assets(self) -> Dict[str, bytes]:
        return {p.name: p.read_bytes() for p in self.release.iterdir()}

    def uploads(self) -> List[List[str]]:
        return [c for c in calls(self.log) if c[:3] == ["gh", "release", "upload"]]

    def signs(self) -> List[List[str]]:
        return [c for c in calls(self.log) if c[:2] == ["cosign", "sign-blob"]]

    def test_a_fresh_release_gets_the_signed_manifest_and_its_bundle(self):
        (self.release / "futureagi-1.60.0.tgz").write_bytes(b"chart")
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        assets = self.assets()
        self.assertEqual(assets[MANIFEST_ASSET], self.manifest)
        self.assertEqual(json.loads(assets[BUNDLE_ASSET])["identity"], self.signer)
        self.assertEqual(len(self.signs()), 1)
        # Round 4 (S12): the bundle, then the manifest, one upload each.
        self.assertEqual([upload[3:] for upload in self.uploads()],
                         [[self.version, f"new/{BUNDLE_ASSET}", "--repo", CANONICAL_REPOSITORY],
                          [self.version, f"new/{MANIFEST_ASSET}", "--repo", CANONICAL_REPOSITORY]])
        for upload in self.uploads():
            self.assertNotIn("--clobber", upload)
        self.assertIn(self.signer, self.summary.read_text())

    def test_the_new_signature_is_verified_against_the_trusted_identity_before_upload(self):
        self.run_publish()
        verifies = [c for c in calls(self.log) if c[:2] == ["cosign", "verify-blob"]]
        self.assertEqual(len(verifies), 1)
        command = verifies[0]
        self.assertEqual(command[command.index("--certificate-identity") + 1], self.signer)
        self.assertEqual(command[command.index("--certificate-oidc-issuer") + 1], DEFAULT_TRUSTED_ISSUER)
        self.assertLess(calls(self.log).index(verifies[0]), calls(self.log).index(self.uploads()[0]))

    def test_a_signature_from_another_identity_is_never_attached(self):
        result = self.run_publish(signer="https://github.com/someone/fork/.github/workflows/x.yml@refs/heads/main")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.assets(), {})

    def test_an_identical_published_manifest_is_kept(self):
        (self.release / MANIFEST_ASSET).write_bytes(self.manifest)
        (self.release / BUNDLE_ASSET).write_text(self.bundle_for(self.manifest))
        before = self.assets()
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("is already published and verifies against", result.stdout)
        self.assertEqual(self.signs(), [])
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.assets(), before)

    def test_a_different_published_manifest_is_never_replaced(self):
        (self.release / MANIFEST_ASSET).write_bytes(b'{"version": "v1.60.0", "other": 1}\n')
        (self.release / BUNDLE_ASSET).write_text(self.bundle_for(b"other"))
        before = self.assets()
        result = self.run_publish()
        self.assertEqual(result.returncode, 1)
        self.assertIn("already carries a different", result.stdout)
        self.assertIn("never replaced", result.stdout)
        self.assertEqual(self.signs(), [])
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.assets(), before)

    def test_an_identical_manifest_with_a_bad_bundle_fails(self):
        (self.release / MANIFEST_ASSET).write_bytes(self.manifest)
        (self.release / BUNDLE_ASSET).write_text(self.bundle_for(self.manifest, identity="https://example.com/x"))
        before = self.assets()
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.assets(), before)

    def test_a_release_with_only_one_of_the_two_assets_fails(self):
        for present in (MANIFEST_ASSET, BUNDLE_ASSET):
            with self.subTest(present=present):
                for path in self.release.iterdir():
                    path.unlink()
                (self.release / present).write_bytes(self.manifest)
                result = self.run_publish()
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"has only one of {MANIFEST_ASSET} and {BUNDLE_ASSET}", result.stdout)
                self.assertEqual(self.uploads(), [])
                self.assertEqual(sorted(self.assets()), [present])


# ===================================================== release-images.yml --

class ReleaseProofWiringTests(unittest.TestCase):
    def setUp(self):
        self.doc = load(UPDATE_WORKFLOW)
        self.generate = self.doc["jobs"]["generate"]
        self.publish = self.doc["jobs"]["publish"]

    def test_generation_and_signing_repeat_identical_tag_and_metadata_guards(self):
        self.assertEqual(self.generate["steps"][0]["run"], self.publish["steps"][0]["run"])
        self.assertEqual(self.generate["steps"][1]["run"], self.publish["steps"][1]["run"])
        self.assertIn('[ "$GITHUB_REPOSITORY" = future-agi/future-agi ]', self.publish["steps"][0]["run"])
        self.assertIn(".isDraft == false", self.publish["steps"][1]["run"])
        self.assertIn(".isPrerelease == $prerelease", self.publish["steps"][1]["run"])

    def test_proof_is_built_after_digests_and_before_manifest_artifact(self):
        steps = self.generate["steps"]
        resolve = one_step(self.generate, runs_containing("declare -A images"))
        upload = one_step(self.generate, lambda s: s.get("with", {}).get("name") == "update-candidate")
        self.assertLess(steps.index(resolve), steps.index(upload))
        self.assertEqual(needs_of(self.doc["jobs"]["rehearse"]), ["generate"])
        self.assertEqual(needs_of(self.publish), ["generate", "rehearse"])
        job = load(WORKFLOWS / "update-compatibility-proof.yml")["jobs"]["proof"]
        build = one_step(job, runs_containing("scripts/build_update_compatibility_proof.py"))
        self.assertLess(build["run"].index("scripts/rehearse_update_compatibility.py"),
                        build["run"].index("scripts/build_update_compatibility_proof.py"))
        self.assertIn('--policy "$RUNNER_TEMP/evidence/policy.json"', build["run"])
        self.assertIn('--run-id "$GITHUB_RUN_ID"', build["run"])
        self.assertNotIn("continue-on-error", build)
        self.assertLess(job["steps"].index(build), job["steps"].index(one_step(
            job, lambda s: s.get("with", {}).get("name") == "update-manifest")))

    def test_signing_validates_proof_against_manifest_before_any_signature(self):
        verify = one_step(self.publish, lambda s: s.get("name") == "Validate proof before signing")
        sign = one_step(self.publish, runs_containing("cosign sign-blob"))
        self.assertLess(self.publish["steps"].index(verify), self.publish["steps"].index(sign))
        self.assertIn("validate_release_proof(read_bytes(proof), target", verify["run"])
        self.assertIn('validation["run_id"] == int(os.environ["GITHUB_RUN_ID"])', verify["run"])
        self.assertIn("assert not proof.exists()", verify["run"])
        self.assertNotIn("continue-on-error", verify)

    def test_qualification_preserves_frozen_migration_and_uses_native_measured_budgets(self):
        verify = one_step(self.publish, lambda s: s.get('name') == 'Validate proof before signing')['run']
        self.assertIn('derived = {"compatibility"}', verify)
        job = load(WORKFLOWS/'update-compatibility-proof.yml')['jobs']['proof']
        self.assertEqual(job['runs-on'],'ubuntu-24.04')
        self.assertEqual(job['timeout-minutes'],'360')
        run = one_step(job,runs_containing('scripts/rehearse_update_compatibility.py'))['run']
        self.assertIn('test "$RUNNER_ARCH" = X64',run)
        self.assertIn('--docker-context default --platform linux/amd64 --purpose product',run)
        self.assertIn('--startup-timeout-seconds 4200 --rehearsal-timeout-seconds 19800',run)
        self.assertIn('set -euo pipefail',run)
        self.assertIn('if [ -f "$RUNNER_TEMP/evidence/manual.json" ] || [ -f "$RUNNER_TEMP/evidence/failure.json" ]',run)
        self.assertIn('exit 2',run)
        self.assertLess(run.index('exit 2'),run.index('scripts/build_update_compatibility_proof.py'))
        self.assertIn('test -s "$RUNNER_TEMP/evidence/evidence.json"',run)
        self.assertNotIn('--manual-decision',run)
        self.assertNotIn('test_update_image_descriptors',(WORKFLOWS/'update-compatibility-proof.yml').read_text())

    def test_proof_downloads_cannot_select_other_runs_or_unreviewed_inputs(self):
        for job in (self.generate, self.publish):
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/download-artifact@"):
                    self.assertLessEqual(set(step["with"]), {"name", "pattern", "path", "merge-multiple"})
        for trigger in ("workflow_call", "workflow_dispatch"):
            self.assertEqual(set(self.doc["on"][trigger]["inputs"]), {"version"})


class ReleaseImagesWiringTests(unittest.TestCase):
    def setUp(self):
        self.text = RELEASE_IMAGES.read_text(encoding="utf-8")
        self.doc = load(RELEASE_IMAGES)
        self.jobs = self.doc["jobs"]
        self.job = self.jobs["update-manifest"]
        self.guard = self.jobs["guard"]["steps"][0]["run"]

    def guard_components(self) -> List[str]:
        return re.search(r'^all="([^"]+)"$', self.guard, re.M).group(1).split()

    def test_update_manifest_is_a_guard_component(self):
        self.assertIn("update-manifest", self.guard_components())
        self.assertEqual(self.guard_components().count("update-manifest"), 1)

    def test_the_dispatch_input_documents_update_manifest(self):
        description = self.doc["on"]["workflow_dispatch"]["inputs"]["components"]["description"]
        self.assertIn("update-manifest", description)

    def test_the_approved_guard_reruns_the_same_script(self):
        self.assertEqual(self.jobs["approve"]["steps"][0]["run"], self.guard)

    def test_update_manifest_is_not_exempt_from_the_tag_check(self):
        exempt = re.search(r'case "\$c" in bump \| docs\) continue ;; esac', self.guard)
        self.assertIsNotNone(exempt)
        self.assertIn('if [ "$c" != ee ] && [ "$GITHUB_REF" != "refs/tags/$v" ]; then', self.guard)

    def test_it_calls_the_reusable_workflow_with_the_guards_version(self):
        self.assertEqual(self.job["uses"], "./.github/workflows/release-update-manifest.yml")
        self.assertEqual(self.job["with"], {"version": "${{ needs.guard.outputs.version }}"})
        self.assertEqual(self.jobs["guard"]["outputs"]["version"], "${{ steps.v.outputs.version }}")
        self.assertNotIn("secrets", self.job)
        self.assertNotIn("steps", self.job)

    def test_with_matches_the_reusable_workflows_inputs(self):
        inputs = load(UPDATE_WORKFLOW)["on"]["workflow_call"]["inputs"]
        required = {name for name, spec in inputs.items() if spec.get("required") == "true"}
        self.assertLessEqual(set(self.job["with"]), set(inputs))
        self.assertLessEqual(required, set(self.job["with"]))

    def test_it_has_exactly_contents_write_and_id_token_write(self):
        self.assertEqual(self.job["permissions"], {"contents": "write", "id-token": "write"})

    def test_it_needs_every_job_that_publishes_what_the_manifest_names(self):
        needs = set(needs_of(self.job))
        self.assertLessEqual({"guard", "approve", "helm-chart", "standalone", "backend", "build", "serving-gpu",
                              "code-executor", "simulation-runner"}, needs)
        for job in needs:
            self.assertIn(job, self.jobs)

    def test_each_manifest_component_is_built_by_a_job_it_needs(self):
        needs = set(needs_of(self.job))
        for component, job_name in COMPONENT_JOBS.items():
            with self.subTest(component=component, job=job_name):
                self.assertIn(job_name, needs)
                job = self.jobs[job_name]
                self.assertEqual(job["uses"], "./.github/workflows/build-image-multiarch.yml")
                images = [job["with"]["image"]]
                if images == ["${{ matrix.image }}"]:
                    images = [item["image"] for item in job["strategy"]["matrix"]["include"]]
                self.assertIn(COMPONENT_REPOSITORIES[component], images)
                self.assertEqual(job["with"].get("tag-suffix", ""), COMPONENT_TAG_SUFFIX.get(component, ""))
        self.assertEqual(sorted(COMPONENT_JOBS), sorted(COMPONENT_REPOSITORIES))

    def test_it_runs_only_after_approval_and_when_selected(self):
        condition = self.job["if"]
        for part in ("!cancelled()", "!failure()", "needs.approve.result == 'success'",
                     "contains(needs.guard.outputs.components, '\"update-manifest\"')"):
            self.assertIn(part, condition)

    def test_only_this_job_calls_the_manifest_workflow(self):
        callers = []
        for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
            if "release-update-manifest.yml" in path.read_text(encoding="utf-8") and path != UPDATE_WORKFLOW:
                for name, job in load(path).get("jobs", {}).items():
                    if str(job.get("uses", "")).endswith("release-update-manifest.yml"):
                        callers.append((path.name, name))
        self.assertEqual(callers, [("release-images.yml", "update-manifest")])

    def test_no_other_workflow_uploads_an_artifact_named_like_the_manifests(self):
        # A called workflow shares the caller's run, and with it the artifact names.
        proof_workflow = WORKFLOWS / "update-compatibility-proof.yml"
        expected = {(UPDATE_WORKFLOW.name, "generate", "update-candidate"),
                    (proof_workflow.name, "proof", "update-manifest"),
                    (proof_workflow.name, "proof", "update-compatibility-proof")}
        actual = set()
        reserved = {row[2] for row in expected}
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            if path not in (UPDATE_WORKFLOW, proof_workflow):
                for name in reserved:
                    with self.subTest(workflow=path.name, artifact=name):
                        self.assertIsNone(re.search(rf"^\s*name:\s*[\"']?{re.escape(name)}[\"']?\s*$",
                                                    path.read_text(encoding="utf-8"), re.M))
                continue
            for job_name, job in load(path).get("jobs", {}).items():
                for step in job.get("steps", []):
                    if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                        name = step.get("with", {}).get("name")
                        if name in reserved:
                            actual.add((path.name, job_name, name))
        self.assertEqual(actual, expected)
        jobs = load(UPDATE_WORKFLOW)["jobs"]
        self.assertEqual(jobs["rehearse"]["uses"], "./.github/workflows/update-compatibility-proof.yml")
        self.assertEqual(set(needs_of(jobs["publish"])), {"generate", "rehearse"})

    def test_no_other_workflow_touches_the_manifest_assets(self):
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            if path == UPDATE_WORKFLOW:
                continue
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8")
                if path.name == "update-compatibility-proof.yml":
                    # The same-run nonpublishing producer must handle the frozen
                    # manifest, but it can neither sign nor attach release assets.
                    doc = load(path)
                    self.assertEqual(doc["permissions"], {"contents": "read"})
                    self.assertTrue(all(job.get("permissions", {}).get("contents", "read") == "read"
                                        and "id-token" not in job.get("permissions", {})
                                        for job in doc["jobs"].values()))
                    self.assertNotIn("gh release upload", text)
                    self.assertNotIn("cosign sign", text)
                    self.assertNotIn(BUNDLE_ASSET, text)
                else:
                    self.assertNotIn(MANIFEST_ASSET, text)
                    self.assertNotIn(BUNDLE_ASSET, text)


class ReleaseImagesGuardStepTests(_StepHarness):
    def setUp(self):
        super().setUp()
        if not shutil.which("jq"):
            self.skipTest("jq is needed")
        self.doc = load(RELEASE_IMAGES)
        write_fake(self.bin, "git", FAKE_GIT_TAGS)
        write_fake(self.bin, "curl", FAKE_RECORDER)
        self.output = self.tmp / "github-output"
        self.output.write_text("")

    def run_guard(self, event: str, ref: str, version: str = "", components: str = "",
                  tags: str = "") -> subprocess.CompletedProcess:
        job = self.doc["jobs"]["guard"]
        step = job["steps"][0]
        env = self.base_env(
            **step_env(self.doc, job, step, {"inputs.version": version, "inputs.components": components, "github.token": "fake-token"}),
            GITHUB_EVENT_NAME=event, GITHUB_REF=ref, GITHUB_REF_NAME=ref.rsplit("/", 1)[-1],
            GITHUB_SERVER_URL="https://github.com", GITHUB_REPOSITORY=CANONICAL_REPOSITORY,
            GITHUB_OUTPUT=str(self.output), FAKE_TAGS=tags, FAKE_EXIT_CURL="97",
        )
        return run_step(step, env, self.tmp, self.scratch)

    def outputs(self) -> Dict[str, str]:
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines() if "=" in line)

    def test_a_tag_push_selects_update_manifest(self):
        result = self.run_guard("push", "refs/tags/v1.60.0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.outputs()["version"], "v1.60.0")
        self.assertIn("update-manifest", json.loads(self.outputs()["components"]))
        self.assertEqual(calls(self.log), [])

    def test_a_dispatch_of_update_manifest_from_a_branch_is_refused(self):
        result = self.run_guard("workflow_dispatch", "refs/heads/main", "v1.60.0", "update-manifest")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run this workflow from the tag v1.60.0 to build 'update-manifest'", result.stdout)
        self.assertEqual(calls(self.log), [])

    def test_a_dispatch_of_update_manifest_for_an_older_release_runs_only_it(self):
        # Round 4 (S13): the manifest moves no tag, so an older release's
        # manifest can be attached again (from its own tag).
        result = self.run_guard("workflow_dispatch", "refs/tags/v1.60.0", "v1.60.0", "update-manifest",
                                tags="v1.59.0 v1.60.0 v1.61.0 v1.62.0-rc.1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("can only be built for the newest release", result.stdout)
        self.assertEqual(json.loads(self.outputs()["components"]), ["update-manifest"])

    def test_a_dispatch_of_update_manifest_for_the_newest_release_runs_only_it(self):
        result = self.run_guard("workflow_dispatch", "refs/tags/v1.60.0", "v1.60.0", "update-manifest",
                                tags="v1.59.0 v1.60.0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.outputs()["components"]), ["update-manifest"])
        # Round 4 (S13): no newest-release check either, so no tag listing.
        self.assertEqual(calls(self.log), [], "no tag listing and no Docker Hub tag check for the manifest")

    def test_update_manifest_cannot_ride_along_with_bump(self):
        result = self.run_guard("workflow_dispatch", "refs/tags/v1.60.0", "v1.60.0", "update-manifest,bump")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run 'bump' on its own", result.stdout)

    def test_a_misspelt_component_is_refused(self):
        result = self.run_guard("workflow_dispatch", "refs/tags/v1.60.0", "v1.60.0", "update_manifest")
        self.assertEqual(result.returncode, 1)
        self.assertIn("components must be a comma-separated list of names", result.stdout)
        result = self.run_guard("workflow_dispatch", "refs/tags/v1.60.0", "v1.60.0", "update-manifests")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown component 'update-manifests'", result.stdout)


# ======================================================== settings defaults --

class SettingsDefaultsTests(unittest.TestCase):
    def test_default_manifest_url_is_the_latest_releases_manifest_asset(self):
        self.assertEqual(DEFAULT_MANIFEST_URL,
                         f"https://github.com/{CANONICAL_REPOSITORY}/releases/latest/download/{MANIFEST_ASSET}")
        self.assertEqual(DEFAULTS["FUTURE_AGI_UPDATE_MANIFEST_URL"], DEFAULT_MANIFEST_URL)

    def test_trust_recognises_the_default_url_as_a_latest_download(self):
        match = trust._GITHUB_LATEST_RE.fullmatch(DEFAULT_MANIFEST_URL)
        self.assertIsNotNone(match)
        # Groups 1 and 2 are the owner and the repository (a later group may name the asset).
        self.assertEqual("/".join(match.group(1, 2)), CANONICAL_REPOSITORY)
        self.assertTrue(DEFAULT_MANIFEST_URL.endswith(f"/releases/latest/download/{MANIFEST_ASSET}"))

    def test_trusted_identity_is_the_manifest_workflow_at_the_manifests_own_tag(self):
        self.assertEqual(DEFAULTS["FUTURE_AGI_UPDATE_TRUSTED_IDENTITY"], DEFAULT_TRUSTED_IDENTITY)
        self.assertEqual(DEFAULT_TRUSTED_IDENTITY.count("{version}"), 1)
        self.assertEqual(
            DEFAULT_TRUSTED_IDENTITY,
            f"https://github.com/{CANONICAL_REPOSITORY}/{UPDATE_WORKFLOW.relative_to(ROOT).as_posix()}"
            "@refs/tags/{version}")

    def test_trusted_identity_and_manifest_url_name_the_same_repository(self):
        url_repo = re.match(r"^https://github\.com/([^/]+/[^/]+)/", DEFAULT_MANIFEST_URL).group(1)
        identity_repo = re.match(r"^https://github\.com/([^/]+/[^/]+)/", DEFAULT_TRUSTED_IDENTITY).group(1)
        self.assertEqual(url_repo, identity_repo)
        self.assertIn(f"https://github.com/{url_repo}/releases/tag/", GENERATOR.read_text(encoding="utf-8"))

    def test_trusted_issuer_is_github_actions_everywhere(self):
        self.assertEqual(DEFAULT_TRUSTED_ISSUER, "https://token.actions.githubusercontent.com")
        self.assertEqual(DEFAULTS["FUTURE_AGI_UPDATE_TRUSTED_ISSUER"], DEFAULT_TRUSTED_ISSUER)
        self.assertEqual(load(HELM_RELEASE)["env"]["OIDC_ISSUER"], DEFAULT_TRUSTED_ISSUER)
        self.assertIn(f'default="{DEFAULT_TRUSTED_ISSUER}"', GENERATOR.read_text(encoding="utf-8"))

    def test_cosign_image_is_pinned_by_digest(self):
        ref = parse_image_ref(DEFAULT_COSIGN_IMAGE)
        self.assertEqual(ref.name, "ghcr.io/sigstore/cosign/cosign")
        self.assertRegex(ref.digest or "", r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(DEFAULTS["FUTURE_AGI_UPDATE_COSIGN_IMAGE"], DEFAULT_COSIGN_IMAGE)

    def test_cosign_image_tag_is_the_release_the_manifest_is_signed_with(self):
        default = load(HELM_TOOLS)["inputs"]["cosign-version"]["default"]
        self.assertRegex(default, r"^v[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertEqual(parse_image_ref(DEFAULT_COSIGN_IMAGE).tag, default)

    def test_composite_action_installs_cosign_at_that_version(self):
        steps = load(HELM_TOOLS)["runs"]["steps"]
        installer = next(s for s in steps if str(s.get("uses", "")).startswith("sigstore/cosign-installer@"))
        self.assertEqual(installer["with"]["cosign-release"], "${{ inputs.cosign-version }}")

    def test_defaults_load_without_any_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = load_settings(Path(temporary), environ={})
        for key, value in DEFAULTS.items():
            self.assertEqual(settings.get(key), value)
            self.assertEqual(settings.sources[key], "default")

    def test_config_file_and_state_dir_names(self):
        self.assertEqual(CONFIG_FILE, "update.conf")
        self.assertEqual(STATE_DIR, ".futureagi-updates")


# ================================================================ .gitignore --

class GitIgnoreTests(unittest.TestCase):
    def setUp(self):
        result = git("rev-parse", "--show-toplevel")
        if result is None or result.returncode != 0:
            self.skipTest("not a git checkout")
        if Path(result.stdout.strip()).resolve() != ROOT.resolve():
            self.skipTest("the checkout root is not this repository")

    def check_ignore(self, path: str) -> subprocess.CompletedProcess:
        return git("-c", f"core.excludesFile={os.devnull}", "check-ignore", "-v", "--no-index", path)

    def test_update_conf_at_the_root_is_ignored_by_the_root_gitignore(self):
        result = self.check_ignore(CONFIG_FILE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(".gitignore:"), result.stdout)

    def test_the_state_dir_and_everything_in_it_is_ignored(self):
        for path in (STATE_DIR + "/", f"{STATE_DIR}/journal.json", f"{STATE_DIR}/receipt-20261019T000000Z-1.json",
                     f"{STATE_DIR}/verify-123/manifest.json", f"{STATE_DIR}/lock"):
            with self.subTest(path=path):
                result = self.check_ignore(path)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout.startswith(".gitignore:"), result.stdout)

    def test_neither_is_tracked(self):
        result = git("ls-files", "--", CONFIG_FILE, STATE_DIR)
        self.assertEqual(result.stdout, "")

    def test_the_rules_are_anchored_at_the_root(self):
        lines = [line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
        self.assertIn("/" + CONFIG_FILE, lines)
        self.assertIn("/" + STATE_DIR + "/", lines)
        self.assertNotIn("!" + CONFIG_FILE, lines)
        self.assertNotIn("!/" + CONFIG_FILE, lines)


# ============================================================= upgrade floor --

class UpgradeFloorTests(unittest.TestCase):
    def test_the_floor_parses(self):
        floor = read_upgrade_floor(ROOT)
        self.assertTrue(is_version(floor))
        self.assertFalse(is_prerelease(floor))

    def test_the_file_holds_exactly_one_version_line(self):
        lines = [line.split("#", 1)[0].strip() for line in (UPDATE_DIR / "upgrade-floor").read_text().splitlines()]
        self.assertEqual([line for line in lines if line], [read_upgrade_floor(ROOT)])

    def test_the_floor_is_not_newer_than_the_latest_release_tag(self):
        result = git("tag", "--list", "v*")
        if result is None or result.returncode != 0:
            self.skipTest("not a git checkout")
        tags = [t for t in result.stdout.split() if is_version(t)]
        if not tags:
            self.skipTest("no release tags in this clone")
        latest = max(tags, key=parse_version)
        self.assertLessEqual(parse_version(read_upgrade_floor(ROOT)), parse_version(latest),
                             f"upgrade floor {read_upgrade_floor(ROOT)} is newer than {latest}")


# ==================================================================== schema --

def _resolve_ref(root: Dict[str, Any], ref: str) -> Dict[str, Any]:
    if not ref.startswith("#/"):
        raise AssertionError(f"unsupported $ref {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _ecma_pattern(pattern: str) -> "re.Pattern[str]":
    # ECMA-262 (JSON Schema): \d is [0-9] and $ matches only at the very end.
    pattern = pattern.replace(r"\d", "[0-9]")
    if pattern.endswith("$") and not pattern.endswith(r"\$"):
        pattern = pattern[:-1] + r"\Z"
    return re.compile(pattern)


def _same_json(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _is_type(value: Any, kind: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        # JSON Schema treats integral JSON numbers as integers; the runtime
        # manifest validator intentionally requires an actual JSON integer.
        "integer": (isinstance(value, int) and not isinstance(value, bool))
                   or (isinstance(value, float) and value.is_integer()),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }[kind]


def schema_errors(instance: Any, schema: Optional[Dict[str, Any]] = None, root: Optional[Dict[str, Any]] = None,
                  path: str = "$") -> List[str]:
    """The subset of JSON Schema 2020-12 the release manifest schema uses."""

    if root is None:
        root = json.loads(SCHEMA.read_text(encoding="utf-8"))
        schema = root
    errors: List[str] = []
    supported = {"$schema", "$id", "$ref", "title", "description", "$defs", "type", "const", "enum", "pattern",
                 "minLength", "maxLength", "minimum", "maximum", "required", "properties", "additionalProperties",
                 "minProperties", "minItems", "maxItems", "uniqueItems", "items", "allOf", "anyOf", "not",
                 "if", "then", "else", "propertyNames"}
    unknown = set(schema) - supported
    if unknown:
        raise AssertionError(f"schema keyword(s) {sorted(unknown)} at {path} are not understood by this test")
    if "$ref" in schema:
        # In 2020-12 a reference does not discard its sibling constraints.
        errors += schema_errors(instance, _resolve_ref(root, schema["$ref"]), root, path)
    if "type" in schema and not _is_type(instance, schema["type"]):
        return errors + [f"{path}: not {schema['type']}"]
    for subschema in schema.get("allOf", []):
        errors += schema_errors(instance, subschema, root, path)
    if "anyOf" in schema:
        # Evaluate every branch: do not swallow unsupported-keyword assertions
        # or short-circuit past one just because an earlier branch accepted.
        branches = [schema_errors(instance, subschema, root, path) for subschema in schema["anyOf"]]
        if all(branches):
            errors.append(f"{path}: no anyOf branch matched")
    if "not" in schema and not schema_errors(instance, schema["not"], root, path):
        errors.append(f"{path}: matches forbidden schema")
    if "if" in schema:
        branch = "else" if schema_errors(instance, schema["if"], root, path) else "then"
        if branch in schema:
            errors += schema_errors(instance, schema[branch], root, path)
    if isinstance(instance, dict) and "propertyNames" in schema:
        for key in instance:
            errors += schema_errors(key, schema["propertyNames"], root, f"{path}[{key!r}]")
    if "const" in schema and not _same_json(instance, schema["const"]):
        errors.append(f"{path}: not {schema['const']!r}")
    if "enum" in schema and not any(_same_json(instance, v) for v in schema["enum"]):
        errors.append(f"{path}: not one of {schema['enum']}")
    if isinstance(instance, str):
        if "pattern" in schema and not _ecma_pattern(schema["pattern"]).search(instance):
            errors.append(f"{path}: does not match {schema['pattern']}")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
    if _is_type(instance, "number"):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: less than {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: greater than {schema['maximum']}")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing {key}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                errors += schema_errors(value, properties[key], root, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unknown {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errors += schema_errors(value, schema["additionalProperties"], root, f"{path}.{key}")
        if len(instance) < schema.get("minProperties", 0):
            errors.append(f"{path}: fewer than {schema['minProperties']} properties")
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in instance}) != len(instance):
            errors.append(f"{path}: items are not unique")
        if "items" in schema:
            for index, value in enumerate(instance):
                errors += schema_errors(value, schema["items"], root, f"{path}[{index}]")
    return errors


def uncertified_compatibility(contract_version: int = 1) -> Dict[str, Any]:
    # Independent expectations. The default retains historical parser fixtures;
    # the generator's shipping default is explicitly the new restore contract.
    result = {
        "contract_version": contract_version,
        "automatic_apply": False,
        "requires_operator_initialization": True,
        "required_env": [],
        "predecessors": [],
        "automatic_rollback": {"strategy": "never"},
        "validation": None,
    }
    if contract_version == 1:
        result['state_domains'] = []
    else:
        assert contract_version == 2
        result.update(qualification_policy='standalone-cold-restore-v1',
            restore_procedure_id='standalone-inplace-cold-restore-v1', backup_inventory_version=2,
            host_observation_contract='standalone-runtime-v2', runtime_activation_contract='standalone-compose-direct-v1')
    return result


def sample_manifest(version: str = "v1.60.0", *, schema_version: int = SCHEMA_VERSION) -> Dict[str, Any]:
    manifest = {
        "schema_version": schema_version,
        "product": PRODUCT,
        "channel": "stable",
        "version": version,
        "source_sha": "0" * 40,
        "published_at": "2026-10-19T00:00:00Z",
        "minimum_version": "v1.51.0",
        "release_notes": f"https://github.com/{CANONICAL_REPOSITORY}/releases/tag/{version}",
        "signature": {"bundle": BUNDLE_ASSET, "identity": DEFAULT_TRUSTED_IDENTITY.replace("{version}", version),
                      "issuer": DEFAULT_TRUSTED_ISSUER},
        "components": {
            name: {"image": f"docker.io/{repo}:{version}{COMPONENT_TAG_SUFFIX.get(name, '')}",
                   "digest": fake_digest(name), "platforms": ["linux/amd64"]}
            for name, repo in COMPONENT_REPOSITORIES.items()
        },
        "helm": {"chart": HELM_CHART, "version": version[1:], "digest": fake_digest("chart")},
        "deployment": {"files": {"docker-compose.yml": "0" * 64}},
        "migration": {"requires_bootstrap": True, "rollback": "manual"},
    }
    if schema_version == 2:
        manifest["compatibility"] = uncertified_compatibility()
    return manifest


def _mutated(path: Tuple[str, ...], value: Any) -> Dict[str, Any]:
    manifest = sample_manifest()
    node = manifest
    for key in path[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return manifest


_DELETE = object()


def _accepted(manifest: Dict[str, Any]) -> bool:
    try:
        validate_manifest(manifest)
    except ManifestError:
        return False
    return True


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_it_is_a_2020_12_schema(self):
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(self.schema["additionalProperties"], False)

    def test_required_components_are_exactly_the_component_repositories(self):
        components = self.schema["properties"]["components"]
        self.assertEqual(components["required"], list(COMPONENT_REPOSITORIES))
        self.assertEqual(len(components["required"]), len(set(components["required"])))
        self.assertEqual(sorted(components["properties"]), sorted(COMPONENT_REPOSITORIES))
        self.assertEqual(components["additionalProperties"], False)
        for name, spec in components["properties"].items():
            self.assertEqual(spec, {"$ref": "#/$defs/component"}, name)

    def test_constants_match_the_validator(self):
        properties = self.schema["properties"]
        self.assertEqual(SCHEMA_VERSION, 2)
        self.assertEqual(properties["schema_version"], {"type": "integer", "enum": [1, 2]})
        self.assertEqual(properties["product"], {"const": PRODUCT})
        self.assertEqual(properties["channel"], {"enum": list(CHANNELS)})

    def test_every_required_key_is_required_by_the_validator_too(self):
        def resolve(node, value):
            if '$ref' in node:
                node = _resolve_ref(self.schema,node['$ref'])
            if 'anyOf' in node:
                choices = [choice for choice in node['anyOf'] if not schema_errors(value,choice,self.schema)]
                self.assertEqual(len(choices),1,'fixture must select exactly one closed contract branch')
                node = choices[0]
            return node

        objects = [(), ('signature',), ('helm',), ('deployment',), ('migration',),
                   ('compatibility',), ('compatibility','automatic_rollback')]
        objects += [('components',name) for name in COMPONENT_REPOSITORIES]
        for version in (1,2):
            document = sample_manifest()
            document['compatibility'] = uncertified_compatibility(version)
            def mutate(path,value):
                result = copy.deepcopy(document)
                node = result
                for key in path[:-1]: node = node[key]
                if value is _DELETE: del node[path[-1]]
                else: node[path[-1]] = value
                return result
            for path in objects:
                spec, value = self.schema, document
                for key in path:
                    spec = resolve(spec,value)['properties'][key]
                    value = value[key]
                spec = resolve(spec,value)
                for key in spec['properties']:
                    with self.subTest(contract=version,path='.'.join(path+(key,))):
                        required = key in spec.get('required',[]) or (path == () and key == 'compatibility')
                        changed = mutate(path+(key,),_DELETE)
                        self.assertEqual(_accepted(changed),not required)
                        self.assertEqual(schema_errors(changed)==[],not required)
                with self.subTest(contract=version,path='.'.join(path)+'.<unknown>'):
                    changed = mutate(path+('unexpected',),'x')
                    self.assertFalse(_accepted(changed))
                    self.assertNotEqual(schema_errors(changed),[])

    def test_the_sample_manifest_is_valid_both_ways(self):
        self.assertTrue(_accepted(sample_manifest()))
        self.assertEqual(schema_errors(sample_manifest()), [])

    def test_v1_manual_and_v2_uncertified_manifests_are_both_accepted(self):
        for version in (1, 2):
            with self.subTest(schema_version=version):
                manifest = sample_manifest(schema_version=version)
                self.assertEqual(validate_manifest(manifest), manifest)
                self.assertEqual(schema_errors(manifest), [])
                if version == 1:
                    self.assertNotIn("compatibility", manifest)
                else:
                    self.assertEqual(manifest["compatibility"], uncertified_compatibility())

    def test_compatibility_is_required_in_v2_and_forbidden_in_v1(self):
        legacy = sample_manifest(schema_version=1)
        legacy["compatibility"] = uncertified_compatibility()
        self.assertBothReject(legacy)
        self.assertBothReject(_mutated(("compatibility",), _DELETE))
        self.assertBothReject(_mutated(("schema_version",), 3))

    def test_whatever_the_validator_accepts_conforms_to_the_schema(self):
        mutations = [
            (("schema_version",), 1.0), (("schema_version",), 2.0), (("schema_version",), "1"),
            (("schema_version",), 1), (("schema_version",), 2), (("schema_version",), 3),
            (("product",), "Future-AGI"), (("channel",), "beta"), (("channel",), "Stable"),
            (("source_sha",), "A" * 40), (("source_sha",), "0" * 39), (("source_sha",), "0" * 40 + "\n"),
            (("published_at",), "2026-10-19 00:00:00Z"), (("published_at",), "2026-10-19T00:00:00Z\n"),
            (("published_at",), "2026-10-19T00:00:00+00:00"),
            (("minimum_version",), "v1.0.0-rc.1"), (("minimum_version",), "v01.0.0"),
            (("minimum_version",), "v1.0.0\n"), (("minimum_version",), " v1.0.0"),
            (("release_notes",), "http://example.com"), (("release_notes",), "https://example.com/notes"),
            (("signature", "bundle"), "a/b"), (("signature", "bundle"), "x.json\n"), (("signature", "bundle"), ".x"),
            (("signature", "identity"), ""), (("signature", "issuer"), "x"), (("signature", "identity"), 1),
            (("components", "backend", "digest"), "sha256:" + "A" * 64),
            (("components", "backend", "image"), f"docker.io/futureagi/future-agi:v1.60.0@{fake_digest('x')}"),
            (("components", "backend", "platforms"), ["linux/amd64", "linux/amd64"]),
            (("components", "backend", "platforms"), []), (("components", "backend", "platforms"), ["linux/s390x"]),
            (("components", "backend", "platforms"), "linux/amd64"),
            (("helm", "digest"), "sha256:" + "0" * 63), (("helm", "chart"), "https://ghcr.io/x"),
            (("helm", "version"), "v1.60.0"),
            (("deployment", "files"), {}), (("deployment", "files"), {"docker-compose.yml": "A" * 64}),
            (("deployment", "files"), {"docker-compose.yml": 0}),
            (("migration", "requires_bootstrap"), "true"), (("migration", "requires_bootstrap"), 1),
            (("migration", "rollback"), "auto"),
        ]
        for path, value in mutations:
            manifest = _mutated(path, value)
            with self.subTest(field=".".join(path), value=value):
                if _accepted(manifest):
                    self.assertEqual(schema_errors(manifest), [])

    # The validator accepts these; the schema it claims to mirror does not.

    def test_validator_rejects_non_ascii_digits_in_minimum_version_like_the_schema(self):
        manifest = _mutated(("minimum_version",), "v1.1\u0663.0")  # ARABIC-INDIC DIGIT THREE
        self.assertNotEqual(schema_errors(manifest), [])
        self.assertFalse(_accepted(manifest), "validate_manifest accepted minimum_version 'v1.1\\u0663.0' as v1.13.0")

    def test_parse_version_rejects_non_ascii_digits(self):
        for value in ("v1.1\u0663.0", "v1.2.3-rc.1\u0663", "v1\u0660.0.0"):
            with self.subTest(value=value):
                self.assertFalse(is_version(value), f"{value!r} is a version")
                with self.assertRaisesRegex(ManifestError, "invalid version"):
                    parse_version(value)

    def test_validator_rejects_non_ascii_digits_in_published_at_like_the_schema(self):
        manifest = _mutated(("published_at",), "\uff12\uff10\uff12\uff16-10-19T00:00:00Z")  # FULLWIDTH 2026
        self.assertNotEqual(schema_errors(manifest), [])
        self.assertFalse(_accepted(manifest))

    def test_validator_rejects_a_boolean_schema_version_like_the_schema(self):
        manifest = _mutated(("schema_version",), True)
        self.assertNotEqual(schema_errors(manifest), [])
        self.assertFalse(_accepted(manifest), "validate_manifest accepted schema_version true (True == 1 in Python)")

    def test_validator_rejects_a_non_string_source_sha_like_the_schema(self):
        manifest = _mutated(("source_sha",), int("1" * 40))
        self.assertNotEqual(schema_errors(manifest), [])
        self.assertFalse(_accepted(manifest), "validate_manifest accepted a 40-digit JSON integer as source_sha")

    # Rules both sides state; each mutation must be rejected by both.

    def assertBothReject(self, manifest: Dict[str, Any]) -> None:
        self.assertFalse(_accepted(manifest), "validate_manifest accepted it")
        self.assertNotEqual(schema_errors(manifest), [], "the schema accepted it")

    def test_each_component_image_must_name_its_own_repository(self):
        repositories = sorted(set(COMPONENT_REPOSITORIES.values()))
        for name, own in COMPONENT_REPOSITORIES.items():
            tag = component_tag(sample_manifest(), name)
            for repository in repositories:
                image = f"ghcr.io/{repository}:{tag}"
                with self.subTest(name=name, image=image):
                    manifest = _mutated(("components", name, "image"), image)
                    if repository == own:
                        self.assertTrue(_accepted(manifest))
                        self.assertEqual(schema_errors(manifest), [])
                    else:
                        self.assertBothReject(manifest)

    def test_the_gpu_suffix_belongs_to_serving_gpu_only(self):
        self.assertBothReject(_mutated(("components", "serving", "image"), "docker.io/futureagi/serving:v1.60.0-gpu"))
        self.assertBothReject(_mutated(("components", "serving_gpu", "image"), "docker.io/futureagi/serving:v1.60.0"))

    def test_a_stable_manifest_carries_no_prerelease(self):
        for version in ("v1.60.0-beta.1", "v1.60.0-rc.0"):
            manifest = sample_manifest(version)
            with self.subTest(version=version):
                self.assertBothReject(manifest)
                manifest["channel"] = "beta"
                self.assertTrue(_accepted(manifest))
                self.assertEqual(schema_errors(manifest), [])

    def test_the_helm_chart_names_a_host_and_a_path_without_tag_or_digest(self):
        for chart in ("oci://", "oci://ghcr.io", "oci://ghcr.io/", HELM_CHART + ":1.60.0",
                      HELM_CHART + "@sha256:" + "a" * 64, "oci://ghcr.io/Future-AGI/charts/futureagi"):
            with self.subTest(chart=chart):
                self.assertBothReject(_mutated(("helm", "chart"), chart))

    def test_deployment_file_paths_are_normalized_relative_paths(self):
        for path in (".", "..", "/etc/passwd", "../x", "a/../b", "a//b", "a/", "a/./b", "./a", "a\\b", " a", "a "):
            with self.subTest(path=path):
                self.assertBothReject(_mutated(("deployment", "files"), {path: "0" * 64}))
        for path in ("...", "a/.../b", ".env.example", "deploy/x.y.z"):
            with self.subTest(path=path):
                manifest = _mutated(("deployment", "files"), {path: "0" * 64})
                self.assertTrue(_accepted(manifest))
                self.assertEqual(schema_errors(manifest), [])

    def test_signature_strings_and_release_notes_are_trimmed(self):
        for path, value in ((("signature", "identity"), "   "), (("signature", "identity"), " x"),
                            (("signature", "issuer"), "x "), (("release_notes",), "https://example.com "),
                            (("release_notes",), "https://"), (("release_notes",), "https://example.com/a b")):
            with self.subTest(path=path, value=value):
                self.assertBothReject(_mutated(path, value))

    def test_no_schema_pattern_uses_backslash_d(self):
        # Validators that run patterns with Python's re match non-ASCII digits with \d.
        patterns = [value for key, value in walk(self.schema) if key == "pattern"]
        self.assertGreater(len(patterns), 10)
        for pattern in patterns:
            self.assertNotIn("\\d", pattern)


class SchemaEvaluatorTests(unittest.TestCase):
    """The JSON Schema subset evaluator the schema tests rely on, so a mis-evaluation cannot pass vacuously."""

    def errors(self, instance: Any, schema: Dict[str, Any]) -> List[str]:
        return schema_errors(instance, schema, schema)

    def test_all_of_applies_every_subschema(self):
        schema = {"allOf": [{"pattern": "^a"}, {"pattern": "b$"}]}
        self.assertEqual(self.errors("ab", schema), [])
        self.assertEqual(len(self.errors("ba", schema)), 2)

    def test_if_then_else_picks_the_branch_by_the_condition(self):
        schema = {"if": {"properties": {"k": {"const": "x"}}, "required": ["k"]},
                  "then": {"required": ["then"]}, "else": {"required": ["else"]}}
        self.assertEqual(self.errors({"k": "x", "then": 1}, schema), [])
        self.assertNotEqual(self.errors({"k": "x", "else": 1}, schema), [])
        self.assertEqual(self.errors({"k": "y", "else": 1}, schema), [])
        self.assertNotEqual(self.errors({"k": "y", "then": 1}, schema), [])
        self.assertEqual(self.errors({"then": 1}, {"if": schema["if"], "then": schema["then"]}), [])
        self.assertEqual(self.errors({}, {"if": schema["if"], "then": schema["then"]}), [])

    def test_property_names_are_checked_as_strings(self):
        schema = {"type": "object", "propertyNames": {"pattern": "^[a-z]+$"}}
        self.assertEqual(self.errors({"abc": 1}, schema), [])
        self.assertNotEqual(self.errors({"abc": 1, "A": 2}, schema), [])

    def test_patterns_follow_ecma_end_of_input(self):
        self.assertNotEqual(self.errors("abc\n", {"pattern": "^abc$"}), [])
        self.assertNotEqual(self.errors("١", {"pattern": "^\\d$"}), [])

    def test_any_of_accepts_one_or_more_matches_but_not_zero(self):
        schema = {"anyOf": [{"type": "string"}, {"const": "x"}, {"type": "null"}]}
        for value in ("x", "other", None):
            with self.subTest(value=value):
                self.assertEqual(self.errors(value, schema), [])
        for value in (0, False, {}, []):
            with self.subTest(value=value):
                self.assertNotEqual(self.errors(value, schema), [])
        self.assertNotEqual(self.errors("x", {"anyOf": schema["anyOf"], "maxLength": 0}), [])

    def test_not_inverts_validity_without_swallowing_helper_errors(self):
        schema = {"not": {"required": ["compatibility"]}}
        self.assertEqual(self.errors({}, schema), [])
        self.assertNotEqual(self.errors({"compatibility": None}, schema), [])
        self.assertNotEqual(self.errors(None, {"not": {}}), [])
        self.assertEqual(self.errors(None, {"not": {"not": {}}}), [])
        with self.assertRaisesRegex(AssertionError, "not understood"):
            self.errors({}, {"not": {"oneOf": [{}]}})

    def test_null_is_not_false_zero_empty_string_or_container(self):
        self.assertEqual(self.errors(None, {"type": "null"}), [])
        for value in (False, 0, "", [], {}):
            with self.subTest(value=value):
                self.assertNotEqual(self.errors(value, {"type": "null"}), [])

    def test_max_length_counts_characters_and_applies_only_to_strings(self):
        for value, accepted in (("", True), ("é\U0001d11e", True), ("abc", False), (0, True), (None, True)):
            with self.subTest(value=value):
                self.assertEqual(self.errors(value, {"maxLength": 2}) == [], accepted)
        self.assertNotEqual(self.errors("a", {"maxLength": 0}), [])

    def test_max_items_bounds_arrays_inclusively_not_other_types(self):
        for value, accepted in (([], True), ([1, 2], True), ([1, 2, 3], False), ("abc", True), ({}, True)):
            with self.subTest(value=value):
                self.assertEqual(self.errors(value, {"maxItems": 2}) == [], accepted)
        self.assertNotEqual(self.errors([None], {"maxItems": 0}), [])

    def test_numeric_bounds_are_inclusive_and_ignore_non_numbers(self):
        schema = {"minimum": 1, "maximum": 3}
        for value, accepted in ((0, False), (1, True), (1.5, True), (3, True), (4, False),
                                (True, True), (False, True), ("0", True), (None, True)):
            with self.subTest(value=value):
                self.assertEqual(self.errors(value, schema) == [], accepted)

    def test_integer_type_accepts_integral_numbers_but_never_booleans(self):
        for value, accepted in ((1, True), (1.0, True), (2.0, True), (1.5, False),
                                (True, False), (False, False), ("1", False)):
            with self.subTest(value=value):
                self.assertEqual(self.errors(value, {"type": "integer"}) == [], accepted)

    def test_reference_siblings_are_enforced_and_unknown_keywords_are_not_hidden(self):
        schema = {"$defs": {"name": {"type": "string"}}, "$ref": "#/$defs/name", "maxLength": 2}
        self.assertEqual(self.errors("ab", schema), [])
        self.assertNotEqual(self.errors("abc", schema), [])
        self.assertNotEqual(self.errors(None, schema), [])
        schema["oneOf"] = [{}]
        with self.assertRaisesRegex(AssertionError, "not understood"):
            self.errors("ab", schema)
        with self.assertRaisesRegex(AssertionError, "not understood"):
            self.errors("x", {"anyOf": [{}, {"oneOf": [{}]}]})

    def test_unknown_keywords_fail_loudly(self):
        with self.assertRaisesRegex(AssertionError, "not understood"):
            self.errors("x", {"oneOf": [{}]})

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "optional jsonschema is not installed")
    def test_v2_keyword_semantics_match_jsonschema(self):
        from jsonschema import Draft202012Validator
        schemas = [
            {"anyOf": [{"type": "null"}, {"type": "string", "maxLength": 2}]},
            {"not": {"required": ["compatibility"]}},
            {"type": "integer", "minimum": 0, "maximum": 2},
            {"maxItems": 2}, {"maxLength": 2}, {"minimum": 0, "maximum": 2},
            {"$defs": {"s": {"type": "string"}}, "$ref": "#/$defs/s", "maxLength": 2},
            json.loads(SCHEMA.read_text(encoding="utf-8")),
        ]
        values = [None, True, False, -1, 0, 1, 1.0, 1.5, 2, 3, "", "é\U0001d11e", "abc", [], [1, 2], [1, 2, 3],
                  {}, {"compatibility": None}, sample_manifest(schema_version=1), sample_manifest(),
                  _mutated(("compatibility", "validation"), {}), _mutated(("compatibility",), _DELETE)]
        for index, schema in enumerate(schemas):
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
            for value in values:
                with self.subTest(schema=index, value=value):
                    self.assertEqual(self.errors(value, schema) == [], validator.is_valid(value))


class ReleaseCompatibilityStaticTests(unittest.TestCase):
    """Synthetic release evidence only; no signature, topology or release is qualified."""

    def setUp(self):
        # Shared pure fixtures, not production observations or filesystem mocks.
        from deploy.tests import test_update_compatibility as fixtures
        self.fixtures = fixtures

    def test_uncertified_v2_and_manual_v1_cannot_automatically_apply(self):
        prepared, observations, controls, backup = self.fixtures.restore_context()
        positive = evaluate_automatic_apply(prepared, observations, controls, backup)
        self.assertTrue(positive.allowed, positive.reason_codes)
        self.assertFalse(positive.backup_pending)
        for schema_version in (1, 2):
            target = sample_manifest(prepared.manifest["version"], schema_version=schema_version)
            self.assertEqual(validate_manifest(target), target)
            candidate = self.fixtures.prepare(target)
            for boundary in ("before_backup", "after_backup", "before_env_commit", "before_rollout"):
                for evidence in ("pending", backup):
                    with self.subTest(schema_version=schema_version, boundary=boundary, backup=evidence == "pending"):
                        result = evaluate_automatic_apply(candidate, replace(observations, release_proof=b"",
                                                          boundary=boundary), controls, evidence)
                        self.assertFalse(result.allowed)
                        self.assertFalse(result.backup_pending)
                        self.assertEqual(result.reason_codes, (ReasonCode.RESTORE_NOT_CERTIFIED,))

    def test_forged_or_mismatched_proof_never_authorizes_automatic_apply(self):
        prepared, observations, controls, backup = self.fixtures.restore_context()
        self.assertTrue(evaluate_automatic_apply(prepared, observations, controls, backup).allowed)
        for raw in (b"", b'{}', observations.release_proof + b"\n"):
            with self.subTest(proof=raw[:20]):
                with self.assertRaises(CompatibilityError):
                    validate_release_proof(raw, prepared.manifest, repository=CANONICAL_REPOSITORY)
                result = evaluate_automatic_apply(prepared, replace(observations, release_proof=raw), controls, backup)
                self.assertFalse(result.allowed)
                self.assertEqual(result.reason_codes, (ReasonCode.UNCERTIFIED_RELEASE,))

    def test_rehashed_forgery_is_refused_by_host_and_generator(self):
        from argparse import Namespace
        from scripts.generate_update_manifest import apply_release_policy

        target, _, proof, raw = self.fixtures.restore_release()
        self.assertEqual(schema_errors(target), [])
        self.assertEqual(validate_release_proof(raw, target, repository=CANONICAL_REPOSITORY), proof)
        policy = {"schema_version": 1, "migration": copy.deepcopy(target["migration"]),
                  "compatibility": copy.deepcopy(target["compatibility"])}
        policy["compatibility"]["validation"] = None
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            policy_path, proof_path = directory / "policy.json", directory / "proof.json"
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            proof_path.write_bytes(raw)
            args = Namespace(schema_version=2, release_policy=str(policy_path), compatibility_proof=str(proof_path),
                             proof_run_id=proof["run_id"], proof_repository=CANONICAL_REPOSITORY)
            self.assertEqual(apply_release_policy(copy.deepcopy(target), args), target)
            # Recompute the byte hash so failures exercise content binding and
            # suite outcomes, rather than only the outer digest comparison.
            mutations = [
                (("repository",), "other/repository"), (("run_id",), proof["run_id"] + 1),
                (("tag",), "v1.99.0"), (("source_sha",), "f" * 40), (("inputs_hash",), "f" * 64),
                (("components", "backend", "digest"), fake_digest("forged")),
                (("predecessors", 0, "predecessor", "manifest_hash"), "f" * 64),
                (("predecessors", 0, "suites", "failure_restore"), "failed"),
                (("predecessors", 0, "suites", "migration_forward"), "skipped"),
            ]
            for path, value in mutations:
                with self.subTest(field=path):
                    damaged = copy.deepcopy(proof)
                    node = damaged
                    for key in path[:-1]:
                        node = node[key]
                    node[path[-1]] = value
                    changed_raw = json.dumps(damaged, sort_keys=True).encode("utf-8")
                    forged = copy.deepcopy(target)
                    forged["compatibility"]["validation"]["sha256"] = hashlib.sha256(changed_raw).hexdigest()
                    self.assertEqual(validate_manifest(forged), forged)
                    with self.assertRaises(CompatibilityError):
                        validate_release_proof(changed_raw, forged, repository=CANONICAL_REPOSITORY)
                    proof_path.write_bytes(changed_raw)
                    with self.assertRaises(ManifestError):
                        apply_release_policy(copy.deepcopy(target), args)
            args.compatibility_proof = None
            args.proof_run_id = None
            with self.assertRaises(ManifestError):
                apply_release_policy(copy.deepcopy(target), args)


# ==================================================================== README --

class ReadmeTests(unittest.TestCase):
    def setUp(self):
        self.text = README.read_text(encoding="utf-8")

    def settings_rows(self) -> Dict[str, str]:
        rows = re.findall(r"^\|\s*`(FUTURE_AGI_UPDATE_[A-Z0-9_]+)`\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$", self.text, re.M)
        keys = [key for key, _, _ in rows]
        self.assertEqual(len(keys), len(set(keys)), "a setting is documented twice")
        return {key: default for key, default, _ in rows}

    def test_every_setting_is_documented_and_nothing_else(self):
        self.assertEqual(sorted(self.settings_rows()), sorted(DEFAULTS))

    def test_documented_defaults_are_the_defaults(self):
        rows = self.settings_rows()
        for key, default in DEFAULTS.items():
            with self.subTest(key=key):
                cell = rows[key]
                if key == "FUTURE_AGI_UPDATE_MANIFEST_URL":
                    self.assertIn("latest GitHub release", cell)
                elif key == "FUTURE_AGI_UPDATE_TRUSTED_IDENTITY":
                    self.assertIn(UPDATE_WORKFLOW.name, cell)
                    self.assertIn("refs/tags/{version}", cell)
                elif key == "FUTURE_AGI_UPDATE_COSIGN_IMAGE":
                    self.assertIn(f"cosign:{parse_image_ref(DEFAULT_COSIGN_IMAGE).tag}", cell)
                    self.assertIn("digest", cell)
                elif default == "":
                    self.assertEqual(cell, "none")
                else:
                    self.assertEqual(cell, f"`{default}`")

    def test_the_example_update_conf_is_accepted(self):
        block = re.search(r"```ini\n# update\.conf\n(.*?)```", self.text, re.S).group(1)
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / CONFIG_FILE).write_text(block)
            values = read_config_file(Path(temporary) / CONFIG_FILE)
            settings = load_settings(Path(temporary), environ={})
        self.assertTrue(values)
        for key, value in values.items():
            self.assertEqual(settings.get(key), value)
            self.assertEqual(settings.sources[key], CONFIG_FILE)

    def test_the_backup_environment_it_documents_is_what_apply_sets(self):
        row = re.search(r"^\|\s*`FUTURE_AGI_UPDATE_BACKUP_COMMAND`\s*\|[^|]*\|(.*)\|\s*$", self.text, re.M)
        description = row.group(1)
        documented = {("FUTURE_AGI_UPDATE" + name if name.startswith("_") else name)
                      for name in re.findall(r"`((?:FUTURE_AGI_UPDATE)?_[A-Z_]+)`", description)}
        apply_source = APPLY.read_text(encoding="utf-8")
        set_by_apply = set(re.findall(r'"(FUTURE_AGI_UPDATE_[A-Z_]+)":', apply_source))
        self.assertTrue(documented)
        self.assertEqual(documented, set_by_apply)

    def test_exit_codes_match_the_cli_docstring(self):
        section = self.text.split("## When something fails", 1)[1].split("\n## ", 1)[0]
        readme = sorted(int(code) for code in re.findall(r"^\|\s*(\d+)\s*\|", section, re.M))
        self.assertEqual(readme, documented_exit_codes())

    def test_it_names_the_assets_and_the_workflow(self):
        self.assertIn(f"`{MANIFEST_ASSET}`", self.text)
        self.assertIn(UPDATE_WORKFLOW.relative_to(ROOT).as_posix(), self.text)
        self.assertIn(f"`{CONFIG_FILE}`", self.text)
        self.assertIn(f"`{STATE_DIR}/`", self.text)

    def test_files_it_points_at_exist(self):
        runtime = {f"{STATE_DIR}/", "deploy/.env.production", MANIFEST_ASSET, BUNDLE_ASSET, CONFIG_FILE,
                   # Private commit records are generated on the target filesystem, not shipped assets.
                   "<canonical-env-parent>/.futureagi-updates/env-commits/<target-id>/<operation-id>/",
                   "env-retained-<id>.json", "manifest.json"}
        tokens = sorted(set(re.findall(r"`([^`\s]+)`", self.text)))
        for token in tokens:
            # A path in the operator's home (~/.docker/config.json) is not the checkout's.
            if token in runtime or any(c in token for c in ":{}*") or token.startswith(("/", "-", "$", "~")):
                continue
            if "/" not in token and not re.search(r"\.(md|py|json|ya?ml|sh|txt)$", token):
                continue
            with self.subTest(path=token):
                relative = token[2:] if token.startswith("./") else token
                self.assertTrue(any((base / relative).exists() for base in (ROOT, UPDATE_DIR, WORKFLOWS)), token)


def documented_exit_codes() -> List[int]:
    docstring = ast.get_docstring(ast.parse(MAIN.read_text(encoding="utf-8")))
    section = docstring.split("Exit codes:", 1)[1]
    return sorted(int(code) for code in re.findall(r"^\s+(\d+)\s+\S", section, re.M))


class ExitCodeTests(unittest.TestCase):
    def test_the_docstring_lists_the_codes(self):
        self.assertEqual(documented_exit_codes(), [0, 2, 3, 4, 5, 130])

    def test_every_code_the_cli_returns_is_documented(self):
        produced = set()
        for node in ast.walk(ast.parse(MAIN.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Return) and node.value is not None:
                for sub in ast.walk(node.value):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, int) and not isinstance(sub.value, bool):
                        produced.add(sub.value)
        for node in ast.walk(ast.parse(APPLY.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "exit_code" for t in node.targets):
                produced.add(node.value.value)
        produced |= {int(code) for code in re.findall(r"^\s*exit (\d+)\s*$", BIN_UPDATE.read_text(), re.M)}
        self.assertTrue(produced)
        self.assertLessEqual(produced, set(documented_exit_codes()))
        self.assertEqual(produced, set(documented_exit_codes()), "a documented code is never produced")


# ===================================================== no leak into the app --

class NoUpdaterSettingsInAppConfigTests(unittest.TestCase):
    """The Standalone app reads .env as env_file: updater settings must never be there."""

    def assert_clean(self, relative: str) -> None:
        path = ROOT / relative
        self.assertTrue(path.is_file(), relative)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            self.assertNotIn("FUTURE_AGI_UPDATE_", line, f"{relative}:{number}: {line}")

    def test_env_example(self):
        self.assert_clean(".env.example")

    def test_env_reference(self):
        self.assert_clean("deploy/env-reference.toml")

    def test_production_env_example(self):
        self.assert_clean("deploy/.env.production.example")

    def test_compose_files(self):
        for relative in ("docker-compose.yml", "docker-compose.distributed.yml", "deploy/docker-compose.production.yml"):
            with self.subTest(file=relative):
                self.assert_clean(relative)

    def test_updater_files_are_not_deployment_files(self):
        listed = read_deployment_file_list(ROOT)
        self.assertNotIn(CONFIG_FILE, listed)
        self.assertFalse(any(p == STATE_DIR or p.startswith(STATE_DIR + "/") for p in listed))


# ============================================ review round 2: trust owner --

class TrustWorkflowGuardAdditions(unittest.TestCase):
    """Finding 2: the reusable workflow signs only in future-agi/future-agi, from this repository's own code.

    Fulcio names the called workflow file in the certificate whoever calls it,
    so a run called from another repository would carry the pinned identity.
    """

    def setUp(self):
        self.doc = load(UPDATE_WORKFLOW)
        self.jobs = self.doc["jobs"]
        self.claims = trust.workflow_claims(DEFAULT_TRUSTED_IDENTITY, "v1.60.0")

    def test_hosts_pin_this_repository_and_the_release_tag(self):
        self.assertEqual(self.claims, (CANONICAL_REPOSITORY, "refs/tags/v1.60.0"))

    def test_both_jobs_run_only_in_the_repository_hosts_pin(self):
        self.assertEqual(sorted(self.jobs), ["generate", "publish", "rehearse"])
        rehearsal = self.jobs["rehearse"]
        self.assertEqual(rehearsal["uses"], "./.github/workflows/update-compatibility-proof.yml")
        self.assertEqual(needs_of(rehearsal), ["generate"])
        self.assertEqual(rehearsal["permissions"], {"contents": "read"})
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertEqual(job.get("if"), f"github.repository == '{self.claims[0]}'")

    def test_every_checkout_takes_this_repository_at_the_release_tag(self):
        checkouts = 0
        for name, job in self.jobs.items():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    checkouts += 1
                    with self.subTest(job=name):
                        self.assertEqual(step["with"].get("repository"), CANONICAL_REPOSITORY)
                        self.assertEqual(step["with"].get("ref"), "refs/tags/" + INPUT_VERSION)
        self.assertEqual(checkouts, 2)

    def test_the_publish_verify_pins_the_claims_hosts_pin(self):
        run = one_step(self.jobs["publish"], runs_containing("cosign verify-blob"))["run"]
        self.assertIn('--certificate-github-workflow-repository "$CERT_REPOSITORY"', run)
        self.assertIn('--certificate-github-workflow-ref "$CERT_REF"', run)
        context = {"github.repository": CANONICAL_REPOSITORY, "inputs.version": "v1.60.0"}
        env = self.jobs["publish"]["env"]
        self.assertEqual((expand(env["CERT_REPOSITORY"], context), expand(env["CERT_REF"], context)), self.claims)


class TrustPublishVerifyAdditions(_StepHarness):
    """The sign-and-attach step verifies with exactly the repository and ref claims a host pins."""

    def setUp(self):
        super().setUp()
        if not shutil.which("cmp"):
            self.skipTest("cmp is needed")
        write_fake(self.bin, "gh", FAKE_GH)
        write_fake(self.bin, "cosign", FAKE_COSIGN)
        self.release = self.tmp / "release"
        self.release.mkdir()
        self.runner_temp = self.tmp / "runner"
        (self.runner_temp / "new").mkdir(parents=True)
        (self.runner_temp / "new" / MANIFEST_ASSET).write_bytes(b'{"version": "v1.60.0"}\n')

    def publish_verify(self) -> List[str]:
        job = self.doc["jobs"]["publish"]
        step = one_step(job, runs_containing("gh release upload"))
        context = {"inputs.version": self.version, "github.repository": CANONICAL_REPOSITORY,
                   "github.token": "fake-token"}
        env = self.base_env(
            **step_env(self.doc, job, step, context),
            GITHUB_REPOSITORY=CANONICAL_REPOSITORY, RUNNER_TEMP=str(self.runner_temp),
            GITHUB_STEP_SUMMARY=str(self.tmp / "summary.md"), FAKE_RELEASE=str(self.release),
            FAKE_SIGNER=DEFAULT_TRUSTED_IDENTITY.replace("{version}", self.version), FAKE_ISSUER=DEFAULT_TRUSTED_ISSUER,
            EXPECT_VERSION=self.version, EXPECT_REPOSITORY=CANONICAL_REPOSITORY,
        )
        result = run_step(step, env, self.tmp, self.scratch)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        verifies = [c for c in calls(self.log) if c[:2] == ["cosign", "verify-blob"]]
        self.assertEqual(len(verifies), 1)
        return verifies[0]

    def host_verify(self) -> List[str]:
        commands: List[List[str]] = []

        def runner(command, **_kwargs):
            commands.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        def probe(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, json.dumps({"gitVersion": "v3.1.3"}), "")

        work = self.tmp / "work"
        work.mkdir()
        (work / "manifest.json").write_bytes(b"{}")
        (work / "bundle.json").write_bytes(b"{}")
        fetched = trust.FetchedManifest("m.json", work, work / "manifest.json", b"{}",
                                        {"version": self.version, "signature": {"bundle": BUNDLE_ASSET}},
                                        bundle_path=work / "bundle.json")
        settings = load_settings(self.tmp, environ={})
        verification = Verifier(settings, runner=runner, probe=probe, environ={},
                                which=lambda name: "/fake/cosign" if name == "cosign" else None).verify(fetched)
        self.assertTrue(verification.verified, verification.error)
        return commands[0]

    def test_the_new_signature_is_verified_with_the_hosts_claims(self):
        published, host = self.publish_verify(), self.host_verify()
        for flag in ("--certificate-identity", "--certificate-oidc-issuer",
                     "--certificate-github-workflow-repository", "--certificate-github-workflow-ref"):
            with self.subTest(flag=flag):
                self.assertIn(flag, published)
                self.assertEqual(published[published.index(flag) + 1], host[host.index(flag) + 1])
        self.assertEqual(published[published.index("--certificate-github-workflow-repository") + 1],
                         CANONICAL_REPOSITORY)
        self.assertEqual(published[published.index("--certificate-github-workflow-ref") + 1],
                         f"refs/tags/{self.version}")



# ==================================================== round 4 (trust, CI) ==

# FAKE_GH, except that uploading the asset FAKE_FAIL_UPLOAD names fails (a
# GitHub 5xx, a lost runner) before anything is attached.
FAKE_GH_FAILING_UPLOAD = FAKE_GH.replace(
    'elif args[1] == "upload":\n',
    'elif args[1] == "upload":\n'
    '    if any(Path(a).name == os.environ.get("FAKE_FAIL_UPLOAD") for a in args[3:]):\n'
    '        print("HTTP 502: Bad Gateway", file=sys.stderr)\n'
    '        sys.exit(1)\n', 1)


class TrustRound4PublishOrderTests(_StepHarness):
    """S12: the bundle goes up first and the manifest last; a rerun finishes a run that stopped in between."""

    bundle_for = PublishStepTests.bundle_for
    assets = PublishStepTests.assets
    uploads = PublishStepTests.uploads
    signs = PublishStepTests.signs

    def setUp(self):
        super().setUp()
        if not shutil.which("cmp"):
            self.skipTest("cmp is needed")
        self.assertNotEqual(FAKE_GH_FAILING_UPLOAD, FAKE_GH)
        write_fake(self.bin, "gh", FAKE_GH_FAILING_UPLOAD)
        write_fake(self.bin, "cosign", FAKE_COSIGN)
        self.release = self.tmp / "release"
        self.release.mkdir()
        self.runner = self.tmp / "runner"
        (self.runner / "new").mkdir(parents=True)
        self.manifest = b'{"version": "v1.60.0"}\n'
        (self.runner / "new" / MANIFEST_ASSET).write_bytes(self.manifest)
        self.summary = self.tmp / "summary.md"
        self.signer = DEFAULT_TRUSTED_IDENTITY.replace("{version}", self.version)

    def run_publish(self, fail_upload: str = "", signer: Optional[str] = None) -> subprocess.CompletedProcess:
        job = self.doc["jobs"]["publish"]
        step = one_step(job, runs_containing("gh release upload"))
        context = {"inputs.version": self.version, "github.repository": CANONICAL_REPOSITORY,
                   "github.token": "fake-token"}
        env = self.base_env(
            **step_env(self.doc, job, step, context),
            GITHUB_REPOSITORY=CANONICAL_REPOSITORY, RUNNER_TEMP=str(self.runner),
            GITHUB_STEP_SUMMARY=str(self.summary), FAKE_RELEASE=str(self.release),
            FAKE_SIGNER=signer or self.signer, FAKE_ISSUER=DEFAULT_TRUSTED_ISSUER,
            EXPECT_VERSION=self.version, EXPECT_REPOSITORY=CANONICAL_REPOSITORY, FAKE_FAIL_UPLOAD=fail_upload,
        )
        return run_step(step, env, self.tmp, self.scratch)

    def uploaded(self) -> List[List[str]]:
        return [[Path(arg).name for arg in call[4:] if arg.startswith("new/")] for call in self.uploads()]

    def delete_command(self, asset: str) -> str:
        return f"gh release delete-asset {self.version} {asset} --repo {CANONICAL_REPOSITORY} --yes"

    def test_a_fresh_release_gets_the_bundle_first_and_the_manifest_last(self):
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.uploaded(), [[BUNDLE_ASSET], [MANIFEST_ASSET]])
        self.assertEqual(self.assets()[MANIFEST_ASSET], self.manifest)

    def test_a_failed_manifest_upload_leaves_no_manifest_and_a_rerun_attaches_it(self):
        result = self.run_publish(fail_upload=MANIFEST_ASSET)
        self.assertNotEqual(result.returncode, 0)
        # bin/update reads the manifest first: without it the release is
        # skipped, never reported as carrying an unverifiable signature.
        self.assertEqual(sorted(self.assets()), [BUNDLE_ASSET])
        bundle = self.assets()[BUNDLE_ASSET]
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.signs()), 1, "the rerun keeps the signature already attached")
        self.assertEqual(self.uploaded(), [[BUNDLE_ASSET], [MANIFEST_ASSET], [MANIFEST_ASSET]])
        self.assertEqual(self.assets(), {MANIFEST_ASSET: self.manifest, BUNDLE_ASSET: bundle})
        self.assertIn(f"{BUNDLE_ASSET} for {self.version} was already published and verifies the regenerated "
                      f"{MANIFEST_ASSET}: attached {MANIFEST_ASSET}", result.stdout)
        self.assertIn(self.signer, self.summary.read_text())

    def test_a_lone_bundle_is_checked_against_the_regenerated_manifest_with_the_pinned_identity(self):
        (self.release / BUNDLE_ASSET).write_text(self.bundle_for(self.manifest))
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        verifies = [c for c in calls(self.log) if c[:2] == ["cosign", "verify-blob"]]
        self.assertEqual(len(verifies), 1)
        self.assertEqual(verifies[0][-1], f"new/{MANIFEST_ASSET}")
        self.assertEqual(verifies[0][verifies[0].index("--certificate-identity") + 1], self.signer)
        self.assertEqual(self.signs(), [])
        self.assertEqual(self.uploaded(), [[MANIFEST_ASSET]])

    def test_a_lone_bundle_that_does_not_verify_names_its_delete_command(self):
        for name, bundle in (("other bytes", self.bundle_for(b"another manifest\n")),
                             ("other signer", self.bundle_for(self.manifest, identity="https://example.com/x")),
                             ("not a bundle", "{not json")):
            with self.subTest(name):
                for path in self.release.iterdir():
                    path.unlink()
                (self.release / BUNDLE_ASSET).write_text(bundle)
                result = self.run_publish()
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"it has {BUNDLE_ASSET}, which does not verify the regenerated {MANIFEST_ASSET}",
                              result.stdout)
                self.assertIn(f"Delete it ({self.delete_command(BUNDLE_ASSET)}) and rerun", result.stdout)
                self.assertEqual(self.uploads(), [])
                self.assertEqual(self.signs(), [])
                self.assertEqual(sorted(self.assets()), [BUNDLE_ASSET])

    def test_a_lone_manifest_names_its_delete_command(self):
        (self.release / MANIFEST_ASSET).write_bytes(self.manifest)
        result = self.run_publish()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"it has {MANIFEST_ASSET}, and hosts cannot verify it without {BUNDLE_ASSET}", result.stdout)
        self.assertIn(f"Delete it ({self.delete_command(MANIFEST_ASSET)}) and rerun", result.stdout)
        self.assertEqual(self.uploads(), [])
        self.assertEqual(sorted(self.assets()), [MANIFEST_ASSET])

    def test_every_upload_carries_exactly_one_asset(self):
        run = one_step(self.doc["jobs"]["publish"], runs_containing("gh release upload"))["run"]
        uploads = [line.strip() for line in run.splitlines() if "gh release upload" in line]
        self.assertEqual(len(uploads), 4)
        for line in uploads:
            with self.subTest(line=line):
                self.assertEqual(len(re.findall(r'"new/\$(?:MANIFEST|BUNDLE|proof)"', line)), 1)
        self.assertLess(run.rindex('gh release upload "$VERSION" "new/$BUNDLE"'),
                        run.rindex('gh release upload "$VERSION" "new/$MANIFEST"'))


class TrustRound4ReleaseImagesGuardTests(_StepHarness):
    """S13: `update-manifest` moves no tag, so any release's manifest can be retried from its own tag."""

    run_guard = ReleaseImagesGuardStepTests.run_guard
    outputs = ReleaseImagesGuardStepTests.outputs

    def setUp(self):
        super().setUp()
        if not shutil.which("jq"):
            self.skipTest("jq is needed")
        self.doc = load(RELEASE_IMAGES)
        write_fake(self.bin, "git", FAKE_GIT_TAGS)
        write_fake(self.bin, "curl", FAKE_RECORDER)
        self.output = self.tmp / "github-output"
        self.output.write_text("")

    def test_an_older_releases_manifest_is_retried_without_reading_the_tags(self):
        # No tag is listed (the listing would come back empty): the guard
        # never needs the newest release for the manifest.
        result = self.run_guard("workflow_dispatch", "refs/tags/v1.52.0", "v1.52.0", "update-manifest", tags="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.outputs()["components"]), ["update-manifest"])
        self.assertEqual(calls(self.log), [])
        # Still only from that release's own tag: the identity it is signed with.
        result = self.run_guard("workflow_dispatch", "refs/tags/v1.53.0", "v1.52.0", "update-manifest",
                                tags="v1.52.0 v1.53.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("run this workflow from the tag v1.52.0 to build 'update-manifest'", result.stdout)

    def test_a_component_that_moves_tags_is_still_refused_for_an_older_release(self):
        for components in ("update-manifest,helm-chart", "update-manifest,serving", "standalone,update-manifest"):
            with self.subTest(components=components):
                self.output.write_text("")
                other = [c for c in components.split(",") if c != "update-manifest"][0]
                result = self.run_guard("workflow_dispatch", "refs/tags/v1.52.0", "v1.52.0", components,
                                        tags="v1.52.0 v1.53.0")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"'{other}' can only be built for the newest release (v1.53.0)", result.stdout)
                self.assertNotIn("components=", self.output.read_text())

    def test_the_approve_job_lets_it_through_too(self):
        job = self.doc["jobs"]["approve"]
        step = job["steps"][0]
        env = self.base_env(
            **step_env(self.doc, job, step, {"inputs.version": "v1.52.0", "inputs.components": "update-manifest", "github.token": "fake-token"}),
            GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/tags/v1.52.0", GITHUB_REF_NAME="v1.52.0",
            GITHUB_SERVER_URL="https://github.com", GITHUB_REPOSITORY=CANONICAL_REPOSITORY,
            GITHUB_OUTPUT=str(self.output), FAKE_TAGS="v1.52.0 v1.53.0", FAKE_EXIT_CURL="97",
        )
        result = run_step(step, env, self.tmp, self.scratch)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('components=["update-manifest"]', self.output.read_text())



# ====================================== round 5: sign only what the build pushed --

TRUST_ROUND5_FAKE_INDEX_DOCKER = """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["docker"] + args) + "\\n")
if args[:3] != ["buildx", "imagetools", "inspect"] or args[4:] != ["--format", "{{json .Manifest}}"]:
    sys.exit(70)
flaky = Path(os.environ["FAKE_LOG"] + ".failed-once")
if args[3] == os.environ.get("FAKE_FAIL_ONCE") and not flaky.exists():
    flaky.write_text("")
    print("ERROR: " + args[3] + ": 503 Service Unavailable", file=sys.stderr)
    sys.exit(1)
indexes = json.loads(os.environ["FAKE_INDEXES"])
if args[3] not in indexes:
    print("ERROR: " + args[3] + ": not found", file=sys.stderr)
    sys.exit(1)
print(json.dumps(dict({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "size": 1},
                      **indexes[args[3]])))
"""


class TrustRound5BuildDigestsJobTests(_StepHarness):
    """T56: release-images.yml hands update-manifest the digest each manifest image's build pushed in this run.

    A build job of one image exposes it as an output; the `build` matrix keeps
    only one leg's output, so its images are taken from the per-architecture
    images each leg pushed (digests_<leg>_<arch> artifacts).
    """

    def setUp(self):
        super().setUp()
        if not shutil.which("jq"):
            self.skipTest("jq is needed")
        self.release_doc = load(RELEASE_IMAGES)
        self.jobs = self.release_doc["jobs"]
        self.job = self.jobs["build-digests"]
        self.step = one_step(self.job, lambda s: s.get("id") == "collect")
        write_fake(self.bin, "docker", TRUST_ROUND5_FAKE_INDEX_DOCKER)
        write_fake(self.bin, "sleep", FAKE_RECORDER)
        self.runner_temp = self.tmp / "runner"
        self.runner_temp.mkdir()
        self.output = self.tmp / "github-output"
        self.output.write_text("")
        self.legs = {item["name"]: item["image"] for item in self.jobs["build"]["strategy"]["matrix"]["include"]}
        self.digests = {name: fake_digest(f"index {name}") for name in COMPONENT_REPOSITORIES}
        self.arch_digests = {leg: [fake_digest(f"{leg} amd64"), fake_digest(f"{leg} arm64")] for leg in self.legs}

    # ---- helpers

    def leg_of(self, component: str) -> str:
        (leg,) = [name for name, image in self.legs.items() if image == COMPONENT_REPOSITORIES[component]]
        return leg

    def guard_name(self, component: str) -> str:
        job = COMPONENT_JOBS[component]
        return self.leg_of(component) if job == "build" else job

    def push_legs(self, legs=None):
        for leg in self.legs if legs is None else legs:
            directory = self.runner_temp / "legs" / leg
            directory.mkdir(parents=True, exist_ok=True)
            for digest in self.arch_digests[leg]:
                (directory / digest[len("sha256:"):]).write_text("")

    def indexes(self, **overrides):
        indexes = {}
        for component, repository in COMPONENT_REPOSITORIES.items():
            if COMPONENT_JOBS[component] != "build":
                continue
            leg = self.leg_of(component)
            indexes[f"docker.io/{repository}:{self.version}"] = {
                "digest": self.digests[component],
                "manifests": [{"mediaType": "application/vnd.oci.image.manifest.v1+json",
                               "digest": self.arch_digests[leg][i], "platform": {"os": "linux", "architecture": arch}}
                              for i, arch in enumerate(("amd64", "arm64"))],
            }
        indexes.update(overrides)
        return indexes

    def run_collect(self, components=None, single=None, indexes=None, **extra):
        components = sorted({self.guard_name(c) for c in COMPONENT_REPOSITORIES}) if components is None else components
        context = {"needs.guard.outputs.version": self.version,
                   "needs.guard.outputs.components": json.dumps(components)}
        for component, job in COMPONENT_JOBS.items():
            if job != "build":
                value = (single or {}).get(component, self.digests[component] if job in components else "")
                context[f"needs.{job}.outputs.digest"] = value
        env = self.base_env(**step_env(self.release_doc, self.job, self.step, context),
                            RUNNER_TEMP=str(self.runner_temp), GITHUB_OUTPUT=str(self.output),
                            FAKE_INDEXES=json.dumps(self.indexes() if indexes is None else indexes), **extra)
        return run_step(self.step, env, self.tmp, self.scratch)

    def recorded(self) -> Dict[str, str]:
        path = self.runner_temp / "release-build-digests" / "build-digests.txt"
        lines = [line for line in path.read_text().splitlines() if line and not line.startswith("#")]
        result = dict(line.split("=", 1) for line in lines)
        self.assertEqual(len(result), len(lines))
        return result

    # ---- the wiring

    def test_update_manifest_waits_for_it_and_takes_nothing_else(self):
        self.assertIn("build-digests", needs_of(self.jobs["update-manifest"]))
        self.assertEqual(self.jobs["update-manifest"]["with"], {"version": "${{ needs.guard.outputs.version }}"})
        for job in set(COMPONENT_JOBS.values()):
            self.assertIn(job, needs_of(self.job))
        self.assertEqual(self.job["if"], self.jobs["update-manifest"]["if"])
        self.assertEqual(self.job["permissions"], {"contents": "read"})

    def test_every_manifest_component_is_collected_from_the_job_that_built_it(self):
        run = self.step["run"]
        single = dict((c, (job, var)) for c, job, var in re.findall(r'^single (\w+) ([\w-]+) "\$(\w+)"$', run, re.M))
        legs = dict((c, (leg, repo)) for c, leg, repo in re.findall(r"^leg (\w+) ([\w-]+) ([\w/-]+)$", run, re.M))
        self.assertEqual(sorted([*single, *legs]), sorted(COMPONENT_REPOSITORIES))
        for component, (job, variable) in single.items():
            with self.subTest(component=component):
                self.assertEqual(COMPONENT_JOBS[component], job)
                self.assertEqual(self.step["env"][variable], f"${{{{ needs.{job}.outputs.digest }}}}")
                self.assertEqual(self.job["outputs"][component], f"${{{{ steps.collect.outputs.{component} }}}}")
        self.assertIn("digest", load(BUILD_MULTIARCH)["on"]["workflow_call"]["outputs"])
        downloads = {s["with"]["pattern"]: s["with"] for s in self.job["steps"]
                     if str(s.get("uses", "")).startswith("actions/download-artifact@")}
        for component, (leg, repository) in legs.items():
            with self.subTest(component=component):
                self.assertEqual(COMPONENT_JOBS[component], "build")
                self.assertEqual(self.legs[leg], repository)
                self.assertEqual(repository, COMPONENT_REPOSITORIES[component])
                self.assertEqual(downloads[f"digests_{leg}_*"],
                                 {"pattern": f"digests_{leg}_*", "path": f"${{{{ runner.temp }}}}/legs/{leg}",
                                  "merge-multiple": "true"})
                self.assertEqual(self.job["outputs"][component], f"${{{{ steps.collect.outputs.{component} }}}}")
        # The patterns name the artifacts the matrix legs upload: digests_<cache-scope><tag-suffix>_<arch>.
        self.assertEqual(self.jobs["build"]["with"]["cache-scope"], "${{ matrix.name }}")
        self.assertNotIn("tag-suffix", self.jobs["build"]["with"])
        upload = one_step(load(BUILD_MULTIARCH)["jobs"]["build"],
                          lambda s: str(s.get("uses", "")).startswith("actions/upload-artifact@"))
        self.assertEqual(upload["with"]["name"],
                         "digests_${{ inputs.cache-scope }}${{ inputs.tag-suffix }}_${{ matrix.arch }}")
        for action in [s["uses"] for s in self.job["steps"] if "uses" in s]:
            self.assertRegex(action, PINNED_ACTION_RE)

    def test_the_artifact_is_the_one_the_manifest_workflow_reads(self):
        upload = one_step(self.job, lambda s: str(s.get("uses", "")).startswith("actions/upload-artifact@"))
        self.assertEqual(upload["with"]["name"], "release-build-digests")
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/release-build-digests/build-digests.txt")
        self.assertIn('out="$RUNNER_TEMP/release-build-digests/build-digests.txt"', self.step["run"])
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        generate = load(UPDATE_WORKFLOW)["jobs"]["generate"]
        download = one_step(generate, lambda s: s.get("with", {}).get("pattern") == "release-build-digests")
        self.assertEqual(download["with"], {"pattern": "release-build-digests", "path": "${{ runner.temp }}/build",
                                            "merge-multiple": "true"})
        self.assertRegex(download["uses"], PINNED_ACTION_RE)
        steps = generate["steps"]
        resolve = one_step(generate, runs_containing("declare -A images"))
        self.assertLess(steps.index(download), steps.index(resolve))
        self.assertIn('build_digests="$RUNNER_TEMP/build/build-digests.txt"', resolve["run"])
        self.assertEqual(generate["permissions"], {"contents": "read"})

    # ---- the step

    def test_a_tag_push_records_every_component_from_its_build(self):
        self.push_legs()
        result = self.run_collect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.recorded(), self.digests)
        outputs = dict(line.split("=", 1) for line in self.output.read_text().splitlines())
        self.assertEqual(outputs, self.digests)
        # Only the matrix images are looked up; a single-image job's digest is its own output.
        inspected = sorted(call[4] for call in calls(self.log) if call[0] == "docker")
        self.assertEqual(inspected, sorted(f"docker.io/{self.legs[leg]}:{self.version}" for leg in self.legs))

    def test_a_matrix_tag_moved_to_other_images_is_refused(self):
        self.push_legs()
        for component in [c for c, job in COMPONENT_JOBS.items() if job == "build"]:
            with self.subTest(component=component):
                self.output.write_text("")
                reference = f"docker.io/{COMPONENT_REPOSITORIES[component]}:{self.version}"
                moved = {"digest": fake_digest("moved"), "manifests": [{"digest": fake_digest("someone else's")}]}
                result = self.run_collect(indexes=self.indexes(**{reference: moved}))
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"::error::{reference} is {fake_digest('moved')} now, which lists other images than "
                              f"the {self.leg_of(component)} build pushed in this run", result.stdout)
                self.assertNotIn(f"{component}={fake_digest('moved')}", self.output.read_text())

    def test_an_index_with_an_extra_or_a_missing_architecture_is_refused(self):
        self.push_legs()
        reference = f"docker.io/futureagi/frontend:{self.version}"
        good = self.indexes()[reference]
        for name, manifests in (("extra", good["manifests"] + [{"digest": fake_digest("riscv")}]),
                                ("missing", good["manifests"][:1]), ("not an index", None)):
            with self.subTest(name):
                index = {"digest": good["digest"]}
                if manifests is not None:
                    index["manifests"] = manifests
                result = self.run_collect(indexes=self.indexes(**{reference: index}))
                self.assertEqual(result.returncode, 1)
                self.assertIn("lists other images than the frontend build pushed", result.stdout)

    def test_a_single_image_job_without_a_digest_is_refused(self):
        self.push_legs()
        for component, job in COMPONENT_JOBS.items():
            if job == "build":
                continue
            for bad in ("", "latest", "sha256:" + "A" * 64):
                with self.subTest(component=component, digest=bad):
                    result = self.run_collect(single={component: bad})
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(f"::error::the {job} job built {component} in this run but handed over no digest",
                                  result.stdout)

    def test_a_matrix_image_without_its_artifacts_or_with_a_bad_one_is_refused(self):
        self.push_legs([leg for leg in self.legs if leg != "serving"])
        result = self.run_collect()
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::the serving build ran in this run, but none of its digests_serving_* artifacts "
                      "was found", result.stdout)
        self.push_legs(["serving"])
        (self.runner_temp / "legs" / "serving" / "not-a-digest").write_text("")
        result = self.run_collect()
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::unexpected digest file not-a-digest for serving", result.stdout)

    def test_only_what_this_run_built_is_recorded(self):
        # A dispatch that builds serving alone; another that builds nothing.
        self.push_legs(["serving"])
        result = self.run_collect(components=["serving", "update-manifest"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.recorded(), {"serving": self.digests["serving"]})
        self.log.unlink()
        result = self.run_collect(components=["update-manifest"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.recorded(), {})
        self.assertEqual(calls(self.log), [])
        # Never an empty artifact: a header line stays.
        self.assertTrue((self.runner_temp / "release-build-digests" / "build-digests.txt").read_text().startswith("# "))

    def test_a_transient_registry_error_is_retried(self):
        self.push_legs()
        result = self.run_collect(FAKE_FAIL_ONCE=f"docker.io/futureagi/frontend:{self.version}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.recorded()["frontend"], self.digests["frontend"])
        self.assertEqual([call for call in calls(self.log) if call[0] == "sleep"], [["sleep", "10"]])


class TrustRound5ResolveCase(_StepHarness):
    """The resolve step of release-update-manifest.yml with fake docker, gh and oras (as ResolveDigestsStepTests)."""

    def setUp(self):
        super().setUp()
        if bash_major() < 4:
            self.skipTest("the step needs bash 4 (declare -A)")
        if not shutil.which("jq") or not shutil.which("awk"):
            self.skipTest("jq and awk are needed")
        for name, body in (("docker", FAKE_DOCKER), ("gh", FAKE_GH), ("oras", FAKE_ORAS), ("sleep", FAKE_RECORDER)):
            write_fake(self.bin, name, body)
        self.release = self.tmp / "release"
        self.release.mkdir()
        self.runner = self.tmp / "runner"
        self.runner.mkdir()
        self.output = self.tmp / "github-output"
        self.output.write_text("")
        self.images = {
            name: f"docker.io/{repository}:{self.version}{COMPONENT_TAG_SUFFIX.get(name, '')}"
            for name, repository in COMPONENT_REPOSITORIES.items()
        }
        self.digests = {image: fake_digest(image) for image in self.images.values()}
        self.built = {name: self.digests[image] for name, image in self.images.items()}
        # The chart's images lock names only some of the images (it never names three of them).
        self.locked = ("backend", "frontend")
        self.write_lock(self.digests)

    def write_lock(self, digests: Dict[str, str]) -> None:
        (self.release / f"futureagi-images-{self.version[1:]}.txt").write_text("".join(
            f"{self.images[name]}@{digests[self.images[name]]}\n" for name in self.locked))

    def write_built(self, text: str) -> None:
        (self.runner / "build").mkdir(exist_ok=True)
        (self.runner / "build" / "build-digests.txt").write_text(text)

    def run_resolve(self, digests: Optional[Dict[str, str]] = None, event: Optional[str] = None):
        job = self.doc["jobs"]["generate"]
        step = one_step(job, runs_containing("declare -A images"))
        extra = {} if event is None else {"GITHUB_EVENT_NAME": event}
        env = self.base_env(
            **step_env(self.doc, job, step, {"inputs.version": self.version, "github.token": "fake-token"}),
            GITHUB_REPOSITORY=CANONICAL_REPOSITORY, RUNNER_TEMP=str(self.runner), GITHUB_OUTPUT=str(self.output),
            FAKE_RELEASE=str(self.release), FAKE_DIGESTS=json.dumps(self.digests if digests is None else digests),
            FAKE_CHART_DIGEST=fake_digest("chart"), EXPECT_VERSION=self.version,
            EXPECT_REPOSITORY=CANONICAL_REPOSITORY, **extra,
        )
        return run_step(step, env, self.tmp, self.scratch)

    def components(self) -> Dict[str, str]:
        lines = (self.runner / "components.txt").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines)


class TrustRound5SignOnlyWhatTheBuildPushedTests(TrustRound5ResolveCase):
    """T56: every component digest signed is the digest this release run's build pushed."""

    def header(self) -> str:
        return "# manifest component=digest its build pushed in this run (release-images.yml build-digests)\n"

    def test_a_tag_push_whose_digests_all_match_is_signed_without_a_warning(self):
        self.write_built(self.header() + "".join(f"{name}={digest}\n" for name, digest in self.built.items()))
        result = self.run_resolve(event="push")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.components(), {name: f"{image}@{self.digests[image]}"
                                             for name, image in self.images.items()})
        self.assertNotIn("::warning::", result.stdout)
        self.assertIn(f"chart={fake_digest('chart')}\n", self.output.read_text())

    def test_a_tag_moved_after_its_build_is_never_signed(self):
        # The scenario of T56: the tag points at another image by the time the manifest is made, including
        # for the images the chart's lock never names (standalone, the simulation runner, serving's -gpu tag).
        cases = [(name, "push") for name in COMPONENT_REPOSITORIES] + [("standalone", "workflow_dispatch")]
        for name, event in cases:
            with self.subTest(component=name, event=event):
                self.output.write_text("")
                self.write_built("".join(f"{n}={d}\n" for n, d in self.built.items()))
                digests = dict(self.digests)
                digests[self.images[name]] = fake_digest("moved")
                # A lock written after the move agrees with the tag: only the build digest tells.
                self.write_lock(digests)
                result = self.run_resolve(digests, event=event)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(f"::error::{self.images[name]} is {fake_digest('moved')} now, but this release "
                              f"run's build pushed {self.built[name]}", result.stdout)
                self.assertNotIn("chart=", self.output.read_text())
                self.assertNotIn(fake_digest("moved"), (self.runner / "components.txt").read_text())

    def test_a_tag_push_signs_nothing_its_build_digests_do_not_vouch_for(self):
        for missing in ("standalone", "backend"):  # one the lock does not name, one it does
            with self.subTest(missing=missing):
                self.write_built("".join(f"{n}={d}\n" for n, d in self.built.items() if n != missing))
                result = self.run_resolve(event="push")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"::error::this release run built {self.images[missing]}, but its build digest did not "
                              "reach this job (release-images.yml build-digests): rerun the failed jobs of this run",
                              result.stdout)
        (self.runner / "build" / "build-digests.txt").unlink()
        result = self.run_resolve(event="push")
        self.assertEqual(result.returncode, 1)
        self.assertIn("but its build digest did not reach this job", result.stdout)

    def test_a_run_that_built_nothing_warns_for_every_image_nothing_vouches_for(self):
        result = self.run_resolve(event="workflow_dispatch")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        warned = re.findall(r"^::warning::(\S+) is signed as (\S+), the digest its tag has now: this run did not "
                            r"build it and the chart's images lock does not name it, so only its revision label "
                            r"\(checked before signing\) vouches for it$",
                            result.stdout, re.M)
        self.assertEqual(sorted(warned), sorted((self.images[n], self.digests[self.images[n]])
                                                for n in COMPONENT_REPOSITORIES if n not in self.locked))

    def test_a_dispatch_that_built_one_image_holds_that_one_to_its_build(self):
        self.write_built(f"serving={self.built['serving']}\n")
        result = self.run_resolve(event="workflow_dispatch")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(f"::warning::{self.images['serving']} ", result.stdout)
        self.assertIn(f"::warning::{self.images['standalone']} ", result.stdout)
        self.write_built(f"serving={fake_digest('another build')}\n")
        result = self.run_resolve(event="workflow_dispatch")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"but this release run's build pushed {fake_digest('another build')}", result.stdout)

    def test_unexpected_build_digest_lines_are_refused(self):
        for line in ("standlone=" + self.built["standalone"], "standalone=latest", "standalone",
                     "standalone=" + self.built["standalone"] + " ", "STANDALONE=" + self.built["standalone"],
                     "serving-gpu=" + self.built["serving_gpu"], "a]b=" + self.built["standalone"],
                     "backend=" + self.built["backend"] + "\nbackend=" + self.built["backend"]):
            with self.subTest(line=line):
                self.write_built(self.header() + line + "\n")
                result = self.run_resolve(event="workflow_dispatch")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("::error::the release run's build digests have an unexpected line: ", result.stdout)
        # Comments, blank lines and a last line without its newline are fine.
        self.write_built(self.header() + "\n" + "\n".join(f"{n}={d}" for n, d in self.built.items()))
        result = self.run_resolve(event="push")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)



# ================================================== round 6: release digests --

TRUST_ROUND6_REVISION_LABEL = "org.opencontainers.image.revision"
# The manifest components a release builds for linux/amd64 only (release-images.yml arches).
TRUST_ROUND6_AMD64_ONLY = ("serving_gpu", "simulation_runner")

# `docker buildx imagetools inspect <ref> --format {{json .Manifest}}` (FAKE_DIGESTS: ref -> digest) and
# `... --format {{json .Image}}` (FAKE_IMAGES: image@digest -> what buildx prints for .Image).
TRUST_ROUND6_FAKE_DOCKER = """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["docker"] + args) + "\\n")
if args[:3] != ["buildx", "imagetools", "inspect"] or len(args) != 6 or args[4] != "--format":
    sys.exit(70)
flaky = Path(os.environ["FAKE_LOG"] + ".failed-once")
if args[3] == os.environ.get("FAKE_FAIL_ONCE") and not flaky.exists():
    flaky.write_text("")
    print("ERROR: " + args[3] + ": 503 Service Unavailable", file=sys.stderr)
    sys.exit(1)
if args[5] == "{{json .Manifest}}":
    digests = json.loads(os.environ.get("FAKE_DIGESTS", "{}"))
    if args[3] not in digests:
        print("ERROR: " + args[3] + ": not found", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"mediaType": "application/vnd.oci.image.index.v1+json", "digest": digests[args[3]], "size": 1}))
elif args[5] == "{{json .Image}}":
    images = json.loads(os.environ.get("FAKE_IMAGES", "{}"))
    if args[3] not in images:
        print("ERROR: " + args[3] + ": not found", file=sys.stderr)
        sys.exit(1)
    raw_images = json.loads(os.environ.get("FAKE_IMAGE_TEXT", "{}"))
    print(raw_images.get(args[3], json.dumps(images[args[3]])))
else:
    sys.exit(71)
"""

TRUST_ROUND6_FAKE_GIT = """
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["git"] + args) + "\\n")
if args != ["rev-parse", "HEAD"]:
    sys.exit(50)
print(os.environ["FAKE_COMMIT"])
"""


def trust_round6_config(arch: str, labels: Optional[Dict[str, str]]) -> Dict[str, Any]:
    """One platform's OCI image configuration, as buildx prints it."""

    config: Dict[str, Any] = {"created": "2026-10-21T00:00:00Z", "architecture": arch, "os": "linux",
                              "rootfs": {"type": "layers", "diff_ids": [fake_digest(arch)]}}
    if labels is not None:
        config["config"] = {"Env": ["PATH=/usr/local/bin:/usr/bin"], "Labels": labels}
    return config


def trust_round6_image(commit: Optional[str], arches=("amd64", "arm64"), **revisions: Optional[str]) -> Any:
    """What `--format {{json .Image}}` prints: the image itself for one platform, else platform -> image.

    Every platform's revision label is `commit` unless `revisions` names that architecture.
    """

    configs = {}
    for arch in arches:
        revision = revisions.get(arch, commit)
        labels = {"org.opencontainers.image.version": "v1.60.0"}
        if revision is not None:
            labels[TRUST_ROUND6_REVISION_LABEL] = revision
        configs[f"linux/{arch}"] = trust_round6_config(arch, labels)
    return next(iter(configs.values())) if len(configs) == 1 else configs


def trust_round6_revision_step(document: Dict[str, Any]) -> Dict[str, Any]:
    return one_step(document["jobs"]["generate"], runs_containing("{{json .Image}}"))


def trust_round6_header(path: Path) -> str:
    """The leading comment block of a workflow, as one line of text."""

    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        lines.append(line[1:].strip())
    return " ".join(" ".join(lines).split())


def trust_round6_comment_above(path: Path, job: str) -> str:
    """The comment block right above a job of a workflow, as one line of text."""

    lines = path.read_text(encoding="utf-8").splitlines()
    end = lines.index(f"  {job}:")
    start = end
    while start > 0 and lines[start - 1].startswith("  #"):
        start -= 1
    return " ".join(" ".join(line.strip()[1:].strip() for line in lines[start:end]).split())


class TrustRound6RevisionCase(_StepHarness):
    """The revision step of release-update-manifest.yml's generate job, with fake docker and git."""

    def setUp(self):
        super().setUp()
        if not shutil.which("jq"):
            self.skipTest("jq is needed")
        write_fake(self.bin, "docker", TRUST_ROUND6_FAKE_DOCKER)
        write_fake(self.bin, "git", TRUST_ROUND6_FAKE_GIT)
        write_fake(self.bin, "sleep", FAKE_RECORDER)
        self.runner = self.tmp / "runner"
        self.runner.mkdir()
        self.commit = "0123456789abcdef" * 2 + "01234567"
        self.references = {
            name: f"docker.io/{repository}:{self.version}{COMPONENT_TAG_SUFFIX.get(name, '')}@{fake_digest(name)}"
            for name, repository in COMPONENT_REPOSITORIES.items()
        }

    def arches(self, name: str) -> Tuple[str, ...]:
        return ("amd64",) if name in TRUST_ROUND6_AMD64_ONLY else ("amd64", "arm64")

    def images(self, **overrides: Any) -> Dict[str, Any]:
        images = {reference: trust_round6_image(self.commit, self.arches(name))
                  for name, reference in self.references.items()}
        for name, image in overrides.items():
            images[self.references[name]] = image
        return images

    def write_components(self) -> None:
        (self.runner / "components.txt").write_text(
            "".join(f"{name}={reference}\n" for name, reference in self.references.items()))

    def run_check(self, images: Optional[Dict[str, Any]] = None, commit: Optional[str] = None, **extra: str):
        self.write_components()
        job = self.doc["jobs"]["generate"]
        step = trust_round6_revision_step(self.doc)
        env = self.base_env(**step_env(self.doc, job, step, {"inputs.version": self.version}),
                            RUNNER_TEMP=str(self.runner), FAKE_COMMIT=self.commit if commit is None else commit,
                            FAKE_IMAGES=json.dumps(self.images() if images is None else images), **extra)
        return run_step(step, env, self.tmp, self.scratch)

    def refused(self, result: subprocess.CompletedProcess, name: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"::error::{self.references[name]} was not built from {self.version}: the "
                      f"{TRUST_ROUND6_REVISION_LABEL} label of each of its platforms must be {self.commit}, the tag's "
                      "commit, but it is ", result.stdout)
        self.assertIn(". Nothing was signed: find out what pushed that image to its tag", result.stdout)


def trust_round6_revision_corpus(commit: str) -> List[Tuple[str, Any, Any]]:
    """buildx configs and the exact revision map (None means a non-object input).

    Check every key as well as every value: dropping an invalid platform must
    never turn the signing predicate into a vacuous or partial success.
    """

    good = trust_round6_config("amd64", {TRUST_ROUND6_REVISION_LABEL: commit})
    cases = [("single", good, {"linux/amd64": commit})]
    for arches in (("amd64",), ("amd64", "arm64"), ("amd64", "arm64", "s390x")):
        cases.append(("index-" + "-".join(arches),
                      {f"linux/{arch}": trust_round6_config(arch, {TRUST_ROUND6_REVISION_LABEL: commit})
                       for arch in arches}, {f"linux/{arch}": commit for arch in arches}))
    bad = [("missing-config", {}, None), ("missing-labels", {"config": {}}, None),
           ("missing-revision", {"config": {"Labels": {}}}, None)]
    for name, value in (("null", None), ("string", "bad"), ("array", []), ("number", 7), ("bool", True)):
        bad.append((f"config-{name}", {"config": value}, None))
        bad.append((f"labels-{name}", {"config": {"Labels": value}}, None))
    for name, value in (("null", None), ("empty", ""), ("wrong", "f" * 40), ("short", commit[:12]),
                        ("uppercase", commit.upper()), ("space", commit + " "), ("newline", commit + "\n"),
                        ("number", 7), ("bool", True), ("array", [commit]), ("object", {"revision": commit})):
        bad.append((f"revision-{name}", {"config": {"Labels": {TRUST_ROUND6_REVISION_LABEL: value}}}, value))
    for name, config, revision in bad:
        single = {**good, **config}
        if name == "missing-config":
            del single["config"]
        cases.append((f"single-{name}", single, {"linux/amd64": revision}))
        cases.append((f"mixed-{name}", {"linux/amd64": good, "linux/arm64": config},
                      {"linux/amd64": commit, "linux/arm64": revision}))
    for name, value in (("null", None), ("string", "bad"), ("array", []), ("number", 7), ("bool", True)):
        cases.append((f"root-{name}", value, None))
        cases.append((f"only-platform-{name}", {"linux/arm64": value}, {"linux/arm64": None}))
        for first in (False, True):
            entries = [("linux/amd64", good), ("linux/arm64", value)]
            cases.append((f"mixed-platform-{name}-first-{first}", dict(reversed(entries) if first else entries),
                          {"linux/amd64": commit, "linux/arm64": None}))
    cases.append(("empty-index", {}, {}))
    return cases


class TrustRound6JqCompatibilityTests(TrustRound6RevisionCase):
    """Run with real jq on PATH (in particular 1.6 and 1.7.1), never a jq stub.

    The version is included in subtest diagnostics. Both the extracted workflow
    filter and the complete workflow step use that same executable.
    """

    def setUp(self):
        super().setUp()
        self.jq = shutil.which("jq")
        self.jq_version = subprocess.check_output([self.jq, "--version"], text=True).strip()
        filters = re.findall(r"jq (-[a-z]+) '(.*?)' <<<\"\$config\"", trust_round6_revision_step(self.doc)["run"], re.S)
        self.assertEqual(len(filters), 1)
        self.jq_options, self.revision_filter = filters[0]

    def test_actual_filter_and_step_preserve_and_validate_every_platform(self):
        for name, image, expected in trust_round6_revision_corpus(self.commit):
            with self.subTest(jq=self.jq_version, config=name):
                self.log.unlink(missing_ok=True)
                filtered = subprocess.run([self.jq, self.jq_options, self.revision_filter], input=json.dumps(image),
                                          capture_output=True, text=True, timeout=10)
                if expected is None:
                    self.assertEqual(filtered.returncode, 5, filtered.stderr)
                    self.assertEqual(filtered.stdout, "")
                else:
                    self.assertEqual(filtered.returncode, 0, filtered.stderr)
                    self.assertEqual(json.loads(filtered.stdout), expected)
                result = self.run_check(self.images(frontend=image))
                valid = bool(expected) and all(revision == self.commit for revision in expected.values())
                if valid:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(f"frontend: {self.references['frontend']} was built from {self.commit}", result.stdout)
                elif expected is None:
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn(f"::error::cannot read the image configuration of {self.references['frontend']}",
                                  result.stderr)
                else:
                    self.refused(result, "frontend")
                    found = result.stdout.split("but it is ", 1)[1].split(". Nothing was signed:", 1)[0]
                    self.assertEqual(json.loads(found), expected)
                sleeps = [call for call in calls(self.log) if call[0] == "sleep"]
                self.assertEqual(sleeps, [["sleep", str(n * 10)] for n in range(1, 6)]
                                 if expected is None else [])

    def test_malformed_json_is_retried_and_never_passes(self):
        good = json.dumps(trust_round6_image(self.commit))
        for raw in ("", " \n\t", "{", "not json", '{"linux/arm64":',
                    good + "\n" + good, "{}\n" + good, good + "\n{}", "null\n" + good):
            with self.subTest(jq=self.jq_version, raw=raw):
                self.log.unlink(missing_ok=True)
                result = self.run_check(FAKE_IMAGE_TEXT=json.dumps({self.references["frontend"]: raw}))
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"::error::cannot read the image configuration of {self.references['frontend']}",
                              result.stderr)
                self.assertEqual([call for call in calls(self.log) if call[0] == "sleep"],
                                 [["sleep", str(n * 10)] for n in range(1, 6)])


class TrustRound6RevisionCheckTests(TrustRound6RevisionCase):
    """U27, U31 (H5): an image is signed only when its revision label is the release tag's commit on every platform.

    That holds in every run, so a retry that can compare no build digest (a
    dispatch, or a run after the build artifacts expired) still signs only
    images built from the tag.
    """

    def test_it_runs_in_every_run_after_resolving_and_before_anything_is_generated_or_signed(self):
        job = self.doc["jobs"]["generate"]
        steps = job["steps"]
        check = trust_round6_revision_step(self.doc)
        resolve = one_step(job, runs_containing("declare -A images"))
        generate = one_step(job, runs_containing("scripts/generate_update_manifest.py"))
        upload = one_step(job, lambda s: s.get("with", {}).get("name") == "update-candidate")
        self.assertLess(steps.index(resolve), steps.index(check))
        self.assertLess(steps.index(check), steps.index(generate))
        self.assertLess(steps.index(generate), steps.index(upload))
        self.assertNotIn("if", check)
        self.assertNotIn("continue-on-error", check)
        self.assertEqual(check["shell"], "bash")
        self.assertEqual(check["env"], {"VERSION": INPUT_VERSION})
        self.assertNotIn("${{", check["run"])
        self.assertIn("set -euo pipefail", check["run"])
        # The commit it compares with is the one the manifest names as its source, at the release tag.
        self.assertIn("commit=$(git rev-parse HEAD)", check["run"])
        self.assertIn('--source-sha "$(git rev-parse HEAD)"', generate["run"])
        checkout = one_step(job, lambda s: str(s.get("uses", "")).startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "refs/tags/${{ inputs.version }}")
        self.assertLess(steps.index(checkout), steps.index(check))
        # It reads every component the resolve step wrote, as image@digest: exactly what is signed.
        self.assertIn('done 3< "$RUNNER_TEMP/components.txt"', check["run"])
        self.assertIn('printf \'%s=%s@%s\\n\' "$name" "$image" "$digest" >> "$RUNNER_TEMP/components.txt"',
                      resolve["run"])
        self.assertEqual(needs_of(self.doc["jobs"]["publish"]), ["generate", "rehearse"])

    def test_images_built_from_the_tags_commit_pass(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        inspected = [call for call in calls(self.log) if call[0] == "docker"]
        self.assertEqual(sorted(call[4] for call in inspected), sorted(self.references.values()))
        for call in inspected:
            self.assertEqual(call[1:4] + call[5:], ["buildx", "imagetools", "inspect", "--format", "{{json .Image}}"])
        self.assertIn(["git", "rev-parse", "HEAD"], calls(self.log))
        for name, reference in self.references.items():
            with self.subTest(component=name):
                self.assertIn(f"{name}: {reference} was built from {self.commit}\n", result.stdout)
        self.assertNotIn("::error::", result.stdout + result.stderr)
        self.assertEqual([call for call in calls(self.log) if call[0] == "sleep"], [])

    def test_an_image_built_from_another_commit_is_never_signed(self):
        other = "f" * 40
        for name in COMPONENT_REPOSITORIES:
            with self.subTest(component=name):
                result = self.run_check(self.images(**{name: trust_round6_image(other, self.arches(name))}))
                self.refused(result, name)
                self.assertIn(other, result.stdout)
        # One platform of an index is enough.
        for arch in ("amd64", "arm64"):
            with self.subTest(platform=arch):
                result = self.run_check(self.images(frontend=trust_round6_image(self.commit, **{arch: other})))
                self.refused(result, "frontend")

    def test_an_image_without_the_label_is_never_signed(self):
        no_labels = trust_round6_image(self.commit)
        no_labels["linux/arm64"] = trust_round6_config("arm64", None)
        other_labels = trust_round6_image(self.commit)
        other_labels["linux/amd64"]["config"]["Labels"] = {"org.opencontainers.image.version": self.version}
        cases = {
            "no labels on one platform": ("frontend", no_labels),
            "no revision label": ("frontend", other_labels),
            "an empty revision label": ("backend", trust_round6_image(self.commit, arm64="")),
            "a null revision label": ("serving", trust_round6_image(self.commit, amd64=None)),
            "an index with no platform": ("standalone", {}),
            "a platform that is no image": ("code_executor", {"linux/amd64": "x", "linux/arm64": None}),
            "one platform without labels": ("serving_gpu", trust_round6_config("amd64", None)),
            "one platform without the label": ("simulation_runner", trust_round6_image(None, ("amd64",))),
        }
        for case, (name, image) in cases.items():
            with self.subTest(case):
                self.refused(self.run_check(self.images(**{name: image})), name)

    def test_a_label_that_is_not_exactly_the_commit_is_refused(self):
        for label in (self.commit.upper(), self.commit[:12], self.commit + " ", " " + self.commit,
                      self.commit + "\nlinux/amd64=" + self.commit, f"{self.commit}-dirty", "unknown"):
            with self.subTest(label=label):
                self.refused(self.run_check(self.images(fi_collector=trust_round6_image(label))), "fi_collector")

    def test_a_transient_registry_error_is_retried_and_a_lasting_one_fails(self):
        result = self.run_check(FAKE_FAIL_ONCE=self.references["agentcc_gateway"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([call for call in calls(self.log) if call[0] == "sleep"], [["sleep", "10"]])
        self.log.unlink()
        images = self.images()
        del images[self.references["serving_gpu"]]
        result = self.run_check(images)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"::error::cannot read the image configuration of {self.references['serving_gpu']}",
                      result.stderr)
        self.assertEqual([call for call in calls(self.log) if call[0] == "sleep"],
                         [["sleep", str(10 * attempt)] for attempt in range(1, 6)])
        # Something that is no image configuration at all is not taken for one either.
        for printed in (None, [], "image"):
            with self.subTest(printed=printed):
                self.log.unlink()
                result = self.run_check(self.images(backend=printed))
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"::error::cannot read the image configuration of {self.references['backend']}",
                              result.stderr)

    def test_an_unexpected_commit_fails_before_any_image_is_read(self):
        for commit in ("", "HEAD", "0" * 39, "0" * 64, "A" * 40):
            with self.subTest(commit=commit):
                if self.log.exists():
                    self.log.unlink()
                result = self.run_check(commit=commit)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"::error::unexpected commit '{commit}' for the tag {self.version}", result.stdout)
                self.assertEqual([call for call in calls(self.log) if call[0] == "docker"], [])


class TrustRound6RevisionSourceTests(unittest.TestCase):
    """The label the check reads is the commit every manifest image's build labels it with."""

    def test_every_component_is_built_from_the_tag_with_the_revision_label(self):
        check = trust_round6_revision_step(load(UPDATE_WORKFLOW))
        self.assertIn(f'.config.Labels["{TRUST_ROUND6_REVISION_LABEL}"]', check["run"])
        jobs = load(RELEASE_IMAGES)["jobs"]
        legs = {item["image"]: item for item in jobs["build"]["strategy"]["matrix"]["include"]}
        for component, job_name in COMPONENT_JOBS.items():
            with self.subTest(component=component):
                job = jobs[job_name]
                self.assertEqual(job["uses"], "./.github/workflows/build-image-multiarch.yml")
                self.assertEqual(job["with"]["ref"], "${{ github.ref }}")
                dockerfile = job["with"]["dockerfile"]
                if job_name == "build":
                    dockerfile = legs[COMPONENT_REPOSITORIES[component]]["dockerfile"]
                text = (ROOT / dockerfile).read_text(encoding="utf-8")
                self.assertIn("ARG REVISION", text)
                self.assertIn(f'{TRUST_ROUND6_REVISION_LABEL}="${{REVISION}}"', text)
        multiarch = load(BUILD_MULTIARCH)["jobs"]
        source = one_step(multiarch["prepare"], lambda s: s.get("id") == "source")
        self.assertIn("revision=$(git rev-parse HEAD)", source["run"])
        checkout = one_step(multiarch["prepare"], lambda s: str(s.get("uses", "")).startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "${{ inputs.ref }}")
        build = one_step(multiarch["build"], lambda s: "build-args" in (s.get("with") or {}))
        self.assertIn("REVISION=${{ needs.prepare.outputs.revision }}", build["with"]["build-args"])


class TrustRound6RetryPathTests(TrustRound5ResolveCase):
    """U27, U31: what a retry does, and what the workflows say about it."""

    def test_the_manifest_workflow_header_documents_both_retry_paths(self):
        header = trust_round6_header(UPDATE_WORKFLOW)
        for text in (
            "signs an image only when it was built from the release tag's commit: the image's "
            "org.opencontainers.image.revision label (build-image-multiarch.yml sets it to the commit it built) must "
            "be that commit on every platform, or the run fails before anything is signed.",
            "Rerun the failed jobs of that release-images.yml run.",
            "When only update-manifest failed, this works for 30 days (the release-build-digests artifact).",
            "When a build failed too, its build-digests job runs again and reads the digests_* artifacts of the build "
            "legs, which are kept for a day: after that it fails, so use the next way.",
            "At any time, run it from the tag: dispatch release-images.yml from the tag vX.Y.Z with components "
            "`update-manifest`, or start this workflow by hand",
            "each image's revision label must be the tag's commit, and each image the chart's images lock names must "
            "have its locked digest. An image the lock does not name is signed with a warning that only its revision "
            "label vouches for it.",
            "checks that each image was built from the tag's commit (its revision label)",
        ):
            with self.subTest(text=text[:40]):
                self.assertIn(text, header)
        self.assertNotIn("nothing else vouches", header)
        self.assertNotIn("(within 30 days)", header)

    def test_release_images_documents_the_same_retry_window(self):
        manifest = trust_round6_comment_above(RELEASE_IMAGES, "update-manifest")
        self.assertIn("That works for 30 days when only this job failed, and for a day when a build failed too "
                      "(build-digests runs again and needs the legs' artifacts). Otherwise, at any time, dispatch this "
                      "workflow from that release's tag with components `update-manifest`", manifest)
        self.assertIn("as every run does, that each image's revision label is the tag's commit.", manifest)
        self.assertNotIn("can check the images only against the chart's images lock", manifest)
        digests = trust_round6_comment_above(RELEASE_IMAGES, "build-digests")
        self.assertIn("A rerun of this job more than a day after the builds finds no leg artifacts and fails: then "
                      "dispatch this workflow from the tag with components `update-manifest`", digests)

    def test_build_digests_without_its_leg_artifacts_says_how_to_attach_the_manifest(self):
        document = load(RELEASE_IMAGES)
        job = document["jobs"]["build-digests"]
        step = one_step(job, lambda s: s.get("id") == "collect")
        context = {"needs.guard.outputs.version": self.version,
                   "needs.guard.outputs.components": json.dumps(["frontend", "update-manifest"])}
        for name in ("standalone", "backend", "serving-gpu", "code-executor", "simulation-runner"):
            context[f"needs.{name}.outputs.digest"] = ""
        env = self.base_env(**step_env(document, job, step, context), RUNNER_TEMP=str(self.runner),
                            GITHUB_OUTPUT=str(self.output))
        result = run_step(step, env, self.tmp, self.scratch)
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error::the frontend build ran in this run, but none of its digests_frontend_* artifacts was "
                      "found. They are kept for a day: to attach the manifest after that, dispatch release-images.yml "
                      f"from the tag {self.version} with components update-manifest\n", result.stdout)

    def test_a_push_run_whose_build_digests_are_gone_is_told_how_to_retry(self):
        result = self.run_resolve(event="push")
        self.assertEqual(result.returncode, 1)
        self.assertIn("but its build digest did not reach this job (release-images.yml build-digests): rerun the "
                      "failed jobs of this run, or, once its artifacts have expired, dispatch release-images.yml from "
                      f"the tag {self.version} with components update-manifest\n", result.stdout)

    def test_a_dispatch_signs_an_image_the_lock_does_not_name_only_when_built_from_the_tag(self):
        # T56 residual: a run that built nothing, and an image the chart's lock does not name whose tag was
        # moved to another build. The resolve step lets it through with a warning; the revision check refuses it.
        write_fake(self.bin, "docker", TRUST_ROUND6_FAKE_DOCKER)
        write_fake(self.bin, "git", TRUST_ROUND6_FAKE_GIT)
        commit = "0123456789abcdef" * 2 + "01234567"
        moved = dict(self.digests)
        moved[self.images["standalone"]] = fake_digest("moved")
        result = self.run_resolve(moved, event="workflow_dispatch")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"::warning::{self.images['standalone']} is signed as {fake_digest('moved')}, the digest its "
                      "tag has now: this run did not build it and the chart's images lock does not name it, so only "
                      "its revision label (checked before signing) vouches for it\n", result.stdout)
        step = trust_round6_revision_step(self.doc)
        job = self.doc["jobs"]["generate"]
        images = {}
        for line in (self.runner / "components.txt").read_text().splitlines():
            name, reference = line.split("=", 1)
            arches = ("amd64",) if name in TRUST_ROUND6_AMD64_ONLY else ("amd64", "arm64")
            built = "e" * 40 if name == "standalone" else commit
            images[reference] = trust_round6_image(built, arches)
        env = self.base_env(**step_env(self.doc, job, step, {"inputs.version": self.version}),
                            RUNNER_TEMP=str(self.runner), FAKE_COMMIT=commit, FAKE_IMAGES=json.dumps(images))
        result = run_step(step, env, self.tmp, self.scratch)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"::error::{self.images['standalone']}@{fake_digest('moved')} was not built from "
                      f"{self.version}", result.stdout)
        # The same run, with the image built from the tag, goes on to generate and sign.
        images[f"{self.images['standalone']}@{fake_digest('moved')}"] = trust_round6_image(commit)
        env["FAKE_IMAGES"] = json.dumps(images)
        result = run_step(step, env, self.tmp, self.scratch)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ClosingDeploymentFileCoverageTests(unittest.TestCase):
    """A change to a file a release hashes, or to a workflow whose contract
    these tests pin, runs update-ci; and git checks those files out as LF,
    so a Windows clone used from WSL matches the signed digests."""

    def entries(self) -> List[str]:
        return read_deployment_file_list(ROOT)

    def ci_paths(self, event: str) -> List[str]:
        text = (ROOT / ".github/workflows/update-ci.yml").read_text(encoding="utf-8")
        block = re.search(rf"^  {event}:\n(?:    .*\n)*?    paths:\n((?:      .*\n)+)", text, re.M)
        self.assertIsNotNone(block, event)
        return [m.group(1) for m in re.finditer(r'^      - "([^"]+)"$', block.group(1), re.M)]

    @staticmethod
    def covered(path: str, patterns: List[str], directory: bool) -> bool:
        probe = f"{path}/any-file" if directory else path
        for pattern in patterns:
            if pattern.endswith("/**") and probe.startswith(pattern[:-2]):
                return True
            if pattern == probe:
                return True
        return False

    def test_update_ci_runs_for_every_deployment_file(self):
        for event in ("pull_request", "push"):
            paths = self.ci_paths(event)
            for entry in self.entries():
                with self.subTest(event=event, entry=entry):
                    self.assertTrue(self.covered(entry, paths, (ROOT / entry).is_dir()), paths)

    def test_update_ci_runs_for_the_workflows_whose_contracts_these_tests_pin(self):
        for event in ("pull_request", "push"):
            paths = self.ci_paths(event)
            for workflow in ("release-images.yml", "release-update-manifest.yml", "build-image-multiarch.yml",
                             "helm-release.yml", "update-ci.yml"):
                with self.subTest(event=event, workflow=workflow):
                    self.assertIn(f".github/workflows/{workflow}", paths)

    def test_update_ci_e2e_matrix_and_native_jobs_run_every_e2e_suite(self):
        text = (ROOT / ".github/workflows/update-ci.yml").read_text(encoding="utf-8")
        listed = re.findall(r"^        suite: \[([^\]]*)\]$", text, re.M)
        self.assertEqual(len(listed), 1, "the fake-image e2e job names its suites in one matrix list")
        matrix = [name.strip() for name in listed[0].split(",")]
        self.assertEqual(len(matrix), len(set(matrix)), "no duplicated matrix suite can hide a missing one")
        # Automatic tests use real published images and vendor backup fixtures
        # have a different opt-in. Running either in the fake-image matrix would
        # discover skipped tests, not execute their contract. Pin BOTH routing
        # and the actual native launchers, and keep complete file coverage.
        self.assertEqual(set(matrix), {"standalone", "distributed", "faults"})
        files = {path.name[len("test_update_e2e_"):-len(".py")]
                 for path in (ROOT / "deploy/tests").glob("test_update_e2e_*.py")}
        self.assertEqual(set(matrix) | {"automatic", "backup"}, files)
        self.assertIn('-p "test_update_e2e_${SUITE}.py"', text)
        for job, launcher in (("automatic-upgrade", "acceptance.py --mode upgrade"),
                              ("automatic-a-b-c", "acceptance.py --mode a-b-c"),
                              ("backup", "backup_ci.py")):
            match = re.search(r"^  " + re.escape(job) + r":\n(.*?)(?=^  [a-zA-Z][a-zA-Z0-9_-]*:|\Z)", text, re.M | re.S)
            self.assertIsNotNone(match, "missing native job " + job)
            block = match.group(1)
            self.assertIn("runs-on: ubuntu-24.04", block)
            self.assertIn("python -I deploy/tests/update_e2e/" + launcher, block)
            self.assertIn("set -euo pipefail", block)
            self.assertIn("--out ", block)
            if job.startswith("automatic-"):
                self.assertIn("if: always()", block)
        # Verify the native entrypoints reach the actual suite and opt it in;
        # merely retaining a job name is not test execution coverage.
        acceptance = (ROOT / "deploy/tests/update_e2e/acceptance.py").read_text()
        backup = (ROOT / "deploy/tests/update_e2e/backup_ci.py").read_text()
        self.assertIn("test_update_e2e_automatic.py", acceptance)
        self.assertIn("'native_backup.py'", backup)
        self.assertIn("FAGI_UPDATE_BACKUP_E2E='1'", backup)
        native = (ROOT / "deploy/tests/update_e2e/native_backup.py").read_text()
        self.assertIn("from deploy.tests import test_update_e2e_backup as fixture", native)
        self.assertIn("DedicatedBackupRestoreTests=fixture.DedicatedBackupRestoreTests", native)
        self.assertIn("__unittest_skip__", native)

    def test_update_ci_jobs_run_where_python_3_9_exists(self):
        text = (ROOT / ".github/workflows/update-ci.yml").read_text(encoding="utf-8")
        runners = re.findall(r"^    runs-on: (.+)$", text, re.M)
        self.assertTrue(runners)
        self.assertEqual(set(runners), {"ubuntu-24.04"})

    def test_git_checks_out_bin_update_and_every_deployment_file_as_lf(self):
        if shutil.which("git") is None or not (ROOT / ".git").exists():
            self.skipTest("needs a git checkout")
        for entry in ["bin/update", "deploy/update/manifest.py", *self.entries()]:
            probe = f"{entry}/any-file" if (ROOT / entry).is_dir() else entry
            with self.subTest(entry=entry):
                result = subprocess.run(["git", "-C", str(ROOT), "check-attr", "eol", "--", probe],
                                        capture_output=True, text=True, check=True)
                self.assertTrue(result.stdout.rstrip().endswith(": eol: lf"), result.stdout)


if __name__ == "__main__":
    unittest.main()
