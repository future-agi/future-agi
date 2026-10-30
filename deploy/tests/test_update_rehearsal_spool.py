"""Daemon-free tests for the replay-aware observed-catalog witness."""
from __future__ import annotations

import base64
import hashlib
import json
import unittest
from unittest.mock import patch

from scripts.update_rehearsal_spool import (
    COLUMN_LAYOUT_QUERY, DATABASE, KEY_COLUMNS, KEY_ORDER, TABLE_LAYOUT_QUERY,
    TABLES, SPOOL_DIRECTORY, VALUE_COLUMNS, VALUE_ORDER, KEYS_QUERY, VALUES_QUERY,
    ObservedSpoolWitness, ObservedSpoolWitnessError, privilege_sql,
)


ORG = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
OTHER_ORG = "44444444-4444-4444-8444-444444444444"
OTHER_WORKSPACE = "55555555-5555-4555-8555-555555555555"
OTHER_PROJECT = "66666666-6666-4666-8666-666666666666"
NAMESPACE = "fagi-upd-e2e-driver-m1-source-0123456789ab"
STAMP = "2026-10-29 12:13:14.123456"


def scope_rows(org=ORG, workspace=WORKSPACE, project=PROJECT):
    return {"organization_id": org, "workspace_id": workspace, "project_id": project}


def key_row(name, *, org=ORG, workspace=WORKSPACE, project=PROJECT, stamp=STAMP):
    return dict(scope_rows(org, workspace, project), source_kind="custom_attribute",
                attribute_key=name, attribute_type="string", key_folded=name.lower(),
                first_seen=stamp, last_seen=stamp)


def value_row(name, value, *, org=ORG, workspace=WORKSPACE, project=PROJECT, stamp=STAMP, fingerprint="a" * 64):
    return dict(scope_rows(org, workspace, project), source_kind="custom_attribute",
                attribute_key=name, attribute_type="string", value_fingerprint=fingerprint,
                value_json=json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                value_search_text_folded=value.casefold(), first_seen=stamp, last_seen=stamp)


def spool_record(keys, values):
    # In-memory wire fixture matching observedcatalog.Encode's actual envelope;
    # tests never write these bytes to a spool path or claim product production.
    raw = json.dumps({"format": "futureagi.observed-attributes", "version": 1,
                      "keys": keys, "values": values}, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return {"path": SPOOL_DIRECTORY + "/observed-" + digest + ".json",
            "sha256": digest, "payload_b64": base64.b64encode(raw).decode("ascii")}


def expected_grants():
    return [f"GRANT SELECT, INSERT ON `{DATABASE}`.`{table}` TO observed_catalog_writer"
            for table in TABLES]


def schema_rows():
    order = {
        "observed_attribute_keys": KEY_ORDER.replace(",", ", "),
        "observed_attribute_values": VALUE_ORDER.replace(",", ", "),
    }
    return [dict(name=table, engine="AggregatingMergeTree", sorting_key=order[table],
                 partition_key="cityHash64(workspace_id) % 64") for table in TABLES]


def column_rows():
    rows = []
    for table, columns in ((TABLES[0], KEY_COLUMNS), (TABLES[1], VALUE_COLUMNS)):
        for position, (name, kind) in enumerate(columns, 1):
            rows.append(dict(table=table, name=name, type=kind, position=position))
    return rows


class FakeDriver:
    project = "fagi-upd-e2e-driver-m1-aaaaaaaaaaaa"

    def __init__(self):
        self.spool = []
        self.index = {"keys": [], "values": []}
        self.grants = expected_grants()
        self.commands = []

    def app_python(self, source, argument):
        from scripts.update_rehearsal_spool import SPOOL_SCAN_PROGRAM
        if source != SPOOL_SCAN_PROGRAM:
            raise AssertionError("unexpected product operation")
        self.last_scan = argument
        return list(self.spool)

    def clickhouse(self, sql, *, params=None):
        if sql == "SHOW GRANTS FOR observed_catalog_writer FORMAT JSONEachRow":
            return [{"grants": grant} for grant in self.grants]
        if sql == TABLE_LAYOUT_QUERY:
            return schema_rows()
        if sql == COLUMN_LAYOUT_QUERY:
            return column_rows()
        for action in ("REVOKE INSERT", "GRANT INSERT"):
            if sql.startswith(action):
                self.commands.append(sql)
                table = next(name for name in TABLES if f".`{name}`" in sql)
                if action.startswith("REVOKE"):
                    self.grants = [grant for grant in self.grants
                                   if f"`{table}`" not in grant]
                    self.grants.append(f"GRANT SELECT ON `{DATABASE}`.`{table}` TO observed_catalog_writer")
                else:
                    self.grants = [grant for grant in self.grants
                                   if f"`{table}`" not in grant]
                    self.grants.append(f"GRANT SELECT, INSERT ON `{DATABASE}`.`{table}` TO observed_catalog_writer")
                return []
        if sql.startswith("SELECT organization_id, workspace_id, project_id, source_kind,"):
            if "observed_attribute_keys FINAL" in sql:
                return list(self.index["keys"])
            if "observed_attribute_values FINAL" in sql:
                return list(self.index["values"])
        raise AssertionError("unexpected ClickHouse operation")


def make_witness(driver=None):
    driver = driver or FakeDriver()
    witness = ObservedSpoolWitness(driver)
    return driver, witness


def capture(witness, label="source", namespace=NAMESPACE, project=PROJECT):
    return witness.capture(label, namespace, ORG, WORKSPACE, project)


def add_target_record(driver):
    keys = [key_row("qualification.namespace")]
    values = [value_row("qualification.namespace", NAMESPACE)]
    row = spool_record(keys, values)
    driver.spool = [row]
    return row, keys, values


class ObservedSpoolWitnessTests(unittest.TestCase):
    def test_sql_uses_clickhouse_format_datetime_not_the_collectors_go_helper_name(self):
        # ClickHouse src/Functions/formatDateTime.cpp registers formatDateTime;
        # formatDateTime64 is a local Go serializer helper, not a SQL function.
        for query in (KEYS_QUERY, VALUES_QUERY):
            self.assertNotIn('formatDateTime64(', query)
            for column in ('first_seen','last_seen'):
                self.assertIn("formatDateTime("+column+", '%Y-%m-%d %H:%i:%S.%f', 'UTC')", query)

    def test_bootstrap_delivery_must_be_observed_without_manual_grant_repair(self):
        driver, witness = make_witness()
        witness.pause_delivery()
        _record, keys, values = add_target_record(driver)
        expected = capture(witness)
        commands = list(driver.commands)
        with self.assertRaisesRegex(ObservedSpoolWitnessError, 'did not restore'):
            witness.observe_bootstrap_delivery('source')
        self.assertEqual(driver.commands, commands)
        driver.grants = expected_grants()
        driver.spool = []
        driver.index = {'keys': keys, 'values': values}
        self.assertEqual(witness.observe_bootstrap_delivery('source'), expected)
        self.assertEqual(driver.commands, commands)
        self.assertFalse(witness._paused)

    def test_persistent_backlog_does_not_pass_the_bootstrap_replay_oracle(self):
        driver, witness = make_witness()
        witness.pause_delivery()
        add_target_record(driver)
        capture(witness)
        driver.grants = expected_grants()
        commands = list(driver.commands)
        with patch('scripts.update_rehearsal_spool.time.monotonic', side_effect=[0, 0, 61]), \
             self.assertRaisesRegex(ObservedSpoolWitnessError, 'did not replay'):
            witness.observe_bootstrap_delivery('source')
        self.assertEqual(driver.commands, commands)
        self.assertTrue(witness._paused)

    def test_captures_real_wire_payload_and_replays_to_exact_index_rows(self):
        driver, witness = make_witness()
        _record, keys, values = add_target_record(driver)
        first = capture(witness)

        self.assertEqual(first["domain"], "shared_files")
        self.assertEqual(first["origin"], "product_spool")
        self.assertEqual(driver.last_scan, {"namespace": NAMESPACE, "project_id": PROJECT})
        driver.spool = []
        driver.index = {"keys": keys, "values": values}
        self.assertEqual(witness.read("source"), first)

    def test_partial_replay_unions_pending_records_with_index_rows(self):
        driver, witness = make_witness()
        marker_key = key_row("qualification.namespace")
        marker_value = value_row("qualification.namespace", NAMESPACE)
        other_key = key_row("service.name")
        other_value = value_row("service.name", "rehearsal-service", fingerprint="b" * 64)
        marker_record = spool_record([marker_key], [marker_value])
        other_record = spool_record([other_key], [other_value])
        driver.spool = [marker_record, other_record]
        expected = capture(witness)

        # Replay of the marker chunk completed, while the second actual spool
        # record remains pending; the union must still equal the captured rows.
        driver.spool = [other_record]
        driver.index = {"keys": [marker_key], "values": [marker_value]}
        self.assertEqual(witness.read("source"), expected)

    def test_foreign_tenant_and_namespace_rows_do_not_enter_the_witness(self):
        driver, witness = make_witness()
        target_key = key_row("qualification.namespace")
        target_value = value_row("qualification.namespace", NAMESPACE)
        foreign_key = key_row("qualification.namespace", org=OTHER_ORG,
                              workspace=OTHER_WORKSPACE, project=OTHER_PROJECT)
        foreign_value = value_row("qualification.namespace", "other-tenant",
                                  org=OTHER_ORG, workspace=OTHER_WORKSPACE, project=OTHER_PROJECT,
                                  fingerprint="c" * 64)
        driver.spool = [spool_record([target_key, foreign_key], [target_value, foreign_value])]
        witness_row = capture(witness)
        driver.spool = []
        driver.index = {"keys": [target_key], "values": [target_value]}
        self.assertEqual(witness.read("source"), witness_row)
        self.assertEqual(len(witness._captures["source"]["expected"]["keys"]), 1)
        self.assertEqual(len(witness._captures["source"]["expected"]["values"]), 1)

    def test_wrong_tenant_namespace_is_not_attributable(self):
        driver, witness = make_witness()
        driver.spool = [spool_record(
            [key_row("qualification.namespace", org=OTHER_ORG,
                     workspace=OTHER_WORKSPACE, project=OTHER_PROJECT)],
            [value_row("qualification.namespace", NAMESPACE, org=OTHER_ORG,
                       workspace=OTHER_WORKSPACE, project=OTHER_PROJECT)])]
        with self.assertRaises(ObservedSpoolWitnessError):
            capture(witness)

    def test_corrupt_record_is_refused(self):
        driver, witness = make_witness()
        raw = b'{"format":"futureagi.observed-attributes","version":1,"keys":'
        digest = hashlib.sha256(raw).hexdigest()
        driver.spool = [{"path": SPOOL_DIRECTORY + "/observed-" + digest + ".json",
                         "sha256": digest,
                         "payload_b64": base64.b64encode(raw).decode("ascii")}]
        with self.assertRaises(ObservedSpoolWitnessError):
            capture(witness)

    def test_spool_observation_file_bound_is_enforced(self):
        driver, witness = make_witness()
        record = spool_record([key_row("qualification.namespace")],
                              [value_row("qualification.namespace", NAMESPACE)])
        driver.spool = [record] * 33
        with self.assertRaises(ObservedSpoolWitnessError):
            capture(witness)

    def test_assert_absent_checks_both_pending_spool_and_index(self):
        driver, witness = make_witness()
        _record, keys, values = add_target_record(driver)
        capture(witness)
        with self.assertRaisesRegex(ObservedSpoolWitnessError, "remains in spool"):
            witness.assert_absent("source")
        driver.spool = []
        driver.index = {"keys": keys, "values": values}
        with self.assertRaisesRegex(ObservedSpoolWitnessError, "remains in index"):
            witness.assert_absent("source")
        driver.index = {"keys": [], "values": []}
        self.assertTrue(witness.assert_absent("source"))

    def test_privilege_ddl_is_fixed_and_restoration_is_exact(self):
        driver, witness = make_witness()
        witness.pause_delivery()
        self.assertEqual(driver.commands, [
            "REVOKE INSERT ON `property_catalog`.`observed_attribute_keys` FROM observed_catalog_writer",
            "REVOKE INSERT ON `property_catalog`.`observed_attribute_values` FROM observed_catalog_writer",
        ])
        witness.restore_delivery()
        self.assertEqual(driver.commands[-2:], [
            "GRANT INSERT ON `property_catalog`.`observed_attribute_keys` TO observed_catalog_writer",
            "GRANT INSERT ON `property_catalog`.`observed_attribute_values` TO observed_catalog_writer",
        ])
        self.assertEqual(sorted(driver.grants), sorted(expected_grants()))
        self.assertEqual(privilege_sql("REVOKE", TABLES[0]), driver.commands[0])
        with self.assertRaises(ObservedSpoolWitnessError):
            privilege_sql("GRANT", "observed_attribute_values; DROP TABLE x")

    def test_bootstrap_restoration_is_observed_without_extra_grant(self):
        driver, witness = make_witness()
        witness.pause_delivery()
        driver.grants = expected_grants()  # normal bootstrap reinstated them
        command_count = len(driver.commands)
        witness.restore_delivery()
        self.assertEqual(len(driver.commands), command_count)
        self.assertEqual(sorted(driver.grants), sorted(expected_grants()))

    def test_unsupported_role_or_grant_layout_refuses_before_mutation(self):
        driver, witness = make_witness()
        driver.grants.append("GRANT observed_catalog_writer TO another_role")
        with self.assertRaises(ObservedSpoolWitnessError):
            witness.pause_delivery()
        self.assertEqual(driver.commands, [])

    def test_unsupported_table_layout_refuses_before_mutation(self):
        driver, witness = make_witness()
        original = driver.clickhouse

        def altered(sql, *, params=None):
            if sql == TABLE_LAYOUT_QUERY:
                rows = schema_rows()
                rows[0]["engine"] = "MergeTree"
                return rows
            return original(sql, params=params)

        driver.clickhouse = altered
        with self.assertRaises(ObservedSpoolWitnessError):
            witness.pause_delivery()
        self.assertEqual(driver.commands, [])


if __name__ == "__main__":
    unittest.main()
