package generated_test

import (
	"bytes"
	"encoding/json"
	"testing"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The platform pushes an alert rule's severity. The admin API decodes a push
// strictly into the generated contract (DisallowUnknownFields,
// server/admin_orgs.go), which carries the severity through to the gateway's
// tenant config and the alerting plugin.
func TestAlertRuleSeverityRoundTrips(t *testing.T) {
	body := []byte(`{"alerting":{"enabled":true,"rules":[{"name":"errors","metric":"error_count","condition":">=","threshold":5,"window":"5m","cooldown":"30m","channels":["ops"],"severity":"critical"}],"channels":[{"name":"ops","type":"webhook","url":"https://alerts.example.com/hook"}]}}`)

	var contract gatewayadmin.OrgConfig
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&contract); err != nil {
		t.Fatalf("the contract refuses an alert rule with a severity: %v", err)
	}
	encoded, err := json.Marshal(contract)
	if err != nil {
		t.Fatal(err)
	}
	var runtime tenant.OrgConfig
	if err := json.Unmarshal(encoded, &runtime); err != nil {
		t.Fatal(err)
	}
	if runtime.Alerting == nil || len(runtime.Alerting.Rules) != 1 {
		t.Fatalf("alerting rules did not round-trip: %#v", runtime.Alerting)
	}
	rule := runtime.Alerting.Rules[0]
	if sev, known := rule.AlertSeverity(); sev != "critical" || !known {
		t.Fatalf("severity after the round trip = %q (known %v), want critical", sev, known)
	}
	if rule.Cooldown != "30m" {
		t.Fatalf("cooldown after the round trip = %q, want 30m", rule.Cooldown)
	}
}
