package server

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestR602SharedFallbackFinancialIdentity(t *testing.T) {
	g, l, _ := r601Gateway(t)
	g.srv.handlers.managedUpstreamKeyMode = "per_org_with_shared_fallback"
	cfg := managedOptIn()
	cfg.ManagedModels.UpstreamKeys = map[string]*tenant.ManagedUpstreamKey{"openrouter": {KeyHash: "synthetic-stale-management-hash"}}
	g.tenants.Set("org-m", cfg)
	answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, _ string) {
		r601Response("0.0000095", false)(w, r, "google/gemini-3.5-flash")
	})
	g.managed.responseWith.Store(&answer)
	w := g.chat(t, `{"model":"google/gemini-3.5-flash","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, nil)
	rows := l.Outbox("org-m")
	if w.Code != 200 || len(rows) != 1 {
		t.Fatalf("status=%d rows=%d", w.Code, len(rows))
	}
	rec := l.OwnedSettlement("org-m", rows[0]["request_id"])
	if rec == nil || rec.BilledMicros != 11 || rec.UpstreamKeyID != "" || rows[0]["upstream_key_id"] != "" {
		t.Fatal("shared fallback inherited an unused per-org key_hash")
	}
	if len(rec.InclusiveBindings) != 1 {
		t.Fatal("shared fallback binding absent")
	}
	for _, id := range rec.InclusiveBindings {
		if id.Scope != "shared" || id.Credential == "" {
			t.Fatal("shared fallback bound to an organization credential")
		}
	}
}
