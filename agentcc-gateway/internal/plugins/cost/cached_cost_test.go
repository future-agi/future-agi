package cost

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Usage as each upstream reports it. The OpenAI shape is written by hand in
// the documented prompt_tokens_details form; the Azure usage is Microsoft's
// documented chat completions example; the OpenRouter one is theirs.
const (
	openAIChatBody = `{"id":"chatcmpl-cached","object":"chat.completion","created":1761580000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":2006,"completion_tokens":300,"total_tokens":2306,"prompt_tokens_details":{"cached_tokens":1920,"audio_tokens":0},"completion_tokens_details":{"reasoning_tokens":0}}}`

	openAIStreamFinalChunk = `{"id":"chatcmpl-cached","object":"chat.completion.chunk","created":1761580000,"model":"gpt-4o","choices":[],"usage":{"prompt_tokens":2006,"completion_tokens":300,"total_tokens":2306,"prompt_tokens_details":{"cached_tokens":1920,"audio_tokens":0},"completion_tokens_details":{"reasoning_tokens":0}}}`

	azureChatBody = `{"id":"chatcmpl-az","object":"chat.completion","created":1761580000,"model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1566,"completion_tokens":1518,"total_tokens":3084,"prompt_tokens_details":{"audio_tokens":null,"cached_tokens":1408,"cache_write_tokens":0},"completion_tokens_details":{"audio_tokens":null,"reasoning_tokens":576}}}`
)

func decodeChat(t *testing.T, body string) *models.Usage {
	t.Helper()
	var resp models.ChatCompletionResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatalf("decode chat response: %v", err)
	}
	if resp.Usage == nil {
		t.Fatal("chat response has no usage")
	}
	return resp.Usage
}

func decodeChunk(t *testing.T, body string) *models.Usage {
	t.Helper()
	var chunk models.StreamChunk
	if err := json.Unmarshal([]byte(body), &chunk); err != nil {
		t.Fatalf("decode stream chunk: %v", err)
	}
	if chunk.Usage == nil {
		t.Fatal("stream chunk has no usage")
	}
	return chunk.Usage
}

func usageWithCached(prompt, completion, cached int) *models.Usage {
	raw := json.RawMessage(fmt.Sprintf(`{"cached_tokens":%d}`, cached))
	return &models.Usage{
		PromptTokens:        prompt,
		CompletionTokens:    completion,
		TotalTokens:         prompt + completion,
		PromptTokensDetails: &raw,
	}
}

// cachedRatesDB lists the entries these tests price against, at the rates the
// bundled table has for the first three.
func cachedRatesDB() func() *modeldb.ModelDB {
	entry := func(id string, in, cached, out float64) *modeldb.ModelInfo {
		return &modeldb.ModelInfo{ID: id, Mode: modeldb.ModeChat, Pricing: modeldb.PricingInfo{
			InputPerToken: in, CachedInputPerToken: cached, OutputPerToken: out,
		}}
	}
	db := modeldb.New(map[string]*modeldb.ModelInfo{
		"gpt-4o":                entry("gpt-4o", 2.5e-6, 1.25e-6, 1e-5),
		"gpt-5.6":               entry("gpt-5.6", 4e-6, 4e-7, 2e-5),
		"gemini/gemini-4-argon": entry("gemini/gemini-4-argon", 2e-6, 1e-7, 1e-5),
		"no-cached-rate":        entry("no-cached-rate", 1e-6, 0, 2e-6),
		"cached-above-input":    entry("cached-above-input", 1e-6, 2e-6, 2e-6),
	}, nil)
	return func() *modeldb.ModelDB { return db }
}

// scenario is one chat request through the cost step.
type scenario struct {
	db       func() *modeldb.ModelDB
	model    string
	provider string
	usage    func(t *testing.T) *models.Usage

	tables   string // price table name the provider states
	provType string // type the provider's config records
	at       time.Time
	resolved string // model the upstream reports
	aliases  map[string]float64
	alias    string
}

// run costs the scenario with the cached-token gate open or closed and the
// usage carrying its prompt cache details or not.
func (s scenario) run(t *testing.T, gate, details bool) *models.RequestContext {
	t.Helper()
	db := s.db
	if db == nil {
		db = cachedRatesDB()
	}
	p := New(true, db, nil, s.aliases)
	if s.tables != "" {
		p.SetPriceTables(func(_, _ string) string { return s.tables })
	}
	if s.provType != "" {
		p.SetProviderTypes(func(_, _ string) string { return s.provType })
	}
	if !s.at.IsZero() {
		p.now = clockAt(s.at)
	}
	p.SetCachedTokensInPrompt(func(_, id string) bool { return gate && id == s.provider })

	usage := *s.usage(t)
	if !details {
		usage.PromptTokensDetails = nil
	}
	rc := newRC(s.model, usage.PromptTokens, usage.CompletionTokens)
	rc.Response.Usage = &usage
	rc.Provider = s.provider
	rc.ResolvedModel = s.resolved
	if s.alias != "" {
		rc.Metadata["model_alias"] = s.alias
	}
	p.ProcessResponse(context.Background(), rc)
	return rc
}

func (s scenario) wantCost(t *testing.T, gate bool, want string, wantCached string) {
	t.Helper()
	rc := s.run(t, gate, true)
	if got := rc.Metadata["cost"]; got != want {
		t.Errorf("gate=%v: cost = %q, want %q", gate, got, want)
	}
	if got := rc.Metadata[cachedTokensKey]; got != wantCached {
		t.Errorf("gate=%v: %s = %q, want %q", gate, cachedTokensKey, got, wantCached)
	}
}

var (
	scenOpenAIChat = scenario{
		model: "gpt-4o", provider: "openai",
		usage: func(t *testing.T) *models.Usage { return decodeChat(t, openAIChatBody) },
	}
	scenOpenAIStream = scenario{
		model: "gpt-4o", provider: "openai",
		usage: func(t *testing.T) *models.Usage { return decodeChunk(t, openAIStreamFinalChunk) },
	}
	scenAzure = scenario{
		model: "gpt-4o", provider: "azure",
		usage: func(t *testing.T) *models.Usage { return decodeChat(t, azureChatBody) },
	}
	scenResponses = scenario{
		model: "gpt-5.6", provider: "openai",
		usage: func(*testing.T) *models.Usage { return usageWithCached(120, 40, 64) },
	}
	scenGemini = scenario{
		model: "gemini-4-argon", provider: "google", tables: "gemini",
		usage: func(*testing.T) *models.Usage { return usageWithCached(1000, 200, 750) },
	}
	scenRetry = scenario{
		model: "gpt-4o", provider: "openai", resolved: "gpt-4o-2099-unlisted",
		usage: func(*testing.T) *models.Usage { return usageWithCached(1_000_000, 100_000, 750_000) },
	}
	scenAlias = scenario{
		model: "gpt-4o", provider: "openai", aliases: map[string]float64{"fast": 2}, alias: "fast-x",
		usage: func(*testing.T) *models.Usage { return usageWithCached(1_000_000, 100_000, 750_000) },
	}
)

func geminiFlashScenario(provider, tables, provType string, at time.Time) scenario {
	return scenario{
		db: bundledDB(), model: "gemini-3.7-flash", provider: provider, tables: tables, provType: provType, at: at,
		usage: func(*testing.T) *models.Usage { return usageWithCached(1_000_000, 100_000, 750_000) },
	}
}

func TestCachedPriceOpenAIChat(t *testing.T) {
	scenOpenAIChat.wantCost(t, true, "0.005615", "1920")
	scenOpenAIChat.wantCost(t, false, "0.008015", "")
}

func TestCachedPriceOpenAIStream(t *testing.T) {
	// The final chunk's usage is what the stream handler hands the cost step.
	scenOpenAIStream.wantCost(t, true, "0.005615", "1920")
	scenOpenAIStream.wantCost(t, false, "0.008015", "")
}

func TestCachedPriceAzureChat(t *testing.T) {
	scenAzure.wantCost(t, true, "0.017335", "1408")
	scenAzure.wantCost(t, false, "0.019095", "")
}

func TestCachedPriceResponsesChat(t *testing.T) {
	scenResponses.wantCost(t, true, "0.001050", "64")
	scenResponses.wantCost(t, false, "0.001280", "")
}

func TestCachedPriceGeminiChat(t *testing.T) {
	scenGemini.wantCost(t, true, "0.002575", "750")
	scenGemini.wantCost(t, false, "0.004000", "")
}

func TestCachedPriceOnlyForCheckedProviders(t *testing.T) {
	cost := func(p *Plugin, provider, org string) string {
		rc := newRC("gpt-4o", 0, 0)
		rc.Response.Usage = decodeChat(t, openAIChatBody)
		rc.Provider = provider
		if org != "" {
			rc.Metadata[tenant.MetadataKeyOrgID] = org
		}
		p.ProcessResponse(context.Background(), rc)
		return rc.Metadata["cost"]
	}
	newP := func() *Plugin { return New(true, cachedRatesDB(), nil, nil) }

	t.Run("no-setter", func(t *testing.T) {
		if got := cost(newP(), "openai", ""); got != "0.008015" {
			t.Errorf("cost = %q, want 0.008015", got)
		}
	})
	t.Run("empty-provider", func(t *testing.T) {
		p := newP()
		calls := 0
		p.SetCachedTokensInPrompt(func(_, _ string) bool { calls++; return true })
		if got := cost(p, "", ""); got != "0.008015" {
			t.Errorf("cost = %q, want 0.008015", got)
		}
		if calls != 0 {
			t.Errorf("the gate was asked %d times for a request with no provider", calls)
		}
	})
	t.Run("func-false", func(t *testing.T) {
		p := newP()
		p.SetCachedTokensInPrompt(func(_, _ string) bool { return false })
		if got := cost(p, "openai", ""); got != "0.008015" {
			t.Errorf("cost = %q, want 0.008015", got)
		}
	})
	t.Run("func-true", func(t *testing.T) {
		p := newP()
		p.SetCachedTokensInPrompt(func(_, _ string) bool { return true })
		if got := cost(p, "openai", ""); got != "0.005615" {
			t.Errorf("cost = %q, want 0.005615", got)
		}
	})
	t.Run("org", func(t *testing.T) {
		p := newP()
		var askedOrg, askedProvider string
		p.SetCachedTokensInPrompt(func(org, id string) bool { askedOrg, askedProvider = org, id; return true })
		cost(p, "openai", "org-1")
		if askedOrg != "org-1" || askedProvider != "openai" {
			t.Errorf("the gate was asked for org %q provider %q, want org-1 and openai", askedOrg, askedProvider)
		}
	})
}

// logCapture swaps the process-wide slog default, so a test that uses it must
// not run in parallel.
func logCapture(t *testing.T) *bytes.Buffer {
	t.Helper()
	var buf bytes.Buffer
	prev := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&buf, nil)))
	t.Cleanup(func() { slog.SetDefault(prev) })
	return &buf
}

const aboveWarning = "more cached prompt tokens than prompt tokens"

func TestCachedPriceAbovePromptIsNotDiscounted(t *testing.T) {
	buf := logCapture(t)
	p := New(true, cachedRatesDB(), nil, nil)
	p.SetCachedTokensInPrompt(func(_, id string) bool { return id == "openai" })

	price := func(model string, prompt, cached int) *models.RequestContext {
		rc := newRC(model, prompt, 10)
		rc.Response.Usage = usageWithCached(prompt, 10, cached)
		rc.Provider = "openai"
		p.ProcessResponse(context.Background(), rc)
		return rc
	}

	for i := 0; i < 3; i++ {
		rc := price("gpt-4o", 100, 1000)
		if got := rc.Metadata["cost"]; got != "0.000350" {
			t.Errorf("request %d: cost = %q, want 0.000350", i, got)
		}
		if _, set := rc.Metadata[cachedTokensKey]; set {
			t.Errorf("request %d: %s is set for a count above the prompt", i, cachedTokensKey)
		}
	}
	if n := strings.Count(buf.String(), aboveWarning); n != 1 {
		t.Errorf("warned %d times for one provider and model, want 1\n%s", n, buf.String())
	}
	for _, field := range []string{"prompt_tokens=100", "cached_tokens=1000"} {
		if !strings.Contains(buf.String(), field) {
			t.Errorf("warning lacks %s\n%s", field, buf.String())
		}
	}

	t.Run("cached=prompt", func(t *testing.T) {
		rc := price("gpt-4o", 100, 100)
		if got := rc.Metadata["cost"]; got != "0.000225" {
			t.Errorf("cost = %q, want 0.000225", got)
		}
		if n := strings.Count(buf.String(), aboveWarning); n != 1 {
			t.Errorf("a count equal to the prompt warned: %d lines", n)
		}
	})

	t.Run("second model", func(t *testing.T) {
		price("gpt-5.6", 100, 1000)
		if n := strings.Count(buf.String(), aboveWarning); n != 2 {
			t.Errorf("warned %d times after a second model, want 2", n)
		}
	})

	t.Run("cap", func(t *testing.T) {
		buf := logCapture(t)
		p := New(true, cachedRatesDB(), nil, nil)
		p.SetCachedTokensInPrompt(func(_, _ string) bool { return true })
		for i := 0; i < maxUnpricedWarnings+5; i++ {
			rc := newRC("gpt-4o", 100, 10)
			rc.Response.Usage = usageWithCached(100, 10, 1000)
			rc.Provider = fmt.Sprintf("p-%d", i)
			p.ProcessResponse(context.Background(), rc)
		}
		if n := strings.Count(buf.String(), aboveWarning); n != maxUnpricedWarnings {
			t.Errorf("logged %d warnings, want the cap of %d", n, maxUnpricedWarnings)
		}
		rc := newRC("unpriced-after-the-cap", 10, 10)
		p.ProcessResponse(context.Background(), rc)
		if !strings.Contains(buf.String(), "no pricing for model") {
			t.Errorf("the unpriced-model warning was used up by the cached-token warnings")
		}
	})
}

func TestCachedPriceOnTheRetry(t *testing.T) {
	scenRetry.wantCost(t, true, "2.562500", "750000")
	scenRetry.wantCost(t, false, "3.500000", "")
}

func TestCachedPriceAtTheDatedRate(t *testing.T) {
	t.Run("t26", func(t *testing.T) {
		geminiFlashScenario("gemini", "gemini", "", t26).wantCost(t, true, "0.618750", "750000")
	})
	t.Run("t27", func(t *testing.T) {
		// The 2027 row's cached rate is 1.5e-7.
		geminiFlashScenario("gemini", "gemini", "", t27).wantCost(t, true, "1.237500", "750000")
	})
}

func TestCachedPriceResellerOwnEntryHasNoCachedRate(t *testing.T) {
	for _, at := range []struct {
		name string
		when time.Time
	}{{"t26", t26}, {"t27", t27}} {
		s := geminiFlashScenario("di", "", "deepinfra", at.when)
		t.Run("di-gate-open-"+at.name, func(t *testing.T) {
			rc := s.run(t, true, true)
			if got := rc.Metadata["cost"]; got != "1.125000" {
				t.Errorf("cost = %q, want 1.125000", got)
			}
		})
		t.Run("di-gate-closed-"+at.name, func(t *testing.T) {
			rc := s.run(t, false, true)
			if got := rc.Metadata["cost"]; got != "1.125000" {
				t.Errorf("cost = %q, want 1.125000", got)
			}
		})
	}
	t.Run("gemini-t26", func(t *testing.T) {
		rc := geminiFlashScenario("gemini", "gemini", "", t26).run(t, true, true)
		if got := rc.Metadata["cost"]; got != "0.618750" {
			t.Errorf("cost = %q, want 0.618750", got)
		}
	})
}

func TestCachedPriceEntryWithoutALowerCachedRate(t *testing.T) {
	for _, model := range []string{"no-cached-rate", "cached-above-input"} {
		t.Run(model, func(t *testing.T) {
			s := scenario{model: model, provider: "openai",
				usage: func(*testing.T) *models.Usage { return usageWithCached(1000, 500, 400) }}
			rc := s.run(t, true, true)
			if got := rc.Metadata["cost"]; got != "0.002000" {
				t.Errorf("cost = %q, want 0.002000", got)
			}
		})
	}
}

func TestCachedPromptTokensReadsTheDetails(t *testing.T) {
	cases := []struct {
		name    string
		details string
		want    int
	}{
		{"null", `null`, 0},
		{"empty object", `{}`, 0},
		{"count", `{"cached_tokens":75}`, 75},
		{"zero", `{"cached_tokens":0}`, 0},
		{"negative", `{"cached_tokens":-3}`, 0},
		{"string", `{"cached_tokens":"75"}`, 0},
		{"fraction", `{"cached_tokens":7.5}`, 0},
		{"exponent", `{"cached_tokens":1e3}`, 0},
		{"array", `[1]`, 0},
		{"other keys ignored", `{"cached_tokens":75,"audio_tokens":"x"}`, 75},
		{"not json", `not json`, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			raw := json.RawMessage(c.details)
			if got := cachedPromptTokens(&models.Usage{PromptTokensDetails: &raw}); got != c.want {
				t.Errorf("cachedPromptTokens(%s) = %d, want %d", c.details, got, c.want)
			}
		})
	}
	t.Run("nil usage", func(t *testing.T) {
		if got := cachedPromptTokens(nil); got != 0 {
			t.Errorf("got %d, want 0", got)
		}
	})
	t.Run("nil details", func(t *testing.T) {
		if got := cachedPromptTokens(&models.Usage{}); got != 0 {
			t.Errorf("got %d, want 0", got)
		}
	})

	t.Run("unreadable details through the cost step", func(t *testing.T) {
		s := scenOpenAIChat
		s.usage = func(t *testing.T) *models.Usage {
			u := decodeChat(t, openAIChatBody)
			raw := json.RawMessage(`not json`)
			u.PromptTokensDetails = &raw
			return u
		}
		rc := s.run(t, true, true)
		if got := rc.Metadata["cost"]; got != "0.008015" {
			t.Errorf("cost = %q, want 0.008015", got)
		}
		if _, set := rc.Metadata[cachedTokensKey]; set {
			t.Errorf("%s is set for unreadable details", cachedTokensKey)
		}
	})
}

func TestCachedPriceAliasFactorAndOrgPrice(t *testing.T) {
	t.Run("alias factor", func(t *testing.T) {
		scenAlias.wantCost(t, true, "5.125000", "750000")
	})

	t.Run("org custom price", func(t *testing.T) {
		store := tenant.NewStore()
		store.Set("org-1", &tenant.OrgConfig{CostTracking: &tenant.CostTrackingConfig{
			Enabled:       true,
			CustomPricing: map[string]*tenant.CustomPricing{"gpt-4o": {InputPerMTok: 1.0, OutputPerMTok: 1.0}},
		}})
		p := New(true, cachedRatesDB(), store, nil)
		p.SetCachedTokensInPrompt(func(_, _ string) bool { return true })
		rc := newRC("gpt-4o", 0, 0)
		rc.Response.Usage = decodeChat(t, openAIChatBody)
		rc.Provider = "openai"
		rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
		p.ProcessResponse(context.Background(), rc)

		if rc.Metadata["cost"] != "0.002306" || rc.Metadata["cost_source"] != "org_custom" {
			t.Errorf("cost = %q source = %q, want 0.002306 from org_custom", rc.Metadata["cost"], rc.Metadata["cost_source"])
		}
		if _, set := rc.Metadata[cachedTokensKey]; set {
			t.Errorf("%s is set on an org custom price", cachedTokensKey)
		}
	})
}

func TestCachedPriceNeverRaisesARequestsBill(t *testing.T) {
	cases := []struct {
		name   string
		s      scenario
		strict bool // must be lower, not only not higher
	}{
		{"openai chat", scenOpenAIChat, true},
		{"openai stream", scenOpenAIStream, true},
		{"azure chat", scenAzure, true},
		{"responses chat", scenResponses, true},
		{"gemini chat", scenGemini, true},
		{"retry", scenRetry, true},
		{"dated rate t26", geminiFlashScenario("gemini", "gemini", "", t26), true},
		{"dated rate t27", geminiFlashScenario("gemini", "gemini", "", t27), true},
		{"reseller own entry", geminiFlashScenario("di", "", "deepinfra", t26), false},
		{"alias factor", scenAlias, true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			with := c.s.run(t, true, true).Metadata["cost"]
			without := c.s.run(t, true, false).Metadata["cost"]
			got, err1 := strconv.ParseFloat(with, 64)
			old, err2 := strconv.ParseFloat(without, 64)
			if err1 != nil || err2 != nil {
				t.Fatalf("cost %q / %q is not a number", with, without)
			}
			if got > old {
				t.Errorf("rose: %g > %g", got, old)
			}
			if c.strict && got >= old {
				t.Errorf("not lower: %g >= %g", got, old)
			}
		})
	}
}
