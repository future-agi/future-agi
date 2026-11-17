"""Checks on the demo datasets seeded into every new organisation.

What is checked: no blocklisted terms in any file; no web addresses outside the image file;
no cell with an error status or error text in any file; in the two RAG files, no row equal to
a row of the earlier files (by row hash) and every mirrored field equal to its primary cell;
in the image file, no row equal to an earlier row that carried an error cell.

DB-free: execute with python -I -B -m unittest; no application settings or network imports.
"""

import hashlib
import json
import re
import unittest
from pathlib import Path

DEMO = Path(__file__).resolve().parents[1] / "demo_dataset"
PRIOR_ROW_HASHES = Path(__file__).resolve().parent / "fixtures" / "demo_dataset_prior_row_hashes.json"
RAG_FILES = ("table_data.json", "experiment.json")
ALL_FILES = RAG_FILES + ("img_dataset_data.json",)
EXPECTED_ROWS = 50

# Word-boundary, case-insensitive. Kept here in the test only, never in a product file.
BLOCKED_TERMS = {
    "adult": [
        "sex", "sexy", "sexual", "sexually", "sex chat", "porn", "porno", "pornography",
        "nude", "nudes", "nudity", "naked", "webcam", "webcams", "cam girl", "camgirl",
        "escort", "escorts", "xxx", "erotic", "fetish", "hookup", "milf", "horny",
        "adult chat", "adult dating", "dating site", "orgasm", "onlyfans", "strip club",
        "stripper", "lingerie", "viagra", "cialis", "hentai", "nsfw",
    ],
    "gambling": [
        "bet", "bets", "betting", "bettor", "casino", "casinos", "gamble", "gambling",
        "gambler", "poker", "slot machine", "slots", "sportsbook", "bookmaker", "bookie",
        "jackpot", "roulette", "blackjack", "wager", "wagering", "lottery", "baccarat",
        "parlay",
    ],
    "profanity": [
        "fuck", "fucking", "shit", "bitch", "asshole", "bastard", "cunt", "dick", "crap",
        "damn",
    ],
    "drugs_weapons": ["cocaine", "heroin", "meth", "marijuana", "cannabis", "firearm", "firearms"],
    "medical_political": [
        "abortion", "suicide", "overdose", "election", "democrat", "democrats",
        "republican", "republicans", "terrorism", "terrorist",
    ],
    "scraped_boilerplate": [
        "click here", "cookie policy", "privacy policy", "terms of service",
        "all rights reserved", "subscribe now", "sign up now", "read more", "share this",
        "copyright", "call us",
    ],
}
BLOCKED = {
    category: re.compile(r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b", re.I)
    for category, terms in BLOCKED_TERMS.items()
}
WEB_ADDRESS = re.compile(r"(?:https?://|www\.)", re.I)
# Text columns (not file or image links) must carry no web addresses.
LINK_COLUMNS_ALLOWED = ("img_dataset_data.json",)
# Case-sensitive substrings that only appear in failed evaluation or provider output.
ERROR_TEXT = ("Error:", "Exception", "Traceback", "could not convert")


def load(filename):
    return json.loads((DEMO / filename).read_text())


def row_hash(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def strings(value, path=()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings(item, path + (str(key),))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from strings(item, path + (str(index),))


def error_cells(filename):
    """(row_id, column_id, path, kind) for every error status or error text; never the text."""
    found = []
    for row in load(filename)["table"]:
        for column_id, cell in row.items():
            if not isinstance(cell, dict):
                continue
            if cell.get("status") == "error":
                found.append((row["row_id"], column_id, "status", "error_status"))
            for path, text in strings(cell):
                for needle in ERROR_TEXT:
                    if needle in text:
                        found.append((row["row_id"], column_id, "/".join(path), needle))
    return found


def column_ids_by_name(data):
    return {column["name"]: column["id"] for column in data["column_config"]}


def error_units(cell, *keys):
    node = cell
    for key in keys:
        node = (node or {}).get(key) if isinstance(node, dict) else None
    return ((node or {}).get("error_analysis") or {}).get("input1") or []


class DemoDatasetContentTests(unittest.TestCase):
    def test_no_blocked_terms_or_web_addresses_in_any_field(self):
        hits = []
        for filename in ALL_FILES:
            data = load(filename)
            for row in data["table"]:
                for column_id, cell in row.items():
                    for path, text in strings(cell):
                        for category, pattern in BLOCKED.items():
                            if pattern.search(text):
                                hits.append((filename, row["row_id"], column_id, "/".join(path), category))
                        if filename not in LINK_COLUMNS_ALLOWED and WEB_ADDRESS.search(text):
                            hits.append((filename, row["row_id"], column_id, "/".join(path), "web_address"))
        # Report ids and categories only, never the text itself.
        self.assertEqual(hits, [], f"{len(hits)} blocked fields: {hits[:20]}")

    def test_rows_parse_with_the_seeded_columns(self):
        for filename in ALL_FILES:
            with self.subTest(filename=filename):
                data = load(filename)
                column_ids = {column["id"] for column in data["column_config"]}
                self.assertEqual(len(data["table"]), EXPECTED_ROWS)
                self.assertEqual(data["metadata"]["total_rows"], EXPECTED_ROWS)
                row_ids = [row["row_id"] for row in data["table"]]
                self.assertEqual(len(set(row_ids)), EXPECTED_ROWS)
                for row in data["table"]:
                    cells = {key for key in row if key not in ("row_id", "order")}
                    self.assertTrue(column_ids <= cells, (row["row_id"], sorted(column_ids - cells)))
                    for column_id in column_ids:
                        self.assertIsInstance(row[column_id], dict)
                        self.assertIn("cell_value", row[column_id])

    def test_rag_rows_match_between_dataset_and_experiment(self):
        table, experiment = (load(filename) for filename in RAG_FILES)
        self.assertEqual(
            [row["row_id"] for row in table["table"]],
            [row["row_id"] for row in experiment["table"]],
        )
        shared = ("context", "question", "answer")
        ids = {c["name"]: c["id"] for c in table["column_config"] if c["name"] in shared}
        for left, right in zip(table["table"], experiment["table"]):
            for name in shared:
                self.assertEqual(
                    left[ids[name]]["cell_value"], right[ids[name]]["cell_value"], (left["row_id"], name)
                )

    def test_metric_columns_are_present(self):
        expected = {
            "table_data.json": {"Groundedness", "Answer Similarity"},
            "experiment.json": {
                "Groundedness",
                "Answer Similarity",
                "Groundedness-Experiment-prompt1-o1-mini",
                "Answer Similarity-Experiment-prompt1-o1-mini",
            },
            "img_dataset_data.json": {"Eval Image Instruction"},
        }
        for filename, names in expected.items():
            with self.subTest(filename=filename):
                data = load(filename)
                metrics = {
                    column["name"]
                    for column in data["column_config"]
                    if column["origin_type"] == "evaluation" and column["data_type"] in ("float", "boolean")
                }
                self.assertTrue(names <= metrics, sorted(names - metrics))
                for row in data["table"]:
                    by_name = {c["name"]: c["id"] for c in data["column_config"]}
                    for name in names:
                        self.assertIn(by_name[name], row)

    def test_no_rag_row_equals_a_row_of_the_earlier_files(self):
        prior = json.loads(PRIOR_ROW_HASHES.read_text())
        known = {digest for commits in prior["files"].values() for digests in commits.values() for digest in digests}
        self.assertGreaterEqual(len(known), 100)
        repeats = [
            (filename, row["row_id"])
            for filename in RAG_FILES
            for row in load(filename)["table"]
            if row_hash(row) in known
        ]
        self.assertEqual(repeats, [], f"{len(repeats)} rows equal a row of the earlier files: {repeats[:20]}")

    def test_no_error_status_or_error_text_in_rag_cells(self):
        for filename in RAG_FILES:
            with self.subTest(filename=filename):
                found = error_cells(filename)
                self.assertEqual(found, [], f"{len(found)} error cells: {found[:20]}")

    def test_no_error_status_or_error_text_in_image_cells(self):
        found = error_cells("img_dataset_data.json")
        self.assertEqual(found, [], f"{len(found)} error cells: {found[:20]}")

    def test_no_image_row_equals_a_prior_row_that_carried_an_error_cell(self):
        prior = json.loads(PRIOR_ROW_HASHES.read_text())
        known = set(prior["files"]["img_dataset_data.json"]["d4f44c05_rows_with_an_error_cell"])
        self.assertEqual(len(known), 40)
        repeats = [row["row_id"] for row in load("img_dataset_data.json")["table"] if row_hash(row) in known]
        self.assertEqual(repeats, [], f"{len(repeats)} rows equal an earlier row with an error cell: {repeats[:20]}")

    def test_rag_mirrored_fields_equal_their_primary_cell(self):
        table, experiment = (load(filename) for filename in RAG_FILES)
        tn, en = column_ids_by_name(table), column_ids_by_name(experiment)
        mismatches = []

        def check(filename, row_id, label, actual, expected):
            if actual != expected:
                mismatches.append((filename, row_id, label))

        def check_units(filename, row_id, label, units, source):
            for index, unit in enumerate(units):
                sentence = unit.get("org_sen") or {}
                start, end = sentence.get("start_idx"), sentence.get("end_idx")
                quoted = source[start:end] if isinstance(start, int) and isinstance(end, int) else None
                check(filename, row_id, f"{label}/{index}/org_sen", quoted, sentence.get("text"))

        experiment_rows = {row["row_id"]: row for row in experiment["table"]}
        for row in table["table"]:
            rid = row["row_id"]
            cells = {name: row[column_id] for name, column_id in tn.items()}
            question, context = cells["question"]["cell_value"], cells["context"]["cell_value"]
            answer, generated = cells["answer"]["cell_value"], cells["generated answer"]["cell_value"]
            check("table_data.json", rid, "generated answer/value_infos/data/response",
                  cells["generated answer"]["value_infos"]["data"]["response"], generated)
            check("table_data.json", rid, "generated answer/value_infos/reason",
                  cells["generated answer"]["value_infos"]["reason"], generated)
            for name in ("Answer Similarity", "Answer Similarity-reason"):
                data = cells[name]["value_infos"]["data"]
                check("table_data.json", rid, f"{name}/response", data["response"], generated)
                check("table_data.json", rid, f"{name}/expected_response", data["expected_response"], answer)
            for name in ("Groundedness", "Groundedness-reason"):
                check("table_data.json", rid, f"{name}/inputs",
                      cells[name]["value_infos"]["metadata"]["data"]["inputs"], [question, generated])
            for suffix in ("new", "old"):
                data = cells[f"Factual Accuracy-Optimization-gpt-4o-mini-{suffix}-prompt"]["value_infos"]["data"]
                output = cells[f"Optimization-gpt-4o-mini-{suffix}-prompt"]["cell_value"]
                check("table_data.json", rid, f"Factual Accuracy {suffix}/input", data["input"], question)
                check("table_data.json", rid, f"Factual Accuracy {suffix}/output", data["output"], output)
                check("table_data.json", rid, f"Factual Accuracy {suffix}/context", data["context"], context)
            check_units("table_data.json", rid, "Answer Similarity/error_analysis",
                        error_units(cells["Answer Similarity"], "value_infos", "metadata"), generated)
            check_units("table_data.json", rid, "Groundedness/error_analysis",
                        error_units(cells["Groundedness"], "value_infos", "metadata"), question)

            other = experiment_rows[rid]
            ecells = {name: other[column_id] for name, column_id in en.items()}
            for name in en:
                if name in tn:
                    check("experiment.json", rid, f"{name}/cell_value", ecells[name]["cell_value"],
                          cells[name]["cell_value"])
            output = ecells["Experiment-prompt1-o1-mini"]["cell_value"]
            check("experiment.json", rid, "generated answer/metadata/reason",
                  ecells["generated answer"]["metadata"]["reason"], generated)
            check("experiment.json", rid, "Experiment-prompt1-o1-mini/metadata/reason",
                  ecells["Experiment-prompt1-o1-mini"]["metadata"]["reason"], output)
            check("experiment.json", rid, "Experiment-prompt1-o1-mini/value_infos/reason",
                  ecells["Experiment-prompt1-o1-mini"]["value_infos"]["reason"], output)
            for name, response in (
                ("Groundedness", generated),
                ("Groundedness-reason", generated),
                ("Groundedness-Experiment-prompt1-o1-mini", output),
                ("Groundedness-Experiment-prompt1-o1-mini-reason", output),
            ):
                check("experiment.json", rid, f"{name}/inputs",
                      ecells[name]["metadata"]["cell_metadata"]["data"]["inputs"], [question, response])
            check_units("experiment.json", rid, "Answer Similarity/error_analysis",
                        error_units(ecells["Answer Similarity"], "metadata", "cell_metadata"), generated)
            check_units("experiment.json", rid, "Groundedness/error_analysis",
                        error_units(ecells["Groundedness"], "metadata", "cell_metadata"), question)
        # Report ids and field names only, never the text itself.
        self.assertEqual(mismatches, [], f"{len(mismatches)} mirrored fields differ: {mismatches[:20]}")


if __name__ == "__main__":
    unittest.main()
