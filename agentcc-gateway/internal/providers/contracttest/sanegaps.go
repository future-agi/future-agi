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
	if len(gaps) == 0 {
		AssertSane(t, got, expectOutput)
		return
	}
	rec := &saneGapRecorder{TB: t}
	AssertSane(rec, got, expectOutput)

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
	if c.Live {
		return
	}
	for i, gap := range gaps {
		if !matched[i] {
			t.Errorf("known sanity gap %q no longer occurs; remove it from the test's expected gaps", gap)
		}
	}
}

// saneGapRecorder collects AssertSane's failures instead of failing the
// test. AssertSane uses only Helper and Errorf; anything else reaches the
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
