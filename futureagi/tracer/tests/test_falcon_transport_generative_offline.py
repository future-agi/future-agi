"""Generative send-site gate; Python 3.12 -I this_file, no application imports.

The list of sends is generated from ASTs, never maintained by hand. Includes
local-import closure, HTTP/SDK/native constructors, aliases, retry waits and
all calls in conservative helper modules. Exceptions are exact and counted.
Use --unadmitted-fixture to demonstrate an independent injected RED oracle.
"""

import ast
import runpy
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
c = runpy.run_path(str(HERE / "falcon_send_census.py"))
a = runpy.run_path(str(HERE / "falcon_send_admission.py"))
StructuralStoreTests = runpy.run_path(
    str(HERE / "test_falcon_transport_stores_offline.py")
)["StructuralStoreTests"]
BindingStoreTests = runpy.run_path(
    str(HERE / "test_falcon_transport_bindings_offline.py")
)["BindingStoreTests"]
CompositionTests = runpy.run_path(
    str(HERE / "test_falcon_transport_composition_offline.py")
)["CompositionTests"]
SelfStoreTests = runpy.run_path(
    str(HERE / "test_falcon_transport_self_stores_offline.py")
)["SelfStoreTests"]
SetterLoadTests = runpy.run_path(
    str(HERE / "test_falcon_transport_setter_loads_offline.py")
)["SetterLoadTests"]


def audit(sources):
    return c["audit"](sources, a["admission"], a["EXCEPTIONS"], a["EXCEPTION_COUNTS"])


def injected(source):
    # Independent fixture deliberately has NO guard import/call and is not one
    # of the existing site's helper names. This must not inherit an exemption.
    return (
        source
        + "\n\ndef newly_added_send():\n    import requests as outbound\n    return outbound.post('https://offline.invalid')\n"
    )


class GenerativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources, cls.boundaries = c["load_sources"]()

    def test_every_generated_site_is_admitted(self):
        sites, errors = audit(self.sources)
        self.assertTrue(sites)
        self.assertEqual(errors, [])
        print(
            f"GENERATIVE: {len(sites)} sites; {len(self.sources)} modules; "
            f"{sum(a['EXCEPTION_COUNTS'].get(k, 1) for k in a['EXCEPTIONS'])} exact send-exempt occurrences; "
            f"{sum(v[0] for v in c['STRUCTURAL_EXCEPTIONS'].values())} exact structural-exempt occurrences"
        )

    def test_new_unguarded_site_fails_in_every_discovered_module(self):
        # Generate a mutation for EACH module, not just the original 16 paths.
        for module, source in self.sources.items():
            with self.subTest(module=module):
                _, errors = audit({**self.sources, module: injected(source)})
                self.assertTrue(
                    any(module in e and "newly_added_send" in e for e in errors), errors
                )

    def test_independent_transport_and_alias_fixtures(self):
        fixtures = (
            "import requests as r\nr.get('url')",
            "from requests import post as emit\nemit('url')",
            "import requests\ns = requests.Session()\ns.get('url')",
            "import httpx\nc = httpx.Client()\nc.get('url')",
            "import httpx\nasync def run():\n    async with httpx.AsyncClient() as c:\n        await c.post('url')",
            "import httpcore\nc = httpcore.ConnectionPool()\nc.request('GET', 'url')",
            "import openai\nc = openai.OpenAI()\nc.chat.completions.create(messages=[])",
            "import anthropic\nc = anthropic.Anthropic()\nc.messages.create(messages=[])",
            "import boto3\nc = boto3.client('bedrock-runtime')\nc.invoke_model(body='x')",
            "from clickhouse_driver import Client as Native\nc = Native(host='offline.invalid')\nc.execute('SELECT 1')",
            "import clickhouse_connect as ch\nc = ch.get_client()\nc.query('SELECT 1')",
            "def run(client):\n    send = client.post\n    send('url')",
            "def run(client):\n    getattr(client, 'post')('url')",
            "def run(client, verb):\n    getattr(client, verb)('url')",
            "def run(client):\n    for attempt in range(3):\n        client.post('url')",
            "import time\ndef retry(client):\n    time.sleep(1)\n    client.get('url')",
            "import socket\nsocket.create_connection(('offline.invalid', 80))",
            "import requests\ndef run(guard=None):\n    if guard is None:\n        requests.post('url')",
        )
        for source in fixtures:
            with self.subTest(source=source):
                sites, errors = c["audit"](
                    {"independent_fixture": source}, a["admission"], {}
                )
                self.assertTrue(sites, "enumeration missed the injected transport")
                self.assertEqual(
                    len(errors), len(sites), "unguarded fixture gained admission"
                )

    def test_local_alias_mutations_in_reachable_cached_session_method(self):
        module = "agentic_eval.core.embeddings.serving_client"
        fixtures = (
            "outbound = self.session\noutbound.get('url', timeout=120)",
            "outbound: object = self.session\noutbound.get('url')",
            "outbound = other = self.session\nother.get('url')",
            "first = self.session\nsecond = first\noutbound = second\noutbound.get('url')",
            "outbound, other = self.session, None\noutbound.get('url')",
            "other, (outbound, unused) = None, (self.session, None)\noutbound.get('url')",
            "[outbound, unused] = [self.session, None]\noutbound.get('url')",
            "pair = (self.session, None)\noutbound, unused = pair\noutbound.get('url')",
            "holder = self\noutbound = holder.session\noutbound.get('url')",
            "outbound = self.transport.connection\noutbound.get('url')",
            "holder = self.transport\nother = holder.connection\noutbound = other\noutbound.get('url')",
            "with self.session as outbound:\n    outbound.get('url')",
            "outbound = self.session\nwith outbound as other:\n    other.get('url')",
            "import requests\nwith requests.Session() as outbound:\n    outbound.get('url')",
            # Scopes/rebinding cannot erase a previously acquired capability.
            "outbound = self.session\noutbound.get('url')\noutbound = {}",
            "outbound = self.session\ndef unrelated():\n    outbound = {}\noutbound.get('url')",
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                tree = ast.parse(self.sources[module])
                cls = next(
                    n
                    for n in tree.body
                    if isinstance(n, ast.ClassDef) and n.name == "ModelServingClient"
                )
                target = next(
                    n
                    for n in cls.body
                    if isinstance(n, ast.FunctionDef) and n.name == "_require_serving"
                )
                target.body[:0] = ast.parse(fixture).body
                sites, errors = audit({**self.sources, module: ast.unparse(tree)})
                injected_gets = [
                    s
                    for m, s in sites
                    if m == module
                    and s["owner"] == "ModelServingClient._require_serving"
                    and s["call"].endswith(".get")
                ]
                self.assertEqual(
                    len(injected_gets), 1, "aliased cached GET escaped census"
                )
                self.assertTrue(
                    any("ModelServingClient._require_serving" in e for e in errors),
                    errors,
                )

    def assert_composed_alias_rejected(self, fixture, call):
        module = "agentic_eval.core.embeddings.serving_client"
        owner = "ModelServingClient._require_serving"
        tree = ast.parse(self.sources[module])
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "ModelServingClient"
        )
        target = next(
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name == "_require_serving"
        )
        target.body[:0] = ast.parse(fixture).body
        sites, errors = audit({**self.sources, module: ast.unparse(tree)})
        matching = [
            site
            for name, site in sites
            if name == module and site["owner"] == owner and site["call"] == call
        ]
        self.assertEqual(len(matching), 1, "composed alias escaped complete census")
        self.assertIn(
            f"{module}:{matching[0]['line']} {owner} {call}: unadmitted", errors
        )

    def test_composed_imported_module_alias_fails_complete_audit(self):
        self.assert_composed_alias_rejected(
            "import requests\nfrom types import SimpleNamespace\n"
            "holder = SimpleNamespace()\nholder.pipe = requests\n"
            "other = holder\noutbound = other.pipe\n"
            "outbound.get('https://offline.invalid', timeout=120)",
            "outbound.get",
        )

    def test_composed_cached_session_alias_fails_complete_audit(self):
        self.assert_composed_alias_rejected(
            "from types import SimpleNamespace\nholder = SimpleNamespace()\n"
            "holder.pipe = self.session\nother = holder\noutbound = other.pipe\n"
            "outbound.get('https://offline.invalid', timeout=120)",
            "outbound.get",
        )

    def test_composed_bound_method_alias_fails_complete_audit(self):
        self.assert_composed_alias_rejected(
            "import requests\nfrom types import SimpleNamespace\n"
            "holder = SimpleNamespace()\nholder.pipe = requests\n"
            "other = holder\nm = other.pipe.get\n"
            "m('https://offline.invalid', timeout=120)",
            "m",
        )

    def test_composed_with_target_alias_fails_complete_audit(self):
        self.assert_composed_alias_rejected(
            "from types import SimpleNamespace\nholder = SimpleNamespace()\n"
            "holder.pipe = self.session\nother = holder\n"
            "with other.pipe as outbound:\n"
            "    outbound.get('https://offline.invalid', timeout=120)",
            "outbound.get",
        )

    def test_composed_cycle_terminates_and_fails_complete_audit(self):
        self.assert_composed_alias_rejected(
            "import requests\na = b\nb = a\na.pipe = requests\n"
            "outbound = b.pipe\n"
            "outbound.get('https://offline.invalid', timeout=120)",
            "outbound.get",
        )

    def test_composed_closure_revisits_new_evidence_without_hop_cutoff(self):
        fixtures = (
            "holder.pipe = requests\nother = holder\n"
            "one.next = other\ntwo = one\noutbound = two.next.pipe\n",
            # Reusing other with a different suffix must not lose its evidence.
            "holder.pipe = other.transport\nother = holder\n"
            "holder.transport = requests\noutbound = other.pipe\n",
            # A growing cycle cannot generate an infinite spelling domain.
            "a = a.pipe\na.pipe.pipe = requests\noutbound = a\n",
            "holder.pipe = requests\nother = holder\noutbound = other.pipe.Session()\n",
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assert_composed_alias_rejected(
                    "import requests\n"
                    + fixture
                    + "outbound.get('https://offline.invalid', timeout=120)",
                    "outbound.get",
                )

    def test_composed_closure_is_finite_idempotent_and_scope_local(self):
        tree = ast.parse(
            "import requests\n"
            "def unrelated():\n    holder.pipe = requests\n"
            "def run():\n    other = holder\n    other.pipe.get('url')\n"
            "def cycle():\n    a = a.pipe\n    a.pipe.pipe = requests\n"
            "    outbound = a\n    outbound.get('url')\n"
        )
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        aliases = c["LocalAliases"](tree, parents, c["TRANSPORT_MODULES"])
        closed = {key: set(values) for key, values in aliases.bindings.items()}
        aliases.close_composed_paths(tree)
        self.assertEqual(
            {key: set(values) for key, values in aliases.bindings.items()}, closed
        )
        # Saturation may add bindings only for the finite source Attribute set.
        source_paths = {
            (aliases.scope(node), ast.unparse(node))
            for node in ast.walk(tree)
            if isinstance(node, (ast.Name, ast.Attribute))
        }
        self.assertLessEqual(set(closed), source_paths | {(tree, "requests")})
        sites = c["census"](ast.unparse(tree))
        self.assertEqual(
            [
                (site["owner"], site["call"])
                for site in sites
                if not site.get("structural")
            ],
            [("cycle", "outbound.get")],
        )

    def test_alias_contract_independent_context_and_factory_forms(self):
        fixtures = (
            "import requests\ndef run():\n    handle = requests.Session()\n    outbound = handle\n    outbound.get('url')",
            "def run(self):\n    self.saved = self.session\n    outbound = self.saved\n    outbound.get('url')",
            "def run(self):\n    with self.session as outbound, self.connection as other:\n        outbound.get('url')\n        other.get('url')",
            "async def run(self):\n    async with self.client as outbound:\n        await outbound.get('url')",
            "def run(self):\n    with self.connection as (outbound, other):\n        outbound.get('url')",
            "def run(self):\n    a = self.session\n    b = a\n    c = b\n    d = c\n    e = d\n    f = e\n    g = f\n    h = g\n    i = h\n    i.get('url')",
        )
        for source in fixtures:
            with self.subTest(source=source):
                sites, errors = c["audit"](
                    {"alias_fixture": source}, a["admission"], {}
                )
                gets = [s for _, s in sites if s["call"].endswith(".get")]
                self.assertTrue(gets, "alias form escaped enumeration")
                self.assertEqual(len(errors), len(sites))

    def test_bound_method_alias_and_cycles_remain_candidates(self):
        fixtures = (
            "def run(self):\n    outbound = self.session\n    emit = outbound.get\n    emit('url')",
            "def run(self):\n    outbound = self.session\n    other = outbound\n    outbound = other\n    outbound.get('url')",
            "def run(self):\n    outbound = self.session\n    outbound = outbound.connection\n    outbound.get('url')",
        )
        for source in fixtures:
            with self.subTest(source=source):
                sites, errors = c["audit"](
                    {"alias_fixture": source}, a["admission"], {}
                )
                self.assertTrue(sites)
                self.assertEqual(len(errors), len(sites))

    def test_new_imported_helper_is_discovered_from_source(self):
        # No network, no repository mutation, no application import. A new local
        # module introduced by an import must enter the same source census.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "ai_tools/background.py"
            policy.parent.mkdir(parents=True)
            policy.write_text(
                "BACKGROUND_TOOL_ALLOWLIST = {}\nfrom new_helper import send\n"
            )
            (root / "new_helper.py").write_text(injected(""))
            sources, _ = c["load_sources"](root)
            self.assertIn("new_helper", sources)
            _, errors = c["audit"](sources, a["admission"], {})
            self.assertTrue(any("new_helper" in e for e in errors))

    def test_removing_guarded_factories_and_hooks_fails(self):
        changes = (
            ("ee.falcon_ai.llm_client", "**async_client_options()", "**{}"),
            ("ee.licensing.activation_client", "**async_client_options()", "**{}"),
            (
                "agentic_eval.core.database.ch_vector",
                "self.client = guard_native_client(",
                "self.client = identity(",
            ),
            (
                "tracer.services.clickhouse.client",
                "guard_native_client(\n",
                "identity(\n",
            ),
            (
                "tracer.services.clickhouse.application_read_transport",
                'kwargs["pool_mgr"] = pool',
                'kwargs["pool_mgr"] = None',
            ),
            ("analytics.utils", "if current_guard() is not None:", "if False:"),
        )
        for module, old, new in changes:
            with self.subTest(module=module):
                self.assertIn(old, self.sources[module])
                _, errors = audit(
                    {**self.sources, module: self.sources[module].replace(old, new)}
                )
                self.assertTrue(
                    errors, "lost the admission proof without failing the gate"
                )

    def test_exact_exceptions_do_not_admit_an_extra_send(self):
        module = "agentic_eval.core.utils.functions"
        source = self.sources[module]
        tree = ast.parse(source)
        target = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "download_image_to_base64"
        )
        target.body.append(ast.parse("requests.get('https://offline.invalid')").body[0])
        _, errors = audit({**self.sources, module: ast.unparse(tree)})
        self.assertTrue(any("download_image_to_base64" in e for e in errors))


if __name__ == "__main__":
    if "--unadmitted-fixture" in sys.argv:
        sources, _ = c["load_sources"]()
        sources["independent_fixture"] = injected("")
        _, errors = audit(sources)
        print("\n".join(errors))
        raise SystemExit(1 if errors else 0)
    unittest.main()
