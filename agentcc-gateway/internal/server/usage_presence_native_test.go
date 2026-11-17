package server

// Permanent regression from the INT-2 investigation. A credit-funded (managed)
// request served by a native adapter must retain the same token evidence and
// estimate provenance as the OpenAI wire. Addendum 9 additionally floors
// incomplete native settlement at the admitted hold; complete counts, explicit
// zero and OpenAI/Azure controls remain exact. The managed source is built exactly as config.example.yaml
// documents a direct-provider source (managed: true, catalog_source:
// price_card) through providers.NewRegistry, so createProvider picks the
// adapter by api_format. Synthetic upstreams only.

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/catalog/sources"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	upInt2Provider = "native-direct"
	upInt2Text     = "hello there"
)

// upInt2Adapter is one managed source: the api_format its provider is built
// with, the catalogue id sent upstream, and the path its adapter calls.
type upInt2Adapter struct {
	name, format, model, path string
}

// The first entry is the oracle: the OpenAI wire, decoded by Usage.UnmarshalJSON.
var upInt2Adapters = []upInt2Adapter{
	{"openai", "openai", "acme-native-token", "/chat/completions"},
	{"azure", "azure", "acme-native-token", "/chat/completions"},
	{"anthropic", "anthropic", "acme-native-token", "/v1/messages"},
	{"gemini", "gemini", "acme-native-token", ":generateContent"},
	{"cohere", "cohere", "acme-native-token", "/v2/chat"},
	{"bedrock-invoke", "bedrock", "acme-native-token", "/invoke"},
	{"bedrock-converse", "bedrock", "us.acme-native-token", "/converse"},
}

// upInt2Body is a successful non-streamed answer of upInt2Text in a's wire
// format, with usage as its usage member ("" omits the member).
func upInt2Body(a upInt2Adapter, usage string) string {
	member := func(name string) string {
		if usage == "" {
			return ""
		}
		return fmt.Sprintf(",%q:%s", name, usage)
	}
	switch a.name {
	case "openai", "azure":
		return `{"id":"c1","object":"chat.completion","model":"` + a.model + `","choices":[{"index":0,"message":{"role":"assistant","content":"` +
			upInt2Text + `"},"finish_reason":"stop"}]` + member("usage") + `}`
	case "anthropic", "bedrock-invoke":
		return `{"id":"msg_1","type":"message","role":"assistant","model":"` + a.model + `","content":[{"type":"text","text":"` +
			upInt2Text + `"}],"stop_reason":"end_turn"` + member("usage") + `}`
	case "gemini":
		return `{"candidates":[{"content":{"role":"model","parts":[{"text":"` + upInt2Text + `"}]},"finishReason":"STOP"}]` +
			member("usageMetadata") + `}`
	case "cohere":
		return `{"id":"c1","finish_reason":"COMPLETE","message":{"role":"assistant","content":[{"type":"text","text":"` +
			upInt2Text + `"}]}` + member("usage") + `}`
	case "bedrock-converse":
		return `{"output":{"message":{"role":"assistant","content":[{"text":"` + upInt2Text + `"}]}},"stopReason":"end_turn"` +
			member("usage") + `}`
	}
	panic(a.name)
}

// upInt2Case is one piece of usage evidence: on the OpenAI wire (oracle) and
// in each native format. A format absent from native skips the case.
type upInt2Case struct {
	name   string
	oracle string
	native map[string]string
}

var upInt2Cases = []upInt2Case{
	{"absent", "", map[string]string{"azure": "", "anthropic": "", "gemini": "", "cohere": "", "bedrock-invoke": "", "bedrock-converse": ""}},
	{"null", "null", map[string]string{"azure": "null", "anthropic": "null", "gemini": "null", "cohere": "null", "bedrock-invoke": "null", "bedrock-converse": "null"}},
	{"empty", "{}", map[string]string{"azure": "{}", "anthropic": "{}", "gemini": "{}", "cohere": "{}", "bedrock-invoke": "{}", "bedrock-converse": "{}"}},
	{"cohere-empty-tokens", "{}", map[string]string{"cohere": `{"tokens":{}}`}},
	{"prompt-only", `{"prompt_tokens":1000}`, map[string]string{
		"azure": `{"prompt_tokens":1000}`, "anthropic": `{"input_tokens":1000}`, "gemini": `{"promptTokenCount":1000}`,
		"cohere": `{"tokens":{"input_tokens":1000}}`, "bedrock-invoke": `{"input_tokens":1000}`, "bedrock-converse": `{"inputTokens":1000}`}},
	{"cohere-billed-prompt-only", `{"prompt_tokens":1000}`, map[string]string{"cohere": `{"billed_units":{"input_tokens":1000}}`}},
	{"output-only", `{"completion_tokens":500}`, map[string]string{
		"azure": `{"completion_tokens":500}`, "anthropic": `{"output_tokens":500}`, "gemini": `{"candidatesTokenCount":500}`,
		"cohere": `{"tokens":{"output_tokens":500}}`, "bedrock-invoke": `{"output_tokens":500}`, "bedrock-converse": `{"outputTokens":500}`}},
	{"total-only", `{"total_tokens":1500}`, map[string]string{
		"azure": `{"total_tokens":1500}`, "gemini": `{"totalTokenCount":1500}`, "bedrock-converse": `{"totalTokens":1500}`}},
	{"complete", `{"prompt_tokens":10,"completion_tokens":5}`, map[string]string{
		"azure": `{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}`, "anthropic": `{"input_tokens":10,"output_tokens":5}`,
		"gemini": `{"promptTokenCount":10,"candidatesTokenCount":5,"totalTokenCount":15}`, "cohere": `{"tokens":{"input_tokens":10,"output_tokens":5}}`,
		"bedrock-invoke": `{"input_tokens":10,"output_tokens":5}`, "bedrock-converse": `{"inputTokens":10,"outputTokens":5,"totalTokens":15}`}},
	{"explicit-zero", `{"prompt_tokens":0,"completion_tokens":0}`, map[string]string{
		"azure": `{"prompt_tokens":0,"completion_tokens":0}`, "anthropic": `{"input_tokens":0,"output_tokens":0}`,
		"gemini": `{"promptTokenCount":0,"candidatesTokenCount":0,"totalTokenCount":0}`, "cohere": `{"tokens":{"input_tokens":0,"output_tokens":0}}`,
		"bedrock-invoke": `{"input_tokens":0,"output_tokens":0}`, "bedrock-converse": `{"inputTokens":0,"outputTokens":0,"totalTokens":0}`}},
}

// upInt2Gateway is newManagedTestGatewayWith's gateway (same plugins, same
// in-memory managed ledger, same price wiring), with one managed source
// built from config as a direct-provider price-card source in a's format.
func upInt2Gateway(t *testing.T, a upInt2Adapter, upstreamURL string, extra ...pipeline.Plugin) *managedTestGateway {
	t.Helper()
	off := false
	cfg := config.DefaultConfig()
	cfg.Admin.Token = adminToken
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{
		Name: "org-user", Key: managedOrgKey, Owner: "user", KeyType: "byok",
		Metadata: map[string]string{"org_id": "org-m"},
	}}
	pc := config.ProviderConfig{BaseURL: upstreamURL, APIKey: "synthetic", APIFormat: a.format,
		Managed: true, CatalogSource: catalog.SourcePriceCard, AutoDiscover: &off}
	if a.format == "bedrock" {
		pc.APIKey = ""
		pc.AWSAccessKeyID, pc.AWSSecretAccessKey, pc.AWSRegion = "SYNTHETIC", "synthetic", "us-east-1"
	}
	cfg.Providers[upInt2Provider] = pc
	cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}

	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	if !registry.IsManaged(upInt2Provider) {
		t.Fatalf("%s is not a managed provider", upInt2Provider)
	}
	card, err := sources.ParsePriceCard([]byte(fmt.Sprintf(`{"provider":%q,"upstream_slug":"acme","upstream_fee_bps":0,`+
		`"as_of":%q,"source_url":"https://pricing.example.test/acme","data_collection":"deny",`+
		`"models":[{"id":%q,"context_length":200000,"max_output":64000,"prices":{"prompt":"0.000002","completion":"0.00001"}}]}`,
		upInt2Provider, time.Now().UTC().Format(time.DateOnly), a.model)))
	if err != nil {
		t.Fatal(err)
	}
	store := catalog.NewStore()
	store.Put(card.Snapshot(upInt2Provider, time.Now()))

	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(BuildModelDB(nil, store))
	tenants := tenant.NewStore()
	tenants.Set("org-m", managedOptIn())

	keyStore := auth.NewKeyStore(cfg.Auth)
	ledger := creditsplugin.NewOrgLedger(nil)
	cost := costplugin.New(true, mdb.Load, tenants, nil)
	cost.SetManagedMarkup(5)
	credits := creditsplugin.New(true, keyStore)
	credits.SetOrgLedger(ledger, true)

	srv := New(cfg, "", registry, pipeline.NewEngine(append([]pipeline.Plugin{cost, credits}, extra...)...), keyStore, nil, nil, nil, &mdb, tenants, nil)
	releaseTestServer(t, srv)
	srv.EnableManaged(ManagedSettings{Catalog: store, Ledger: ledger, MarkupPercent: 5, RequireCredits: true})
	h := srv.handlers
	deps := *h.Deps()
	deps.Prices = store.PriceLookup(deps.Prices)
	h.SetInjectedDeps(deps)
	cost.SetPricer(costplugin.NewPricer(h.ModelDB, func() models.PriceLookup { return h.Deps().Prices }))
	credits.SetPrices(func(providerID, model, endpointID string) (models.PriceCard, bool) {
		return h.Deps().Prices(providerID, model, endpointID)
	})
	credits.SetOrgMaxTokens(h.OrgDefaultMaxTokensEstimate)
	h.SetBilling(credits)
	srv.ready.Store(true)
	ledger.SetMicros("org-m", 1_000_000)
	return &managedTestGateway{srv: srv, ledger: ledger, credits: credits, tenants: tenants}
}

type upInt2Result struct {
	status, calls int
	native        bool
	mode, body    string
	spent, held   int64
	records       []map[string]string
}

// upInt2Run sends one managed request on surface to a gateway whose managed
// source speaks a's format, answered with usage (or by script when set).
func upInt2Run(t *testing.T, a upInt2Adapter, surface string, stream bool, usage string, script func(http.ResponseWriter)) upInt2Result {
	t.Helper()
	var calls atomic.Int32
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || !containsPath(r.URL.Path, a.path) {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_, _ = io.Copy(io.Discard, r.Body)
		calls.Add(1)
		if script != nil {
			script(w)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, upInt2Body(a, usage))
	}))
	defer up.Close()
	g := upInt2Gateway(t, a, up.URL)
	status, body, header := sendManaged(t, g, surface, a.model, stream, false, nil)
	spent, held := settledAccount(t, g)
	return upInt2Result{status: status, calls: int(calls.Load()), native: a.format != "openai" && a.format != "azure", mode: header.Get("x-agentcc-billing-mode"), body: body,
		spent: spent, held: held, records: g.outbox("org-m")}
}

func containsPath(path, want string) bool {
	for i := 0; i+len(want) <= len(path); i++ {
		if path[i:i+len(want)] == want {
			return true
		}
	}
	return false
}

// upInt2Served is the dispatch control: the request reached the synthetic
// upstream once, was answered, was billed to the org's managed credit
// (billing mode managed, one ledger record for the managed source) and left
// nothing held. A failure here is a harness failure, never a verdict.
func upInt2Served(t *testing.T, who string, r upInt2Result) {
	t.Helper()
	if r.status != http.StatusOK || r.calls != 1 || r.mode != models.BillingModeManaged || r.held != 0 ||
		len(r.records) != 1 || r.records[0]["provider"] != upInt2Provider {
		t.Fatalf("%s dispatch control failed: status=%d calls=%d mode=%q held=%d records=%v body=%.400s",
			who, r.status, r.calls, r.mode, r.held, r.records, r.body)
	}
}

func upInt2Compare(t *testing.T, got, want upInt2Result) {
	t.Helper()
	upInt2Served(t, "native", got)
	g, w := got.records[0], want.records[0]
	t.Logf("VERDICT native spent=%d est=%s kind=%s billed=%s hold=%s source=%s | oracle spent=%d est=%s kind=%s billed=%s source=%s",
		got.spent, g["usage_estimated"], g["kind"], g["billed_micros"], g["hold_micros"], g["cost_source"],
		want.spent, w["usage_estimated"], w["kind"], w["billed_micros"], w["cost_source"])
	// Addendum 9 strengthens only native incomplete settlement: the same
	// evidence/provenance now has a full-hold monetary floor. OpenAI/Azure
	// and complete/explicit-zero controls keep their original exact oracle.
	if got.native && w["usage_estimated"] == "1" {
		hold, err := strconv.ParseInt(g["hold_micros"], 10, 64)
		if err != nil || hold <= 0 {
			t.Fatalf("invalid native hold: %v", g)
		}
		want.spent = max(want.spent, hold)
	}
	if got.spent != want.spent || g["usage_estimated"] != w["usage_estimated"] {
		t.Errorf("AFFECTED: managed credits settled %d micros (usage_estimated=%s kind=%s) where the same evidence on the OpenAI wire settles %d (usage_estimated=%s kind=%s)",
			got.spent, g["usage_estimated"], g["kind"], want.spent, w["usage_estimated"], w["kind"])
	}
}

func upInt2SurfaceName(surface string) string {
	if surface == "/v1/messages" {
		return "messages"
	}
	return "chat"
}

func TestUsagePresenceInt2NativeManaged(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, c := range upInt2Cases {
			prefix := upInt2SurfaceName(surface) + "/" + c.name
			var oracle upInt2Result
			if !t.Run(prefix+"/oracle-openai-wire", func(t *testing.T) {
				oracle = upInt2Run(t, upInt2Adapters[0], surface, false, c.oracle, nil)
				upInt2Served(t, "oracle", oracle)
				r := oracle.records[0]
				t.Logf("ORACLE spent=%d est=%s kind=%s billed=%s source=%s", oracle.spent, r["usage_estimated"], r["kind"], r["billed_micros"], r["cost_source"])
			}) {
				continue
			}
			for _, a := range upInt2Adapters[1:] {
				usage, ok := c.native[a.name]
				if !ok {
					continue
				}
				t.Run(prefix+"/"+a.name, func(t *testing.T) {
					upInt2Compare(t, upInt2Run(t, a, surface, false, usage, nil), oracle)
				})
			}
		}
	}
}

// upInt2AnthropicSSE streams upInt2Text as Anthropic's Messages stream, with
// start as message_start's usage and delta as message_delta's ("" omits it).
func upInt2AnthropicSSE(start, delta string) func(http.ResponseWriter) {
	return func(w http.ResponseWriter) {
		startUsage, deltaUsage := "", ""
		if start != "" {
			startUsage = `,"usage":` + start
		}
		if delta != "" {
			deltaUsage = `,"usage":` + delta
		}
		w.Header().Set("Content-Type", "text/event-stream")
		for _, e := range []struct{ event, data string }{
			{"message_start", `{"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"acme-native-token","content":[]` + startUsage + `}}`},
			{"content_block_start", `{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`},
			{"content_block_delta", `{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"` + upInt2Text + `"}}`},
			{"content_block_stop", `{"type":"content_block_stop","index":0}`},
			{"message_delta", `{"type":"message_delta","delta":{"stop_reason":"end_turn"}` + deltaUsage + `}`},
			{"message_stop", `{"type":"message_stop"}`},
		} {
			fmt.Fprintf(w, "event: %s\ndata: %s\n\n", e.event, e.data)
			w.(http.Flusher).Flush()
		}
	}
}

// upInt2OpenAISSE streams upInt2Text on the OpenAI wire with usage on the
// finishing chunk ("" omits it).
func upInt2OpenAISSE(usage string) func(http.ResponseWriter) {
	return func(w http.ResponseWriter) {
		u := ""
		if usage != "" {
			u = `,"usage":` + usage
		}
		w.Header().Set("Content-Type", "text/event-stream")
		writeManagedChunk(w, "acme-native-token", `"choices":[{"index":0,"delta":{"role":"assistant","content":"`+upInt2Text+`"}}]`)
		writeManagedChunk(w, "acme-native-token", `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`+u)
		fmt.Fprint(w, "data: [DONE]\n\n")
	}
}

func TestUsagePresenceInt2NativeManagedStream(t *testing.T) {
	anthropic := upInt2Adapters[2]
	for _, surface := range managedSurfaces {
		for _, c := range []struct{ name, start, delta, oracle string }{
			{"start-input-delta-empty", `{"input_tokens":1000}`, `{}`, `{"prompt_tokens":1000}`},
			{"delta-empty", "", `{}`, `{}`},
			{"delta-output-only", "", `{"output_tokens":5}`, `{"completion_tokens":5}`},
			{"delta-null", "", `null`, `null`},
			{"complete", `{"input_tokens":10}`, `{"output_tokens":5}`, `{"prompt_tokens":10,"completion_tokens":5}`},
			{"no-delta-usage", `{"input_tokens":1000}`, "", `{"prompt_tokens":1000}`},
		} {
			prefix := upInt2SurfaceName(surface) + "/stream/" + c.name
			var oracle upInt2Result
			if !t.Run(prefix+"/oracle-openai-wire", func(t *testing.T) {
				oracle = upInt2Run(t, upInt2Adapters[0], surface, true, "", upInt2OpenAISSE(c.oracle))
				upInt2Served(t, "oracle", oracle)
				r := oracle.records[0]
				t.Logf("ORACLE spent=%d est=%s kind=%s billed=%s source=%s", oracle.spent, r["usage_estimated"], r["kind"], r["billed_micros"], r["cost_source"])
			}) {
				continue
			}
			t.Run(prefix+"/anthropic", func(t *testing.T) {
				upInt2Compare(t, upInt2Run(t, anthropic, surface, true, "", upInt2AnthropicSSE(c.start, c.delta)), oracle)
			})
		}
	}
}
