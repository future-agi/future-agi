package server

import (
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestR602PerOrgAttemptIdentity(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/%t", surface, stream), func(t *testing.T) {
				g, l, _ := r601Gateway(t)
				g.srv.handlers.managedUpstreamKeyMode = "per_org"
				for _, token := range []string{"synthetic-org-key-before", "synthetic-org-key-after"} {
					cfg := managedOptIn()
					cfg.ManagedModels.UpstreamKeys = map[string]*tenant.ManagedUpstreamKey{"openrouter": {APIKey: token, KeyHash: "management-hash-not-the-credential-fingerprint"}}
					g.tenants.Set("org-m", cfg)
					answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, _ string) {
						if r.Header.Get("Authorization") != "Bearer "+token {
							t.Error("request did not use organization credential")
						}
						// Change the current pushed key while the invocation is in flight.
						next := managedOptIn()
						next.ManagedModels.UpstreamKeys = map[string]*tenant.ManagedUpstreamKey{"openrouter": {APIKey: "synthetic-future-key"}}
						g.tenants.Set("org-m", next)
						r601Response("0.0000095", stream)(w, r, "google/gemini-3.5-flash")
					})
					g.managed.responseWith.Store(&answer)
					g.managed.streamWith.Store(&answer)
					body := fmt.Sprintf(`{"model":"google/gemini-3.5-flash","max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, stream)
					if status := sendBillabilityBody(t, g, surface, body); status != 200 {
						t.Fatalf("status=%d", status)
					}
					rows := l.Outbox("org-m")
					row := rows[len(rows)-1]
					rec := l.OwnedSettlement("org-m", row["request_id"])
					if rec == nil || len(rec.InclusiveBindings) != 1 {
						t.Fatal("durable credential identity missing")
					}
					if rec.UpstreamKeyID != "management-hash-not-the-credential-fingerprint" || row["upstream_key_id"] != rec.UpstreamKeyID {
						t.Fatal("v1 financial upstream_key_id no longer preserves the control-plane key_hash")
					}
					for _, id := range rec.InclusiveBindings {
						if id.Provider != "openrouter" || id.Scope != "org-m" || id.Credential != models.CredentialFingerprint(token) || id.SnapshotModels != "" {
							t.Fatalf("wrong bound identity provider=%s scope=%s", id.Provider, id.Scope)
						}
					}
				}
			})
		}
	}
}
