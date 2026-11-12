package redisstate

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestR602RotatedCredentialPendingAndReconcile(t *testing.T) {
	for _, scope := range []string{"shared", "org-owned"} {
		t.Run(scope, func(t *testing.T) {
			s, rdb, _ := managedTestStore(t)
			mustGrant(t, s, "org-owned", 10000, 1, 0)
			model := "google/gemini-3.5-flash"
			if _, e := s.ReserveInclusive(bg, "org-owned", "req", "req.1", model, 100, time.Hour, billing.ReserveNew); e != nil {
				t.Fatal(e)
			}
			if e := s.BeginInclusive(bg, "org-owned", "req", "req.1"); e != nil {
				t.Fatal(e)
			}
			rec := ownedRecord("req", 100)
			rec.Kind = billing.KindSpend
			rec.UsageEstimated = true
			rec.Dimensional.InclusiveAttempts[0].Estimated = true
			old := "synthetic-before-rotation"
			current := "synthetic-after-rotation"
			rec.InclusiveBindings = map[string]models.InclusiveIdentity{"req.1": {Provider: "openrouter", Credential: models.CredentialFingerprint(old), Scope: scope}}
			if _, e := s.Settle(bg, "org-owned", "req", rec); e != nil {
				t.Fatal(e)
			}
			var calls atomic.Int64
			up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.Header.Get("Authorization") != "Bearer "+old {
					t.Error("lookup used rotated/current or another org's key")
					w.WriteHeader(403)
					return
				}
				fmt.Fprintf(w, `{"data":{"id":%q,"model":%q,"total_cost":0.0000095}}`, r.URL.Query().Get("id"), model)
			}))
			defer up.Close()
			org := scope
			if scope == "shared" {
				org = ""
			}
			adapter := inclusive.IdentityHTTPAdapters{HTTPAdapters: inclusive.HTTPAdapters{ProviderID: "openrouter", GenerationURL: up.URL}, Resolve: inclusive.RetainedResolver([]inclusive.RetainedCredential{{OrgID: org, Provider: "openrouter", Token: current}})}
			refunds := &refundMock{amounts: map[string]int64{}}
			if e := s.ReconcileOrg(bg, "org-owned", adapter, refunds); e == nil {
				t.Fatal("rotation without retained original key waived")
			}
			if calls.Load() != 0 || len(refunds.amounts) != 0 {
				t.Fatal("wrong identity contacted upstream or refunded")
			}
			if reason := rdb.HGet(bg, s.ownedKeys("org-owned", "")[8]+":errors", "req").Val(); reason != "original_credential_unavailable_or_not_retained" {
				t.Fatalf("missing durable pending reason %q", reason)
			}
			adapter.Resolve = inclusive.RetainedResolver([]inclusive.RetainedCredential{{OrgID: org, Provider: "openrouter", Token: old}, {OrgID: org, Provider: "openrouter", Token: current}})
			restarted := NewManagedCreditStore(s.client, s.prefix)
			if e := restarted.ReconcileOrg(bg, "org-owned", adapter, refunds); e != nil {
				t.Fatal(e)
			}
			if calls.Load() != 1 || refunds.amounts["org-owned/req"] != 89 {
				t.Fatalf("calls=%d refunds=%v", calls.Load(), refunds.amounts)
			}
			if rdb.SCard(bg, s.ownedKeys("org-owned", "")[8]).Val() != 0 || rdb.HExists(bg, s.ownedKeys("org-owned", "")[8]+":errors", "req").Val() {
				t.Fatal("successful correction not acknowledged")
			}
			raw := rdb.Get(bg, s.ownedKeys("org-owned", "req")[5]).Val()
			if strings.Contains(raw, old) || strings.Contains(raw, current) {
				t.Fatal("credential leaked into journal")
			}
			if scope != "shared" {
				a := rec.Dimensional.InclusiveAttempts[0]
				a.Identity = rec.InclusiveBindings["req.1"]
				if _, e := adapter.GenerationFor(context.Background(), "other-org", a); e == nil || calls.Load() != 1 {
					t.Fatal("cross-org identity accepted")
				}
			}
		})
	}
}
