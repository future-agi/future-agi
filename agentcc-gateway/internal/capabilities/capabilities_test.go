package capabilities

import (
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// builtinConfig has one provider per built-in api_format, at public base
// URLs (no connectivity check), with fake credentials. Nothing is called.
func builtinConfig() map[string]config.ProviderConfig {
	off := false
	return map[string]config.ProviderConfig{
		"openai":    {BaseURL: "https://api.openai.com", APIKey: "test-not-a-key", APIFormat: "openai", AutoDiscover: &off, Models: []string{"gpt-4o"}},
		"anthropic": {BaseURL: "https://api.anthropic.com", APIKey: "test-not-a-key", APIFormat: "anthropic", AutoDiscover: &off},
		"gemini":    {BaseURL: "https://generativelanguage.googleapis.com", APIKey: "test-not-a-key", APIFormat: "gemini", AutoDiscover: &off},
		"bedrock": {BaseURL: "https://bedrock-runtime.us-east-1.amazonaws.com", APIFormat: "bedrock", AutoDiscover: &off,
			AWSAccessKeyID: "test-not-an-id", AWSSecretAccessKey: "test-not-a-secret", AWSRegion: "us-east-1"},
		"cohere": {BaseURL: "https://api.cohere.com", APIKey: "test-not-a-key", APIFormat: "cohere", AutoDiscover: &off},
		"azure":  {BaseURL: "https://example.openai.azure.com", APIKey: "test-not-a-key", APIFormat: "azure", AutoDiscover: &off},
		// A managed OpenAI-compatible aggregator, as OpenRouter is configured.
		"openrouter": {BaseURL: "https://openrouter.ai/api", APIKey: "test-not-a-key", APIFormat: "openai", Type: "openrouter",
			Managed: true, AutoDiscover: &off},
	}
}

func builtinRegistry(t *testing.T) (*providers.Registry, map[string]config.ProviderConfig) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Providers = builtinConfig()
	reg, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	return reg, cfg.Providers
}

func builtinProviders(t *testing.T) []Provider {
	t.Helper()
	reg, cfg := builtinRegistry(t)
	return FromRegistry(reg, cfg)
}

// directSupport is what the handler of each endpoint does with adapter,
// written out per endpoint from the handlers' own type assertions, so the
// matrix is checked against the code paths rather than against itself.
func directSupport(endpoint string, adapter providers.Provider) bool {
	switch endpoint {
	case EndpointChat, EndpointModels, EndpointMessages, EndpointGenAI, EndpointCompletions:
		return true // the base interface, or translated to chat
	case EndpointResponses:
		_, ok := adapter.(providers.ResponsesProvider)
		return ok
	case EndpointEmbeddings:
		_, ok := adapter.(providers.EmbeddingProvider)
		return ok
	case EndpointImages:
		_, ok := adapter.(providers.ImageProvider)
		return ok
	case EndpointSpeech:
		_, ok := adapter.(providers.SpeechProvider)
		return ok
	case EndpointTranscriptions:
		_, ok := adapter.(providers.TranscriptionProvider)
		return ok
	case EndpointRerank:
		_, ok := adapter.(providers.RerankProvider)
		return ok
	case EndpointOCR:
		_, ok := adapter.(providers.OCRProvider)
		return ok
	case EndpointSearch:
		_, ok := adapter.(providers.SearchProvider)
		return ok
	case EndpointAssistants, EndpointThreads:
		_, ok := adapter.(providers.AssistantsProvider)
		return ok
	case EndpointVectorStores:
		_, ok := adapter.(providers.VectorStoresProvider)
		return ok
	case EndpointVideos:
		_, ok := adapter.(providers.VideoProvider)
		return ok
	case EndpointRealtime:
		_, ok := adapter.(providers.RealtimeProvider)
		return ok
	case EndpointFiles, EndpointBatch, EndpointAsync:
		return true // gateway-hosted, or chat through the global registry
	case EndpointScheduled:
		return false // routing.scheduled is off in these inputs
	}
	panic("no direct check for " + endpoint)
}

func TestRowsMatchBuiltinInterfaceAssertions(t *testing.T) {
	reg, _ := builtinRegistry(t)
	m := Build(Inputs{Providers: builtinProviders(t), Audience: AudienceAdmin, Now: time.Now()})

	if len(m.Endpoints) != len(endpointSpecs) {
		t.Fatalf("matrix has %d endpoints, want %d", len(m.Endpoints), len(endpointSpecs))
	}
	for _, id := range []string{"openai", "anthropic", "gemini", "bedrock", "cohere", "azure", "openrouter"} {
		adapter, _ := reg.GetProvider(id)
		for _, row := range m.Endpoints {
			c, ok := row.Providers[id]
			if !ok {
				t.Fatalf("%s has no %s cell", row.Endpoint, id)
			}
			if got, want := c.Status != StatusUnsupported, directSupport(row.Endpoint, adapter); got != want {
				t.Errorf("%s on %s: status %s, but the handler's assertion says served=%v", id, row.Endpoint, c.Status, want)
			}
		}
	}
	// The interface-only endpoints no built-in adapter implements.
	for _, ep := range []string{EndpointSearch, EndpointVideos, EndpointRealtime} {
		row := rowOf(t, m, ep)
		if row.LaunchTier != TierUnsupported {
			t.Errorf("%s: launch_tier %s with no provider serving it, want unsupported", ep, row.LaunchTier)
		}
	}
	if tier := rowOf(t, m, EndpointChat).LaunchTier; tier != TierLaunch {
		t.Errorf("chat.completions launch_tier = %s, want launch", tier)
	}
}

func TestFormatInterfacesMatchAdapters(t *testing.T) {
	for _, p := range builtinProviders(t) {
		if p.Upstream == "openrouter" {
			continue // same adapter as openai
		}
		if want := FormatInterfaces(p.APIFormat); !reflect.DeepEqual(p.Interfaces, want) {
			t.Errorf("%s: adapter implements %v, formatInterfaces says %v", p.APIFormat, p.Interfaces, want)
		}
	}
	if got := FormatInterfaces("google"); !reflect.DeepEqual(got, FormatInterfaces("gemini")) {
		t.Errorf("google is gemini's alias: %v", got)
	}
}

func TestOpenAICompatibleUpstreamIsProviderDependent(t *testing.T) {
	m := Build(Inputs{Providers: builtinProviders(t), Audience: AudienceAdmin, Now: time.Now()})
	for _, tc := range []struct{ provider, endpoint, want string }{
		{"openai", EndpointImages, StatusSupported},
		{"openai", EndpointResponses, StatusSupported},
		{"openai", EndpointOCR, StatusProviderDependent}, // Mistral serves /v1/ocr, OpenAI does not
		{"openrouter", EndpointChat, StatusSupported},
		{"openrouter", EndpointEmbeddings, StatusProviderDependent},
		{"openrouter", EndpointImages, StatusProviderDependent},
		{"anthropic", EndpointMessages, StatusSupported},
		{"anthropic", EndpointEmbeddings, StatusUnsupported},
		{"cohere", EndpointRerank, StatusSupported},
	} {
		if c := cellOf(t, m, tc.endpoint, tc.provider); c.Status != tc.want {
			t.Errorf("%s on %s: status %s, want %s", tc.provider, tc.endpoint, c.Status, tc.want)
		}
	}
	if UpstreamOf("", "https://api.mistral.ai") != "mistral" || UpstreamOf("", "http://127.0.0.1:9/api") != "" {
		t.Error("UpstreamOf reads the host of an untyped provider")
	}
}

func TestAdapterFeaturesFollowTranslation(t *testing.T) {
	m := Build(Inputs{Providers: builtinProviders(t), Audience: AudienceAdmin, Now: time.Now()})
	chat := func(p string) Features { return cellOf(t, m, EndpointChat, p).Features }
	// Anthropic and Bedrock drop input_audio; Gemini maps it.
	if chat("anthropic").AudioInput || chat("bedrock").AudioInput || !chat("gemini").AudioInput {
		t.Errorf("audio_input: anthropic %v, bedrock %v, gemini %v", chat("anthropic").AudioInput, chat("bedrock").AudioInput, chat("gemini").AudioInput)
	}
	// Cohere degrades json_schema to json_object and translates no images.
	if c := chat("cohere"); c.JSONSchema || c.Vision || !c.Tools {
		t.Errorf("cohere chat features = %+v", c)
	}
	// Anthropic: no cache_control on chat, but the native
	// Messages passthrough carries it.
	if chat("anthropic").PromptCaching || !cellOf(t, m, EndpointMessages, "anthropic").Features.PromptCaching {
		t.Error("anthropic prompt_caching must be false on chat.completions and true on native messages")
	}
	// Messages translated for a non-Anthropic adapter keeps tools and images only.
	if f := cellOf(t, m, EndpointMessages, "gemini").Features; f.JSONSchema || f.Reasoning || f.PromptCaching || !f.Tools || !f.Vision {
		t.Errorf("translated messages features = %+v", f)
	}
	// Non-generation endpoints carry no generation features.
	if f := cellOf(t, m, EndpointEmbeddings, "openai").Features; f != (Features{}) {
		t.Errorf("embeddings features = %+v", f)
	}
	// An unsupported cell has no features.
	if f := cellOf(t, m, EndpointResponses, "anthropic").Features; f != (Features{}) {
		t.Errorf("unsupported cell features = %+v", f)
	}
}

// catalogWith holds one managed snapshot.
func catalogWith(providerID string, models map[string]*catalog.Model) *catalog.Catalog {
	s := catalog.NewStore()
	s.Put(&catalog.Snapshot{ProviderID: providerID, Source: "bundled", FetchedAt: time.Now(), Models: models})
	return s.Load()
}

var testCatalog = map[string]*catalog.Model{
	"anthropic/claude-sonnet-5.5": {ID: "anthropic/claude-sonnet-5.5", ContextLength: 1000000, MaxOutputTokens: 64000,
		InputModalities: []string{"text", "image"}, OutputModalities: []string{"text"},
		SupportedParameters: []string{"tools", "structured_outputs", "reasoning"}},
	"qwen/qwen3.7-flash": {ID: "qwen/qwen3.7-flash", ContextLength: 131072, MaxOutputTokens: 32000,
		InputModalities: []string{"text"}, SupportedParameters: []string{"tools"}},
}

func TestCatalogNarrowsFeatures(t *testing.T) {
	cat := catalogWith("openrouter", testCatalog)
	m := Build(Inputs{Providers: builtinProviders(t), Catalog: cat, Audience: AudienceAdmin, Now: time.Now()})
	c := cellOf(t, m, EndpointChat, "openrouter")
	if c.Source != SourceCatalog {
		t.Errorf("source = %s, want catalog", c.Source)
	}
	// The adapter carries everything; no catalog model takes audio.
	if f := c.Features; !f.Tools || !f.JSONSchema || !f.Vision || !f.Reasoning || f.AudioInput {
		t.Errorf("catalog-narrowed features = %+v", f)
	}
	// Without a catalog the adapter's features stand, from its interface.
	m = Build(Inputs{Providers: builtinProviders(t), Audience: AudienceAdmin, Now: time.Now()})
	if c := cellOf(t, m, EndpointChat, "openrouter"); c.Source != SourceInterface || !c.Features.AudioInput {
		t.Errorf("no catalog: source %s features %+v", c.Source, c.Features)
	}
}

func TestConfiguredModelsNarrowFeatures(t *testing.T) {
	db := modeldb.New(map[string]*modeldb.ModelInfo{
		"gpt-4o": {ID: "gpt-4o", Mode: modeldb.ModeChat, MaxInputTokens: 128000, MaxOutputTokens: 16384,
			Capabilities: modeldb.CapabilityFlags{FunctionCalling: true, Vision: true, ResponseSchema: true, Streaming: true}},
	}, nil)
	m := Build(Inputs{Providers: builtinProviders(t), ModelDB: db, Audience: AudienceAdmin, Now: time.Now()})
	c := cellOf(t, m, EndpointChat, "openai")
	if c.Source != SourceConfig || c.Features.Reasoning || c.Features.AudioInput || !c.Features.Tools || !c.Features.Vision {
		t.Errorf("configured gpt-4o: source %s features %+v", c.Source, c.Features)
	}
}

func TestManagedAllowed(t *testing.T) {
	cat := catalogWith("openrouter", testCatalog)
	priced := func(endpoint, model string) bool { return model == "anthropic/claude-sonnet-5.5" }
	in := Inputs{Providers: builtinProviders(t), Catalog: cat, Priced: priced, Audience: AudienceAdmin, Now: time.Now()}
	m := Build(in)

	if !cellOf(t, m, EndpointChat, "openrouter").ManagedAllowed {
		t.Error("a managed provider with a priced catalog model must allow managed chat")
	}
	// Images are not in the launch set, priced or not.
	for _, p := range []string{"openrouter", "openai", "gemini"} {
		if cellOf(t, m, EndpointImages, p).ManagedAllowed {
			t.Errorf("images on %s report managed_allowed=true", p)
		}
	}
	// A BYOK provider is never managed.
	if cellOf(t, m, EndpointChat, "openai").ManagedAllowed {
		t.Error("a BYOK provider reported managed_allowed=true")
	}
	// Nothing priced: not allowed.
	in.Priced = func(string, string) bool { return false }
	if cellOf(t, Build(in), EndpointChat, "openrouter").ManagedAllowed {
		t.Error("an unpriced managed provider reported managed_allowed=true")
	}
	// Priced only under the provider's prefix (the catalog's model-database key).
	in.Priced = func(endpoint, model string) bool { return model == "openrouter/qwen/qwen3.7-flash" }
	if !cellOf(t, Build(in), EndpointChat, "openrouter").ManagedAllowed {
		t.Error("a model priced as <provider>/<model> must count")
	}
	// An endpoint the gateway does not serve managed traffic on.
	in.Priced = priced
	in.ManagedServes = func(endpoint string) bool { return endpoint == EndpointChat }
	m = Build(in)
	if !cellOf(t, m, EndpointChat, "openrouter").ManagedAllowed || cellOf(t, m, EndpointMessages, "openrouter").ManagedAllowed {
		t.Error("managed_allowed must follow ManagedServes")
	}
}

func TestResaleTerms(t *testing.T) {
	ps := builtinProviders(t)
	m := Build(Inputs{Providers: ps, Audience: AudienceAdmin, Now: time.Now()})
	if c := cellOf(t, m, EndpointChat, "openrouter"); c.ManagedResaleTerms != ResaleUnconfirmed {
		t.Errorf("managed provider without resale_terms_ref: %s, want unconfirmed", c.ManagedResaleTerms)
	}
	for i := range ps {
		if ps[i].ID == "openrouter" {
			ps[i].ResaleTermsRef = "test-agreement-ref"
		}
	}
	m = Build(Inputs{Providers: ps, Audience: AudienceAdmin, Now: time.Now()})
	if c := cellOf(t, m, EndpointChat, "openrouter"); c.ManagedResaleTerms != ResaleCleared || c.Status != StatusSupported {
		t.Errorf("with resale_terms_ref: %s %s, want cleared and still supported", c.ManagedResaleTerms, c.Status)
	}
}

func TestAudienceGates(t *testing.T) {
	ps := builtinProviders(t)
	admin := Build(Inputs{Providers: ps, Audience: AudienceAdmin, Now: time.Now()})
	global := Build(Inputs{Providers: ps, Audience: AudienceGlobalKey, ScheduledEnabled: true, Now: time.Now()})
	org := Build(Inputs{Providers: ps, Audience: AudienceOrgKey, ScheduledEnabled: true, Now: time.Now()})
	for _, tc := range []struct {
		m        *Matrix
		endpoint string
		want     string
	}{
		{admin, EndpointBatch, StatusSupported},
		{global, EndpointBatch, StatusUnsupported},    // admin-token route
		{admin, EndpointScheduled, StatusUnsupported}, // routing.scheduled off
		{global, EndpointScheduled, StatusSupported},
		{org, EndpointScheduled, StatusUnsupported}, // refused to org-only keys
		{global, EndpointAsync, StatusSupported},
		{org, EndpointAsync, StatusUnsupported},
		{org, EndpointFiles, StatusSupported},
	} {
		c := cellOf(t, tc.m, tc.endpoint, "openai")
		if c.Status != tc.want {
			t.Errorf("%s: status %s, want %s", tc.endpoint, c.Status, tc.want)
		}
		if c.Status == StatusUnsupported && c.Source != SourceConfig && tc.endpoint != EndpointScheduled {
			t.Errorf("%s: a gate's refusal has source %s, want config", tc.endpoint, c.Source)
		}
	}
}

func writeJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "qualification.json")
	if err := os.WriteFile(path, b, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestQualificationFileOverridesQualified(t *testing.T) {
	ranAt := time.Date(2026, 10, 12, 18, 20, 0, 0, time.UTC)
	path := writeJSON(t, map[string]any{
		"mode": "live", "ran_at": ranAt,
		"results": []map[string]any{
			{"provider": "openai", "endpoint": "chat.completions", "feature": "stream", "status": "pass"},
			{"provider": "openai", "endpoint": "chat.completions", "feature": "tools", "status": "pass"},
			{"provider": "openrouter", "endpoint": "embeddings", "feature": "basic", "status": "pass"},
			{"provider": "gemini", "endpoint": "chat.completions", "feature": "tools", "status": "pass"},
			{"provider": "gemini", "endpoint": "chat.completions", "feature": "vision", "status": "fail"},
			{"provider": "cohere", "endpoint": "chat.completions", "feature": "tools", "status": "unqualified"},
		},
	})
	q, err := LoadQualification(path)
	if err != nil {
		t.Fatal(err)
	}
	ps := builtinProviders(t)
	without := Build(Inputs{Providers: ps, Audience: AudienceAdmin, Now: time.Now()})
	with := Build(Inputs{Providers: ps, Qualification: q, Audience: AudienceAdmin, Now: time.Now()})

	if c := cellOf(t, without, EndpointChat, "openai"); c.Qualified || c.QualifiedAt != nil {
		t.Errorf("no evidence: qualified %v at %v, want false and null", c.Qualified, c.QualifiedAt)
	}
	c := cellOf(t, with, EndpointChat, "openai")
	if !c.Qualified || c.QualifiedAt == nil || !c.QualifiedAt.Equal(ranAt) || c.Source != SourceQualified {
		t.Errorf("qualified run: %+v", c)
	}
	// A pass proves the aggregator's upstream serves embeddings.
	if c := cellOf(t, with, EndpointEmbeddings, "openrouter"); !c.Qualified || c.Status != StatusSupported {
		t.Errorf("qualified provider_dependent cell: %+v", c)
	}
	// One failed check: not qualified, though run.
	if c := cellOf(t, with, EndpointChat, "gemini"); c.Qualified || c.QualifiedAt == nil {
		t.Errorf("failed check: %+v", c)
	}
	// unqualified results are not evidence.
	if c := cellOf(t, with, EndpointChat, "cohere"); c.Qualified || c.QualifiedAt != nil || c.Source == SourceQualified {
		t.Errorf("unqualified result treated as evidence: %+v", c)
	}
}

func TestQualificationRefusesMockRuns(t *testing.T) {
	for _, mode := range []string{"mock", ""} {
		path := writeJSON(t, map[string]any{"mode": mode, "results": []map[string]any{
			{"provider": "openai", "endpoint": "chat.completions", "status": "pass"}}})
		if q, err := LoadQualification(path); !errors.Is(err, ErrNotLiveQualification) || q != nil {
			t.Errorf("mode %q: q=%v err=%v, want ErrNotLiveQualification", mode, q, err)
		}
	}
	if q, err := LoadQualification(filepath.Join(t.TempDir(), "absent.json")); q != nil || err != nil {
		t.Errorf("missing file: q=%v err=%v, want no evidence", q, err)
	}
	if q, err := LoadQualification(""); q != nil || err != nil {
		t.Errorf("unset path: q=%v err=%v", q, err)
	}
}

func TestUnsupportedError(t *testing.T) {
	m := Build(Inputs{Providers: builtinProviders(t), Audience: AudienceAdmin, Now: time.Now()})
	e := m.UnsupportedError(EndpointResponses, "anthropic")
	if e.Status != http.StatusNotImplemented || e.Code != "endpoint_not_supported_by_provider" {
		t.Fatalf("error = %d %s", e.Status, e.Code)
	}
	meta := e.Metadata.(UnsupportedMeta)
	if meta.Endpoint != EndpointResponses || meta.Provider != "anthropic" ||
		!reflect.DeepEqual(meta.SupportedProviders, []string{"openai", "openrouter"}) {
		t.Errorf("metadata = %+v", meta)
	}
	if _, ok := m.Lookup(EndpointResponses, "anthropic"); !ok {
		t.Error("Lookup misses a provider cell")
	}
	if _, ok := m.Lookup("no.such.endpoint", "openai"); ok {
		t.Error("Lookup finds an unknown endpoint")
	}
}

func TestBuildModel(t *testing.T) {
	cat := catalogWith("openrouter", testCatalog)
	priced := func(endpoint, model string) bool {
		return model == "anthropic/claude-sonnet-5.5" && endpoint == EndpointChat
	}
	in := Inputs{Providers: builtinProviders(t), Catalog: cat, Priced: priced, Audience: AudienceAdmin, Now: time.Now()}

	for _, id := range []string{"anthropic/claude-sonnet-5.5", "openrouter/anthropic/claude-sonnet-5.5"} {
		v := BuildModel(in, id)
		c, ok := v.Providers["openrouter"]
		if !ok || len(v.Providers) != 1 {
			t.Fatalf("%s: providers %v", id, v.Providers)
		}
		if c.Source != SourceCatalog || c.ContextLength != 1000000 || c.MaxOutputTokens != 64000 {
			t.Errorf("%s: %+v", id, c)
		}
		if f := c.Flags; !f.Tools || !f.JSONSchema || !f.Vision || !f.Reasoning || f.AudioInput || !f.Stream {
			t.Errorf("%s flags = %+v", id, f)
		}
		if !reflect.DeepEqual(c.Modalities, Modalities{Input: []string{"text", "image"}, Output: []string{"text"}}) {
			t.Errorf("%s modalities = %+v", id, c.Modalities)
		}
		if !c.Endpoints[EndpointChat].ManagedAllowed || c.Endpoints[EndpointEmbeddings].ManagedAllowed {
			t.Errorf("%s endpoints = %+v", id, c.Endpoints)
		}
		if _, ok := c.Endpoints[EndpointImages]; ok {
			t.Errorf("%s: the model view lists only launch endpoints", id)
		}
	}
	// A model priced nowhere is not managed-allowed.
	if c := BuildModel(in, "qwen/qwen3.7-flash").Providers["openrouter"]; c.Endpoints[EndpointChat].ManagedAllowed {
		t.Error("an unpriced model reported managed_allowed=true")
	}
	// A configured model, described by the model database.
	in.ModelDB = modeldb.New(map[string]*modeldb.ModelInfo{
		"gpt-4o": {ID: "gpt-4o", Mode: modeldb.ModeChat, MaxInputTokens: 128000, MaxOutputTokens: 16384,
			Capabilities: modeldb.CapabilityFlags{FunctionCalling: true, Vision: true, ResponseSchema: true}},
	}, nil)
	c, ok := BuildModel(in, "gpt-4o").Providers["openai"]
	if !ok || c.Source != SourceConfig || c.ContextLength != 128000 || !c.Flags.Tools || c.Flags.Reasoning ||
		!reflect.DeepEqual(c.Modalities, Modalities{Input: []string{"text", "image"}, Output: []string{"text"}}) {
		t.Errorf("configured gpt-4o = %+v", c)
	}
	if v := BuildModel(in, "no-such-model"); len(v.Providers) != 0 {
		t.Errorf("unknown model: %v", v.Providers)
	}
}

func rowOf(t *testing.T, m *Matrix, endpoint string) EndpointRow {
	t.Helper()
	for _, r := range m.Endpoints {
		if r.Endpoint == endpoint {
			return r
		}
	}
	t.Fatalf("no %s row", endpoint)
	return EndpointRow{}
}

func cellOf(t *testing.T, m *Matrix, endpoint, provider string) Cell {
	t.Helper()
	c, ok := m.Lookup(endpoint, provider)
	if !ok {
		t.Fatalf("no %s cell for %s", endpoint, provider)
	}
	return c
}
