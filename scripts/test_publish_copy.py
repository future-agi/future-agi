"""Offline regression checks for published copy and portable fixture paths.

Run this single file with ``python3 -I scripts/test_publish_copy.py``.
Only repository text is read; no application, database or provider is loaded.
"""

import json
from pathlib import Path
import re
import sys
import unittest


def _deny_network(event, _args):
    if event.startswith("socket."):
        raise RuntimeError("network disabled in publication-copy tests")


sys.addaudithook(_deny_network)
ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "api_contracts/gateway/examples"
BILLING = ROOT / "futureagi/ee/usage/contracts"
INTERNAL_LABEL = re.compile(r"\bruling\s+\d+", re.IGNORECASE)


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def words(value):
    return " ".join(value.split())


class PublishedCopyTests(unittest.TestCase):
    def test_capabilities_description_preserves_public_contract(self):
        description = json.loads(
            (SCHEMAS / "capabilities-v1.schema.json").read_text()
        )["description"]
        self.assertEqual(
            description,
            "GET /-/capabilities and GET /v1/capabilities. endpoint is a dotted "
            "capability id, status is supported|provider_dependent|unsupported "
            "and source is interface|config|catalog|qualified. qualified comes "
            "only from an imported live qualification run; mock runs never set "
            "it, and no evidence is qualified false with qualified_at null. "
            "managed_resale_terms is optional and admin-only: /-/capabilities "
            "shows it (unconfirmed until the provider's resale_terms_ref is set; "
            "native for a FutureAGI-owned service with no vendor gate; byok_only "
            "when the vendor's terms allow no managed resale; it never blocks "
            "traffic) and /v1/capabilities omits it. price is optional and shown "
            "on both: it is the published managed price, markup included. "
            "launch_tier is computed per response: unsupported when no provider "
            "in the caller's view serves the endpoint.",
        )

    def test_credits_description_keeps_path_organization_binding(self):
        schema = json.loads((SCHEMAS / "credits-v1.schema.json").read_text())
        self.assertEqual(
            schema["$defs"]["Admin"]["properties"]["org_id"]["description"],
            "The org the account belongs to, as the path names it.",
        )

    def test_credits_read_description_keeps_endpoints_without_plan_label(self):
        schema = json.loads((SCHEMAS / "credits-v1.schema.json").read_text())
        self.assertEqual(
            schema["description"],
            "Credits reads: the admin GET /-/orgs/{org_id}/credits ($defs/Admin) "
            "and the customer GET /v1/credits ($defs/Customer). balance_usd and "
            "markup_percent are kept because Django's managed-models status view "
            "reads them. ledger names the backing store: redis, or memory only "
            "when managed.require_redis=false (tests).",
        )

    def test_usage_purpose_description_keeps_meter_rule_without_plan_label(self):
        schema = json.loads((BILLING / "usage-event-v1.schema.json").read_text())
        self.assertEqual(
            schema["$defs"]["Attribution"]["properties"]["purpose"]["description"],
            "learning marks work the managed learning service did for this agent. "
            "It has no meter of its own. null is ordinary use.",
        )

    def test_billing_read_description_keeps_money_and_read_rules(self):
        schema = json.loads((BILLING / "billing-ledger-read-v1.schema.json").read_text())
        description = schema["description"]
        self.assertTrue(description.startswith("The read API of the one billing ledger,"))
        self.assertNotRegex(description, INTERNAL_LABEL)
        self.assertIn("no read calls Stripe", description)
        self.assertIn("Money is integer USD micros", description)
        self.assertIn("invoice amounts, which are integer cents", description)
        self.assertIn("billing-ledger-read-v1.md section 2", description)

    def test_usage_description_keeps_encoding_and_ingest_rules(self):
        schema = json.loads((BILLING / "usage-event-v1.schema.json").read_text())
        description = schema["description"]
        self.assertTrue(
            description.startswith(
                "The one usage event every product meter emits into the Future AGI billing ledger."
            )
        )
        self.assertNotRegex(description, INTERNAL_LABEL)
        self.assertIn("service-authenticated batch ingest", description)
        self.assertIn("exactly one canonical encoding and one payload hash", description)
        self.assertIn("outside this contract", description)

    def test_scope_failure_still_points_to_public_scope_rule(self):
        doc = text("docs/gateway/connectors/omega-ee-attach.md")
        self.assertIn("(section 8, item 12). Do not retry; fix the caller", doc)
        self.assertIn("## 8. What EE must never do", doc)
        self.assertIn("Send an agent key anywhere but the virtual servers", doc)
        self.assertIn("`key_scope_denied`", doc)

    def test_attach_contract_has_no_internal_labels_or_private_citations(self):
        doc = text("docs/gateway/connectors/omega-ee-attach.md")
        self.assertNotRegex(
            doc,
            r"\b(?:CN\d+[a-z]?|AM-C\d+|OM\d+[a-z]?|M\d+[a-z]?|"
            r"CW\d+|N\d+|U\d+|MC\.\d+|RF\d+|G\d+|OA-\d+)\b",
        )
        self.assertNotRegex(doc, r"`(?:EE|OSS|OM1):|ee/[a-z]|packages/(?:server|core)/")
        # Public contract digests are content addresses, not private commit pins.
        without_digests = re.sub(r"sha256:[a-f0-9…]+", "sha256:<digest>", doc)
        self.assertNotRegex(without_digests, r"(?<![a-f0-9])[a-f0-9]{8,40}(?![a-f0-9])")
        self.assertNotRegex(doc, r"(?i)\bplanned\b|not merged|plan-omega-agents")

    def test_attach_contract_states_current_ensure_modes(self):
        doc = words(text("docs/gateway/connectors/omega-ee-attach.md"))
        # Boolean assertions avoid echoing the whole publication on failure.
        self.assertTrue(
            "Ensure accepts `agent` today and answers 400 `invalid` "
            "(`attr` `mode`) for `user`." in doc
        )

    def test_attach_contract_distinguishes_user_mode_requirements(self):
        doc = words(text("docs/gateway/connectors/omega-ee-attach.md"))
        rule = doc.split("6. **Access.**", 1)[1].split("7. **", 1)[0]
        self.assertTrue(
            "Today's gateway access rules have no workspace or membership dimension." in rule
        )
        self.assertTrue(
            "User-mode compatibility requires workspace-aware authorization: "
            "admission of an OAuth principal requires FutureAGI's live membership "
            "to confirm that the person belongs to the virtual server's workspace; "
            "anyone else must get the same 404." in rule
        )
        self.assertTrue(
            "Ensure rejects `user` mode as described in section 3.2." in doc
        )

    def test_attach_contract_keeps_numbered_sections_and_prohibitions(self):
        doc = text("docs/gateway/connectors/omega-ee-attach.md")
        self.assertEqual(
            re.findall(r"^#{2,3} (\d+(?:\.\d+)?)(?:\.| )", doc, re.MULTILINE),
            ["1", "2", "3", "3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7",
             "4", "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "5", "5.1", "5.2",
             "5.3", "6", "7", "8", "9", "10", "11", "12"],
        )
        prohibitions = doc.split("## 8. What EE must never do", 1)[1].split("## 9.", 1)[0]
        self.assertEqual(
            re.findall(r"^(\d+)\. ", prohibitions, re.MULTILINE),
            [str(i) for i in range(1, 15)],
        )
        self.assertIn(
            "Contract: `omega-ee-attach/v1`. Set digest: "
            "`sha256:22f51018609e39eefa84eaf1a7e8852160827bd3148e107242f385e6332f6ea3`",
            doc,
        )
        self.assertIn("Enterprise **broker = gateway** provisioning is unavailable by default.", doc)
        self.assertIn("## 3. Provisioning a virtual server\n", doc)

    def test_mint_change_describes_idempotency_without_review_label(self):
        doc = words(text("docs/gateway/connectors/omega-ee-attach.md"))
        self.assertIn(
            "The third change (2026-10-22): idempotent agent-key minting (section 3.7).",
            doc,
        )
        self.assertIn("409 `agent_key_mint_replayed`", doc)
        self.assertIn("422 `idempotency_key_reused`", doc)
        self.assertIn("The required `Idempotency-Key` header", doc)
        self.assertIn("deploy them together", doc)

    def test_mint_prohibition_keeps_replay_rule_without_review_label(self):
        doc = words(text("docs/gateway/connectors/omega-ee-attach.md"))
        self.assertIn(
            "14. **Mint without an Idempotency-Key, or retry an uncertain mint "
            "with a new one** (section 3.7). After a 409 `agent_key_mint_replayed`, "
            "never keep the replayed key unrevoked: revoke it, then mint with a "
            "new Idempotency-Key.",
            doc,
        )

    def test_conformance_intro_keeps_contract_without_unavailable_sources(self):
        doc = words(text("futureagi/webhooks/core/conformance/README.md"))
        self.assertIn(
            "Frozen from the webhook core design and B8 event contract `b8/1`;",
            doc,
        )
        self.assertNotIn("WH-0", doc)
        self.assertNotIn("DESIGN.md", doc)
        self.assertNotIn("B8-EVENT-CONTRACT.md", doc)
        self.assertIn("v1 signing vectors shared by the SDK and server", doc)
        self.assertIn("B8 notice validation fixtures", doc)
        self.assertIn("## B8 fixture path mapping", doc)

    def test_translated_address_note_keeps_rule_without_review_label(self):
        doc = words(text("futureagi/webhooks/core/conformance/README.md"))
        self.assertIn(
            "1.2.0 also unwraps the IPv4-translated prefix `::ffff:0:0:0/96` "
            "in `addresses.json`, so `::ffff:0:7f00:1` is `never` like 127.0.0.1, "
            "and pins the hex spelling `::7f00:1` of the IPv4-compatible form.",
            doc,
        )

    def test_nat64_version_notes_keep_never_overrides_cidr_rule(self):
        doc = words(text("futureagi/webhooks/core/conformance/README.md"))
        self.assertIn("1.3.0 adds NAT64 loopback cases:", doc)
        self.assertIn("`64:ff9b::7f00:1` is `never` on hosted", doc)
        self.assertIn("NEVER beats any CIDR", doc)

    def test_embedded_address_version_note_keeps_admission_rule(self):
        doc = words(text("futureagi/webhooks/core/conformance/README.md"))
        self.assertIn("1.4.0 makes admission stricter:", doc)
        self.assertIn("a private embedded IPv4 address must be inside an IPv4", doc)
        self.assertIn("`private_cidrs` entry itself", doc)

    def test_conformance_mapping_keeps_canonical_location(self):
        doc = text("futureagi/webhooks/core/conformance/README.md")
        self.assertIn(
            "The contract names `futureagi/webhooks/contracts/...`; the canonical copy is here.",
            doc,
        )
        self.assertNotRegex(doc, INTERNAL_LABEL)
        self.assertIn("`contracts/b8/notice.v1.schema.json`", doc)
        self.assertIn("`schemas/b8/notice.v1.schema.json`", doc)

    def test_shell_paths_remain_configurable_and_integrity_checked(self):
        launch = text("e2e/walks/launch/launch.sh")
        self.assertIn("PULL_ROOT=${WALK_PULL_ROOT:-$WT/e2e/walk-landed}", launch)
        self.assertIn(
            "KIT_SRC=${WALK_KIT_ARCHIVE:-$WT/e2e/walk-kit/egress-sinkhole-r574b.tar}",
            launch,
        )
        self.assertIn("KIT_SHA256=", launch)
        self.assertIn('"${RIG_EXEC_ROOT:-$HOME/rig-exec}/vm/exec/exec-preflight.sh" quick', text(
            "agentcc-gateway/scripts/onekey-cat/gate.sh"
        ))
        self.assertIn(
            ': "${PLAYWRIGHT_BROWSERS_PATH:=$HOME/Library/Caches/ms-playwright}"',
            text("frontend/src/sections/omega/hub/e2e/run-e2e.sh"),
        )

    def test_default_walk_kit_directory_is_ignored(self):
        self.assertIn("walk-kit/", text("e2e/.gitignore").splitlines())

    def test_hub_command_names_a_playwright_modules_directory(self):
        doc = text("frontend/src/sections/omega/hub/e2e/README.md")
        self.assertIn('PLAYWRIGHT_NODE_MODULES="path/to/node_modules"', doc)
        self.assertIn(
            "Replace `path/to/node_modules` with the directory containing `playwright`, "
            "whose version matches the browsers in `PLAYWRIGHT_BROWSERS_PATH`.",
            words(doc),
        )

    def test_walk_landing_note_distinguishes_vm_and_mac_layouts(self):
        self.assertIn(
            "`e2e/walk-landed/` holds `<launch-id>/` on the VM and "
            "`<group>/files/...` as `launch.sh`'s default pull root on the Mac "
            "(override with `WALK_PULL_ROOT`).",
            words(text("e2e/README.md")),
        )

    def test_synthetic_mailbox_uses_reserved_example_domain(self):
        fixture = text("frontend/src/test-fixtures/wrapped-conversations.js")
        self.assertIn('email: "d.okafor@example.com"', fixture)
        self.assertNotIn("@gmail.com", fixture)

    def test_example_mailboxes_use_reserved_domains(self):
        examples = {
            "frontend/src/_mock/assets.js": 24,
            "frontend/src/_mock/api/prototype-observe-handlers/_labels.js": 1,
            "futureagi/api_docs/prompt-management/is_run prompt run_template.bru": 2,
            "frontend/src/sections/experiment-detail/results/drawer/__tests__/fixtures/trace-a.json": 1,
            "futureagi/tfc/management/commands/seed_dummy_data.py": 4,
        }
        for path, count in examples.items():
            with self.subTest(path=path):
                source = text(path)
                self.assertFalse("@gmail" + ".com" in source)
                self.assertEqual(
                    len(re.findall(r"example-user-\d{2}@example\.com\b", source)),
                    count,
                )
        mailboxes = re.findall(
            r"example-user-\d{2}@example\.com\b", text("frontend/src/_mock/assets.js")
        )
        self.assertEqual(len(mailboxes), len(set(mailboxes)))

    def test_mock_and_seed_mailboxes_preserve_reserved_identities(self):
        # The seed already repeats one identity three times; preserve that reuse.
        expected_multiplicities = {
            "frontend/src/_mock/assets.js": [1] * 24,
            "futureagi/tfc/management/commands/seed_dummy_data.py": [1, 3],
        }
        for path, multiplicities in expected_multiplicities.items():
            with self.subTest(path=path):
                mailboxes = re.findall(
                    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                    text(path),
                )
                self.assertTrue(mailboxes)
                # Boolean/count assertions never echo original mailbox values.
                self.assertTrue(all(
                    address.rsplit("@", 1)[1].lower() in
                    {"example.com", "example.org", "example.net"}
                    for address in mailboxes
                ))
                self.assertEqual(
                    sorted(mailboxes.count(address) for address in set(mailboxes)),
                    multiplicities,
                )

    def test_local_path_examples_use_portable_placeholders(self):
        paths = (
            "e2e/README.md",
            "futureagi/api_docs/saml/idp-upload.bru",
            "futureagi/api_docs/develop/Create Dataset by file.bru",
            "futureagi/accounts/templates/saml2_auth/login.html",
        )
        for path in paths:
            with self.subTest(path=path):
                source = text(path)
                for root in ("Users", "Volumes", "home"):
                    self.assertFalse("/" + root + "/" in source)
        self.assertEqual(text(paths[0]).count("`/path/to/futureagi`"), 2)
        self.assertIn("@file(./fixtures/idp-metadata.xml)", text(paths[1]))
        self.assertRegex(text(paths[2]), r"@file\(\./fixtures/dataset\.[a-z0-9]+\)")
        self.assertIn(" :: /path/to/notebooks", text(paths[3]))

    def test_readiness_paths_are_portable_without_claiming_acceptance(self):
        readiness = json.loads(text("deploy/hosted-readiness.json"))
        self.assertFalse(readiness["productAccepted"])
        self.assertEqual(readiness["profile"], "hosted-preparation")
        self.assertTrue(all(p.startswith("<HOME>/") for p in readiness["historicalReferences"]))
        paths = [p for gate in readiness["gates"] for p in gate.get("evidencePaths", [])]
        self.assertTrue(paths)
        self.assertTrue(all(p.startswith("<WORKSPACE>/") for p in paths))


if __name__ == "__main__":
    unittest.main()
