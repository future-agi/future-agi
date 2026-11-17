package server

// A refusal the gateway makes before it sends the request to any model is
// answered with x-agentcc-dispatched: false, so a client can tell the
// gateway's own refusal from a provider's answer passed through, whatever the
// error code says. An answer from a provider, an error or not, carries no
// such header.

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const dispatchedHeader = "x-agentcc-dispatched"

func expectNotDispatched(t *testing.T, w *httptest.ResponseRecorder, status int) {
	t.Helper()
	if w.Code != status {
		t.Fatalf("status %d, want %d: %s", w.Code, status, w.Body)
	}
	if got := w.Header().Get(dispatchedHeader); got != "false" {
		t.Fatalf("%s = %q on a refusal made before any call, want \"false\"", dispatchedHeader, got)
	}
}

func expectNoDispatchedHeader(t *testing.T, w *httptest.ResponseRecorder) {
	t.Helper()
	if got, set := w.Header()[http.CanonicalHeaderKey(dispatchedHeader)]; set {
		t.Fatalf("%s = %v on an answer from a provider (status %d)", dispatchedHeader, got, w.Code)
	}
}

func TestARefusalBeforeAnyModelCallSaysSo(t *testing.T) {
	t.Run("an unknown model", func(t *testing.T) {
		g := newManagedTestGateway(t, managedOptIn())
		expectNotDispatched(t, g.chat(t, chatBody("qwen3.7-flsh"), nil), http.StatusNotFound)
		if n := g.managed.calls() + g.operator.calls(); n != 0 {
			t.Fatalf("upstream called %d times", n)
		}
	})
	t.Run("the route stage: Future AGI credits off", func(t *testing.T) {
		g := newManagedTestGateway(t, &tenant.OrgConfig{})
		expectNotDispatched(t, g.chat(t, chatBody("claude-sonnet-5.5"), nil), http.StatusForbidden)
	})
	t.Run("a credit refusal", func(t *testing.T) {
		g := newManagedTestGateway(t, managedOptIn())
		g.grant(t, "org-m", 0)
		expectNotDispatched(t, g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil), http.StatusPaymentRequired)
		if g.managed.calls() != 0 {
			t.Fatal("the managed provider was called")
		}
	})
	t.Run("an admission cap", func(t *testing.T) {
		s := newJobRoutesServer(t, true)
		s.TenantStore.Set(videoOrgA, capRouteConfig(t, s, true))
		w := jobRoutesCall(s, videoKeyA, "POST", "/v1/videos", videoBody)
		expectNotDispatched(t, w, http.StatusPaymentRequired)
		if code := errorCode(t, w); code != "call_cap_unenforceable" {
			t.Fatalf("code %q", code)
		}
	})
	t.Run("an org budget", func(t *testing.T) {
		g := r7bStart(t)
		g.push(t, r7bFixture(t, "org_block"))
		first := r7bRecorder(g)
		if first.Code != http.StatusOK {
			t.Fatalf("first call: %d %s", first.Code, first.Body)
		}
		expectNoDispatchedHeader(t, first)
		second := r7bRecorder(g)
		expectNotDispatched(t, second, http.StatusTooManyRequests)
		if !strings.Contains(second.Body.String(), "org_budget_exceeded") {
			t.Fatalf("second call: %s", second.Body)
		}
	})
	t.Run("an unknown gateway key", func(t *testing.T) {
		g := newManagedTestGateway(t, managedOptIn())
		w := g.chat(t, chatBody("gpt-4o"), map[string]string{"Authorization": "Bearer sk-agentcc-0000"})
		expectNotDispatched(t, w, http.StatusUnauthorized)
	})
	t.Run("an unknown gateway key, Anthropic Messages", func(t *testing.T) {
		g := newManagedTestGateway(t, managedOptIn())
		w := postMessages(t, g.srv, "/v1/messages", "sk-agentcc-0000", managedMessagesBody("anthropic/claude-sonnet-5.5", false, ""))
		expectNotDispatched(t, w, http.StatusUnauthorized)
	})
}

func TestAProviderAnswerCarriesNoDispatchedHeader(t *testing.T) {
	for _, upstream := range []int{http.StatusBadRequest, http.StatusNotFound, http.StatusTooManyRequests, http.StatusInternalServerError} {
		g := newManagedTestGateway(t, dxBYOKOrg())
		g.operator.status.Store(int32(upstream))
		w := g.chat(t, chatBody("gpt-4o"), nil)
		if w.Code < 400 || g.operator.calls() == 0 {
			t.Fatalf("upstream %d: answered %d after %d calls", upstream, w.Code, g.operator.calls())
		}
		expectNoDispatchedHeader(t, w)
	}
	g := newManagedTestGateway(t, dxBYOKOrg())
	w := g.chat(t, chatBody("gpt-4o"), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("served: %d %s", w.Code, w.Body)
	}
	expectNoDispatchedHeader(t, w)
}

// r7bRecorder is r7bGateway.chat's request, answered as the client reads it.
func r7bRecorder(g *r7bGateway) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{"model":"org-model","messages":[{"role":"user","content":"hello"}]}`))
	req.Header.Set("Authorization", "Bearer "+r7bCust)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}
