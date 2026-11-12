package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const callerFallbackProviders = `"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["org-model","org-model-2","org-model-3"],"enabled":true}}`

func callerFallbackGateway(t *testing.T, routingJSON string) (*Server, *r7Upstream) {
	t.Helper()
	up := r7StartUpstream(t, func(model string, _ int) bool { return model == "org-model" })
	srv := r7Gateway(t, map[string]r7Op{"alpha": {up, []string{"org-model", "org-model-2", "org-model-3"}}}, nil)
	r7Push(t, srv, r7OrgA, `{`+callerFallbackProviders+`,"routing":`+routingJSON+`}`)
	return srv, up
}

func callerFallbackChat(t *testing.T, srv *Server, key string, caller, stream bool) *httptest.ResponseRecorder {
	t.Helper()
	fields := fmt.Sprintf(`"stream":%v`, stream)
	if caller {
		fields += `,"models":["org-model","org-model-3"]`
	}
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body("org-model", fields)))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// The HTTP matrix decodes the real admin contract (false must not become
// unset), then dispatches chat through both the customer and internal-key
// paths, streamed and not. A caller chain replaces, rather than appends to,
// the org's stored chain. An absent switch still permits either chain.
func TestCallerFallbackControlMatrix(t *testing.T) {
	for _, mode := range []string{"true", "false", "unset"} {
		for _, stored := range []bool{false, true} {
			for _, caller := range []bool{false, true} {
				for _, stream := range []bool{false, true} {
					for _, key := range []struct{ name, value string }{{"customer", r7CustA}, {"internal", r7IntlA}} {
						name := fmt.Sprintf("%s/stored=%v/caller=%v/stream=%v/%s", mode, stored, caller, stream, key.name)
						t.Run(name, func(t *testing.T) {
							var fields []string
							if mode != "unset" {
								fields = append(fields, `"fallback_enabled":`+mode)
							}
							if stored {
								fields = append(fields, `"model_fallbacks":{"org-model":["org-model-2"]}`)
							}
							srv, up := callerFallbackGateway(t, "{"+strings.Join(fields, ",")+"}")
							w := callerFallbackChat(t, srv, key.value, caller, stream)
							calls := up.snapshot()
							wantFallback := mode != "false" && (stored || caller)
							if wantFallback {
								wantModel := "org-model-2"
								if caller {
									wantModel = "org-model-3"
								}
								if w.Code != http.StatusOK || len(calls) < 2 || calls[len(calls)-1].Model != wantModel {
									t.Fatalf("status=%d calls=%v body=%s; want fallback to %s", w.Code, calls, w.Body, wantModel)
								}
								for _, c := range calls[:len(calls)-1] {
									if c.Model != "org-model" {
										t.Fatalf("unexpected intermediate model: %v", calls)
									}
								}
							} else {
								if w.Code < 500 || len(calls) == 0 {
									t.Fatalf("status=%d calls=%v body=%s; want primary upstream failure", w.Code, calls, w.Body)
								}
								for _, c := range calls {
									if c.Model != "org-model" {
										t.Fatalf("disabled or absent chain dispatched a fallback: %v", calls)
									}
								}
								if mode == "false" && len(calls) != 1 {
									t.Fatalf("disabled fallback dispatched %d attempts, want primary only", len(calls))
								}
							}
							if key.name == "customer" {
								r7AssertOrgCredentialOnly(t, up)
							}
						})
					}
				}
			}
		}
	}
}

// Checking only installation is insufficient: even an ignored models[]
// must not widen the org's status-code policy for a different/global chain.
func TestCallerFallbackControlDoesNotWidenFailoverCodes(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			srv, up := callerFallbackGateway(t, `{"fallback_enabled":false,"failover":{"enabled":true,"on_status_codes":[429]}}`)
			srv.handlers.modelFallbacks.Store(routing.NewModelFallbacks(map[string][]string{"org-model": {"org-model-2"}}))
			w := callerFallbackChat(t, srv, r7CustA, true, stream)
			if calls := up.snapshot(); w.Code < 500 || len(calls) != 1 || calls[0].Model != "org-model" {
				t.Fatalf("status=%d calls=%v body=%s; disabled models[] must not turn a 503 into a fallback", w.Code, calls, w.Body)
			}
		})
	}
}

func TestCallerFallbackControlUsesEffectiveRollout(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("candidate=%v/stream=%v", enabled, stream), func(t *testing.T) {
				cfg := fmt.Sprintf(`{"fallback_enabled":%v,"rollout":{"version":"v2","percent":100,"sticky_by":"key","routing":{"fallback_enabled":%v}}}`, !enabled, enabled)
				srv, up := callerFallbackGateway(t, cfg)
				w := callerFallbackChat(t, srv, r7CustA, true, stream)
				calls := up.snapshot()
				if enabled {
					if w.Code != http.StatusOK || len(calls) < 2 || calls[len(calls)-1].Model != "org-model-3" {
						t.Fatalf("enabled candidate: status=%d calls=%v body=%s", w.Code, calls, w.Body)
					}
				} else if w.Code < 500 || len(calls) != 1 || calls[0].Model != "org-model" {
					t.Fatalf("disabled candidate: status=%d calls=%v body=%s", w.Code, calls, w.Body)
				}
			})
		}
	}
}

// Disabling a list neither rejects a healthy primary nor forwards that
// list upstream, on either funding mode. A failing Future AGI primary must
// also stop after its own attempt instead of spending on another model.
func TestCallerFallbackControlPrimaryAndFundingModes(t *testing.T) {
	for _, managed := range []bool{false, true} {
		for _, fail := range []bool{false, true} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("managed=%v/fail=%v/stream=%v", managed, fail, stream), func(t *testing.T) {
					org := byokOrg()
					primary, fallback := "gpt-4o", managedModel
					var orFails, opFails int32
					if managed {
						org = managedOrg()
						primary, fallback = managedModel, "openai/gpt-6-sol"
						if fail {
							orFails = 1
						}
					} else if fail {
						opFails = 1
					}
					org.Routing = &tenant.RoutingConfig{}
					org.Routing.SetFallbackEnabled(false)
					g := newFailingRoutingGateway(t, org, orFails, opFails)
					w := g.chat(t, body(primary, fmt.Sprintf(`"models":[%q],"stream":%v`, fallback, stream)))
					if fail && w.Code < 500 || !fail && w.Code != http.StatusOK {
						t.Fatalf("primary status=%d body=%s, fail=%v", w.Code, w.Body, fail)
					}
					calls := append(g.operator.received(), g.openrouter.received()...)
					if len(calls) != 1 || calls[0]["model"] != primary || calls[0]["models"] != nil {
						t.Fatalf("calls=%v; want primary only, with models[] stripped", calls)
					}
				})
			}
		}
	}
}

func TestCallerFallbackControlStillChecksEligibility(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			org := managedOrg()
			org.ManagedModels.DenyVendors = []string{"openai"}
			org.Routing = &tenant.RoutingConfig{FallbackEnabled: true}
			g := newFailingRoutingGateway(t, org, 1, 0)
			w := g.chat(t, body(managedModel, fmt.Sprintf(`"models":["openai/gpt-6-sol"],"stream":%v`, stream)))
			if calls := g.openrouter.received(); w.Code < 500 || len(calls) != 1 || calls[0]["model"] != managedModel {
				t.Fatalf("status=%d calls=%v body=%s; denied model must not be dispatched", w.Code, calls, w.Body)
			}
		})
	}
}

func TestCallerFallbackControlStillNeedsCredit(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			org := managedOptIn()
			org.Providers = map[string]*tenant.ProviderConfig{
				"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: []string{"org-model"}, Enabled: true},
			}
			org.Routing = &tenant.RoutingConfig{FallbackEnabled: true}
			g := newManagedTestGateway(t, org)
			g.operator.failFor.Store("org-model", http.StatusServiceUnavailable)
			w := g.chat(t, body("org-model", fmt.Sprintf(`"models":[%q],"stream":%v`, managedModel, stream)), nil)
			if w.Code < 500 || g.operator.calls() == 0 || g.managed.calls() != 0 || g.ledger.BalanceUSD("org-m") != 0 {
				t.Fatalf("status=%d byok=%d funded=%d body=%s; unfunded fallback must not be dispatched", w.Code, g.operator.calls(), g.managed.calls(), w.Body)
			}
		})
	}
}
