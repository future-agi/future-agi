package server

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/requestview"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const r4dPlatformCanary = "R4D_OPAQUE_PLATFORM_CREDENTIAL_NOT_LIVE"

// Exercise the real /v1/responses route and OpenAI adapter, not just the
// publication helper. Each failing response echoes the exact credential the
// adapter sent; streaming requests fail before their first downstream byte.
func TestOptBR4DResponsesNon200Publication(t *testing.T) {
	for _, byok := range []bool{false, true} {
		for _, stream := range []bool{false, true} {
			for _, status := range []int{201, 302, 400, 401, 403, 404, 429, 500, 502, 503} {
				for _, raw := range []bool{false, true} {
					t.Run(fmt.Sprintf("byok=%t/stream=%t/status=%d/raw=%t", byok, stream, status, raw), func(t *testing.T) {
						credential := r4dPlatformCanary
						if byok {
							credential = "sk-" + strings.Repeat("r", 40)
						}
						message := "provider diagnostic " + credential
						var calls atomic.Int32
						up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
							// Server construction probes the provider's model list. That
							// is not a Responses attempt and must not consume its fixture.
							if r.URL.Path == "/v1/models" {
								w.Header().Set("Content-Type", "application/json")
								_, _ = io.WriteString(w, `{"object":"list","data":[]}`)
								return
							}
							calls.Add(1)
							if r.URL.Path != "/v1/responses" || r.Header.Get("Authorization") != "Bearer "+credential {
								t.Error("fixture did not reach the Responses adapter with the intended owner credential")
							}
							var sent struct {
								Stream bool `json:"stream"`
							}
							if err := json.NewDecoder(r.Body).Decode(&sent); err != nil || sent.Stream != stream {
								t.Error("stream request not preserved")
							}
							w.WriteHeader(status)
							if raw {
								_, _ = io.WriteString(w, message)
								return
							}
							_ = json.NewEncoder(w).Encode(models.ErrorResponse{Error: models.ErrorDetail{Message: message, Code: credential, Type: credential}})
						}))
						defer up.Close()
						cfg := config.DefaultConfig()
						cfg.Auth.Enabled = true
						const callerKey = "r4d-synthetic-internal-caller"
						cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "fixture", Key: callerKey, Owner: "fixture", KeyType: "internal", Metadata: map[string]string{"org_id": "r4d-org"}}}
						cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: r4dPlatformCanary, APIFormat: "openai", Models: []string{"gpt-4o"}}
						registry, err := providers.NewRegistry(cfg)
						if err != nil {
							t.Fatal(err)
						}
						defer registry.Close()
						store := tenant.NewStore()
						if byok {
							store.Set("r4d-org", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"openai": {Enabled: true, APIKey: credential, Models: []string{"gpt-4o"}}}})
						}
						ks := auth.NewKeyStore(cfg.Auth)
						s := New(cfg, "", registry, pipeline.NewEngine(), ks, nil, nil, nil, testModelDBPtr(), store, nil)
						s.ready.Store(true)
						r := httptest.NewRequest(http.MethodPost, "/v1/responses", strings.NewReader(fmt.Sprintf(`{"model":"gpt-4o","input":"fixture","stream":%t}`, stream)))
						r.Header.Set("Authorization", "Bearer "+callerKey)
						w := httptest.NewRecorder()
						s.httpServer.Handler.ServeHTTP(w, r)
						if calls.Load() != 1 {
							t.Fatalf("provider calls=%d, want 1", calls.Load())
						}
						if w.Code == 200 || w.Header().Get("Content-Type") != "application/json" || strings.Contains(w.Body.String(), "data:") {
							t.Error("pre-stream error was not an HTTP JSON refusal")
						}
						if strings.Contains(w.Body.String(), credential) {
							t.Error("client body retained planted provider credential")
						}
						var body models.ErrorResponse
						if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
							t.Fatal(err)
						}
						if !byok {
							if strings.Contains(body.Error.Message, "provider diagnostic") {
								t.Error("platform provider message was not projected")
							}
						} else {
							meta, ok := body.Error.Metadata.(map[string]any)
							// R1 removes the exact sent value before publication, for BYOK too (C-b).
							want := "provider diagnostic [REDACTED]"
							if raw {
								want = fmt.Sprintf("provider error (HTTP %d): %s", status, want)
							}
							if !ok || meta["provider_message"] != want {
								t.Error("BYOK provider text changed beyond credential redaction")
							}
						}
					})
				}
			}
		}
	}
}

type r4dResponsesFailure struct{ failure error }

func (p r4dResponsesFailure) CreateResponse(context.Context, []byte) ([]byte, int, error) {
	return nil, 0, p.failure
}
func (p r4dResponsesFailure) StreamResponse(context.Context, []byte) (io.ReadCloser, int, error) {
	return nil, 0, p.failure
}

func TestOptBR4DResponsesPreStreamPublication(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, byok := range []bool{false, true} {
			t.Run(fmt.Sprintf("stream=%t/byok=%t", stream, byok), func(t *testing.T) {
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.Provider, rc.RequestID, rc.CredentialSource = "fixture", r4dRequestID, "platform"
				if byok {
					rc.CredentialSource = "org"
				}
				text := "transport diagnostic " + r4dPlatformCanary
				original := &models.APIError{Status: 502, Type: models.ErrTypeUpstream, Code: r4dPlatformCanary, Message: text}
				p := r4dResponsesFailure{original}
				h := &Handlers{engine: pipeline.NewEngine()}
				w := httptest.NewRecorder()
				req := requestview.Responses([]byte(`{"model":"fixture","input":"fixture"}`))
				if stream {
					h.handleResponseStream(context.Background(), w, rc, p, req, "", false)
				} else {
					h.handleResponseNonStream(context.Background(), w, rc, p, req, "", false)
				}
				if w.Code != 502 || w.Header().Get("Content-Type") != "application/json" {
					t.Error("pre-stream failure started a successful stream")
				}
				var body models.ErrorResponse
				if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
					t.Fatal(err)
				}
				if !byok && strings.Contains(w.Body.String(), r4dPlatformCanary) {
					t.Error("platform pre-stream provider fields escaped projection")
				}
				if byok && !strings.Contains(body.Error.Message, text) {
					t.Error("BYOK pre-stream diagnostic was not retained")
				}
				if original.Message != text || original.Code != r4dPlatformCanary {
					t.Error("publication mutated internal error")
				}
			})
		}
	}
}
