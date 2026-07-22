package auth

import (
	"context"
	"log/slog"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func newTestRedis(t *testing.T) *redis.Client {
	t.Helper()
	rdb := redis.NewClient(&redis.Options{Addr: "localhost:6379"})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := rdb.Ping(ctx).Err(); err != nil {
		t.Skipf("Redis not available: %v", err)
	}
	return rdb
}

func newTestMetering(t *testing.T) *Metering {
	t.Helper()
	rdb := newTestRedis(t)
	t.Cleanup(func() { rdb.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	sha, err := rdb.ScriptLoad(ctx, checkQuotaLua).Result()
	if err != nil {
		t.Fatalf("script load: %v", err)
	}
	return &Metering{rdb: rdb, pg: nil, log: slog.Default(), luaSHA: sha}
}

func TestNewMeteringNilRedis(t *testing.T) {
	m := NewMetering(nil, nil, slog.Default())
	if m != nil {
		t.Fatal("expected nil for nil redis")
	}
}

func TestCheckUsageNilMetering(t *testing.T) {
	var m *Metering
	r := m.CheckUsage(context.Background(), "org-1", "tracing_event", 1)
	if !r.Allowed {
		t.Fatal("nil metering must allow")
	}
}

func TestCheckUsageUnknownEventType(t *testing.T) {
	m := newTestMetering(t)

	r := m.CheckUsage(context.Background(), "org-1", "unknown_event", 1)
	if !r.Allowed {
		t.Fatal("unknown event type must be allowed")
	}
}

func TestCheckUsageEventTypeToDimension(t *testing.T) {
	tests := []struct {
		eventType string
		dimension string
	}{
		{"tracing_event", "tracing_events"},
		{"observe_add", "storage"},
	}
	for _, tt := range tests {
		dim, ok := eventTypeToDimension[tt.eventType]
		if !ok || dim != tt.dimension {
			t.Errorf("eventTypeToDimension[%q] = %q, want %q", tt.eventType, dim, tt.dimension)
		}
	}
}

func TestCheckUsageFreeTierUnderLimit(t *testing.T) {
	m := newTestMetering(t)

	ctx := context.Background()

	orgID := "test-org-free-under"
	period := time.Now().UTC().Format("2006-01")
	usageKey := "usage:" + orgID + ":tracing_events:" + period
	planKey := "plan:" + orgID

	m.rdb.Set(ctx, planKey, "free", time.Minute)
	m.rdb.Set(ctx, usageKey, "100", time.Minute)
	defer m.rdb.Del(ctx, planKey, usageKey)

	r := m.CheckUsage(ctx, orgID, "tracing_event", 1)
	if !r.Allowed {
		t.Fatalf("expected allowed under limit, got: %+v", r)
	}
	if r.Dimension != "tracing_events" {
		t.Errorf("dimension = %q, want tracing_events", r.Dimension)
	}
}

func TestCheckUsageFreeTierAtLimit(t *testing.T) {
	m := newTestMetering(t)

	ctx := context.Background()

	orgID := "test-org-free-at"
	period := time.Now().UTC().Format("2006-01")
	usageKey := "usage:" + orgID + ":tracing_events:" + period
	planKey := "plan:" + orgID

	m.rdb.Set(ctx, planKey, "free", time.Minute)
	m.rdb.Set(ctx, usageKey, "50000", time.Minute)
	defer m.rdb.Del(ctx, planKey, usageKey)

	r := m.CheckUsage(ctx, orgID, "tracing_event", 1)
	if r.Allowed {
		t.Fatal("expected blocked at 50000, got allowed")
	}
	if r.ErrorCode != "FREE_TIER_LIMIT" {
		t.Errorf("error_code = %q, want FREE_TIER_LIMIT", r.ErrorCode)
	}
}

func TestCheckUsageFreeTierExactBoundary(t *testing.T) {
	m := newTestMetering(t)

	ctx := context.Background()

	orgID := "test-org-free-boundary"
	period := time.Now().UTC().Format("2006-01")
	usageKey := "usage:" + orgID + ":tracing_events:" + period
	planKey := "plan:" + orgID

	m.rdb.Set(ctx, planKey, "free", time.Minute)
	defer m.rdb.Del(ctx, planKey, usageKey)

	// At 49999, adding 1 should be allowed (49999+1 = 50000, not > 50000)
	m.rdb.Set(ctx, usageKey, "49999", time.Minute)
	r := m.CheckUsage(ctx, orgID, "tracing_event", 1)
	if !r.Allowed {
		t.Fatal("49999 + 1 = 50000, should be allowed (not > limit)")
	}

	// At 50000, adding 1 should be blocked (50000+1 > 50000)
	m.rdb.Set(ctx, usageKey, "50000", time.Minute)
	r = m.CheckUsage(ctx, orgID, "tracing_event", 1)
	if r.Allowed {
		t.Fatal("50000 + 1 > 50000, should be blocked")
	}
}

func TestCheckUsagePaidPlanNotBlocked(t *testing.T) {
	m := newTestMetering(t)

	ctx := context.Background()

	orgID := "test-org-paid"
	period := time.Now().UTC().Format("2006-01")
	usageKey := "usage:" + orgID + ":tracing_events:" + period
	planKey := "plan:" + orgID

	for _, plan := range []string{"payg", "enterprise", "team"} {
		m.rdb.Set(ctx, planKey, plan, time.Minute)
		m.rdb.Set(ctx, usageKey, "999999", time.Minute)

		r := m.CheckUsage(ctx, orgID, "tracing_event", 1)
		if !r.Allowed {
			t.Errorf("plan %q should not have hard cap, got blocked: %+v", plan, r)
		}
	}

	m.rdb.Del(ctx, planKey, usageKey)
}

func TestCheckUsageBudgetPause(t *testing.T) {
	m := newTestMetering(t)

	ctx := context.Background()

	orgID := "test-org-pause"
	planKey := "plan:" + orgID
	pauseKey := "pause:" + orgID + ":tracing_events"

	m.rdb.Set(ctx, planKey, "enterprise", time.Minute)
	m.rdb.Set(ctx, pauseKey, "1", time.Minute)
	defer m.rdb.Del(ctx, planKey, pauseKey)

	r := m.CheckUsage(ctx, orgID, "tracing_event", 1)
	if r.Allowed {
		t.Fatal("paused org should be blocked")
	}
	if r.ErrorCode != "BUDGET_PAUSED" {
		t.Errorf("error_code = %q, want BUDGET_PAUSED", r.ErrorCode)
	}

	// Remove pause, should be allowed
	m.rdb.Del(ctx, pauseKey)
	r = m.CheckUsage(ctx, orgID, "tracing_event", 1)
	if !r.Allowed {
		t.Fatal("unpaused org should be allowed")
	}
}

func TestCheckUsageBudgetPauseAndFreeTier(t *testing.T) {
	m := newTestMetering(t)

	ctx := context.Background()

	orgID := "test-org-pause-free"
	planKey := "plan:" + orgID
	pauseKey := "pause:" + orgID + ":tracing_events"

	m.rdb.Set(ctx, planKey, "free", time.Minute)
	m.rdb.Set(ctx, pauseKey, "1", time.Minute)
	defer m.rdb.Del(ctx, planKey, pauseKey)

	// Free tier under limit but paused — should be blocked with BUDGET_PAUSED
	r := m.CheckUsage(ctx, orgID, "tracing_event", 1)
	if r.Allowed {
		t.Fatal("paused free-tier org should be blocked")
	}
	if r.ErrorCode != "BUDGET_PAUSED" {
		t.Errorf("error_code = %q, want BUDGET_PAUSED", r.ErrorCode)
	}
}

func TestGetCachedPlanFromRedis(t *testing.T) {
	m := newTestMetering(t)

	ctx := context.Background()

	orgID := "test-org-plan-cache"
	planKey := "plan:" + orgID

	m.rdb.Set(ctx, planKey, "enterprise", time.Minute)
	defer m.rdb.Del(ctx, planKey)

	plan := m.getCachedPlan(ctx, orgID)
	if plan != "enterprise" {
		t.Errorf("plan = %q, want enterprise", plan)
	}
}

func TestGetCachedPlanDefaultsFree(t *testing.T) {
	m := newTestMetering(t)

	ctx := context.Background()

	// No PG pool, no Redis cache → defaults to "free"
	plan := m.getCachedPlan(ctx, "nonexistent-org-"+time.Now().Format("150405"))
	if plan != "free" {
		t.Errorf("plan = %q, want free (default)", plan)
	}
}

func TestCheckUsageStorageDimension(t *testing.T) {
	m := newTestMetering(t)

	ctx := context.Background()

	orgID := "test-org-storage"
	period := time.Now().UTC().Format("2006-01")
	usageKey := "usage:" + orgID + ":storage:" + period
	planKey := "plan:" + orgID

	m.rdb.Set(ctx, planKey, "free", time.Minute)
	m.rdb.Set(ctx, usageKey, "0", time.Minute)
	defer m.rdb.Del(ctx, planKey, usageKey)

	r := m.CheckUsage(ctx, orgID, "observe_add", 1)
	if !r.Allowed {
		t.Fatalf("storage under limit should be allowed: %+v", r)
	}
	if r.Dimension != "storage" {
		t.Errorf("dimension = %q, want storage", r.Dimension)
	}
}


func TestFreeTierAllowances(t *testing.T) {
	if freeTierAllowances["tracing_events"] != 50_000 {
		t.Errorf("tracing_events allowance = %d, want 50000", freeTierAllowances["tracing_events"])
	}
	if freeTierAllowances["storage"] != 50*1024*1024*1024 {
		t.Errorf("storage allowance = %d, want 50GB", freeTierAllowances["storage"])
	}
}

func TestHardCapPlans(t *testing.T) {
	if !hardCapPlans["free"] {
		t.Error("free plan should have hard cap")
	}
	if hardCapPlans["payg"] {
		t.Error("payg plan should not have hard cap")
	}
	if hardCapPlans["enterprise"] {
		t.Error("enterprise plan should not have hard cap")
	}
}

func TestCheckUsageLuaNotLoaded(t *testing.T) {
	rdb := newTestRedis(t)
	ctx := context.Background()

	m := &Metering{rdb: rdb, pg: nil, log: slog.Default(), luaSHA: ""}

	orgID := "test-org-no-lua"
	planKey := "plan:" + orgID
	rdb.Set(ctx, planKey, "free", time.Minute)
	defer rdb.Del(ctx, planKey)

	// Without Lua SHA, quota checks are skipped (fail-open)
	r := m.CheckUsage(ctx, orgID, "tracing_event", 1)
	if !r.Allowed {
		t.Fatal("with no lua SHA, should be allowed (fail-open)")
	}
}

func TestCheckUsageZeroUsageKey(t *testing.T) {
	m := newTestMetering(t)

	ctx := context.Background()

	orgID := "test-org-zero-usage"
	planKey := "plan:" + orgID

	m.rdb.Set(ctx, planKey, "free", time.Minute)
	defer m.rdb.Del(ctx, planKey)
	// No usage key set at all → current=0, 0+1 <= 50000 → allowed

	r := m.CheckUsage(ctx, orgID, "tracing_event", 1)
	if !r.Allowed {
		t.Fatal("zero usage should be allowed")
	}
}
