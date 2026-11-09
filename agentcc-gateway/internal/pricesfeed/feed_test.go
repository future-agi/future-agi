package pricesfeed

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

func fixtureInputs() []Input {
	cards := []models.PriceCard{
		{Prompt: 2e-6, Completion: 10e-6, CacheRead: .5e-6, CacheWrite: 3e-6, Reasoning: 4e-6, Request: .0001, WebSearch: .002},
		{Prompt: 2e-6, Completion: 10e-6},
		{Request: .04},
		{Completion: 1e-6},
		{ExplicitFree: true},
		{Prompt: .5e-6, Completion: .5e-6},
	}
	names := []string{"fixture/all", "fixture/fallback", "fixture/request", "fixture/zero-input", "fixture/listed:free", "fixture/half"}
	var out []Input
	for i, card := range cards {
		out = append(out, Input{ProviderID: "synthetic", CatalogSource: "openrouter", ModelID: names[i], EndpointID: "fixture-endpoint/" + names[i], Card: card})
	}
	return out
}

func fixtureFeed(t *testing.T) Feed {
	t.Helper()
	f, err := Build(fixtureInputs(), 500, SchemaVersion)
	if err != nil {
		t.Fatal(err)
	}
	// All parity consumers read the serialized public contract, not the
	// projection's in-memory document or the original fixture cards.
	var wire Document
	if err := json.Unmarshal(f.Body, &wire); err != nil {
		t.Fatal(err)
	}
	f.Document = wire
	return f
}

func offer(t *testing.T, f Feed, id string) Offer {
	t.Helper()
	for _, o := range f.Document.Offers {
		if o.ModelID == id {
			return o
		}
	}
	t.Fatalf("missing offer %s", id)
	return Offer{}
}

func dimension(t *testing.T, o Offer, name string) Dimension {
	t.Helper()
	for _, d := range o.Dimensions {
		if d.Name == name {
			return d
		}
	}
	t.Fatalf("missing dimension %s", name)
	return Dimension{}
}

// Consume the wire representation, not the input fixture's card. A null
// search quote is deliberately not usable to estimate a search/tool charge.
func projectedCard(t *testing.T, o Offer) models.PriceCard {
	t.Helper()
	p := models.PriceCard{ExplicitFree: o.ExplicitFree, Source: models.PriceSourceCatalog}
	for _, d := range o.Dimensions {
		if d.CardRateUSD == nil {
			if d.Name != "web_search" || d.Rule != "not_quoted" {
				t.Fatalf("unexpected unquoted dimension: %+v", d)
			}
			continue
		}
		rate, err := strconv.ParseFloat(*d.CardRateUSD, 64)
		if err != nil {
			t.Fatal(err)
		}
		switch d.Name {
		case "uncached_input":
			p.Prompt = rate
		case "output":
			p.Completion = rate
		case "cache_read":
			p.CacheRead = rate
		case "cache_write":
			p.CacheWrite = rate
		case "reasoning":
			p.Reasoning = rate
		case "request":
			p.Request = rate
		case "web_search":
			p.WebSearch = rate
		default:
			t.Fatalf("unknown dimension %s", d.Name)
		}
	}
	return p
}

func TestModelPricesDimensions(t *testing.T) {
	f := fixtureFeed(t)
	all := offer(t, f, "fixture/all")
	for _, tc := range []struct{ name, unit, rate, rule string }{
		{"uncached_input", "token", "0.000002", "prompt_minus_cache_unless_exclusive"},
		{"output", "token", "0.00001", "completion_minus_separately_priced_reasoning"},
		{"cache_read", "token", "0.0000005", "replaces_included_prompt"},
		{"cache_write", "token", "0.000003", "replaces_included_prompt"},
		{"reasoning", "token", "0.000004", "replaces_clamped_completion"},
		{"request", "request", "0.0001", "once_per_billable_attempt"},
		{"web_search", "search_request", "0.002", "per_reported_search_request"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d := dimension(t, all, tc.name)
			if d.Unit != tc.unit || d.Rule != tc.rule || d.ProviderRateUSD == nil || *d.ProviderRateUSD != tc.rate {
				t.Fatalf("dimension = %+v", d)
			}
		})
	}
	fallback := offer(t, f, "fixture/fallback")
	for _, name := range []string{"cache_read", "cache_write"} {
		d := dimension(t, fallback, name)
		if d.Rule != "fallback_to_prompt" || d.ProviderRateUSD == nil || *d.ProviderRateUSD != "0.000002" {
			t.Fatalf("fallback = %+v", d)
		}
	}
	r := dimension(t, fallback, "reasoning")
	if r.Rule != "included_in_output" || r.ProviderRateUSD != nil || r.NominalOneKeyRateUSD != nil {
		t.Fatalf("reasoning double charge: %+v", r)
	}
	w := dimension(t, fallback, "web_search")
	if w.Rule != "not_quoted" || w.CardRateUSD != nil || w.ProviderRateUSD != nil {
		t.Fatalf("unknown search became free: %+v", w)
	}
	for _, id := range []string{"fixture/request", "fixture/zero-input", "fixture/listed:free"} {
		d := dimension(t, offer(t, f, id), "uncached_input")
		if d.ProviderRateUSD == nil || *d.ProviderRateUSD != "0" {
			t.Fatalf("explicit zero lost for %s", id)
		}
	}
	if !offer(t, f, "fixture/listed:free").ExplicitFree {
		t.Fatal("explicit free evidence lost")
	}
	if got := *dimension(t, all, "uncached_input").NominalOneKeyRateUSD; got != "0.0000021" {
		t.Fatalf("nominal marked-up rate %s", got)
	}
	for _, p := range []models.PriceCard{{}, {Prompt: -1}, {Prompt: math.NaN()}, {Prompt: 1, Image: 1}} {
		_, err := Build([]Input{{ProviderID: "synthetic", CatalogSource: "openrouter", ModelID: "unsupported", Card: p}}, 500, SchemaVersion)
		if err == nil {
			t.Fatalf("unsupported card acquired quote: %+v", p)
		}
	}
}

func TestModelPricesBillingParity(t *testing.T) {
	f := fixtureFeed(t)
	full := models.Usage{PromptTokens: 100, CompletionTokens: 50, PromptDetails: models.PromptTokensDetails{CachedTokens: 20, CacheWriteTokens: 10}, CompletionDetails: models.CompletionTokensDetails{ReasoningTokens: 15}, ServerToolUse: &models.ServerToolUsage{WebSearchRequests: 2}}
	exclusive := full
	exclusive.PromptTokens = 5
	fallback := full
	fallback.ServerToolUse = nil
	reported := full
	reportedUSD := .000010
	reported.Cost = &reportedUSD
	reportedZero := full
	zeroUSD := 0.0
	reportedZero.Cost = &zeroUSD
	for _, tc := range []struct {
		name, id         string
		usage            models.Usage
		byok             bool
		upstream, billed int64
		source           string
	}{
		{"inclusive", "fixture/all", full, false, 4690, 4925, "catalog"},
		{"exclusive", "fixture/all", exclusive, false, 4560, 4788, "catalog"},
		{"cache_and_reasoning_fallback", "fixture/fallback", fallback, false, 700, 735, "catalog"},
		{"request_only", "fixture/request", models.Usage{PromptTokens: 100, CompletionTokens: 50}, false, 40000, 42000, "catalog"},
		{"zero_prompt", "fixture/zero-input", models.Usage{PromptTokens: 100, CompletionTokens: 2}, false, 2, 2, "catalog"},
		{"free", "fixture/listed:free", models.Usage{PromptTokens: 100, CompletionTokens: 50}, false, 0, 0, "catalog"},
		{"half_micro", "fixture/half", models.Usage{PromptTokens: 1}, false, 1, 1, "catalog"},
		{"no_dimension_rounding", "fixture/half", models.Usage{PromptTokens: 1, CompletionTokens: 1}, false, 1, 1, "catalog"},
		{"reported_precedence", "fixture/all", reported, false, 10, 11, "provider_reported"},
		{"reported_zero_precedence", "fixture/all", reportedZero, false, 0, 0, "provider_reported"},
		{"byok_upstream_is_not_free", "fixture/all", full, true, 4690, 4690, "catalog"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			p := projectedCard(t, offer(t, f, tc.id))
			a := models.Attempt{CredentialType: models.CredentialManaged}
			if tc.byok {
				a.CredentialType = models.CredentialBYOK
			}
			up, billed, landed, source, ok := cost.PriceAttempt(&a, &tc.usage, p, f.Document.OneKeyMarkupBps, 0)
			if !ok || up != tc.upstream || billed != tc.billed || source != tc.source || (tc.byok && landed != 0) {
				t.Fatalf("got %d/%d/%d %s %t", up, billed, landed, source, ok)
			}
		})
	}
	t.Run("fractional_boundary_and_admission_is_not_settlement", func(t *testing.T) {
		for _, tc := range []struct {
			rate float64
			up   int64
		}{{.499999e-6, 0}, {.5e-6, 1}, {2.5e-6, 3}} {
			wire, err := Build([]Input{{ProviderID: "synthetic", CatalogSource: "openrouter", ModelID: "boundary", Card: models.PriceCard{Completion: tc.rate}}}, 500, SchemaVersion)
			if err != nil {
				t.Fatal(err)
			}
			p := projectedCard(t, wire.Document.Offers[0])
			u, _, _, _, _ := cost.PriceAttempt(&models.Attempt{CredentialType: models.CredentialManaged}, &models.Usage{CompletionTokens: 1}, p, 500, 0)
			if u != tc.up {
				t.Fatalf("boundary %g = %d", tc.rate, u)
			}
			ceiling := billing.EstimateWorstCaseMicros(nil, p, 500, 1)
			want := int64(math.Ceil(math.Round(tc.rate*1e6*1.05*1e6) / 1e6))
			if ceiling != want {
				t.Fatalf("admission = %d want %d", ceiling, want)
			}
			if tc.up == 0 && ceiling != 1 {
				t.Fatal("admission ceiling was confused with settled zero")
			}
		}
	})
	t.Run("finalization_sums_rounded_attempts", func(t *testing.T) {
		wire, err := Build([]Input{{ProviderID: "synthetic", CatalogSource: "openrouter", ModelID: "sum", Card: models.PriceCard{Prompt: 1e-6}}}, 500, SchemaVersion)
		if err != nil {
			t.Fatal(err)
		}
		p := projectedCard(t, wire.Document.Offers[0])
		plugin := cost.New(true, func() *modeldb.ModelDB { return nil }, nil, nil)
		plugin.SetManagedMarkup(5)
		plugin.SetPricer(cost.NewPricer(nil, func() models.PriceLookup { return func(_, _, _ string) (models.PriceCard, bool) { return p, true } }))
		u := models.Usage{PromptTokens: 10}
		rc := &models.RequestContext{Model: "sum", Provider: "synthetic", Metadata: map[string]string{"billing_mode": "managed"}, Response: &models.ChatCompletionResponse{Usage: &u}}
		rc.AddAttempt(models.Attempt{Provider: "synthetic", Model: "sum", CredentialType: models.CredentialManaged, Outcome: models.OutcomeError, PromptTokens: 10})
		rc.AddAttempt(models.Attempt{Provider: "synthetic", Model: "sum", CredentialType: models.CredentialManaged, Outcome: models.OutcomeSuccess})
		plugin.FinalizeRequest(rc)
		if rc.Billing.UpstreamMicros != 20 || rc.Billing.BilledMicros != 22 || rc.Billing.MarkupBps != 500 {
			t.Fatalf("attempt sum: %+v", rc.Billing)
		}
	})
}

func TestModelPricesCanonicalSerialization(t *testing.T) {
	f := fixtureFeed(t)
	inputs := fixtureInputs()
	for i, j := 0, len(inputs)-1; i < j; i, j = i+1, j-1 {
		inputs[i], inputs[j] = inputs[j], inputs[i]
	}
	g, err := Build(inputs, 500, SchemaVersion)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(f.Body, g.Body) || f.ETag != g.ETag {
		t.Fatal("input iteration changed semantic bytes")
	}
	for i := range inputs {
		inputs[i].Card.SnapshotID = "private-catalog-snapshot-not-for-publication"
		inputs[i].Card.Source = models.PriceSourceCatalog
	}
	g, err = Build(inputs, 500, SchemaVersion)
	if err != nil || !bytes.Equal(f.Body, g.Body) || f.ETag != g.ETag {
		t.Fatalf("internal provenance changed public content: %v", err)
	}
	for _, tc := range []struct {
		name   string
		change func([]Input) []Input
		markup int
		schema string
	}{
		{"price", func(in []Input) []Input { in[0].Card.Prompt *= 2; return in }, 500, SchemaVersion},
		{"eligibility", func(in []Input) []Input { return in[1:] }, 500, SchemaVersion},
		{"markup", func(in []Input) []Input { return in }, 600, SchemaVersion},
		{"schema", func(in []Input) []Input { return in }, 500, "model-prices-v2"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g, err := Build(tc.change(fixtureInputs()), tc.markup, tc.schema)
			if err != nil {
				t.Fatal(err)
			}
			if g.ETag == f.ETag || g.Document.ContentVersion == f.Document.ContentVersion {
				t.Fatal("semantic change did not change content identity")
			}
			if tc.name == "markup" && g.Document.Sources[0].SnapshotID != f.Document.Sources[0].SnapshotID {
				t.Fatal("markup changed the provider's public source snapshot")
			}
		})
	}
}

func TestModelPricesFixture(t *testing.T) {
	f := fixtureFeed(t)
	pretty, err := json.MarshalIndent(f.Document, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	pretty = append(pretty, '\n')
	if os.Getenv("MODEL_PRICES_FIXTURE_STDOUT") == "1" {
		// The isolated test container needs only a read-only binary mount;
		// the generator writes the fixture as its own host user.
		fmt.Println("MODEL_PRICES_FIXTURE:" + base64.StdEncoding.EncodeToString(pretty))
		return
	}
	golden, err := os.ReadFile(filepath.Join("testdata", "model-prices-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(golden, pretty) {
		t.Fatal("fixture drift: regenerate with scripts/model-prices/check.py")
	}
}
