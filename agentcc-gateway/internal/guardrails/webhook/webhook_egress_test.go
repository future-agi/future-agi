package webhook

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// An org webhook check is built with the guarded egress dialer, so a
// metadata URL is refused, a loopback one unless the operator lists it, and a
// listed loopback one works.

func egressInput() *guardrails.CheckInput {
	return &guardrails.CheckInput{
		Request: &models.ChatCompletionRequest{Model: "gpt-4o",
			Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}}},
		Metadata: map[string]string{"org_id": "org-a"},
	}
}

func mustGuard(t *testing.T, allow ...string) *egress.Dialer {
	t.Helper()
	d, err := egress.New(egress.Options{PrivateAllow: allow})
	if err != nil {
		t.Fatalf("egress.New(%v): %v", allow, err)
	}
	return d
}

// TestWebhook_OrgLoopbackRefused: with no operator exception, an org webhook
// at a loopback URL is refused — the check fails, and the server is never hit.
func TestWebhook_OrgLoopbackRefused(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		_, _ = w.Write([]byte(`{"pass":true,"score":0}`))
	}))
	defer srv.Close()

	g := New("exfil", map[string]interface{}{"url": srv.URL + "/internal"}, mustGuard(t))
	res := g.Check(context.Background(), egressInput())
	if res.Pass {
		t.Fatal("a loopback webhook was allowed; the guard did not refuse it")
	}
	if n := hits.Load(); n != 0 {
		t.Fatalf("the loopback server was reached %d times", n)
	}
}

// TestWebhook_OrgMetadataRefused: a cloud-metadata URL is refused and never
// dialled.
func TestWebhook_OrgMetadataRefused(t *testing.T) {
	g := New("exfil", map[string]interface{}{"url": "http://169.254.169.254/latest/meta-data/"}, mustGuard(t))
	if res := g.Check(context.Background(), egressInput()); res.Pass {
		t.Fatal("a metadata webhook was allowed")
	}
}

// TestWebhook_OrgListedTargetWorks: the operator lists the server (a public
// test target stands in; here a loopback server listed via private_allow) and
// the webhook reaches it and reads its verdict.
func TestWebhook_OrgListedTargetWorks(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		_, _ = w.Write([]byte(`{"pass":false,"score":0.9,"message":"flagged"}`))
	}))
	defer srv.Close()

	host := strings.TrimPrefix(srv.URL, "http://")
	g := New("ext", map[string]interface{}{"url": srv.URL + "/guard"}, mustGuard(t, host))
	res := g.Check(context.Background(), egressInput())
	if res.Pass || res.Message != "flagged" {
		t.Fatalf("listed webhook: pass=%v msg=%q, want the server's verdict", res.Pass, res.Message)
	}
	if n := hits.Load(); n != 1 {
		t.Fatalf("the listed server was reached %d times, want 1", n)
	}
}

// TestWebhook_NilDialerRefusesLoopback: a nil dialer is the guard with no
// exceptions, so a webhook built with none still refuses loopback.
func TestWebhook_NilDialerRefusesLoopback(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"pass":true,"score":0}`))
	}))
	defer srv.Close()
	g := New("x", map[string]interface{}{"url": srv.URL}, nil)
	if res := g.Check(context.Background(), egressInput()); res.Pass {
		t.Fatal("a nil dialer reached a loopback webhook")
	}
}

// TestWebhook_ConfigFileRuleKeepsPlainClient: an operator config.yaml rule is
// not guarded — it may point at the operator's own host without listing it.
func TestWebhook_ConfigFileRuleKeepsPlainClient(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		hits.Add(1)
		_, _ = w.Write([]byte(`{"pass":true,"score":0}`))
	}))
	defer srv.Close()
	g := NewConfigFileRule("op", map[string]interface{}{"url": srv.URL})
	if res := g.Check(context.Background(), egressInput()); !res.Pass {
		t.Fatalf("operator config-file webhook to its own host was refused: %q", res.Message)
	}
	if n := hits.Load(); n != 1 {
		t.Fatalf("the operator webhook was reached %d times, want 1", n)
	}
}
