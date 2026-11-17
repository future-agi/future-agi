package logspool

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Merge keeps the summary's per-team and per-tag spend: the start-up seed
// reads them after the merge. The spool's own lines carry no team or tag, so
// they add to the org, key, user and model levels only.
func TestMergeKeepsTheSummarysTeamAndTagSpend(t *testing.T) {
	s := &tenant.SpendSummary{Period: "monthly", Orgs: map[string]tenant.OrgSpendSummary{
		"org-a": {TotalSpend: 10, PerTeam: map[string]float64{"eng": 6}, PerTag: map[string]float64{"project:atlas": 4}},
	}}
	x := &Spend{}
	x.add("org-a", "svc-a", "user-1", "m-1", 2)
	got := Merge(s, x, "monthly", PeriodStart("monthly", time.Now())).Orgs["org-a"]
	if got.PerTeam["eng"] != 6 || got.PerTag["project:atlas"] != 4 {
		t.Fatalf("merged team %v tag %v, want the summary's eng 6 and project:atlas 4", got.PerTeam, got.PerTag)
	}
	if got.TotalSpend != 12 {
		t.Fatalf("merged total %v, want 12", got.TotalSpend)
	}
	s.Orgs["org-a"].PerTeam["eng"] = 99
	if got.PerTeam["eng"] != 6 {
		t.Fatal("Merge shares the summary's per-team map")
	}
}

// A spooled request counts under its key's name, as the budgets and the
// control plane's spend summary key it, not under the key's id.
func TestASpooledLineCountsUnderItsKeysName(t *testing.T) {
	raw, _ := json.Marshal(map[string]any{
		"request_id": "req-named", "timestamp": time.Now(), "model": "m-1", "cost": 1.5,
		"auth_key_id": "gk-1", "user_id": "alice",
		"metadata": map[string]string{"key_org_id": "org-a", "auth_key_id": "gk-1", "auth_key_name": "svc-a"},
	})
	line, err := SpendOf(raw)
	if err != nil {
		t.Fatal(err)
	}
	if line.KeyID != "svc-a" {
		t.Fatalf("spend line key %q, want the key's name svc-a", line.KeyID)
	}
	s := &tenant.SpendSummary{Orgs: map[string]tenant.OrgSpendSummary{"org-a": {TotalSpend: 4, PerKey: map[string]float64{"svc-a": 4}}}}
	x := &Spend{}
	x.add(line.Org, line.KeyID, line.UserID, line.Model, line.Cost)
	if got := Merge(s, x, "monthly", PeriodStart("monthly", time.Now())).Orgs["org-a"].PerKey["svc-a"]; got != 5.5 {
		t.Fatalf("merged spend of svc-a %v, want 5.5", got)
	}
}
