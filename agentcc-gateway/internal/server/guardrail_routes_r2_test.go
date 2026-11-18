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
	"github.com/futureagi/agentcc-gateway/internal/guardrails/expression"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const routeR2Prompt = "contact alice@example.com"
const routeR2Audio = "\x00\x01synthetic-audio\xff"

// Capture the bytes actually received by a localhost provider, not rc.Request.
// Authentication headers are deliberately neither retained nor printed.
type routeR2Wire struct {
	mu     sync.Mutex
	paths  []string
	bodies [][]byte
}

func (u *routeR2Wire) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		fmt.Fprint(w, `{"data":[]}`)
		return
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read failed", http.StatusBadRequest)
		return
	}
	u.mu.Lock()
	u.paths = append(u.paths, r.URL.Path)
	u.bodies = append(u.bodies, append([]byte(nil), body...))
	u.mu.Unlock()
	switch r.URL.Path {
	case "/v1/completions":
		fmt.Fprint(w, `{"id":"r2","object":"text_completion","model":"gpt-5.5","choices":[{"index":0,"text":"safe reply","finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}`)
	case "/v1/embeddings":
		fmt.Fprint(w, `{"object":"list","model":"text-embedding-3-small","data":[{"object":"embedding","index":0,"embedding":[0.1,0.2]}],"usage":{"prompt_tokens":5,"total_tokens":5}}`)
	case "/v1/audio/speech":
		w.Header().Set("Content-Type", "audio/mpeg")
		fmt.Fprint(w, routeR2Audio)
	default:
		r.Body = io.NopCloser(bytes.NewReader(body))
		(&dialectUpstream{reply: "safe reply"}).ServeHTTP(w, r)
	}
}

func (u *routeR2Wire) snapshot() ([]string, [][]byte) {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.paths...), append([][]byte(nil), u.bodies...)
}

// Removing only the optional native-completion capability forces the existing
// translated path; all HTTP serialization and transport remain the real provider.
type routeR2ChatOnly struct{ providers.Provider }

type routeR2Options struct {
	org          *tenant.GuardrailConfig
	staticRedact bool
	translated   bool
}

func routeR2Server(t *testing.T, opts routeR2Options) (*Server, *routeR2Wire, *runtimePolicyAudit) {
	t.Helper()
	wire := &routeR2Wire{}
	up := httptest.NewServer(wire)
	t.Cleanup(up.Close)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "selected", Key: "sk-agentcc-r2-a", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-r2"}},
		{Name: "other-key", Key: "sk-agentcc-r2-b", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-r2"}},
		{Name: "other-org", Key: "sk-agentcc-r2-c", Owner: "synthetic", KeyType: "internal", Metadata: map[string]string{"org_id": "org-r2-other"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-5.5", "gpt-3.5-turbo-instruct", "text-embedding-3-small", "tts-1"}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}
	static := map[string]guardrails.Guardrail{}
	if opts.staticRedact {
		static["r2-static-redact"] = pii.New(map[string]interface{}{"remediation": "redact"})
		cfg.Guardrails.Rules = []config.GuardrailRuleConfig{{Name: "r2-static-redact", Stage: "pre", Mode: "sync", Action: "log", Threshold: .8}}
	}
	store := tenant.NewStore()
	store.Set("org-r2", &tenant.OrgConfig{Guardrails: opts.org})
	gr := guardrails.NewEngine(cfg.Guardrails, static)
	factory := func(name string, c map[string]interface{}) guardrails.Guardrail {
		switch name {
		case "pii-detection":
			return pii.New(c)
		case "r2-post":
			return runtimePostCheck{}
		}
		if expression.IsExpressionConfig(c) {
			return expression.New(name, c)
		}
		if webhook.IsWebhookConfig(c) {
			return webhook.New(name, c)
		}
		return nil
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if opts.translated {
		p, ok := registry.GetProvider("openai")
		if !ok {
			t.Fatal("missing OpenAI provider")
		}
		registry.RegisterProvider("openai", routeR2ChatOnly{p}, nil)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	audit := &runtimePolicyAudit{finalized: make(chan struct{}, 1)}
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true), guardrails.NewPlugin(gr, static, factory, nil, store), audit), keys, gr, nil, nil, testModelDBPtr(), store, nil)
	srv.handlers.SetCaptureStreamContent(false)
	srv.ready.Store(true)
	return srv, wire, audit
}

func routeR2Policy(mode, action, name string, cfg map[string]interface{}) *tenant.KeyGuardrailPolicy {
	return &tenant.KeyGuardrailPolicy{ID: "r2-block", Name: "R2 selected check", Mode: mode, Priority: 2, KeyIDs: []string{"key_1"}, Checks: map[string]*tenant.GuardrailCheck{
		name: {Enabled: true, Action: action, ConfidenceThreshold: .8, Config: cfg},
	}}
}

func routeR2Org(policies ...*tenant.KeyGuardrailPolicy) *tenant.GuardrailConfig {
	return &tenant.GuardrailConfig{PipelineMode: "sequential", TimeoutMs: 1000, KeyPolicies: policies}
}

func routeR2Send(srv *Server, path, body, key string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer sk-agentcc-r2-"+key)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}

func routeR2JSON(t *testing.T, value interface{}) string {
	t.Helper()
	b, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// Check the exact serialization, including all forwarded options, and emit only
// synthetic wire bytes for the bounded red/green evidence matrix.
func routeR2AssertWire(t *testing.T, wire *routeR2Wire, status int, w *httptest.ResponseRecorder, path, body string) {
	t.Helper()
	paths, bodies := wire.snapshot()
	t.Logf("status=%d upstream_calls=%d paths=%q synthetic_wire=%q", w.Code, len(bodies), paths, bodies)
	if w.Code != status {
		t.Errorf("status=%d want=%d body=%s", w.Code, status, w.Body.String())
	}
	wantCalls := 0
	if body != "" {
		wantCalls = 1
	}
	if len(bodies) != wantCalls {
		t.Fatalf("upstream calls=%d want=%d", len(bodies), wantCalls)
	}
	if wantCalls == 1 && (paths[0] != path || string(bodies[0]) != body) {
		t.Errorf("wire path=%q body=%s; want path=%q body=%s", paths[0], bodies[0], path, body)
	}
}

func routeR2AssertOutcome(t *testing.T, audit *runtimePolicyAudit, name, mode, outcome string) models.GuardrailResult {
	t.Helper()
	results, _ := audit.snapshot()
	t.Logf("sanitized_attribution=%s", routeR2JSON(t, results))
	for _, r := range results {
		if strings.Contains(routeR2JSON(t, r), "alice@example.com") {
			t.Fatal("attribution leaked synthetic PII")
		}
		if r.PolicyID == "r2-block" && r.PolicyScope == "key" && r.Name == name && r.Mode == mode && r.Outcome == outcome {
			return r
		}
	}
	t.Fatalf("missing %s/%s/%s attribution: %+v", name, mode, outcome, results)
	return models.GuardrailResult{}
}

// Native projections are inspection-only; translated completion remediation is
// wire-backed. In particular an earlier remediator must not conceal an email
// from a later Block while forwarding the unchanged native CompletionRequest.
func TestGuardrailRoutesR2CompletionRemediationActualWire(t *testing.T) {
	for _, source := range []string{"static", "org", "key"} {
		for _, path := range []string{"native", "translated", "translated-stream"} {
			for _, mode := range []string{"enforce", "monitor"} {
				for _, action := range []string{"block", "log"} {
					for _, key := range []string{"a", "b"} {
						t.Run(source+"/"+path+"/"+mode+"/"+action+"/key="+key, func(t *testing.T) {
							block := routeR2Policy(mode, action, "pii-detection", nil)
							org := routeR2Org(block)
							redact := &tenant.GuardrailCheck{Enabled: true, Action: "log", ConfidenceThreshold: .8, Config: map[string]interface{}{"remediation": "redact"}}
							switch source {
							case "org":
								org.Checks = map[string]*tenant.GuardrailCheck{"pii-detection": redact}
							case "key":
								org.KeyPolicies = append(org.KeyPolicies, &tenant.KeyGuardrailPolicy{ID: "r2-redact", Name: "R2 redact first", Mode: mode, Priority: 1, KeyIDs: []string{"key_1"}, Checks: map[string]*tenant.GuardrailCheck{"pii-detection": redact}})
							}
							srv, wire, audit := routeR2Server(t, routeR2Options{org: org, staticRedact: source == "static", translated: path == "translated"})
							body := fmt.Sprintf(`{"model":"gpt-5.5","prompt":%q,"max_tokens":17,"temperature":0.2,"top_p":0.9,"n":1,"stop":["END"],"presence_penalty":0.1,"frequency_penalty":0.2,"logit_bias":{"42":1},"logprobs":2,"echo":true,"suffix":"finish","best_of":1,"user":"synthetic-user","seed":7,"stream":%v}`, routeR2Prompt, path == "translated-stream")
							var req models.CompletionRequest
							if err := json.Unmarshal([]byte(body), &req); err != nil {
								t.Fatal(err)
							}
							blocked := path == "native" && key == "a" && mode == "enforce" && action == "block"
							status, expectedPath, expectedBody := 200, "/v1/completions", routeR2JSON(t, &req)
							if path != "native" {
								chat := req.ToChatRequest()
								if source != "key" || (key == "a" && mode == "enforce") {
									chat.Messages[0].Content = json.RawMessage(`"contact [REDACTED:email]"`)
								}
								if req.Stream {
									chat.StreamOptions = &models.StreamOptions{IncludeUsage: true}
								}
								expectedPath, expectedBody = "/v1/chat/completions", routeR2JSON(t, chat)
							}
							if blocked {
								status, expectedBody = 403, ""
							}
							w := routeR2Send(srv, "/v1/completions", body, key)
							routeR2AssertWire(t, wire, status, w, expectedPath, expectedBody)
							if blocked {
								routeR2AssertOutcome(t, audit, "pii-detection", mode, "blocked")
							}
							if key == "a" && path == "native" && mode == "monitor" && action == "block" {
								routeR2AssertOutcome(t, audit, "pii-detection", mode, "would_block")
							}
							if req.Stream && !strings.Contains(w.Body.String(), "[DONE]") {
								t.Fatal("translated stream did not complete")
							}
						})
					}
				}
			}
		}
	}
}

func TestGuardrailRoutesR2NativeLegacyStreamUnchanged(t *testing.T) {
	for _, key := range []string{"a", "b"} {
		t.Run(key, func(t *testing.T) {
			srv, wire, _ := routeR2Server(t, routeR2Options{org: routeR2Org(routeR2Policy("enforce", "block", "pii-detection", nil))})
			w := routeR2Send(srv, "/v1/completions", `{"model":"gpt-3.5-turbo-instruct","prompt":"contact alice@example.com","stream":true}`, key)
			routeR2AssertWire(t, wire, 501, w, "", "")
			if !strings.Contains(w.Body.String(), "streaming_not_supported") {
				t.Fatalf("legacy refusal changed: %s", w.Body.String())
			}
		})
	}
}

func TestGuardrailRoutesR2EmbeddingTextActualWire(t *testing.T) {
	for _, tc := range []struct {
		name, input string
		violation   bool
	}{
		{"string", `"contact alice@example.com"`, true},
		{"array-first", `["alice@example.com","clean","also clean"]`, true},
		{"array-middle", `["clean","alice@example.com","also clean"]`, true},
		{"array-last", `["clean","also clean","alice@example.com"]`, true},
		{"clean", `"clean"`, false},
		{"empty-string", `""`, false},
		{"empty-texts", `["",""]`, false},
	} {
		for _, mode := range []string{"enforce", "monitor"} {
			t.Run(tc.name+"/"+mode, func(t *testing.T) {
				srv, wire, audit := routeR2Server(t, routeR2Options{org: routeR2Org(routeR2Policy(mode, "block", "pii-detection", nil))})
				body := `{"model":"text-embedding-3-small","input":` + tc.input + `,"encoding_format":"float","dimensions":2,"user":"synthetic-user"}`
				status, expected := 200, body
				if tc.violation && mode == "enforce" {
					status, expected = 403, ""
				}
				w := routeR2Send(srv, "/v1/embeddings", body, "a")
				routeR2AssertWire(t, wire, status, w, "/v1/embeddings", expected)
				if tc.violation {
					outcome := "blocked"
					if mode == "monitor" {
						outcome = "would_block"
					}
					routeR2AssertOutcome(t, audit, "pii-detection", mode, outcome)
				}
			})
		}
	}
}

// Token IDs cannot be semantically inspected by a text checker. Blocking
// enforcement refuses the capability, independently of fail_open. Monitor
// observes an unavailable/unsupported check, never a fictitious content pass.
func TestGuardrailRoutesR2EmbeddingTokenInputAdmission(t *testing.T) {
	for _, input := range []string{`[101,202,303]`, `[[101,202],[303,404]]`} {
		for _, mode := range []string{"enforce", "monitor"} {
			for _, open := range []bool{false, true} {
				for _, key := range []string{"a", "b"} {
					t.Run(fmt.Sprintf("%s/%s/open=%v/key=%s", input, mode, open, key), func(t *testing.T) {
						org := routeR2Org(routeR2Policy(mode, "block", "pii-detection", nil))
						org.FailOpen = open
						srv, wire, audit := routeR2Server(t, routeR2Options{org: org})
						body := `{"model":"text-embedding-3-small","input":` + input + `,"encoding_format":"float","dimensions":2,"user":"synthetic-user"}`
						status, expected := 200, body
						blocked := key == "a" && mode == "enforce"
						if blocked {
							status, expected = 403, ""
						}
						w := routeR2Send(srv, "/v1/embeddings", body, key)
						routeR2AssertWire(t, wire, status, w, "/v1/embeddings", expected)
						if blocked && !strings.Contains(w.Body.String(), "input_guardrail_unsupported") {
							t.Fatalf("missing explicit unsupported refusal: %s", w.Body.String())
						}
						if key == "a" {
							outcome := "blocked"
							if mode == "monitor" {
								outcome = "logged"
							}
							r := routeR2AssertOutcome(t, audit, "pii-detection", mode, outcome)
							if !strings.Contains(strings.ToLower(r.Message), "unsupported") && !strings.Contains(strings.ToLower(r.Message), "unavailable") {
								t.Fatalf("token check did not record execution limitation: %+v", r)
							}
						}
					})
				}
			}
		}
	}
}

func TestGuardrailRoutesR2HTTPSpeechInputActualWire(t *testing.T) {
	for _, path := range []string{"/v1/audio/speech", "/v1/audio/speech/stream"} {
		for _, mode := range []string{"enforce", "monitor"} {
			for _, prompt := range []string{routeR2Prompt, "clean synthesis"} {
				t.Run(path+"/"+mode+"/"+prompt, func(t *testing.T) {
					srv, wire, audit := routeR2Server(t, routeR2Options{org: routeR2Org(routeR2Policy(mode, "block", "pii-detection", nil))})
					// SpeechRequest has no user field; preserve its actual supported options.
					body := fmt.Sprintf(`{"model":"tts-1","input":%q,"voice":"alloy","response_format":"mp3","speed":1.25}`, prompt)
					status, expected := 200, body
					if mode == "enforce" && prompt == routeR2Prompt {
						status, expected = 403, ""
					}
					w := routeR2Send(srv, path, body, "a")
					routeR2AssertWire(t, wire, status, w, "/v1/audio/speech", expected)
					if prompt == routeR2Prompt {
						outcome := "blocked"
						if mode == "monitor" {
							outcome = "would_block"
						}
						routeR2AssertOutcome(t, audit, "pii-detection", mode, outcome)
					}
					if status == 200 {
						routeR2AssertAudio(t, path, w)
					}
				})
			}
		}
	}
}

func routeR2AssertAudio(t *testing.T, path string, w *httptest.ResponseRecorder) {
	t.Helper()
	if strings.HasSuffix(path, "/stream") {
		if !strings.Contains(w.Body.String(), base64.StdEncoding.EncodeToString([]byte(routeR2Audio))) || !strings.Contains(w.Body.String(), "[DONE]") {
			t.Fatalf("audio SSE did not preserve binary data: %q", w.Body.String())
		}
	} else if w.Body.String() != routeR2Audio || w.Header().Get("Content-Type") != "audio/mpeg" {
		t.Fatalf("binary audio changed: %q", w.Body.String())
	}
}

func TestGuardrailRoutesR2HTTPSpeechPostAdmission(t *testing.T) {
	for _, path := range []string{"/v1/audio/speech", "/v1/audio/speech/stream"} {
		for _, mode := range []string{"enforce", "monitor"} {
			for _, open := range []bool{false, true} {
				for _, key := range []string{"a", "b"} {
					t.Run(fmt.Sprintf("%s/%s/open=%v/key=%s", path, mode, open, key), func(t *testing.T) {
						org := routeR2Org(routeR2Policy(mode, "block", "r2-post", nil))
						org.FailOpen = open
						srv, wire, audit := routeR2Server(t, routeR2Options{org: org})
						body := `{"model":"tts-1","input":"clean synthesis","voice":"alloy","response_format":"mp3","speed":1.25}`
						status, expected := 200, body
						if key == "a" && mode == "enforce" {
							status, expected = 403, ""
						}
						w := routeR2Send(srv, path, body, key)
						routeR2AssertWire(t, wire, status, w, "/v1/audio/speech", expected)
						if status == 403 {
							if !strings.Contains(w.Body.String(), "output_guardrail_unsupported") {
								t.Fatalf("missing binary-output unsupported reason: %s", w.Body.String())
							}
							routeR2AssertOutcome(t, audit, "r2-post", mode, "blocked")
						} else {
							routeR2AssertAudio(t, path, w)
							if key == "a" && mode == "monitor" {
								routeR2AssertOutcome(t, audit, "r2-post", mode, "logged")
							}
						}
					})
				}
			}
		}
	}
}

func TestGuardrailRoutesR2ResponsesUserEvaluatorParity(t *testing.T) {
	for _, evaluator := range []string{"expression", "webhook"} {
		for _, route := range []string{"responses", "chat"} {
			for _, user := range []string{"restricted", "allowed", ""} {
				for _, mode := range []string{"enforce", "monitor"} {
					for _, key := range []string{"a", "b"} {
						t.Run(evaluator+"/"+route+"/user="+user+"/"+mode+"/key="+key, func(t *testing.T) {
							var hookMu sync.Mutex
							var hookBodies [][]byte
							hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
								body, _ := io.ReadAll(r.Body)
								hookMu.Lock()
								hookBodies = append(hookBodies, body)
								hookMu.Unlock()
								var payload struct {
									Request models.ChatCompletionRequest `json:"request"`
								}
								if err := json.Unmarshal(body, &payload); err != nil {
									http.Error(w, "bad evaluator body", 400)
									return
								}
								if payload.Request.User == "restricted" {
									fmt.Fprint(w, `{"pass":false,"score":1,"message":"synthetic restricted user"}`)
								} else {
									fmt.Fprint(w, `{"pass":true,"score":0}`)
								}
							}))
							defer hook.Close()
							checkCfg := map[string]interface{}{"expression": "request.user == 'restricted'"}
							if evaluator == "webhook" {
								checkCfg = map[string]interface{}{"url": hook.URL}
							}
							srv, wire, audit := routeR2Server(t, routeR2Options{org: routeR2Org(routeR2Policy(mode, "block", "r2-user", checkCfg))})
							path, body := "/v1/responses", `{"model":"gpt-5.5","input":"hello"`
							if route == "chat" {
								path, body = "/v1/chat/completions", `{"model":"gpt-5.5","messages":[{"role":"user","content":"hello"}]`
							}
							if user != "" {
								body += `,"user":` + routeR2JSON(t, user)
							}
							body += "}"
							status, expected := 200, body
							if key == "a" && mode == "enforce" && user == "restricted" {
								status, expected = 403, ""
							}
							w := routeR2Send(srv, path, body, key)
							routeR2AssertWire(t, wire, status, w, path, expected)
							if key == "a" && user == "restricted" {
								outcome := "blocked"
								if mode == "monitor" {
									outcome = "would_block"
								}
								routeR2AssertOutcome(t, audit, "r2-user", mode, outcome)
							}
							hookMu.Lock()
							captured := append([][]byte(nil), hookBodies...)
							hookMu.Unlock()
							wantCalls := 0
							if evaluator == "webhook" && key == "a" {
								wantCalls = 1
							}
							if len(captured) != wantCalls {
								t.Fatalf("webhook calls=%d want=%d", len(captured), wantCalls)
							}
							if wantCalls == 1 {
								var payload struct {
									Request  models.ChatCompletionRequest `json:"request"`
									Metadata map[string]string            `json:"metadata"`
								}
								if err := json.Unmarshal(captured[0], &payload); err != nil {
									t.Fatal(err)
								}
								// Do not log metadata: auth machinery may attach credentials there.
								t.Logf("webhook_calls=1 projected_request=%s", routeR2JSON(t, payload.Request))
								if payload.Request.User != user || payload.Request.Model != "gpt-5.5" || len(payload.Request.Messages) != 1 || string(payload.Request.Messages[0].Content) != `"hello"` || payload.Request.Messages[0].Role != "user" {
									t.Fatalf("webhook request context mismatch: %+v", payload.Request)
								}
								if payload.Metadata["org_id"] != "org-r2" || payload.Metadata["auth_key_id"] != "key_1" {
									t.Fatal("webhook lost selected key/org context")
								}
							}
						})
					}
				}
			}
		}
	}
}

// is_active is not a gateway wire field: inactive policies are omitted by the
// control plane. The no-policy case tests that representation, not an invented flag.
func TestGuardrailRoutesR2SelectionControls(t *testing.T) {
	for _, rt := range []struct{ name, path, body, upstream string }{
		{"completion", "/v1/completions", `{"model":"gpt-5.5","prompt":"contact alice@example.com"}`, "/v1/completions"},
		{"embedding", "/v1/embeddings", `{"model":"text-embedding-3-small","input":"alice@example.com"}`, "/v1/embeddings"},
		{"speech", "/v1/audio/speech", `{"model":"tts-1","input":"alice@example.com","voice":"alloy"}`, "/v1/audio/speech"},
		{"speech-stream", "/v1/audio/speech/stream", `{"model":"tts-1","input":"alice@example.com","voice":"alloy"}`, "/v1/audio/speech"},
		{"responses", "/v1/responses", `{"model":"gpt-5.5","input":"alice@example.com","user":"restricted"}`, "/v1/responses"},
	} {
		for _, control := range []string{"other-key", "other-org", "no-selection", "disabled-check", "inactive-omitted", "log-only", "clean"} {
			t.Run(rt.name+"/"+control, func(t *testing.T) {
				p := routeR2Policy("enforce", "block", "pii-detection", nil)
				key, body := "a", rt.body
				org := routeR2Org(p)
				switch control {
				case "other-key":
					key = "b"
				case "other-org":
					key = "c"
				case "no-selection":
					p.KeyIDs = nil
				case "disabled-check":
					p.Checks["pii-detection"].Enabled = false
				case "inactive-omitted":
					org.KeyPolicies = nil
				case "log-only":
					p.Checks["pii-detection"].Action = "log"
				case "clean":
					body = strings.ReplaceAll(body, "alice@example.com", "the team")
				}
				srv, wire, audit := routeR2Server(t, routeR2Options{org: org})
				w := routeR2Send(srv, rt.path, body, key)
				routeR2AssertWire(t, wire, 200, w, rt.upstream, body)
				if control == "log-only" {
					routeR2AssertOutcome(t, audit, "pii-detection", "enforce", "logged")
				} else if results, _ := audit.snapshot(); len(results) != 0 {
					t.Fatalf("unselected/disabled/clean traffic attributed: %+v", results)
				}
			})
		}
	}
}

func TestGuardrailRoutesR2EmbeddingOutputAdmission(t *testing.T) {
	for _, mode := range []string{"enforce", "monitor"} {
		for _, open := range []bool{false, true} {
			for _, key := range []string{"a", "b"} {
				t.Run(fmt.Sprintf("%s/open=%v/key=%s", mode, open, key), func(t *testing.T) {
					org := routeR2Org(routeR2Policy(mode, "block", "r2-post", nil))
					org.FailOpen = open
					srv, wire, audit := routeR2Server(t, routeR2Options{org: org})
					body := `{"model":"text-embedding-3-small","input":"clean text"}`
					status, expected := 200, body
					if key == "a" && mode == "enforce" {
						status, expected = 403, ""
					}
					w := routeR2Send(srv, "/v1/embeddings", body, key)
					routeR2AssertWire(t, wire, status, w, "/v1/embeddings", expected)
					if status == 403 && !strings.Contains(w.Body.String(), "output_guardrail_unsupported") {
						t.Fatalf("missing vector-output refusal: %s", w.Body.String())
					}
					if key == "a" {
						outcome := "blocked"
						if mode == "monitor" {
							outcome = "logged"
						}
						routeR2AssertOutcome(t, audit, "r2-post", mode, outcome)
					}
				})
			}
		}
	}
}

// A webhook inspects the real Responses context, including supported non-text
// fields. The request retains its original provider bytes in both stream modes.
func TestGuardrailRoutesR2ResponsesWebhookSupportedContext(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			var mu sync.Mutex
			var requests []models.ChatCompletionRequest
			hook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var payload struct {
					Request models.ChatCompletionRequest `json:"request"`
				}
				if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
					http.Error(w, "bad evaluator body", 400)
					return
				}
				mu.Lock()
				requests = append(requests, payload.Request)
				mu.Unlock()
				fmt.Fprint(w, `{"pass":true,"score":0}`)
			}))
			defer hook.Close()
			srv, wire, _ := routeR2Server(t, routeR2Options{org: routeR2Org(routeR2Policy("enforce", "block", "r2-user", map[string]interface{}{"url": hook.URL}))})
			body := fmt.Sprintf(`{"model":"gpt-5.5","input":"hello","instructions":"synthetic instruction","user":"allowed","stream":%v,"temperature":0.25,"top_p":0.75,"max_output_tokens":17,"metadata":{"case":"r2"},"store":false}`, stream)
			w := routeR2Send(srv, "/v1/responses", body, "a")
			routeR2AssertWire(t, wire, 200, w, "/v1/responses", body)
			mu.Lock()
			captured := append([]models.ChatCompletionRequest(nil), requests...)
			mu.Unlock()
			if len(captured) != 1 {
				t.Fatalf("webhook calls=%d want=1", len(captured))
			}
			r := captured[0]
			t.Logf("webhook_calls=1 projected_request=%s", routeR2JSON(t, r))
			if r.User != "allowed" || r.Stream != stream || r.Temperature == nil || *r.Temperature != .25 || r.TopP == nil || *r.TopP != .75 {
				t.Fatalf("supported evaluator fields missing: %+v", r)
			}
			if len(r.Messages) != 2 || r.Messages[0].Role != "system" || string(r.Messages[0].Content) != `"synthetic instruction"` || r.Messages[1].Role != "user" || string(r.Messages[1].Content) != `"hello"` {
				t.Fatalf("instructions/input context changed: %+v", r.Messages)
			}
			if string(r.Extra["metadata"]) != `{"case":"r2"}` {
				t.Fatalf("request metadata missing from projection: %s", r.Extra["metadata"])
			}
			if stream && !strings.Contains(w.Body.String(), "response.completed") {
				t.Fatal("Responses stream did not complete")
			}
		})
	}
}

func TestGuardrailRoutesR2EmbeddingDegenerateInput(t *testing.T) {
	for _, field := range []string{``, `,"input":null`, `,"input":[]`, `,"input":{}`, `,"input":["hello",101]`} {
		for _, key := range []string{"a", "b"} {
			t.Run(fmt.Sprintf("%s/key=%s", field, key), func(t *testing.T) {
				srv, wire, audit := routeR2Server(t, routeR2Options{org: routeR2Org(routeR2Policy("enforce", "block", "pii-detection", nil))})
				body := `{"model":"text-embedding-3-small"` + field + `}`
				var req models.EmbeddingRequest
				if err := json.Unmarshal([]byte(body), &req); err != nil {
					t.Fatal(err)
				}
				status, expected := 200, routeR2JSON(t, req)
				if key == "a" {
					status, expected = 403, ""
				}
				w := routeR2Send(srv, "/v1/embeddings", body, key)
				routeR2AssertWire(t, wire, status, w, "/v1/embeddings", expected)
				if key == "a" {
					if !strings.Contains(w.Body.String(), "input_guardrail_unsupported") {
						t.Fatalf("missing explicit unsupported input error: %s", w.Body.String())
					}
					routeR2AssertOutcome(t, audit, "pii-detection", "enforce", "blocked")
				}
			})
		}
	}
}

func TestGuardrailRoutesR2NativeCompletionTokensAndSuffix(t *testing.T) {
	for _, tc := range []struct{ name, prompt, suffix, code string }{
		{"tokens", `[101,202]`, `"clean suffix"`, "input_guardrail_unsupported"},
		{"token-batch", `[[101,202],[303]]`, `"clean suffix"`, "input_guardrail_unsupported"},
		{"suffix-pii", `"clean prompt"`, `"alice@example.com"`, "content_blocked"},
		{"text-array", `["clean prompt","alice@example.com"]`, `"clean suffix"`, "content_blocked"},
	} {
		for _, key := range []string{"a", "b"} {
			t.Run(tc.name+"/key="+key, func(t *testing.T) {
				org := routeR2Org(routeR2Policy("enforce", "block", "pii-detection", nil))
				org.FailOpen = true
				srv, wire, audit := routeR2Server(t, routeR2Options{org: org})
				body := `{"model":"gpt-3.5-turbo-instruct","prompt":` + tc.prompt + `,"suffix":` + tc.suffix + `}`
				status, expected := 200, body
				if key == "a" {
					status, expected = 403, ""
				}
				w := routeR2Send(srv, "/v1/completions", body, key)
				routeR2AssertWire(t, wire, status, w, "/v1/completions", expected)
				if key == "a" {
					if !strings.Contains(w.Body.String(), tc.code) {
						t.Fatalf("missing %s: %s", tc.code, w.Body.String())
					}
					routeR2AssertOutcome(t, audit, "pii-detection", "enforce", "blocked")
				}
			})
		}
	}
}

func TestGuardrailRoutesR2MalformedClientInput(t *testing.T) {
	for _, tc := range []struct{ path, body, code string }{
		{"/v1/completions", `{"model":"gpt-5.5"}`, "missing_prompt"},
		{"/v1/completions", `{"prompt":"hello"}`, "missing_model"},
		{"/v1/embeddings", `{"input":"hello"}`, "missing_model"},
		{"/v1/embeddings", `{"model":"text-embedding-3-small","input":`, "invalid_json"},
		{"/v1/audio/speech", `{"model":"tts-1","input":"","voice":"alloy"}`, "missing_input"},
		{"/v1/audio/speech/stream", `{"model":"tts-1","input":"","voice":"alloy"}`, "missing_input"},
		{"/v1/audio/speech", `{"model":"tts-1","input":123,"voice":"alloy"}`, "invalid_json"},
		{"/v1/audio/speech/stream", `{"model":"tts-1","input":"hello"}`, "missing_voice"},
		{"/v1/responses", `{"model":`, "invalid_json"},
	} {
		t.Run(tc.path+"/"+tc.code, func(t *testing.T) {
			srv, wire, _ := routeR2Server(t, routeR2Options{org: routeR2Org(routeR2Policy("enforce", "block", "pii-detection", nil))})
			w := routeR2Send(srv, tc.path, tc.body, "a")
			routeR2AssertWire(t, wire, 400, w, "", "")
			if !strings.Contains(w.Body.String(), tc.code) {
				t.Fatalf("ordinary validation changed: %s", w.Body.String())
			}
		})
	}
}
