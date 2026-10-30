package ingestbench

import (
	"bytes"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func retainedResult(t *testing.T, name string) Result {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", "round2-"+name+".json"))
	if err != nil {
		t.Fatal(err)
	}
	var r Result
	if err := json.Unmarshal(b, &r); err != nil {
		t.Fatal(err)
	}
	return r
}

// Change one observed sentinel into an unseen or non-acknowledged sentinel.
// Copy slice storage because headline tests start from a shared value receipt.
func removeSeenSentinel(r *Result, unseen bool) {
	l := &r.Lag
	l.Latency.Count--
	l.FromScheduled.Count--
	l.LowerFromScheduled.Count--
	l.Windows = append([]LagWindow(nil), l.Windows...)
	l.Windows[0].Seen--
	l.Windows[0].Upper.Count--
	l.Buckets = append([]HistogramBucket(nil), l.Buckets...)
	l.Buckets[0].Count--
	if l.Buckets[0].Count == 0 {
		l.Buckets = l.Buckets[1:]
	}
	if unseen {
		l.Unseen++
		l.Windows[0].Unseen++
	} else {
		l.Sampled--
		l.DroppedNotAcknowledged++
	}
}

func TestResultRetainedIntegrityGaps(t *testing.T) {
	for _, tc := range []struct{ name, field string }{
		{"sentinel-conservation", "lag.sampled_spans"},
		{"verified-chunks-zero", "verification.verified_chunks"},
		{"response-total-off-one", "response_statuses"},
		{"impossible-percentile-order", "otlp_request_latency"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := retainedResult(t, "confirmed-"+tc.name)
			var b bytes.Buffer
			if err := Report(&b, r); err == nil || !strings.Contains(err.Error(), tc.field) {
				t.Errorf("Report: want %s rejection, got %v", tc.field, err)
			}
			if b.Len() != 0 {
				t.Error("invalid evidence emitted report bytes")
			}
			if p, err := Publish(r, PublishOptions{MinSustained: 500 * time.Millisecond}); err == nil || !strings.Contains(err.Error(), tc.field) {
				t.Errorf("Publish: want %s rejection, got %v; headline %s", tc.field, err, p.Headline.Status)
			}
		})
	}
	for _, name := range []string{"exact-head-mock", "confirmed-sentinel-consistent-unseen"} {
		t.Run(name, func(t *testing.T) {
			r := retainedResult(t, name)
			if name == "confirmed-sentinel-consistent-unseen" {
				// Round 2 removed the median bucket without recomputing p50.
				// Preserve that receipt as a rejection regression; the positive
				// twin instead removes the fastest sample, retaining the median
				// bucket and the same two-sample summary. Unseen stays diagnostic.
				var out bytes.Buffer
				if err := Report(&out, r); err == nil || out.Len() != 0 {
					t.Fatal("stale unseen-sentinel histogram accepted")
				}
				mock := retainedResult(t, "exact-head-mock")
				r.Lag.Buckets = append([]HistogramBucket(nil), mock.Lag.Buckets[1:]...)
			}
			if err := Report(&bytes.Buffer{}, r); err != nil {
				t.Fatal(err)
			}
			p, err := Publish(r, PublishOptions{})
			if err != nil || p.Headline.Status != "withheld" {
				t.Fatalf("diagnostic: %v %+v", err, p.Headline)
			}
		})
	}
}

// All percentile/distribution occurrences, including optional probes and recovery
// metadata, use the same type contract. This intentionally discovers new fields.
func TestResultSummaryTypes(t *testing.T) {
	types := map[reflect.Type]bool{}
	var walk func(reflect.Type)
	walk = func(v reflect.Type) {
		if types[v] {
			return
		}
		types[v] = true
		switch v.Kind() {
		case reflect.Pointer, reflect.Slice, reflect.Array, reflect.Map:
			walk(v.Elem())
		case reflect.Struct:
			for i := 0; i < v.NumField(); i++ {
				if v.Field(i).PkgPath == "" {
					walk(v.Field(i).Type)
				}
			}
		}
	}
	walk(reflect.TypeOf(Result{}))
	if !types[reflect.TypeOf(Percentiles{})] || !types[reflect.TypeOf(Distribution{})] {
		t.Fatal("summary type inventory empty")
	}
	for _, tc := range []struct {
		name   string
		mutate func(*Result)
	}{
		{"percentile-empty", func(r *Result) { r.RequestLatency = Percentiles{Max: 1} }},
		{"percentile-nan", func(r *Result) { r.RequestLatency.P50 = math.NaN() }},
		{"percentile-inf", func(r *Result) { r.RequestLatency.P50 = math.Inf(1) }},
		{"distribution-order", func(r *Result) { r.Workload.InputBytes.P50 = r.Workload.InputBytes.Max + 1 }},
		{"distribution-mean", func(r *Result) { r.Workload.InputBytes.Mean = float64(r.Workload.InputBytes.Max) + 1 }},
		{"sentinel-overflow", func(r *Result) { r.Lag.FromScheduled.Count = math.MaxInt64; r.Lag.Unseen = math.MaxInt }},
		{"sentinel-series", func(r *Result) { r.Lag.Latency.Count++ }},
		{"sentinel-window", func(r *Result) { r.Lag.Windows[0].Seen++ }},
		{"sentinel-bucket", func(r *Result) { r.Lag.Buckets[0].Count++ }},
		{"sentinel-bounds", func(r *Result) { r.Lag.LowerFromScheduled.P50 = r.Lag.FromScheduled.Max + 1 }},
		{"chunk-size", func(r *Result) { r.Verification.ChunkSpans = 0 }},
		{"request-latency-count", func(r *Result) { r.RequestLatency.Count++ }},
		{"request-http-outcome", func(r *Result) { r.Statuses = map[string]int64{"http:429": r.Counters.Requests} }},
		{"retry-count", func(r *Result) { r.RetryHints.Requests++ }},
		{"bytes-without-requests", func(r *Result) { *r = newResult(r.Config); r.Counters.Bytes = 1 }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := retainedResult(t, "exact-head-mock")
			tc.mutate(&r)
			if err := ValidateResult(r); err == nil {
				t.Fatal("corrupt summary accepted")
			}
		})
	}
}
