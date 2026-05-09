package budget

import (
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

func boolPtr(b bool) *bool { return &b }

func testConfig() config.BudgetsConfig {
	return config.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "monthly",
		WarnThreshold: 0.8,
		Org: &config.BudgetLevelConfig{
			Limit: 10000.00,
			PerModel: map[string]float64{
				"gpt-4o": 3000.00,
			},
		},
		Teams: map[string]config.BudgetLevelConfig{
			"engineering": {
				Limit: 5000.00,
				PerModel: map[string]float64{
					"gpt-4o": 2000.00,
				},
			},
			"marketing": {
				Limit: 1000.00,
				Hard:  boolPtr(false),
			},
		},
		Users: map[string]config.BudgetLevelConfig{
			"alice": {Limit: 500.00, Period: "daily"},
			"bob":   {Limit: 200.00},
		},
		Keys: map[string]config.BudgetLevelConfig{
			"ci-key": {Limit: 100.00, Period: "daily"},
		},
		Tags: map[string]config.BudgetLevelConfig{
			"project:alpha": {Limit: 2000.00},
		},
	}
}

// --- PeriodStart ---

func TestPeriodStart_Daily(t *testing.T) {
	now := time.Date(2026, 2, 22, 15, 30, 0, 0, time.UTC)
	start := PeriodStart("daily", now)
	expected := time.Date(2026, 2, 22, 0, 0, 0, 0, time.UTC)
	if !start.Equal(expected) {
		t.Errorf("daily start = %v, want %v", start, expected)
	}
}

func TestPeriodStart_Weekly(t *testing.T) {
	// Feb 22, 2026 is a Sunday
	now := time.Date(2026, 2, 22, 15, 30, 0, 0, time.UTC)
	start := PeriodStart("weekly", now)
	expected := time.Date(2026, 2, 16, 0, 0, 0, 0, time.UTC) // Monday
	if !start.Equal(expected) {
		t.Errorf("weekly start = %v, want %v", start, expected)
	}
}

func TestPeriodStart_WeeklyOnMonday(t *testing.T) {
	now := time.Date(2026, 2, 16, 10, 0, 0, 0, time.UTC) // Monday
	start := PeriodStart("weekly", now)
	expected := time.Date(2026, 2, 16, 0, 0, 0, 0, time.UTC)
	if !start.Equal(expected) {
		t.Errorf("weekly start on Monday = %v, want %v", start, expected)
	}
}

func TestPeriodStart_Monthly(t *testing.T) {
	now := time.Date(2026, 2, 22, 15, 30, 0, 0, time.UTC)
	start := PeriodStart("monthly", now)
	expected := time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC)
	if !start.Equal(expected) {
		t.Errorf("monthly start = %v, want %v", start, expected)
	}
}

func TestPeriodStart_Total(t *testing.T) {
	now := time.Date(2026, 2, 22, 15, 30, 0, 0, time.UTC)
	start := PeriodStart("total", now)
	if !start.IsZero() {
		t.Errorf("total start should be zero, got %v", start)
	}
}

func TestPeriodStart_Unknown(t *testing.T) {
	now := time.Date(2026, 2, 22, 15, 30, 0, 0, time.UTC)
	start := PeriodStart("unknown", now)
	expected := time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC) // defaults to monthly
	if !start.Equal(expected) {
		t.Errorf("unknown period start = %v, want %v", start, expected)
	}
}

// --- NewTracker ---

func TestNewTracker_Defaults(t *testing.T) {
	tr := NewTracker(config.BudgetsConfig{})
	if tr.defaultPeriod != "monthly" {
		t.Errorf("default period = %q", tr.defaultPeriod)
	}
	if tr.warnThreshold != 0.8 {
		t.Errorf("warn threshold = %f", tr.warnThreshold)
	}
}

func TestNewTracker_FromConfig(t *testing.T) {
	tr := NewTracker(testConfig())
	if tr.org == nil {
		t.Fatal("org should be set")
	}
	if tr.org.limit != 10000.00 {
		t.Errorf("org limit = %f", tr.org.limit)
	}
	if len(tr.teams) != 2 {
		t.Errorf("teams count = %d", len(tr.teams))
	}
	if len(tr.users) != 2 {
		t.Errorf("users count = %d", len(tr.users))
	}
	if !tr.teams["engineering"].hard {
		t.Error("engineering should be hard cap by default")
	}
	if tr.teams["marketing"].hard {
		t.Error("marketing should be soft cap")
	}
}

// --- CheckBudget ---

func TestCheckBudget_AllUnderLimit(t *testing.T) {
	tr := NewTracker(testConfig())
	result := tr.CheckBudget("engineering", "alice", "", "gpt-4o", nil)
	if !result.Allowed {
		t.Error("should be allowed, no spend yet")
	}
	if result.Remaining < 0 {
		t.Error("remaining should be set")
	}
}

func TestCheckBudget_OrgHardCapExceeded(t *testing.T) {
	tr := NewTracker(testConfig())
	// Record spend up to org limit.
	tr.RecordSpend("", "", "", "gpt-3.5-turbo", nil, 10000.00)
	result := tr.CheckBudget("", "", "", "gpt-3.5-turbo", nil)
	if result.Allowed {
		t.Error("should be blocked by org budget")
	}
	if result.BlockedBy != "org:org" {
		t.Errorf("blocked by = %q", result.BlockedBy)
	}
}

func TestCheckBudget_TeamHardCapExceeded(t *testing.T) {
	tr := NewTracker(testConfig())
	tr.RecordSpend("engineering", "", "", "gpt-3.5-turbo", nil, 5000.00)
	result := tr.CheckBudget("engineering", "", "", "gpt-3.5-turbo", nil)
	if result.Allowed {
		t.Error("should be blocked by team budget")
	}
	if result.BlockedBy != "team:engineering" {
		t.Errorf("blocked by = %q", result.BlockedBy)
	}
}

func TestCheckBudget_TeamSoftCapExceeded(t *testing.T) {
	tr := NewTracker(testConfig())
	tr.RecordSpend("marketing", "", "", "gpt-3.5-turbo", nil, 1000.00)
	result := tr.CheckBudget("marketing", "", "", "gpt-3.5-turbo", nil)
	if !result.Allowed {
		t.Error("marketing is soft cap, should still be allowed")
	}
	if len(result.Warnings) == 0 {
		t.Error("should have warnings")
	}
}

func TestCheckBudget_UserCapExceeded(t *testing.T) {
	tr := NewTracker(testConfig())
	tr.RecordSpend("", "alice", "", "gpt-4o", nil, 500.00)
	result := tr.CheckBudget("", "alice", "", "gpt-4o", nil)
	if result.Allowed {
		t.Error("should be blocked by user budget")
	}
	if result.BlockedBy != "user:alice" {
		t.Errorf("blocked by = %q", result.BlockedBy)
	}
}

func TestCheckBudget_KeyCapExceeded(t *testing.T) {
	tr := NewTracker(testConfig())
	tr.RecordSpend("", "", "ci-key", "gpt-4o", nil, 100.00)
	result := tr.CheckBudget("", "", "ci-key", "gpt-4o", nil)
	if result.Allowed {
		t.Error("should be blocked by key budget")
	}
	if result.BlockedBy != "key:ci-key" {
		t.Errorf("blocked by = %q", result.BlockedBy)
	}
}

func TestCheckBudget_PerModelCapExceeded(t *testing.T) {
	tr := NewTracker(testConfig())
	// Engineering team has $2000 per-model cap on gpt-4o.
	tr.RecordSpend("engineering", "", "", "gpt-4o", nil, 2000.00)
	result := tr.CheckBudget("engineering", "", "", "gpt-4o", nil)
	if result.Allowed {
		t.Error("should be blocked by per-model budget")
	}
	if result.BlockedBy != "team:engineering:model:gpt-4o" {
		t.Errorf("blocked by = %q", result.BlockedBy)
	}
}

func TestCheckBudget_PerModelUnderLimit(t *testing.T) {
	tr := NewTracker(testConfig())
	tr.RecordSpend("engineering", "", "", "gpt-4o", nil, 1500.00)
	result := tr.CheckBudget("engineering", "", "", "gpt-4o", nil)
	if !result.Allowed {
		t.Errorf("should be allowed, under per-model limit: %s", result.BlockMsg)
	}
}

func TestCheckBudget_TagBudget(t *testing.T) {
	tr := NewTracker(testConfig())
	tags := map[string]string{"project": "alpha"}
	tr.RecordSpend("", "", "", "gpt-4o", tags, 2000.00)
	result := tr.CheckBudget("", "", "", "gpt-4o", tags)
	if result.Allowed {
		t.Error("should be blocked by tag budget")
	}
	if result.BlockedBy != "tag:project:alpha" {
		t.Errorf("blocked by = %q", result.BlockedBy)
	}
}

func TestCheckBudget_MultipleWarnings(t *testing.T) {
	tr := NewTracker(testConfig())
	// Spend $4500 on engineering (no user) → team at 90% warns, org at 45% no warn.
	tr.RecordSpend("engineering", "", "", "gpt-3.5-turbo", nil, 4500.00)
	result := tr.CheckBudget("engineering", "", "", "gpt-3.5-turbo", nil)
	if !result.Allowed {
		t.Errorf("should be allowed: %s", result.BlockMsg)
	}
	hasTeamWarn := false
	for _, w := range result.Warnings {
		if w == "team:engineering" {
			hasTeamWarn = true
		}
	}
	if !hasTeamWarn {
		t.Errorf("should have team warning, got %v", result.Warnings)
	}
}

func TestCheckBudget_NoApplicableBudgets(t *testing.T) {
	tr := NewTracker(testConfig())
	result := tr.CheckBudget("unknown-team", "unknown-user", "unknown-key", "gpt-4o", nil)
	if !result.Allowed {
		t.Error("unknown entities should pass (only org budget applies)")
	}
}

func TestCheckBudget_RemainingTracksLowest(t *testing.T) {
	tr := NewTracker(testConfig())
	// alice has $500 daily, org has $10000 monthly.
	tr.RecordSpend("", "alice", "", "gpt-4o", nil, 400.00)
	result := tr.CheckBudget("", "alice", "", "gpt-4o", nil)
	if result.Remaining > 100.01 || result.Remaining < 99.99 {
		t.Errorf("remaining = %f, want ~100.00", result.Remaining)
	}
}

// --- RecordSpend ---

func TestRecordSpend_IncrementsTotals(t *testing.T) {
	tr := NewTracker(testConfig())
	tr.RecordSpend("engineering", "alice", "ci-key", "gpt-4o", nil, 10.50)
	tr.RecordSpend("engineering", "alice", "ci-key", "gpt-4o", nil, 5.25)

	// Check org spend.
	total, pm := tr.GetSpend("org", "org")
	if total < 15.74 || total > 15.76 {
		t.Errorf("org total = %f", total)
	}
	if pm["gpt-4o"] < 15.74 || pm["gpt-4o"] > 15.76 {
		t.Errorf("org gpt-4o = %f", pm["gpt-4o"])
	}

	// Check team spend.
	total, _ = tr.GetSpend("team", "engineering")
	if total < 15.74 || total > 15.76 {
		t.Errorf("team total = %f", total)
	}

	// Check user spend.
	total, _ = tr.GetSpend("user", "alice")
	if total < 15.74 || total > 15.76 {
		t.Errorf("user total = %f", total)
	}
}

func TestRecordSpend_ZeroCostIgnored(t *testing.T) {
	tr := NewTracker(testConfig())
	tr.RecordSpend("engineering", "", "", "gpt-4o", nil, 0)
	tr.RecordSpend("engineering", "", "", "gpt-4o", nil, -5.0)
	total, _ := tr.GetSpend("team", "engineering")
	if total != 0 {
		t.Errorf("zero/negative cost should be ignored, got %f", total)
	}
}

func TestRecordSpend_TagTracking(t *testing.T) {
	tr := NewTracker(testConfig())
	tags := map[string]string{"project": "alpha"}
	tr.RecordSpend("", "", "", "gpt-4o", tags, 50.00)
	total, _ := tr.GetSpend("tag", "project:alpha")
	if total < 49.99 || total > 50.01 {
		t.Errorf("tag spend = %f", total)
	}
}

// --- Period Reset ---

func TestPeriodReset(t *testing.T) {
	cfg := config.BudgetsConfig{
		Enabled:       true,
		DefaultPeriod: "daily",
		WarnThreshold: 0.8,
		Users: map[string]config.BudgetLevelConfig{
			"alice": {Limit: 100.00},
		},
	}
	tr := NewTracker(cfg)
	tr.RecordSpend("", "alice", "", "gpt-4o", nil, 50.00)

	// Manually expire the period by setting periodStart to yesterday.
	lb := tr.users["alice"]
	lb.counter.mu.Lock()
	lb.counter.periodStart = time.Now().UTC().AddDate(0, 0, -2)
	lb.counter.mu.Unlock()

	// Next check should reset.
	result := tr.CheckBudget("", "alice", "", "gpt-4o", nil)
	if !result.Allowed {
		t.Error("should be allowed after period reset")
	}
	total, _ := tr.GetSpend("user", "alice")
	if total != 0 {
		t.Errorf("spend should be 0 after reset, got %f", total)
	}
}

// --- Concurrency ---

func TestConcurrentRecordSpend(t *testing.T) {
	tr := NewTracker(testConfig())
	var wg sync.WaitGroup
	n := 100
	wg.Add(n)
	for i := 0; i < n; i++ {
		go func() {
			defer wg.Done()
			tr.RecordSpend("engineering", "alice", "", "gpt-4o", nil, 1.00)
		}()
	}
	wg.Wait()

	total, _ := tr.GetSpend("user", "alice")
	if total < 99.99 || total > 100.01 {
		t.Errorf("concurrent spend total = %f, want 100.00", total)
	}
}

func TestConcurrentCheckAndRecord(t *testing.T) {
	tr := NewTracker(testConfig())
	var wg sync.WaitGroup
	wg.Add(200)
	for i := 0; i < 100; i++ {
		go func() {
			defer wg.Done()
			tr.RecordSpend("engineering", "alice", "", "gpt-4o", nil, 1.00)
		}()
		go func() {
			defer wg.Done()
			tr.CheckBudget("engineering", "alice", "", "gpt-4o", nil)
		}()
	}
	wg.Wait()
	// Just verify no race/panic.
}

// --- GetSpend ---

func TestGetSpend_UnknownLevel(t *testing.T) {
	tr := NewTracker(testConfig())
	total, pm := tr.GetSpend("unknown", "foo")
	if total != 0 || pm != nil {
		t.Error("unknown level should return zero")
	}
}

// --- formatUSD ---

func TestFormatUSD(t *testing.T) {
	tests := []struct {
		value    float64
		expected string
	}{
		{0, "0.00"},
		{1.5, "1.50"},
		{100.99, "100.99"},
		{10000.00, "10000.00"},
		{0.01, "0.01"},
	}
	for _, tt := range tests {
		got := formatUSD(tt.value)
		if got != tt.expected {
			t.Errorf("formatUSD(%f) = %q, want %q", tt.value, got, tt.expected)
		}
	}
}
