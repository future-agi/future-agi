package auth

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"testing"

	"github.com/redis/go-redis/v9"
)

// Exercise the startup constructor and request path without a datastore or
// socket. The mode log is the hosted rollout check, not a per-request event.
func TestMeteringStartupAdmissionModeLog(t *testing.T) {
	for _, tt := range []struct {
		name, cloud, license, chart, mode, level string
	}{
		{"oss", "", "", "", "non-billable", "WARN"},
		{"explicit-self-hosted", "false", "", "false", "non-billable", "WARN"},
		{"hosted", "US", "", "", "billable", "INFO"},
		{"licensed", "", "synthetic-license-not-for-logs", "", "billable", "INFO"},
		{"chart", "", "", "true", "billable", "INFO"},
	} {
		for _, withStore := range []bool{false, true} {
			t.Run(tt.name+map[bool]string{false: "-no-store", true: "-store"}[withStore], func(t *testing.T) {
				t.Setenv("CLOUD_DEPLOYMENT", tt.cloud)
				t.Setenv("EE_LICENSE_KEY", tt.license)
				t.Setenv("FI_BILLING_ENFORCEMENT_ENABLED", tt.chart)
				var rdb *redis.Client
				if withStore {
					rdb = redis.NewClient(&redis.Options{Addr: "unused.invalid:6379", MaxRetries: -1})
					rdb.AddHook(&billingStoreHook{values: map[string]string{"plan:synthetic-org": "enterprise"}})
					t.Cleanup(func() { _ = rdb.Close() })
				}
				var logs bytes.Buffer
				m := NewMetering(rdb, nil, slog.New(slog.NewJSONHandler(&logs, nil)))
				startup := logs.String()
				for i := 0; i < 3; i++ {
					m.CheckUsage(context.Background(), "synthetic-org", "tracing_event", 1)
				}
				if logs.String() != startup {
					t.Fatalf("mode must not be logged per request: %s", logs.String())
				}
				count := 0
				for _, line := range strings.Split(strings.TrimSpace(startup), "\n") {
					var entry map[string]any
					if err := json.Unmarshal([]byte(line), &entry); err != nil {
						t.Fatal(err)
					}
					if _, ok := entry["admission_mode"]; !ok {
						continue // e.g. missing Redis has its own fail-closed error
					}
					count++
					if entry["admission_mode"] != tt.mode || entry["level"] != tt.level {
						t.Fatalf("unexpected admission mode log: %s", line)
					}
					if tt.mode == "non-billable" {
						message, _ := entry["msg"].(string)
						for _, want := range []string{
							"collector is not enforcing billing",
							"expected for self-hosted open source",
							"Do NOT set billing signals unless running Future AGI Enterprise or Cloud",
						} {
							if !strings.Contains(message, want) {
								t.Fatalf("non-billable warning must explain %q: %s", want, line)
							}
						}
						if _, ok := entry["missing_signals"]; ok {
							t.Fatalf("OSS must not be told it is missing billing signals: %s", line)
						}
						missing, ok := entry["enterprise_cloud_only_signals"].([]any)
						if !ok || len(missing) != 3 {
							t.Fatalf("non-billable log must name signals for Enterprise/Cloud operators only: %s", line)
						}
						for _, name := range []string{"EE_LICENSE_KEY", "CLOUD_DEPLOYMENT", "FI_BILLING_ENFORCEMENT_ENABLED"} {
							found := false
							for _, signal := range missing {
								found = found || strings.Contains(signal.(string), name)
							}
							if !found {
								t.Fatalf("missing signal %s not named: %s", name, line)
							}
						}
					}
				}
				if count != 1 {
					t.Fatalf("want exactly one startup admission mode log, got %d: %s", count, startup)
				}
				if strings.Contains(startup, "synthetic-license-not-for-logs") {
					t.Fatal("startup mode log leaked license value")
				}
			})
		}
	}
}
