package server

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/realtime"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/gorilla/websocket"
)

type speechTestCheck struct{ stage guardrails.Stage }

func (g speechTestCheck) Name() string            { return "speech-test" }
func (g speechTestCheck) Stage() guardrails.Stage { return g.stage }
func (g speechTestCheck) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	b, _ := json.Marshal(in)
	blocked := strings.Contains(string(b), "prohibited")
	score := 0.0
	if blocked {
		score = 1
	}
	return &guardrails.CheckResult{Pass: !blocked, Score: score}
}

type speechFixture struct {
	gateway  *httptest.Server
	mu       sync.Mutex
	received []string
	dials    int
}

// All upstream traffic terminates at an owned loopback HTTP/WebSocket recorder.
func newSpeechFixture(t *testing.T, vendor, stage, source, action string, replies []string, configure ...func(*config.GuardrailsConfig, map[string]guardrails.Guardrail)) *speechFixture {
	t.Helper()
	f := &speechFixture{}
	upgrader := websocket.Upgrader{}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.dials++
		f.mu.Unlock()
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer func() { _ = conn.Close() }()
		for {
			_, data, err := conn.ReadMessage()
			if err != nil {
				return
			}
			f.mu.Lock()
			f.received = append(f.received, string(data))
			f.mu.Unlock()
			if replies != nil {
				for _, reply := range replies {
					if err := conn.WriteMessage(websocket.TextMessage, []byte(reply)); err != nil {
						return
					}
				}
				return
			}
			if err := conn.WriteMessage(websocket.TextMessage, []byte(`{"type":"ack"}`)); err != nil {
				return
			}
		}
	}))
	t.Cleanup(upstream.Close)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "voice", Key: "sk-voice-test", Owner: "platform", KeyType: "internal", Metadata: map[string]string{"org_id": "voice-org"}}}
	discover := false
	cfg.Providers[vendor] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic-vendor-key", APIFormat: vendor, AutoDiscover: &discover, Local: &discover}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	policies := policy.NewStore()
	tenants := tenant.NewStore()
	gstage := guardrails.StagePre
	if stage == "post" {
		gstage = guardrails.StagePost
	}
	checks := map[string]guardrails.Guardrail{"speech-test": speechTestCheck{gstage}}
	grCfg := config.GuardrailsConfig{DefaultTimeout: time.Second}
	if source != "managed" && source != "other-org" && source != "unavailable" {
		staticAction := action
		if source == "key" || source == "org" {
			staticAction = "log"
		}
		grCfg.Rules = []config.GuardrailRuleConfig{{Name: "speech-test", Stage: stage, Mode: "sync", Action: staticAction}}
	}
	if source == "managed" || source == "org" || source == "other-org" || source == "unavailable" {
		org := "voice-org"
		if source == "other-org" {
			org = "other-org"
		}
		tenants.Set(org, &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"speech-test": {Enabled: true, Action: action}}}})
	}
	if source == "key" {
		policies.Register(keys.Lookup("sk-voice-test").ID, &config.KeyGuardrailPolicyConfig{Overrides: []config.KeyGuardrailOverride{{Name: "speech-test", Action: action}}})
	}
	for _, change := range configure {
		change(&grCfg, checks)
	}
	gr := guardrails.NewEngine(grCfg, checks)
	engine := pipeline.NewEngine(authplugin.New(keys, true), guardrails.NewPlugin(gr, checks, nil, policies, tenants))
	if source == "unavailable" {
		engine = pipeline.NewEngine(authplugin.New(keys, true))
		gr = nil
	}
	srv := New(cfg, "", registry, engine, keys, gr, policies, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	f.gateway = httptest.NewServer(srv.httpServer.Handler)
	t.Cleanup(f.gateway.Close)
	return f
}

func (f *speechFixture) dial(t *testing.T, path string, headers http.Header) (*websocket.Conn, *http.Response, error) {
	t.Helper()
	if headers == nil {
		headers = http.Header{"Authorization": {"Bearer sk-voice-test"}}
	}
	return websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(f.gateway.URL, "http")+path, headers)
}

func TestSpeechGuardrailsSynthesisPre(t *testing.T) {
	for _, vendor := range []string{"deepgram", "cartesia"} {
		for _, source := range []string{"static", "org", "key", "managed"} {
			for _, allowed := range []bool{true, false} {
				name := vendor + "/" + source + "/blocked"
				text := "prohibited"
				if allowed {
					name = vendor + "/" + source + "/allowed"
					text = "hello"
				}
				t.Run(name, func(t *testing.T) {
					f := newSpeechFixture(t, vendor, "pre", source, "block", nil)
					path := "/" + vendor + "/tts/websocket"
					messages := []string{`{"model_id":"sonic-3","transcript":"` + text + `","context_id":"turn"}`}
					if vendor == "deepgram" {
						path = "/deepgram/v1/speak?model=aura-2-thalia-en"
						messages = []string{`{"type":"Speak","text":"` + text + `"}`, `{"type":"Flush"}`}
					}
					client, _, err := f.dial(t, path, nil)
					if err != nil {
						t.Fatal(err)
					}
					defer func() { _ = client.Close() }()
					for _, m := range messages {
						if err := client.WriteMessage(websocket.TextMessage, []byte(m)); err != nil {
							t.Fatal(err)
						}
					}
					if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
						t.Fatal(err)
					}
					_, data, err := client.ReadMessage()
					if allowed {
						if err != nil || string(data) != `{"type":"ack"}` {
							t.Fatalf("allowed response=%s error=%v", data, err)
						}
					} else {
						var closeErr *websocket.CloseError
						if !errors.As(err, &closeErr) || closeErr.Code != 1008 || closeErr.Text != "realtime_guardrail_blocked" {
							t.Fatalf("blocked content must close 1008 before delivery: data=%s err=%v", data, err)
						}
						f.mu.Lock()
						defer f.mu.Unlock()
						if len(f.received) != 0 {
							t.Fatalf("blocked content reached provider: %v", f.received)
						}
					}
				})
			}
		}
	}
}

func TestSpeechGuardrailsTranscriptionPost(t *testing.T) {
	for _, vendor := range []string{"deepgram", "cartesia"} {
		for _, allowed := range []bool{true, false} {
			name := vendor + "/blocked"
			text := "prohibited"
			if allowed {
				name = vendor + "/allowed"
				text = "hello"
			}
			t.Run(name, func(t *testing.T) {
				path := "/cartesia/stt/websocket?encoding=pcm_s16le&sample_rate=16000"
				final := `{"type":"transcript","text":"` + text + `","is_final":true}`
				interim := `{"type":"transcript","text":"prohibited interim","is_final":false}`
				if vendor == "deepgram" {
					path = "/deepgram/v1/listen?encoding=linear16&sample_rate=16000"
					final = `{"type":"Results","is_final":true,"speech_final":true,"channel":{"alternatives":[{"transcript":"` + text + `"}]}}`
					interim = `{"type":"Results","is_final":false,"channel":{"alternatives":[{"transcript":"prohibited interim"}]}}`
				}
				f := newSpeechFixture(t, vendor, "post", "managed", "block", []string{interim, final})
				client, _, err := f.dial(t, path, nil)
				if err != nil {
					t.Fatal(err)
				}
				defer func() { _ = client.Close() }()
				if err := client.WriteMessage(websocket.BinaryMessage, []byte{0, 0}); err != nil {
					t.Fatal(err)
				}
				if err := client.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
					t.Fatal(err)
				}
				_, data, err := client.ReadMessage()
				if allowed {
					if err != nil || string(data) != final {
						t.Fatalf("final=%s err=%v want=%s", data, err, final)
					}
				} else {
					var closeErr *websocket.CloseError
					if !errors.As(err, &closeErr) || closeErr.Code != 1008 {
						t.Fatalf("blocked transcript delivered: %s err=%v", data, err)
					}
				}
			})
		}
	}
}

func TestSpeechGuardrailsRefuseUninspectableBeforeDial(t *testing.T) {
	for _, tc := range []struct{ vendor, stage, path string }{
		{"deepgram", "pre", "/deepgram/v1/listen?encoding=linear16&sample_rate=16000"},
		{"deepgram", "post", "/deepgram/v1/speak?model=aura-2-thalia-en"},
		{"cartesia", "pre", "/cartesia/stt/websocket?encoding=pcm_s16le&sample_rate=16000"},
		{"cartesia", "post", "/cartesia/tts/websocket"},
		{"cartesia", "post", "/cartesia/stt/turns/websocket?encoding=pcm_s16le&sample_rate=16000"},
	} {
		t.Run(tc.path, func(t *testing.T) {
			f := newSpeechFixture(t, tc.vendor, tc.stage, "managed", "block", nil)
			conn, resp, err := f.dial(t, tc.path, nil)
			if conn != nil {
				_ = conn.Close()
			}
			if err == nil || resp == nil || resp.StatusCode != 403 {
				t.Errorf("uninspectable session must refuse 403, response=%v err=%v", resp, err)
			}
			f.mu.Lock()
			defer f.mu.Unlock()
			if f.dials != 0 {
				t.Errorf("refused session dialed vendor %d times", f.dials)
			}
		})
	}
}

func TestSpeechGuardrailsPolicySelection(t *testing.T) {
	for _, tc := range []struct {
		name, source, action string
		header               http.Header
		status               int
	}{
		{"unauthorized override", "managed", "block", http.Header{"Authorization": {"Bearer sk-voice-test"}, "X-Guardrail-Policy": {"disabled"}}, 403},
		{"invalid key", "managed", "block", http.Header{"Authorization": {"Bearer invalid"}}, 401},
		{"nonmatching org configuration", "other-org", "block", nil, 101},
		{"warn does not refuse audio", "managed", "warn", nil, 101},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newSpeechFixture(t, "cartesia", "post", tc.source, tc.action, nil)
			conn, resp, err := f.dial(t, "/cartesia/tts/websocket", tc.header)
			if conn != nil {
				defer func() { _ = conn.Close() }()
			}
			if resp == nil || resp.StatusCode != tc.status {
				t.Fatalf("status=%v err=%v want=%d", resp, err, tc.status)
			}
			if tc.status != 101 {
				f.mu.Lock()
				defer f.mu.Unlock()
				if f.dials != 0 {
					t.Errorf("provider dials=%d, want 0", f.dials)
				}
			}
		})
	}
}

func TestSpeechGuardrailsUnavailablePluginRefuses(t *testing.T) {
	f := newSpeechFixture(t, "cartesia", "pre", "unavailable", "block", nil)
	conn, resp, err := f.dial(t, "/cartesia/tts/websocket", nil)
	if conn != nil {
		_ = conn.Close()
	}
	if err == nil || resp == nil || resp.StatusCode != 403 {
		t.Fatalf("unavailable policy must refuse: response=%v err=%v", resp, err)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.dials != 0 || len(f.received) != 0 {
		t.Fatalf("unavailable policy contacted upstream: dials=%d received=%v", f.dials, f.received)
	}
}

func TestGenericRealtimeRemainsUnsupported(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = false
	discover := false
	cfg.Providers = map[string]config.ProviderConfig{"openai": {BaseURL: "http://127.0.0.1:9", APIFormat: "openai", Models: []string{"synthetic"}, AutoDiscover: &discover, Local: &discover}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	handler := NewRealtimeHandler(realtime.NewSessionTracker(1), registry, nil, realtimeHandlerConfig{})
	response := httptest.NewRecorder()
	handler.HandleRealtime(response, httptest.NewRequest("GET", "/v1/realtime?model=synthetic", nil))
	if response.Code != 501 || !strings.Contains(response.Body.String(), "realtime_not_supported") {
		t.Fatalf("generic route=%d %s", response.Code, response.Body.String())
	}
}
