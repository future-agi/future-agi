package budget

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
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func capMoneyPlugin(t *testing.T) *Plugin {
	t.Helper()
	db, err := strconv.Atoi(os.Getenv("TEST_REDIS_DB"))
	if err != nil {
		t.Fatal(err)
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: os.Getenv("TEST_REDIS_ADDR"), DB: db, Timeout: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	prefix := fmt.Sprintf("test:g8-money:%d:", time.Now().UnixNano())
	t.Cleanup(func() {
		it := client.Redis().Scan(context.Background(), 0, prefix+"*", 100).Iterator()
		for it.Next(context.Background()) {
			client.Redis().Del(context.Background(), it.Val())
		}
		client.Close()
	})
	p := New(nil, nil, false, nil)
	p.SetCapStore(redisstate.NewCapHoldStore(client, prefix))
	return p
}
func capMoneyConfig() *tenant.OrgConfig {
	limit := 0.005
	return &tenant.OrgConfig{Meta: &tenant.ConfigMeta{Version: 1}, Budgets: &tenant.BudgetsConfig{Enabled: true, AdmissionCapsEnabled: true, PolicyRevision: 1, NoMaxTokens: "clamp", Agents: map[string]*tenant.AdmissionCapConfig{"worker": {Limit: &limit, ActivatedAt: "2026-10-27T00:00:00Z"}}}, Routing: &tenant.RoutingConfig{Agents: &tenant.AgentsConfig{KeyBindings: map[string]string{"key-a": "worker", "key-b": "worker"}}}}
}
func capMoneyRequest(t *testing.T, org, key string, managed bool) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.Metadata["org_id"] = org
	rc.Metadata["auth_key_id"] = key
	if managed {
		rc.Metadata["billing_mode"] = models.BillingModeManaged
	}
	rc.Provider = "synthetic"
	rc.Model = "text"
	rc.EndpointType = "chat"
	rc.Request = &models.ChatCompletionRequest{Model: "text", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}}}
	rc.Billing.EndpointSet = []models.EndpointRef{{Price: models.PriceCard{Request: 0.004, Source: models.PriceSourceCatalog}}}
	a, e := ResolveAdmission(auth.Identity{OrgID: org, KeyID: key}, capMoneyConfig(), time.Now().Add(time.Minute))
	if e != nil {
		t.Fatal(e)
	}
	BindAdmission(rc, a)
	if e := QualifyCapChat(rc, nil, 256); e != nil {
		t.Fatal(e)
	}
	return rc
}

func TestCapManagedAndBYOKUseSameAgentLedger(t *testing.T) {
	for _, managed := range []bool{false, true} {
		t.Run(fmt.Sprint(managed), func(t *testing.T) {
			p := capMoneyPlugin(t)
			first := capMoneyRequest(t, "org-a", "key-a", managed)
			if e := p.reserveCaps(context.Background(), first); e != nil {
				t.Fatal(e)
			}
			if !RequestAdmission(first).ClaimAttempt() {
				t.Fatal("attempt refused")
			}
			first.Response = &models.ChatCompletionResponse{Usage: models.MeasuredUsage(1, 0)}
			first.Billing.UpstreamMicros = 3000
			first.Billing.BilledMicros = 3150
			p.finishCaps(first)
			next := capMoneyRequest(t, "org-a", "key-b", managed)
			if e := p.reserveCaps(context.Background(), next); e == nil || e.Code != "org_budget_exceeded" {
				t.Fatalf("two keys failed to share agent spend: %v", e)
			}
			other := capMoneyRequest(t, "org-b", "key-a", managed)
			if e := p.reserveCaps(context.Background(), other); e != nil {
				t.Fatalf("cross-org collision: %v", e)
			}
			// No wallet ledger is invoked by this budget plugin, including BYOK.
		})
	}
}

func TestCapForwardedRewriteRefusesAndReleasesHolds(t *testing.T) {
	for _, late := range []bool{false, true} {
		t.Run(fmt.Sprint(late), func(t *testing.T) {
			p := capMoneyPlugin(t)
			rc := capMoneyRequest(t, "org-a", "key-a", false)
			if late {
				if e := p.reserveCaps(context.Background(), rc); e != nil {
					t.Fatal(e)
				}
			}
			rc.Request.Messages = append(rc.Request.Messages, models.Message{Role: "system", Content: json.RawMessage(`"expanded remediation"`)})
			if late {
				if e := RecheckCapChat(rc); e == nil {
					t.Fatal("late rewrite accepted")
				}
				p.finishCaps(rc)
			} else {
				if e := p.reserveCaps(context.Background(), rc); e == nil || e.Code != "call_cap_unenforceable" {
					t.Fatalf("expanding pre-plugin rewrite admitted: %v", e)
				}
			}
			retry := capMoneyRequest(t, "org-a", "key-a", false)
			if e := p.reserveCaps(context.Background(), retry); e != nil {
				t.Fatalf("refusal leaked hold: %v", e)
			}
		})
	}
}

func TestCapTrustedIdentityMatrix(t *testing.T) {
	cfg := capMoneyConfig()
	for _, id := range []auth.Identity{{OrgID: "org-a"}, {KeyID: "key-a"}, {OrgID: "org-a", KeyID: "unbound"}} {
		if _, e := ResolveAdmission(id, cfg, time.Now().Add(time.Minute)); e == nil {
			t.Fatalf("missing identity accepted: %+v", id)
		}
	}
	p := capMoneyPlugin(t)
	rc := capMoneyRequest(t, "org-a", "key-a", false)
	original := RequestAdmission(rc)
	rc.Metadata["agent_id"] = "spoofed"
	rc.Metadata["auth_key_name"] = "renamed"
	if e := p.reserveCaps(context.Background(), rc); e != nil {
		t.Fatal(e)
	}
	if original.Agent() != "worker" || original.Key() != "key-a" {
		t.Fatal("metadata renamed authoritative bucket")
	}
	conflicting := capMoneyRequest(t, "org-a", "key-a", false)
	conflicting.Metadata["auth_key_id"] = "key-b"
	if e := p.reserveCaps(context.Background(), conflicting); e == nil {
		t.Fatal("conflicting key identity accepted")
	}
	duplicate := capMoneyRequest(t, "org-a", "key-a", false)
	duplicate.RequestID = rc.RequestID
	if RequestAdmission(duplicate).Nonce() == original.Nonce() {
		t.Fatal("caller id shared admission nonce")
	}
}
