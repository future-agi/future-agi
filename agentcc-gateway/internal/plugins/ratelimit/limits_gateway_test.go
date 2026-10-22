package ratelimit_test

// Org rate limits through the gateway's chat handler: the org config the
// console's Org Config > Rate Limiting tab saves, the org's own keys, and a
// loopback upstream that reports 2000 tokens per call. These cover
// global_rpm, global_tpm, per_key_rpm and per_user_rpm through the handler;
// per_key_tpm and per_model_rpm are covered with the metadata set directly
// (limits_plugin_test.go).

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	ratelimitplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const limitsModel = "gpt-4o-mini"

type limitsUpstream struct {
	*httptest.Server
	mu    sync.Mutex
	calls int
}

// otherRouteAnswers are the loopback upstream's answers on the routes other than
// chat (limits_routes_test.go), by path suffix.
var otherRouteAnswers = map[string]string{
	"/responses":             `{"id":"resp_limits","object":"response","created_at":1,"model":"` + limitsModel + `","status":"completed","output":[{"type":"message","id":"msg_limits","status":"completed","role":"assistant","content":[{"type":"output_text","text":"hello","annotations":[]}]}],"usage":{"input_tokens":1000,"output_tokens":1000,"total_tokens":2000}}`,
	"/messages/count_tokens": `{"input_tokens":5}`,
	"/v1/completions":        `{"id":"cmpl_limits","object":"text_completion","created":1,"model":"` + limitsModel + `","choices":[{"index":0,"text":"hello","finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":1000,"total_tokens":2000}}`,
	"/embeddings":            `{"object":"list","data":[{"object":"embedding","index":0,"embedding":[0.1,0.2,0.3]}],"model":"` + limitsModel + `","usage":{"prompt_tokens":5,"total_tokens":5}}`,
	"/images/generations":    `{"created":1,"data":[{"b64_json":"aGVsbG8="}]}`,
	"/ocr":                   `{"pages":[{"index":0,"markdown":"hello"}],"model":"` + limitsModel + `","usage_info":{"pages_processed":1}}`,
	"/assistants":            `{"id":"asst_limits","object":"assistant","created_at":1,"model":"` + limitsModel + `","tools":[]}`,
}

// startLimitsUpstream answers every chat call with 2000 tokens of usage, as JSON
// or, for a stream, as SSE with the usage in the last chunk; a Responses or
// legacy completions call with 2000 tokens too, and the other routes of
// otherRouteAnswers as their API does.
func startLimitsUpstream(t *testing.T) *limitsUpstream {
	t.Helper()
	u := &limitsUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/audio/speech") {
			w.Header().Set("Content-Type", "audio/mpeg")
			_, _ = w.Write([]byte("ID3-test-audio"))
			return
		}
		for suffix, answer := range otherRouteAnswers {
			if r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, suffix) {
				w.Header().Set("Content-Type", "application/json")
				_, _ = io.WriteString(w, answer)
				return
			}
		}
		if r.Method != http.MethodPost || !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"object":"list","data":[]}`)
			return
		}
		u.mu.Lock()
		u.calls++
		u.mu.Unlock()
		if strings.Contains(string(body), `"stream":true`) {
			w.Header().Set("Content-Type", "text/event-stream")
			fl, _ := w.(http.Flusher)
			for _, chunk := range []string{
				`{"id":"c1","object":"chat.completion.chunk","created":1,"model":"` + limitsModel + `","choices":[{"index":0,"delta":{"role":"assistant","content":"hello"}}]}`,
				`{"id":"c1","object":"chat.completion.chunk","created":1,"model":"` + limitsModel + `","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}`,
				`{"id":"c1","object":"chat.completion.chunk","created":1,"model":"` + limitsModel + `","choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":1000,"total_tokens":2000}}`,
			} {
				_, _ = io.WriteString(w, "data: "+chunk+"\n\n")
				if fl != nil {
					fl.Flush()
				}
			}
			_, _ = io.WriteString(w, "data: [DONE]\n\n")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"c1","object":"chat.completion","created":1,"model":"`+limitsModel+`","choices":[{"index":0,"message":{"role":"assistant","content":"hello"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":1000,"total_tokens":2000}}`)
	}))
	t.Cleanup(u.Close)
	return u
}

type limitsGateway struct {
	h    *server.Handlers
	ks   *auth.KeyStore
	up   *limitsUpstream
	keys []string
}

var limitsKeySeq atomic.Int64

// gatewayOption changes a gateway newLimitsGateway builds.
type gatewayOption func(*gatewaySetup)

type gatewaySetup struct {
	cache     bool
	anthropic bool
}

// limitsAnthropicModel is served by an Anthropic-format provider when
// withAnthropic is given.
const limitsAnthropicModel = "claude-limits"

// withAnthropic adds an Anthropic-format provider on the same loopback
// upstream, serving limitsAnthropicModel.
func withAnthropic() gatewayOption { return func(s *gatewaySetup) { s.anthropic = true } }

// withCache adds the response cache plugin (in memory) and switches the
// cache on in both orgs' config.
func withCache() gatewayOption { return func(s *gatewaySetup) { s.cache = true } }

// newLimitsGateway is a gateway with the auth and rate-limit plugins, the node's
// rate_limiting switch on, and nKeys keys of org "org-a" whose org config
// holds rl. A key of org "org-b" with the same limits is keys[nKeys].
func newLimitsGateway(t *testing.T, rl *tenant.RateLimitConfig, nKeys int, opts ...gatewayOption) *limitsGateway {
	t.Helper()
	var setup gatewaySetup
	for _, o := range opts {
		o(&setup)
	}
	up := startLimitsUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "limits-admin-token-0123456789abcdef"
	cfg.Auth.Enabled = true
	cfg.RateLimiting.Enabled = true
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL + "/v1", APIKey: "node-operator-key", APIFormat: "openai", Models: []string{limitsModel}}
	if setup.anthropic {
		cfg.Providers["anthropic"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "node-operator-key", APIFormat: "anthropic", Models: []string{limitsAnthropicModel}}
	}
	g := &limitsGateway{up: up}
	add := func(org string) {
		raw := fmt.Sprintf("sk-agentcc-limits-%s-%d-0123456789abcdef", org, limitsKeySeq.Add(1))
		// Config keys of type internal so the call may reach the operator's
		// loopback provider; the org's rate_limiting is read from the org's
		// config whatever the key type.
		cfg.Auth.Keys = append(cfg.Auth.Keys, config.AuthKeyConfig{Name: "limits-" + org, Key: raw, Owner: org, KeyType: "internal", Metadata: map[string]string{"org_id": org}})
		g.keys = append(g.keys, raw)
	}
	for i := 0; i < nKeys; i++ {
		add("org-a")
	}
	add("org-b")
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	g.ks = keyStore
	tenants := tenant.NewStore()
	var orgCache *tenant.CacheConfig
	if setup.cache {
		orgCache = &tenant.CacheConfig{Enabled: true}
	}
	tenants.Set("org-a", &tenant.OrgConfig{RateLimiting: rl, Cache: orgCache})
	if rl != nil {
		cp := *rl
		tenants.Set("org-b", &tenant.OrgConfig{RateLimiting: &cp, Cache: orgCache})
	}
	limiter := ratelimitplugin.NewRateLimiter()
	t.Cleanup(limiter.Stop)
	plugins := []pipeline.Plugin{authplugin.New(keyStore, true), ratelimitplugin.New(cfg.RateLimiting, keyStore, limiter)}
	if setup.cache {
		plugins = append(plugins, cacheplugin.New(cache.NewMemoryBackend(cache.NewStore(100)), time.Minute))
	}
	engine := pipeline.NewEngine(plugins...)
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))
	srv := server.New(cfg, "", registry, engine, keyStore, nil, nil, nil, &mdb, tenants, nil)
	g.h = srv.Handlers()
	return g
}

// chat sends one chat request with key; extra is appended to the body and
// hdr set on the request.
func (g *limitsGateway) chat(key, extra string, hdr map[string]string) *httptest.ResponseRecorder {
	body := `{"model":"` + limitsModel + `","messages":[{"role":"user","content":"Say hello."}]` + extra + `}`
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	g.h.ChatCompletion(w, req)
	return w
}

func wantStatus(t *testing.T, what string, w *httptest.ResponseRecorder, status int, field string) {
	t.Helper()
	if w.Code != status {
		t.Fatalf("%s: %d %s, want %d", what, w.Code, strings.TrimSpace(w.Body.String()), status)
	}
	if status != http.StatusTooManyRequests {
		return
	}
	var e struct {
		Error struct {
			Code    string  `json:"code"`
			Param   *string `json:"param"`
			Message string  `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &e); err != nil {
		t.Fatalf("%s: 429 body %q: %v", what, w.Body.String(), err)
	}
	if e.Error.Code != "rate_limit_exceeded" || e.Error.Param == nil || *e.Error.Param != field || !strings.Contains(e.Error.Message, field) {
		t.Errorf("%s: 429 body %s, want code rate_limit_exceeded naming %s", what, w.Body.String(), field)
	}
	if w.Header().Get("Retry-After") == "" {
		t.Errorf("%s: 429 without Retry-After", what)
	}
}

// global_rpm=1: the first call of the org is admitted and every later call of the org is refused, from any key; another org is not affected.
func TestOrgWideRPMThroughGateway(t *testing.T) {
	g := newLimitsGateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 1}, 2)
	wantStatus(t, "org-a key1", g.chat(g.keys[0], "", nil), http.StatusOK, "")
	wantStatus(t, "org-a key2", g.chat(g.keys[1], "", nil), http.StatusTooManyRequests, "global_rpm")
	wantStatus(t, "org-b", g.chat(g.keys[2], "", nil), http.StatusOK, "")
}

// global_tpm=1000 against calls of 2000 tokens: the first call is admitted
// (nothing counted yet), the next is refused, from any key of the org.
func TestOrgTPMThroughGateway(t *testing.T) {
	g := newLimitsGateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalTPM: 1000}, 2)
	w := g.chat(g.keys[0], "", nil)
	wantStatus(t, "org-a key1", w, http.StatusOK, "")
	if w.Header().Get("x-ratelimit-limit-tokens") != "1000" {
		t.Errorf("x-ratelimit-limit-tokens = %q, want 1000", w.Header().Get("x-ratelimit-limit-tokens"))
	}
	if w.Header().Get("x-ratelimit-remaining-tokens") != "0" {
		t.Errorf("x-ratelimit-remaining-tokens after 2000 tokens = %q, want 0", w.Header().Get("x-ratelimit-remaining-tokens"))
	}
	wantStatus(t, "org-a key2", g.chat(g.keys[1], "", nil), http.StatusTooManyRequests, "global_tpm")
	wantStatus(t, "org-b", g.chat(g.keys[2], "", nil), http.StatusOK, "")
}

// A stream settles on the usage in its last chunk.
func TestOrgTPMThroughGatewayStream(t *testing.T) {
	g := newLimitsGateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalTPM: 1000}, 2)
	w := g.chat(g.keys[0], `,"stream":true`, nil)
	wantStatus(t, "org-a key1 stream", w, http.StatusOK, "")
	if !strings.Contains(w.Body.String(), "[DONE]") {
		t.Fatalf("stream body: %q", w.Body.String())
	}
	wantStatus(t, "org-a key2 after the stream", g.chat(g.keys[1], "", nil), http.StatusTooManyRequests, "global_tpm")
}

// per_user_rpm=1: the user is x-agentcc-user-id, else the body's user; a
// request that names no user is not limited by it.
func TestPerUserRPMThroughGateway(t *testing.T) {
	g := newLimitsGateway(t, &tenant.RateLimitConfig{Enabled: true, PerUserRPM: 1}, 2)
	wantStatus(t, "u1 by header via key1", g.chat(g.keys[0], "", map[string]string{"x-agentcc-user-id": "u1"}), http.StatusOK, "")
	wantStatus(t, "u1 by body user via key2", g.chat(g.keys[1], `,"user":"u1"`, nil), http.StatusTooManyRequests, "per_user_rpm")
	wantStatus(t, "u2 by header", g.chat(g.keys[0], "", map[string]string{"x-agentcc-user-id": "u2"}), http.StatusOK, "")
	for i := 0; i < 3; i++ {
		wantStatus(t, "no user", g.chat(g.keys[0], "", nil), http.StatusOK, "")
	}
}

// A caller's x-agentcc-metadata or body metadata neither sets nor raises an
// org limit.
func TestOrgLimitNotCallerSettableThroughGateway(t *testing.T) {
	set := `{"org_rate_limit_rpm":"1","org_rate_limit_tpm":"1","org_rate_limit_per_key_rpm":"1","org_rate_limit_per_key_tpm":"1","org_rate_limit_per_user_rpm":"1","org_rate_limit_per_model_rpm":"{\"` + limitsModel + `\":1}"}`
	g := newLimitsGateway(t, nil, 1)
	for i := 0; i < 3; i++ {
		wantStatus(t, "org without limits, limits named in a caller header do not apply", g.chat(g.keys[0], `,"user":"u1"`, map[string]string{"x-agentcc-metadata": set}), http.StatusOK, "")
		wantStatus(t, "org without limits, limits named in caller body metadata do not apply", g.chat(g.keys[0], `,"user":"u1","metadata":`+set, nil), http.StatusOK, "")
	}

	raise := `{"org_rate_limit_rpm":"100000","org_rate_limit_per_key_rpm":"100000"}`
	g = newLimitsGateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 1}, 2)
	wantStatus(t, "org global_rpm=1, first call", g.chat(g.keys[0], "", map[string]string{"x-agentcc-metadata": raise}), http.StatusOK, "")
	wantStatus(t, "org global_rpm=1, a higher value in caller metadata does not apply", g.chat(g.keys[1], `,"metadata":`+raise, map[string]string{"x-agentcc-metadata": raise}), http.StatusTooManyRequests, "global_rpm")
}
