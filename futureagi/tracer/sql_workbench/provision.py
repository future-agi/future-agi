"""Operator-only SQL DDL generation. No admin credentials enter the app API."""

import hashlib
import re
from datetime import timezone

import sqlglot
from sqlglot import exp

from .catalog import Scope, sources
from .runner import QUERY_SETTINGS

SAFE_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")


def name(value):
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise ValueError(
            "Use lowercase SQL names (letters, digits, underscore; at most 48 characters)."
        )
    return value


def direct_access_ddl(scope: Scope, *, database, principal, password, cidr):
    """One invoker + non-login scoped definer, explicit view grants and row policies.

    Never reuse these accounts for the application runner or another tenant. The
    returned statements intentionally do not use IF NOT EXISTS: a name collision
    must fail rather than reuse an account with unknown privileges/policies.
    """
    import ipaddress

    database, principal = name(database), name(principal)
    if len(password) < 24:
        raise ValueError(
            "Supply a password of at least 24 characters in SQL_DIRECT_PASSWORD."
        )
    network = str(ipaddress.ip_network(cidr, strict=True))
    if network in {"0.0.0.0/0", "::/0"}:
        raise ValueError("Restrict the account to a specific BI/notebook network.")
    view_db = name(f"sql_{principal}")
    definer = name(f"sqld_{principal}")
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    profile = ", ".join(
        (
            f"{key} = '{value}' READONLY"
            if isinstance(value, str)
            else f"{key} = {value} READONLY"
        )
        for key, value in QUERY_SETTINGS.items()
    )
    profile += ", max_concurrent_queries_for_user = 2 READONLY"
    statements = [
        f"CREATE USER {definer} IDENTIFIED WITH no_password HOST NONE SETTINGS {profile}",
        f"CREATE USER {principal} IDENTIFIED WITH sha256_hash BY '{password_hash}' HOST IP '{network}' SETTINGS {profile}",
        f"CREATE DATABASE {view_db}",
    ]
    # BI views are rolling windows, never stale snapshots pinned at provisioning.
    templates = {}
    boundaries = {
        scope.start.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ): f"now64(6, 'UTC') - INTERVAL {int((scope.end - scope.start).total_seconds())} SECOND",
        scope.end.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ): "now64(6, 'UTC')",
    }
    for dataset, source in sources(scope).items():
        tree = sqlglot.parse_one(source, read="clickhouse")
        for node in list(tree.find_all(exp.Anonymous)):
            if (
                node.name.lower() == "todatetime64"
                and node.expressions
                and isinstance(node.expressions[0], exp.Literal)
            ):
                replacement = boundaries.get(node.expressions[0].this)
                if replacement:
                    node.replace(sqlglot.parse_one(replacement, read="clickhouse"))
        templates[dataset] = tree.sql(dialect="clickhouse")
    # Server-authored predicates are reused verbatim for row policies as defense
    # in depth, in addition to the view WHERE clauses. Every physical table gets
    # its OWN predicate; users receive no SELECT privilege on the base tables.
    predicates = {}
    for text in templates.values():
        tree = sqlglot.parse_one(text, read="clickhouse")
        for select in tree.find_all(exp.Select):
            from_ = select.args.get("from_")
            physical = from_.this if from_ else None
            if isinstance(physical, exp.Final):
                physical = physical.this
            if isinstance(physical, exp.Table) and select.args.get("where"):
                predicates.setdefault(physical.name, select.args["where"].this.copy())
    for table, predicate in predicates.items():
        # Row policies containing typed target subqueries are supported on the
        # qualified CH25 deployment; grants remain limited to these tables.
        for reference in predicate.find_all(exp.Table):
            reference.set("db", exp.to_identifier(database))
        condition = predicate.sql(dialect="clickhouse")
        statements.append(f"GRANT SELECT ON {database}.{name(table)} TO {definer}")
        statements.append(
            f"CREATE ROW POLICY {principal}_{name(table)} ON {database}.{name(table)} FOR SELECT USING {condition} TO {definer}"
        )
    for dataset, text in templates.items():
        tree = sqlglot.parse_one(text, read="clickhouse")
        for table in tree.find_all(exp.Table):
            table.set("db", exp.to_identifier(database))
        statements.append(
            f"CREATE VIEW {view_db}.{dataset} DEFINER = {definer} SQL SECURITY DEFINER AS {tree.sql(dialect='clickhouse')}"
        )
        statements.append(f"GRANT SELECT ON {view_db}.{dataset} TO {principal}")
    return statements


def service_access_ddl(
    *,
    database,
    principal,
    password,
    cidr,
    eval_table="tracer_eval_logger",
    include_derived_metrics=False,
):
    """Application-only principal. Never issue this cross-project login to BI users."""
    import ipaddress

    database, principal = name(database), name(principal)
    network = str(ipaddress.ip_network(cidr, strict=True))
    if network in {"0.0.0.0/0", "::/0"} or len(password) < 24:
        raise ValueError(
            "Use a restricted application subnet and a password of at least 24 characters."
        )
    if eval_table not in {"tracer_eval_logger", "tracer_eval_logger_v2"}:
        raise ValueError("Unsupported evaluation table.")
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    profile = ", ".join(
        (
            f"{key} = '{value}' READONLY"
            if isinstance(value, str)
            else f"{key} = {value} READONLY"
        )
        for key, value in QUERY_SETTINGS.items()
    )
    profile += ", max_concurrent_queries_for_user = 2 READONLY"
    statements = [
        f"CREATE USER {principal} IDENTIFIED WITH sha256_hash BY '{password_hash}' HOST IP '{network}' SETTINGS {profile}"
    ]
    tables = [
        "spans",
        "traces",
        "trace_sessions",
        "end_users",
        eval_table,
        "model_hub_score",
    ]
    if include_derived_metrics:
        tables += [
            "derived_metrics",
            "derived_metric_results",
            "derived_metric_commits",
        ]
    for table in tables:
        statements.append(f"GRANT SELECT ON {database}.{table} TO {principal}")
    return statements
