package server

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const compatR4Audio = "\x00\x01ordinary-synthetic-audio\xff"
const compatR4Completion = `{"id":"compat","object":"text_completion","model":"gpt-3.5-turbo-instruct","choices":[{"index":0,"text":"safe reply","finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}`
const compatR4Embedding = `{"object":"list","model":"text-embedding-3-small","data":[{"object":"embedding","index":0,"embedding":[0.1,0.2]}],"usage":{"prompt_tokens":5,"total_tokens":5}}`

type compatR4Wire struct {
	mu     sync.Mutex
	paths  []string
	bodies [][]byte
}

func (u *compatR4Wire) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		fmt.Fprint(w, `{"data":[]}`)
		return
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read failed", 400)
		return
	}
	u.mu.Lock()
	u.paths = append(u.paths, r.URL.Path)
	u.bodies = append(u.bodies, body)
	u.mu.Unlock()
	switch r.URL.Path {
	case "/v1/completions":
		fmt.Fprint(w, compatR4Completion)
	case "/v1/embeddings":
		fmt.Fprint(w, compatR4Embedding)
	case "/v1/audio/speech":
		w.Header().Set("Content-Type", "audio/mpeg")
		fmt.Fprint(w, compatR4Audio)
	default:
		http.Error(w, "unexpected provider path", 500)
	}
}

func compatR4Server(t *testing.T, org *tenant.GuardrailConfig, staticName string, staticConfig map[string]interface{}) (string, *compatR4Wire) {
	t.Helper()
	wire := &compatR4Wire{}
	upstream := httptest.NewServer(wire)
	t.Cleanup(upstream.Close)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "selected", Key: "sk-agentcc-compat-a", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-compat"}},
		{Name: "other", Key: "sk-agentcc-compat-b", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-compat"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-3.5-turbo-instruct", "text-embedding-3-small", "tts-1"}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}
	factory := func(name string, c map[string]interface{}) guardrails.Guardrail {
		switch name {
		case "input-validation":
			return validation.New(c)
		case "keyword-blocklist":
			return blocklist.New(c)
		}
		return nil
	}
	static := map[string]guardrails.Guardrail{}
	if staticName != "" {
		static[staticName] = factory(staticName, staticConfig)
		cfg.Guardrails.Rules = []config.GuardrailRuleConfig{{Name: staticName, Stage: "pre", Mode: "sync", Action: "block", Threshold: .5}}
	}
	store := tenant.NewStore()
	store.Set("org-compat", &tenant.OrgConfig{Guardrails: org})
	gr := guardrails.NewEngine(cfg.Guardrails, static)
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true), guardrails.NewPlugin(gr, static, factory, nil, store)), keys, gr, nil, nil, testModelDBPtr(), store, nil)
	srv.ready.Store(true)
	gateway := httptest.NewServer(srv.httpServer.Handler)
	t.Cleanup(gateway.Close)
	return gateway.URL, wire
}

// Decode the persisted policy shape so this same fixture also compiles on dev,
// which predates selected policies. Dev controls run only the no-policy cases.
func compatR4Policy(t *testing.T, org *tenant.GuardrailConfig, mode, action string) {
	t.Helper()
	body, err := json.Marshal(map[string]interface{}{"key_policies": []interface{}{map[string]interface{}{
		"id": "compat-selected", "name": "compat-selected", "mode": mode, "priority": 1, "key_ids": []string{"key_1"},
		"checks": map[string]*tenant.GuardrailCheck{"keyword-blocklist": {Enabled: true, Action: action, ConfidenceThreshold: .5, Config: map[string]interface{}{"words": []interface{}{"forbidden"}}}},
	}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(body, org); err != nil {
		t.Fatal(err)
	}
}

func compatR4Send(t *testing.T, gateway string, wire *compatR4Wire, path, body, key string, want int) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, gateway+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-agentcc-compat-"+key)
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	released, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	wire.mu.Lock()
	paths := append([]string(nil), wire.paths...)
	bodies := append([][]byte(nil), wire.bodies...)
	wire.mu.Unlock()
	t.Logf("status=%d provider_calls=%d provider_paths=%q provider_bytes=%q released_bytes=%q", resp.StatusCode, len(bodies), paths, bodies, released)
	if resp.StatusCode != want {
		t.Errorf("status=%d want=%d", resp.StatusCode, want)
	}
	calls := 0
	if want == 200 {
		calls = 1
	}
	if len(bodies) != calls {
		t.Errorf("provider calls=%d want=%d", len(bodies), calls)
	}
	if want != 200 {
		if want == http.StatusForbidden && !bytes.Contains(released, []byte(`"code":"content_blocked"`)) {
			t.Error("expected a real content decision, not unsupported-shape refusal")
		}
		if bytes.Contains(released, []byte("safe reply")) || bytes.Contains(released, []byte(compatR4Audio)) {
			t.Error("blocked response released provider content")
		}
		return
	}
	var expected []byte
	switch path {
	case "/v1/completions":
		var req models.CompletionRequest
		if err := json.Unmarshal([]byte(body), &req); err != nil {
			t.Fatal(err)
		}
		expected, _ = json.Marshal(req)
	case "/v1/embeddings":
		var req models.EmbeddingRequest
		if err := json.Unmarshal([]byte(body), &req); err != nil {
			t.Fatal(err)
		}
		expected, _ = json.Marshal(req)
	default:
		var req models.SpeechRequest
		if err := json.Unmarshal([]byte(body), &req); err != nil {
			t.Fatal(err)
		}
		expected, _ = json.Marshal(req)
	}
	upstreamPath := path
	if strings.HasSuffix(path, "/stream") {
		upstreamPath = "/v1/audio/speech"
	}
	if len(bodies) == 1 && (paths[0] != upstreamPath || !bytes.Equal(bodies[0], expected)) {
		t.Errorf("provider bytes changed: path=%q body=%s want=%s", paths[0], bodies[0], expected)
	}
	switch path {
	case "/v1/audio/speech":
		if string(released) != compatR4Audio || resp.Header.Get("Content-Type") != "audio/mpeg" {
			t.Error("binary audio changed")
		}
	case "/v1/audio/speech/stream":
		if !bytes.Contains(released, []byte(base64.StdEncoding.EncodeToString([]byte(compatR4Audio)))) || !bytes.Contains(released, []byte(`"done":true`)) {
			t.Error("speech SSE audio/done changed")
		}
	case "/v1/completions":
		var value models.CompletionResponse
		if err := json.Unmarshal([]byte(compatR4Completion), &value); err != nil {
			t.Fatal(err)
		}
		want, _ := json.Marshal(value)
		if !bytes.Equal(released, append(want, '\n')) {
			t.Errorf("completion response bytes changed: %q", released)
		}
	case "/v1/embeddings":
		var value models.EmbeddingResponse
		if err := json.Unmarshal([]byte(compatR4Embedding), &value); err != nil {
			t.Fatal(err)
		}
		want, _ := json.Marshal(value)
		if !bytes.Equal(released, append(want, '\n')) {
			t.Errorf("embedding response bytes changed: %q", released)
		}
	}
}

// This test file is deliberately self-contained so these same HTTP assertions
// can run against exact dev and candidate source trees, not reconstructed handlers.
func TestOrdinaryCompatibilityR4Completion(t *testing.T) {
	for _, source := range []string{"org", "static", "none"} {
		for _, selection := range []string{"none", "selected", "other-key"} {
			t.Run(source+"/"+selection, func(t *testing.T) {
				config := map[string]interface{}{"max_message_length": 5}
				org := &tenant.GuardrailConfig{PipelineMode: "sequential", TimeoutMs: 1000}
				if selection != "none" {
					compatR4Policy(t, org, "enforce", "block")
				}
				staticName := ""
				if source == "org" {
					org.Checks = map[string]*tenant.GuardrailCheck{"input-validation": {Enabled: true, Action: "block", ConfidenceThreshold: .5, Config: config}}
				}
				if source == "static" {
					staticName = "input-validation"
				}
				gateway, wire := compatR4Server(t, org, staticName, config)
				key := "a"
				if selection == "other-key" {
					key = "b"
				}
				want := 403
				if source == "none" {
					want = 200
				}
				compatR4Send(t, gateway, wire, "/v1/completions", `{"model":"gpt-3.5-turbo-instruct","prompt":["abc","def"],"max_tokens":17,"user":"synthetic-user"}`, key, want)
			})
		}
	}
}

func TestOrdinaryCompatibilityR4EmbeddingSpeech(t *testing.T) {
	for _, route := range []struct{ name, path, body string }{
		{"embedding", "/v1/embeddings", `{"model":"text-embedding-3-small","input":["clean","forbidden"],"user":"synthetic-user"}`},
		{"speech", "/v1/audio/speech", `{"model":"tts-1","input":"forbidden","voice":"alloy"}`},
		{"speech-stream", "/v1/audio/speech/stream", `{"model":"tts-1","input":"forbidden","voice":"alloy"}`},
	} {
		for _, source := range []string{"org", "static", "none"} {
			for _, selection := range []string{"none", "other-key", "block", "monitor", "warn", "log"} {
				t.Run(route.name+"/"+source+"/"+selection, func(t *testing.T) {
					config := map[string]interface{}{"words": []interface{}{"forbidden"}}
					org := &tenant.GuardrailConfig{PipelineMode: "sequential", TimeoutMs: 1000}
					key := "a"
					want := 200
					if selection != "none" {
						mode, action := "enforce", "block"
						if selection == "monitor" {
							mode = "monitor"
						}
						if selection == "warn" || selection == "log" {
							action = selection
						}
						compatR4Policy(t, org, mode, action)
						if selection == "other-key" {
							key = "b"
						}
						if selection == "block" {
							want = 403
						}
					}
					staticName := ""
					if source == "org" {
						org.Checks = map[string]*tenant.GuardrailCheck{"keyword-blocklist": {Enabled: true, Action: "block", ConfidenceThreshold: .5, Config: config}}
					}
					if source == "static" {
						staticName = "keyword-blocklist"
					}
					gateway, wire := compatR4Server(t, org, staticName, config)
					compatR4Send(t, gateway, wire, route.path, route.body, key, want)
				})
			}
		}
	}
}

func TestSelectedCompatibilityR4CompletionSuffix(t *testing.T) {
	for _, key := range []string{"a", "b"} {
		t.Run(key, func(t *testing.T) {
			org := &tenant.GuardrailConfig{PipelineMode: "parallel", TimeoutMs: 1000}
			compatR4Policy(t, org, "enforce", "block")
			gateway, wire := compatR4Server(t, org, "", nil)
			want := 200
			if key == "a" {
				want = 403
			}
			compatR4Send(t, gateway, wire, "/v1/completions", `{"model":"gpt-3.5-turbo-instruct","prompt":["abc","def"],"suffix":"forbidden","user":"synthetic-user"}`, key, want)
		})
	}
}
