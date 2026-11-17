package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// A Console run's model call, on a console-run grant, runs as the org on the
// org's own provider key and says so: x-agentcc-billing-mode byok.
func TestDelegatedConsoleRunPaysWithTheOrgsKey(t *testing.T) {
	srv := newDelegated733Server(t)
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
	env, grant, _ := signedConsoleRun733(delegated733Route, delegated733Body, []string{"gpt-4o-mini"}, time.Now().Unix())
	w := request733(srv, delegated733Route, delegated733Body, env, grant)
	if w.Code != 200 {
		t.Fatalf("console-run call: %d %s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("x-agentcc-billing-mode"); got != "byok" {
		t.Fatalf("x-agentcc-billing-mode = %q, want byok", got)
	}
}

// A model only the gateway's own providers serve is refused to a Console
// run, and the gateway's provider is never called: a delegated call never
// falls back to a platform key.
func TestDelegatedConsoleRunNeverFallsBackToAPlatformKey(t *testing.T) {
	// Only a model call is a call made on the operator's key; any other
	// request the gateway sends the provider (a listing, a probe) is recorded
	// so a failure names it.
	var platformCalls atomic.Int64
	var otherMu sync.Mutex
	var other []string
	platform := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/chat/completions") {
			platformCalls.Add(1)
		} else {
			otherMu.Lock()
			other = append(other, r.Method+" "+r.URL.Path)
			otherMu.Unlock()
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"platform","object":"chat.completion","model":"platform-only-model","choices":[{"index":0,"message":{"role":"assistant","content":"platform"},"finish_reason":"stop"}]}`)
	}))
	t.Cleanup(platform.Close)
	srv := newDelegated733Server(t, func(cfg *config.Config) {
		cfg.Providers["platform"] = config.ProviderConfig{BaseURL: platform.URL + "/v1", APIFormat: "openai", APIKey: "test-only-platform-key", Models: []string{"platform-only-model"}}
	})
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
	body := `{"model":"platform-only-model","messages":[{"role":"user","content":"hello"}]}`
	env, grant, _ := signedConsoleRun733(delegated733Route, body, []string{"platform-only-model"}, time.Now().Unix())
	w := request733(srv, delegated733Route, body, env, grant)
	if w.Code/100 == 2 {
		t.Fatalf("a console-run call for a platform-only model answered %d: %s", w.Code, w.Body.String())
	}
	if w.Code != 403 && w.Code != 404 {
		t.Fatalf("refusal status %d, want 403 or 404: %s", w.Code, w.Body.String())
	}
	if n := platformCalls.Load(); n != 0 {
		otherMu.Lock()
		defer otherMu.Unlock()
		t.Fatalf("the gateway's own provider was sent %d model calls for a Console run (other requests: %v)", n, other)
	}
	otherMu.Lock()
	t.Logf("other requests the gateway's own provider received: %v", other)
	otherMu.Unlock()
}

// A console-run grant opens the delegated chat route only: the delegated
// batch route refuses it, as it refuses every envelope that is not a batch's.
func TestDelegatedConsoleRunGrantOpensNoOtherRoute(t *testing.T) {
	srv := newDelegated733Server(t)
	srv.delegated = auth.NewEnvelopeVerifier(delegated733Secret, time.Now().Add(-time.Minute))
	srv.keyStore.SyncDelegations(&auth.DelegationSnapshot{Complete: true, Members: []auth.DelegatedMember{{Org: delegated733Org, User: delegated733User, Workspace: "-"}}}, time.Now())
	body := `{"requests":[` + delegated733Body + `],"max_concurrency":1}`
	env, grant, _ := signedConsoleRun733("/-/batches", body, []string{"gpt-4o-mini"}, time.Now().Unix())
	if w := request733(srv, "/-/batches", body, env, grant); w.Code != 401 {
		t.Fatalf("a console-run grant on the batch route answered %d, want 401: %s", w.Code, w.Body.String())
	}
	if rows := srv.batchProcessor.OrgInventory(delegated733Org); len(rows) != 0 {
		t.Fatalf("a console-run grant left %d batches", len(rows))
	}
}
