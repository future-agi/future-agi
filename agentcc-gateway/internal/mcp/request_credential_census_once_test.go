package mcp

import (
	"strings"
	"testing"
)

// The census skips a Do call only when go/types resolves it to sync.Once's
// method: on a sync.Once, a *sync.Once, or promoted from an embedded one. A
// name never decides. A receiver whose type cannot be resolved (a file
// excluded by build constraints, a package with a type error, an import that
// does not load) is still counted.
func TestK2R7CensusSkipsOnlyResolvedSyncOnce(t *testing.T) {
	sources := map[string]string{
		"cmd/synthetic-census/once.go": `package synthetic

import "sync"

type sender struct{}

func (sender) Do(func()) {}

type Once struct{}

func (*Once) Do(func()) {}

type embedsOnce struct{ sync.Once }

type embedsOncePointer struct{ *sync.Once }

type holder struct{ closeOnce sender }

func direct() {
	var once sync.Once
	once.Do(func() {})
}

func pointer(once *sync.Once) { once.Do(func() {}) }

func embedded(e *embedsOnce) { e.Do(func() {}) }

func embeddedPointer(e embedsOncePointer) { e.Do(func() {}) }

func namedOnce() {
	var once sender
	once.Do(func() {})
}

func fieldNamedOnce(h holder) { h.closeOnce.Do(func() {}) }

func lookalikeOnce(once *Once) { once.Do(func() {}) }
`,
		"cmd/synthetic-census/tagged.go": `//go:build censusnever

package synthetic

import "sync"

func tagged(once *sync.Once) { once.Do(func() {}) }
`,
		"cmd/synthetic-census-broken/broken.go": `package synthetic

import "sync"

var mismatch int = "not an int"

func broken(once *sync.Once) { once.Do(func() {}) }

func untyped() { undefinedOnce.Do(func() {}) }
`,
		"cmd/synthetic-census-missing/missing.go": `package synthetic

import (
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/censusmissing"
)

func missing(once *sync.Once) {
	censusmissing.Touch()
	once.Do(func() {})
}
`,
	}
	problems := r7CustodyProblems(sources, map[string]r7DispatchSite{})
	got := map[string]bool{}
	for _, problem := range problems {
		if strings.HasPrefix(problem, "parse ") {
			t.Fatalf("synthetic sources must parse: %s", problem)
		}
		got[problem] = true
	}
	skipped := []string{
		"cmd/synthetic-census/once.go|direct|Do",
		"cmd/synthetic-census/once.go|pointer|Do",
		"cmd/synthetic-census/once.go|embedded|Do",
		"cmd/synthetic-census/once.go|embeddedPointer|Do",
	}
	for _, key := range skipped {
		for _, problem := range problems {
			if strings.Contains(problem, key) {
				t.Errorf("resolved sync.Once call was counted: %s", problem)
			}
		}
	}
	counted := []string{
		"cmd/synthetic-census/once.go|namedOnce|Do",
		"cmd/synthetic-census/once.go|fieldNamedOnce|Do",
		"cmd/synthetic-census/once.go|lookalikeOnce|Do",
		"cmd/synthetic-census/tagged.go|tagged|Do",
		"cmd/synthetic-census-broken/broken.go|broken|Do",
		"cmd/synthetic-census-broken/broken.go|untyped|Do",
		"cmd/synthetic-census-missing/missing.go|missing|Do",
	}
	for _, key := range counted {
		if !got["dispatch census "+key+": got 1 want 0"] {
			t.Errorf("Do call not counted: %s; problems: %v", key, problems)
		}
	}
}
