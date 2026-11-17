package catalog

import (
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func TestBundledEndpointsParse(t *testing.T) {
	b, err := BundledEndpoints()
	if err != nil {
		t.Fatal(err)
	}
	n := 0
	for _, eps := range b.ByModel {
		n += len(eps)
	}
	if n <= 1000 {
		t.Fatalf("bundled endpoints = %d, want more than 1,000", n)
	}
	if b.FetchedAt.IsZero() || len(b.ZDR) == 0 {
		t.Fatalf("bundled endpoints lack their fetch time or ZDR list (fetched_at %v, %d ZDR)", b.FetchedAt, len(b.ZDR))
	}
}

func TestEndpointCarriesSourceAndFee(t *testing.T) {
	sources := []Source{{ProviderID: "openrouter", Type: "openrouter", URL: "http://upstream.invalid/api/v1/models"}}
	store := NewStore()
	Seed(store, sources)

	// The default: an OpenRouter provider with no fee configured, read
	// through config exactly as the wiring reads it.
	cfg := config.DefaultConfig()
	cfg.Providers["openrouter"] = config.ProviderConfig{Type: "openrouter", Managed: true}
	if err := store.Configure(OptionsFromConfig(cfg, sources)); err != nil {
		t.Fatal(err)
	}
	assertEveryEndpoint(t, store, SourceOpenRouter, 550)

	// A configured upstream_fee_bps: 800 is reflected on every endpoint.
	fee := 800
	cfg.Providers["openrouter"] = config.ProviderConfig{Type: "openrouter", Managed: true, UpstreamFeeBps: &fee}
	if err := store.Configure(OptionsFromConfig(cfg, sources)); err != nil {
		t.Fatal(err)
	}
	assertEveryEndpoint(t, store, SourceOpenRouter, 800)

	// The fee is config, not catalog content: the version does not change.
	before := store.Load().ID()
	zero := 0
	cfg.Providers["openrouter"] = config.ProviderConfig{Type: "openrouter", Managed: true, UpstreamFeeBps: &zero}
	if err := store.Configure(OptionsFromConfig(cfg, sources)); err != nil {
		t.Fatal(err)
	}
	assertEveryEndpoint(t, store, SourceOpenRouter, 0)
	if store.Load().ID() != before {
		t.Fatal("changing the upstream fee must not change the catalog's snapshot id")
	}
}

func assertEveryEndpoint(t *testing.T, store *Store, source string, fee int) {
	t.Helper()
	n := 0
	for _, snap := range store.Load().Snapshots() {
		for _, m := range snap.Models {
			for _, e := range m.Endpoints {
				n++
				if e.Source != source || e.UpstreamFeeBps != fee || e.Provider != snap.ProviderID {
					t.Fatalf("endpoint %s: source %q fee %d provider %q, want %q %d %q",
						e.ID, e.Source, e.UpstreamFeeBps, e.Provider, source, fee, snap.ProviderID)
				}
				if !strings.HasPrefix(e.ID, source+":"+e.Tag+":") || !strings.HasSuffix(e.ID, ":"+m.ID) {
					t.Fatalf("endpoint id %q is not <source>:<tag>:<model>", e.ID)
				}
			}
		}
	}
	if n <= 1000 {
		t.Fatalf("checked %d endpoints, want the bundled ones", n)
	}
}

func TestRegionsFromTag(t *testing.T) {
	for tag, want := range map[string][]string{
		"anthropic":                        nil,
		"google-vertex/us":                 {"us"},
		"google-vertex/europe":             {"europe"},
		"google-vertex/global":             {"global"},
		"amazon-bedrock/us-east-1":         {"us-east-1"},
		"google-vertex/us-east5":           {"us-east5"},
		"google-vertex/us-central1":        {"us-central1"},
		"azure/swedencentral":              {"swedencentral"},
		"azure/eastus":                     {"eastus"},
		"deepinfra/fp8":                    nil,
		"together/fp4/us":                  {"us"},
		"amazon-bedrock/fp8/us-east-1":     {"us-east-1"},
		"novita/nvfp4/eu":                  {"eu"},
		"openai/flex":                      nil,
		"openai/priority":                  nil,
		"groq/fast":                        nil,
		"anthropic/zdr/us":                 {"us"},
		"google-ai-studio/global/priority": {"global"},
		"cerebras/ultrafast":               nil,
		"baseten/highspeed":                nil,
	} {
		if got := RegionsFromTag(tag); !reflect.DeepEqual(got, want) {
			t.Errorf("RegionsFromTag(%q) = %v, want %v", tag, got, want)
		}
	}
}

func TestRegionsFromTagOverBundledTags(t *testing.T) {
	// Every variant the bundled endpoints use is either a known
	// non-region or reads as a region; nothing else slips through.
	b, err := BundledEndpoints()
	if err != nil {
		t.Fatal(err)
	}
	regions := map[string]bool{}
	for _, eps := range b.ByModel {
		for _, e := range eps {
			for _, r := range e.Regions {
				regions[r] = true
			}
		}
	}
	for r := range regions {
		if notRegions[r] {
			t.Errorf("%q is a quantization or service tier, not a region", r)
		}
	}
	if !regions["us"] || !regions["eu"] {
		t.Errorf("bundled regions %v miss us or eu", keys(regions))
	}
}

func TestParseOpenRouterEndpoints(t *testing.T) {
	eps, err := ParseOpenRouterEndpoints("anthropic/claude-sonnet-5.5", fixture(t, "endpoints_claude.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(eps) != 4 {
		t.Fatalf("got %d endpoints, want 4", len(eps))
	}
	direct := eps[0]
	if direct.ID != "openrouter:anthropic:anthropic/claude-sonnet-5.5" || direct.UpstreamProvider != "anthropic" ||
		direct.Quantization != "" || direct.MaxCompletion != 64000 || *direct.UptimeLast30m != 99.6 ||
		*direct.LatencyP50Ms != 640 || *direct.LatencyP90Ms != 1510 || *direct.ThroughputP50 != 71.4 ||
		direct.Prices.CacheWrite != 3.75e-6 || direct.DataCollection != DataCollectionUnknown || direct.ZDR != nil {
		t.Errorf("direct endpoint = %+v", direct)
	}
	vertex := eps[1]
	if vertex.UpstreamProvider != "google-vertex" || !reflect.DeepEqual(vertex.Regions, []string{"europe"}) ||
		vertex.Status != -2 || vertex.MaxPrompt != 900000 || !vertex.SupportsImplicitCaching || vertex.UptimeLast30m != nil {
		t.Errorf("vertex endpoint = %+v", vertex)
	}
	bedrock := eps[2]
	if bedrock.Quantization != "fp8" || !reflect.DeepEqual(bedrock.Regions, []string{"us-east-1"}) || bedrock.UpstreamProvider != "amazon-bedrock" {
		t.Errorf("bedrock endpoint = %+v", bedrock)
	}
	if !eps[3].DynamicPrice || eps[3].priced() {
		t.Errorf("a negative endpoint price must be dynamic: %+v", eps[3])
	}
	if _, err := ParseOpenRouterEndpoints("other/model", fixture(t, "endpoints_claude.json")); err == nil {
		t.Error("a response for another model must be refused")
	}
}

func TestZDRMerged(t *testing.T) {
	zdr, err := ParseZDR(fixture(t, "zdr.json"))
	if err != nil {
		t.Fatal(err)
	}
	eps, _ := ParseOpenRouterEndpoints("anthropic/claude-sonnet-5.5", fixture(t, "endpoints_claude.json"))
	models := fixtureModels(t)
	models["anthropic/claude-sonnet-5.5"].Endpoints = eps

	store := NewStore()
	store.Put(&Snapshot{ProviderID: "or", CatalogSource: SourceOpenRouter, Models: models})
	m, _ := store.Load().Get("or", "anthropic/claude-sonnet-5.5")
	for _, e := range m.Endpoints {
		if e.ZDR != nil || e.DataCollection != DataCollectionUnknown {
			t.Fatalf("with no ZDR list read, %s must be unknown: zdr %v, %s", e.ID, e.ZDR, e.DataCollection)
		}
	}

	models = fixtureModels(t)
	models["anthropic/claude-sonnet-5.5"].Endpoints = eps
	store.Put(&Snapshot{ProviderID: "or", CatalogSource: SourceOpenRouter, Models: models, ZDR: zdr})
	m, _ = store.Load().Get("or", "anthropic/claude-sonnet-5.5")
	for _, e := range m.Endpoints {
		wantZDR := e.Tag == "anthropic"
		if e.ZDR == nil || *e.ZDR != wantZDR {
			t.Fatalf("%s: zdr = %v, want %v", e.ID, e.ZDR, wantZDR)
		}
		if wantDC := map[bool]string{true: DataCollectionDeny, false: DataCollectionUnknown}[wantZDR]; e.DataCollection != wantDC {
			t.Fatalf("%s: data collection %q, want %q", e.ID, e.DataCollection, wantDC)
		}
	}
	if _, err := ParseZDR([]byte(`{"error":"x"}`)); err == nil {
		t.Error("a ZDR response with no data must be an error, not an empty list that marks everything non-ZDR")
	}
}

func TestEndpointsDocumentShape(t *testing.T) {
	eps, _ := ParseOpenRouterEndpoints("anthropic/claude-sonnet-5.5", fixture(t, "endpoints_claude.json"))
	models := fixtureModels(t)
	models["anthropic/claude-sonnet-5.5"].Endpoints = eps
	zdr, _ := ParseZDR(fixture(t, "zdr.json"))
	store := NewStore()
	store.Put(&Snapshot{ProviderID: "openrouter", CatalogSource: SourceOpenRouter, Models: models, ZDR: zdr})
	m, _ := store.Load().Get("openrouter", "anthropic/claude-sonnet-5.5")

	admin := m.EndpointsDocument(true)
	if len(admin.Data.Endpoints) != 3 {
		t.Fatalf("got %d endpoints, want 3: the dynamic-price one has no price to show", len(admin.Data.Endpoints))
	}
	first := admin.Data.Endpoints[0]
	if first.Pricing.Prompt != "0.000003" || first.Pricing.InputCacheWrite != "0.00000375" || first.Pricing.Request != "0" {
		t.Errorf("pricing = %+v, want plain decimal strings", first.Pricing)
	}
	if first.UpstreamFeeBps == nil || *first.UpstreamFeeBps != 550 || first.Source != SourceOpenRouter ||
		first.HealthSource != "upstream" || first.ZDR == nil || !*first.ZDR || first.DataCollection != DataCollectionDeny {
		t.Errorf("admin endpoint = %+v", first)
	}
	if r := admin.Data.Endpoints[1].Region; r == nil || *r != "europe" {
		t.Errorf("region = %v, want europe", r)
	}
	if body := mustJSON(t, m.EndpointsDocument(false)); strings.Contains(body, "upstream_fee_bps") {
		t.Errorf("the developer document must not carry the upstream fee: %s", body)
	}
}
