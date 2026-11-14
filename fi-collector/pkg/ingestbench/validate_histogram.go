package ingestbench

import (
	"fmt"
	"math"
	"sort"
)

// histogramEdgeEqual allows only four binary64 rounding steps at the expected
// edge, not a relative measurement error or a fraction of a bucket. JSON's
// shortest-roundtrip numbers need none; equivalent unit conversions can need a
// few. Even at the 38-hour edge this is < 0.00000012 ms (< 0.001 microseconds).
func histogramEdgeEqual(got, want float64) bool {
	ulp := math.Nextafter(want, math.Inf(1)) - want
	return math.Abs(got-want) <= 4*ulp
}

// validateHistogram reconciles the complete raw population with its summary.
// METHODOLOGY's nearest-rank rule is ceil(n * perMille / 1000), evaluated
// without floating rank rounding or integer overflow. Only two v4 summaries
// carry raw histograms: scheduled read visibility (upper) and durable lag
// (lower). Other Percentiles and all Distribution fields retain their ordering
// checks; their raw populations are not in the receipt and cannot be inferred.
func validateHistogram(path string, buckets []HistogramBucket, summary Percentiles, lowerBound bool) error {
	left := uint64(summary.Count)
	for i, b := range buckets {
		// Locate the canonical edge without converting untrusted milliseconds
		// to an integer (overflow or roundoff can select the wrong bucket).
		// Both edges are exact integer-microsecond divisions, just as emitted;
		// the quantile conversion tolerance does not apply to bucket geometry.
		index := sort.Search(histogramBuckets, func(j int) bool { return latencyLowerEdge(j) >= b.LowerMS })
		if index == histogramBuckets || b.LowerMS != latencyLowerEdge(index) || b.UpperMS != latencyEdge(index) {
			return fmt.Errorf("%s[%d].lower_ms/upper_ms must be a canonical whole-microsecond/log-linear bucket pair", path, i)
		}
		if b.Count <= 0 || b.LowerMS >= b.UpperMS || (i > 0 && b.LowerMS < buckets[i-1].UpperMS) || !consume(&left, uint64(b.Count)) {
			return fmt.Errorf("%s[%d] requires ordered non-overlapping bounds and positive counts within the series total", path, i)
		}
	}
	if left != 0 {
		return fmt.Errorf("%s counts must sum to the percentile count", path)
	}
	if summary.Count == 0 {
		// The numeric/type check also requires all empty-summary values to be 0.
		return nil
	}
	last := buckets[len(buckets)-1]
	// The final log-linear bucket is an overflow bucket: its printed upper
	// edge is nominal, but values >= 2^37 us remain in it. No other bucket is
	// open-ended. Max is exact, not a quantised upper or lower bound.
	overflow := last.LowerMS == latencyLowerEdge(histogramBuckets-1) && last.UpperMS == latencyEdge(histogramBuckets-1)
	if summary.Max < last.LowerMS || (!overflow && summary.Max >= last.UpperMS) {
		return fmt.Errorf("%s max_ms must lie in the final occupied bucket [lower_ms, upper_ms)", path)
	}
	for _, q := range []struct {
		name     string
		perMille int64
		got      float64
	}{{"p50_ms", 500, summary.P50}, {"p95_ms", 950, summary.P95}, {"p99_ms", 990, summary.P99}, {"p999_ms", 999, summary.P999}} {
		rank := summary.Count/1000*q.perMille + (summary.Count%1000*q.perMille+999)/1000
		for _, b := range buckets {
			if rank > b.Count {
				rank -= b.Count
				continue
			}
			want := b.LowerMS
			if !lowerBound {
				want = math.Min(b.UpperMS, summary.Max)
			}
			if !histogramEdgeEqual(q.got, want) {
				return fmt.Errorf("%s %s disagrees with histogram rank: got %.17g, want %.17g", path, q.name, q.got, want)
			}
			break
		}
	}
	return nil
}
