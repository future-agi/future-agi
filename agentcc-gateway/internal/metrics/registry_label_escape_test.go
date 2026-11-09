package metrics

import (
	"strings"
	"testing"
)

// T3 (internal security assessment 1, P3 hardening, RF16): label values were
// written into the Prometheus text output as they came, and the model label
// can be a name the caller chose (or the upstream echoed), so a value holding
// a quote and a newline closed the label set and started a series of its own
// choosing in the gateway's shared, org-less metric namespace. Label values
// are escaped now as the exposition format defines (backslash, double quote,
// line feed), and two different label sets are always two series.

const forgingModel = "gpt-4o\"} 1\nagentcc_forged_total{org=\"victim"

func TestRender_LabelValueCannotForgeASeries(t *testing.T) {
	r := NewRegistry()
	r.CounterInc("agentcc_requests_total", "Total requests processed",
		map[string]string{"model": forgingModel, "provider": "openai"})
	r.HistogramObserve("agentcc_request_duration_ms", "Request duration in milliseconds",
		map[string]string{"model": forgingModel, "provider": "openai"}, 12)

	out := r.Render()
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "agentcc_forged_total") {
			t.Fatalf("a label value started its own series %q:\n%s", line, out)
		}
	}
	want := `agentcc_requests_total{model="gpt-4o\"} 1\nagentcc_forged_total{org=\"victim",provider="openai"} 1`
	if !strings.Contains(out, want+"\n") {
		t.Fatalf("the counter's line is not escaped; want %s in:\n%s", want, out)
	}
	if !strings.Contains(out, `agentcc_request_duration_ms_count{model="gpt-4o\"} 1\nagentcc_forged_total{org=\"victim",provider="openai"} 1`+"\n") {
		t.Fatalf("the histogram's lines are not escaped:\n%s", out)
	}
}

func TestFormatLabels_EscapesBackslashQuoteAndNewline(t *testing.T) {
	got := formatLabels(map[string]string{"model": `a\b"c` + "\nd"})
	if want := `model="a\\b\"c\nd"`; got != want {
		t.Fatalf("formatLabels = %q, want %q", got, want)
	}
}

// A value holding the separators the series key used (a comma and an equals
// sign) must not merge two different label sets into one series.
func TestCounter_DistinctLabelSetsAreDistinctSeries(t *testing.T) {
	r := NewRegistry()
	r.CounterInc("agentcc_requests_total", "h", map[string]string{"model": "x"})
	r.CounterInc("agentcc_requests_total", "h", map[string]string{"model": "x,provider=y"})
	r.CounterInc("agentcc_requests_total", "h", map[string]string{"model": "x", "provider": "y"})

	out := r.Render()
	for _, want := range []string{
		`agentcc_requests_total{model="x"} 1`,
		`agentcc_requests_total{model="x,provider=y"} 1`,
		`agentcc_requests_total{model="x",provider="y"} 1`,
	} {
		if !strings.Contains(out, want+"\n") {
			t.Errorf("missing series %s in:\n%s", want, out)
		}
	}
	if labelsKey(map[string]string{"model": "x,provider=y"}) == labelsKey(map[string]string{"model": "x", "provider": "y"}) {
		t.Error("two different label sets share one series key")
	}
}

// A plain value (the common case) is written exactly as before.
func TestFormatLabels_PlainValuesUnchanged(t *testing.T) {
	if got := formatLabels(map[string]string{"model": "gpt-4", "provider": "openai"}); got != `model="gpt-4",provider="openai"` {
		t.Fatalf("formatLabels = %q", got)
	}
}
