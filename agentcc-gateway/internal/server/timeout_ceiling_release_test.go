package server

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The request-timeout ceiling (server.max_request_timeout) on the gateway's
// own timeout code: resolveTimeoutFor (a rollout candidate's timeout on
// chat) and nodeTimeout (the node's own time for a model, which
// resolveTimeout ends on and feedsHealth measures attempts against). The
// header tests cover resolveTimeout; these cover the other paths.
// Every timeout the node resolves is held at the ceiling: the request
// context carries that same maximum, so a larger value would be a number
// nothing enforces.
func TestResolvedTimeoutsAreHeldAtTheCeiling(t *testing.T) {
	h := &Handlers{defaultTimeout: 60 * time.Second, maxTimeout: 200 * time.Second}
	mt := map[string]time.Duration{"m-long": 600 * time.Second, "m-short": 90 * time.Second}
	h.modelTimeouts.Store(&mt)
	aboveCeiling := &tenant.OrgConfig{Routing: &tenant.RoutingConfig{ModelTimeouts: map[string]string{"m-short": "15m"}}}

	request := func(header map[string]string) *http.Request {
		r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
		for k, v := range header {
			r.Header.Set(k, v)
		}
		return r
	}
	for _, tc := range []struct {
		name   string
		model  string
		header map[string]string
		orgCfg *tenant.OrgConfig
		want   time.Duration
	}{
		{"a model timeout above the ceiling, no header", "m-long", nil, &tenant.OrgConfig{}, 200 * time.Second},
		{"a model timeout under the ceiling stands", "m-short", nil, &tenant.OrgConfig{}, 90 * time.Second},
		{"the server default stands", "m-other", nil, &tenant.OrgConfig{}, 60 * time.Second},
		{"a header above the ceiling", "m-short", map[string]string{"x-agentcc-timeout": "10m"}, &tenant.OrgConfig{}, 200 * time.Second},
		{"a header under the ceiling stands", "m-long", map[string]string{"x-agentcc-request-timeout": "5000"}, &tenant.OrgConfig{}, 5 * time.Second},
		{"an org model timeout above the ceiling", "m-short", nil, aboveCeiling, 200 * time.Second},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model = tc.model
			r := request(tc.header)
			if got := h.resolveTimeoutFor(rc, r, tc.orgCfg); got != tc.want {
				t.Errorf("resolveTimeoutFor = %v, want %v", got, tc.want)
			}
			if tc.orgCfg == aboveCeiling {
				return // resolveTimeout reads the org from the request's key; there is none here
			}
			if got := h.resolveTimeout(rc, r); got != tc.want {
				t.Errorf("resolveTimeout = %v, want %v", got, tc.want)
			}
			if tc.header == nil {
				if got := h.nodeTimeout(tc.model); got != tc.want {
					t.Errorf("nodeTimeout = %v, want %v", got, tc.want)
				}
			}
		})
	}
}

// With no header, a model timeout above the ceiling is held at the ceiling
// on the wire too: the provider gets the ceiling, and x-agentcc-timeout-ms
// says so. (TestEmbedding_MaxRequestTimeoutHoldsTheHeaderAndTheModelTimeout,
// in timeout_ceiling_test.go, sends a 10m header, so the header step, not the
// model timeout, is what it holds at the ceiling.)
func TestModelTimeoutAboveTheCeilingIsHeldWithoutAHeader(t *testing.T) {
	t.Run("embeddings", func(t *testing.T) {
		p := &ceilingProvider{clientTimeout: 300 * time.Second}
		srv := newCeilingServer(t, p, 600*time.Second)
		srv.handlers.maxTimeout = 200 * time.Second

		w := postEmbedding(t, srv)

		if got := p.remaining(t); got != 200*time.Second {
			t.Errorf("the provider had %v, want 200s (the ceiling, not the 600s model timeout)", got)
		}
		if got := w.Header().Get("x-agentcc-timeout-ms"); got != "200000" {
			t.Errorf("x-agentcc-timeout-ms = %q, want 200000 (the ceiling)", got)
		}
	})
	t.Run("chat", func(t *testing.T) {
		p := &ceilingProvider{clientTimeout: 300 * time.Second}
		srv := newCeilingServer(t, p, 600*time.Second)
		srv.handlers.maxTimeout = 200 * time.Second

		body := `{"model":"ceiling-model","messages":[{"role":"user","content":"hello"}]}`
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewBufferString(body))
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)

		if w.Code != http.StatusOK {
			t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
		}
		if got := p.remaining(t); got != 200*time.Second {
			t.Errorf("the provider had %v, want 200s (the ceiling, not the 600s model timeout)", got)
		}
		if got := w.Header().Get("x-agentcc-timeout-ms"); got != "200000" {
			t.Errorf("x-agentcc-timeout-ms = %q, want 200000 (the ceiling)", got)
		}
	})
}

// A gateway timeout at the ceiling is the node's own time for a model whose
// configured timeout is longer: the request cannot run past the ceiling, so
// the model had all the time the node gives it, and the timeout counts
// against the model (feedsHealth compares against nodeTimeout, which is held
// at the ceiling). Without that, a model configured above the ceiling would
// never be marked slow.
func TestGatewayTimeoutAtTheCeilingFeedsModelHealth(t *testing.T) {
	g := newAttemptsGateway(t, attemptsGatewayOpts{})
	health := routing.NewModelHealth()
	g.srv.handlers.SetInjectedDeps(InjectedDeps{ModelHealth: health})
	g.first.callDelay, g.second.callDelay = 5*time.Second, 5*time.Second
	mt := map[string]time.Duration{"gpt-4o": time.Minute}
	g.srv.handlers.modelTimeouts.Store(&mt)
	g.srv.handlers.maxTimeout = 500 * time.Millisecond

	for i := 0; i < 3; i++ {
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(attemptsChat))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+attemptsKey)
		w := httptest.NewRecorder()
		g.srv.httpServer.Handler.ServeHTTP(w, req)
		attempts, _, _, _ := g.capture.last()
		if len(attempts) != 1 || attempts[0].Outcome != models.OutcomeTimeout || attempts[0].ErrorOrigin != models.OriginGateway {
			t.Fatalf("attempts = %+v, want one gateway timeout", attempts)
		}
		if got := w.Header().Get("x-agentcc-timeout-ms"); got != "" && got != "500" {
			t.Fatalf("x-agentcc-timeout-ms = %q, want 500 (the ceiling) when set", got)
		}
	}
	first, second := health.Snapshot("first", "gpt-4o"), health.Snapshot("second", "gpt-4o")
	if first.N+second.N != 3 || first.SuccessRate+second.SuccessRate != 0 {
		t.Fatalf("timeouts at the ceiling: first %+v, second %+v; want 3 failures", first, second)
	}
}
