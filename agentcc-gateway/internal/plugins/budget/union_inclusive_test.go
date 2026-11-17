package budget

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func unionUnquotedRequest(t *testing.T) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Provider, rc.Model, rc.EndpointType = "synthetic", "text", "chat"
	rc.Metadata["org_id"], rc.Metadata["billing_mode"] = "org", models.BillingModeManaged
	rc.Request = &models.ChatCompletionRequest{Model: "text", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	cfg := capMoneyConfig()
	zero := 0.0
	cfg.Budgets.Agents["worker"].Limit = &zero
	cfg.Routing.Agents.MaxRunUSD = &zero
	a, err := ResolveAdmission(auth.Identity{OrgID: "org", KeyID: "key-a"}, cfg, time.Now().Add(time.Minute))
	if err != nil || a == nil {
		t.Fatalf("zero daily/run policy: %v", err)
	}
	BindAdmission(rc, a)
	return rc
}

func TestUnionStrictCapsRejectInclusiveCardsBeforeHold(t *testing.T) {
	for _, scalar := range []bool{false, true} {
		for _, source := range []string{"pinned", "candidate-first", "candidate-last", "exact-lookup"} {
			t.Run(source+map[bool]string{false: "/generation-only", true: "/scalar-looking"}[scalar], func(t *testing.T) {
				rc := unionUnquotedRequest(t)
				paid := models.PriceCard{Source: models.PriceSourceCatalog, Inclusive: &models.InclusivePrice{Model: "text", Contract: "synthetic-generation", Generation: "0.08"}}
				if scalar {
					paid.Prompt, paid.Completion = 0.001, 0.002
				}
				free := models.PriceCard{Source: models.PriceSourceCatalog, ExplicitFree: true}
				rc.Billing.EndpointSet = []models.EndpointRef{{Price: free}}
				lookupCalls := 0
				lookup := func(provider, model, endpoint string) (models.PriceCard, bool) {
					lookupCalls++
					if provider != "synthetic" || model != "text" || endpoint != rc.Billing.EndpointID {
						t.Fatal("lookup did not use the exact admitted route")
					}
					return paid, true
				}
				switch source {
				case "pinned":
					rc.Billing.ReservePrice = &paid
				case "candidate-first":
					rc.Billing.EndpointSet = []models.EndpointRef{{Price: paid}, {Price: free}}
				case "candidate-last":
					rc.Billing.EndpointSet = append(rc.Billing.EndpointSet, models.EndpointRef{Price: paid})
				case "exact-lookup":
					rc.Billing.EndpointSet = nil
				}
				before, _ := json.Marshal(rc.Billing)
				err := QualifyCapChat(rc, lookup, 500, 256)
				if err == nil || err.Status != http.StatusPaymentRequired || err.Code != "call_cap_unenforceable" {
					t.Fatalf("inclusive quote must refuse before admission: %v", err)
				}
				a := RequestAdmission(rc)
				if a.quoteReady || a.Qualified() || a.ClaimAttempt() || rc.Billing.HoldMicros != 0 {
					t.Fatal("inclusive card acquired quote, reservation or dispatch authority")
				}
				after, _ := json.Marshal(rc.Billing)
				if string(before) != string(after) {
					t.Fatal("refusal changed billing state")
				}
				if (lookupCalls == 1) != (source == "exact-lookup") {
					t.Fatalf("lookup calls=%d source=%s", lookupCalls, source)
				}
			})
		}
	}
}

func TestUnionStrictCapsNeverQuoteGenerationAsZero(t *testing.T) {
	for _, inclusive := range []bool{true, false} {
		t.Run(map[bool]string{true: "positive-generation", false: "explicit-free-scalar"}[inclusive], func(t *testing.T) {
			rc := unionUnquotedRequest(t)
			card := models.PriceCard{Source: models.PriceSourceCatalog, ExplicitFree: true}
			if inclusive {
				card.ExplicitFree = false
				card.Inclusive = &models.InclusivePrice{Model: "text", Contract: "synthetic-lyria", Generation: "0.08"}
			}
			rc.Billing.EndpointSet = []models.EndpointRef{{Price: card}}
			err := QualifyCapChat(rc, nil, 500, 256)
			a := RequestAdmission(rc)
			if inclusive {
				if err == nil || err.Status != http.StatusPaymentRequired || err.Code != "call_cap_unenforceable" || a.quoteReady || a.ClaimAttempt() {
					t.Fatalf("positive generation qualified as free: error=%v quoteReady=%t", err, a.quoteReady)
				}
			} else if err != nil || !a.quoteReady || a.quoteMicros != 0 {
				t.Fatalf("explicit-free scalar control failed: error=%v ready=%t quote=%d", err, a.quoteReady, a.quoteMicros)
			}
		})
	}
}
