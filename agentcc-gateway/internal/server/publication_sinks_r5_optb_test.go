package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const r5SinkKey = `W2 opaque "sent\key" /+?=<> 0123456789 not-live`

// Decode every JSON leaf (including each SSE data and slog record), so JSON
// escaping cannot make a leaking sink appear clean.
func r5SinkLeaves(t *testing.T, data string) string {
	t.Helper()
	var out strings.Builder
	var visit func(any)
	visit = func(v any) {
		switch v := v.(type) {
		case string:
			out.WriteString(v)
			out.WriteByte('\n')
		case map[string]any:
			for k, x := range v {
				out.WriteString(k)
				visit(x)
			}
		case []any:
			for _, x := range v {
				visit(x)
			}
		}
	}
	for _, line := range strings.Split(data, "\n") {
		line = strings.TrimPrefix(line, "data: ")
		var v any
		if json.Unmarshal([]byte(line), &v) == nil {
			visit(v)
		}
	}
	return out.String()
}

// INT-R1-01/02/03: actual admitted routes, actual adapter, and the exact key
// witnessed on the outgoing request. No manually projected test writer.
func TestOptBR5RoutePublicationSinks(t *testing.T) {
	for _, route := range []string{"speech", "messages", "collision-chat"} {
		for _, byok := range []bool{false, true} {
			for spelling, spell := range optBG1Spellings() {
				for _, field := range []string{"message", "code", "type"} {
					t.Run(fmt.Sprintf("%s/byok=%t/%s/%s", route, byok, spelling, field), func(t *testing.T) {
						var logs bytes.Buffer
						old := slog.Default()
						slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, nil)))
						defer slog.SetDefault(old)
						var calls atomic.Int32
						credential := r5SinkKey
						if byok {
							credential = "tenant " + r5SinkKey
						}
						reflected := spell(credential)
						up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
							if r.URL.Path == "/v1/models" {
								_, _ = io.WriteString(w, `{"object":"list","data":[]}`)
								return
							}
							calls.Add(1)
							if r.Header.Get("Authorization") != "Bearer "+credential {
								t.Error("upstream did not receive the planted owner credential")
							}
							wantPath := "/v1/chat/completions"
							if route == "speech" {
								wantPath = "/v1/audio/speech"
							}
							if r.URL.Path != wantPath {
								t.Errorf("upstream path=%s, want %s", r.URL.Path, wantPath)
							}
							detail := map[string]string{"message": "benign diagnostic", "code": "fixture_rejection", "type": "invalid_request_error"}
							detail[field] += " " + reflected
							if route == "messages" {
								w.Header().Set("Content-Type", "text/event-stream")
								_, _ = io.WriteString(w, "data: {\"id\":\"msg_r5\",\"object\":\"chat.completion.chunk\",\"model\":\"fixture-model\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"ordinary streamed output\"}}]}\n\n")
								w.(http.Flusher).Flush()
								body, _ := json.Marshal(map[string]any{"error": detail})
								_, _ = fmt.Fprintf(w, "data: %s\n\n", body)
								return
							}
							w.Header().Set("Content-Type", "application/json")
							w.WriteHeader(http.StatusBadRequest)
							_ = json.NewEncoder(w).Encode(map[string]any{"error": detail})
						}))
						defer up.Close()
						cfg := config.DefaultConfig()
						cfg.Auth.Enabled = true
						const caller = "r5-internal-caller-not-live"
						keyType := "internal"
						if byok {
							keyType = "byok"
						}
						cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "fixture", Key: caller, Owner: "fixture", KeyType: keyType, Metadata: map[string]string{"org_id": "acme"}}}
						id := "edge"
						if route == "collision-chat" {
							id = "edge_org_acme"
						}
						cfg.Providers = map[string]config.ProviderConfig{id: {BaseURL: up.URL, APIKey: r5SinkKey, APIFormat: "openai", Models: []string{"fixture-model"}}}
						registry, err := providers.NewRegistry(cfg)
						if err != nil {
							t.Fatal(err)
						}
						defer registry.Close()
						store := tenant.NewStore()
						if byok {
							store.Set("acme", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{id: {Enabled: true, APIKey: credential, Models: []string{"fixture-model"}}}})
						}
						s := New(cfg, "", registry, pipeline.NewEngine(), auth.NewKeyStore(cfg.Auth), nil, nil, nil, testModelDBPtr(), store, nil)
						s.ready.Store(true)
						path, body := "/v1/audio/speech/stream", `{"model":"fixture-model","input":"hello","voice":"alloy"}`
						if route == "messages" {
							path, body = "/v1/messages", `{"model":"fixture-model","max_tokens":20,"messages":[{"role":"user","content":"hello"}],"stream":true}`
						} else if route == "collision-chat" {
							path, body = "/v1/chat/completions", `{"model":"fixture-model","messages":[{"role":"user","content":"hello"}]}`
						}
						ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
						defer cancel()
						r := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body)).WithContext(ctx)
						r.Header.Set("Authorization", "Bearer "+caller)
						r.Header.Set("Content-Type", "application/json")
						w := httptest.NewRecorder()
						s.httpServer.Handler.ServeHTTP(w, r)
						if calls.Load() != 1 || ctx.Err() != nil {
							t.Fatalf("provider calls=%d, context=%v", calls.Load(), ctx.Err())
						}
						if route == "messages" {
							if w.Code != 200 || strings.Count(w.Body.String(), "event: error\n") != 1 || !strings.Contains(w.Body.String(), "ordinary streamed output") || strings.Contains(w.Body.String(), "event: message_stop\n") {
								t.Fatal("expected admitted output followed by exactly one terminal error, not successful completion")
							}
							if !strings.Contains(logs.String(), `"msg":"provider stream error"`) {
								t.Fatal("terminal error diagnostic was not logged")
							}
						} else if w.Code != 400 || strings.Contains(w.Body.String(), "data:") {
							t.Fatalf("expected HTTP 400 before SSE, got %d", w.Code)
						}
						for sink, raw := range map[string]string{"client": w.Body.String(), "slog": logs.String()} {
							text := r5SinkLeaves(t, raw)
							if strings.Contains(text, reflected) || strings.Contains(text, r5SinkKey) {
								t.Errorf("%s retained sent credential in %s spelling", sink, spelling)
							}
							if sink == "client" {
								if byok && !strings.Contains(text, "benign diagnostic") {
									t.Error("BYOK lost non-secret provider diagnostic")
								}
								if !byok && strings.Contains(text, "benign diagnostic") {
									t.Error("platform diagnostics bypassed closed-category projection")
								}
							}
						}
					})
				}
			}
		}
	}
}

// The source-bound stream copy must ignore later billing/route state and leave
// the original adapter error (including evidence and private ownership) intact.
func TestOptBR5StreamPublicationUsesConstructedInstance(t *testing.T) {
	cfg := config.ProviderConfig{APIFormat: "openai", BaseURL: "http://127.0.0.1:1", APIKey: "fixture-only"}
	cache := providers.NewOrgProviderCache(map[string]config.ProviderConfig{"edge": cfg}, false)
	p, err := cache.GetOrCreate("acme", "edge", "fixture-tenant")
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	for _, selected := range []providers.Provider{p, optBOwnerProvider{id: "edge_org_acme"}} {
		rc := models.AcquireRequestContext()
		rc.Provider, rc.CredentialSource = "edge", "org"
		rc.Metadata["org_id"], rc.Metadata["billing_mode"] = "acme", "byok"
		rc.AddAttempt(models.Attempt{Provider: "prior"})
		rc.AddAttempt(models.Attempt{Provider: "edge"})
		e := &models.APIError{Status: 400, ProviderStatus: 400, Message: "benign diagnostic", Code: "fixture", Type: "invalid_request_error"}
		before := *e
		out := clientStreamError(rc, e, selected)
		wantBYOK := selected == p
		if strings.Contains(out.Message, e.Message) != wantBYOK {
			t.Error("publication trusted provider name instead of org-cache provenance")
		}
		if e.Message != before.Message || e.Provider != before.Provider {
			t.Error("publication mutated original error")
		}
		if _, bound := e.PublicationCredentialOwner(); bound {
			t.Error("publication bound owner on original error")
		}
		rc.Release()
	}
}
