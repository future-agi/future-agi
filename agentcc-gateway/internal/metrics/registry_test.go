package metrics

import (
	"strings"
	"sync"
	"testing"
)

func TestNewRegistry(t *testing.T) {
	r := NewRegistry()
	if r.startTime.IsZero() {
		t.Fatal("startTime should be set")
	}
}

func TestCounterInc(t *testing.T) {
	r := NewRegistry()
	labels := map[string]string{"model": "gpt-4", "provider": "openai"}

	r.CounterInc("agentcc_requests_total", "Total requests", labels)
	r.CounterInc("agentcc_requests_total", "Total requests", labels)
	r.CounterInc("agentcc_requests_total", "Total requests", labels)

	out := r.Render()
	if !strings.Contains(out, `agentcc_requests_total{model="gpt-4",provider="openai"} 3`) {
		t.Fatalf("expected counter value 3, got:\n%s", out)
	}
}

func TestCounterAdd(t *testing.T) {
	r := NewRegistry()
	labels := map[string]string{"model": "gpt-4"}

	r.CounterAdd("agentcc_tokens_input_total", "Total input tokens", labels, 100)
	r.CounterAdd("agentcc_tokens_input_total", "Total input tokens", labels, 250)

	out := r.Render()
	if !strings.Contains(out, `agentcc_tokens_input_total{model="gpt-4"} 350`) {
		t.Fatalf("expected counter value 350, got:\n%s", out)
	}
}

func TestCounterAdd_ZeroIgnored(t *testing.T) {
	r := NewRegistry()
	r.CounterAdd("test_counter", "test", nil, 0)
	r.CounterAdd("test_counter", "test", nil, -5)

	out := r.Render()
	if strings.Contains(out, "test_counter") {
		t.Fatalf("zero/negative adds should not create counter:\n%s", out)
	}
}

func TestCounterNoLabels(t *testing.T) {
	r := NewRegistry()
	r.CounterInc("agentcc_cache_misses_total", "Cache misses", nil)

	out := r.Render()
	if !strings.Contains(out, "agentcc_cache_misses_total 1\n") {
		t.Fatalf("counter without labels should not have braces:\n%s", out)
	}
}

func TestMultipleCounterLabels(t *testing.T) {
	r := NewRegistry()
	r.CounterInc("agentcc_requests_total", "Total requests", map[string]string{"model": "gpt-4", "status": "ok"})
	r.CounterInc("agentcc_requests_total", "Total requests", map[string]string{"model": "gpt-4", "status": "error"})
	r.CounterInc("agentcc_requests_total", "Total requests", map[string]string{"model": "gpt-4", "status": "ok"})

	out := r.Render()
	if !strings.Contains(out, `model="gpt-4",status="ok"} 2`) {
		t.Fatalf("expected ok=2:\n%s", out)
	}
	if !strings.Contains(out, `model="gpt-4",status="error"} 1`) {
		t.Fatalf("expected error=1:\n%s", out)
	}
}

func TestHistogramObserve(t *testing.T) {
	r := NewRegistry()
	labels := map[string]string{"model": "gpt-4"}

	r.HistogramObserve("agentcc_request_duration_ms", "Request latency", labels, 75.0)
	r.HistogramObserve("agentcc_request_duration_ms", "Request latency", labels, 250.0)
	r.HistogramObserve("agentcc_request_duration_ms", "Request latency", labels, 1500.0)

	out := r.Render()

	// 75ms fits in le=100 bucket. Cumulative: le=5→0, le=10→0, le=25→0, le=50→0, le=100→1
	if !strings.Contains(out, `agentcc_request_duration_ms_bucket{model="gpt-4",le="100"} 1`) {
		t.Fatalf("expected 1 in le=100 bucket:\n%s", out)
	}
	// 250ms fits in le=250. Cumulative: le=250 → 1(75)+1(250) = 2
	if !strings.Contains(out, `agentcc_request_duration_ms_bucket{model="gpt-4",le="250"} 2`) {
		t.Fatalf("expected 2 in le=250 bucket:\n%s", out)
	}
	// +Inf should have all 3
	if !strings.Contains(out, `agentcc_request_duration_ms_bucket{model="gpt-4",le="+Inf"} 3`) {
		t.Fatalf("expected 3 in +Inf bucket:\n%s", out)
	}
	// Count = 3
	if !strings.Contains(out, `agentcc_request_duration_ms_count{model="gpt-4"} 3`) {
		t.Fatalf("expected count=3:\n%s", out)
	}
	// Sum = 75 + 250 + 1500 = 1825.0
	if !strings.Contains(out, `agentcc_request_duration_ms_sum{model="gpt-4"} 1825.000`) {
		t.Fatalf("expected sum=1825.000:\n%s", out)
	}
}

func TestHistogramNoLabels(t *testing.T) {
	r := NewRegistry()
	r.HistogramObserve("test_hist", "test", nil, 50.0)

	out := r.Render()
	if !strings.Contains(out, `test_hist_bucket{le="50"} 1`) {
		t.Fatalf("histogram without labels:\n%s", out)
	}
	if !strings.Contains(out, `test_hist_count 1`) {
		t.Fatalf("histogram count without labels:\n%s", out)
	}
}

func TestRender_UptimeGauge(t *testing.T) {
	r := NewRegistry()
	out := r.Render()

	if !strings.Contains(out, "# HELP agentcc_uptime_seconds") {
		t.Fatalf("should contain uptime gauge:\n%s", out)
	}
	if !strings.Contains(out, "# TYPE agentcc_uptime_seconds gauge") {
		t.Fatalf("should contain uptime type:\n%s", out)
	}
	if !strings.Contains(out, "agentcc_uptime_seconds") {
		t.Fatalf("should contain uptime value:\n%s", out)
	}
}

func TestRender_Empty(t *testing.T) {
	r := NewRegistry()
	out := r.Render()

	// Should still have uptime.
	if !strings.Contains(out, "agentcc_uptime_seconds") {
		t.Fatalf("empty registry should still have uptime:\n%s", out)
	}
}

func TestRender_HelpAndType(t *testing.T) {
	r := NewRegistry()
	r.CounterInc("agentcc_requests_total", "Total requests processed", nil)

	out := r.Render()
	if !strings.Contains(out, "# HELP agentcc_requests_total Total requests processed") {
		t.Fatalf("missing HELP line:\n%s", out)
	}
	if !strings.Contains(out, "# TYPE agentcc_requests_total counter") {
		t.Fatalf("missing TYPE line:\n%s", out)
	}
}

func TestRender_SortedCounters(t *testing.T) {
	r := NewRegistry()
	r.CounterInc("zzz_counter", "Z", nil)
	r.CounterInc("aaa_counter", "A", nil)
	r.CounterInc("mmm_counter", "M", nil)

	out := r.Render()
	aPos := strings.Index(out, "aaa_counter")
	mPos := strings.Index(out, "mmm_counter")
	zPos := strings.Index(out, "zzz_counter")

	if aPos > mPos || mPos > zPos {
		t.Fatalf("counters not sorted:\n%s", out)
	}
}

func TestConcurrent_CounterInc(t *testing.T) {
	r := NewRegistry()
	var wg sync.WaitGroup

	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r.CounterInc("agentcc_requests_total", "Total requests", map[string]string{"model": "gpt-4"})
		}()
	}
	wg.Wait()

	out := r.Render()
	if !strings.Contains(out, `agentcc_requests_total{model="gpt-4"} 100`) {
		t.Fatalf("expected 100 after concurrent increments:\n%s", out)
	}
}

func TestConcurrent_HistogramObserve(t *testing.T) {
	r := NewRegistry()
	var wg sync.WaitGroup

	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r.HistogramObserve("agentcc_request_duration_ms", "Latency", nil, 50.0)
		}()
	}
	wg.Wait()

	out := r.Render()
	if !strings.Contains(out, `agentcc_request_duration_ms_count 100`) {
		t.Fatalf("expected count=100 after concurrent observes:\n%s", out)
	}
}

func TestLabelsKey(t *testing.T) {
	// Same labels in different order should produce same key.
	l1 := map[string]string{"b": "2", "a": "1"}
	l2 := map[string]string{"a": "1", "b": "2"}
	if labelsKey(l1) != labelsKey(l2) {
		t.Fatalf("labelsKey should be order-independent: %q vs %q", labelsKey(l1), labelsKey(l2))
	}

	// Empty labels.
	if labelsKey(nil) != "" {
		t.Fatalf("nil labels should be empty string")
	}
}

func TestFormatLabels(t *testing.T) {
	labels := map[string]string{"model": "gpt-4", "provider": "openai"}
	out := formatLabels(labels)
	if out != `model="gpt-4",provider="openai"` {
		t.Fatalf("formatLabels = %q, want model=\"gpt-4\",provider=\"openai\"", out)
	}

	if formatLabels(nil) != "" {
		t.Fatalf("nil labels should be empty")
	}
}
