"""Offline source census, including native driver SQL. No application import.

Classifies each candidate, not each file. Known negative-test functions are exact
exceptions, not runtime permits. Dynamic targets resolve only from literal module
constants or imported model metadata; new unresolved targets fail the gate.
"""

import ast
import re
import runpy
import unittest
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
source_files = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_source_files.py"))
)["source_files"]
SERVICES = ROOT / "futureagi/accounts/services"
read_source = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_source_reader.py"))
)["read_source"]
POLICY = runpy.run_path(str(SERVICES / "hard_erase_sql_policy.py"))
SOURCE_SITES = runpy.run_path(
    str(Path(__file__).with_name("hard_erase_source_sites.py"))
)["SOURCE_SITES"]
_backfill_tree = ast.parse((SERVICES / "hard_erase_backfill.py").read_text())
BACKFILL = {"_lex": POLICY["_lex"]}
exec(
    compile(
        ast.Module(
            body=[
                node
                for node in _backfill_tree.body
                if not isinstance(node, (ast.Import, ast.ImportFrom))
            ],
            type_ignores=[],
        ),
        "hard_erase_backfill.py",
        "exec",
    ),
    BACKFILL,
)
MUTATION = re.compile(
    r"\b(?:DELETE\s+FROM|UPDATE|COPY|TRUNCATE(?:\s+TABLE)?)\s+"
    r'(?:ONLY\s+)?(?P<table>"[^"]+"(?:\."[^"]+")?|[\w.]+|[{}%][\w{}%]*)',
    re.I,
)
# Exact function-level exceptions: synthetic negative tests, not application SQL.
EXCEPTIONS = {
    (
        "test_hard_erase_r2f8_flow.py",
        "test_bare_protected_explain_all_read_starts_and_opaque_data",
    ): "Offline bare-EXPLAIN controls and executing/row-lock negatives; actual guard with mock driver only.",
    (
        "test_hard_erase_r2f8_flow.py",
        "test_standard_strings_are_literal_backslashes_and_doubled_quotes",
    ): "Offline standard-string data and real following-statement negatives; actual guard refuses before mock driver.",
    (
        "test_hard_erase_r2f8_flow.py",
        "test_escape_strings_keep_backslash_escapes_and_whole_input_validation",
    ): "Offline E-string controls and malformed suffix negatives; driver doubles only, no runtime permission.",
    (
        "test_hard_erase_r2f8_flow.py",
        "test_empty_segments_do_not_count_as_executable_statements",
    ): "Offline empty-segment/COPY controls and protected mutation negatives; mock driver only.",
    (
        "test_hard_erase_r2f8_flow.py",
        "test_actual_dataset_search_builder_sends_both_statements",
    ): "SQL-looking search text is parameter data in the unchanged builder; actual guard dispatches to driver doubles only.",
    (
        "test_hard_erase_r2f8_flow.py",
        "test_cursor_copy_allows_empty_segments_but_no_second_statement",
    ): "Actual CursorWrapper with driver doubles: protected COPY, multiple statements and malformed suffixes refuse before driver.",
    (
        "test_hard_erase_r2f7_flow.py",
        "test_nested_comments_cannot_hide_execution",
    ): "Offline nested-comment execution negatives; actual guard refuses before mock driver, execute and executemany.",
    (
        "test_hard_erase_r2f7_flow.py",
        "test_nested_comments_preserve_pure_reads_and_upserts",
    ): "Offline pure-read/unprotected-upsert controls; mock driver only, no runtime permission.",
    (
        "test_hard_erase_r2f7_flow.py",
        "test_quoting_is_opaque_but_real_following_statements_refuse",
    ): "Offline opaque-data controls and real following-statement negatives; mock driver only.",
    (
        "test_hard_erase_r2f7_flow.py",
        "test_unterminated_or_unsupported_constructs_fail_closed",
    ): "Offline malformed lexical inputs must refuse before mock driver; no runtime permission.",
    (
        "test_hard_erase_r2f6_flow.py",
        "test_explain_prefix_stops_before_unknown_wrapped_statement",
    ): "Offline unknown-statement parser/guard fixtures; driver doubles only, protected inputs assert refusal.",
    (
        "test_hard_erase_r2f6_flow.py",
        "test_executing_and_mutating_forms_still_refuse",
    ): "Offline EXPLAIN/executing/multi-statement negatives; actual guard with mock driver asserted not called.",
    (
        "test_hard_erase_cursor_flow.py",
        "test_deferred_copy_checks_entry_and_writes_after_expiry",
    ): "Offline delayed COPY context and write negatives; driver doubles only.",
    (
        "test_hard_erase_r2f5_flow.py",
        "test_nonexecute_driver_apis_refuse_before_driver",
    ): "Actual CursorWrapper methods with driver doubles; protected nonexecute APIs refuse before the driver.",
    (
        "test_hard_erase_r2f5_flow.py",
        "test_unprotected_copy_and_fetch_work_but_unparseable_copy_refuses",
    ): "Offline unprotected COPY/fetch positives and malformed COPY refusal; driver doubles only.",
    (
        "test_hard_erase_r2f5_flow.py",
        "test_copy_requires_connection_bound_atomic_erase_or_migration",
    ): "Offline connection/atomic/migration-bound COPY dispatch; driver doubles only.",
    (
        "test_hard_erase_r2f5_flow.py",
        "test_deferred_copy_does_not_borrow_expired_authority",
    ): "Offline deferred COPY context and write refusal after erase scope expiry; driver doubles only.",
    (
        "test_hard_erase_issuance_flow.py",
        "test_orm_row_locks_work_but_raw_row_locks_refuse",
    ): "mocked direct ORM row-lock positive and raw row-lock refusals",
    (
        "test_hard_erase_fixture_flow.py",
        "test_fixture_sql_uses_executor_and_authority_does_not_escape",
    ): "offline migration fixture dispatch and runtime refusal; mock driver only",
    (
        "test_hard_erase_r2f_flow.py",
        "test_executing_wrappers_and_all_non_reads_refuse",
    ): "offline executing-wrapper negative fixtures; mock driver only",
    (
        "test_hard_erase_r2f_flow.py",
        "test_pure_reads_and_nonexecuting_explain_reach_driver",
    ): "offline pure-read positives; mock driver only",
    (
        "test_hard_erase_r2f_flow.py",
        "test_eight_omitted_models_refuse_execute_and_executemany",
    ): "offline credential-table refusal fixtures; mock driver only",
    (
        "test_hard_erase_issuance_flow.py",
        "test_ordinary_joined_update_has_no_snapshot_or_lifecycle_work",
    ): "mocked direct ORM dispatch positive, no SQL driver",
    (
        "test_hard_erase_issuance_flow.py",
        "test_application_callback_cannot_borrow_compiler_dispatch",
    ): "mocked raw and DELETE negative controls",
    (
        "test_hard_erase_sql_source.py",
        "test_backfill_boundary_matches_only_immutable_same_scope_shapes",
    ): "source-only exact-template detector fixture; no SQL execution",
    (
        "test_hard_erase_r2d_flow.py",
        "test_name_based_refusal_variants",
    ): "offline negative mock-driver assertions",
    (
        "test_hard_erase_r2d_db.py",
        "test_real_prism_registry_and_modifier_refusals",
    ): "DB refusal assertions before driver",
    (
        "test_hard_erase_sql_flow.py",
        "test_sealing_requires_typed_atomic_boundary",
    ): "mocked seal authority positive and refusal controls",
    (
        "test_hard_erase_r2_db.py",
        "test_cursor_execute_and_executemany_cannot_mutate_sensitive_tables",
    ): "DB negative fixture asserts refusal before driver execution",
    (
        "test_hard_erase_sql_flow.py",
        "test_raw_mutations_fail_before_driver_for_execute_and_executemany",
    ): "offline negative fixture; driver is a mock asserted not called",
    (
        "test_hard_erase_sql_flow.py",
        "test_permit_is_transaction_and_connection_bound",
    ): "offline authority-scope fixture; driver is a mock",
    (
        "test_hard_erase_sql_source.py",
        "test_native_and_dynamic_mutations_are_detected",
    ): "source-detector fixture; SQL is never executed",
}


def update_safety_declaration(tree):
    """The value of the module's one UPDATE_SAFETY literal dict, or None.

    scripts/check_update_safety.py reads that dict as review prose; nothing runs
    it. It qualifies only as a module-level assignment with that one name as its
    only target and a literal dict as its value, in a module whose syntax tree
    names UPDATE_SAFETY nowhere else: no other name, attribute, import, argument
    or string.
    """
    declarations = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "UPDATE_SAFETY"
        )
        or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "UPDATE_SAFETY"
        )
    ]
    mentions = sum(
        "UPDATE_SAFETY" in item
        for node in ast.walk(tree)
        for _field, value in ast.iter_fields(node)
        for item in (value if isinstance(value, list) else [value])
        if isinstance(item, str)
    )
    if len(declarations) != 1 or mentions != 1 or declarations[0].value is None:
        return None
    try:
        literal = ast.literal_eval(declarations[0].value)
    except (TypeError, ValueError):
        return None
    return declarations[0].value if isinstance(literal, dict) else None


def strings(tree, name=None):
    """Outer string expressions, excluding docstrings and, in a migration module
    (``name`` under a ``migrations`` folder), its one UPDATE_SAFETY declaration;
    retain dynamic provenance."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    in_migrations = name is not None and "migrations" in name.split("/")[:-1]
    declaration = update_safety_declaration(tree) if in_migrations else None
    declarations = {id(declaration)} if declaration is not None else set()

    def fold(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                value.value
                if isinstance(value, ast.Constant)
                else "{" + ast.unparse(value.value) + "}"
                for value in node.values
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = fold(node.left), fold(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    def visit(node):
        if id(node) in docstrings or id(node) in declarations:
            return
        value = fold(node)
        if value is not None:
            yield node.lineno, value
            return
        for child in ast.iter_child_nodes(node):
            yield from visit(child)

    return list(visit(tree))


# The only non-test source that may read a migration's UPDATE_SAFETY declaration.
# scripts/rehearse_update_compatibility.py also reads declarations, from inside a
# program string that update_safety_reads() cannot see; it takes only the
# regex-checked table names and runs no declaration text.
UPDATE_SAFETY_READERS = ("scripts/check_update_safety.py",)


def update_safety_reads(tree):
    """Lines that read UPDATE_SAFETY: an import naming it, a <module>.UPDATE_SAFETY
    attribute read, and getattr(..., "UPDATE_SAFETY") or any other string that is
    exactly the name."""
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if (
            isinstance(node, (ast.Import, ast.ImportFrom))
            and any("UPDATE_SAFETY" in alias.name.split(".") for alias in node.names)
        )
        or (isinstance(node, ast.Attribute) and node.attr == "UPDATE_SAFETY")
        or (isinstance(node, ast.Constant) and node.value == "UPDATE_SAFETY")
    )


def mutations(text, tables, *, dynamic=True):
    found = []
    for match in MUTATION.finditer(text):
        target = match.group("table").replace('"', "").split(".")[-1].lower()
        if target in tables or (dynamic and target.startswith(("{", "%"))):
            found.append(match.group(0))
    return found


def tracked_sources():
    # Keep the public helper name for the independent oracle and migration/COPY
    # probes, but discover the same shipped sources without a Git subprocess.
    for name in source_files(ROOT):
        yield name, ROOT / name


def model_census(sources, rule=None):
    rule = rule or POLICY["model_requires_protection"]
    all_tables, protected, model_names = set(), {}, {}
    sources = list(sources)
    labels = {}
    for _name, path in sources:
        if path.name != "apps.py":
            continue
        for node in ast.parse(read_source(ROOT, path)).body:
            if not isinstance(node, ast.ClassDef):
                continue
            attrs = {
                target.id: child.value.value
                for child in node.body
                if isinstance(child, ast.Assign)
                and isinstance(child.value, ast.Constant)
                for target in child.targets
                if isinstance(target, ast.Name)
            }
            if isinstance(attrs.get("name"), str):
                labels[attrs["name"]] = attrs.get("label", attrs["name"].split(".")[-1])
    for name, path in sources:
        if path.suffix != ".py" or not (
            "/models/" in name or name.endswith("/models.py")
        ):
            continue
        app = name.split("/")[1] if name.startswith("futureagi/") else ""
        for node in ast.walk(ast.parse(read_source(ROOT, path))):
            if not isinstance(node, ast.ClassDef) or not app:
                continue
            module = (
                name.removeprefix("futureagi/").removesuffix(".py").replace("/", ".")
            )
            package = module.split(".models")[0]
            table = labels.get(package, app) + "_" + node.name.lower()
            fields = {"id"}
            credential_fields = []
            for child in ast.walk(node):
                if not isinstance(child, ast.Assign):
                    continue
                names = [
                    target.id
                    for target in child.targets
                    if isinstance(target, ast.Name)
                ]
                if "db_table" in names and isinstance(child.value, ast.Constant):
                    table = child.value.value
                if isinstance(child.value, ast.Call):
                    call = child.value.func
                    if child in node.body:
                        verbose = next(
                            (
                                kw.value.value
                                for kw in child.value.keywords
                                if kw.arg == "verbose_name"
                                and isinstance(kw.value, ast.Constant)
                            ),
                            "",
                        )
                        credential_fields.extend(
                            type(
                                ast.unparse(call),
                                (),
                                {
                                    "name": field,
                                    "verbose_name": verbose,
                                    "db_column": next(
                                        (
                                            kw.value.value
                                            for kw in child.value.keywords
                                            if kw.arg == "db_column"
                                            and isinstance(kw.value, ast.Constant)
                                        ),
                                        None,
                                    ),
                                },
                            )()
                            for field in names
                        )
                    relation = isinstance(call, ast.Attribute) and call.attr in {
                        "ForeignKey",
                        "OneToOneField",
                    }
                    for field in names:
                        if relation:
                            column = field + "_id"
                            for kw in child.value.keywords:
                                if kw.arg == "db_column" and isinstance(
                                    kw.value, ast.Constant
                                ):
                                    column = kw.value.value
                            fields.add(column)
            all_tables.add(table)
            model_names[app, node.name] = table
            module = (
                name.removeprefix("futureagi/").removesuffix(".py").replace("/", ".")
            )
            if rule(module, node.name, table, credential_fields):
                protected[table] = fields
    return all_tables, protected, model_names


def model_tables(sources):
    return model_census(sources)[0]


def _function(tree, line):
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= line <= node.end_lineno
    ]
    return (
        min(functions, key=lambda node: node.end_lineno - node.lineno)
        if functions
        else None
    )


def _resolve(sql, tree, model_names):
    constants, imports = {}, {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                imports[item.asname or item.name] = (
                    node.module.split(".")[0],
                    item.name,
                )
        if isinstance(node, ast.Assign):
            value = None
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                value = node.value.value
            elif (
                isinstance(node.value, ast.Attribute) and node.value.attr == "db_table"
            ):
                owner = node.value.value
                if (
                    isinstance(owner, ast.Attribute)
                    and owner.attr == "_meta"
                    and isinstance(owner.value, ast.Name)
                ):
                    value = model_names.get(imports.get(owner.value.id))
            if value is not None:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = value
    return re.sub(r"\{([^{}]+)\}", lambda match: constants.get(match[1], match[0]), sql)


@lru_cache(maxsize=1)
def qualified_migration_routes():
    # A filename classification alone cannot qualify a broken product route.
    # Execute both unchanged historical directions through Django's executor and
    # cursor method bodies + installed guard, and assert runtime refusals too.
    probe = runpy.run_path(
        str(Path(__file__).with_name("test_hard_erase_migrations_flow.py"))
    )
    return (
        probe["history_dispatch"]()
        | probe["additional_history_dispatch"]()
        | probe["mcp_reconnect_history_dispatch"]()
    )


def classify(name, line, sql, tree, protected, model_names):
    fn = _function(tree, line)
    function = fn.name if fn else "<module>"
    exception = EXCEPTIONS.get((Path(name).name, function))
    if exception and name.startswith("futureagi/accounts/tests/"):
        return "EXCEPTION", exception
    runtime_exceptions = runpy.run_path(str(SERVICES / "hard_erase_sql_exceptions.py"))[
        "SQL_EXCEPTIONS"
    ]
    for exception in runtime_exceptions:
        if (
            name == "futureagi/" + exception["file"]
            and function == exception["function"]
            and sha256(sql.encode()).hexdigest() == exception["fingerprint"]
            and any(
                isinstance(node, ast.Call)
                and node.lineno == exception["line"]
                and node.lineno <= line <= node.end_lineno
                for node in ast.walk(tree)
            )
        ):
            return "EXCEPTION", "exact production callsite: " + exception[
                "justification"
            ]
    exact = SOURCE_SITES.get((name, function, sha256(sql.encode()).hexdigest()))
    if exact:
        status, reason = exact
        if status == "MIGRATION_TEMPLATE":
            if name in qualified_migration_routes():
                return "ROUTED", reason
            return "UNCLASSIFIED", "historical template lost executor qualification"
        if status == "GUARDED_COPY":
            probe = runpy.run_path(
                str(Path(__file__).with_name("test_hard_erase_cursor_flow.py"))
            )
            probe[
                "ProductCopyTests"
            ]().test_actual_ingestion_models_work_and_protected_model_refuses()
            return "ROUTED", reason
        if status == "FIXTURE":
            # The preserved SQL is deliberately tested through an ephemeral
            # MigrationExecutor, never whitelisted for runtime execution.
            if not any(
                isinstance(node, ast.ImportFrom)
                and node.module == "accounts.tests.hard_erase_fixture"
                and any(
                    alias.name == "migration_fixture_cursor" and not alias.asname
                    for alias in node.names
                )
                for node in ast.walk(tree)
            ):
                return "UNCLASSIFIED", "fixture lost its migration-boundary import"
            scopes = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.With)
                and node.lineno <= line <= node.end_lineno
                and any(
                    ast.unparse(item.context_expr)
                    == "migration_fixture_cursor(connection)"
                    for item in node.items
                )
            ]
            # A module constant is dispatched by the one exact helper below.
            if function == "<module>":
                scopes = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.With)
                    and any(
                        ast.unparse(item.context_expr)
                        == "migration_fixture_cursor(connection)"
                        for item in node.items
                    )
                    and "COLLECTORS_UPSERT" in ast.unparse(node)
                ]
            if not scopes:
                return (
                    "UNCLASSIFIED",
                    "fixture SQL is not inside the migration fixture cursor",
                )
            return "ROUTED", reason
        if status == "TEST_DATABASE_ONLY":
            # TEST_DATABASE_ONLY: a string in a file under */tests/ whose native
            # connection is opened only to the test runner's own database.
            if "/tests/" not in name:
                return "UNCLASSIFIED", "test-database-only SQL outside a tests folder"
            if not any(
                isinstance(node, ast.ImportFrom)
                and node.module == "mcp_server.tests.test_database_probe"
                and any(
                    alias.name == "test_database_connection" and not alias.asname
                    for alias in node.names
                )
                for node in ast.walk(tree)
            ):
                return (
                    "UNCLASSIFIED",
                    "test-database-only SQL lost its checked test-database connection",
                )
            return status, reason
        return status, reason
    resolved = _resolve(sql, tree, model_names)
    routes = {
        "futureagi/accounts/migrations/0015_backfill_rbac_data.py": "backfill_ws_org_membership_fk",
        "futureagi/accounts/migrations/0022_backfill_ws_org_membership_fk.py": "backfill_ws_org_membership_fk",
        "futureagi/accounts/migrations/0023_create_missing_org_memberships.py": "create_missing_org_memberships",
    }
    try:
        backfill = BACKFILL["membership_backfill_kind"](resolved)
    except ValueError:
        backfill = None  # Source-only fragments still require classification below.
    if backfill is not None:
        if name == "futureagi/accounts/services/hard_erase_backfill.py":
            return "ROUTED", "exact template for the typed membership backfill boundary"
        caller = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == routes.get(name)
            ),
            None,
        )
        if caller and "connection = schema_editor.connection" in ast.unparse(caller):
            return (
                "ROUTED",
                routes[name]
                + ": exact same-person/same-org NULL-FK fill uses guarded Django schema-editor connection",
            )
        return "UNCLASSIFIED", "matching backfill text has no qualified Django caller"
    # The backfill's final pinned UPDATE is constructed from an exact matched
    # statement, not from an arbitrary caller SQL string.
    if (
        name.endswith("/services/hard_erase_backfill.py")
        and function == "execute_membership_backfill"
    ):
        return (
            "ROUTED",
            "exact-SQL typed boundary pins selected IDs under per-key and row locks",
        )
    if (
        name == "futureagi/accounts/api_key_lookup.py"
        and function == "_write_where_unchanged"
    ):
        body = ast.unparse(fn)
        if (
            "kept = _KEPT_UNLESS_SEALED" in body
            and "for name in kept" in body
            and "assignments = [f\"{column['secret_key']} = ''\"]" in body
            and "{', '.join(assignments)}" in sql
            and "sql_authority(using, 'seal')" in ast.unparse(fn)
        ):
            return (
                "ROUTED",
                "credential sealing uses typed atomic seal authority and only seal-field assignments",
            )
        return "UNCLASSIFIED", "credential sealing assignment shape changed"
    if any(
        match.group("table").startswith(("{", "%"))
        for match in MUTATION.finditer(resolved)
    ):
        return "UNCLASSIFIED", "dynamic mutation target is unresolved"
    try:
        sensitive = POLICY["protected_mutations"](resolved, protected)
    except ValueError:
        return (
            "UNCLASSIFIED",
            "unparseable candidate requires exact source classification",
        )
    # Historical schema SQL must have a working executor route,
    # never a filename exemption at runtime or a REFUSED-but-green source gate.
    migration_history = {
        "futureagi/accounts/migrations/0026_authtoken_scope.py": {
            "_add_foreign_key",
            "forwards",  # CREATE INDEX CONCURRENTLY also names the protected table.
            "<module>",
        },
        "futureagi/accounts/migrations/0035_gateway_retirement_fence.py": {"<module>"},
        "futureagi/tracer/migrations/0102_reconcile_report_flags.py": {
            "backfill_has_issues"
        },
    }
    if sensitive and function in migration_history.get(name, set()):
        if name in qualified_migration_routes():
            return (
                "ROUTED",
                "migration boundary: forward/reverse executor dispatch reaches mock driver; outside dispatch refuses (DB replay NOT_RUN)",
            )
        return "UNCLASSIFIED", "historical migration has no working executor route"
    if sensitive:
        return (
            "UNCLASSIFIED",
            "mutating statement names a protected table outside a typed boundary",
        )
    return (
        "OUT_OF_SCOPE",
        "non-mutating statement or no protected identifier token",
    )


def source_policy_mutations(sql, protected):
    try:
        found = POLICY["protected_mutations"](sql, protected)
    except ValueError:
        # Runtime lexing now refuses malformed/unsupported constructs, including
        # non-SQL prose. Source strings are not driver inputs: retain conservative
        # identifier candidates without invoking the same failing lexer again.
        names = {value.lower() for value in re.findall(r"\b\w+\b", sql)}
        found = [("unparsed", name, frozenset()) for name in names & set(protected)]
        found.append(("unparsed", "<execution>", frozenset()))
    # EXECUTE/CALL/DO have no table requirement. Avoid treating prose such as
    # "Do not ..." in prompts/help as SQL; runtime never has this source filter.
    executing = re.search(
        r"(?:^|;)\s*(?:explain\s+(?:\([^;]*?\)\s*|analyze\s+)?)?(?:execute\s+(?:[\w.]+|\"[^\"]+\")(?:\s*\([^;]*\))?\s*;?\s*$"
        r"|call\s+[\w.]+\s*\(|do\s+(?:language\s+\w+\s+)?(?:\$|'))",
        sql,
        re.I | re.M,
    )
    return [row for row in found if row[1] != "<execution>" or executing]


def classifications():
    sources = list(tracked_sources())
    tables, protected, model_names = model_census(sources)
    rows = []
    for name, path in sources:
        text = read_source(ROOT, path)
        tree = (
            ast.parse(text)
            if path.suffix == ".py"
            else ast.Module(body=[], type_ignores=[])
        )
        candidates = strings(tree, name) if path.suffix == ".py" else [(1, text)]
        dynamic = bool(re.search(r"psycopg|asyncpg|pg8000|\.cursor\(", text))
        for line, sql in candidates:
            if mutations(sql, tables, dynamic=dynamic) or (
                re.search(
                    r"\b(select|into|share|delete|truncate|update|insert|merge|alter|drop|create|grant|revoke|prepare|execute|deallocate|call|do|copy|lock|vacuum|cluster|reindex|comment|security|analyze|explain)\s+",
                    sql,
                    re.I,
                )
                and source_policy_mutations(sql, protected)
            ):
                status, reason = classify(name, line, sql, tree, protected, model_names)
                rows.append(
                    {"site": f"{name}:{line}", "status": status, "reason": reason}
                )
    return rows


class SQLSourceGuard(unittest.TestCase):
    def test_native_and_dynamic_mutations_are_detected(self):
        for sql in (
            'DELETE FROM "accounts_user"',
            "UPDATE public.accounts_user SET id=1",
            "TRUNCATE TABLE accounts_user",
            "DELETE FROM {table}",
            "WITH x AS (SELECT 1) DELETE FROM accounts_user",
        ):
            self.assertTrue(mutations(sql, {"accounts_user"}), sql)
        tree = ast.parse('cursor.execute("DE" + "LETE FROM accounts_user")')
        self.assertTrue(mutations(strings(tree)[0][1], {"accounts_user"}))

    def test_backfill_boundary_matches_only_immutable_same_scope_shapes(self):
        originals = []
        for file in (
            "0015_backfill_rbac_data.py",
            "0022_backfill_ws_org_membership_fk.py",
            "0023_create_missing_org_memberships.py",
        ):
            tree = ast.parse(
                (ROOT / "futureagi/accounts/migrations" / file).read_text()
            )
            sql = next(
                sql
                for _, sql in strings(tree)
                if "UPDATE accounts_workspacemembership" in sql
            )
            originals.append(sql)
            self.assertIsNotNone(BACKFILL["membership_backfill_kind"](sql))
            self.assertIsNone(
                BACKFILL["membership_backfill_kind"](
                    sql.replace("om.user_id = wm.user_id", "TRUE")
                )
            )
            self.assertIsNone(
                BACKFILL["membership_backfill_kind"](
                    sql.replace("om.organization_id = w.organization_id", "TRUE")
                )
            )
        self.assertEqual(len(originals), 3)

    def test_repository_has_zero_unclassified_candidates(self):
        violations = [
            row for row in classifications() if row["status"] == "UNCLASSIFIED"
        ]
        self.assertEqual(violations, [], str(violations))

    def test_mcp_reconnect_migration_sql_is_classified(self):
        files = {
            "futureagi/mcp_server/migrations/0005_personal_key_reconnect.py",
            "futureagi/mcp_server/migrations/0007_reconnect_required_db_default.py",
            "futureagi/mcp_server/tests/test_reconnect_default_migration.py",
            "futureagi/mcp_server/tests/test_reconnect_migration.py",
            "futureagi/mcp_server/tests/test_reconnect_migration_locks.py",
        }
        rows = {
            row["site"]: row["status"]
            for row in classifications()
            if row["site"].rsplit(":", 1)[0] in files
        }
        self.assertEqual(
            rows,
            {
                "futureagi/mcp_server/migrations/0005_personal_key_reconnect.py:76": "ROUTED",
                "futureagi/mcp_server/migrations/0005_personal_key_reconnect.py:80": "ROUTED",
                "futureagi/mcp_server/migrations/0007_reconnect_required_db_default.py:61": "ROUTED",
                "futureagi/mcp_server/tests/test_reconnect_default_migration.py:48": "TEST_DATABASE_ONLY",
                "futureagi/mcp_server/tests/test_reconnect_default_migration.py:54": "TEST_DATABASE_ONLY",
                "futureagi/mcp_server/tests/test_reconnect_default_migration.py:59": "TEST_DATABASE_ONLY",
                "futureagi/mcp_server/tests/test_reconnect_default_migration.py:137": "TEST_DATABASE_ONLY",
                "futureagi/mcp_server/tests/test_reconnect_migration_locks.py:66": "TEST_DATABASE_ONLY",
                "futureagi/mcp_server/tests/test_reconnect_migration_locks.py:216": "TEST_DATABASE_ONLY",
                "futureagi/mcp_server/tests/test_reconnect_migration_locks.py:397": "TEST_DATABASE_ONLY",
                "futureagi/mcp_server/tests/test_reconnect_migration_locks.py:411": "TEST_DATABASE_ONLY",
            },
        )

    def test_test_database_only_outside_tests_is_unclassified(self):
        checked = ast.parse(
            "from mcp_server.tests.test_database_probe import test_database_connection\n"
            "STATEMENT = 'native test statement'\n"
        )
        unchecked = ast.parse("STATEMENT = 'native test statement'\n")
        for name, tree, expected in (
            # Outside */tests/, even with the checked connection imported.
            ("futureagi/mcp_server/database_probe.py", checked, "UNCLASSIFIED"),
            # Under */tests/ without the checked connection.
            ("futureagi/mcp_server/tests/database_probe.py", unchecked, "UNCLASSIFIED"),
            # Under */tests/ with the checked connection: the class holds.
            (
                "futureagi/mcp_server/tests/database_probe.py",
                checked,
                "TEST_DATABASE_ONLY",
            ),
        ):
            ((line, sql),) = strings(tree)
            key = (name, "<module>", sha256(sql.encode()).hexdigest())
            SOURCE_SITES[key] = ("TEST_DATABASE_ONLY", "a native test statement")
            try:
                status, reason = classify(name, line, sql, tree, {}, {})
            finally:
                del SOURCE_SITES[key]
            self.assertEqual(status, expected, (name, reason))

    def test_only_the_update_gate_reads_migration_declarations(self):
        # strings() leaves UPDATE_SAFETY prose out because nothing runs it:
        # outside tests, only the update gate may read a declaration at all.
        readers = {}
        for name, path in tracked_sources():
            parts = name.split("/")
            if (
                path.suffix != ".py"
                or "tests" in parts[:-1]
                or parts[-1].startswith("test_")
                or parts[-1] == "conftest.py"
            ):
                continue
            lines = update_safety_reads(ast.parse(read_source(ROOT, path)))
            if lines:
                readers[name] = lines
        self.assertEqual(sorted(readers), sorted(UPDATE_SAFETY_READERS), readers)

    def test_only_a_sole_literal_update_safety_declaration_is_skipped(self):
        migration = "futureagi/tracer/migrations/0001_example.py"
        declaration = 'UPDATE_SAFETY = {"mitigation": "review prose"}\n'
        self.assertEqual(strings(ast.parse(declaration), migration), [])
        annotated = declaration.replace(" =", ": dict =")
        self.assertEqual(strings(ast.parse(annotated), migration), [])
        for source in (
            declaration + "print(UPDATE_SAFETY)\n",
            declaration + 'NAME = "UPDATE_SAFETY"\n',
            declaration.replace("UPDATE_SAFETY =", "UPDATE_SAFETY = OTHER ="),
            declaration.replace("{", "dict(x={").replace("}", "})"),
            "def f():\n    " + declaration,
            # Any other name keeps its strings, whatever the dict holds.
            declaration.replace("UPDATE_SAFETY", "SAFETY_NOTES"),
        ):
            with self.subTest(source=source):
                kept = [text for _, text in strings(ast.parse(source), migration)]
                self.assertIn("review prose", kept)
        # Outside a migrations folder the same declaration is scanned like any
        # other string.
        for name in ("futureagi/tracer/services/example.py", None):
            with self.subTest(name=name):
                kept = [text for _, text in strings(ast.parse(declaration), name)]
                self.assertIn("review prose", kept)


if __name__ == "__main__":
    unittest.main()
