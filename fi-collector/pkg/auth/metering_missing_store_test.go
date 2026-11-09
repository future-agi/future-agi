package auth

import (
	"context"
	"log/slog"
	"testing"
)

// Auth can still resolve tenants from PostgreSQL without Redis. That must not
// turn missing pause-store configuration into a billable admission exemption.
// Authored here; execute on the admitted Go kit, never the local host.
func TestMissingPauseStoreRefusesEveryBillableAdmission(t *testing.T) {
	t.Setenv("CLOUD_DEPLOYMENT", "DEV")
	meters := map[string]*Metering{
		"nil receiver": nil,
		"zero value":   {},
		"constructor":  NewMetering(nil, nil, slog.Default()),
	}
	for name, meter := range meters {
		for _, event := range []string{"tracing_event", "observe_add", "unknown_event"} {
			t.Run(name+"/"+event, func(t *testing.T) {
				result := meter.CheckUsage(context.Background(), "synthetic-org", event, 1)
				if result.Allowed || result.ErrorCode != "BUDGET_CHECK_UNAVAILABLE" || result.Dimension != "total_spend" {
					t.Fatalf("missing pause store must refuse with typed org-wide result: %+v", result)
				}
			})
		}
	}
}

func TestMissingStoreConstructorStillBuildsAdmission(t *testing.T) {
	t.Setenv("CLOUD_DEPLOYMENT", "DEV")
	if NewMetering(nil, nil, slog.Default()) == nil {
		t.Fatal("missing Redis must not disable metering")
	}
}
