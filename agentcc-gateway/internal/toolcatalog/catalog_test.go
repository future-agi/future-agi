package toolcatalog

import (
	"encoding/json"
	"io/fs"
	"slices"
	"strings"
	"testing"

	seedcards "github.com/futureagi/agentcc-gateway/catalog/tools"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
)

// The shipped seed cards load, each file validates against the card
// schema and sits at <capability>/<vendor>.json, the native, unconfirmed
// and byok_only resale states all occur, and the cards the contract names
// are seeded as it says.
func TestSeedCardsValid(t *testing.T) {
	cat := seedCatalog(t)
	cards := cat.Cards()
	if len(cards) < 90 {
		t.Fatalf("%d seed cards, want at least 90 (one per seeded vendor and capability)", len(cards))
	}
	files := 0
	err := fs.WalkDir(seedcards.Cards, ".", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || slices.Contains(NonCardFiles, p) {
			return err
		}
		files++
		raw, err := fs.ReadFile(seedcards.Cards, p)
		if err != nil {
			return err
		}
		c, err := ParseCard(raw)
		if err != nil {
			t.Errorf("%s: %v", p, err)
			return nil
		}
		if want := c.Capability + "/" + c.Vendor + ".json"; p != want {
			t.Errorf("%s holds the card %s; seed cards live at <capability>/<vendor>.json", p, want)
		}
		if errs := schematest.ValidateDef(t, cardSchema, "Card", decodeAny(t, raw)); len(errs) > 0 {
			t.Errorf("%s fails tool-price-card-v1 $defs/Card: %v", p, errs)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if files != len(cards) {
		t.Errorf("%d seed files but %d cards loaded", files, len(cards))
	}
	statuses := map[string]int{}
	for _, c := range cards {
		statuses[c.ResaleStatus]++
	}
	for _, s := range []string{ResaleNative, ResaleUnconfirmed, ResaleByokOnly} {
		if statuses[s] == 0 {
			t.Errorf("no seed card is %s", s)
		}
	}

	// Native seeding: the built-in checks and the native fetcher are free and
	// priced; Protect, the Turing judges and the native sandbox tier wait for
	// the owner's price, and Turing is unconfirmed (its fallbacks are third
	// party models).
	native := []struct {
		capability, vendor, status string
		pending                    bool
	}{
		{"guardrails", "futureagi_builtin", ResaleNative, false},
		{"fetch", "futureagi_fetcher", ResaleNative, false},
		{"guardrails", "futureagi_protect", ResaleNative, true},
		{"evals", "futureagi_turing", ResaleUnconfirmed, true},
		{"sandboxes", "futureagi", ResaleNative, true},
	}
	for _, n := range native {
		c, ok := cat.Card(n.capability, n.vendor)
		if !ok {
			t.Errorf("no seed card %s/%s", n.capability, n.vendor)
			continue
		}
		if c.ResaleStatus != n.status || c.OwnerPricePending != n.pending {
			t.Errorf("%s/%s: %s pending=%v, want %s pending=%v", n.capability, n.vendor, c.ResaleStatus, c.OwnerPricePending, n.status, n.pending)
		}
		for _, s := range c.SKUs {
			if s.USDPerUnit != 0 {
				t.Errorf("%s/%s %s costs %v; it is seeded at $0", n.capability, n.vendor, s.ID, s.USDPerUnit)
			}
		}
	}

	// Vendor terms profiles.
	for _, v := range cat.VendorCards("parallel") {
		if !v.NoDivert || !v.NoBenchmark || v.Cacheable != CacheNone {
			t.Errorf("parallel %s: no_divert=%v no_benchmark=%v cacheable=%s, want true true none", v.Capability, v.NoDivert, v.NoBenchmark, v.Cacheable)
		}
	}
	for _, v := range cat.VendorCards("tavily") {
		if !v.NoPerfDisclosure || v.TrainsOnInput != TrainsAIFeaturesOnly || !v.ImprovementLicense {
			t.Errorf("tavily %s: no_perf_disclosure=%v trains_on_input=%s improvement_license=%v", v.Capability, v.NoPerfDisclosure, v.TrainsOnInput, v.ImprovementLicense)
		}
	}
	for _, v := range cat.VendorCards("brave") {
		if !v.NoBenchmark || v.ResultsRetention != RetentionMetadata || v.EvalExport != EvalExportForbidden || v.ResaleStatus != ResaleByokOnly {
			t.Errorf("brave %s: no_benchmark=%v retention=%s eval_export=%s resale=%s", v.Capability, v.NoBenchmark, v.ResultsRetention, v.EvalExport, v.ResaleStatus)
		}
	}
	for _, vendor := range []string{"parallel", "tavily", "brave", "exa"} {
		if len(cat.VendorCards(vendor)) == 0 {
			t.Errorf("no seed card for %s", vendor)
		}
	}
}

// Exa's terms bar any copy beyond a browser cache: every
// Exa card is uncacheable and keeps metadata only, and an operator catalog
// cannot loosen that.
func TestExaNeverCacheable(t *testing.T) {
	exa := seedCatalog(t).VendorCards("exa")
	if len(exa) < 2 {
		t.Fatalf("%d exa seed cards; want search and fetch (contents)", len(exa))
	}
	for _, c := range exa {
		if c.Cacheable != CacheNone || c.ResultsRetention != RetentionMetadata || !c.ImprovementLicense {
			t.Errorf("exa %s: cacheable=%s retention=%s improvement_license=%v, want none metadata true",
				c.Capability, c.Cacheable, c.ResultsRetention, c.ImprovementLicense)
		}
	}
	base := with(with(validCard(), "vendor", "exa"), "improvement_license", true)
	base = with(base, "results_retention", "metadata")
	if _, err := LoadDir(writeCards(t, base)); err != nil {
		t.Fatalf("an exa card at the floor is refused: %v", err)
	}
	for name, card := range map[string]map[string]any{
		"cacheable per org":      with(base, "cacheable", "org"),
		"cacheable globally":     with(base, "cacheable", "global"),
		"full retention":         with(base, "results_retention", "full"),
		"no improvement licence": with(base, "improvement_license", false),
	} {
		_, err := LoadDir(writeCards(t, card))
		if err == nil || !strings.Contains(err.Error(), "exa") {
			t.Errorf("%s: got %v, want the exa terms floor to refuse it", name, err)
		}
	}
	if _, err := LoadDir(writeCards(t, with(base, "results_retention", "none"))); err != nil {
		t.Errorf("a stricter exa card (retention none) is refused: %v", err)
	}
}

// The loader's file rules: a card's file is named for its vendor, a
// capability and vendor appear once, the listed measurement file is skipped,
// and every other JSON file must be a card, so a card with misspelled keys
// fails the load instead of vanishing.
func TestCatalogFileRules(t *testing.T) {
	good := validCard()
	dir := writeCards(t, good)
	writeFile(t, dir, "fetch/native_block_rate.json", []byte(`{"measured_at":"2026-10-15","sample_size":10}`))
	writeFile(t, dir, "README.md", []byte("not json"))
	cat, err := LoadDir(dir)
	if err != nil {
		t.Fatalf("a valid catalog with a measurement file and a README is refused: %v", err)
	}
	if len(cat.Cards()) != 1 {
		t.Fatalf("%d cards, want 1", len(cat.Cards()))
	}
	bad := map[string]func(dir string){
		"file named for another vendor": func(d string) { writeFile(t, d, "search/other.json", mustJSON(t, good)) },
		"same card twice":               func(d string) { writeFile(t, d, "web/acme.json", mustJSON(t, good)) },
		"card at the root":              func(d string) { writeFile(t, d, "acme.json", mustJSON(t, good)) },
		"card too deep":                 func(d string) { writeFile(t, d, "search/x/acme.json", mustJSON(t, good)) },
		"partial card":                  func(d string) { writeFile(t, d, "search/bad.json", []byte(`{"vendor":"bad"}`)) },
		"not json":                      func(d string) { writeFile(t, d, "search/broken.json", []byte(`{`)) },
		"misspelled card keys": func(d string) {
			writeFile(t, d, "search/typo.json", []byte(`{"Vendor":"typo","Capability":"search","SKUs":[]}`))
		},
		"a JSON file that is not a card": func(d string) {
			writeFile(t, d, "search/notes.json", []byte(`{"measured_at":"2026-10-15"}`))
		},
		"the measurement file in another directory": func(d string) {
			writeFile(t, d, "search/native_block_rate.json", []byte(`{"measured_at":"2026-10-15"}`))
		},
	}
	for name, add := range bad {
		d := writeCards(t, good)
		add(d)
		if _, err := LoadDir(d); err == nil {
			t.Errorf("%s: loaded", name)
		}
	}
	if _, err := LoadDir(t.TempDir() + "/missing"); err == nil {
		t.Error("a missing catalog_dir loaded")
	}
}

func decodeAny(t *testing.T, raw []byte) any {
	t.Helper()
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatal(err)
	}
	return v
}
