package ingestbench

import (
	"bytes"
	"strings"
	"testing"
	"time"
)

// F3 applies to the human-readable publication as well as JSON: rounding a
// 1.536 ms lower bound to 2 ms can put it above a true latency of 1.537999 ms.
func TestLatencyRenderingPreservesHistogramBounds(t *testing.T) {
	lower := histogram{lowerBound: true}
	lower.add(1537*time.Microsecond + 999*time.Nanosecond)
	p := lower.summary()
	if got := pct(p); !strings.Contains(got, "1.536 / 1.536 / 1.536 / 1.536 / 1.537999 ms") {
		t.Fatalf("publication rounds a lower bound upward: %s", got)
	}
	r := repairReceipt()
	upper := lower
	upper.lowerBound = false
	lag, durable, lowerLag := upper.summary(), p, p
	lag.Count, durable.Count, lowerLag.Count = 3, 12, 3
	r.Lag.Latency, r.Lag.FromScheduled, r.Lag.LowerFromScheduled = lag, lag, lowerLag
	r.Lag.Windows = []LagWindow{{EndS: 10, Seen: 3, Upper: lag}}
	r.Lag.Buckets = lower.buckets()
	r.Lag.Buckets[0].Count = 3
	durableBuckets := lower.buckets()
	durableBuckets[0].Count = 12
	r.Verification.DurableLag = DurableLagResult{Status: "measured", Rows: 12, Lag: durable, Buckets: durableBuckets}
	r.DirectReads = []DirectReadResult{{Name: "trace-by-id", Status: "window-censored", Censored: 1, CensoredElapsed: p}}
	var text bytes.Buffer
	if err := Report(&text, r); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"lower bound p50/p99 1.536/1.536 ms",
		"p50/p95/p99/p99.9/max 1.536/1.536/1.536/1.536/1.537999 ms",
		"at least p50/max 1.536/1.537999 ms",
	} {
		if !strings.Contains(text.String(), want) {
			t.Fatalf("report rounds the stored bound; missing %q", want)
		}
	}
}
