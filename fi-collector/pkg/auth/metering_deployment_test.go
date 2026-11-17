package auth

import (
	"bytes"
	"context"
	"log/slog"
	"strings"
	"testing"
)

// These controls never construct a Redis client or open a socket. Deployment
// intent, not the usage-emission switch, decides whether admission is billable.
func TestMeteringDeploymentWithoutRedis(t *testing.T) {
	for _, tt := range []struct {
		name, cloud, license, chart, events string
		billable                            bool
	}{
		{"oss", "", "", "", "", false},
		{"self-hosted", "false", "", "false", "false", false},
		{"usage-emission-is-not-billing", "", "", "", "true", false},
		{"cloud-us", "US", "", "", "false", true},
		{"cloud-eu", " eu ", "", "", "false", true},
		{"cloud-dev", "DEV", "", "", "", true},
		{"ee", "", "synthetic-license", "", "false", true},
		{"chart-ee-signal", "", "", "true", "false", true},
		{"cannot-disable-cloud", "US", "", "false", "false", true},
		{"cannot-disable-ee", "", "synthetic-license", "false", "false", true},
		{"malformed-signal-fails-closed", "", "", "tru", "false", true},
	} {
		t.Run(tt.name, func(t *testing.T) {
			t.Setenv("CLOUD_DEPLOYMENT", tt.cloud)
			t.Setenv("EE_LICENSE_KEY", tt.license)
			t.Setenv("FI_BILLING_ENFORCEMENT_ENABLED", tt.chart)
			t.Setenv("USAGE_EVENTS_ENABLED", tt.events)
			var logs bytes.Buffer
			m := NewMetering(nil, nil, slog.New(slog.NewTextHandler(&logs, nil)))
			if m == nil {
				t.Fatal("deployment decision must be explicit, not a nil admission object")
			}
			for _, event := range []string{"tracing_event", "observe_add", "unknown_event"} {
				r := m.CheckUsage(context.Background(), "synthetic-org", event, 1)
				if tt.billable {
					if r.Allowed || r.ErrorCode != "BUDGET_CHECK_UNAVAILABLE" || r.Dimension != "total_spend" {
						t.Fatalf("configured billing without Redis must fail closed: %+v", r)
					}
				} else if !r.Allowed || r.ErrorCode != "" {
					t.Fatalf("no billing writer: ingestion must remain available: %+v", r)
				}
			}
			if !tt.billable && strings.Count(logs.String(), "admission_mode=non-billable") != 1 {
				t.Fatalf("nonbillable mode must log once at startup, not per request: %s", logs.String())
			}
		})
	}
}
