package contracttest

import (
	"fmt"
	"strings"
	"sync"
	"testing"
)

// AssertSaneWithGaps is AssertSane for a case whose canonical output is known
// to break some of AssertSane's rules because of an adapter gap, such as a
// stream parser that never reads usage. Each gap is a substring of a failure
// AssertSane reports (e.g. "usage is missing").
//
// It fails t for every failure no gap matches, so the case still guards the
// other rules, and for every gap that matches no failure, so the gap is
// removed from the test once the adapter is fixed: the same rule as a stale
// allowlist entry. Like that rule, the second check is skipped for a live case,
// whose real response need not trigger every gap its fixture pinned (a prompt
// the model answers instead of blocking, say). With no gaps it is AssertSane.
func AssertSaneWithGaps(t testing.TB, c Case, got Completion, expectOutput bool, gaps ...string) {
	t.Helper()
	expectGaps(t, c, gaps, func(tb testing.TB) { AssertSane(tb, got, expectOutput) })
}

// ExpectGaps runs check, a sanity check such as AssertSane, against the gaps
// case.json lists under sanity_gaps: a failure a gap's Failure matches is
// expected, any other failure fails t, and a gap that matches no failure
// fails t as stale (except live, where a real response need not trigger it).
// AssertSaneError applies it itself.
func ExpectGaps(t testing.TB, c Case, check func(t testing.TB)) {
	t.Helper()
	gaps := make([]string, len(c.SanityGaps))
	for i, g := range c.SanityGaps {
		gaps[i] = g.Failure
	}
	expectGaps(t, c, gaps, check)
}

func expectGaps(t testing.TB, c Case, gaps []string, check func(testing.TB)) {
	t.Helper()
	if len(gaps) == 0 {
		check(t)
		return
	}
	rec := &saneGapRecorder{TB: t}
	check(rec)

	matched := make([]bool, len(gaps))
	for _, failure := range rec.failures() {
		known := false
		for i, gap := range gaps {
			if strings.Contains(failure, gap) {
				matched[i], known = true, true
			}
		}
		if !known {
			t.Errorf("%s", failure)
		}
	}
	if c.Live || c.mutant {
		return
	}
	for i, gap := range gaps {
		if !matched[i] {
			t.Errorf("known sanity gap %q no longer occurs; remove it from the case's expected gaps", gap)
		}
	}
}

// saneGapRecorder collects a sanity check's failures instead of failing the
// test. The checks use only Helper and Errorf; anything else reaches the
// embedded test.
type saneGapRecorder struct {
	testing.TB

	mu   sync.Mutex
	errs []string
}

func (r *saneGapRecorder) Helper() {}

func (r *saneGapRecorder) Errorf(format string, args ...any) {
	r.mu.Lock()
	r.errs = append(r.errs, fmt.Sprintf(format, args...))
	r.mu.Unlock()
}

func (r *saneGapRecorder) failures() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.errs...)
}
