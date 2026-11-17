package ratelimit_test

// Org rate limits through the gateway's chat handler: the org config the
// console's Org Config > Rate Limiting tab saves, the org's own keys, and a
// loopback upstream that reports 2000 tokens per call. These cover the four
// metadata keys the handler stamps at the base (global_rpm, global_tpm,
// per_key_rpm, per_user_rpm); per_key_tpm and per_model_rpm are stamped by
// the routing change, so their tests set the metadata directly
// (r7_limits_plugin_test.go).

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

const r7Model = "gpt-4o-mini"

type r7Upstream struct {
	*httptest.Server
	mu    sync.Mutex
	calls int
}

// r7OtherAnswers are the loopback upstream's answers on the routes other than
// chat (r7_limits_routes_test.go), by path suffix.
var r7OtherAnswers = map[string]string{
	"/responses":             `{"id":"resp_r7","object":"response","created_at":1,"model":"` + r7Model + `","status":"completed","output":[{"type":"message","id":"msg_r7","status":"completed","role":"assistant","content":[{"type":"output_text","text":"hello","annotations":[]}]}],"usage":{"input_tokens":1000,"output_tokens":1000,"total_tokens":2000}}`,
	"/messages/count_tokens": `{"input_tokens":5}`,
	"/v1/completions":        `{"id":"cmpl_r7","object":"text_completion","created":1,"model":"` + r7Model + `","choices":[{"index":0,"text":"hello","finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":1000,"total_tokens":2000}}`,
	"/embeddings":            `{"object":"list","data":[{"object":"embedding","index":0,"embedding":[0.1,0.2,0.3]}],"model":"` + r7Model + `","usage":{"prompt_tokens":5,"total_tokens":5}}`,
	"/images/generations":    `{"created":1,"data":[{"b64_json":"aGVsbG8="}]}`,
	"/ocr":                   `{"pages":[{"index":0,"markdown":"hello"}],"model":"` + r7Model + `","usage_info":{"pages_processed":1}}`,
	"/assistants":            `{"id":"asst_r7","object":"assistant","created_at":1,"model":"` + r7Model + `","tools":[]}`,
}

// startR7Upstream answers every chat call with 2000 tokens of usage, as JSON
// or, for a stream, as SSE with the usage in the last chunk; a Responses or
// legacy completions call with 2000 tokens too, and the other routes of
// r7OtherAnswers as their API does.
func startR7Upstream(t *testing.T) *r7Upstream {
	t.Helper()
	u := &r7Upstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/audio/speech") {
			w.Header().Set("Content-Type", "audio/mpeg")
			_, _ = w.Write([]byte("ID3-r7-audio"))
			return
		}
		for suffix, answer := range r7OtherAnswers {
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
				`{"id":"c1","object":"chat.completion.chunk","created":1,"model":"` + r7Model + `","choices":[{"index":0,"delta":{"role":"assistant","content":"hello"}}]}`,
				`{"id":"c1","object":"chat.completion.chunk","created":1,"model":"` + r7Model + `","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}`,
				`{"id":"c1","object":"chat.completion.chunk","created":1,"model":"` + r7Model + `","choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":1000,"total_tokens":2000}}`,
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
		_, _ = io.WriteString(w, `{"id":"c1","object":"chat.completion","created":1,"model":"`+r7Model+`","choices":[{"index":0,"message":{"role":"assistant","content":"hello"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":1000,"total_tokens":2000}}`)
	}))
	t.Cleanup(u.Close)
	return u
}

type r7Gateway struct {
	h    *server.Handlers
	ks   *auth.KeyStore
	up   *r7Upstream
	keys []string
}

var r7KeySeq atomic.Int64

// r7Option changes a gateway newR7Gateway builds.
type r7Option func(*r7Setup)

type r7Setup struct {
	cache     bool
	anthropic bool
}

// r7AnthropicModel is served by an Anthropic-format provider when
// r7WithAnthropic is given.
const r7AnthropicModel = "claude-r7-limits"

// r7WithAnthropic adds an Anthropic-format provider on the same loopback
// upstream, serving r7AnthropicModel.
func r7WithAnthropic() r7Option { return func(s *r7Setup) { s.anthropic = true } }

// r7WithCache adds the response cache plugin (in memory) and switches the
// cache on in both orgs' config.
func r7WithCache() r7Option { return func(s *r7Setup) { s.cache = true } }

// newR7Gateway is a gateway with the auth and rate-limit plugins, the node's
// rate_limiting switch on, and nKeys keys of org "org-a" whose org config
// holds rl. A key of org "org-b" with the same limits is keys[nKeys].
func newR7Gateway(t *testing.T, rl *tenant.RateLimitConfig, nKeys int, opts ...r7Option) *r7Gateway {
	t.Helper()
	var setup r7Setup
	for _, o := range opts {
		o(&setup)
	}
	up := startR7Upstream(t)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "r7-limits-admin-token-0123456789abcdef"
	cfg.Auth.Enabled = true
	cfg.RateLimiting.Enabled = true
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL + "/v1", APIKey: "node-operator-key", APIFormat: "openai", Models: []string{r7Model}}
	if setup.anthropic {
		cfg.Providers["anthropic"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "node-operator-key", APIFormat: "anthropic", Models: []string{r7AnthropicModel}}
	}
	g := &r7Gateway{up: up}
	add := func(org string) {
		raw := fmt.Sprintf("sk-agentcc-r7-limits-%s-%d-0123456789abcdef", org, r7KeySeq.Add(1))
		// Config keys of type internal so the call may reach the operator's
		// loopback provider; the org's rate_limiting is read from the org's
		// config whatever the key type.
		cfg.Auth.Keys = append(cfg.Auth.Keys, config.AuthKeyConfig{Name: "r7-" + org, Key: raw, Owner: org, KeyType: "internal", Metadata: map[string]string{"org_id": org}})
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
func (g *r7Gateway) chat(key, extra string, hdr map[string]string) *httptest.ResponseRecorder {
	body := `{"model":"` + r7Model + `","messages":[{"role":"user","content":"Say hello."}]` + extra + `}`
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

func r7Want(t *testing.T, what string, w *httptest.ResponseRecorder, status int, field string) {
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

// The probe: global_rpm=1 gave key1 200,429,429 and key2 of the same org 200.
func TestR7OrgWideRPMThroughGateway(t *testing.T) {
	g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 1}, 2)
	r7Want(t, "org-a key1", g.chat(g.keys[0], "", nil), http.StatusOK, "")
	r7Want(t, "org-a key2", g.chat(g.keys[1], "", nil), http.StatusTooManyRequests, "global_rpm")
	r7Want(t, "org-b", g.chat(g.keys[2], "", nil), http.StatusOK, "")
}

// global_tpm=1000 against calls of 2000 tokens: the first call is admitted
// (nothing counted yet), the next is refused, from any key of the org.
func TestR7OrgTPMThroughGateway(t *testing.T) {
	g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalTPM: 1000}, 2)
	w := g.chat(g.keys[0], "", nil)
	r7Want(t, "org-a key1", w, http.StatusOK, "")
	if w.Header().Get("x-ratelimit-limit-tokens") != "1000" {
		t.Errorf("x-ratelimit-limit-tokens = %q, want 1000", w.Header().Get("x-ratelimit-limit-tokens"))
	}
	if w.Header().Get("x-ratelimit-remaining-tokens") != "0" {
		t.Errorf("x-ratelimit-remaining-tokens after 2000 tokens = %q, want 0", w.Header().Get("x-ratelimit-remaining-tokens"))
	}
	r7Want(t, "org-a key2", g.chat(g.keys[1], "", nil), http.StatusTooManyRequests, "global_tpm")
	r7Want(t, "org-b", g.chat(g.keys[2], "", nil), http.StatusOK, "")
}

// A stream settles on the usage in its last chunk.
func TestR7OrgTPMThroughGatewayStream(t *testing.T) {
	g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalTPM: 1000}, 2)
	w := g.chat(g.keys[0], `,"stream":true`, nil)
	r7Want(t, "org-a key1 stream", w, http.StatusOK, "")
	if !strings.Contains(w.Body.String(), "[DONE]") {
		t.Fatalf("stream body: %q", w.Body.String())
	}
	r7Want(t, "org-a key2 after the stream", g.chat(g.keys[1], "", nil), http.StatusTooManyRequests, "global_tpm")
}

// per_user_rpm=1: the user is x-agentcc-user-id, else the body's user; a
// request that names no user is not limited by it.
func TestR7PerUserRPMThroughGateway(t *testing.T) {
	g := newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, PerUserRPM: 1}, 2)
	r7Want(t, "u1 by header via key1", g.chat(g.keys[0], "", map[string]string{"x-agentcc-user-id": "u1"}), http.StatusOK, "")
	r7Want(t, "u1 by body user via key2", g.chat(g.keys[1], `,"user":"u1"`, nil), http.StatusTooManyRequests, "per_user_rpm")
	r7Want(t, "u2 by header", g.chat(g.keys[0], "", map[string]string{"x-agentcc-user-id": "u2"}), http.StatusOK, "")
	for i := 0; i < 3; i++ {
		r7Want(t, "no user", g.chat(g.keys[0], "", nil), http.StatusOK, "")
	}
}

// A caller's x-agentcc-metadata or body metadata neither sets nor raises an
// org limit.
func TestR7OrgLimitNotCallerSettableThroughGateway(t *testing.T) {
	set := `{"org_rate_limit_rpm":"1","org_rate_limit_tpm":"1","org_rate_limit_per_key_rpm":"1","org_rate_limit_per_key_tpm":"1","org_rate_limit_per_user_rpm":"1","org_rate_limit_per_model_rpm":"{\"` + r7Model + `\":1}"}`
	g := newR7Gateway(t, nil, 1)
	for i := 0; i < 3; i++ {
		r7Want(t, "org without limits, caller header sets them", g.chat(g.keys[0], `,"user":"u1"`, map[string]string{"x-agentcc-metadata": set}), http.StatusOK, "")
		r7Want(t, "org without limits, caller body metadata sets them", g.chat(g.keys[0], `,"user":"u1","metadata":`+set, nil), http.StatusOK, "")
	}

	raise := `{"org_rate_limit_rpm":"100000","org_rate_limit_per_key_rpm":"100000"}`
	g = newR7Gateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 1}, 2)
	r7Want(t, "org global_rpm=1, first call", g.chat(g.keys[0], "", map[string]string{"x-agentcc-metadata": raise}), http.StatusOK, "")
	r7Want(t, "org global_rpm=1, caller raises it", g.chat(g.keys[1], `,"metadata":`+raise, map[string]string{"x-agentcc-metadata": raise}), http.StatusTooManyRequests, "global_rpm")
}
