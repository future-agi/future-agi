package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// /v1/realtime never hands a key limited to its org's providers a global
// realtime provider: such a key is refused as chat refuses a model it may
// not use. No shipped provider implements RealtimeProvider (the route
// answers 501 to every key), so a stub stands in for one that does.
//
// A key limited to its org's providers (any key but an internal one, unless a
// license authorized the request: Handlers.orgProvidersOnly) never reaches a
// global realtime provider; an internal key does.

const (
	rtModel       = "rt-model"
	rtOrg         = "org-rt"
	rtOrgKey      = "sk-agentcc-rt-org-0001"
	rtInternalKey = "sk-agentcc-rt-ops-0001"
)

// realtimeStub is a global provider that implements RealtimeProvider and
// records every model it was asked a realtime URL for: the call that hands
// the operator's upstream to the session.
type realtimeStub struct {
	mu    sync.Mutex
	asked []string
}

func (s *realtimeStub) ID() string { return "rt-stub" }
func (s *realtimeStub) ChatCompletion(context.Context, *models.ChatCompletionRequest) (*models.ChatCompletionResponse, error) {
	return nil, nil
}
func (s *realtimeStub) StreamChatCompletion(context.Context, *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	return nil, nil
}
func (s *realtimeStub) ListModels(context.Context) ([]models.ModelObject, error) { return nil, nil }
func (s *realtimeStub) Close() error                                             { return nil }
func (s *realtimeStub) RealtimeURL(model string) (string, error) {
	s.mu.Lock()
	s.asked = append(s.asked, model)
	s.mu.Unlock()
	return "ws://127.0.0.1:9/v1/realtime", nil
}
func (s *realtimeStub) RealtimeHeaders(string) (http.Header, error) {
	return http.Header{"Authorization": {"Bearer operator-realtime-key"}}, nil
}
func (s *realtimeStub) RealtimeSubprotocols() []string { return nil }

func (s *realtimeStub) calls() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.asked)
}

// newRealtimeGateway is the real server (KeyAuth and the realtime route as
// cmd/agentcc wires them) with the stub registered globally and two keys: an
// org byok key and an internal one.
func newRealtimeGateway(t *testing.T) (*Server, *realtimeStub) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "rt-org", Key: rtOrgKey, Owner: "o", KeyType: "byok", Metadata: map[string]string{"org_id": rtOrg}},
		{Name: "rt-ops", Key: rtInternalKey, Owner: "ops", KeyType: "internal"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	stub := &realtimeStub{}
	registry.RegisterProvider(stub.ID(), stub, []string{rtModel})
	tenants := tenant.NewStore()
	tenants.Set(rtOrg, &tenant.OrgConfig{})
	ks := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(), ks, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	return srv, stub
}

func realtimeRequest(key string) *http.Request {
	req := httptest.NewRequest(http.MethodGet, "/v1/realtime?model="+rtModel, nil)
	req.Header.Set("Authorization", "Bearer "+key)
	return req
}

func TestRealtime_OrgKeyNeverReachesAGlobalProvider(t *testing.T) {
	srv, stub := newRealtimeGateway(t)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, realtimeRequest(rtOrgKey))

	if n := stub.calls(); n != 0 {
		t.Fatalf("an org key reached the operator's realtime provider (%d RealtimeURL calls), want 0", n)
	}
	if w.Code != http.StatusForbidden {
		t.Fatalf("status %d, want 403; body %s", w.Code, w.Body.String())
	}
	if got := w.Body.String(); !strings.Contains(got, `model \"`+rtModel+`\" is not available for this API key`) {
		t.Errorf("body %s, want the refusal chat gives a key that may not use the model", got)
	}
}

// The internal key keeps the global provider, as on chat: the session goes
// as far as the WebSocket upgrade, which a recorder cannot complete.
func TestRealtime_InternalKeyReachesTheGlobalProvider(t *testing.T) {
	srv, stub := newRealtimeGateway(t)
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, realtimeRequest(rtInternalKey))

	if n := stub.calls(); n != 1 {
		t.Fatalf("the internal key's session asked the realtime provider %d times, want 1 (status %d, body %s)",
			n, w.Code, w.Body.String())
	}
}

// A refused session leaves no slot taken: the org can open as many refused
// sessions as it likes and the limit still lets the internal key in.
func TestRealtime_RefusedOrgSessionHoldsNoSlot(t *testing.T) {
	srv, stub := newRealtimeGateway(t)
	for i := 0; i < 10; i++ {
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, realtimeRequest(rtOrgKey))
		if w.Code != http.StatusForbidden {
			t.Fatalf("attempt %d: status %d, want 403", i, w.Code)
		}
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, realtimeRequest(rtInternalKey))
	if w.Code == http.StatusTooManyRequests || stub.calls() != 1 {
		t.Fatalf("after 10 refused org sessions the internal key got %d with %d provider calls", w.Code, stub.calls())
	}
}
