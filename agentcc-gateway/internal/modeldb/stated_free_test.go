package modeldb

import (
	"context"
	"maps"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestStatedFreeLayers(t *testing.T) {
	var target atomic.Pointer[ModelDB]
	c := NewCatalogue(&target, nil)
	check := func(wantModels map[string]*ModelInfo, overrides map[string]ModelOverride, free, missing []string) {
		t.Helper()
		db := target.Load()
		plain := New(maps.Clone(wantModels), overrides)
		if !reflect.DeepEqual(db.models, plain.models) || !reflect.DeepEqual(db.byProvider, plain.byProvider) || db.Count() != c.Info().Entries {
			t.Fatal("stated rates changed the general catalogue")
		}
		for _, key := range free {
			if !db.StatedFree(key, "", "") {
				t.Errorf("%s: want stated free", key)
			}
		}
		for _, key := range missing {
			if db.StatedFree(key, "", "") {
				t.Errorf("%s: unexpected free classification", key)
			}
		}
	}
	check(BundledModels, nil, []string{"ollama/llama3"}, []string{"unlisted", "perplexity/pplx-7b-online"})
	data := []byte(`{"test-free":{"mode":"embedding","input_cost_per_token":0,"output_cost_per_token":0},"missing":{},"one":{"input_cost_per_token":0},"null":{"input_cost_per_token":null,"output_cost_per_token":0},"negative":{"input_cost_per_token":0,"output_cost_per_token":0,"cache_read_input_token_cost":-1},"other":{"input_cost_per_token":0,"output_cost_per_token":0,"input_cost_per_image":1},"ollama/llama3":{"input_cost_per_token":1,"output_cost_per_token":2},"gpt-4o-mini":{"input_cost_per_token":0,"output_cost_per_token":0}}`)
	hints := emptyDeclarations(bundledZero)
	fetched, err := loadLiteLLM(data, true, hints)
	if err != nil {
		t.Fatal(err)
	}
	plain, err := parseLiteLLM(data, true)
	if err != nil || !reflect.DeepEqual(fetched, plain) {
		t.Fatal("price table differs with free declarations")
	}
	merged := maps.Clone(BundledModels)
	maps.Copy(merged, plain)
	c.overlay(fetched, hints, "fixture")
	missing := []string{"missing", "one", "null", "negative", "other", "ollama/llama3", "gpt-4o-mini"}
	check(merged, nil, []string{"test-free", "ollama/codegeex4"}, missing)
	overrides := map[string]ModelOverride{"test-free": {}}
	c.SetOverrides(overrides)
	check(merged, overrides, []string{"ollama/codegeex4", "test-free"}, missing)
	c.SetOverrides(nil)
	check(merged, nil, []string{"test-free"}, missing)
	c.Overlay(nil, "replacement")
	check(BundledModels, nil, []string{"ollama/llama3"}, []string{"test-free"})
}

// Only an accepted fetch can change declarations. Free-only entries must not
// count toward the existing minimum size or appear in the public catalogue.
func TestStatedFreeStrictSync(t *testing.T) {
	body := strings.TrimSuffix(catalogueJSON(60, 1e-6), "}") + `,"local/free":{"mode":"embedding","input_cost_per_token":0,"output_cost_per_token":0},"absent":{"mode":"chat"}}`
	f := newFakeCatalogue(t, body)
	s, ptr, c := newTestSyncer(f.URL, 100)
	s.syncOnce(context.Background(), time.Second)
	if c.Info().Fetched != 60 || !ptr.Load().StatedFree("free", "local", "") || ptr.Load().StatedFree("absent", "", "") {
		t.Fatalf("unexpected fetched catalogue: %+v", c.Info())
	}
	if _, ok := ptr.Load().Get("local/free"); ok {
		t.Fatal("free key entered the general model map")
	}
	f.body.Store(`{"new-free":{"input_cost_per_token":0,"output_cost_per_token":0}}`)
	s.syncOnce(context.Background(), time.Second)
	if c.Info().LastError == "" || ptr.Load().StatedFree("new-free", "", "") || !ptr.Load().StatedFree("local/free", "", "") {
		t.Fatal("rejected download changed free declarations")
	}
}

// Preserve the parser's declaration type without coupling these black-box checks
// to its internal representation.
func emptyDeclarations[V any](_ map[string]V) map[string]V { return make(map[string]V) }

func TestExactFreeDeclaration(t *testing.T) {
	data := []byte(`{
		"acme-chat":{"input_cost_per_token":0,"output_cost_per_token":0},
		"reseller/acme-chat":{"input_cost_per_token":0,"output_cost_per_token":1},
		"per-request":{"input_cost_per_token":0,"output_cost_per_token":0,"input_cost_per_request":0.01},
		"per-request-zero":{"input_cost_per_token":0,"output_cost_per_token":0,"input_cost_per_request":0},
		"cache-null":{"input_cost_per_token":0,"output_cost_per_token":0,"cache_read_input_token_cost":null},
		"other-price":{"input_cost_per_token":0,"output_cost_per_token":0,"cache_creation_input_audio_token_cost":1}
	}`)
	declared := emptyDeclarations(bundledZero)
	models, err := loadLiteLLM(data, true, declared)
	if err != nil {
		t.Fatal(err)
	}
	db := newWithZero(models, nil, declared)
	for _, tc := range []struct {
		key, provider string
		free          bool
	}{
		{"acme-chat", "", true}, {"reseller/acme-chat", "reseller", false},
		{"acme-chat", "reseller", false}, {"unlisted/acme-chat", "unlisted", false},
		{"per-request", "", false}, {"per-request-zero", "", true}, {"cache-null", "", false}, {"other-price", "", false},
	} {
		t.Run(tc.key+"-"+tc.provider, func(t *testing.T) {
			if got := db.StatedFree(tc.key, tc.provider, ""); got != tc.free {
				t.Errorf("stated free = %t, want %t", got, tc.free)
			}
			if amount, ok := db.CalculateCost(db.PriceKey(tc.key, tc.provider, ""), 1000, 500, CostOptions{}); ok || amount != 0 {
				t.Errorf("monetary result changed: (%v,%v)", amount, ok)
			}
		})
	}
}

func TestFreeDeclarationWithOverride(t *testing.T) {
	zero, one, limit := 0.0, 1.0, 1024
	for _, tc := range []struct {
		name     string
		override ModelOverride
		want     bool
	}{
		{"limits-only", ModelOverride{MaxInputTokens: &limit}, true},
		{"mode-only", ModelOverride{Mode: "image_generation"}, true},
		{"zero-prices", ModelOverride{Pricing: &PricingOverride{InputPerToken: &zero, OutputPerToken: &zero}}, true},
		{"nonzero-character", ModelOverride{Pricing: &PricingOverride{InputPerToken: &zero, OutputPerToken: &zero, InputPerCharacter: &one}}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			db := New(BundledModels, map[string]ModelOverride{"ollama/llama3": tc.override})
			if got := db.StatedFree("ollama/llama3", "ollama", ""); got != tc.want {
				t.Errorf("stated free = %t, want %t", got, tc.want)
			}
		})
	}
}
