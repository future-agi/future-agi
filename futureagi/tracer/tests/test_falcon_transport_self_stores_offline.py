"""Ruling 441: reject bare-self stores, without reference-path enumeration."""

import ast
import runpy
import signal
import time
import unittest
from pathlib import Path

HERE = Path(__file__).parent
s = runpy.run_path(str(HERE / "test_falcon_transport_stores_offline.py"))
c = s["c"]


class SelfStoreTests(unittest.TestCase):
    setUpClass = classmethod(s["StructuralStoreTests"].setUpClass.__func__)
    assert_store_rejected = s["StructuralStoreTests"].assert_store_rejected

    def rejected(self, fixture, nested=""):
        self.assert_store_rejected(fixture, "bare-self-store", nested)

    def test_every_r3a9_cycle_variant_rejects_complete_audit(self):
        for seed in (
            "setattr(self, 'saved', self.session)",
            "self.saved = self.session if True else None",
            "self.saved = self.session",
        ):
            for alias in (
                "self.loop = self\nlocal = self.loop\nother.pipe = local.saved",
                "self.loop = self\nlocal = self.loop.loop\nother.pipe = local.saved",
                "self.loop = self\nlocal = self.loop.loop.loop\nother.pipe = local.saved",
                "self.loop = self\nother.pipe = self.loop.loop.saved",
                "box = SimpleNamespace()\nbox.link = self\nbox.loop = box\nlocal = box.loop.loop.link\nother.pipe = local.saved",
                "self.peer = SimpleNamespace()\nself.peer.back = self\nlocal = self.peer.back.peer.back\nother.pipe = local.saved",
                "local = self\nlocal.loop = local\nother.pipe = local.loop.loop.saved",
            ):
                with self.subTest(seed=seed, alias=alias):
                    self.rejected(
                        "from types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\n"
                        + seed
                        + "\n"
                        + alias
                        + "\nemit = holder.pipe.get\nemit('offline')"
                    )

    def test_class_namespace_aliases_reject_complete_audit(self):
        for nested in (False, True):
            for copied in (False, True):
                body = (
                    "class Box:\n    class Inner:\n        ref = self"
                    if nested
                    else "class Box:\n    ref = self"
                )
                path = ("local" if copied else "Box") + (".Inner" if nested else "")
                with self.subTest(nested=nested, copied=copied):
                    self.rejected(
                        "from types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\n"
                        "setattr(self, 'saved', self.session)\n"
                        + body
                        + ("\nlocal = Box" if copied else "")
                        + f"\nother.pipe = {path}.ref.saved\nemit = holder.pipe.get\nemit('offline')",
                        ".Box.Inner" if nested else ".Box",
                    )

    def test_other_r3a9_reviewer_prefix_and_scope_variants_reject(self):
        seed = "setattr(self, 'saved', self.session)\n"
        fixtures = (
            (
                seed
                + "box = SimpleNamespace()\nbox.link = self\nfirst = box\nsecond = first.link\nother.pipe = second.saved",
                "",
            ),
            (
                seed
                + "box = SimpleNamespace(link=SimpleNamespace())\nbox.link.ref = self\nlocal = box.link\nother.pipe = local.ref.saved",
                "",
            ),
            (
                seed
                + "pair = [None, [self]]\nunused, [local] = pair\nother.pipe = local.saved",
                "",
            ),
            (seed + "first = (local := self)\nother.pipe = first.saved", ""),
            (
                seed
                + "first = local = self\nsecond = local\nother.pipe = second.saved",
                "",
            ),
            ("first = self\nsecond = first\n" + seed + "other.pipe = second.saved", ""),
            (
                seed + "self.link = self\nlocal = self.link\nother.pipe = local.saved",
                "",
            ),
            (
                seed
                + "first = self\nsecond = first\ndel first\nother.pipe = second.saved",
                "",
            ),
            (
                seed
                + "box = SimpleNamespace()\nbox.link = self\nlocal = box.link\ndel box.link\nother.pipe = local.saved",
                "",
            ),
            (
                seed
                + "local = self\ndef copy():\n    other.pipe = local.saved\ncopy()",
                "",
            ),
            (
                seed + "def copy(local=self):\n    other.pipe = local.saved\ncopy()",
                ".copy",
            ),
            (
                seed
                + "copy = lambda local=self: setattr(other, 'pipe', local.saved)\ncopy()",
                "",
            ),
            ("global local\n" + seed + "local = self\nother.pipe = local.saved", ""),
            (
                seed
                + "local = None\ndef copy():\n    nonlocal local\n    local = self\n    other.pipe = local.saved\ncopy()",
                ".copy",
            ),
            (seed + "for local in [self]:\n    other.pipe = local.saved", ""),
            (seed + "for local in (self,):\n    other.pipe = local.saved", ""),
            (
                seed
                + "for local in {'value': self}.values():\n    other.pipe = local.saved",
                "",
            ),
            (
                seed + "match self:\n    case local:\n        other.pipe = local.saved",
                "",
            ),
            (seed + "[setattr(other, 'pipe', local.saved) for local in [self]]", ""),
            (
                "self.saved = self.session if True else None\nbox = SimpleNamespace()\nbox.ref = self\nlocal = box\nother.pipe = local.ref.saved",
                "",
            ),
            (
                seed
                + "local = self\noutbound = local.saved\ndel self.saved\nother.pipe = outbound",
                "",
            ),
            (
                seed
                + "pair = (self, None)\nlocal, unused = pair\nother.pipe = local.saved",
                "",
            ),
            (
                seed
                + "box = SimpleNamespace(link=self)\nbox.link = self\nlocal = box\nother.pipe = local.link.saved",
                "",
            ),
        )
        for fixture, nested in fixtures:
            with self.subTest(fixture=fixture):
                self.rejected(
                    "from types import SimpleNamespace\nholder = SimpleNamespace()\nother = holder\n"
                    + fixture
                    + "\nemit = holder.pipe.get\nemit('offline')",
                    nested,
                )

    def test_every_bare_self_store_form_rejects_complete_audit(self):
        fixtures = (
            ("local = self", ""),
            ("local = other.ref = self", ""),
            ("local: object = self", ""),
            ("local = []\nlocal += self", ""),
            ("(local := self)", ""),
            ("local, unused = self, None", ""),
            ("[unused, [local]] = [None, [self]]", ""),
            ("self.ref = self", ""),
            ("box[0] = self", ""),
            ("setattr(self, 'ref', self)", ""),
            ("setattr(box, name, self)", ""),
            ("import builtins as b\nb.setattr(box, 'ref', self)", ""),
            ("class Box:\n    ref = self", ".Box"),
            ("items = [self]", ""),
            ("consume([self])", ""),
            ("consume((self,))", ""),
            ("consume({self})", ""),
            ("consume({'ref': self})", ""),
            ("consume({self: 'ref'})", ""),
            ("consume([self for unused in []])", ""),
            ("consume({self for unused in []})", ""),
            ("consume({unused: self for unused in []})", ""),
            ("consume(self for unused in [])", ""),
            ("def save(local=self):\n    pass", ".save"),
            ("def save(local=self, /):\n    pass", ".save"),
            ("def save(*, local=self):\n    pass", ".save"),
            ("async def save(local=self):\n    pass", ".save"),
            ("save = lambda local=self: None", ""),
            ("global local\nlocal = self", ""),
            (
                "local = None\ndef save():\n    nonlocal local\n    local = self",
                ".save",
            ),
            ("return self", ""),
            ("yield self", ""),
            ("yield from self", ""),
            ("save = lambda: self", ""),
            ("with self as local:\n    pass", ""),
            ("async def save():\n    async with self as local:\n        pass", ".save"),
            ("for local in self:\n    pass", ""),
            ("match self:\n    case local:\n        pass", ""),
            ("local = self if enabled else None", ""),
            ("local = self or None", ""),
        )
        for fixture, nested in fixtures:
            with self.subTest(fixture=fixture):
                self.rejected(fixture, nested)

    def test_alias_values_are_flagged_at_each_store(self):
        for seed, value in (
            ("local = self", "local"),
            ("one = self\nlocal = one", "local"),
            ("self.ref = self", "self.ref"),
            ("box.ref = self", "box.ref"),
            ("setattr(box, 'ref', self)", "box.ref"),
            ("class Box:\n    ref = self", "Box.ref"),
            ("class Box:\n    class Inner:\n        ref = self", "Box.Inner.ref"),
        ):
            with self.subTest(seed=seed):
                self.assert_store_rejected(
                    seed + f"\nfinal = {value}", f"bare-self-store final <- {value}"
                )
                self.assert_store_rejected(
                    seed + f"\nreturn {value}", f"bare-self-store return {value}"
                )

    def test_attribute_bases_and_plain_call_arguments_are_allowed(self):
        source = (
            "def run(self):\n    local = self.value\n    self.ref = local\n"
            "    consume(self)\n    consume(owner=self)\n    result = consume(self)\n"
            "    setattr(self, 'ref', None)\n    return consume(self)\n"
        )
        self.assertEqual([x for x in c["census"](source) if x.get("structural")], [])

    def test_alias_evidence_is_lexical_and_delete_aware(self):
        sources = (
            "def run(self):\n    local = self\n    del local\n    final = local",
            "def run(self):\n    local = self\n    def save(local=None):\n        final = local",
            "def run(self):\n    local = self\ndef sibling():\n    final = local",
            "def run(self):\n    box.ref = self\n    del box.ref\n    final = box.ref",
        )
        for source in sources:
            with self.subTest(source=source):
                calls = [site["call"] for site in c["census"](source)]
                self.assertTrue(
                    any(call.startswith("bare-self-store") for call in calls)
                )
                self.assertFalse(
                    any(call.startswith("bare-self-store final <-") for call in calls)
                )
        self.assert_store_rejected(
            "local = self\ncopy = local\ndel local\nfinal = copy",
            "bare-self-store final <- copy",
        )

    def test_sixty_four_rebinding_edges_finish_under_one_second(self):
        source = (
            "def run(self):\n    setattr(self, 'saved', self.session)\n"
            "    local = self\n"
            + "    local = local.loop\n" * 64
            + "    other.pipe = local.saved\n"
        )
        tree = ast.parse(source)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        aliases = c["LocalAliases"](tree, parents, c["TRANSPORT_MODULES"])

        def timeout(*args):
            raise AssertionError("64 rebindings exceeded the one-second bound")

        previous = signal.signal(signal.SIGALRM, timeout)
        started = time.monotonic()
        try:
            signal.setitimer(signal.ITIMER_REAL, 1.0)
            flags = list(c["TransportStores"](tree, aliases).violations())
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)
        self.assertTrue(any(text.startswith("bare-self-store") for _, text in flags))
        print(f"BARE-SELF: 64 rebinding edges in {elapsed:.6f}s")

    def test_sixty_four_tuple_name_fanout_levels_finish_under_one_second(self):
        source = (
            "def run(client):\n    a0 = client\n"
            + "".join(
                f"    a{i} = (client, a{i - 1}, a{i - 1})\n" for i in range(1, 65)
            )
            + "    return a64\n"
        )

        def timeout(*args):
            raise AssertionError("64 tuple/Name fanout levels exceeded one second")

        previous = signal.signal(signal.SIGALRM, timeout)
        started = time.monotonic()
        try:
            signal.setitimer(signal.ITIMER_REAL, 1.0)
            sites = c["census"](source)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.5)
        self.assertEqual(sites, [])
        print(f"SETTER-LOAD: 64 tuple/Name fanout levels in {elapsed:.6f}s")


if __name__ == "__main__":
    unittest.main()
