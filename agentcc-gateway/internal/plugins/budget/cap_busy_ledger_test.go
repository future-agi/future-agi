package budget_test

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/agentbudget"
	"github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/providers/admission"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Exercise the actual slot limiter and both production settlement plugins:
// slow request holds the only slot -> busy refusal -> slot free -> next request.
func TestCapBusyThenSlotFreeKeepsDailyAndRunAvailable(t *testing.T) {
	for _, scope := range []string{"agent", "key", "both"} {
		for _, strictRun := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/run=%v", scope, strictRun), func(t *testing.T) {
				ctx := context.Background()
				db, err := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
				if err != nil {
					t.Fatal("TEST_REDIS_DB is required for the ledger regression: ", err)
				}
				client, err := redisstate.NewClient(redisstate.Config{Address: os.Getenv("TEST_REDIS_ADDR"), DB: db, Timeout: time.Second})
				if err != nil {
					t.Fatal(err)
				}
				prefix := fmt.Sprintf("test:walk-cap-busy:%d:", time.Now().UnixNano())
				t.Cleanup(func() {
					it := client.Redis().Scan(ctx, 0, prefix+"*", 100).Iterator()
					for it.Next(ctx) {
						client.Redis().Del(ctx, it.Val())
					}
					client.Close()
				})
				cfg := capBusyConfig(scope, strictRun)
				daily := budget.New(nil, nil, false, nil)
				daily.SetCapStore(redisstate.NewCapHoldStore(client, prefix))
				runs := redisstate.NewAgentBudgetStore(client, prefix)
				run := agentbudget.New(agentbudget.Deps{Store: runs, OrgConfig: func(string) *tenant.OrgConfig { return cfg }})
				limiter := admission.New(prefix, 1, time.Millisecond)
				t.Cleanup(limiter.Close)
				reserve := func(key string) *models.RequestContext {
					rc := capBusyRequest(t, cfg, key, strictRun)
					if r := daily.ProcessRequest(ctx, rc); r.Error != nil {
						t.Fatalf("daily admission after slot recovery: %v", r.Error)
					}
					if r := run.ProcessRequest(ctx, rc); r.Error != nil {
						t.Fatalf("run admission after slot recovery: %v", r.Error)
					}
					if !budget.RequestAdmission(rc).ClaimAttempt() {
						t.Fatal("qualified request could not claim its attempt")
					}
					return rc
				}
				settle := func(rc *models.RequestContext) {
					daily.ProcessResponse(ctx, rc)
					run.ProcessResponse(ctx, rc)
				}
				slow := reserve("key-a")
				started, release, done := make(chan error, 1), make(chan struct{}), make(chan struct{})
				go func() {
					defer close(done)
					err := limiter.Acquire(ctx)
					started <- err
					if err == nil {
						<-release
						limiter.Release()
					}
				}()
				released := false
				t.Cleanup(func() {
					if !released {
						close(release)
					}
					<-done
				})
				if err := <-started; err != nil {
					t.Fatal(err)
				}
				busy := reserve("key-a")
				busyErr := limiter.Acquire(ctx)
				if !models.IsProviderBusy(busyErr) {
					t.Fatalf("occupied slot did not produce a non-dispatch refusal: %v", busyErr)
				}
				// The same provenance mapping as server.attemptRun.finish. No
				// string parsing or invented usage/billing marks a refusal zero.
				outcome, class, origin := routing.AttemptResult(ctx, busyErr)
				busy.AddAttempt(models.Attempt{CredentialType: models.CredentialBYOK,
					Outcome: outcome, ErrorClass: class, ErrorOrigin: origin, StatusCode: routing.AttemptStatus(busyErr)})
				busy.Billing.BusyCredential = models.CredentialBYOK
				settle(busy)
				// Replaying post-processing/finalization must not fence or double-release.
				settle(busy)
				close(release)
				released = true
				<-done
				capBusySuccess(slow)
				settle(slow)
				if strictRun {
					state, err := runs.Peek(ctx, "org-busy", "same-run")
					if err != nil || state.HeldMicros != 0 || state.SpentMicros != 1000 {
						t.Errorf("busy refusal leaked a strict-run hold: state=%+v err=%v", state, err)
					}
				}
				// An agent cap is shared with a different key; a key cap is
				// retried with the same key. Policy and run identity never change.
				key := "key-a"
				if scope == "agent" {
					key = "key-b"
				}
				next := reserve(key)
				if err := limiter.Acquire(ctx); err != nil {
					t.Fatalf("provider slot did not recover: %v", err)
				}
				limiter.Release()
				capBusySuccess(next)
				settle(next)
				stateKey := prefix + "bhold:{org-busy}:" + time.Now().UTC().Format("2006-01-02") + ":state"
				state, err := client.Redis().HGetAll(ctx, stateKey).Result()
				if err != nil {
					t.Fatal(err)
				}
				levels := []string{"agent:worker", "key_id:key-a"}
				if scope == "agent" {
					levels = levels[:1]
				}
				if scope == "key" {
					levels = levels[1:]
				}
				for _, level := range levels {
					if state["spent:"+level] != "2000" || state["held:"+level] != "0" || (state["uncertain:"+level] != "" && state["uncertain:"+level] != "0") {
						t.Fatalf("daily ledger retained busy uncertainty or charged it: %+v", state)
					}
				}
			})
		}
	}
}

func capBusyConfig(scope string, strictRun bool) *tenant.OrgConfig {
	limit := 0.02
	cap := &tenant.AdmissionCapConfig{Limit: &limit, ActivatedAt: "2026-10-27T00:00:00Z"}
	cfg := &tenant.OrgConfig{Budgets: &tenant.BudgetsConfig{Enabled: true, AdmissionCapsEnabled: true, PolicyRevision: 1, NoMaxTokens: "clamp"},
		Routing: &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{KeyBindings: map[string]string{"key-a": "worker", "key-b": "worker"}}}}
	if scope != "key" {
		cfg.Budgets.Agents = map[string]*tenant.AdmissionCapConfig{"worker": cap}
	}
	if scope != "agent" {
		cfg.Budgets.KeyIDs = map[string]*tenant.AdmissionCapConfig{"key-a": cap}
	}
	if strictRun {
		cfg.Routing.Agents.MaxRunUSD = &limit
	}
	return cfg
}

func capBusyRequest(t *testing.T, cfg *tenant.OrgConfig, key string, strictRun bool) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Metadata["org_id"], rc.Metadata["auth_key_id"] = "org-busy", key
	rc.Provider, rc.Model, rc.EndpointType = "synthetic", "text", "chat"
	if strictRun {
		rc.Agent.RunID = "same-run"
	}
	rc.Request = &models.ChatCompletionRequest{Model: "text", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}}}
	rc.Billing.EndpointSet = []models.EndpointRef{{Price: models.PriceCard{Request: 0.004, Source: models.PriceSourceCatalog}}}
	a, err := budget.ResolveAdmission(auth.Identity{OrgID: "org-busy", KeyID: key}, cfg, time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	budget.BindAdmission(rc, a)
	if err := budget.QualifyCapChat(rc, nil, 500, 256); err != nil {
		t.Fatal(err)
	}
	return rc
}

func capBusySuccess(rc *models.RequestContext) {
	rc.AddAttempt(models.Attempt{CredentialType: models.CredentialBYOK, Outcome: models.OutcomeSuccess,
		Usage: models.MeasuredUsage(1, 0), UpstreamCostMicros: 1000})
}
