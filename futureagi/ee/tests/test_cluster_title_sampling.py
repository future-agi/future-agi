"""The cluster title must be written from a sample of the group, not its head.

``generate_scan_cluster_title`` sends the model at most 25 briefs. That was
written when clusters were tens of members; distilling briefs before embedding
makes them hundreds, so the first 25 rows of an arbitrary DB order stopped being
a sample of anything. On one project's largest cluster (329 members) the head
happened to be the fabricated-number variants and the title came back
"Fabricated quantitative data instead of calling tools" — over-claiming against
every member that merely answered in prose. An evenly spread 25 gave "provided
text answers instead of executing required data retrieval tools".
"""

from ee.agenthub.trace_scanner.eval_cluster_title import _spread


class TestSpread:
    def test_short_lists_pass_through_whole(self):
        items = [f"b{i}" for i in range(7)]
        assert _spread(items, 25) == items

    def test_exactly_k_passes_through_whole(self):
        items = [f"b{i}" for i in range(25)]
        assert _spread(items, 25) == items

    def test_returns_exactly_k_from_a_long_list(self):
        assert len(_spread([f"b{i}" for i in range(329)], 25)) == 25

    def test_reaches_the_tail_not_just_the_head(self):
        """The actual defect: briefs[:25] over a 329-member cluster never sees a
        member past index 24, so a variant concentrated in the tail is invisible
        to the model writing the title."""
        items = [f"b{i}" for i in range(329)]
        picked = _spread(items, 25)
        assert picked[0] == "b0"
        assert int(picked[-1][1:]) > 300
        assert picked != items[:25]

    def test_samples_are_spread_not_clustered(self):
        items = list(range(1000))
        picked = _spread([str(i) for i in items], 25)
        gaps = [int(b) - int(a) for a, b in zip(picked, picked[1:])]
        assert min(gaps) > 0                      # strictly increasing, no repeats
        assert max(gaps) - min(gaps) <= 1         # evenly spaced

    def test_is_deterministic(self):
        """A cluster recomputes its title at several growth points; a random
        sample would hand it a different title each time for no reason."""
        items = [f"b{i}" for i in range(329)]
        assert _spread(items, 25) == _spread(items, 25)

    def test_preserves_order(self):
        items = [f"b{i}" for i in range(200)]
        picked = _spread(items, 25)
        assert picked == sorted(picked, key=lambda s: items.index(s))
