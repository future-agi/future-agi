package agentbudget

import (
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	contract "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A concurrent caller can tighten the stored ceiling after CallCap. Attribute
// the ReserveStrict result, not merely the current request's administrative cap.
func TestCapRefusalHoldAttribution(t *testing.T) {
	for _, tc := range []struct {
		name              string
		stored, requested int64
		admission         bool
	}{
		{"policy", -1, 10000, true},
		{"equal-caller", 10000, 10000, true},
		{"tighter-caller", -1, 1000, false},
		{"tighter-stored", 1000, 10000, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store, _, _ := testStore(t)
			usd := 0.01
			cfg := &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{Enabled: true, AdmissionCapsEnabled: true, PolicyRevision: 7, NoMaxTokens: "clamp"}, Routing: &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{MaxRunUSD: &usd}}}
			a, e := capbudget.ResolveAdmission(auth.Identity{OrgID: testOrg, KeyID: "key-a"}, cfg, time.Now().Add(time.Minute))
			if e != nil || a == nil {
				t.Fatalf("admission: %v", e)
			}
			if tc.stored >= 0 {
				res, err := store.ReserveStrict(bg, testOrg, "run", "seed", 0, tc.stored, time.Minute, time.Hour)
				if err != nil || res.Status != redisstate.AgentBudgetOK {
					t.Fatalf("seed: %+v %v", res, err)
				}
				if _, err := store.Complete(bg, testOrg, "run", "seed", 0, time.Hour); err != nil {
					t.Fatal(err)
				}
			}
			p := testPlugin(store, cardFee10k, cfg)
			st := &runState{strict: a, org: testOrg, scope: "run", holdID: a.Nonce(), limitMicros: tc.requested, pol: policyOf(cfg)}
			err := p.hold(bg, call(t, 64), st, estimate{micros: 20000, priced: true})
			if err == nil || err.Code != CodeBudgetExhausted {
				t.Fatalf("refusal: %v", err)
			}
			md, ok := err.Metadata.(contract.AdmissionCapRefusalMetadata)
			if ok != tc.admission {
				t.Fatalf("wrong attribution: %+v", err)
			}
			if ok && (md.CapKind != "run" || md.CapScope != "key" || md.CapRef != "key-a" || md.PolicyRevision != 7) {
				t.Fatalf("identity: %+v", md)
			}
			if !ok {
				if _, legacy := err.Metadata.(models.PaymentMeta); !legacy {
					t.Fatalf("legacy contract lost: %+v", err)
				}
			}
		})
	}
}
