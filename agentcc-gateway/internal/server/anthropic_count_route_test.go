package server

import (
	"context"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// This is a separate structural regression, not a change to the CAT2 guard's
// rules. Two copied routing implementations are not the accepted design.
func TestAnthropicCountRouteUsesMessagesHelper(t *testing.T) {
	f, err := parser.ParseFile(token.NewFileSet(), "handlers_anthropic.go", nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"AnthropicMessages", "AnthropicCountTokens"} {
		calls := 0
		for _, d := range f.Decls {
			fn, ok := d.(*ast.FuncDecl)
			if !ok || fn.Name.Name != name {
				continue
			}
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				if c, ok := n.(*ast.CallExpr); ok {
					if s, ok := c.Fun.(*ast.SelectorExpr); ok && s.Sel.Name == "resolveAnthropicRoute" {
						calls++
					}
				}
				return true
			})
		}
		if calls != 1 {
			t.Errorf("count/messages must share route resolution: %s calls=%d, want 1", name, calls)
		}
	}
}

type countRouteCall struct{ provider, model, path string }

func countRouteRequest(srv *Server, endpoint, key, body, lock string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(http.MethodPost, endpoint, strings.NewReader(body))
	r.Header.Set("x-api-key", key)
	r.Header.Set("Content-Type", "application/json")
	if lock != "" {
		r.Header.Set("x-agentcc-provider-lock", lock)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}

// Both routes use real native adapters against loopback-only synthetic servers.
// No catalogue discovery or provider connectivity probe is enabled.
func TestAnthropicCountRouteIdenticalToMessages(t *testing.T) {
	for _, tc := range []struct {
		name, model, lock, wantProvider, wantModel string
		fallback                                   bool
	}{
		{name: "explicit", model: "alpha/route-model", wantProvider: "alpha", wantModel: "route-model"},
		{name: "provider_lock", model: "alpha/route-model", lock: "beta", wantProvider: "beta", wantModel: "alpha/route-model"},
		{name: "resolution_fallback", model: "unregistered-model", fallback: true, wantProvider: "beta", wantModel: "route-model"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := make(chan countRouteCall, 8)
			cfg := config.DefaultConfig()
			off := false
			for _, id := range []string{"alpha", "beta"} {
				up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					var body struct {
						Model string `json:"model"`
					}
					if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
						t.Errorf("decode synthetic request: %v", err)
					}
					calls <- countRouteCall{id, body.Model, r.URL.Path}
					w.Header().Set("Content-Type", "application/json")
					if r.URL.Path == "/v1/messages/count_tokens" {
						io.WriteString(w, `{"input_tokens":17}`)
						return
					}
					if r.URL.Path != "/v1/messages" {
						t.Errorf("unexpected upstream path %s", r.URL.Path)
					}
					fmt.Fprintf(w, `{"id":"msg_route","type":"message","role":"assistant","model":%q,"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":17,"output_tokens":1}}`, body.Model)
				}))
				t.Cleanup(up.Close)
				cfg.Providers[id] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic-key", APIFormat: "anthropic", Models: []string{"route-model"}, AutoDiscover: &off, Local: &off}
			}
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "route-test", Key: "sk-synthetic-route", Owner: "synthetic-user", KeyType: "internal"}}
			if tc.fallback {
				cfg.Routing.ModelFallbacks = map[string][]string{tc.model: {"beta/route-model"}}
			}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			keys := auth.NewKeyStore(cfg.Auth)
			srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), nil, nil)
			srv.ready.Store(true)
			countRouteCleanup(t, srv)
			body := fmt.Sprintf(`{"model":%q,"max_tokens":32,"messages":[{"role":"user","content":"hello"}]}`, tc.model)
			var identities []string
			srv.handlers.hookObserver = func(hook, detail string, rc *models.RequestContext) {
				if hook == hookCheckEligibility {
					identities = append(identities, detail)
				}
				if rc != nil && rc.EndpointType == "anthropic_count_tokens" && (hook == hookBeginAttempt || hook == hookReserveAdd || hook == hookRecordGeneration) {
					t.Errorf("counting reached billable hook %s", hook)
				}
			}
			var chosen [2]string
			for i, path := range []string{"/v1/messages", "/v1/messages/count_tokens"} {
				identities = nil
				w := countRouteRequest(srv, path, "sk-synthetic-route", body, tc.lock)
				if w.Code != http.StatusOK {
					t.Fatalf("identical route refused on %s: %d %s", path, w.Code, w.Body)
				}
				if len(identities) == 0 {
					t.Fatalf("%s bypassed route eligibility", path)
				}
				chosen[i] = identities[len(identities)-1]
				select {
				case got := <-calls:
					if got.provider != tc.wantProvider || got.model != tc.wantModel || got.path != path {
						t.Errorf("count/messages route mismatch: %+v, want %s %s %s", got, tc.wantProvider, tc.wantModel, path)
					}
				default:
					t.Fatal("successful response without the synthetic provider call")
				}
			}
			if chosen[0] != chosen[1] || chosen[0] != tc.wantProvider+" "+tc.wantModel {
				t.Fatalf("count/messages resolved different model/provider: %v", chosen)
			}
			if len(calls) != 0 {
				t.Fatalf("unexpected extra upstream calls: %d", len(calls))
			}
		})
	}
}

// Reuse the unchanged five-policy fixture but submit an identical valid
// Messages body to both endpoints. No count is returned for a refused route.
func TestAnthropicCountRouteIdenticalRefusals(t *testing.T) {
	for _, policy := range []string{
		`{"provider_lock":{"enabled":true,"deny_providers":["native"]}}`,
		`{"provider_lock":{"enabled":true,"allowed_providers":["other"]}}`,
		`{"data_policy":{"zdr":true}}`,
		`{"data_policy":{"data_collection":"deny"}}`,
		`{"data_policy":{"allowed_regions":["eu"]}}`,
	} {
		t.Run(policy, func(t *testing.T) {
			// Decode through the real tenant wire contract, not a new policy stub.
			var routing tenant.RoutingConfig
			if err := json.Unmarshal([]byte(policy), &routing); err != nil {
				t.Fatal(err)
			}
			srv, calls, _ := newCountPolicyServer(t, &routing, false)
			countRouteCleanup(t, srv)
			body := strings.TrimSuffix(countPolicyBody, "}") + `,"max_tokens":32}`
			var identities [2]string
			var answers [2]string
			for i, path := range []string{"/v1/messages", "/v1/messages/count_tokens"} {
				srv.handlers.hookObserver = func(hook, detail string, _ *models.RequestContext) {
					if hook == hookCheckEligibility {
						identities[i] = detail
					}
				}
				w := countRouteRequest(srv, path, "sk-count-policy", body, "")
				if w.Code != http.StatusForbidden {
					t.Fatalf("%s policy refusal = %d %s", path, w.Code, w.Body)
				}
				answers[i] = w.Body.String()
			}
			if identities[0] == "" || identities[0] != identities[1] || answers[0] != answers[1] || calls.Load() != 0 {
				t.Fatalf("count/messages refusal diverged: routes=%v answers=%v calls=%d", identities, answers, calls.Load())
			}
		})
	}
}

// A count must select the Messages route even when that route cannot expose a
// free native count. Refuse instead of choosing a different BYOK model, and
// never enter the real cost/credit pipeline for this unsupported operation.
func TestAnthropicCountManagedRouteNoBillableEvent(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	countRouteCleanup(t, g.srv)
	g.ledger.SetMicros("org-m", 10_000_000)
	body := `{"model":"anthropic/claude-sonnet-5.5","max_tokens":32,"messages":[{"role":"user","content":"hello"}]}`
	var route string
	g.srv.handlers.hookObserver = func(hook, detail string, _ *models.RequestContext) {
		if hook == hookCheckEligibility {
			route = detail
		}
	}
	message := countRouteRequest(g.srv, "/v1/messages", managedOrgKey, body, "")
	if message.Code != http.StatusOK || route == "" {
		t.Fatalf("Messages control failed: %d %s route=%q", message.Code, message.Body, route)
	}
	wantRoute := route
	before, err := g.ledger.Account(context.Background(), "org-m")
	if err != nil {
		t.Fatal(err)
	}
	outbox := g.outbox("org-m")
	upstream := g.managed.calls()
	route = ""
	count := countRouteRequest(g.srv, "/v1/messages/count_tokens", managedOrgKey, body, "")
	after, err := g.ledger.Account(context.Background(), "org-m")
	if count.Code != http.StatusNotImplemented || route != wantRoute {
		t.Fatalf("count must refuse the same managed route without dispatch: status=%d route=%q want=%q body=%s", count.Code, route, wantRoute, count.Body)
	}
	if err != nil || !reflect.DeepEqual(before, after) || !reflect.DeepEqual(outbox, g.outbox("org-m")) || g.managed.calls() != upstream {
		t.Fatalf("count created a financial or provider event: before=%+v after=%+v err=%v", before, after, err)
	}
}

func countRouteCleanup(t *testing.T, srv *Server) {
	t.Helper()
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		srv.Shutdown(ctx)
		srv.batchProcessor.Stop()
	})
}
