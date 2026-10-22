package sources

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

var cardNow = time.Date(2026, 10, 16, 12, 0, 0, 0, time.UTC)

func TestPriceCardSource(t *testing.T) {
	card, err := ParsePriceCard(fixture(t, "pricecard_direct.json"))
	if err != nil {
		t.Fatal(err)
	}
	if card.Provider != "anthropic-direct" || card.UpstreamSlug != "anthropic" || card.UpstreamFeeBps != 0 ||
		card.AsOf != "2026-10-01" || card.SourceURL == "" || len(card.Models) != 3 {
		t.Fatalf("card = %+v", card)
	}
	// The card's own fee is what it parses to.
	withFee, err := ParsePriceCard([]byte(strings.Replace(string(fixture(t, "pricecard_direct.json")),
		`"upstream_fee_bps": 0`, `"upstream_fee_bps": 120`, 1)))
	if err != nil || withFee.UpstreamFeeBps != 120 {
		t.Fatalf("a card with upstream_fee_bps 120 parses to %+v, %v", withFee, err)
	}

	snap := card.Snapshot("anthropic-direct", cardNow)
	if snap.ProviderID != "anthropic-direct" || snap.CatalogSource != catalog.SourcePriceCard ||
		snap.Source != catalog.OriginPriceCard || !snap.FetchedAt.Equal(cardNow) || len(snap.Models) != 3 {
		t.Fatalf("snapshot = %+v", snap)
	}
	sonnet := snap.Models["claude-3-5-sonnet-20241022"]
	if sonnet.Canonical != "anthropic/claude-3.5-sonnet" || sonnet.Name != "Claude 3.5 Sonnet" ||
		sonnet.ContextLength != 200000 || sonnet.MaxOutputTokens != 8192 || !sonnet.Priced() || sonnet.StalePrice ||
		sonnet.Prices != (catalog.Prices{Prompt: 3e-6, Completion: 15e-6, CacheRead: 3e-7, CacheWrite: 375e-8}) ||
		strings.Join(sonnet.SupportedParameters, ",") != "tools,max_tokens,temperature" ||
		strings.Join(sonnet.InputModalities, ",") != "text,image" || strings.Join(sonnet.OutputModalities, ",") != "text" {
		t.Fatalf("sonnet = %+v", sonnet)
	}
	if len(sonnet.Endpoints) != 1 {
		t.Fatalf("sonnet endpoints = %+v", sonnet.Endpoints)
	}
	e := sonnet.Endpoints[0]
	if e.ID != catalog.EndpointID(catalog.SourcePriceCard, "anthropic", "claude-3-5-sonnet-20241022") ||
		e.Source != catalog.SourcePriceCard || e.UpstreamProvider != "anthropic" || e.Tag != "anthropic" ||
		e.UpstreamFeeBps != 0 || e.Prices != sonnet.Prices || e.ContextLength != 200000 || e.MaxCompletion != 8192 ||
		e.DataCollection != catalog.DataCollectionDeny || strings.Join(e.Regions, ",") != "us" {
		t.Errorf("sonnet endpoint = %+v", e)
	}
	// A card model with no canonical id gets the derived one, numbers parse
	// like decimal strings, and a model with no prices is listed unpriced.
	if haiku := snap.Models["claude-haiku-4-5-20251001"]; haiku.Canonical != "" || haiku.Prices.Prompt != 1e-6 || len(haiku.Endpoints) != 1 {
		t.Errorf("haiku = %+v (the store derives its canonical id)", haiku)
	}
	if next := snap.Models["claude-next-preview"]; !next.Unpriced || next.Priced() || len(next.Endpoints) != 0 {
		t.Errorf("unpriced card model = %+v", next)
	}
	if withFee.Snapshot("anthropic-direct", cardNow).Models["claude-3-5-sonnet-20241022"].Endpoints[0].UpstreamFeeBps != 120 {
		t.Error("the card's fee is not on its endpoints")
	}

	// In a store, the merged canonical model carries the card's endpoint.
	store := catalog.NewStore()
	store.Put(snap)
	if !store.Load().Has("anthropic-direct", "claude-3-5-sonnet-20241022") || store.Load().Has("anthropic-direct", "claude-next-preview") {
		t.Error("Has must offer the priced card model and not the unpriced one")
	}
	if pc, ok := store.PriceCard("anthropic-direct", "claude-3-5-sonnet-20241022", ""); !ok || pc.Source != "price_card" || pc.Prompt != 3e-6 {
		t.Errorf("price card lookup = %+v, %v", pc, ok)
	}
}

// TestPriceCardPricesThatCannotBill: a direct upstream reports no cost, so a
// card model whose prices cannot price a call by themselves (image only, or
// all zero) is listed unpriced and never offered as managed.
func TestPriceCardPricesThatCannotBill(t *testing.T) {
	card, err := ParsePriceCard([]byte(`{"provider": "d", "upstream_slug": "v", "as_of": "2026-10-01", "models": [
		{"id": "img", "prices": {"image": "0.04"}},
		{"id": "zero", "prices": {"prompt": 0, "completion": 0}},
		{"id": "per-request", "prices": {"request": "0.01"}}
	]}`))
	if err != nil {
		t.Fatal(err)
	}
	snap := card.Snapshot("d", cardNow)
	for _, id := range []string{"img", "zero"} {
		if m := snap.Models[id]; !m.Unpriced || m.Sellable() || len(m.Endpoints) != 0 {
			t.Errorf("%s = %+v; want listed unpriced", id, m)
		}
	}
	if m := snap.Models["per-request"]; m.Unpriced || !m.Priced() || len(m.Endpoints) != 1 {
		t.Errorf("a request price alone = %+v; want priced", m)
	}
}

func TestPriceCardStaleExcluded(t *testing.T) {
	card, err := ParsePriceCard(fixture(t, "pricecard_direct.json"))
	if err != nil {
		t.Fatal(err)
	}
	asOf, _ := time.Parse(time.DateOnly, card.AsOf)

	fresh := card.Snapshot("d", asOf.Add(90*24*time.Hour))
	for id, m := range fresh.Models {
		if m.StalePrice {
			t.Errorf("%s is stale at exactly 90 days", id)
		}
	}
	stale := card.Snapshot("d", asOf.Add(91*24*time.Hour))
	sonnet := stale.Models["claude-3-5-sonnet-20241022"]
	if !sonnet.StalePrice || sonnet.Priced() || len(sonnet.Endpoints) != 0 || sonnet.Prices.Prompt != 3e-6 {
		t.Fatalf("a 91-day-old card's model = %+v; want stale_price, its prices kept, no endpoint", sonnet)
	}

	// Stale models drop out of managed eligibility: not offered (Has), not
	// priced, no endpoint in the merged view.
	store := catalog.NewStore()
	store.Put(stale)
	cat := store.Load()
	if cat.Has("d", "claude-3-5-sonnet-20241022") {
		t.Error("a stale-priced model is offered as managed")
	}
	if _, ok := store.PriceCard("d", "claude-3-5-sonnet-20241022", ""); ok {
		t.Error("a stale-priced model prices a call")
	}
	if infos := cat.ModelInfos(); infos["d/claude-3-5-sonnet-20241022"] != nil {
		t.Error("a stale-priced model reaches the model database")
	}
	if cm, ok := cat.CanonicalModel("anthropic/claude-3.5-sonnet"); !ok || len(cm.Endpoints) != 0 {
		t.Errorf("merged stale model = %+v, %v", cm, ok)
	}
	if m, _ := cat.Get("d", "claude-3-5-sonnet-20241022"); !m.StalePrice {
		t.Error("the stored model lost its stale_price flag")
	}
}

func TestPriceCardRejectsBadCards(t *testing.T) {
	good := string(fixture(t, "pricecard_direct.json"))
	for name, body := range map[string]string{
		"not json":         `{`,
		"no upstream slug": strings.Replace(good, `"upstream_slug": "anthropic"`, `"upstream_slug": ""`, 1),
		"no as_of":         strings.Replace(good, `"as_of": "2026-10-01"`, `"as_of": ""`, 1),
		"bad as_of":        strings.Replace(good, `"as_of": "2026-10-01"`, `"as_of": "Oct 1"`, 1),
		"negative fee":     strings.Replace(good, `"upstream_fee_bps": 0`, `"upstream_fee_bps": -5`, 1),
		"negative price":   strings.Replace(good, `"prompt": "0.000003"`, `"prompt": "-0.000003"`, 1),
		"price not number": strings.Replace(good, `"prompt": "0.000003"`, `"prompt": "three"`, 1),
		"no models":        `{"provider":"p","upstream_slug":"s","as_of":"2026-10-01","models":[]}`,
		"model without id": strings.Replace(good, `"id": "claude-next-preview",`, ``, 1),
		"duplicate id":     strings.Replace(good, `"id": "claude-next-preview"`, `"id": "claude-3-5-sonnet-20241022"`, 1),
		"bad collection":   strings.Replace(good, `"data_collection": "deny"`, `"data_collection": "never"`, 1),
		"bad canonical":    strings.Replace(good, `"canonical": "anthropic/claude-next-preview"`, `"canonical": "claude-next-preview"`, 1),
	} {
		if _, err := ParsePriceCard([]byte(body)); err == nil {
			t.Errorf("%s: parsed without an error", name)
		}
	}
}

func TestLoadPriceCardFileAndBundled(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "card.json")
	if err := os.WriteFile(path, fixture(t, "pricecard_direct.json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if card, err := LoadPriceCard(path, "anthropic-direct"); err != nil || card.UpstreamSlug != "anthropic" {
		t.Fatalf("file card = %+v, %v", card, err)
	}
	// A card names the provider it prices; another provider may not use it.
	if _, err := LoadPriceCard(path, "someone-else"); err == nil {
		t.Error("a card for anthropic-direct loaded for another provider")
	}
	if _, err := LoadPriceCard(filepath.Join(dir, "missing.json"), "anthropic-direct"); err == nil {
		t.Error("a missing card file loaded")
	}
	// No file: the card bundled for the provider id.
	bundled, err := LoadPriceCard("", "anthropic-direct")
	if err != nil {
		t.Fatalf("bundled anthropic-direct card: %v", err)
	}
	if bundled.Provider != "anthropic-direct" || bundled.UpstreamSlug != "anthropic" || bundled.UpstreamFeeBps != 0 || len(bundled.Models) == 0 {
		t.Errorf("bundled card = %+v", bundled)
	}
	for _, m := range bundled.Models {
		if m.Canonical == "" || catalog.Canonical("anthropic-direct", m.ID) != m.Canonical {
			t.Errorf("bundled card model %s: canonical %q, derived %q", m.ID, m.Canonical, catalog.Canonical("anthropic-direct", m.ID))
		}
	}
	if _, err := LoadPriceCard("", "no-such-provider"); err == nil {
		t.Error("a provider with no bundled card and no file loaded a card")
	}
}

// TestServesManaged pins the check every managed billing decision must make:
// an OpenRouter-sourced provider serves any model
// it routes, since OpenRouter reports each call's cost; a price-card provider
// serves only a model its fresh card prices.
func TestServesManaged(t *testing.T) {
	card, err := ParsePriceCard(fixture(t, "pricecard_direct.json"))
	if err != nil {
		t.Fatal(err)
	}
	asOf, _ := time.Parse(time.DateOnly, card.AsOf)
	store := catalog.NewStore()
	store.Put(card.Snapshot("direct", cardNow))
	store.Put(card.Snapshot("direct-stale", asOf.Add(91*24*time.Hour)))
	store.Put(&catalog.Snapshot{ProviderID: "openrouter", CatalogSource: catalog.SourceOpenRouter, FetchedAt: cardNow,
		Models: map[string]*catalog.Model{"vendor/listed": {ID: "vendor/listed", InputPerToken: 1e-6}}})
	cat := store.Load()
	for _, tc := range []struct {
		provider, model string
		want            bool
	}{
		{"direct", "claude-3-5-sonnet-20241022", true},
		{"direct", "claude-next-preview", false},    // listed, no price
		{"direct", "totally-unlisted-model", false}, // not on the card
		{"direct-stale", "claude-3-5-sonnet-20241022", false},
		{"openrouter", "vendor/listed", true},
		{"openrouter", "vendor/not-listed", true}, // OpenRouter reports its cost
		{"no-catalog", "claude-3-5-sonnet-20241022", false},
	} {
		if got := cat.ServesManaged(tc.provider, tc.model); got != tc.want {
			t.Errorf("ServesManaged(%s, %s) = %v, want %v", tc.provider, tc.model, got, tc.want)
		}
	}
	if (*catalog.Catalog)(nil).ServesManaged("openrouter", "x") {
		t.Error("a nil catalog serves a managed model")
	}
}
