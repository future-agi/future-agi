package server

// An org key asking for "<provider>/<host id>" is served by the org's own
// provider for that ID, matched by orgModelMatches on the id the org
// registered. The gateway sends the host the id it takes, the registered
// one: an OpenAI-format host (openai, fireworks, together, groq, mistral,
// cohere, openrouter) refuses "openai/gpt-4o-mini". Every adapter, on every
// route, sends the registered id with the matched prefix cut exactly once.
//
// These tests drive the real handlers, the org provider cache and the real
// adapters against loopback hosts that know only their own ids and refuse any
// other, as a real host does, and record the id each call named.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	okOrg    = "org-ownkey"
	okOrgKey = "sk-ownkey-org"

	// okARN is a Bedrock application inference profile: its id contains a
	// "/", so an adapter that drops a first segment must see it exactly once.
	okARN = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abc123def456"

	// okTogether has two price entries in okModelDB: the provider-qualified
	// one, which is the right one, and a bare one at another price.
	okTogether = "meta-llama/Llama-3.3-70B-Instruct-Turbo"

	// okDown is a Fireworks id its host knows but fails (502), for the model
	// fallback cases.
	okDown = "accounts/fireworks/models/down"
)

// okProviders are the org's own providers: each has a config.yaml entry of
// the same ID on a loopback host of its own (whose base_url the org's
// instance inherits; the operator's key is never used for an org key) and an
// org entry with the org's key and the ids it registered. No two of them
// match the same requested model, so the order orgProviderFor reads the org's
// providers in (a map) cannot decide a case.
var okProviders = map[string]struct {
	format string
	ids    []string
}{
	"openai":    {"openai", []string{"gpt-4o-mini", "gpt-3.5-turbo-instruct", "text-embedding-3-small", "gpt-image-2", "tts-1", "whisper-1"}},
	"mistral":   {"openai", []string{"mistral-ocr-2512"}},
	"fireworks": {"openai", []string{"accounts/fireworks/models/llama-v3p1-8b-instruct", okDown}},
	"together":  {"openai", []string{okTogether}},
	"groq":      {"openai", []string{"openai/gpt-oss-120b"}}, // a Groq id with a slash of its own
	"anthropic": {"anthropic", []string{"claude-haiku-4-5"}},
	"myrouter":  {"anthropic", []string{"anthropic/claude-sonnet-4"}}, // an Anthropic-format host whose id has a slash
	"bedrock":   {"bedrock", []string{okARN, "us.anthropic.claude-haiku-4-5-20251001-v1:0"}},
	"cohere":    {"cohere", []string{"rerank-english-v3.0"}},
	"gemini":    {"gemini", []string{"gemini-2.0-flash"}},
}

// okHost is one loopback host. It answers each wire format the way its real
// host does, refuses (404 model_not_found) any id it does not know, and keeps
// every call's path, the id it named and its credential.
type okHost struct {
	*httptest.Server
	id    string
	knows map[string]bool
	mu    sync.Mutex
	seen  []string // "<path> <id>"
	creds []string
}

func startOKHost(t *testing.T, id string, ids []string) *okHost {
	t.Helper()
	h := &okHost{id: id, knows: map[string]bool{}}
	for _, m := range ids {
		h.knows[m] = true
	}
	h.Server = httptest.NewServer(http.HandlerFunc(h.serve))
	t.Cleanup(h.Close)
	return h
}

func (h *okHost) serve(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost { // model discovery and the start-up connectivity probe
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"object":"list","data":[]}`)
		return
	}
	path, model, stream := okRequestModel(r)
	cred := r.Header.Get("Authorization") + r.Header.Get("x-api-key") + r.Header.Get("x-goog-api-key")
	h.mu.Lock()
	h.seen = append(h.seen, path+" "+model)
	h.creds = append(h.creds, cred)
	h.mu.Unlock()
	if !h.knows[model] {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		fmt.Fprintf(w, `{"error":{"message":"The model %q does not exist","type":"invalid_request_error","code":"model_not_found"}}`, model)
		return
	}
	if model == okDown {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		io.WriteString(w, `{"error":{"message":"overloaded","type":"server_error"}}`)
		return
	}
	okAnswer(w, path, model, stream)
}

// okRequestModel is the path a call reached and the id it named: in the JSON
// body, the multipart form, a Bedrock /model/{id}/... path or a Gemini
// /v1beta/models/{id}:action path. For the two path forms, the path is
// recorded with the id cut out.
func okRequestModel(r *http.Request) (path, model string, stream bool) {
	path = r.URL.Path
	if rest, ok := strings.CutPrefix(r.URL.EscapedPath(), "/model/"); ok {
		seg, action, _ := strings.Cut(rest, "/")
		model, _ = url.PathUnescape(seg)
		return "/model/{id}/" + action, model, strings.HasSuffix(action, "-stream")
	}
	if rest, ok := strings.CutPrefix(r.URL.Path, "/v1beta/models/"); ok {
		i := strings.LastIndex(rest, ":")
		if i > 0 {
			return "/v1beta/models/{id}:" + rest[i+1:], rest[:i], strings.HasPrefix(rest[i+1:], "stream")
		}
	}
	if strings.HasPrefix(r.Header.Get("Content-Type"), "multipart/") {
		if err := r.ParseMultipartForm(32 << 20); err == nil {
			model = r.FormValue("model")
		}
		return path, model, false
	}
	var body struct {
		Model  string `json:"model"`
		Stream bool   `json:"stream"`
	}
	_ = json.NewDecoder(r.Body).Decode(&body)
	return path, body.Model, body.Stream
}

// okUsage is 1,000 tokens in and 1,000 out: big enough that a price per
// token shows in x-agentcc-cost's six decimals.
const okUsage = `"usage":{"prompt_tokens":1000,"completion_tokens":1000,"total_tokens":2000}`

func okAnswer(w http.ResponseWriter, path, model string, stream bool) {
	ct := "application/json"
	var body string
	switch path {
	case "/v1/chat/completions":
		if stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"ok\"},\"finish_reason\":null}]}\n\n", model)
			fmt.Fprintf(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],%s}\n\n", model, okUsage)
			io.WriteString(w, "data: [DONE]\n\n")
			return
		}
		body = fmt.Sprintf(`{"id":"c1","object":"chat.completion","created":1,"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}],%s}`, model, okUsage)
	case "/v1/completions":
		body = fmt.Sprintf(`{"id":"t1","object":"text_completion","created":1,"model":%q,"choices":[{"index":0,"text":"ok","finish_reason":"stop"}],%s}`, model, okUsage)
	case "/v1/embeddings":
		body = fmt.Sprintf(`{"object":"list","model":%q,"data":[{"object":"embedding","index":0,"embedding":[0.1,0.2]}],"usage":{"prompt_tokens":1000,"total_tokens":1000}}`, model)
	case "/v1/responses":
		body = fmt.Sprintf(`{"id":"resp_1","object":"response","status":"completed","model":%q,"output":[],"usage":{"input_tokens":1000,"output_tokens":1000,"total_tokens":2000}}`, model)
	case "/v1/images/generations":
		body = `{"created":1760000000,"data":[{"url":"https://images.example.com/ok.png"}]}`
	case "/v1/audio/speech":
		ct, body = "audio/mpeg", "not-really-audio"
	case "/v1/audio/transcriptions", "/v1/audio/translations":
		body = `{"text":"hello"}`
	case "/v1/ocr":
		body = ocrUpstreamBody
	case "/v1/messages":
		body = fmt.Sprintf(`{"id":"msg_1","type":"message","role":"assistant","model":%q,"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":1000,"output_tokens":1000}}`, model)
	case "/v1/messages/count_tokens":
		body = `{"input_tokens":5}`
	case "/v2/rerank":
		body = `{"id":"rr","results":[{"index":1,"relevance_score":0.9},{"index":0,"relevance_score":0.2}],"meta":{"billed_units":{"search_units":1}}}`
	case "/model/{id}/converse":
		body = `{"output":{"message":{"role":"assistant","content":[{"text":"ok"}]}},"stopReason":"end_turn","usage":{"inputTokens":1000,"outputTokens":1000,"totalTokens":2000}}`
	case "/model/{id}/invoke":
		body = fmt.Sprintf(`{"id":"msg_1","type":"message","role":"assistant","model":%q,"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1000,"output_tokens":1000}}`, model)
	case "/v1beta/models/{id}:generateContent":
		body = fmt.Sprintf(`{"candidates":[{"content":{"role":"model","parts":[{"text":"ok"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1000,"candidatesTokenCount":1000,"totalTokenCount":2000},"modelVersion":%q}`, model)
	default:
		w.WriteHeader(http.StatusNotFound)
		body = `{"error":{"message":"no such path","type":"invalid_request_error"}}`
	}
	w.Header().Set("Content-Type", ct)
	io.WriteString(w, body)
}

func (h *okHost) calls() (seen, creds []string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	return append([]string(nil), h.seen...), append([]string(nil), h.creds...)
}

// okLogProbe keeps what the request log reads off rc, after the cost plugin.
type okLogProbe struct {
	mu       sync.Mutex
	model    string
	meta     map[string]string
	attempts []string // "<provider> <model>"
}

func (p *okLogProbe) Name() string      { return "ownkey-probe" }
func (p *okLogProbe) Priority() int     { return 900 }
func (p *okLogProbe) PostPriority() int { return 900 }
func (p *okLogProbe) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *okLogProbe) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.model = rc.Model
	p.meta = make(map[string]string, len(rc.Metadata))
	for k, v := range rc.Metadata {
		p.meta[k] = v
	}
	p.attempts = nil
	for _, a := range rc.AttemptsSnapshot() {
		p.attempts = append(p.attempts, a.Provider+" "+a.Model)
	}
	return pipeline.ResultContinue()
}

// okGateway is a gateway with auth on, one org key, the org's providers
// (okProviders), the cost plugin, the probe and request logging to a sink.
type okGateway struct {
	srv     *Server
	hosts   map[string]*okHost
	probe   *okLogProbe
	logs    *capcoreLogSink
	flusher *loggingplugin.LogFlusher
}

// okModelDB is the bundled model database plus okTogether's two prices: the
// "together/" entry is $2 and $3 a million tokens, the bare one $9 and $9.
func okModelDB() *atomic.Pointer[modeldb.ModelDB] {
	db := modeldb.New(modeldb.WithExtra(modeldb.BundledModels, map[string]*modeldb.ModelInfo{
		"together/" + okTogether: {ID: "together/" + okTogether, Provider: "together", Mode: modeldb.ModeChat,
			Pricing: modeldb.PricingInfo{InputPerToken: 2e-6, OutputPerToken: 3e-6}},
		okTogether: {ID: okTogether, Mode: modeldb.ModeChat,
			Pricing: modeldb.PricingInfo{InputPerToken: 9e-6, OutputPerToken: 9e-6}},
	}), nil)
	var p atomic.Pointer[modeldb.ModelDB]
	p.Store(db)
	return &p
}

// newOKGateway builds the gateway; mutate, when set, may change the config
// and the org's config before it is built.
func newOKGateway(t *testing.T, mutate ...func(*config.Config, *tenant.OrgConfig)) *okGateway {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "org", Key: okOrgKey, Owner: "user", KeyType: "byok", Metadata: map[string]string{"org_id": okOrg}}}
	g := &okGateway{hosts: map[string]*okHost{}, probe: &okLogProbe{}, logs: startCapcoreLogSink(t)}
	orgCfg := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{}}
	for id, p := range okProviders {
		h := startOKHost(t, id, p.ids)
		g.hosts[id] = h
		pc := config.ProviderConfig{BaseURL: h.URL, APIKey: "operator-" + id, APIFormat: p.format}
		org := &tenant.ProviderConfig{Enabled: true, APIKey: "org-" + id, Models: p.ids}
		if p.format == "bedrock" {
			pc.APIKey, pc.AWSRegion = "", "us-east-1"
			pc.AWSAccessKeyID, pc.AWSSecretAccessKey = "AKIAOPERATOR", "operator-secret"
			org.APIKey, org.AWSAccessKeyID, org.AWSSecretAccessKey, org.AWSRegion = "", "AKIAORGOWNKEY", "org-secret", "us-east-1"
		}
		cfg.Providers[id] = pc
		orgCfg.Providers[id] = org
	}
	for _, m := range mutate {
		m(cfg, orgCfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	t.Cleanup(func() { registry.Close() })
	keyStore := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	tenants.Set(okOrg, orgCfg)
	mdb := okModelDB()
	g.flusher = loggingplugin.NewLogFlusher(g.logs.URL, "", time.Hour, 100)
	logPlugin := loggingplugin.New(config.RequestLoggingConfig{Enabled: true}, nil)
	logPlugin.SetFlusher(g.flusher)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), costplugin.New(true, mdb.Load, tenants, nil), g.probe, logPlugin)
	g.srv = New(cfg, "", registry, engine, keyStore, nil, nil, nil, mdb, tenants, nil)
	g.srv.ready.Store(true)
	g.srv.handlers.failover.Store(nil)
	return g
}

func (g *okGateway) send(t *testing.T, path, contentType string, body []byte) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(body))
	req.Header.Set("Content-Type", contentType)
	if strings.HasPrefix(path, "/v1beta/") {
		req.Header.Set("x-goog-api-key", okOrgKey)
	} else {
		req.Header.Set("Authorization", "Bearer "+okOrgKey)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// logged closes the flusher and returns the one log entry the sink got.
func (g *okGateway) logged(t *testing.T) map[string]any {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	g.flusher.Close(ctx)
	g.logs.mu.Lock()
	defer g.logs.mu.Unlock()
	if len(g.logs.entries) != 1 {
		t.Fatalf("log entries = %d, want 1: %v", len(g.logs.entries), g.logs.entries)
	}
	return g.logs.entries[0]
}

func okJSON(format string) func(*testing.T, string) (string, []byte) {
	return func(_ *testing.T, model string) (string, []byte) {
		return "application/json", []byte(strings.Replace(format, "@MODEL@", model, 1))
	}
}

const (
	okChat     = `{"model":"@MODEL@","messages":[{"role":"user","content":"hi"}]}`
	okChatSSE  = `{"model":"@MODEL@","stream":true,"messages":[{"role":"user","content":"hi"}]}`
	okMessages = `{"model":"@MODEL@","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}`
	okGenAI    = `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`
)

// okCase is one call: the caller asks route for model; host must receive
// the call at hostPath naming want.
type okCase struct {
	name, route, model string
	body               func(*testing.T, string) (string, []byte)
	host, hostPath     string
	want               string
	cost               string // x-agentcc-cost, when the case checks pricing
}

var okCases = []okCase{
	// OpenAI-format hosts: the bare host id, on every route.
	{name: "openai chat", route: "/v1/chat/completions", model: "openai/gpt-4o-mini", body: okJSON(okChat),
		host: "openai", hostPath: "/v1/chat/completions", want: "gpt-4o-mini",
		cost: "0.000750"}, // gpt-4o-mini: $0.15 and $0.60 a million tokens
	{name: "openai chat stream", route: "/v1/chat/completions", model: "openai/gpt-4o-mini", body: okJSON(okChatSSE),
		host: "openai", hostPath: "/v1/chat/completions", want: "gpt-4o-mini"},
	{name: "fireworks chat", route: "/v1/chat/completions", model: "fireworks/accounts/fireworks/models/llama-v3p1-8b-instruct", body: okJSON(okChat),
		host: "fireworks", hostPath: "/v1/chat/completions", want: "accounts/fireworks/models/llama-v3p1-8b-instruct"},
	{name: "together chat", route: "/v1/chat/completions", model: "together/" + okTogether, body: okJSON(okChat),
		host: "together", hostPath: "/v1/chat/completions", want: okTogether,
		cost: "0.005000"}, // the together/ entry; the bare entry would be 0.018000
	{name: "together chat stream", route: "/v1/chat/completions", model: "together/" + okTogether, body: okJSON(okChatSSE),
		host: "together", hostPath: "/v1/chat/completions", want: okTogether},
	{name: "groq chat, prefixed slash id", route: "/v1/chat/completions", model: "groq/openai/gpt-oss-120b", body: okJSON(okChat),
		host: "groq", hostPath: "/v1/chat/completions", want: "openai/gpt-oss-120b"},
	{name: "groq chat, registered slash id as is", route: "/v1/chat/completions", model: "openai/gpt-oss-120b", body: okJSON(okChat),
		host: "groq", hostPath: "/v1/chat/completions", want: "openai/gpt-oss-120b"},
	{name: "openai chat, bare id", route: "/v1/chat/completions", model: "gpt-4o-mini", body: okJSON(okChat),
		host: "openai", hostPath: "/v1/chat/completions", want: "gpt-4o-mini"},
	{name: "openai completions", route: "/v1/completions", model: "openai/gpt-3.5-turbo-instruct", body: okJSON(`{"model":"@MODEL@","prompt":"hi"}`),
		host: "openai", hostPath: "/v1/completions", want: "gpt-3.5-turbo-instruct"},
	{name: "openai embeddings", route: "/v1/embeddings", model: "openai/text-embedding-3-small", body: okJSON(`{"model":"@MODEL@","input":"hi"}`),
		host: "openai", hostPath: "/v1/embeddings", want: "text-embedding-3-small"},
	{name: "openai responses", route: "/v1/responses", model: "openai/gpt-4o-mini", body: okJSON(`{"model":"@MODEL@","input":"hi","store":false}`),
		host: "openai", hostPath: "/v1/responses", want: "gpt-4o-mini"},
	{name: "openai images", route: "/v1/images/generations", model: "openai/gpt-image-2", body: okJSON(`{"model":"@MODEL@","prompt":"a lighthouse"}`),
		host: "openai", hostPath: "/v1/images/generations", want: "gpt-image-2"},
	{name: "openai speech", route: "/v1/audio/speech", model: "openai/tts-1", body: okJSON(`{"model":"@MODEL@","input":"hello","voice":"alloy"}`),
		host: "openai", hostPath: "/v1/audio/speech", want: "tts-1"},
	{name: "openai speech stream", route: "/v1/audio/speech/stream", model: "openai/tts-1", body: okJSON(`{"model":"@MODEL@","input":"hello","voice":"alloy"}`),
		host: "openai", hostPath: "/v1/audio/speech", want: "tts-1"},
	{name: "openai transcription", route: "/v1/audio/transcriptions", model: "openai/whisper-1", body: nonChatAudioForm,
		host: "openai", hostPath: "/v1/audio/transcriptions", want: "whisper-1"},
	{name: "openai translation", route: "/v1/audio/translations", model: "openai/whisper-1", body: nonChatAudioForm,
		host: "openai", hostPath: "/v1/audio/translations", want: "whisper-1"},
	{name: "mistral ocr", route: "/v1/ocr", model: "mistral/mistral-ocr-2512",
		body: okJSON(`{"model":"@MODEL@","document":{"type":"document_url","document_url":"https://docs.example.com/a.pdf"}}`),
		host: "mistral", hostPath: "/v1/ocr", want: "mistral-ocr-2512"},
	{name: "openai via /v1/messages", route: "/v1/messages", model: "openai/gpt-4o-mini", body: okJSON(okMessages),
		host: "openai", hostPath: "/v1/chat/completions", want: "gpt-4o-mini"},
	{name: "openai via the GenAI API", route: "/v1beta/models/openai/gpt-4o-mini:generateContent", model: "openai/gpt-4o-mini", body: okJSON(okGenAI),
		host: "openai", hostPath: "/v1/chat/completions", want: "gpt-4o-mini"},

	// Anthropic-format hosts. The adapter's chat translation drops a first
	// segment itself: a bare id must reach the host as it is, and an id with
	// a slash of its own must lose only the matched prefix, never a second
	// segment. The native /v1/messages routes send the body as written.
	{name: "anthropic chat", route: "/v1/chat/completions", model: "anthropic/claude-haiku-4-5", body: okJSON(okChat),
		host: "anthropic", hostPath: "/v1/messages", want: "claude-haiku-4-5"},
	{name: "anthropic messages", route: "/v1/messages", model: "anthropic/claude-haiku-4-5", body: okJSON(okMessages),
		host: "anthropic", hostPath: "/v1/messages", want: "claude-haiku-4-5"},
	{name: "anthropic count_tokens", route: "/v1/messages/count_tokens", model: "anthropic/claude-haiku-4-5", body: okJSON(okMessages),
		host: "anthropic", hostPath: "/v1/messages/count_tokens", want: "claude-haiku-4-5"},
	{name: "anthropic-format slash id, chat", route: "/v1/chat/completions", model: "myrouter/anthropic/claude-sonnet-4", body: okJSON(okChat),
		host: "myrouter", hostPath: "/v1/messages", want: "anthropic/claude-sonnet-4"},
	{name: "anthropic-format slash id, messages", route: "/v1/messages", model: "myrouter/anthropic/claude-sonnet-4", body: okJSON(okMessages),
		host: "myrouter", hostPath: "/v1/messages", want: "anthropic/claude-sonnet-4"},

	// Bedrock drops a first segment too: an ARN, whose id has a slash, must
	// reach Bedrock whole; a bare inference profile id as it is.
	{name: "bedrock ARN chat", route: "/v1/chat/completions", model: "bedrock/" + okARN, body: okJSON(okChat),
		host: "bedrock", hostPath: "/model/{id}/converse", want: okARN},
	{name: "bedrock profile chat", route: "/v1/chat/completions", model: "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0", body: okJSON(okChat),
		host: "bedrock", hostPath: "/model/{id}/converse", want: "us.anthropic.claude-haiku-4-5-20251001-v1:0"},

	// Cohere (rerank) and Gemini (the native GenAI API, which names the
	// model in the URL).
	{name: "cohere rerank", route: "/v1/rerank", model: "cohere/rerank-english-v3.0",
		body: okJSON(`{"model":"@MODEL@","query":"q","documents":["a","b"]}`),
		host: "cohere", hostPath: "/v2/rerank", want: "rerank-english-v3.0"},
	{name: "gemini native", route: "/v1beta/models/gemini/gemini-2.0-flash:generateContent", model: "gemini/gemini-2.0-flash", body: okJSON(okGenAI),
		host: "gemini", hostPath: "/v1beta/models/{id}:generateContent", want: "gemini-2.0-flash"},
}

func okCaseNamed(t *testing.T, name string) okCase {
	t.Helper()
	for _, c := range okCases {
		if c.name == name {
			return c
		}
	}
	t.Fatalf("no case %q", name)
	return okCase{}
}

// Every own-key call reaches the org's host once, with the org's credential,
// naming the id the host takes; no other host is called.
func TestOwnKeyPrefixedModelReachesTheHostAsItsOwnID(t *testing.T) {
	for _, c := range okCases {
		t.Run(c.name, func(t *testing.T) {
			g := newOKGateway(t)
			ct, body := c.body(t, c.model)
			w := g.send(t, c.route, ct, body)
			seen, creds := g.hosts[c.host].calls()
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d %s; %s received %q, want one %s naming %q", w.Code, w.Body.String(), c.host, seen, c.hostPath, c.want)
			}
			if len(seen) != 1 || seen[0] != c.hostPath+" "+c.want {
				t.Fatalf("%s received %q, want one %s naming %q", c.host, seen, c.hostPath, c.want)
			}
			if !strings.Contains(creds[0], "org-"+c.host) && !strings.Contains(creds[0], "AKIAORGOWNKEY") {
				t.Errorf("%s was called with %q, want the org's own credential", c.host, creds[0])
			}
			for id, h := range g.hosts {
				if id == c.host {
					continue
				}
				if other, _ := h.calls(); len(other) != 0 {
					t.Errorf("%s received %q; only %s serves %s", id, other, c.host, c.model)
				}
			}
			if c.cost != "" {
				if got := w.Header().Get("x-agentcc-cost"); got != c.cost {
					t.Errorf("x-agentcc-cost = %q, want %s", got, c.cost)
				}
			}
		})
	}
}

// The request log records the model the caller asked for, the id the host
// was sent (org_upstream_model, and the attempt's model on the routes that
// record attempts), the org's provider and byok billing.
func TestOwnKeyLogRecordsTheAskedModelAndTheHostID(t *testing.T) {
	for _, c := range []struct {
		okCase
		attempt string // the attempt's "<provider> <model>"; "" where the route records none
	}{
		{okCaseNamed(t, "openai chat"), "openai gpt-4o-mini"},
		{okCaseNamed(t, "openai chat stream"), "openai gpt-4o-mini"},
		{okCaseNamed(t, "together chat"), "together " + okTogether},
		{okCaseNamed(t, "openai embeddings"), "openai text-embedding-3-small"},
		{okCaseNamed(t, "openai images"), ""},
		{okCaseNamed(t, "anthropic messages"), ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			g := newOKGateway(t)
			ct, body := c.body(t, c.model)
			if w := g.send(t, c.route, ct, body); w.Code != http.StatusOK {
				t.Fatalf("status = %d %s", w.Code, w.Body.String())
			}
			e := g.logged(t)
			if e["model"] != c.model || e["provider"] != c.host {
				t.Errorf("log entry model %v, provider %v; want the asked %s on %s", e["model"], e["provider"], c.model, c.host)
			}
			meta, _ := e["metadata"].(map[string]any)
			if meta["org_upstream_model"] != c.want {
				t.Errorf("log entry metadata.org_upstream_model = %v, want the host id %s", meta["org_upstream_model"], c.want)
			}
			g.probe.mu.Lock()
			defer g.probe.mu.Unlock()
			if g.probe.model != c.model {
				t.Errorf("rc.Model = %q, want the asked %s", g.probe.model, c.model)
			}
			if c.attempt != "" && (len(g.probe.attempts) != 1 || g.probe.attempts[0] != c.attempt) {
				t.Errorf("attempts = %q, want one: %s", g.probe.attempts, c.attempt)
			}
		})
	}
}

// A model fallback the org's own provider serves is sent as its host's id
// too, and the fallback chain is still found under the model the caller
// asked for: the org's chain (non-streamed and streamed) and, with no org
// chain, the gateway's own (config.yaml), on both paths.
func TestOwnKeyModelFallbackReachesTheHostAsItsOwnID(t *testing.T) {
	asked := "fireworks/" + okDown
	failover := &tenant.FailoverConfig{Enabled: true, OnStatusCodes: []int{http.StatusBadGateway}}
	orgChain := func(_ *config.Config, org *tenant.OrgConfig) {
		org.Routing = &tenant.RoutingConfig{Failover: failover,
			ModelFallbacks: map[string][]string{asked: {"together/" + okTogether}}}
	}
	globalChain := func(cfg *config.Config, org *tenant.OrgConfig) {
		org.Routing = &tenant.RoutingConfig{Failover: failover}
		cfg.Routing.ModelFallbacks = map[string][]string{asked: {"together/" + okTogether}}
	}
	for _, c := range []struct {
		name   string
		body   string
		mutate func(*config.Config, *tenant.OrgConfig)
	}{
		{"org chain", okChat, orgChain},
		{"org chain, streamed", okChatSSE, orgChain},
		{"gateway chain, streamed", okChatSSE, globalChain},
	} {
		t.Run(c.name, func(t *testing.T) {
			g := newOKGateway(t, c.mutate)
			ct, body := okJSON(c.body)(t, asked)
			w := g.send(t, "/v1/chat/completions", ct, body)
			down, _ := g.hosts["fireworks"].calls()
			fb, _ := g.hosts["together"].calls()
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d %s; fireworks received %q, together %q", w.Code, w.Body.String(), down, fb)
			}
			if len(down) != 1 || down[0] != "/v1/chat/completions "+okDown {
				t.Errorf("fireworks received %q, want one call naming %s", down, okDown)
			}
			if len(fb) != 1 || fb[0] != "/v1/chat/completions "+okTogether {
				t.Errorf("together received %q, want the fallback naming %s", fb, okTogether)
			}
		})
	}
}

// An org's auto candidate its own provider serves ("openai/gpt-4o-mini") is
// chosen under its listed id, which auto answers with, and reaches the host
// as the id the org registered, billed byok. A listed candidate whose org
// provider cannot be built (its base_url is refused) stays in the pool under
// its listed id and is never sent.
func TestOwnKeyAutoCandidateReachesTheHostAsItsOwnID(t *testing.T) {
	for _, c := range []struct {
		name       string
		candidates []string
	}{
		{"one org candidate", []string{"openai/gpt-4o-mini"}},
		{"with a candidate whose provider cannot be built", []string{"broken/broken-model", "openai/gpt-4o-mini"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			g := newOKGateway(t, func(_ *config.Config, org *tenant.OrgConfig) {
				org.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: c.candidates}}
				org.Providers["broken"] = &tenant.ProviderConfig{Enabled: true, APIKey: "org-broken",
					BaseURL: "http://169.254.169.254", Models: []string{"broken-model"}}
			})
			ct, body := okJSON(okChat)(t, "auto")
			w := g.send(t, "/v1/chat/completions", ct, body)
			seen, creds := g.hosts["openai"].calls()
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d %s; openai received %q", w.Code, w.Body.String(), seen)
			}
			if len(seen) != 1 || seen[0] != "/v1/chat/completions gpt-4o-mini" || creds[0] != "Bearer org-openai" {
				t.Errorf("openai received %q with %q, want one call naming gpt-4o-mini with the org's key", seen, creds)
			}
			if got := w.Header().Get("x-agentcc-billing-mode"); got != "byok" {
				t.Errorf("x-agentcc-billing-mode = %q, want byok", got)
			}
			g.probe.mu.Lock()
			defer g.probe.mu.Unlock()
			if g.probe.meta["auto_model"] != "openai/gpt-4o-mini" || g.probe.model != "openai/gpt-4o-mini" {
				t.Errorf("auto_model = %q, rc.Model = %q; want the listed openai/gpt-4o-mini", g.probe.meta["auto_model"], g.probe.model)
			}
			if g.probe.meta[metaOrgUpstreamModel] != "gpt-4o-mini" {
				t.Errorf("org_upstream_model = %q, want gpt-4o-mini", g.probe.meta[metaOrgUpstreamModel])
			}
		})
	}
}

// The pool keeps an org candidate under its listed id and describes and
// judges it on the id its provider is sent (sentAs), as it does a
// provider-prefixed managed model; a candidate whose org provider cannot be
// built keeps its listed id and is sent as it is.
func TestOwnKeyAutoPoolJudgesTheIDSent(t *testing.T) {
	g := newOKGateway(t, func(_ *config.Config, org *tenant.OrgConfig) {
		org.Providers["broken"] = &tenant.ProviderConfig{Enabled: true, APIKey: "org-broken",
			BaseURL: "http://169.254.169.254", Models: []string{"broken-model"}}
	})
	h := g.srv.handlers
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Metadata["org_id"] = okOrg
	pool, sentAs := h.listedAutoPool(context.Background(), rc, h.tenantStore.Get(okOrg),
		[]string{"openai/gpt-4o-mini", "together/" + okTogether, "broken/broken-model"})
	if len(pool) != 3 {
		t.Fatalf("pool = %+v, want the three listed candidates", pool)
	}
	for i, want := range []struct{ model, provider, sent string }{
		{"openai/gpt-4o-mini", "openai", "gpt-4o-mini"},
		{"together/" + okTogether, "together", okTogether},
		{"broken/broken-model", "broken", ""},
	} {
		c := pool[i]
		if c.Model != want.model || c.ProviderID != want.provider || sentAs[i] != want.sent {
			t.Errorf("candidate %d = %s on %s sent as %q; want %s on %s sent as %q", i, c.Model, c.ProviderID, sentAs[i], want.model, want.provider, want.sent)
		}
	}
	if info := pool[1].Info; info == nil || info.ID != "together/"+okTogether {
		t.Errorf("together's candidate is described by %+v, want the together/ entry", pool[1].Info)
	}
}
