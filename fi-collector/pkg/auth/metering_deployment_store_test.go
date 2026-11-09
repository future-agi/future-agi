package auth

import (
	"context"
	"errors"
	"log/slog"
	"net"
	"testing"

	"github.com/redis/go-redis/v9"
)

// Use go-redis's existing hook interface, not a Redis server. Every command is
// answered in memory; even an unexpected command can never open a socket.
type billingStoreHook struct {
	values map[string]string
	failed bool
	reads  int
}

func (h *billingStoreHook) DialHook(redis.DialHook) redis.DialHook {
	return func(context.Context, string, string) (net.Conn, error) {
		return nil, errors.New("socket-free billing test forbids dialing")
	}
}
func (h *billingStoreHook) ProcessPipelineHook(redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(context.Context, []redis.Cmder) error { return errors.New("unexpected pipeline") }
}
func (h *billingStoreHook) ProcessHook(redis.ProcessHook) redis.ProcessHook {
	return func(_ context.Context, cmd redis.Cmder) error {
		if cmd.Name() == "script" {
			cmd.(*redis.StringCmd).SetVal("synthetic-loaded-script")
			return nil
		}
		if cmd.Name() != "get" {
			return errors.New("unexpected command")
		}
		h.reads++
		if h.failed {
			return errors.New("synthetic unreadable store")
		}
		key := cmd.Args()[1].(string)
		if value, ok := h.values[key]; ok {
			cmd.(*redis.StringCmd).SetVal(value)
			return nil
		}
		return redis.Nil
	}
}

func TestMeteringDeploymentStoreDecisions(t *testing.T) {
	for _, tt := range []struct {
		name, cloud, event, pauseScope, errorCode string
		failed                                    bool
	}{
		{"oss-ignores-orphan-pause", "", "tracing_event", "total_spend", "", false},
		{"oss-ignores-store-failure", "", "tracing_event", "", "", true},
		{"cloud-total-pause", "DEV", "tracing_event", "total_spend", "BUDGET_PAUSED", false},
		{"cloud-unknown-event-pause", "DEV", "unknown_event", "total_spend", "BUDGET_PAUSED", false},
		{"cloud-dimension-pause", "DEV", "tracing_event", "tracing_events", "BUDGET_PAUSED", false},
		{"cloud-store-failure", "DEV", "tracing_event", "", "BUDGET_CHECK_UNAVAILABLE", true},
		{"cloud-unknown-event-store-failure", "DEV", "unknown_event", "", "BUDGET_CHECK_UNAVAILABLE", true},
		{"cloud-unpaused", "DEV", "tracing_event", "", "", false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			t.Setenv("CLOUD_DEPLOYMENT", tt.cloud)
			t.Setenv("EE_LICENSE_KEY", "")
			t.Setenv("FI_BILLING_ENFORCEMENT_ENABLED", "")
			hook := &billingStoreHook{values: map[string]string{"plan:synthetic-org": "enterprise"}, failed: tt.failed}
			if tt.pauseScope != "" {
				hook.values["pause:synthetic-org:"+tt.pauseScope] = "1"
			}
			rdb := redis.NewClient(&redis.Options{Addr: "unused.invalid:6379", MaxRetries: -1})
			rdb.AddHook(hook)
			t.Cleanup(func() { _ = rdb.Close() })
			m := NewMetering(rdb, nil, slog.Default())
			r := m.CheckUsage(context.Background(), "synthetic-org", tt.event, 1)
			if r.ErrorCode != tt.errorCode || r.Allowed != (tt.errorCode == "") {
				t.Fatalf("admission = %+v, want code %q", r, tt.errorCode)
			}
			if tt.cloud == "" && hook.reads != 0 {
				t.Fatal("nonbillable admission must not read budget or plan state")
			}
		})
	}
}
