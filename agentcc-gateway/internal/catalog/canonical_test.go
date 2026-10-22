package catalog

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// knownAliasPairs are model ids as sources, SDKs and other gateways spell
// them, with the canonical id each must map to. They cover every rule
// Canonical applies and the explicit alias table, so a rule or an alias that
// changes fails here.
var knownAliasPairs = []struct{ provider, in, want string }{
	// The contract's own examples.
	{"openai", "gpt-4o", "openai/gpt-4o"},
	{"anthropic-direct", "claude-3-5-sonnet-20241022", "anthropic/claude-3.5-sonnet"},
	// OpenRouter ids are canonical already, minus case and variants.
	{"openrouter", "anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-5.5"},
	{"openrouter", "anthropic/claude-sonnet-5.5:batch", "anthropic/claude-sonnet-5.5"},
	{"openrouter", "ANTHROPIC/Claude-Sonnet-5.5", "anthropic/claude-sonnet-5.5"},
	// OpenRouter's dated canonical_slug names its undated id.
	{"openrouter", "anthropic/claude-4.5-haiku-20251001", "anthropic/claude-haiku-4.5"},
	// Anthropic's native ids: dashes for the version, a date suffix.
	{"anthropic-direct", "claude-haiku-4-5-20251001", "anthropic/claude-haiku-4.5"},
	{"anthropic-direct", "claude-opus-4-8", "anthropic/claude-opus-4.8"},
	{"anthropic-direct", "claude-sonnet-5", "anthropic/claude-sonnet-5"},
	{"mock-direct", "mock-direct/claude-sonnet-5", "anthropic/claude-sonnet-5"},
	// Model-database prefixes and providers.
	{"vertex", "gemini/gemini-3.7-flash", "google/gemini-3.7-flash"},
	{"vertex", "gemini-3.7-flash", "google/gemini-3.7-flash"},
	{"xai", "xai/grok-4.6", "x-ai/grok-4.6"},
	{"xai", "grok-4.6", "x-ai/grok-4.6"},
	{"mistral", "mistral/mistral-medium-3.5", "mistralai/mistral-medium-3-5"},
	{"deepseek", "deepseek-v4-flash", "deepseek/deepseek-v4-flash"},
	{"openrouter", "openrouter/anthropic/claude-sonnet-5.5", "anthropic/claude-sonnet-5.5"},
	// Bedrock's dotted, regional, versioned ids.
	{"bedrock", "us.anthropic.claude-opus-4-6-v1", "anthropic/claude-opus-4.6"},
	{"bedrock", "anthropic.claude-sonnet-4-5-20250929-v1:0", "anthropic/claude-sonnet-4.5"},
	{"bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0", "anthropic/claude-3.5-sonnet"},
	// The alias table: retired and host-specific names.
	{"anthropic-direct", "claude-3-5-sonnet-20240620", "anthropic/claude-3.5-sonnet"},
	{"anthropic-direct", "claude-3-5-haiku-20241022", "anthropic/claude-3.5-haiku"},
	{"anthropic-direct", "claude-3-7-sonnet-20250219", "anthropic/claude-3.7-sonnet"},
	{"anthropic-direct", "claude-3-opus-20240229", "anthropic/claude-3-opus"},
	{"anthropic-direct", "claude-3-haiku-20240307", "anthropic/claude-3-haiku"},
	{"anthropic-direct", "claude-sonnet-4-20250514", "anthropic/claude-sonnet-4"},
	{"anthropic-direct", "claude-4-opus-20250514", "anthropic/claude-opus-4"},
	{"together", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "meta-llama/llama-3.3-70b-instruct"},
	{"groq", "llama-3.3-70b-versatile", "meta-llama/llama-3.3-70b-instruct"},
	{"groq", "llama-3.1-8b-instant", "meta-llama/llama-3.1-8b-instruct"},
	{"fireworks", "accounts/fireworks/models/llama-v3p1-70b-instruct", "meta-llama/llama-3.1-70b-instruct"},
	{"deepinfra", "meta-llama/Meta-Llama-3.1-70B-Instruct", "meta-llama/llama-3.1-70b-instruct"},
	{"huggingface", "deepseek-ai/DeepSeek-R1", "deepseek/deepseek-r1"},
	{"huggingface", "Qwen/Qwen2.5-72B-Instruct", "qwen/qwen-2.5-72b-instruct"},
	{"huggingface", "moonshotai/Kimi-K2-Instruct", "moonshotai/kimi-k2"},
	{"together", "mistralai/Mixtral-8x7B-Instruct-v0.1", "mistralai/mixtral-8x7b-instruct"},
	{"gemini", "gemini-1.5-pro", "google/gemini-pro-1.5"},
	// Nothing identifies the vendor: the provider names it, so the id stays
	// unique and is never merged with another vendor's model.
	{"together", "acme-model-1", "together/acme-model-1"},
}

func TestAliasTable(t *testing.T) {
	if len(knownAliasPairs) < 30 {
		t.Fatalf("only %d known pairs; the contract asks for 30", len(knownAliasPairs))
	}
	for _, p := range knownAliasPairs {
		if got := Canonical(p.provider, p.in); got != p.want {
			t.Errorf("Canonical(%q, %q) = %q, want %q", p.provider, p.in, got, p.want)
		}
	}

	// Every alias names a lowercased vendor/model that is its own canonical
	// id, so the table cannot point at a name Canonical would rewrite again.
	table := aliasTable()
	if len(table) < 20 {
		t.Fatalf("aliases.json holds %d aliases", len(table))
	}
	for from, to := range table {
		if from != strings.ToLower(from) || to != strings.ToLower(to) || strings.Count(to, "/") != 1 {
			t.Errorf("alias %q -> %q is not lowercased vendor/model", from, to)
		}
		if again := Canonical("any", to); again != to {
			t.Errorf("alias target %q is not canonical: Canonical gives %q", to, again)
		}
	}
}

func TestCanonical(t *testing.T) {
	// Idempotent: a canonical id maps to itself.
	for _, p := range knownAliasPairs {
		if again := Canonical("any", p.want); again != p.want {
			t.Errorf("Canonical(%q) = %q, want it unchanged", p.want, again)
		}
	}
	// Every bundled OpenRouter id is its own canonical id once its variant
	// is dropped: the bundled list is the naming every other source maps to.
	for id := range bundledOpenRouterIDs().ids {
		if got := Canonical("openrouter", id); got != id {
			t.Errorf("Canonical(openrouter, %q) = %q", id, got)
		}
	}
	// The store gives every model its canonical id, and a price card's own
	// canonical id wins over the derived one.
	s := NewStore()
	s.Put(&Snapshot{ProviderID: "direct", CatalogSource: SourcePriceCard, Models: map[string]*Model{
		"claude-haiku-4-5-20251001": {Prices: Prices{Prompt: 1e-6, Completion: 5e-6}},
		"house-model":               {Canonical: "Anthropic/Claude-Opus-4.8", Prices: Prices{Prompt: 1e-6}},
	}})
	cat := s.Load()
	if m, _ := cat.Get("direct", "claude-haiku-4-5-20251001"); m.Canonical != "anthropic/claude-haiku-4.5" {
		t.Errorf("derived canonical = %q", m.Canonical)
	}
	if m, _ := cat.Get("direct", "house-model"); m.Canonical != "anthropic/claude-opus-4.8" {
		t.Errorf("card canonical = %q, want the card's, lowercased", m.Canonical)
	}
}

// TestCanonicalModelsMergeListings: listings of one canonical model become
// one model whose endpoints are every source's; a listing whose endpoints
// were never read is one endpoint priced from its own card; unpriced and
// stale-priced listings offer nothing.
func TestCanonicalModelsMergeListings(t *testing.T) {
	s := NewStore()
	mustConfigure(t, s, Options{UpstreamFeeBps: map[string]int{"or": 550, "card": 0, "plain": 0}})
	s.Put(&Snapshot{ProviderID: "or", CatalogSource: SourceOpenRouter, Models: map[string]*Model{
		"anthropic/claude-haiku-4.5": {Name: "Claude Haiku 4.5", ContextLength: 200000,
			Prices: Prices{Prompt: 1e-6, Completion: 5e-6}},
		"anthropic/claude-haiku-4.5:batch": {Prices: Prices{Prompt: 5e-7, Completion: 25e-7}},
	}})
	s.Put(&Snapshot{ProviderID: "card", CatalogSource: SourcePriceCard, Models: map[string]*Model{
		"claude-haiku-4-5-20251001": {Prices: Prices{Prompt: 1e-6, Completion: 5e-6}, Endpoints: []Endpoint{{
			ID: EndpointID(SourcePriceCard, "anthropic", "claude-haiku-4-5-20251001"), UpstreamProvider: "anthropic",
			Tag: "anthropic", Prices: Prices{Prompt: 1e-6, Completion: 5e-6}}}},
		// A stale card's listing offers nothing, even an endpoint it still carries.
		"claude-opus-4-8": {StalePrice: true, Prices: Prices{Prompt: 5e-6}, Endpoints: []Endpoint{{
			ID: EndpointID(SourcePriceCard, "anthropic", "claude-opus-4-8"), Prices: Prices{Prompt: 5e-6}}}},
	}})
	s.Put(&Snapshot{ProviderID: "plain", CatalogSource: SourceOpenAICompatible, Models: map[string]*Model{
		"claude-haiku-4-5": {Unpriced: true},
	}})

	cat := s.Load()
	cm, ok := cat.CanonicalModel("anthropic/claude-haiku-4.5")
	if !ok {
		t.Fatal("anthropic/claude-haiku-4.5 is not a canonical model")
	}
	if cm.Name != "Claude Haiku 4.5" || cm.ContextLength != 200000 || len(cm.Listings) != 4 {
		t.Fatalf("merged = name %q context %d listings %d", cm.Name, cm.ContextLength, len(cm.Listings))
	}
	byID := map[string]Endpoint{}
	for _, e := range cm.Endpoints {
		byID[e.ID] = e
	}
	if len(byID) != 3 {
		t.Fatalf("endpoints = %+v; want the two OpenRouter listings and the card's", cm.Endpoints)
	}
	or := byID[EndpointID(SourceOpenRouter, "", "anthropic/claude-haiku-4.5")]
	if or.Provider != "or" || or.Source != SourceOpenRouter || or.UpstreamFeeBps != 550 || or.UpstreamProvider != "" ||
		or.Prices.Prompt != 1e-6 || or.ContextLength != 200000 {
		t.Errorf("synthesized OpenRouter endpoint = %+v", or)
	}
	card := byID[EndpointID(SourcePriceCard, "anthropic", "claude-haiku-4-5-20251001")]
	if card.Provider != "card" || card.Source != SourcePriceCard || card.UpstreamFeeBps != 0 || card.UpstreamProvider != "anthropic" {
		t.Errorf("card endpoint = %+v", card)
	}
	if opus, ok := cat.CanonicalModel("anthropic/claude-opus-4.8"); !ok || len(opus.Endpoints) != 0 {
		t.Errorf("a stale-priced listing offers endpoints: %+v", opus)
	}
	// The merge is computed once per catalog version.
	if again, _ := cat.CanonicalModel("anthropic/claude-haiku-4.5"); again != cm {
		t.Error("the merged view is rebuilt for the same catalog version")
	}
	if n := len(cat.CanonicalModels()); n != 2 {
		t.Errorf("canonical models = %d, want 2", n)
	}
}

// TestSellableAndServable: Has, the managed resolver's catalog check, offers
// only models with a current price; Servable follows the catalog states.
func TestSellableAndServable(t *testing.T) {
	s := NewStore()
	s.Put(&Snapshot{ProviderID: "p", Models: map[string]*Model{
		"fixed":   {Prices: Prices{Prompt: 1e-6}},
		"dynamic": {DynamicPrice: true},
		"none":    {Unpriced: true},
		"stale":   {StalePrice: true, Prices: Prices{Prompt: 1e-6}},
	}})
	cat := s.Load()
	for id, want := range map[string]bool{"fixed": true, "dynamic": true, "none": false, "stale": false, "missing": false} {
		if got := cat.Has("p", id); got != want {
			t.Errorf("Has(%s) = %v, want %v", id, got, want)
		}
	}
	if m, _ := cat.Get("p", "stale"); m.Priced() {
		t.Error("a stale price card counts as priced")
	}
	if _, ok := s.PriceCard("p", "stale", ""); ok {
		t.Error("a stale price card prices a call")
	}

	now := time.Date(2026, 10, 16, 0, 0, 0, 0, time.UTC)
	for _, c := range []struct {
		m    Model
		want bool
	}{
		{Model{State: StateActive}, true},
		{Model{State: StateDegraded}, true},
		{Model{State: StateBlocked}, false},
		{Model{State: StateDeprecated, ExpirationDate: "2026-10-20"}, true},
		{Model{State: StateDeprecated, ExpirationDate: "2026-10-16"}, false},
		{Model{State: StateDeprecated, ExpirationDate: "2026-09-01"}, false},
	} {
		if got := c.m.Servable(now); got != c.want {
			t.Errorf("Servable(%s, expires %q) = %v, want %v", c.m.State, c.m.ExpirationDate, got, c.want)
		}
	}
}

// TestPriceCardSourceOfACardSnapshot: a price-card source's card names its
// source, so cost_source says price_card.
func TestPriceCardSourceOfACardSnapshot(t *testing.T) {
	s := NewStore()
	s.Put(&Snapshot{ProviderID: "card", CatalogSource: SourcePriceCard, Models: map[string]*Model{
		"m": {Prices: Prices{Prompt: 1e-6}}}})
	s.Put(&Snapshot{ProviderID: "or", CatalogSource: SourceOpenRouter, Models: map[string]*Model{
		"m": {Prices: Prices{Prompt: 1e-6}}}})
	if card, _ := s.PriceCard("card", "m", ""); card.Source != "price_card" {
		t.Errorf("card source = %q", card.Source)
	}
	if card, _ := s.PriceCard("or", "m", ""); card.Source != "catalog" {
		t.Errorf("openrouter source = %q", card.Source)
	}
	b, _ := json.Marshal(s.Load().CanonicalModels())
	if !strings.Contains(string(b), `"id":"`) {
		t.Errorf("canonical models marshal to %s", b)
	}
}
