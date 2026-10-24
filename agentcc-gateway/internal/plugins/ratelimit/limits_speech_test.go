package ratelimit_test

// The relayed speech sessions (GET /deepgram/v1/listen, /deepgram/v1/speak,
// /cartesia/tts/websocket, /cartesia/stt/websocket, ...) and the org's
// rate_limiting section: what the C66 release note says about them.
//
// IF ONE OF THESE TESTS FAILS because a relay now counts (or stops counting)
// against the org's limits, update C66 and its Known limits bullet in
// docs/gateway/release-notes.md together with this file.

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/gorilla/websocket"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	ratelimitplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/server"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type speechGateway struct {
	listen, speak string // ws:// URLs of the Deepgram relays, each naming a model
	// ws:// URLs of sessions that name no model: Deepgram synthesis on the vendor's default
	// model, and Cartesia synthesis, whose requests name the model.
	speakDefault, cartesiaTTS string
	keys                      []string
	dials                     *atomic.Int32
	h                         *server.Handlers // also serves chat completions of limitsModel
}

// newSpeechGateway is newLimitsGateway's setup (auth and rate-limit plugins,
// the node's rate_limiting switch on, nKeys internal keys of org "org-a"
// whose org config holds rl, and one key of org "org-b" with the same
// limits) with Deepgram and Cartesia providers on a WebSocket upstream, and
// limitsModel on startLimitsUpstream for chat. Each option changes the config before
// the gateway is built.
func newSpeechGateway(t *testing.T, rl *tenant.RateLimitConfig, nKeys int, opts ...func(*config.Config)) *speechGateway {
	t.Helper()
	g := &speechGateway{dials: &atomic.Int32{}}
	upgrader := websocket.Upgrader{}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Both speech relay test upstreams, this one and the server package's
		// (newSpeechRelayGateway), count a relay's dial before they answer its
		// handshake, so a test that reads the dials right after its 101 never
		// misses one. The gateway's start-up probes (GET /v1/models) are no dial.
		if !websocket.IsWebSocketUpgrade(r) {
			http.NotFound(w, r)
			return
		}
		g.dials.Add(1) // counted before the handshake is answered
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
		}
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Admin.Token = "limits-admin-token-0123456789abcdef"
	cfg.Auth.Enabled = true
	cfg.RateLimiting.Enabled = true
	cfg.Providers["deepgram"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "node-deepgram-key", APIFormat: "deepgram"}
	cfg.Providers["cartesia"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "node-cartesia-key", APIFormat: "cartesia"}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: startLimitsUpstream(t).URL + "/v1", APIKey: "node-operator-key", APIFormat: "openai", Models: []string{limitsModel}}
	add := func(org string) {
		raw := fmt.Sprintf("sk-agentcc-speech-%s-%d-0123456789abcdef", org, limitsKeySeq.Add(1))
		cfg.Auth.Keys = append(cfg.Auth.Keys, config.AuthKeyConfig{Name: "speech-" + org, Key: raw, Owner: org, KeyType: "internal", Metadata: map[string]string{"org_id": org}})
		g.keys = append(g.keys, raw)
	}
	for i := 0; i < nKeys; i++ {
		add("org-a")
	}
	add("org-b")
	for _, o := range opts {
		o(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	tenants := tenant.NewStore()
	tenants.Set("org-a", &tenant.OrgConfig{RateLimiting: rl})
	if rl != nil {
		cp := *rl
		tenants.Set("org-b", &tenant.OrgConfig{RateLimiting: &cp})
	}
	limiter := ratelimitplugin.NewRateLimiter()
	t.Cleanup(limiter.Stop)
	engine := pipeline.NewEngine(authplugin.New(keyStore, true), ratelimitplugin.New(cfg.RateLimiting, keyStore, limiter))
	var mdb atomic.Pointer[modeldb.ModelDB]
	mdb.Store(modeldb.New(modeldb.BundledModels, nil))
	h := server.New(cfg, "", registry, engine, keyStore, nil, nil, nil, &mdb, tenants, nil).Handlers()
	g.h = h
	listen := httptest.NewServer(h.SpeechRelay("deepgram", "/v1/listen"))
	t.Cleanup(listen.Close)
	speak := httptest.NewServer(h.SpeechRelay("deepgram", "/v1/speak"))
	t.Cleanup(speak.Close)
	tts := httptest.NewServer(h.SpeechRelay("cartesia", "/tts/websocket"))
	t.Cleanup(tts.Close)
	g.listen = "ws" + strings.TrimPrefix(listen.URL, "http") + "/deepgram/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000"
	g.speak = "ws" + strings.TrimPrefix(speak.URL, "http") + "/deepgram/v1/speak?model=aura-2-thalia-en"
	g.speakDefault = "ws" + strings.TrimPrefix(speak.URL, "http") + "/deepgram/v1/speak"
	g.cartesiaTTS = "ws" + strings.TrimPrefix(tts.URL, "http") + "/cartesia/tts/websocket"
	return g
}

// chat sends one chat completion with key; its answer reports 2000 tokens.
func (g *speechGateway) chat(key string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions",
		strings.NewReader(`{"model":"`+limitsModel+`","messages":[{"role":"user","content":"Say hello."}]}`))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	g.h.ChatCompletion(w, req)
	return w
}

// open opens a relayed session with key and returns the handshake status
// and, for a refusal, its body.
func (g *speechGateway) open(t *testing.T, url, key string) (int, string) {
	t.Helper()
	conn, resp, err := websocket.DefaultDialer.Dial(url, http.Header{"Authorization": {"Token " + key}})
	if resp == nil {
		t.Fatalf("dialing %s: no handshake response: %v", url, err)
	}
	if conn != nil {
		t.Cleanup(func() { conn.Close() })
		return resp.StatusCode, ""
	}
	var body strings.Builder
	buf := make([]byte, 4096)
	n, _ := resp.Body.Read(buf)
	body.Write(buf[:n])
	return resp.StatusCode, body.String()
}

// Each relayed session counts as one request against the org's global_rpm
// (every key of the org together), and a session over it is refused at the
// handshake with 429 naming the limit, before the vendor is dialed.
func TestSpeechRelaySessionsCountAgainstTheOrgsRPM(t *testing.T) {
	g := newSpeechGateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 1}, 2)
	if status, body := g.open(t, g.listen, g.keys[0]); status != http.StatusSwitchingProtocols {
		t.Fatalf("org-a key1, first session: %d %s, want 101", status, body)
	}
	status, body := g.open(t, g.speak, g.keys[1])
	if status != http.StatusTooManyRequests || !strings.Contains(body, "global_rpm") {
		t.Fatalf("org-a key2, second session in the minute: %d %s, want 429 naming global_rpm", status, body)
	}
	if status, body := g.open(t, g.listen, g.keys[2]); status != http.StatusSwitchingProtocols {
		t.Fatalf("org-b: %d %s, want 101 (its own limit)", status, body)
	}
	if n := g.dials.Load(); n != 2 {
		t.Errorf("the vendor was dialed %d times, want 2 (the refused session never reaches it)", n)
	}
}

// per_key_rpm and per_model_rpm count relayed sessions too, per_model_rpm by
// the model the session names (deepgram/nova-3 and deepgram/aura-2 are two
// models).
func TestSpeechRelaySessionsCountAgainstPerKeyAndPerModelRPM(t *testing.T) {
	g := newSpeechGateway(t, &tenant.RateLimitConfig{Enabled: true, PerKeyRPM: 1}, 2)
	if status, body := g.open(t, g.listen, g.keys[0]); status != http.StatusSwitchingProtocols {
		t.Fatalf("per_key_rpm=1, key1 first: %d %s, want 101", status, body)
	}
	if status, body := g.open(t, g.speak, g.keys[0]); status != http.StatusTooManyRequests || !strings.Contains(body, "per_key_rpm") {
		t.Fatalf("per_key_rpm=1, key1 second: %d %s, want 429 naming per_key_rpm", status, body)
	}
	if status, body := g.open(t, g.speak, g.keys[1]); status != http.StatusSwitchingProtocols {
		t.Fatalf("per_key_rpm=1, key2 first: %d %s, want 101", status, body)
	}

	g = newSpeechGateway(t, &tenant.RateLimitConfig{Enabled: true, PerModelRPM: 1}, 2)
	if status, body := g.open(t, g.listen, g.keys[0]); status != http.StatusSwitchingProtocols {
		t.Fatalf("per_model_rpm=1, nova-3 first: %d %s, want 101", status, body)
	}
	if status, body := g.open(t, g.listen, g.keys[1]); status != http.StatusTooManyRequests || !strings.Contains(body, "per_model_rpm") {
		t.Fatalf("per_model_rpm=1, nova-3 again from key2: %d %s, want 429 naming per_model_rpm", status, body)
	}
	if status, body := g.open(t, g.speak, g.keys[1]); status != http.StatusSwitchingProtocols {
		t.Fatalf("per_model_rpm=1, aura-2: %d %s, want 101 (another model)", status, body)
	}
}

// A relayed session reports no tokens, so it adds nothing to the org's
// global_tpm or per_key_tpm: sessions keep opening under a token limit of 1.
func TestSpeechRelaySessionsAddNoTokens(t *testing.T) {
	for _, rl := range []*tenant.RateLimitConfig{{Enabled: true, GlobalTPM: 1}, {Enabled: true, PerKeyTPM: 1}} {
		g := newSpeechGateway(t, rl, 1)
		for i := 0; i < 3; i++ {
			if status, body := g.open(t, g.listen, g.keys[0]); status != http.StatusSwitchingProtocols {
				t.Fatalf("%+v, session %d: %d %s, want 101", *rl, i+1, status, body)
			}
		}
	}
}

// per_model_rpm counts a session by the model its URL names. A session that names none (one on
// the vendor's default model, or Cartesia synthesis, whose requests name the model) is not
// counted against it; global_rpm and per_key_rpm count it like any other session.
func TestSpeechRelaySessionsThatNameNoModelAreNotCountedPerModel(t *testing.T) {
	g := newSpeechGateway(t, &tenant.RateLimitConfig{Enabled: true, PerModelRPM: 1}, 2)
	for i := 0; i < 3; i++ {
		for _, url := range []string{g.speakDefault, g.cartesiaTTS} {
			if status, body := g.open(t, url, g.keys[i%2]); status != http.StatusSwitchingProtocols {
				t.Fatalf("per_model_rpm=1, session %d naming no model (%s): %d %s, want 101", i+1, url, status, body)
			}
		}
	}
	if status, body := g.open(t, g.listen, g.keys[0]); status != http.StatusSwitchingProtocols {
		t.Fatalf("per_model_rpm=1, nova-3 first: %d %s, want 101", status, body)
	}
	if status, body := g.open(t, g.listen, g.keys[1]); status != http.StatusTooManyRequests || !strings.Contains(body, "per_model_rpm") {
		t.Fatalf("per_model_rpm=1, nova-3 again: %d %s, want 429 naming per_model_rpm", status, body)
	}

	g = newSpeechGateway(t, &tenant.RateLimitConfig{Enabled: true, GlobalRPM: 1, PerModelRPM: 5}, 2)
	if status, body := g.open(t, g.cartesiaTTS, g.keys[0]); status != http.StatusSwitchingProtocols {
		t.Fatalf("global_rpm=1, a session naming no model: %d %s, want 101", status, body)
	}
	if status, body := g.open(t, g.speakDefault, g.keys[1]); status != http.StatusTooManyRequests || !strings.Contains(body, "global_rpm") {
		t.Fatalf("global_rpm=1, a second session naming no model: %d %s, want 429 naming global_rpm", status, body)
	}
}

// A session adds no tokens, but a token limit that other requests have spent for the minute
// refuses it: after a chat answer of 2000 tokens spends the org's global_tpm (every key of the
// org), a key's per_key_tpm, or a config-file key's own rate_limit_tpm (C75), the next session
// that limit holds is refused at the handshake with 429 naming it, before the vendor is reached;
// a session the spent limit does not hold opens.
func TestSpeechRelaySessionsAreRefusedWhileATokenLimitIsSpent(t *testing.T) {
	cases := []struct {
		name     string
		rl       *tenant.RateLimitConfig
		opt      func(*config.Config)
		refused  int // index of the key whose next session is refused
		admitted int
		limit    string
	}{
		{"global_tpm", &tenant.RateLimitConfig{Enabled: true, GlobalTPM: 1000}, nil, 1, 2, "global_tpm"},
		{"per_key_tpm", &tenant.RateLimitConfig{Enabled: true, PerKeyTPM: 1000}, nil, 0, 1, "per_key_tpm"},
		{"the key's own rate_limit_tpm", nil, func(c *config.Config) { c.Auth.Keys[0].RateLimitTPM = 1000 }, 0, 1, "rate_limit_tpm"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var opts []func(*config.Config)
			if tc.opt != nil {
				opts = append(opts, tc.opt)
			}
			g := newSpeechGateway(t, tc.rl, 2, opts...)
			if w := g.chat(g.keys[0]); w.Code != http.StatusOK {
				t.Fatalf("chat with key1: %d %s, want 200", w.Code, w.Body.String())
			}
			status, body := g.open(t, g.listen, g.keys[tc.refused])
			if status != http.StatusTooManyRequests || !strings.Contains(body, tc.limit) {
				t.Fatalf("session after the chat answer spent %s: %d %s, want 429 naming %s", tc.limit, status, body, tc.limit)
			}
			if status, body := g.open(t, g.listen, g.keys[tc.admitted]); status != http.StatusSwitchingProtocols {
				t.Fatalf("session the spent %s does not hold: %d %s, want 101", tc.limit, status, body)
			}
			if n := g.dials.Load(); n != 1 {
				t.Errorf("the vendor was dialed %d times, want 1 (the refused session never reaches it)", n)
			}
		})
	}
}
