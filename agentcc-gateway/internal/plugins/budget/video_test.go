package budget

import (
	"context"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"reflect"
	"testing"
)

type scopeRecorder struct{ calls []string }

func (r *scopeRecorder) RecordSpend(org, level, key, period, model string, cost float64) (float64, bool) {
	r.calls = append(r.calls, level+"/"+key+"/"+period)
	return cost, true
}
func (r *scopeRecorder) GetSpend(string, string, string, string) (float64, map[string]float64, bool) {
	return 0, nil, true
}
func (r *scopeRecorder) Available() bool { return true }
func TestVideoHierarchyParityAndDirectSettlement(t *testing.T) {
	b := &tenant.BudgetsConfig{Enabled: true, OrgLimit: 100, HardLimit: true, DefaultPeriod: "monthly", Teams: map[string]*tenant.BudgetLevelConfig{"team": {Limit: 20}}, Users: map[string]*tenant.BudgetLevelConfig{"owner": {Limit: 5, Period: "daily"}}, Keys: map[string]*tenant.BudgetLevelConfig{"name": {Limit: 10}}, Tags: map[string]*tenant.BudgetLevelConfig{"project:test": {Limit: 2}}}
	ts := tenant.NewStore()
	ts.Set("org", &tenant.OrgConfig{Budgets: b})
	p := New(nil, nil, false, ts)
	record := &scopeRecorder{}
	p.SetRedisBudget(record)
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "video/model"
	rc.Metadata = map[string]string{"key_org_id": "org", "key_team": "team", "auth_key_owner": "owner", "auth_key_name": "name", "tag:project": "test", "cost": "1.5"}
	expected := []string{"org//monthly", "team/team/monthly", "user/owner/daily", "key/name/monthly", "tag/project:test/monthly"}
	scopes := HierarchyScopes(b, rc)
	var actual []string
	for _, s := range scopes {
		actual = append(actual, s.Level+"/"+s.Key+"/"+s.Period)
	}
	if !reflect.DeepEqual(actual, expected) {
		t.Fatal(actual)
	}
	p.ProcessResponse(context.Background(), rc)
	if !reflect.DeepEqual(record.calls, expected) {
		t.Fatal(record.calls)
	}
	rc.Metadata["video_settlement"] = "direct"
	p.ProcessResponse(context.Background(), rc)
	if !reflect.DeepEqual(record.calls, expected) {
		t.Fatal("direct settlement recorded twice")
	}
}
