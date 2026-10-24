package tenant

import (
	"encoding/json"
	"testing"
	"time"
)

// The severity and cooldown an org alert rule is read
// with. The platform pushes severity (info, warning or critical); a rule
// without a cooldown waits DefaultAlertCooldown (15m) between firings.
func TestMonitorAlertRuleAccessors(t *testing.T) {
	if DefaultAlertCooldown != 15*time.Minute {
		t.Fatalf("DefaultAlertCooldown = %s; the docs and the console state 15m", DefaultAlertCooldown)
	}
	if DefaultAlertSeverity != "warning" {
		t.Fatalf("DefaultAlertSeverity = %q; the console states warning", DefaultAlertSeverity)
	}

	var rule AlertRuleConfig
	if err := json.Unmarshal([]byte(`{"name":"r","severity":"critical","cooldown":"1h"}`), &rule); err != nil {
		t.Fatal(err)
	}
	if sev, known := rule.AlertSeverity(); sev != "critical" || !known {
		t.Fatalf("AlertSeverity() = %q, %v; want critical, true (severity is read from the pushed JSON)", sev, known)
	}
	if got := rule.AlertCooldown(); got != time.Hour {
		t.Fatalf("AlertCooldown() = %s, want 1h", got)
	}

	for _, tc := range []struct {
		severity  string
		want      string
		wantKnown bool
	}{
		{"", "warning", true},
		{"info", "info", true},
		{"warning", "warning", true},
		{"critical", "critical", true},
		{"CRITICAL", "warning", false},
		{"sev1", "warning", false},
	} {
		r := AlertRuleConfig{Severity: tc.severity}
		if got, known := r.AlertSeverity(); got != tc.want || known != tc.wantKnown {
			t.Errorf("severity %q: AlertSeverity() = %q, %v; want %q, %v", tc.severity, got, known, tc.want, tc.wantKnown)
		}
	}

	for _, tc := range []struct {
		cooldown string
		want     time.Duration
	}{
		{"", 15 * time.Minute},
		{"not-a-duration", 15 * time.Minute},
		{"-5m", 15 * time.Minute},
		{"0s", 0},
		{"30s", 30 * time.Second},
		{"2h", 2 * time.Hour},
	} {
		r := AlertRuleConfig{Cooldown: tc.cooldown}
		if got := r.AlertCooldown(); got != tc.want {
			t.Errorf("cooldown %q: AlertCooldown() = %s, want %s", tc.cooldown, got, tc.want)
		}
	}

	var nilRule *AlertRuleConfig
	if sev, _ := nilRule.AlertSeverity(); sev != "warning" {
		t.Errorf("nil rule severity = %q, want warning", sev)
	}
	if got := nilRule.AlertCooldown(); got != DefaultAlertCooldown {
		t.Errorf("nil rule cooldown = %s, want %s", got, DefaultAlertCooldown)
	}
}
