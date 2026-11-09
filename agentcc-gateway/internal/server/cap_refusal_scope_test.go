package server

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

func capRefusalError(t *testing.T, w *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var body struct {
		Error map[string]any `json:"error"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	return body.Error
}

func TestCapRefusalDefaultOffMessages(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	g.grant(t, "org-m", 1)
	// Pin the refusal's origin as well as both Messages shapes.
	chat := g.chat(t, chatBody("anthropic/claude-sonnet-5.5"), nil)
	if chat.Code != 402 || errorCode(t, chat) != "managed_credits_insufficient" {
		t.Fatalf("credits setup: %d %s", chat.Code, chat.Body)
	}
	message := capRefusalError(t, chat)["message"]
	for _, stream := range []string{"false", "true"} {
		t.Run("stream="+stream, func(t *testing.T) {
			w := serveWithHeaders(t, g.srv, "POST", "/v1/messages", `{"model":"anthropic/claude-sonnet-5.5","max_tokens":64,"messages":[{"role":"user","content":"hi"}],"stream":`+stream+`}`, map[string]string{"Authorization": "Bearer " + managedOrgKey, "anthropic-version": "2023-06-01"}, 5*time.Second)
			if w.Code != 402 {
				t.Fatalf("status=%d %s", w.Code, w.Body)
			}
			err := capRefusalError(t, w)
			// Cost estimates differ from chat; pin only the stable base message
			// meaning, while the exact envelope has no code or metadata.
			msg, ok := err["message"].(string)
			if !ok || msg == "" || message == "" || !strings.Contains(msg, "credit") {
				t.Fatalf("credits message: %s", w.Body)
			}
			if !reflect.DeepEqual(err, map[string]any{"type": "api_error", "message": msg}) {
				t.Fatalf("caps-OFF shape changed: %s", w.Body)
			}
			var body map[string]any
			if e := json.Unmarshal(w.Body.Bytes(), &body); e != nil {
				t.Fatal(e)
			}
			if len(body) != 2 || body["type"] != "error" {
				t.Fatalf("envelope changed: %s", w.Body)
			}
		})
	}
	if g.managed.calls() != 0 {
		t.Fatal("credits refusal dispatched")
	}
}

func TestCapRefusalAttemptBudgetIdentity(t *testing.T) {
	g, _, _ := capComposedGateway(t)
	cfg := g.tenants.Get("org-m")
	key := g.srv.keyStore.Authenticate(managedOrgKey)
	for _, scope := range []string{"", "caller-budget-77"} {
		rc := &models.RequestContext{}
		rc.Agent.RunID, rc.Agent.BudgetScope = "caller-run-77", scope
		a, apiErr := capbudget.ResolveAdmission(auth.Identity{OrgID: "org-m", KeyID: key.ID}, cfg, time.Now().Add(time.Minute))
		if apiErr != nil || a == nil {
			t.Fatalf("admission: %v", apiErr)
		}
		capbudget.BindAdmission(rc, a)
		t.Cleanup(rc.RunFinalizers)
		w := httptest.NewRecorder()
		err := g.srv.handlers.attemptLimitError(w, rc, routing.SkipHop(nil, models.ReasonAttemptBudget))
		refusal, ok := err.(*models.APIError)
		if !ok {
			t.Fatalf("not an API refusal: %v", err)
		}
		models.WriteError(w, refusal)
		if w.Code != 402 || errorCode(t, w) != "call_cap_unenforceable" {
			t.Fatalf("attempt refusal: %d %s", w.Code, w.Body)
		}
		assertCapIdentity(t, w, "agent", "worker", "daily", 1)
		if strings.Contains(w.Body.String(), "caller-") {
			t.Fatalf("caller identity: %s", w.Body)
		}
	}
}

func TestCapRefusalLegacyRunSemantics(t *testing.T) {
	for _, runPolicy := range []bool{false, true} {
		for _, cause := range []string{"per-call", "managed-per-call", "caller-run", "stored-caller-run", "stopped"} {
			t.Run(cause+map[bool]string{false: "/daily-only", true: "/run-policy"}[runPolicy], func(t *testing.T) {
				g, client, prefix := capComposedGateway(t)
				cfg := g.tenants.Get("org-m")
				if runPolicy {
					v := 0.5
					cfg.Routing.Agents.MaxRunUSD = &v
				}
				headers := map[string]string{"x-agentcc-run-id": "synthetic-run"}
				code := "agent_call_cap_exceeded"
				switch cause {
				case "per-call":
					headers["x-agentcc-max-cost-usd"] = "0.000001"
				case "managed-per-call":
					v := 0.000001
					cfg.ManagedModels.MaxCallCostUSD = &v
				case "caller-run":
					headers["x-agentcc-budget-usd"] = "0.000001"
					code = "agent_budget_exhausted"
				case "stored-caller-run":
					// Earlier callers can persist a tighter run ceiling. The new
					// request omits it; it still must not masquerade as policy.
					client.Redis().Set(context.Background(), prefix+"abud:{org-m}:synthetic-run:limit", "1", 0)
					code = "agent_budget_exhausted"
				case "stopped":
					store := redisstate.NewAgentBudgetStore(client, prefix)
					if err := store.Stop(context.Background(), "org-m", "synthetic-run", "tool_call_blocked:shell", time.Minute); err != nil {
						t.Fatal(err)
					}
					code = "agent_run_stopped"
				}
				g.tenants.Set("org-m", cfg)
				w := g.chat(t, chatBody("openai/gpt-6-sol"), headers)
				if w.Code != 402 || errorCode(t, w) != code || g.managed.calls() != 0 {
					t.Fatalf("legacy refusal: %d %s", w.Code, w.Body)
				}
				err := capRefusalError(t, w)
				meta, _ := err["metadata"].(map[string]any)
				if _, ok := meta["cap_kind"]; ok {
					t.Fatalf("legacy refusal labelled as admission policy: %s", w.Body)
				}
				if cause == "stopped" && (meta["reason"] != "tool_call_blocked:shell" || !strings.Contains(err["message"].(string), "tool_call_blocked:shell")) {
					t.Fatalf("stop reason lost: %s", w.Body)
				}
			})
		}
	}
}
