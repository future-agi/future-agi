package auth

import (
	"context"
	"log/slog"
	"testing"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

// Run on the admitted Go kit under its socket guard, not on the local host.
func TestOrgWidePauseBeforeEveryIngestionAdmission(t *testing.T) {
	t.Setenv("CLOUD_DEPLOYMENT", "DEV")
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr(), MaxRetries: -1})
	t.Cleanup(func() { _ = rdb.Close() })
	m := NewMetering(rdb, nil, slog.Default())
	ctx := context.Background()
	mr.Set("plan:synthetic-org", "enterprise")
	mr.Set("pause:synthetic-org:total_spend", "1")
	for _, event := range []string{"tracing_event", "observe_add", "unknown_event"} {
		t.Run(event, func(t *testing.T) {
			result := m.CheckUsage(ctx, "synthetic-org", event, 1)
			if result.Allowed || result.ErrorCode != "BUDGET_PAUSED" {
				t.Fatalf("org-wide pause must refuse: %+v", result)
			}
		})
	}
	if result := m.CheckUsage(ctx, "other-org", "tracing_event", 1); !result.Allowed {
		t.Fatalf("pause must not cross organizations: %+v", result)
	}
	mr.Del("pause:synthetic-org:total_spend")
	if result := m.CheckUsage(ctx, "synthetic-org", "tracing_event", 1); !result.Allowed {
		t.Fatalf("unpaused org should be unchanged: %+v", result)
	}
	mr.Set("pause:synthetic-org:total_spend", "1")
	if result := m.CheckUsage(ctx, "synthetic-org", "tracing_event", 1); result.Allowed {
		t.Fatal("must read the flag on every admission, not a cached allow")
	}
}

func TestPauseReadFailureIsTypedRefusal(t *testing.T) {
	t.Setenv("CLOUD_DEPLOYMENT", "DEV")
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr(), MaxRetries: -1})
	t.Cleanup(func() { _ = rdb.Close() })
	m := NewMetering(rdb, nil, slog.Default())
	mr.SetError("synthetic pause store unavailable")
	for _, event := range []string{"tracing_event", "observe_add", "unknown_event"} {
		result := m.CheckUsage(context.Background(), "synthetic-org", event, 1)
		if result.Allowed || result.ErrorCode != "BUDGET_CHECK_UNAVAILABLE" {
			t.Fatalf("unreadable pause must fail closed: %+v", result)
		}
	}
}
