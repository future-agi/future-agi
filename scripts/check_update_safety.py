#!/usr/bin/env python3
"""Honest-mistake PR checks for the supported unattended Standalone upgrade layout.

Host/static code uses Python 3.9+ stdlib only. --django runs in the backend's
locked environment, never on a customer installation. No database is mutated.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATA = Path("deploy/update")
SAFE_PATTERNS = "add a nullable column, backfill in batches, add the index in its own migration"
RISK_CLASSES = {"type_change", "not_null", "volatile_default", "index", "constraint",
                "data_migration", "table_rewrite", "clickhouse_rewrite", "clickhouse_mutation", "clickhouse_change"}
DOMAINS = {"postgres_app", "clickhouse", "object_store", "temporal", "shared_files"}


class GateError(ValueError):
    pass


def require(condition, message, update):
    if not condition:
        raise GateError(f"{message}; update {update}")


def document(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"{path}: duplicate key {key}", str(path))
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, timeout=60)


def changed_paths(root, base, head=None):
    """Triple-dot PR comparison, plus untracked files for the developer's tree."""
    git(root, "rev-parse", "--verify", base + "^{commit}")
    start = git(root, "merge-base", base, head or "HEAD").strip()
    args = ["diff", "--name-status", "--no-renames", start]
    if head:
        args.append(head)
    result = []
    for line in git(root, *args, "--").splitlines():
        status, path = line.split("\t", 1)
        result.append((status, path))
    if head is None:
        result += [("A", p) for p in git(root, "ls-files", "--others", "--exclude-standard").splitlines()]
    return result


def migration_path(path):
    p = Path(path)
    return p.suffix == ".py" and p.parent.name == "migrations" and p.stem != "__init__"


def registry_tables(registry, database):
    require(registry.get("schema_version") == 1, "unsupported table registry schema", DATA / "high-volume-tables.json")
    rows = registry.get("tables", [])
    keys = [(row["database"], row["table"]) for row in rows]
    require(len(keys) == len(set(keys)) and all(row.get("reason") and row.get("source") for row in rows),
            "duplicate tables or missing table rationale/source", DATA / "high-volume-tables.json")
    return {r["table"] for r in rows if r["database"] == database}


def compose_model(root):
    """Compose is the YAML parser; config is daemon-free, all profiles included.

    Ignore the developer's shell/.env overrides: this is the shipped default
    layout, not qualification of a particular customer's retained installation.
    """
    command = ["docker", "compose"]
    if shutil.which("docker-compose"):
        command = ["docker-compose"]
    env = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_CONTEXT", "DOCKER_CONFIG") if key in os.environ}
    env["COMPOSE_PROJECT_NAME"] = "futureagi"
    if env.get("DOCKER_CONTEXT"):
        command += ["--context", env["DOCKER_CONTEXT"]]
    command += ["--env-file", os.devnull, "-f", str(root / "docker-compose.yml"),
                "--profile", "*", "config", "--no-path-resolution", "--no-env-resolution", "--format", "json"]
    try:
        return json.loads(subprocess.check_output(command, env=env, text=True, stderr=subprocess.PIPE, timeout=60))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise GateError("docker-compose.yml: cannot render the all-profile default model; "
                        "update/install Docker Compose >=2.30 (config --no-env-resolution), or pass --compose-model JSON") from exc


def layout_projection(model):
    result = {"services": {}, "volumes": {}}
    require(not any(model.get(key) for key in ("configs", "secrets", "include")),
            "docker-compose.yml: unqualified config/secret/include mounts", DATA / "automatic-layout.json and backup coverage together")
    for name, service in model.get("services", {}).items():
        require(not any(service.get(k) for k in ("volumes_from", "tmpfs", "configs", "secrets")),
                f"docker-compose.yml {name}: unqualified indirect/temporary mount", DATA / "automatic-layout.json and backup coverage together")
        require(not service.get("deploy", {}).get("restart_policy"),
                f"docker-compose.yml {name}: deploy.restart_policy is unsupported", "docker-compose.yml (restart: unless-stopped or 'no')")
        restart = service.get("restart", "no")
        require(restart in ("unless-stopped", "no"), f"docker-compose.yml {name}: restart {restart!r} is unsafe",
                "docker-compose.yml (restart: unless-stopped or 'no') and deploy/update/automatic-layout.json")
        mounts = []
        for mount in service.get("volumes", []):
            require(isinstance(mount, dict) and mount.get("source") and mount.get("type") in ("bind", "volume"),
                    f"docker-compose.yml {name}: anonymous/unsupported mount {mount!r}", DATA / "automatic-layout.json and backup coverage together")
            projected = {k: mount.get(k, False) if k == "read_only" else mount[k]
                         for k in ("type", "source", "target", "read_only")}
            extra = {k: v for k, v in mount.items() if k not in projected}
            if extra.get("bind") in ({}, {"create_host_path": True}):
                extra.pop("bind", None)  # Compose's defaults for short bind syntax.
            if not extra.get("volume"):
                extra.pop("volume", None)
            projected.update(extra)
            mounts.append(projected)
            require(not mount.get("volume", {}).get("subpath") and not mount.get("volume", {}).get("nocopy"),
                    f"docker-compose.yml {name}: unsupported volume options", DATA / "automatic-layout.json and backup coverage together")
        result["services"][name] = {"profiles": sorted(service.get("profiles", [])), "restart": restart,
                                     "mounts": sorted(mounts, key=lambda m: m["target"])}
    for name, volume in model.get("volumes", {}).items():
        # Compose synthesizes the default physical name; an explicit nondefault
        # name, external volume, driver or options must change the contract.
        result["volumes"][name] = {k: v for k, v in (volume or {}).items()
                                     if not (k == "name" and v == "futureagi_" + name)}
    return result


def check_layout(model, contract):
    update = "deploy/update/automatic-layout.json and backup coverage together"
    require(contract.get("schema_version") == 1, "unsupported automatic layout schema", update)
    actual = layout_projection(model)
    for key in ("services", "volumes"):
        require(actual[key] == contract.get(key), f"docker-compose.yml: {key} differ from reviewed automatic layout", update)
    require(contract.get("automatic_profiles") == [], "only base Standalone is qualified", update)
    coverage = contract.get("backup_coverage", {})
    require(set(coverage) == set(actual["volumes"]), "named volume missing/extraneous backup coverage", update)
    covered = set()
    for name, row in coverage.items():
        require(row.get("method") == "cold_archive" and row.get("domains") and
                set(row["domains"]) <= DOMAINS and row.get("restore") == "same_named_volume",
                f"volume {name}: unsupported or incomplete backup/restore coverage", update)
        covered.update(row["domains"])
    require(covered == DOMAINS, "persistent logical domains not completely covered", update)
    storage = contract.get("service_storage", {})
    require(set(storage) == set(actual["services"]), "service added/removed without backup/ephemeral-state coverage", update)
    for name, entry in actual["services"].items():
        volumes = {m["source"] for m in entry["mounts"] if m["type"] == "volume"}
        require(set(storage[name].get("volumes", [])) == volumes and
                storage[name].get("ephemeral") is (not bool(volumes)) and storage[name].get("reason"),
                f"service {name}: storage coverage disagrees with Compose", update)
    binds = {f"{service}:{m['target']}" for service, entry in actual["services"].items()
             for m in entry["mounts"] if m["type"] == "bind"}
    require(set(contract.get("bind_coverage", {})) == binds, "bind backup coverage does not match Compose", update)
    for name, row in contract.get("bind_coverage", {}).items():
        require(row.get("method") in ("saved_private_config", "retained_deployment", "empty_device") and row.get("reason"),
                f"bind {name}: missing coverage reason/method", update)
    for name, entry in actual["services"].items():
        for mount in entry["mounts"]:
            if mount["type"] == "volume":
                require(mount["source"] in coverage, f"service {name}: unknown named volume {mount['source']}", update)


def reference_default(raw):
    """Parse schema-1's string or inline setup/string table on Python 3.9.

    This is deliberately bounded metadata, not a permissive TOML approximation:
    unsupported values or trailing data fail closed rather than become defaults.
    Quoted group keys (e.g. "S D") and TOML literal strings are supported.
    """
    string = r'''"(?:[^"\\]|\\.)*"|'[^']*' '''.strip()
    update = "deploy/env-reference.toml (single-line string or per-setup string defaults)"

    def decode(token):
        return token[1:-1] if token.startswith("'") else json.loads(token)

    raw = raw.strip()
    scalar = re.fullmatch(r"(" + string + r")\s*(?:#.*)?", raw)
    if scalar:
        return decode(scalar[1])
    table = re.fullmatch(r"\{(.*)\}\s*(?:#.*)?", raw)
    require(table is not None, "env reference has an unsupported default value", update)
    rest, result = table[1].strip(), {}
    while rest:
        entry = re.match(r"(" + string + r"|[A-Za-z_][A-Za-z_0-9-]*)\s*=\s*(" + string + r")\s*", rest)
        require(entry is not None, "env reference has an invalid setup default", update)
        key = decode(entry[1]) if entry[1].startswith(('"', "'")) else entry[1]
        require(key and key not in result, "env reference has duplicate/empty setup defaults", update)
        result[key] = decode(entry[2])
        rest = rest[entry.end():]
        if rest:
            require(rest.startswith(",") and rest[1:].strip(), "env reference has invalid inline-table defaults", update)
            rest = rest[1:].strip()
    return result


def standalone_default(row):
    default = row["default"]
    if isinstance(default, str):
        return default
    matches = [value for codes, value in default.items() if "S" in codes.split()]
    require(len(matches) == 1, "env reference needs exactly one Standalone-specific default",
            "deploy/env-reference.toml (unambiguous S default)")
    return matches[0]


def toml_line(line):
    """Remove only unquoted TOML comments, including after table headers."""
    quote, escaped = None, False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif quote == '"' and char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            return line[:index].strip()
    return line.strip()


def env_rows(text):
    """Read the reference's row metadata without a third-party TOML dependency.

    A deliberately bounded subset, not a TOML parser: quoted keys, setups,
    default and generator metadata are single-line values in schema 1. Unknown
    or multiline metadata fails closed. Prose and multiline bodies are ignored.
    """
    rows = {}
    generated = False
    legacy = False
    # Split only at actual table headers, never a header inside quoted prose.
    blocks, block, multiline = [], [], None
    for line in text.splitlines():
        stripped = line.strip()
        if multiline:
            if multiline in stripped:
                multiline = None
            continue
        stripped = toml_line(stripped)
        if "'''" in stripped or '\"\"\"' in stripped:
            marker = "'''" if "'''" in stripped else '\"\"\"'
            if stripped.count(marker) == 1:
                multiline = marker
            continue
        if stripped.startswith("["):
            header = re.fullmatch(r"\[\[\s*([A-Za-z_][A-Za-z_0-9]*(?:\s*\.\s*[A-Za-z_][A-Za-z_0-9]*)*)\s*\]\]", stripped)
            require(header is not None, "env reference has an unsupported table header", "deploy/env-reference.toml")
            if block:
                blocks.append(block)
            block = ["[[" + re.sub(r"\s", "", header[1]) + "]]"]
        else:
            block.append(stripped)
    if block:
        blocks.append(block)
    for block in blocks:
        if block[0] == "[[part]]":
            generated = any(re.fullmatch(r'role\s*=\s*"generated"', s) for s in block)
            legacy = any(re.fullmatch(r'role\s*=\s*"legacy"', s) for s in block)
        if block[0] != "[[part.section.row]]":
            continue
        fields = {}
        for line in block[1:]:
            match = re.match(r"(keys|setups|default|generator)\s*=\s*(.+)$", line)
            if match:
                fields[match[1]] = match[2]
        if legacy:
            fields.setdefault("default", '"legacy; not required"')
            fields.setdefault("setups", "[]")
        require("keys" in fields and "default" in fields and "setups" in fields,
                "env reference row lacks single-line keys/default/setups", "deploy/env-reference.toml")
        keys, setups = json.loads(fields["keys"]), json.loads(fields["setups"])
        require(isinstance(keys, list) and all(isinstance(key, str) for key in keys) and
                isinstance(setups, list) and all(isinstance(code, str) for code in setups),
                "env reference has invalid keys/setups metadata", "deploy/env-reference.toml")
        fields.update(default=reference_default(fields["default"]), setups=setups)
        for key in keys:
            rows.setdefault(key, []).append({**fields, "generated": generated})
    return rows


def references(text):
    """Compose substitutions, including nested defaults and escaped dollars."""
    i = 0
    while i < len(text):
        if text.startswith("$$", i):
            i += 2
            continue
        match = re.match(r"\$\{([A-Za-z_][A-Za-z_0-9]*)", text[i:])
        if match:
            j, depth = i + match.end(), 1
            k = j
            while k < len(text) and depth:
                if text.startswith("${", k):
                    depth += 1
                    k += 2
                    continue
                if text[k] == "}":
                    depth -= 1
                k += 1
            require(depth == 0, "docker-compose.yml: unterminated substitution", "docker-compose.yml")
            body = text[j:k - 1]
            op = next((p for p in (":-", ":?", ":+", "-", "?", "+") if body.startswith(p)), "")
            yield match[1], op
            yield from references(body[len(op):])
            i = k
        elif text[i] == "$":
            match = re.match(r"\$([A-Za-z_][A-Za-z_0-9]*)", text[i:])
            if match:
                yield match[1], ""
                i += match.end()
            else:
                i += 1
        else:
            i += 1


def closed(value, fields, path):
    require(isinstance(value, dict) and set(value) == set(fields),
            f"{path}: expected exactly {sorted(fields)}", path)


def strings(value, path, nonempty=True):
    require(isinstance(value, list) and (value or not nonempty) and
            all(isinstance(v, str) and v.strip() == v and v for v in value) and
            len(value) == len(set(value)), f"{path}: expected a duplicate-free string list", path)


def relative_path(value, path, pattern=False):
    require(isinstance(value, str) and value and not value.startswith("/") and
            "\\" not in value and all(part not in ("", ".", "..") for part in value.split("/")) and
            (pattern or not any(c in value for c in "*?[]")), f"{path}: invalid repository path", path)
    return value


def generator_definitions(source, language, path):
    """Locate definitions, not calls/log messages. Never source a shell file.

    Shell registry sources deliberately support only files without heredocs.
    An unsupported source is refused, rather than treating its quoted payload
    as a generator. posix=False retains quotes, including multiline log strings.
    """
    if language == "python":
        return {node.name for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}
    require(language == "shell", f"{path}: unknown generator language", DATA / "env-generators.json")
    require("<<" not in source, f"{path}: generator source with heredocs is unsupported",
            "deploy/update/env-generators.json (use a dedicated generator file)")
    lexer = shlex.shlex(source, posix=False, punctuation_chars="();{}&|\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        words = list(lexer)
    except ValueError as exc:
        raise GateError(f"{path}: malformed generator source; update that source") from exc
    # Split punctuation runs, not quoted tokens or ordinary words.
    tokens = [part for word in words for part in (list(word) if word and all(c in "();{}&|\n" for c in word) else [word])]
    found = set()
    for index in range(len(tokens) - 3):
        name = tokens[index]
        if (index == 0 or tokens[index - 1] in ("\n", ";", "}")) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name) and tokens[index + 1:index + 4] == ["(", ")", "{"]:
            # A definition must also occur literally at the beginning of a line.
            if re.search(r"(?m)^" + re.escape(name) + r"\(\)\s*\{", source):
                found.add(name)
    return found


def check_generators(root, registry):
    path = DATA / "env-generators.json"
    closed(registry, {"schema_version", "generators"}, path)
    require(type(registry["schema_version"]) is int and registry["schema_version"] == 1 and
            isinstance(registry["generators"], list), "invalid generator registry", path)
    result = set()
    for row in registry["generators"]:
        closed(row, {"variables", "path", "language", "symbol", "entrypoint", "availability", "reason"}, path)
        strings(row["variables"], path)
        require(all(re.fullmatch(r"[A-Z][A-Z_0-9]*", v) for v in row["variables"]) and
                not result.intersection(row["variables"]), "invalid/duplicate generated variables", path)
        source_path = root / relative_path(row["path"], path)
        require(source_path.is_file() and source_path.resolve() == root.resolve() / row["path"],
                f"{source_path}: generator file is missing/indirect", path)
        require(row["availability"] in ("fresh_install", "every_install") and
                isinstance(row["reason"], str) and len(row["reason"].strip()) >= 20,
                "generator availability/reason is required", path)
        require(all(isinstance(row[k], str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", row[k])
                    for k in ("symbol", "entrypoint")), "invalid generator definition names", path)
        definitions = generator_definitions(source_path.read_text(), row["language"], row["path"])
        for name in (row["symbol"], row["entrypoint"]):
            require(name in definitions, f"{row['path']}: exact generator/entrypoint definition {name} is missing", path)
        result.update(row["variables"])
    return result


def check_env(compose_text, reference_text, generators):
    """The explicit reviewed registry supplies generators, never source inference."""
    require(isinstance(generators, set), "generator registry must be validated first", DATA / "env-generators.json")
    rows = env_rows(reference_text)
    compose_text = "\n".join(s for s in compose_text.splitlines() if not s.lstrip().startswith("#"))
    defaults = set()
    for key, operator in references(compose_text):
        require(key in rows, f"docker-compose.yml: {key} is undocumented", "deploy/env-reference.toml")
        if operator in ("-", ":-"):
            defaults.add(key)
        require(operator in ("-", ":-") or key in generators,
                f"docker-compose.yml: {key} has no unattended default or registered generator",
                "docker-compose.yml or deploy/update/env-generators.json, and deploy/env-reference.toml")
    for key, candidates in rows.items():
        for row in candidates:
            if "S" not in row["setups"]:
                continue
            required = standalone_default(row).lower().strip('"\' ') in ("required", "no default")
            require(not required or key in defaults or key in generators,
                    f"deploy/env-reference.toml: required Standalone {key} has no Compose default or registered generator",
                    "docker-compose.yml or deploy/update/env-generators.json, and deploy/env-reference.toml")


def check_migration_layout(apps, loader, settings, mapping):
    update = "deploy/update/migration-apps.json and the fixed host observer together"
    require(mapping.get("schema_version") == 1, "unsupported migration app mapping", update)
    require(not getattr(settings, "MIGRATION_MODULES", {}), "custom MIGRATION_MODULES is unsupported", "tfc/settings/settings.py; use default app migrations")
    expected = {r["label"]: r for r in mapping["apps"]}
    require(len(expected) == len(mapping["apps"]), "duplicate app labels in mapping", update)
    actual = {a.label: a for a in apps.get_app_configs()}
    for label, config in actual.items():
        require(label in expected and expected[label]["module"] == config.name,
                f"INSTALLED_APPS: unknown label/module {label} ({config.name})", update)
        row = expected[label]
        require(row["condition"] != "unsupported_ee", f"{label}: not a supported OSS app", "tfc/settings/settings.py and deploy/update/migration-apps.json")
        require(Path(config.path).as_posix().endswith("/" + row["source_root"]),
                f"{label}: source root differs from mapping", update)
        require((label in loader.migrated_apps) == row["migrations"], f"{label}: migration package presence changed", update)
    backend_roots = {Path(config.path).parents[len(Path(expected[label]["source_root"]).parts) - 1]
                     for label, config in actual.items() if expected[label].get("location", "backend") == "backend"}
    require(len(backend_roots) == 1, "active backend apps do not share a supported source root", update)
    backend_root = next(iter(backend_roots))
    for label, row in expected.items():
        if row["condition"] == "always":
            require(label in actual, f"INSTALLED_APPS: required label {label} disappeared", update)
        elif row["condition"] == "if_present":
            # Repo-shipped conditional apps must not silently disappear.
            root = backend_root / row["source_root"]
            require((label in actual) == root.is_dir(), f"conditional app {label}: presence differs from registry", update)
    for (label, name), migration in loader.disk_migrations.items():
        require(label in actual and label in expected, f"MigrationLoader: unknown active label {label}", update)
        require(not migration.replaces, f"{label}.{name}: squashed/replaces migrations are unsupported", "that migration; retain the unsquashed migration chain")


def declaration(source, path):
    tree = ast.parse(source)
    values = [n.value for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and
              any(isinstance(t, ast.Name) and t.id == "UPDATE_SAFETY" for t in
                  (n.targets if isinstance(n, ast.Assign) else [n.target]))]
    assignments = [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign)) and
                   any(isinstance(t, ast.Name) and t.id == "UPDATE_SAFETY" for t in
                       (n.targets if isinstance(n, ast.Assign) else [n.target]))]
    require(len(values) == len(assignments), f"{path}: UPDATE_SAFETY must be module-level", path)
    require(len(values) <= 1, f"{path}: duplicate UPDATE_SAFETY declaration", str(path))
    if values:
        # literal_eval itself accepts duplicate dict keys. Reject them first,
        # recursively, so a malformed declaration cannot silently become valid.
        for node in ast.walk(values[0]):
            if isinstance(node, ast.Dict):
                try:
                    keys = [ast.literal_eval(key) for key in node.keys]
                    require(len(keys) == len(set(keys)), f"{path}: duplicate declaration key", path)
                except (ValueError, TypeError) as exc:
                    raise GateError(f"{path}: declaration needs literal keys; update {path}") from exc
        try:
            value = ast.literal_eval(values[0])
            require(isinstance(value, dict), f"{path}: UPDATE_SAFETY must be a literal dict", path)
            return value
        except (ValueError, TypeError) as exc:
            raise GateError(f"{path}: UPDATE_SAFETY must be a literal dict; update {path}") from exc
    return None


def validate_declaration(value, risks, registry, path):
    tables = sorted({r["table"] for r in risks if "table" in r})
    classes = sorted({r["risk_class"] for r in risks})
    update = f"{path} UPDATE_SAFETY (see deploy/update/UPDATE_SAFETY.md)"
    if value is None:
        require(not risks, f"{path}: reviewed declaration required for {classes} on {tables}; safe patterns: {SAFE_PATTERNS}", update)
        return
    fields = {"schema_version", "risk_classes", "tables", "mitigation", "review", "reviewer", "large_volume_rehearsal"}
    closed(value, fields, update)
    require(type(value["schema_version"]) is int and value["schema_version"] == 1,
            f"{path}: invalid risk declaration schema", update)
    strings(value["risk_classes"], update)
    strings(value["tables"], update)
    require(set(value["risk_classes"]) <= RISK_CLASSES, f"{path}: invalid risk_classes", update)
    # Arbitrary code is intentionally NOT analyzed. Its reviewer declares the
    # complete actual table roster, including small/non-model tables. No '*' or
    # sentinel identities can masquerade as measurable qualification inputs.
    require(all(re.fullmatch(r"(?:postgres|clickhouse):[A-Za-z_][A-Za-z_0-9]*", table)
                for table in value["tables"]), f"{path}: invalid tables", update)
    require(set(classes) <= set(value["risk_classes"]) and set(tables) <= set(value["tables"]),
            f"{path}: declaration does not cover detected tables/classes {tables}/{classes}; safe patterns: {SAFE_PATTERNS}", update)
    require(isinstance(value["mitigation"], str) and 30 <= len(value["mitigation"].strip()) <= 2000 and
            isinstance(value["review"], str) and
            re.fullmatch(r"https://github\.com/future-agi/future-agi/pull/[1-9][0-9]*#discussion_r[1-9][0-9]*", value["review"]) and
            isinstance(value["reviewer"], str) and len(value["reviewer"]) <= 39 and
            re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", value["reviewer"]) and
            value["large_volume_rehearsal"] is True,
            f"{path}: mitigation, reviewer, review-comment URL and large-volume rehearsal are mandatory; safe patterns: {SAFE_PATTERNS}", update)


def model_table(state, app, model):
    key = (app, model.lower())
    require(key in state.models, f"cannot resolve historical model {app}.{model}", "the new migration and deploy/update/high-volume-tables.json")
    return state.models[key].options.get("db_table", f"{app}_{model.lower()}")


def operation_risks(operation, app, state, registry, fresh=None):
    """Use real Django operation objects + historical ProjectState table options."""
    from django.db import migrations
    from django.db.models import NOT_PROVIDED, Value
    fresh = fresh if fresh is not None else set()
    pg = registry_tables(registry, "postgres")
    kind = type(operation).__name__
    module = type(operation).__module__
    require(isinstance(operation, (migrations.RunPython, migrations.RunSQL)) or
            module.startswith(("django.db.migrations.operations.", "django.contrib.postgres.operations")),
            f"unclassified custom migration operation {module}.{kind}", "the migration and scripts/check_update_safety.py (explicit classifier)")
    risks = []

    def add(table, risk):
        row = {"table": "postgres:" + table, "risk_class": risk, "operation": kind}
        if table in pg and table not in fresh and row not in risks:
            risks.append(row)

    if isinstance(operation, migrations.SeparateDatabaseAndState):
        for operations, state_only in ((operation.database_operations, False), (operation.state_operations, True)):
            nested_state = state.clone()
            nested_fresh = set(fresh)
            for op in operations:
                # A state-only CreateModel cannot certify an empty physical table.
                risks += operation_risks(op, app, nested_state, registry, set(fresh) if state_only else nested_fresh)
                op.state_forwards(app, nested_state)
        return risks
    if isinstance(operation, migrations.CreateModel):
        table = operation.options.get("db_table", f"{app}_{operation.name.lower()}")
        historical = {model_table(state, a, m) for a, m in state.models}
        # State-only/unmanaged/proxy aliases do not create empty physical data.
        if (operation.options.get("managed", True) and not operation.options.get("proxy", False)
                and table not in historical):
            fresh.add(table)
        return []
    if isinstance(operation, (migrations.RunPython, migrations.RunSQL)):
        # Both directions must be positively identified as Django's own no-op.
        # Subclasses can override behavior/equality, so are not exempt.
        noop_python = (type(operation) is migrations.RunPython and operation.code is migrations.RunPython.noop and
                       operation.reverse_code is migrations.RunPython.noop)
        noop_sql = (type(operation) is migrations.RunSQL and type(operation.sql) is str and type(operation.reverse_sql) is str and
                    operation.sql == migrations.RunSQL.noop and operation.reverse_sql == migrations.RunSQL.noop)
        if not (noop_python or noop_sql):
            risks.append({"risk_class": "data_migration", "operation": kind})
        if isinstance(operation, migrations.RunSQL):
            nested_state = state.clone()
            for op in operation.state_operations:
                risks += operation_risks(op, app, nested_state, registry, set(fresh))
                op.state_forwards(app, nested_state)
        return risks
    model = getattr(operation, "model_name", getattr(operation, "name", getattr(operation, "old_name", None)))
    if not model:
        require(kind in ("AlterModelManagers", "AlterModelOptions"), f"unknown migration operation {kind}", "scripts/check_update_safety.py with a risk classifier")
        return []
    table = model_table(state, app, model)
    if isinstance(operation, migrations.AlterField):
        old = state.models[(app, model.lower())].fields[operation.name]
        new = operation.field
        # Field classes are not storage types: an unchanged ForeignKey class
        # can resolve to integer before this operation and varchar afterwards.
        # Compare rendered historical backend types, including inbound FKs on
        # hot tables when the operation changes a smaller target table's PK.
        from django.db import connection
        after = state.clone()
        operation.state_forwards(app, after)
        try:
            for before_model in state.apps.get_models(include_auto_created=True):
                hot_table = before_model._meta.db_table
                if hot_table not in pg or hot_table in fresh:
                    continue
                after_model = after.apps.get_model(before_model._meta.app_label, before_model._meta.model_name)
                for before_field in before_model._meta.local_fields:
                    after_field = after_model._meta.get_field(before_field.name)
                    before_db = before_field.db_parameters(connection)
                    after_db = after_field.db_parameters(connection)
                    if (before_db.get("type"), before_db.get("collation")) != (after_db.get("type"), after_db.get("collation")):
                        add(hot_table, "type_change")
                    # Django's backend-generated field CHECKs (e.g. positive
                    # integers) validate existing rows even on nullable columns.
                    if after_db.get("check") and before_db.get("check") != after_db.get("check"):
                        add(hot_table, "constraint")
        except Exception as exc:
            raise GateError(f"{app}.{model}.{operation.name}: cannot resolve historical backend column type; "
                            "update the migration/field implementation and scripts/check_update_safety.py") from exc
        if old.get_internal_type() != new.get_internal_type() or any(getattr(old, k, None) != getattr(new, k, None)
                for k in ("max_length", "max_digits", "decimal_places", "db_collation")):
            add(table, "type_change")
        if any(getattr(old, key, None) != getattr(new, key, None) for key in ("db_column", "db_tablespace")):
            add(table, "table_rewrite")
        if (getattr(old, "many_to_many", False) or getattr(new, "many_to_many", False)) and old.deconstruct() != new.deconstruct():
            add(table, "table_rewrite")
        if old.null and not new.null:
            add(table, "not_null")
        if (new.db_index and not old.db_index) or (new.unique and not old.unique):
            add(table, "index")
        if ((getattr(new, "db_constraint", False) and not getattr(old, "db_constraint", False)) or
                (new.primary_key and not old.primary_key) or
                getattr(new.remote_field, "model", None) != getattr(old.remote_field, "model", None) or
                getattr(new, "to_fields", None) != getattr(old, "to_fields", None) or
                getattr(new, "from_fields", None) != getattr(old, "from_fields", None)):
            add(table, "constraint")
        if getattr(new, "generated", False) and old.deconstruct() != new.deconstruct():
            add(table, "table_rewrite")
    elif isinstance(operation, migrations.AddField):
        field = operation.field
        if (field.default is not NOT_PROVIDED and callable(field.default)) or (getattr(field, "db_default", NOT_PROVIDED) is not NOT_PROVIDED and
                                       not isinstance(field.db_default, (str, int, float, bool, type(None), Value))):
            add(table, "volatile_default")
        if field.db_index or field.unique or getattr(field, "many_to_many", False):
            add(table, "index")
        # Resolve the rendered field against the historical model/backend,
        # rather than hardcoding a list of Positive* field classes. This also
        # covers backend CHECKs from other supported field implementations.
        from django.db import connection
        after = state.clone()
        operation.state_forwards(app, after)
        try:
            rendered = after.apps.get_model(app, model)._meta.get_field(operation.name)
            check = rendered.db_parameters(connection).get("check")
        except Exception as exc:
            raise GateError(f"{app}.{model}.{operation.name}: cannot resolve historical backend column constraint; "
                            "update the migration/field implementation and scripts/check_update_safety.py") from exc
        if check or getattr(field, "db_constraint", False) or field.primary_key:
            add(table, "constraint")
        if getattr(field, "generated", False):
            add(table, "table_rewrite")
        if not field.null and getattr(field, "db_default", NOT_PROVIDED) is NOT_PROVIDED:
            add(table, "not_null")
    elif kind in ("AddIndex", "AddIndexConcurrently", "RemoveIndex", "RemoveIndexConcurrently", "RenameIndex"):
        add(table, "index")
    elif kind in ("AlterIndexTogether", "AlterUniqueTogether"):
        option = "index_together" if kind == "AlterIndexTogether" else "unique_together"
        old = set(state.models[(app, model.lower())].options.get(option, []))
        if set(getattr(operation, option) or []) - old:
            add(table, "index")
    elif kind in ("AddConstraint", "AddConstraintNotValid", "ValidateConstraint", "RemoveConstraint", "AlterConstraint"):
        add(table, "constraint")
    elif kind in ("RemoveField", "DeleteModel", "AlterModelTable", "RenameModel", "RenameField", "AlterOrderWithRespectTo"):
        add(table, "table_rewrite")
    elif kind not in ("AlterModelOptions", "AlterModelManagers", "AlterModelTableComment"):
        require(False, f"unclassified migration operation {kind}", "scripts/check_update_safety.py with a risk classifier")
    return risks


def lint_migrations(loader, paths, root, registry, mapping=None):
    result = []
    by_path = {Path(inspect.getfile(type(m))).resolve(): (key, m) for key, m in loader.disk_migrations.items()}
    for relative in paths:
        path = (root / relative).resolve()
        if path not in by_path and mapping:
            inactive = [r for r in mapping["apps"] if r["condition"] in ("unsupported_ee", "if_present") and
                        Path(relative).parent == Path("futureagi") / r["source_root"] / "migrations"]
            if inactive:
                # This source is not part of an OSS image's active inventory.
                # Never infer migration labels from path basenames.
                continue
        require(path in by_path, f"{relative}: added migration is not in the active OSS MigrationLoader", "that migration or deploy/update/migration-apps.json (unsupported apps stay notify-only)")
        (app, name), migration = by_path[path]
        parents = [node.key for node in loader.graph.node_map[(app, name)].parents]
        state = loader.project_state(parents) if parents else __import__("django.db.migrations.state", fromlist=["ProjectState"]).ProjectState()
        risks, fresh = [], set()
        for operation in migration.operations:
            risks += operation_risks(operation, app, state, registry, fresh)
            operation.state_forwards(app, state)
        value = declaration(path.read_text(), relative)
        validate_declaration(value, risks, registry, relative)
        if value:
            result.append({"path": relative, "tables": value["tables"], "risk_classes": value["risk_classes"], "large_volume_rehearsal": True})
    return result


# A9: an honest-mistake keyword alarm, not a SQL/program parser or security
# boundary. Client imports/calls, MergeTree names and SELECT builders alone are
# NOT schema changes. Deliberately split/obfuscated SQL is out of scope. Scan
# both changed blobs so removing a schema/mutation statement also needs review.
SQL_OBJECT = r"(?:TABLE|VIEW|DICTIONARY|DATABASE|INDEX|FUNCTION|USER|ROLE|QUOTA|ROW\s+POLICY|SETTINGS\s+PROFILE|NAMED\s+COLLECTION)"
DDL_DISCOVERY = re.compile(
    r"\b(?:(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:MATERIALIZED|LIVE|WINDOW|TEMPORARY)\s+)?|"
    r"ALTER\s+|DROP\s+(?:TEMPORARY\s+)?|RENAME\s+)" + SQL_OBJECT +
    r"|TRUNCATE\s+TABLE|OPTIMIZE\s+TABLE|DELETE\s+FROM|"
    r"ATTACH\s+(?:TABLE|DATABASE)|DETACH\s+(?:TABLE|DATABASE))\b|"
    r"(?:^|[;\"'`])[ \t]*TRUNCATE\s+(?:IF\s+EXISTS\s+)?[A-Za-z_`\"][A-Za-z_0-9.`\"]*"
    r"[ \t]*(?=[;\"'`\n]|$)", re.I | re.M)


def source_candidate(path):
    p = Path(path)
    # Extensionless shipped shell entrypoints are included too.
    return p.suffix in (".py", ".go", ".sql", ".sh") or (not p.suffix and not p.name.startswith("."))


def source_blob(path, content):
    """Extensionless binaries/cache/config data are not shell entrypoints."""
    return content is not None and (bool(Path(path).suffix) or content.startswith(b"#!"))


def schema_statements(content):
    if content is None:
        return []
    text = content.decode("utf-8", errors="strict")
    # Ignore whole-line source/SQL comments while preserving diagnostic lines.
    # No untrusted code is imported and no SQL strings are reconstructed.
    text = re.sub(r"(?m)^[ \t]*(?:#|//|--)[^\n]*", lambda m: " " * len(m.group()), text)
    return [{"line": text.count("\n", 0, match.start()) + 1,
             "keyword": " ".join(match.group().upper().split())}
            for match in DDL_DISCOVERY.finditer(text)]


def test_directory_pattern(pattern):
    # No filename heuristics: simulate/services/test_executor.py ships. Only
    # concrete reviewed test directories can exclude future fixture blobs.
    parts = pattern.split("/")
    return (len(parts) >= 2 and parts[-1] == "**" and parts[-2] in ("tests", "test", "testdata") and
            not any(c in "/".join(parts[:-1]) for c in "*?[]"))


def matches(path, rows):
    return any(fnmatch.fnmatchcase(path, row["pattern"]) for row in rows)


def source_registry(value):
    path = DATA / "clickhouse-sources.json"
    closed(value, {"schema_version", "sources", "exclusions"}, path)
    require(type(value["schema_version"]) is int and value["schema_version"] == 1,
            "unsupported ClickHouse source registry", path)
    for name in ("sources", "exclusions"):
        require(isinstance(value[name], list), f"invalid {name}", path)
        seen = set()
        for row in value[name]:
            closed(row, {"pattern", "reason", "sha256"} if name == "exclusions" else {"pattern", "reason"}, path)
            pattern = relative_path(row["pattern"], path, pattern=True)
            require(pattern not in seen and isinstance(row["reason"], str) and len(row["reason"].strip()) >= 20,
                    "duplicate pattern or missing source rationale", path)
            if name == "exclusions":
                digest = row["sha256"]
                require(digest is None and test_directory_pattern(pattern) or
                        isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) and
                        not any(c in pattern for c in "*?[]"),
                        "exclusions need a concrete reviewed test directory or one exact reviewed source SHA-256; test-like filenames are not exempt", path)
            seen.add(pattern)
    return value


def excluded_source(path, rows, content):
    return any(fnmatch.fnmatchcase(path, row["pattern"]) and
               (row["sha256"] is None or row["sha256"] == content_sha(content)) for row in rows)


def repository_paths(root):
    return sorted(set(git(root, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()))


def check_source_coverage(root, registry, paths=None):
    registry = source_registry(registry)
    missing, covered = [], []
    for path in paths if paths is not None else repository_paths(root):
        target = root / path
        if not source_candidate(path):
            continue
        require(not target.is_symlink() or not matches(path, registry["sources"]),
                f"{path}: indirect source path is unsupported", DATA / "clickhouse-sources.json")
        if not target.is_file():
            continue
        require(target.resolve() == root.resolve() / path, f"{path}: indirect source path is unsupported", DATA / "clickhouse-sources.json")
        # Django migration SQL is covered by the immutable-history check and
        # mandatory real-loader RunSQL/RunPython declarations, not two reviews.
        if migration_path(path):
            continue
        content = target.read_bytes()
        if not source_blob(path, content) or excluded_source(path, registry["exclusions"], content) or not schema_statements(content):
            continue
        if matches(path, registry["sources"]):
            covered.append(path)
        else:
            missing.append(path)
    require(not missing, f"unregistered schema/mutation statement sources: {missing}",
            "deploy/update/clickhouse-sources.json (source path/rationale, or an exact reviewed non-ClickHouse exclusion), "
            "then add human-reviewed entries to deploy/update/clickhouse-change-reviews.json")
    return covered


def base_commit(root, base):
    return git(root, "merge-base", base, "HEAD").strip()


def base_bytes(root, base, path):
    entry = git(root, "ls-tree", "-z", base, "--", path)
    if not entry:
        return None
    require(" blob " in entry.split("\t", 1)[0], f"{path}: baseline source is not a blob", path)
    return subprocess.check_output(["git", "-C", str(root), "show", base + ":" + path], timeout=60)


def content_sha(content):
    return hashlib.sha256(content).hexdigest() if content is not None else None


def clickhouse_review_candidates(root, changes, baseline, sources, head=None):
    """Changed production blobs with literal schema/mutation statements.

    Paths define scan coverage, never a review trigger. A raw keyword scan is
    intentionally simple; it does not inspect client calls or reconstruct SQL.
    """
    source_registry(sources)
    result = []
    for status, relative in changes:
        if migration_path(relative) or not source_candidate(relative):
            continue
        before = base_bytes(root, baseline, relative)
        current = root / relative
        after = base_bytes(root, head, relative) if head else (current.read_bytes() if current.is_file() else None)
        if before == after or excluded_source(relative, sources["exclusions"], after if after is not None else before):
            continue
        old_statements = schema_statements(before) if source_blob(relative, before) else []
        new_statements = schema_statements(after) if source_blob(relative, after) else []
        if old_statements or new_statements:
            result.append({"path": relative, "before_sha256": content_sha(before), "after_sha256": content_sha(after),
                           "before_statements": old_statements, "after_statements": new_statements})
    return result


def check_clickhouse_changes(root, changes, baseline, sources, reviews, registry):
    """Require human declarations only for keyword-discovered statement sources."""
    path = DATA / "clickhouse-change-reviews.json"
    source_registry(sources)
    closed(reviews, {"schema_version", "changes"}, path)
    require(type(reviews["schema_version"]) is int and reviews["schema_version"] == 1 and isinstance(reviews["changes"], list),
            "invalid ClickHouse change-review registry", path)
    entries = {}
    for row in reviews["changes"]:
        closed(row, {"path", "before_sha256", "after_sha256", "declaration"}, path)
        relative_path(row["path"], path)
        require(row["path"] not in entries, "duplicate ClickHouse change review path", path)
        for key in ("before_sha256", "after_sha256"):
            require(row[key] is None or isinstance(row[key], str) and re.fullmatch(r"[0-9a-f]{64}", row[key]),
                    f"{row['path']}: invalid {key}", path)
        require(row["before_sha256"] != row["after_sha256"], "review must describe an actual blob change", path)
        validate_declaration(row["declaration"], [{"risk_class": "clickhouse_change"}], registry, str(path))
        require(any(t.startswith("clickhouse:") for t in row["declaration"]["tables"]),
                "ClickHouse review must name ClickHouse tables", path)
        entries[row["path"]] = row
    candidates = clickhouse_review_candidates(root, changes, baseline, sources)
    missing = [row for row in candidates if row["path"] not in entries]
    require(not missing,
            "schema/mutation statements require human-reviewed declarations: " + json.dumps(missing, sort_keys=True) +
            "; for EACH path add {path, before_sha256, after_sha256, declaration}; declaration needs schema_version=1, "
            "risk_classes including clickhouse_change, complete clickhouse:<table> roster, mitigation, actual reviewer, "
            "actual PR discussion review URL and large_volume_rehearsal=true; do not invent approval",
            path)
    required = []
    for candidate in candidates:
        relative = candidate["path"]
        row = entries[relative]
        require(all(row[key] == candidate[key] for key in ("before_sha256", "after_sha256")),
                f"{relative}: review hashes do not match this exact base-to-head change", path)
        value = row["declaration"]
        required.append({"path": relative, "tables": value["tables"], "risk_classes": value["risk_classes"], "large_volume_rehearsal": True})
    return required


def check_registry_models(apps, registry, mapping):
    for row in registry["tables"]:
        if row["database"] != "postgres" or "model" not in row:
            continue
        try:
            model = apps.get_model(row["app_label"], row["model"])
        except LookupError as exc:
            # Absent optional EE apps are not in the active OSS inventory.
            if row["app_label"] not in {a.label for a in apps.get_app_configs()} and any(
                    app["label"] == row["app_label"] and app["condition"] == "if_present" for app in mapping["apps"]):
                continue
            raise GateError(f"{row['source']}: registered model {row['model']} no longer exists; "
                            "update deploy/update/high-volume-tables.json, retaining historical table coverage") from exc
        require(model._meta.db_table == row["table"],
                f"{row['source']}: {row['app_label']}.{row['model']} table differs from registry",
                "deploy/update/high-volume-tables.json, retaining historical table coverage")


def django_gate(root, paths, mapping, registry):
    sys.path.insert(0, str(root / "futureagi"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
    import django
    django.setup()
    from django.apps import apps
    from django.conf import settings
    from django.db.migrations.loader import MigrationLoader
    loader = MigrationLoader(None, ignore_no_migrations=True, replace_migrations=False)
    check_migration_layout(apps, loader, settings, mapping)
    check_registry_models(apps, registry, mapping)
    return lint_migrations(loader, paths, root, registry, mapping)


def calibrate_releases(root):
    """Actual historical loaders, fresh process per tag, current locked env.

    This is a developer calibration report, never product qualification. Source
    is isolated in a new empty directory; no checkout/reset/worktree mutation.
    No historical code is used as the driver or installed into the interpreter.
    """
    child = '''import contextlib, importlib.util, json, sys
from pathlib import Path
script, source_root, inputs_path = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("gate", script)
gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)
data = gate.document(inputs_path)
with contextlib.redirect_stdout(sys.stderr):
    sys.path.insert(0, str(source_root / "futureagi"))
    import django; django.setup()
    from django.apps import apps
    from django.conf import settings
    from django.db.migrations.loader import MigrationLoader
    loader = MigrationLoader(None, ignore_no_migrations=True, replace_migrations=False)
    gate.check_migration_layout(apps, loader, settings, data["mapping"])
    findings = []
    for path in data["paths"]:
        try:
            required = gate.lint_migrations(loader, [path], source_root, data["registry"], data["mapping"])
            findings.append({"path": path, "status": "declared" if required else "passed", "requirements": required})
        except gate.GateError as exc:
            findings.append({"path": path, "status": "flagged", "message": str(exc)})
print(json.dumps({"real_django_loader": True, "migrations": findings}))
'''
    mapping = document(root / DATA / "migration-apps.json")
    sources = source_registry(document(root / DATA / "clickhouse-sources.json"))
    reports, previous = [], "v1.47.1"
    for minor in range(48, 56):
        tag = f"v1.{minor}.0"
        paths = [p for status, p in changed_paths(root, previous, tag) if status == "A" and migration_path(p)]
        with tempfile.TemporaryDirectory(prefix="update-safety-gate-calibration-") as temporary:
            directory = Path(temporary)
            source = directory / "source"
            source.mkdir()
            archive = directory / "backend.tar"
            subprocess.run(["git", "-C", str(root), "archive", "--format=tar", "--output", str(archive), tag, "futureagi"], check=True, timeout=60)
            with tarfile.open(archive) as stream:
                for member in stream:
                    path = Path(member.name)
                    require(not path.is_absolute() and ".." not in path.parts and path.parts[0] == "futureagi" and
                            (member.isdir() or member.isfile()), f"{tag}: unsupported historical archive member", "the tag's backend source layout")
                    target = source / path
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open("xb") as output, stream.extractfile(member) as content:
                            shutil.copyfileobj(content, output, length=1024 * 1024)
            inputs = directory / "inputs.json"
            inputs.write_text(json.dumps({"mapping": mapping,
                                          "registry": document(root / DATA / "high-volume-tables.json"), "paths": paths}))
            env = {**os.environ, "DJANGO_SETTINGS_MODULE": "tfc.settings.settings", "EE_LICENSE_KEY": "", "ENV_TYPE": "local",
                   "LITELLM_MODE": "PRODUCTION", "NO_STARTUP_DB_MUTATIONS": "true", "FI_SKIP_CH25_MIGRATION": "1",
                   "FUTURE_AGI_TELEMETRY_DISABLED": "true"}
            process = subprocess.run([sys.executable, "-I", "-B", "-c", child, str(Path(__file__).resolve()), str(source), str(inputs)],
                                     env=env, text=True, capture_output=True, timeout=180)
            literal_requirements = []
            for relative in paths:
                tree = ast.parse((source / relative).read_text())
                calls = sorted({node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and
                                isinstance(node.func, ast.Attribute) and node.func.attr in ("RunPython", "RunSQL")})
                if calls:
                    app_rows = [row for row in mapping["apps"] if Path(relative).parent ==
                                Path("futureagi") / row["source_root"] / "migrations"]
                    literal_requirements.append({"path": relative, "operation_spellings": calls,
                                                 "app_conditions": [row["condition"] for row in app_rows],
                                                 "classification": "source_inventory_only_not_loader_evidence"})
            candidates = clickhouse_review_candidates(root, changed_paths(root, previous, tag), previous, sources, head=tag)
            report = {"release": tag, "base": previous, "added_migrations": len(paths),
                      "clickhouse_source_changes_requiring_review": [row["path"] for row in candidates],
                      "clickhouse_statement_reviews": candidates,
                      "data_operation_source_inventory": literal_requirements}
            if process.returncode:
                report.update(real_django_loader=False, loader_error=process.stderr[-4000:])
            else:
                report.update(json.loads(process.stdout))
            findings = report.get("migrations", [])
            declarations = [row["path"] for row in findings if row["status"] == "declared" or
                            (row["status"] == "flagged" and "reviewed declaration required" in row.get("message", ""))]
            complete = report["real_django_loader"] and all(row["status"] != "flagged" or
                        "reviewed declaration required" in row.get("message", "") for row in findings)
            report["developer_cost"] = {
                "migration_declarations": len(declarations) if complete else None,
                "migration_paths_requiring_declaration": declarations if complete else None,
                "clickhouse_reviews": len(candidates),
                "exact_migration_cost_verified": complete,
                "note": "One declaration per migration file, one review per keyword-bearing changed production file; "
                        "source inventory alone is not a schema-risk count."}
            reports.append(report)
        previous = tag
    return {"status": "calibrated" if all(r["real_django_loader"] and
            r["developer_cost"]["exact_migration_cost_verified"] for r in reports) else "blocked",
            "qualification": False, "releases": reports}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base", help="PR base commit/ref (required; diff is merge-base to working tree)")
    parser.add_argument("--static", action="store_true", help="Compose, env and changed ClickHouse checks")
    parser.add_argument("--django", action="store_true", help="real OSS app registry/loader and new migration risk checks")
    parser.add_argument("--calibrate", action="store_true", help="audit added migrations in v1.48.0..v1.55.0 with fresh real historical loaders")
    parser.add_argument("--compose-model", type=Path, help="pre-rendered all-profile default Compose JSON")
    parser.add_argument("--json", action="store_true", help="machine-readable rehearsal requirements")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.calibrate:
            require(not args.static and not args.django, "--calibrate is a separate historical audit", "your CLI invocation")
            calibration = calibrate_releases(root)
            print(json.dumps(calibration, sort_keys=True))
            return 0 if calibration["status"] == "calibrated" else 1
        require(args.base, "PR comparison needs an explicit --base commit/ref", ".github/workflows/backend-ci.yml or your CLI invocation")
        registry = document(root / DATA / "high-volume-tables.json")
        changes = changed_paths(root, args.base)
        migrations = [p for status, p in changes if migration_path(p) and status == "A"]
        require(not any(migration_path(p) and s != "A" for s, p in changes),
                "existing migration changed/deleted: deployed histories are immutable", "add a new migration instead of editing the existing migration")
        rehearsals = []
        if args.static or not args.django:
            check_layout(document(args.compose_model) if args.compose_model else compose_model(root), document(root / DATA / "automatic-layout.json"))
            generators = check_generators(root, document(root / DATA / "env-generators.json"))
            check_env((root / "docker-compose.yml").read_text(), (root / "deploy/env-reference.toml").read_text(), generators)
            sources = document(root / DATA / "clickhouse-sources.json")
            check_source_coverage(root, sources)
            rehearsals += check_clickhouse_changes(root, changes, base_commit(root, args.base), sources,
                                                  document(root / DATA / "clickhouse-change-reviews.json"), registry)
        if args.django:
            # AppConfig.ready() diagnostics must not corrupt the JSON API.
            with contextlib.redirect_stdout(sys.stderr):
                rehearsals += django_gate(root, migrations, document(root / DATA / "migration-apps.json"), registry)
        result = {"status": "passed", "new_migrations": len(migrations), "large_volume_rehearsal_required": rehearsals}
        print(json.dumps(result, sort_keys=True) if args.json else "Update-safety checks passed. Required large-volume rehearsals: " + str(len(rehearsals)))
        return 0
    except GateError as exc:
        print(f"update-safety: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Import/loader/graph failures are refusals, not an implicit clean plan.
        print(f"update-safety: {type(exc).__name__}: {exc}; update the affected migration/settings/contract "
              "(deploy/update/UPDATE_SAFETY.md); run --django in the locked backend environment", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
