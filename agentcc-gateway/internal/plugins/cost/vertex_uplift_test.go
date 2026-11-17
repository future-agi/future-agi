package cost

import (
	"context"
	"math"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// vertexPlugin is a cost plugin wired the way the server wires it: provider
// "vertex" is Gemini on Vertex AI and "google" is the Gemini API, and both
// count their cached tokens inside the prompt.
func vertexPlugin(db func() *modeldb.ModelDB, store *tenant.Store, aliases map[string]float64, at time.Time) *Plugin {
	p := New(true, db, store, aliases)
	p.SetPriceTables(func(_, id string) string {
		switch id {
		case "vertex":
			return "vertex_ai"
		case "google":
			return "gemini"
		}
		return ""
	})
	p.SetCachedTokensInPrompt(func(_, id string) bool { return id == "vertex" || id == "google" })
	if !at.IsZero() {
		p.now = clockAt(at)
	}
	return p
}

// vertexChat is a chat request served by provider; location, when set, is the
// Vertex AI location the server recorded for the instance that served it.
func vertexChat(model, provider, location string, usage *models.Usage) *models.RequestContext {
	rc := newRC(model, usage.PromptTokens, usage.CompletionTokens)
	rc.Response.Usage = usage
	rc.EndpointType = "chat"
	rc.Provider = provider
	if location != "" {
		rc.Metadata[servedVertexLocationKey] = location
	}
	return rc
}

func wantNoUplift(t *testing.T, rc *models.RequestContext) {
	t.Helper()
	for _, key := range []string{vertexLocationKey, regionalUpliftKey} {
		if v, set := rc.Metadata[key]; set {
			t.Errorf("%s = %q, want it absent", key, v)
		}
	}
}

func wantUplift(t *testing.T, rc *models.RequestContext, location, factor string) {
	t.Helper()
	if got := rc.Metadata[vertexLocationKey]; got != location {
		t.Errorf("%s = %q, want %q", vertexLocationKey, got, location)
	}
	if got := rc.Metadata[regionalUpliftKey]; got != factor {
		t.Errorf("%s = %q, want %q", regionalUpliftKey, got, factor)
	}
}

func costOf(t *testing.T, rc *models.RequestContext) float64 {
	t.Helper()
	got, err := strconv.ParseFloat(rc.Metadata["cost"], 64)
	if err != nil {
		t.Fatalf("cost = %q, want a number", rc.Metadata["cost"])
	}
	return got
}

// Google's non-global prices for Gemini 3.8 Flash, per million tokens: 2026
// input 0.825, cached 0.0825, output 4.125; 2027 input 1.65, cached 0.165.
func TestVertexUpliftOnEveryTokenPrice(t *testing.T) {
	rows := []struct {
		name                       string
		at                         time.Time
		prompt, completion, cached int
		want                       string
	}{
		{"t27 input", t27, 1_000_000, 0, 0, "1.650000"},
		{"t27 cached", t27, 1_000_000, 0, 1_000_000, "0.165000"},
		{"t27 output", t27, 0, 1_000_000, 0, "8.250000"},
		{"t27 mixed", t27, 1_000_000, 100_000, 400_000, "1.881000"},
		{"t26 input", t26, 1_000_000, 0, 0, "0.825000"},
		{"t26 cached", t26, 1_000_000, 0, 1_000_000, "0.082500"},
		{"t26 output", t26, 0, 1_000_000, 0, "4.125000"},
		{"t26 mixed", t26, 1_000_000, 100_000, 400_000, "0.940500"},
	}
	for _, row := range rows {
		t.Run(row.name, func(t *testing.T) {
			p := vertexPlugin(bundledDB(), nil, nil, row.at)
			usage := usageWithCached(row.prompt, row.completion, row.cached)
			if row.cached == 0 {
				usage.PromptTokensDetails = nil
			}
			rc := vertexChat("gemini-3.8-flash", "vertex", "us-central1", usage)
			p.ProcessResponse(context.Background(), rc)
			if got := rc.Metadata["cost"]; got != row.want {
				t.Errorf("cost = %q, want %q", got, row.want)
			}
			wantUplift(t, rc, "us-central1", "1.1")
			if row.name == "t27 mixed" {
				if got := rc.Metadata[cachedTokensKey]; got != "400000" {
					t.Errorf("%s = %q, want 400000", cachedTokensKey, got)
				}
			}
		})
	}
}

func TestVertexUpliftSkipsGlobalAndUnknown(t *testing.T) {
	const want = "1.710000"
	run := func(t *testing.T, provider string, prepare func(rc *models.RequestContext)) *models.RequestContext {
		t.Helper()
		p := vertexPlugin(bundledDB(), nil, nil, t27)
		rc := vertexChat("gemini-3.8-flash", provider, "", usageWithCached(1_000_000, 100_000, 400_000))
		prepare(rc)
		p.ProcessResponse(context.Background(), rc)
		return rc
	}

	t.Run("global", func(t *testing.T) {
		rc := run(t, "vertex", func(rc *models.RequestContext) { rc.Metadata[servedVertexLocationKey] = "global" })
		if got := rc.Metadata["cost"]; got != want {
			t.Errorf("cost = %q, want %q", got, want)
		}
		wantNoUplift(t, rc)
	})
	t.Run("empty", func(t *testing.T) {
		rc := run(t, "vertex", func(rc *models.RequestContext) { rc.Metadata[servedVertexLocationKey] = "" })
		if got := rc.Metadata["cost"]; got != want {
			t.Errorf("cost = %q, want %q", got, want)
		}
		wantNoUplift(t, rc)
	})
	t.Run("none", func(t *testing.T) {
		rc := run(t, "vertex", func(*models.RequestContext) {})
		if got := rc.Metadata["cost"]; got != want {
			t.Errorf("cost = %q, want %q", got, want)
		}
		wantNoUplift(t, rc)
	})
	t.Run("google", func(t *testing.T) {
		rc := run(t, "google", func(rc *models.RequestContext) { rc.Metadata[servedVertexLocationKey] = "us-central1" })
		if got := rc.Metadata["cost"]; got != want {
			t.Errorf("cost = %q, want %q", got, want)
		}
		wantNoUplift(t, rc)
	})
	t.Run("no provider", func(t *testing.T) {
		rc := run(t, "", func(rc *models.RequestContext) { rc.Metadata[servedVertexLocationKey] = "us-central1" })
		wantNoUplift(t, rc)
	})
}

func TestVertexUpliftReadsTheVertexEntry(t *testing.T) {
	usage := func() *models.Usage { return usageWithCached(1_000_000, 100_000, 400_000) }
	price := func(model, location string) *models.RequestContext {
		p := vertexPlugin(bundledDB(), nil, nil, t27)
		rc := vertexChat(model, "vertex", location, usage())
		p.ProcessResponse(context.Background(), rc)
		return rc
	}

	for model, want := range map[string]string{
		"gemini-3.8-flash":           "1.881000",
		"vertex_ai/gemini-3.8-flash": "1.881000",
		"gemini-4-argon":             "2.464000",
	} {
		t.Run(model, func(t *testing.T) {
			rc := price(model, "us-central1")
			if got := rc.Metadata["cost"]; got != want {
				t.Errorf("cost = %q, want %q", got, want)
			}
			wantUplift(t, rc, "us-central1", "1.1")
		})
	}

	t.Run("gemini-3-pro-preview", func(t *testing.T) {
		rc := price("gemini-3-pro-preview", "us-central1")
		wantNoUplift(t, rc)
		global := price("gemini-3-pro-preview", "global")
		if rc.Metadata["cost"] == "" || rc.Metadata["cost"] != global.Metadata["cost"] {
			t.Errorf("cost = %q, want the global cost %q", rc.Metadata["cost"], global.Metadata["cost"])
		}
	})
}

func TestVertexUpliftBound(t *testing.T) {
	buf := logCapture(t)

	factors := map[string]float64{"a": 1.0, "b": 0.9, "c": 1.5, "d": 1.5000001, "e": 2, "f": math.NaN()}
	entries := map[string]*modeldb.ModelInfo{}
	for suffix, factor := range factors {
		bare := "m-" + suffix
		pricing := modeldb.PricingInfo{InputPerToken: 1e-6, CachedInputPerToken: 1e-7, OutputPerToken: 2e-6}
		entries[bare] = &modeldb.ModelInfo{ID: bare, Mode: modeldb.ModeChat, Pricing: pricing}
		regional := "vertex_ai/" + bare
		entries[regional] = &modeldb.ModelInfo{ID: regional, Mode: modeldb.ModeChat, Pricing: pricing, RegionalUplift: factor}
	}
	db := modeldb.New(entries, nil)
	p := vertexPlugin(func() *modeldb.ModelDB { return db }, nil, nil, time.Time{})

	for suffix := range factors {
		t.Run("m-"+suffix, func(t *testing.T) {
			for i := 0; i < 2; i++ {
				rc := vertexChat("m-"+suffix, "vertex", "us-central1", &models.Usage{PromptTokens: 1000, CompletionTokens: 1000, TotalTokens: 2000})
				p.ProcessResponse(context.Background(), rc)
				if suffix == "c" {
					if got := rc.Metadata["cost"]; got != "0.004500" {
						t.Errorf("cost = %q, want 0.004500", got)
					}
					wantUplift(t, rc, "us-central1", "1.5")
					continue
				}
				if got := rc.Metadata["cost"]; got != "0.003000" {
					t.Errorf("cost = %q, want 0.003000", got)
				}
				wantNoUplift(t, rc)
			}
		})
	}

	logs := buf.String()
	if n := strings.Count(logs, "outside (1, 1.5]"); n != 5 {
		t.Errorf("logged %d bound warnings, want 5 (one per rejected key)\n%s", n, logs)
	}
	for _, suffix := range []string{"a", "b", "d", "e", "f"} {
		if n := strings.Count(logs, "model=vertex_ai/m-"+suffix+" "); n != 1 {
			t.Errorf("warned %d times for vertex_ai/m-%s, want 1\n%s", n, suffix, logs)
		}
	}
	if strings.Contains(logs, "model=vertex_ai/m-c ") {
		t.Errorf("warned for the accepted factor 1.5\n%s", logs)
	}
}

func TestVertexUpliftOnTheRetry(t *testing.T) {
	p := vertexPlugin(bundledDB(), nil, nil, t27)
	rc := vertexChat("gemini-3.8-flash", "vertex", "us-central1", usageWithCached(1_000_000, 100_000, 400_000))
	rc.ResolvedModel = "gemini-3.8-flash-unlisted"
	p.ProcessResponse(context.Background(), rc)
	if got := rc.Metadata["cost"]; got != "1.881000" {
		t.Errorf("cost = %q, want 1.881000", got)
	}
	wantUplift(t, rc, "us-central1", "1.1")
}

func TestVertexUpliftChatOnly(t *testing.T) {
	db := modeldb.New(map[string]*modeldb.ModelInfo{
		"vertex_ai/emb": {ID: "vertex_ai/emb", Mode: modeldb.ModeEmbedding, Pricing: modeldb.PricingInfo{InputPerToken: 1e-6}, RegionalUplift: 1.1},
		"vertex_ai/img": {ID: "vertex_ai/img", Mode: modeldb.ModeImageGeneration, Pricing: modeldb.PricingInfo{OutputPerImage: 0.04}, RegionalUplift: 1.1},
	}, nil)
	p := vertexPlugin(func() *modeldb.ModelDB { return db }, nil, nil, time.Time{})

	t.Run("embedding", func(t *testing.T) {
		rc := &models.RequestContext{
			Model:        "vertex_ai/emb",
			EndpointType: "embedding",
			Provider:     "vertex",
			EmbeddingResponse: &models.EmbeddingResponse{
				Object: "list",
				Model:  "vertex_ai/emb",
				Usage:  &models.EmbeddingUsage{PromptTokens: 1000, TotalTokens: 1000},
			},
			Metadata: map[string]string{servedVertexLocationKey: "us-central1"},
			Timings:  map[string]time.Duration{},
		}
		p.ProcessResponse(context.Background(), rc)
		if got := rc.Metadata["cost"]; got != "0.001000" {
			t.Errorf("cost = %q, want 0.001000", got)
		}
		wantNoUplift(t, rc)
	})
	t.Run("image", func(t *testing.T) {
		rc := &models.RequestContext{
			Model:         "vertex_ai/img",
			EndpointType:  "image",
			Provider:      "vertex",
			ImageResponse: &models.ImageResponse{Data: []models.ImageData{{URL: "a"}}},
			Metadata:      map[string]string{servedVertexLocationKey: "us-central1"},
			Timings:       map[string]time.Duration{},
		}
		p.ProcessResponse(context.Background(), rc)
		if got := rc.Metadata["cost"]; got != "0.040000" {
			t.Errorf("cost = %q, want 0.040000", got)
		}
		wantNoUplift(t, rc)
	})
}

func TestVertexUpliftNotOnOrgPrice(t *testing.T) {
	store := tenant.NewStore()
	store.Set("org-1", &tenant.OrgConfig{CostTracking: &tenant.CostTrackingConfig{
		Enabled:       true,
		CustomPricing: map[string]*tenant.CustomPricing{"gemini-3.8-flash": {InputPerMTok: 1.0, OutputPerMTok: 1.0}},
	}})
	p := vertexPlugin(bundledDB(), store, nil, t27)
	rc := vertexChat("gemini-3.8-flash", "vertex", "us-central1", &models.Usage{PromptTokens: 1_000_000, CompletionTokens: 100_000, TotalTokens: 1_100_000})
	rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
	p.ProcessResponse(context.Background(), rc)
	if got := rc.Metadata["cost"]; got != "1.100000" {
		t.Errorf("cost = %q, want 1.100000", got)
	}
	if got := rc.Metadata["cost_source"]; got != "org_custom" {
		t.Errorf("cost_source = %q, want org_custom", got)
	}
	wantNoUplift(t, rc)
}

func TestVertexUpliftWithAliasFactor(t *testing.T) {
	p := vertexPlugin(bundledDB(), nil, map[string]float64{"fast": 2}, t27)
	rc := vertexChat("gemini-3.8-flash", "vertex", "us-central1", usageWithCached(1_000_000, 100_000, 400_000))
	rc.Metadata["model_alias"] = "fast-1"
	p.ProcessResponse(context.Background(), rc)
	if got := rc.Metadata["cost"]; got != "3.762000" {
		t.Errorf("cost = %q, want 3.762000", got)
	}
}

func TestVertexUpliftLeavesOtherProvidersAlone(t *testing.T) {
	usage := func() *models.Usage {
		return &models.Usage{PromptTokens: 1_000_000, CompletionTokens: 100_000, TotalTokens: 1_100_000}
	}
	t.Run("openai at a Vertex location", func(t *testing.T) {
		p := vertexPlugin(bundledDB(), nil, nil, t27)
		with := vertexChat("gemini-3.8-flash", "openai", "us-central1", usage())
		p.ProcessResponse(context.Background(), with)
		without := vertexChat("gemini-3.8-flash", "openai", "", usage())
		p.ProcessResponse(context.Background(), without)
		if with.Metadata["cost"] == "" || with.Metadata["cost"] != without.Metadata["cost"] {
			t.Errorf("cost = %q, want the same as without a location (%q)", with.Metadata["cost"], without.Metadata["cost"])
		}
		wantNoUplift(t, with)
	})
	t.Run("reseller", func(t *testing.T) {
		p := vertexPlugin(bundledDB(), nil, nil, t27)
		p.SetProviderTypes(func(_, id string) string {
			if id == "di" {
				return "deepinfra"
			}
			return ""
		})
		rc := vertexChat("gemini-3.7-flash", "di", "us-central1", usage())
		p.ProcessResponse(context.Background(), rc)
		if got := rc.Metadata["cost"]; got != "1.125000" {
			t.Errorf("cost = %q, want 1.125000", got)
		}
		wantNoUplift(t, rc)
	})
}

// upliftRequest is a request the uplift prices; the same request at the
// global endpoint is what it is compared with.
type upliftRequest struct {
	name                       string
	model, resolved            string
	at                         time.Time
	prompt, completion, cached int
}

var upliftRequests = []upliftRequest{
	{"t27 input", "gemini-3.8-flash", "", t27, 1_000_000, 0, 0},
	{"t27 cached", "gemini-3.8-flash", "", t27, 1_000_000, 0, 1_000_000},
	{"t27 output", "gemini-3.8-flash", "", t27, 0, 1_000_000, 0},
	{"t27 mixed", "gemini-3.8-flash", "", t27, 1_000_000, 100_000, 400_000},
	{"t26 mixed", "gemini-3.8-flash", "", t26, 1_000_000, 100_000, 400_000},
	{"vertex key", "vertex_ai/gemini-3.8-flash", "", t27, 1_000_000, 100_000, 400_000},
	{"4-argon", "gemini-4-argon", "", t27, 1_000_000, 100_000, 400_000},
	{"retry", "gemini-3.8-flash", "gemini-3.8-flash-unlisted", t27, 1_000_000, 100_000, 400_000},
}

func (r upliftRequest) price(t *testing.T, location string) *models.RequestContext {
	t.Helper()
	usage := usageWithCached(r.prompt, r.completion, r.cached)
	if r.cached == 0 {
		usage.PromptTokensDetails = nil
	}
	rc := vertexChat(r.model, "vertex", location, usage)
	rc.ResolvedModel = r.resolved
	vertexPlugin(bundledDB(), nil, nil, r.at).ProcessResponse(context.Background(), rc)
	return rc
}

func TestVertexUpliftNeverLowersABill(t *testing.T) {
	for _, r := range upliftRequests {
		t.Run(r.name, func(t *testing.T) {
			regional := costOf(t, r.price(t, "us-central1"))
			global := costOf(t, r.price(t, "global"))
			if regional < global {
				t.Errorf("regional cost %v is below the global cost %v", regional, global)
			}
			// The recorded costs are rounded to six decimals.
			if math.Abs(regional-1.1*global) > 2e-6 {
				t.Errorf("regional cost %v, want 1.1 x %v", regional, global)
			}
		})
	}
}

// The uplift is for chat completions only. Every row carries the same usage,
// cached details included, so only the endpoint type differs.
func TestVertexUpliftChatEndpointsOnly(t *testing.T) {
	rows := []struct {
		endpoint string
		uplifted bool
	}{
		{"chat", true},
		{"", true},
		{"genai_generate", false},
		{"genai_stream", false},
		{"anthropic_messages", false},
		{"completion", false},
	}
	for _, row := range rows {
		name := row.endpoint
		if name == "" {
			name = "empty"
		}
		t.Run(name, func(t *testing.T) {
			p := vertexPlugin(bundledDB(), nil, nil, t27)
			rc := vertexChat("gemini-3.8-flash", "vertex", "us-central1", usageWithCached(1_000_000, 100_000, 400_000))
			rc.EndpointType = row.endpoint
			p.ProcessResponse(context.Background(), rc)
			if row.uplifted {
				if got := rc.Metadata["cost"]; got != "1.881000" {
					t.Errorf("cost = %q, want 1.881000", got)
				}
				wantUplift(t, rc, "us-central1", "1.1")
				return
			}
			if got := rc.Metadata["cost"]; got != "1.710000" {
				t.Errorf("cost = %q, want 1.710000", got)
			}
			wantNoUplift(t, rc)
		})
	}
}
