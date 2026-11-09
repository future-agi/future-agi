package prometheus

import (
	"context"
	"fmt"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/metrics"
	"github.com/futureagi/agentcc-gateway/internal/providers/admission"
)

var saturationSeq atomic.Int64

// TestProviderSaturationMetrics: an enabled plugin exports every provider
// entry's slots: in flight, max_concurrent, queued, saturated, busy refusals
// and the queue wait histogram.
func TestProviderSaturationMetrics(t *testing.T) {
	entry := fmt.Sprintf("prom-sat-%d", saturationSeq.Add(1))
	l := admission.New(entry, 1, 20*time.Millisecond)
	defer l.Close()
	if err := l.Acquire(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer l.Release()
	if err := l.Acquire(context.Background()); err == nil {
		t.Fatal("a full entry gave a second slot")
	}

	reg := metrics.NewRegistry()
	New(reg, true)
	out := reg.Render()
	lbl := `{provider="` + entry + `"}`
	for _, want := range []string{
		MetricProviderInFlight + lbl + " 1\n",
		MetricProviderMaxConcurrent + lbl + " 1\n",
		MetricProviderQueued + lbl + " 0\n",
		MetricProviderSaturated + lbl + " 1\n",
		MetricProviderBusyTotal + lbl + " 1\n",
		MetricProviderQueueWait + `_bucket{le="+Inf",provider="` + entry + `"} 2` + "\n",
		MetricProviderQueueWait + `_count` + lbl + " 2\n",
		"# TYPE " + MetricProviderBusyTotal + " counter\n",
		"# TYPE " + MetricProviderQueueWait + " histogram\n",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("metrics lack %q", want)
		}
	}

	off := metrics.NewRegistry()
	New(off, false)
	if strings.Contains(off.Render(), MetricProviderInFlight) {
		t.Error("a disabled plugin exports provider saturation")
	}
}
