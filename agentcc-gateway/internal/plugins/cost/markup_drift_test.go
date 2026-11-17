package cost

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// DT7, the gateway markup check (price-catalog-v1 sections 6 and 9): the
// markup the gateway adds to a managed call's upstream cost is the price
// catalog's model_pricing.markup_bps, and its tool markup inherits it exactly
// when the catalog's tool_markup_bps is null.
//
// The catalog lives in the private ee repo, so this side pins the catalog's
// value, 500 bps (5%), and BL-CAT's drift test pins the catalog to it. Set
// PRICE_CATALOG_FILE to a catalog file (the internal catalog or its public
// projection) to check against that file instead.
//
// The pinned catalog: markup_bps 500, and tool_markup_bps null (inherit).
var pinnedCatalog = catalogMarkup{bps: 500}

// catalogMarkup is the catalog's model pricing: its markup in bps, and its
// tool markup in bps or nil when the tool markup inherits the markup.
type catalogMarkup struct {
	bps     int
	toolBps *int
}

// readCatalogMarkup reads model_pricing from a price-catalog-v1 file. Both the
// internal catalog and its public projection hold each value as {"value": …}.
func readCatalogMarkup(path string) (catalogMarkup, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return catalogMarkup{}, err
	}
	var doc struct {
		ModelPricing struct {
			MarkupBps     *struct{ Value *int } `json:"markup_bps"`
			ToolMarkupBps *struct{ Value *int } `json:"tool_markup_bps"`
		} `json:"model_pricing"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		return catalogMarkup{}, fmt.Errorf("%s: %w", path, err)
	}
	mp := doc.ModelPricing
	if mp.MarkupBps == nil || mp.MarkupBps.Value == nil {
		return catalogMarkup{}, fmt.Errorf("%s: no model_pricing.markup_bps.value", path)
	}
	if mp.ToolMarkupBps == nil {
		return catalogMarkup{}, fmt.Errorf("%s: no model_pricing.tool_markup_bps", path)
	}
	return catalogMarkup{bps: *mp.MarkupBps.Value, toolBps: mp.ToolMarkupBps.Value}, nil
}

func catalogUnderTest(t *testing.T) catalogMarkup {
	t.Helper()
	path := os.Getenv("PRICE_CATALOG_FILE")
	if path == "" {
		return pinnedCatalog
	}
	m, err := readCatalogMarkup(path)
	if err != nil {
		t.Fatalf("PRICE_CATALOG_FILE: %v", err)
	}
	return m
}

// percentBps is round(percent x 100), the catalog's rule for a markup percent.
func percentBps(percent float64) int {
	return int(math.Round(percent * 100))
}

// markupDrift lists every way the gateway's managed config differs from the
// catalog's model pricing.
func markupDrift(cat catalogMarkup, m config.ManagedConfig) []string {
	var drift []string
	if got := percentBps(m.MarkupPercent); got != cat.bps {
		drift = append(drift, fmt.Sprintf("managed.markup_percent %v is %d bps; the catalog's markup_bps is %d", m.MarkupPercent, got, cat.bps))
	}
	if got := markupBpsOf(m.MarkupPercent); got != cat.bps {
		drift = append(drift, fmt.Sprintf("the cost plugin bills managed.markup_percent %v at %d bps; the catalog's markup_bps is %d", m.MarkupPercent, got, cat.bps))
	}
	inherits := m.ToolMarkupPercent == -1
	switch {
	case cat.toolBps == nil && !inherits:
		drift = append(drift, fmt.Sprintf("managed.tool_markup_percent is %v; the catalog's tool_markup_bps is null, so it must be -1 (inherit)", m.ToolMarkupPercent))
	case cat.toolBps != nil && inherits:
		drift = append(drift, fmt.Sprintf("managed.tool_markup_percent is -1 (inherit); the catalog's tool_markup_bps is %d", *cat.toolBps))
	case cat.toolBps != nil && m.ToolMarkupBps() != *cat.toolBps:
		drift = append(drift, fmt.Sprintf("managed.tool_markup_percent %v is %d bps; the catalog's tool_markup_bps is %d", m.ToolMarkupPercent, m.ToolMarkupBps(), *cat.toolBps))
	}
	return drift
}

func TestMarkupDrift_DefaultConfigIsTheCatalogs(t *testing.T) {
	for _, d := range markupDrift(catalogUnderTest(t), config.DefaultConfig().Managed) {
		t.Error(d)
	}
}

// exampleManaged is the active managed: block, including commented optional
// defaults, in the bundled self-hosted config.example.yaml.
var exampleManaged = regexp.MustCompile(`(?m)^managed:\n((?:(?:  |#   ).*\n)+)`)
var exampleMarkup = regexp.MustCompile(`(?m)^#   markup_percent:\s*([0-9.]+)`)

func TestMarkupDrift_ExampleConfigStatesTheCatalogsMarkup(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "config.example.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	block := exampleManaged.FindSubmatch(raw)
	if block == nil {
		t.Fatal("config.example.yaml has no active managed: block")
	}
	m := exampleMarkup.FindSubmatch(block[1])
	if m == nil {
		t.Fatal("config.example.yaml's managed: block states no markup_percent")
	}
	percent, err := strconv.ParseFloat(string(m[1]), 64)
	if err != nil {
		t.Fatal(err)
	}
	if cat := catalogUnderTest(t); percentBps(percent) != cat.bps {
		t.Errorf("config.example.yaml states markup_percent %v (%d bps); the catalog's markup_bps is %d", percent, percentBps(percent), cat.bps)
	}
}

func TestMarkupDrift_ManagedIsBilledAtTheCatalogsMarkupAndBYOKAtNone(t *testing.T) {
	bps := markupBpsOf(config.DefaultConfig().Managed.MarkupPercent)
	const upstream = 1_000_000 // $1.00
	billed, _ := billedAndLanded(upstream, true, bps, 0, 0)
	if want := int64(upstream * (10_000 + catalogUnderTest(t).bps) / 10_000); billed != want {
		t.Errorf("a managed call of $1.00 upstream bills %d micros; at the catalog's markup it is %d", billed, want)
	}
	if billed, landed := billedAndLanded(upstream, false, bps, 0, 0); billed != upstream || landed != 0 {
		t.Errorf("a BYOK call of $1.00 upstream bills %d micros and lands %d; it carries no markup", billed, landed)
	}
}

// The check itself: it finds a drift in either direction.
func TestMarkupDrift_ReportsEveryDifference(t *testing.T) {
	defaults := config.DefaultConfig().Managed
	five := 500
	cases := []struct {
		name string
		cat  catalogMarkup
		m    config.ManagedConfig
		want int
	}{
		{"same", catalogMarkup{bps: 500}, defaults, 0},
		{"catalog markup moved", catalogMarkup{bps: 600}, defaults, 2},
		{"gateway markup moved", catalogMarkup{bps: 500}, config.ManagedConfig{MarkupPercent: 6, ToolMarkupPercent: -1}, 2},
		{"fractional percent rounds", catalogMarkup{bps: 550}, config.ManagedConfig{MarkupPercent: 5.5, ToolMarkupPercent: -1}, 0},
		{"tool markup set, catalog inherits", catalogMarkup{bps: 500}, config.ManagedConfig{MarkupPercent: 5, ToolMarkupPercent: 5}, 1},
		{"catalog tool markup set, gateway inherits", catalogMarkup{bps: 500, toolBps: &five}, defaults, 1},
		{"both set, equal", catalogMarkup{bps: 500, toolBps: &five}, config.ManagedConfig{MarkupPercent: 5, ToolMarkupPercent: 5}, 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := markupDrift(tc.cat, tc.m); len(got) != tc.want {
				t.Errorf("markupDrift = %q, want %d findings", got, tc.want)
			}
		})
	}
}

func TestMarkupDrift_ReadsACatalogFile(t *testing.T) {
	dir := t.TempDir()
	write := func(name, body string) string {
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	internal := write("internal.json", `{"model_pricing": {
		"markup_bps": {"value": 500, "status": "unconfirmed", "source": ["config.go"], "visibility": "public"},
		"tool_markup_bps": {"value": null, "status": "unconfirmed", "visibility": "public"}}}`)
	public := write("public.json", `{"model_pricing": {
		"markup_bps": {"value": 600, "status": "confirmed"},
		"tool_markup_bps": {"value": 450, "status": "confirmed"}}}`)
	missing := write("missing.json", `{"model_pricing": {"tool_markup_bps": {"value": null}}}`)

	got, err := readCatalogMarkup(internal)
	if err != nil || got.bps != 500 || got.toolBps != nil {
		t.Fatalf("internal catalog = %+v, %v", got, err)
	}
	got, err = readCatalogMarkup(public)
	if err != nil || got.bps != 600 || got.toolBps == nil || *got.toolBps != 450 {
		t.Fatalf("public projection = %+v, %v", got, err)
	}
	if d := markupDrift(got, config.DefaultConfig().Managed); len(d) != 3 {
		t.Errorf("drift against a 600/450 catalog = %q, want 3 findings", d)
	}
	if _, err := readCatalogMarkup(missing); err == nil {
		t.Error("a catalog without markup_bps must be refused")
	}
}
