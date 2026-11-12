package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const backgroundOtherCaller = "background-polling-other-caller"

func newBackgroundPollingGateway(t *testing.T, upstream http.Handler) (*Server, *tenant.Store) {
	t.Helper()
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Registry connectivity checks are not response polls and must not
		// consume a scripted refresh or assert its originating credential.
		if r.Method == http.MethodGet && r.URL.Path == "/v1/models" {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"data":[]}`)
			return
		}
		upstream.ServeHTTP(w, r)
	}))
	t.Cleanup(up.Close)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "byok", Key: respByokKey, Owner: "owner", KeyType: "byok", Metadata: map[string]string{"org_id": respOrg}},
		{Name: "other", Key: backgroundOtherCaller, Owner: "other", KeyType: "byok", Metadata: map[string]string{"org_id": "other-org"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	tenants := tenant.NewStore()
	for _, org := range []string{respOrg, "other-org"} {
		tenants.Set(org, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
			"openai": {Enabled: true, APIKey: "fixture-" + org, Models: []string{"gpt-4o"}},
		}})
	}
	ks := auth.NewKeyStore(cfg.Auth)
	engine := pipeline.NewEngine(authplugin.New(ks, true))
	srv := New(cfg, "", registry, engine, ks, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv, tenants
}

func backgroundPollingRequest(t *testing.T, srv *Server, method, caller, body string) *httptest.ResponseRecorder {
	t.Helper()
	path := "/v1/responses"
	if method != http.MethodPost {
		path += "/resp_background"
	}
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+caller)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

const backgroundPollingBody = `{"model":"gpt-4o","input":"hi","background":true,"store":true}`

// GET must advance a pending BYOK response, using the
// provider that created it, rather than replaying the POST snapshot forever.
func TestResponsesBackground_PollRefreshesOrigin(t *testing.T) {
	for _, initial := range []string{"queued", "in_progress"} {
		for _, terminal := range []string{"completed", "failed", "incomplete", "cancelled"} {
			t.Run(initial+"/"+terminal, func(t *testing.T) {
				var polls atomic.Int32
				srv, tenants := newBackgroundPollingGateway(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if got := r.Header.Get("Authorization"); got != "Bearer fixture-"+respOrg {
						t.Errorf("wrong originating credential: %q", got)
					}
					status := initial
					switch {
					case r.Method == http.MethodPost && r.URL.Path == "/v1/responses":
					case r.Method == http.MethodGet && r.URL.Path == "/v1/responses/resp_background":
						status = "in_progress"
						if polls.Add(1) > 1 {
							status = terminal
						}
					default:
						t.Errorf("unexpected upstream call: %s %s", r.Method, r.URL.Path)
						w.WriteHeader(http.StatusNotFound)
						return
					}
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"id":"resp_background","object":"response","status":%q,"background":true,"store":true,"model":"gpt-4o","output":[{"type":"message","content":[{"type":"output_text","text":%q}]}]}`, status, "answer-"+status)
				}))
				created := backgroundPollingRequest(t, srv, http.MethodPost, respByokKey, backgroundPollingBody)
				if created.Code != http.StatusOK || !strings.Contains(created.Body.String(), `"status":"`+initial+`"`) {
					t.Fatalf("create: %d %s", created.Code, created.Body.String())
				}

				// Later routing changes must not send the old resource to a
				// different upstream or use a different org's credentials.
				replacement := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					t.Error("polled a replacement provider instead of the origin")
					w.WriteHeader(http.StatusBadGateway)
				}))
				t.Cleanup(replacement.Close)
				tenants.Set(respOrg, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
					"openai": {Enabled: true, APIKey: "replacement-fixture", BaseURL: replacement.URL, Models: []string{"gpt-4o"}},
				}})
				other := backgroundPollingRequest(t, srv, http.MethodGet, backgroundOtherCaller, "")
				if other.Code != http.StatusNotFound || polls.Load() != 0 {
					t.Fatalf("another org polled the resource: status=%d, polls=%d", other.Code, polls.Load())
				}
				for _, want := range []string{"in_progress", terminal, terminal} {
					got := backgroundPollingRequest(t, srv, http.MethodGet, respByokKey, "")
					if got.Code != http.StatusOK || !strings.Contains(got.Body.String(), `"status":"`+want+`"`) || !strings.Contains(got.Body.String(), "answer-"+want) {
						t.Fatalf("poll: %d %s; want status and body for %s", got.Code, got.Body.String(), want)
					}
				}
				if polls.Load() != 2 {
					t.Errorf("upstream polls=%d, want 2; terminal snapshots should be cached", polls.Load())
				}
			})
		}
	}
}

func TestResponsesBackground_PollErrorsDoNotReplaceSnapshot(t *testing.T) {
	for _, bad := range []string{"upstream_error", "invalid_json", "wrong_id", "missing_status"} {
		t.Run(bad, func(t *testing.T) {
			var polls atomic.Int32
			srv, _ := newBackgroundPollingGateway(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if r.Method == http.MethodPost {
					fmt.Fprint(w, `{"id":"resp_background","status":"queued","store":true}`)
					return
				}
				if polls.Add(1) == 1 {
					switch bad {
					case "upstream_error":
						w.WriteHeader(http.StatusServiceUnavailable)
						fmt.Fprint(w, `{"error":{"message":"retry later","type":"server_error"}}`)
					case "invalid_json":
						fmt.Fprint(w, `{`)
					case "wrong_id":
						fmt.Fprint(w, `{"id":"another_response","status":"completed"}`)
					case "missing_status":
						fmt.Fprint(w, `{"id":"resp_background"}`)
					}
					return
				}
				fmt.Fprint(w, `{"id":"resp_background","status":"completed","output":[]}`)
			}))
			if got := backgroundPollingRequest(t, srv, http.MethodPost, respByokKey, backgroundPollingBody); got.Code != http.StatusOK {
				t.Fatalf("create: %d %s", got.Code, got.Body.String())
			}
			if got := backgroundPollingRequest(t, srv, http.MethodGet, respByokKey, ""); got.Code < 500 {
				t.Fatalf("bad refresh returned %d %s instead of an upstream error", got.Code, got.Body.String())
			}
			if got := backgroundPollingRequest(t, srv, http.MethodGet, respByokKey, ""); got.Code != http.StatusOK || !strings.Contains(got.Body.String(), `"status":"completed"`) {
				t.Fatalf("retry: %d %s", got.Code, got.Body.String())
			}
			if polls.Load() != 2 {
				t.Errorf("polls=%d, want 2", polls.Load())
			}
		})
	}
}

func TestResponsesBackground_DeleteDuringPollDoesNotRestoreResponse(t *testing.T) {
	started, release := make(chan struct{}), make(chan struct{})
	srv, _ := newBackgroundPollingGateway(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodPost {
			fmt.Fprint(w, `{"id":"resp_background","status":"queued","store":true}`)
			return
		}
		close(started)
		<-release
		fmt.Fprint(w, `{"id":"resp_background","status":"completed"}`)
	}))
	defer close(release)
	if got := backgroundPollingRequest(t, srv, http.MethodPost, respByokKey, backgroundPollingBody); got.Code != http.StatusOK {
		t.Fatalf("create: %d %s", got.Code, got.Body.String())
	}
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() { done <- backgroundPollingRequest(t, srv, http.MethodGet, respByokKey, "") }()
	select {
	case <-started:
	case got := <-done:
		t.Fatalf("GET returned without polling: %d %s", got.Code, got.Body.String())
	case <-time.After(5 * time.Second):
		t.Fatal("poll did not reach upstream")
	}
	if got := backgroundPollingRequest(t, srv, http.MethodDelete, respByokKey, ""); got.Code != http.StatusOK {
		t.Fatalf("delete: %d %s", got.Code, got.Body.String())
	}
	release <- struct{}{}
	select {
	case got := <-done:
		if got.Code != http.StatusNotFound {
			t.Errorf("deleted response returned by pending poll: %d %s", got.Code, got.Body.String())
		}
	case <-time.After(5 * time.Second):
		t.Fatal("poll did not finish")
	}
	if got := backgroundPollingRequest(t, srv, http.MethodGet, respByokKey, ""); got.Code != http.StatusNotFound {
		t.Errorf("deleted response restored: %d %s", got.Code, got.Body.String())
	}
}
