package trajectory_test

import (
	"bytes"
	"flag"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/internal/trajectorycontract"
)

var updateContractFixtures = flag.Bool("update", false, "rewrite the shared trajectory contract fixtures")

// TestTrajectoryContractFixturesUpToDate regenerates the writer/reader contract
// fixtures from the real converter and fails when the committed files differ.
// Generate itself fails unless every extracted side reconstructs to the exact
// bytes the dedup-off path stores. The Python reader tests consume the same
// files, so a change to the envelope, hashes or previews fails both sides.
//
// Regenerate with:
//
//	go test ./pkg/trajectory -run TestTrajectoryContractFixturesUpToDate -update
func TestTrajectoryContractFixturesUpToDate(t *testing.T) {
	files, err := trajectorycontract.Generate()
	if err != nil {
		t.Fatalf("generate contract fixtures: %v", err)
	}
	dir := filepath.Join("..", "..", "..", filepath.FromSlash(trajectorycontract.FixtureDir))
	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		want := files[name]
		path := filepath.Join(dir, name)
		if *updateContractFixtures {
			if err := os.MkdirAll(dir, 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, want, 0o644); err != nil {
				t.Fatal(err)
			}
			continue
		}
		got, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read %s: %v (regenerate with -update)", path, err)
		}
		if !bytes.Equal(got, want) {
			t.Errorf("%s drifted from the converter output; regenerate with "+
				"go test ./pkg/trajectory -run TestTrajectoryContractFixturesUpToDate -update "+
				"and keep the Python reader tests green", path)
		}
	}
}

// TestTrajectoryContractCorpusCarriesUnicodeEscapes keeps the shared corpus
// honest about JSON escapes: at least one payload must carry an escaped
// accent, an escaped surrogate pair and an escaped U+2028 as the six-character
// sequences, which the reader must return unchanged.
func TestTrajectoryContractCorpusCarriesUnicodeEscapes(t *testing.T) {
	for _, escape := range []string{`é`, `🚀`, ` `} {
		found := false
		for _, c := range trajectorycontract.Corpus() {
			if (c.WantInput && strings.Contains(c.Input, escape)) || (c.WantOutput && strings.Contains(c.Output, escape)) {
				found = true
				break
			}
		}
		if !found {
			t.Errorf("no extracted corpus payload carries the escape %s", escape)
		}
	}
}
