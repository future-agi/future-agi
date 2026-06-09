package alerting

import (
	"sync"
	"testing"
	"time"
)

// captureChannel records alerts for test assertions.
type captureChannel struct {
	mu     sync.Mutex
	alerts []Alert
}

func (c *captureChannel) Send(alert Alert) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.alerts = append(c.alerts, alert)
	return nil
}

func (c *captureChannel) Alerts() []Alert {
	c.mu.Lock()
	defer c.mu.Unlock()
	cp := make([]Alert, len(c.alerts))
	copy(cp, c.alerts)
	return cp
}

func TestWindowCounter_RecordAndSum(t *testing.T) {
	wc := NewWindowCounter(time.Minute, 60)
	wc.Record(5)
	wc.Record(3)
	wc.Record(2)

	sum := wc.Sum()
	if sum != 10 {
		t.Fatalf("Sum() = %f, want 10", sum)
	}
}

func TestWindowCounter_ZeroBuckets(t *testing.T) {
	wc := NewWindowCounter(time.Minute, 60)
	if wc.Sum() != 0 {
		t.Fatalf("empty counter Sum() = %f, want 0", wc.Sum())
	}
}

func TestWindowCounter_DefaultBuckets(t *testing.T) {
	wc := NewWindowCounter(time.Minute, 0)
	if wc.nBuckets != 60 {
		t.Fatalf("nBuckets = %d, want 60", wc.nBuckets)
	}
}

func TestConditionMet(t *testing.T) {
	tests := []struct {
		value     float64
		condition string
		threshold float64
		want      bool
	}{
		{10, ">=", 10, true},
		{11, ">=", 10, true},
		{9, ">=", 10, false},
		{11, ">", 10, true},
		{10, ">", 10, false},
		{10, "<=", 10, true},
		{9, "<=", 10, true},
		{11, "<=", 10, false},
		{9, "<", 10, true},
		{10, "<", 10, false},
		{10, "==", 10, true},
		{11, "==", 10, false},
		{10, "??", 10, false},
	}

	for _, tc := range tests {
		got := conditionMet(tc.value, tc.condition, tc.threshold)
		if got != tc.want {
			t.Errorf("conditionMet(%f, %q, %f) = %v, want %v", tc.value, tc.condition, tc.threshold, got, tc.want)
		}
	}
}

func TestManager_RecordAndEvaluate_Fires(t *testing.T) {
	ch := &captureChannel{}
	rule := NewRule("high-errors", "error_count", ">=", 5, time.Minute, 0, []string{"test"})
	mgr := NewManagerWithChannels([]*Rule{rule}, map[string]Channel{"test": ch})

	// Record enough to trigger.
	for i := 0; i < 5; i++ {
		mgr.Record("error_count", 1)
	}
	mgr.Evaluate()

	alerts := ch.Alerts()
	if len(alerts) != 1 {
		t.Fatalf("expected 1 alert, got %d", len(alerts))
	}
	if alerts[0].Name != "high-errors" {
		t.Fatalf("alert name = %q, want high-errors", alerts[0].Name)
	}
	if alerts[0].Value != 5 {
		t.Fatalf("alert value = %f, want 5", alerts[0].Value)
	}
	if alerts[0].Service != "agentcc-gateway" {
		t.Fatalf("service = %q, want agentcc-gateway", alerts[0].Service)
	}
}

func TestManager_RecordAndEvaluate_NoFire(t *testing.T) {
	ch := &captureChannel{}
	rule := NewRule("high-errors", "error_count", ">=", 10, time.Minute, 0, []string{"test"})
	mgr := NewManagerWithChannels([]*Rule{rule}, map[string]Channel{"test": ch})

	mgr.Record("error_count", 5)
	mgr.Evaluate()

	if len(ch.Alerts()) != 0 {
		t.Fatal("should not fire when below threshold")
	}
}

func TestManager_Cooldown(t *testing.T) {
	ch := &captureChannel{}
	rule := NewRule("test", "error_count", ">=", 1, time.Minute, 10*time.Second, []string{"test"})
	mgr := NewManagerWithChannels([]*Rule{rule}, map[string]Channel{"test": ch})

	mgr.Record("error_count", 5)

	// First evaluation fires.
	mgr.Evaluate()
	if len(ch.Alerts()) != 1 {
		t.Fatalf("expected 1 alert, got %d", len(ch.Alerts()))
	}

	// Second evaluation within cooldown — should NOT fire again.
	mgr.Evaluate()
	if len(ch.Alerts()) != 1 {
		t.Fatalf("expected still 1 alert (cooldown), got %d", len(ch.Alerts()))
	}
}

func TestManager_CooldownExpires(t *testing.T) {
	ch := &captureChannel{}
	// Use tiny cooldown for test.
	rule := NewRule("test", "error_count", ">=", 1, time.Minute, 10*time.Millisecond, []string{"test"})
	mgr := NewManagerWithChannels([]*Rule{rule}, map[string]Channel{"test": ch})

	mgr.Record("error_count", 5)

	mgr.Evaluate()
	if len(ch.Alerts()) != 1 {
		t.Fatalf("expected 1 alert, got %d", len(ch.Alerts()))
	}

	// Wait for cooldown to expire.
	time.Sleep(20 * time.Millisecond)
	mgr.Evaluate()
	if len(ch.Alerts()) != 2 {
		t.Fatalf("expected 2 alerts after cooldown, got %d", len(ch.Alerts()))
	}
}

func TestManager_MultipleRules(t *testing.T) {
	ch := &captureChannel{}
	rules := []*Rule{
		NewRule("errors", "error_count", ">=", 5, time.Minute, 0, []string{"test"}),
		NewRule("cost", "cost_total", ">=", 100, time.Minute, 0, []string{"test"}),
	}
	mgr := NewManagerWithChannels(rules, map[string]Channel{"test": ch})

	mgr.Record("error_count", 10)
	mgr.Record("cost_total", 50) // Below threshold.
	mgr.Evaluate()

	alerts := ch.Alerts()
	if len(alerts) != 1 {
		t.Fatalf("expected 1 alert (errors only), got %d", len(alerts))
	}
	if alerts[0].Name != "errors" {
		t.Fatalf("wrong alert fired: %q", alerts[0].Name)
	}
}

func TestManager_MultipleChannels(t *testing.T) {
	ch1 := &captureChannel{}
	ch2 := &captureChannel{}
	rule := NewRule("test", "error_count", ">=", 1, time.Minute, 0, []string{"ch1", "ch2"})
	mgr := NewManagerWithChannels([]*Rule{rule}, map[string]Channel{"ch1": ch1, "ch2": ch2})

	mgr.Record("error_count", 5)
	mgr.Evaluate()

	if len(ch1.Alerts()) != 1 {
		t.Fatalf("ch1: expected 1 alert, got %d", len(ch1.Alerts()))
	}
	if len(ch2.Alerts()) != 1 {
		t.Fatalf("ch2: expected 1 alert, got %d", len(ch2.Alerts()))
	}
}

func TestManager_UnknownChannel(t *testing.T) {
	rule := NewRule("test", "error_count", ">=", 1, time.Minute, 0, []string{"nonexistent"})
	mgr := NewManagerWithChannels([]*Rule{rule}, map[string]Channel{})

	mgr.Record("error_count", 5)
	// Should not panic — just logs warning.
	mgr.Evaluate()
}

func TestManager_MetricMismatch(t *testing.T) {
	ch := &captureChannel{}
	rule := NewRule("test", "error_count", ">=", 1, time.Minute, 0, []string{"test"})
	mgr := NewManagerWithChannels([]*Rule{rule}, map[string]Channel{"test": ch})

	// Record a different metric.
	mgr.Record("cost_total", 100)
	mgr.Evaluate()

	if len(ch.Alerts()) != 0 {
		t.Fatal("should not fire for mismatched metric")
	}
}

func TestManager_RuleCount(t *testing.T) {
	rules := []*Rule{
		NewRule("a", "m", ">=", 1, time.Minute, 0, nil),
		NewRule("b", "m", ">=", 1, time.Minute, 0, nil),
	}
	mgr := NewManagerWithChannels(rules, nil)
	if mgr.RuleCount() != 2 {
		t.Fatalf("RuleCount() = %d, want 2", mgr.RuleCount())
	}
}

func TestLogChannel_Send(t *testing.T) {
	ch := &LogChannel{}
	alert := Alert{Name: "test", Metric: "error_count", Value: 10}
	if err := ch.Send(alert); err != nil {
		t.Fatalf("LogChannel.Send error: %v", err)
	}
}

func TestWindowCounter_Concurrent(t *testing.T) {
	wc := NewWindowCounter(time.Minute, 60)
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			wc.Record(1)
		}()
	}
	wg.Wait()

	sum := wc.Sum()
	if sum != 100 {
		t.Fatalf("Sum() = %f, want 100 after concurrent writes", sum)
	}
}
