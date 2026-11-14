package ingestbench

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

// receiptOracle is deliberately test-only. It does not call ValidateResult,
// accounting/quantile helpers, or publication gates. These identities are
// independently transcribed from METHODOLOGY: five stages, outcome classes,
// read-visible sentinels, latency figures, offline evidence validation.
// big.Rat preserves all integer arithmetic, including uint64, in the oracle.
func receiptOracle(r Result) bool {
	raw, err := json.Marshal(r)
	if err != nil {
		return false
	}
	var doc map[string]any
	d := json.NewDecoder(strings.NewReader(string(raw)))
	d.UseNumber()
	if d.Decode(&doc) != nil {
		return false
	}
	var node func(any) bool
	node = func(x any) bool {
		switch x := x.(type) {
		case map[string]any:
			if _, ok := x["p50_ms"]; ok {
				last := 0.0
				for _, key := range []string{"p50_ms", "p95_ms", "p99_ms", "p999_ms", "max_ms"} {
					v, _ := x[key].(json.Number).Float64()
					if v < last {
						return false
					}
					last = v
				}
				if x["count"] == json.Number("0") && last != 0 {
					return false
				}
			}
			if _, ok := x["p50_upper_bound"]; ok {
				last, _ := x["min"].(json.Number).Float64()
				for _, key := range []string{"p50_upper_bound", "p95_upper_bound", "p99_upper_bound", "max"} {
					v, _ := x[key].(json.Number).Float64()
					if v < last {
						return false
					}
					last = v
				}
				mean, _ := x["mean"].(json.Number).Float64()
				lo, _ := new(big.Rat).SetString(string(x["min"].(json.Number)))
				hi, _ := new(big.Rat).SetString(string(x["max"].(json.Number)))
				exactMean := new(big.Rat).SetFloat64(mean)
				if exactMean == nil || exactMean.Cmp(lo) < 0 || exactMean.Cmp(hi) > 0 {
					return false
				}
				if x["count"] == json.Number("0") && (last != 0 || mean != 0) {
					return false
				}
			}
			for key, v := range x {
				if n, ok := v.(json.Number); ok {
					f, e := n.Float64()
					signed := key == "seed" || key == "server_minus_generator_ms" || key == "clock_offset_applied_ms" || key == "buffered_rows_slope_rows_per_second" || key == "second_half_slope_rows_per_second"
					if e != nil || math.IsNaN(f) || math.IsInf(f, 0) || (!signed && f < 0) {
						return false
					}
				}
				if !node(v) {
					return false
				}
			}
		case []any:
			for _, v := range x {
				if !node(v) {
					return false
				}
			}
		}
		return true
	}
	if !node(doc) {
		return false
	}
	get := func(path string) *big.Rat {
		var v any = doc
		for _, key := range strings.Split(path, ".") {
			m, ok := v.(map[string]any)
			if !ok {
				return new(big.Rat)
			}
			v = m[key]
		}
		n, ok := v.(json.Number)
		if !ok {
			return new(big.Rat)
		}
		q, _ := new(big.Rat).SetString(string(n))
		return q
	}
	eq := func(total string, terms ...string) bool {
		sum := new(big.Rat)
		for _, p := range terms {
			sum.Add(sum, get(p))
		}
		return sum.Cmp(get(total)) == 0
	}
	for _, row := range [][]string{
		{"counters.attempted_spans", "counters.acknowledged_spans", "counters.otlp_partial_rejected_spans", "counters.whole_request_rejected_spans", "counters.ambiguous_spans"},
		{"counters.accepted_spans", "counters.acknowledged_spans"},
		{"counters.whole_request_rejected_spans", "counters.whole_request_rejected_retryable_spans", "counters.whole_request_rejected_non_retryable_spans"},
		{"counters.unacknowledged_spans", "counters.whole_request_rejected_spans", "counters.ambiguous_spans"},
		{"counters.requests", "counters.request_outcomes.acknowledged", "counters.request_outcomes.partial_rejection", "counters.request_outcomes.whole_request_rejected_retryable", "counters.request_outcomes.whole_request_rejected_non_retryable", "counters.request_outcomes.ambiguous"},
		{"counters.request_errors", "counters.request_outcomes.whole_request_rejected_retryable", "counters.request_outcomes.whole_request_rejected_non_retryable", "counters.request_outcomes.ambiguous"},
		{"schedule.scheduled_spans", "counters.attempted_spans", "schedule.scheduled_but_not_started_spans"},
		{"counters.attempted_spans", "verification.persisted_distinct_attempted_spans", "verification.missing_attempted_spans"},
		{"counters.attempted_spans", "verification.scanned_attempted_spans", "verification.unscanned_attempted_spans"},
		{"verification.scanned_run_rows", "verification.persisted_distinct_attempted_spans", "verification.duplicate_expected_rows_visible", "verification.unexpected_rows"},
		{"verification.missing_attempted_spans", "verification.missing_by_acknowledgement.acknowledged_but_missing_spans", "verification.missing_by_acknowledgement.partial_rejected_and_missing_spans", "verification.missing_by_acknowledgement.whole_request_rejected_and_missing_spans", "verification.missing_by_acknowledgement.ambiguous_and_missing_spans", "verification.unscanned_attempted_spans"},
		{"lag.registered_sentinels", "lag.sampled_spans", "lag.dropped_not_acknowledged_sentinels"},
		{"lag.sampled_spans", "lag.read_visible_lag_upper_bound_from_scheduled_send.count", "lag.unseen_sampled_spans"},
		{"lag.visibility_lag_upper_bound.count", "lag.read_visible_lag_upper_bound_from_scheduled_send.count"},
		{"lag.read_visible_lag_lower_bound_from_scheduled_send.count", "lag.read_visible_lag_upper_bound_from_scheduled_send.count"},
		{"retry_hints.requests_with_retry_hint", "counters.request_outcomes.whole_request_rejected_with_retry_hint"},
		{"retry_hints.requested_delay.count", "retry_hints.requests_with_retry_hint"},
		{"verification.durable_lag.stored_spans_measured", "verification.durable_lag.insert_lag_from_scheduled_send.count", "verification.durable_lag.spans_inserted_before_scheduled_send"},
	} {
		if !eq(row[0], row[1:]...) {
			return false
		}
	}
	c, k, v, l := r.Config, r.Counters, r.Verification, r.Lag
	if c.Rate <= 0 || c.BatchSize <= 0 || c.Duration <= 0 {
		return false
	}
	planned := new(big.Int).Mul(big.NewInt(c.Rate), big.NewInt(int64(c.Duration)))
	planned.Quo(planned, big.NewInt(1e9))
	if !planned.IsInt64() || planned.Int64() != r.PlannedSpans {
		return false
	}
	if k.Attempted > k.Generated || k.Generated > r.PlannedSpans || r.Schedule.ScheduledSpans > r.PlannedSpans || k.Requests != (k.Attempted+int64(c.BatchSize)-1)/int64(c.BatchSize) {
		return false
	}
	if r.Status == "measured-profile-complete" && r.LoadSeconds < c.Duration.Seconds() {
		return false
	}
	if v.Complete && (v.Chunks != v.TotalChunks || v.Unscanned != 0 || (k.Attempted > 0 && (v.Chunks <= 0 || v.ChunkSpans <= 0))) {
		return false
	}
	if v.ChunkSpans > 0 && v.Complete {
		chunks := k.Attempted / v.ChunkSpans
		if k.Attempted%v.ChunkSpans != 0 {
			chunks++
		}
		if v.TotalChunks != chunks {
			return false
		}
	}
	if v.OutsideNamespace > v.UnexpectedRows || v.DistinctExpected > v.ScannedAttempted {
		return false
	}
	total := new(big.Int)
	for _, n := range r.Statuses {
		total.Add(total, big.NewInt(n))
	}
	if total.Cmp(big.NewInt(k.Requests)) != 0 {
		return false
	}
	if r.Schedule.StartDelay.Count != k.Requests || (r.Workload.Spans > 0 && r.Workload.DispatchDelay.Count != r.Schedule.StartDelay.Count) {
		return false
	}
	if r.RequestLatency.Count != k.Requests || r.RequestLatencyFromScheduled.Count != k.Requests {
		return false
	}
	if k.Requests == 0 && (k.Bytes != 0 || k.UncompressedBytes != 0) {
		return false
	}
	if r.RetryHints.Requests > k.RequestOutcomes.RejectedRetryable {
		return false
	}
	if l.Sampled > int(k.RequestOutcomes.Acknowledged) || l.Registered > k.Requests {
		return false
	}
	seen, unseen := new(big.Int), new(big.Int)
	for i, w := range l.Windows {
		if w.EndS <= w.StartS || (i > 0 && w.StartS < l.Windows[i-1].EndS) || w.Seen != w.Upper.Count {
			return false
		}
		seen.Add(seen, big.NewInt(w.Seen))
		unseen.Add(unseen, big.NewInt(w.Unseen))
	}
	if len(l.Windows) > 0 && (seen.Cmp(big.NewInt(l.FromScheduled.Count)) != 0 || unseen.Cmp(big.NewInt(int64(l.Unseen))) != 0) {
		return false
	}
	for _, pair := range []struct {
		b []HistogramBucket
		n int64
	}{{l.Buckets, l.FromScheduled.Count}, {v.DurableLag.Buckets, v.DurableLag.Lag.Count}} {
		sum := new(big.Int)
		for i, b := range pair.b {
			if b.Count <= 0 || b.UpperMS <= b.LowerMS || (i > 0 && b.LowerMS < pair.b[i-1].UpperMS) {
				return false
			}
			sum.Add(sum, big.NewInt(b.Count))
		}
		if len(pair.b) > 0 && sum.Cmp(big.NewInt(pair.n)) != 0 {
			return false
		}
	}
	for _, pair := range rawHistogramPairs(&r) {
		if !histogramOracle(*pair.buckets, *pair.summary, pair.lower) {
			return false
		}
	}
	for _, pair := range [][2]Percentiles{{l.LowerFromScheduled, l.FromScheduled}, {l.Latency, l.FromScheduled}, {r.RequestLatency, r.RequestLatencyFromScheduled}} {
		a, b := pair[0], pair[1]
		if a.P50 > b.P50 || a.P95 > b.P95 || a.P99 > b.P99 || a.P999 > b.P999 || a.Max > b.Max {
			return false
		}
	}
	return true
}

// visitNumbers traverses exported numeric leaves, including maps, nil pointers
// and empty collection element types. The temporary materialization is undone
// after each branch. time.Time's private implementation and opaque JSON bytes
// are not schema numeric leaves.
func visitNumbers(v reflect.Value, path string, commit func(), visit func(reflect.Value, string, func())) {
	if v.Type() == reflect.TypeOf(time.Time{}) || v.Type() == reflect.TypeOf(json.RawMessage{}) {
		return
	}
	switch v.Kind() {
	case reflect.Interface:
		if !v.IsNil() {
			x := reflect.New(v.Elem().Type()).Elem()
			x.Set(v.Elem())
			visitNumbers(x, path, func() { v.Set(x); commit() }, visit)
		}
	case reflect.Pointer:
		wasNil := v.IsNil()
		if wasNil {
			v.Set(reflect.New(v.Type().Elem()))
		}
		visitNumbers(v.Elem(), path, commit, visit)
		if wasNil {
			v.SetZero()
		}
	case reflect.Struct:
		for i := 0; i < v.NumField(); i++ {
			f := v.Type().Field(i)
			tag := strings.Split(f.Tag.Get("json"), ",")[0]
			if f.PkgPath == "" && tag != "-" && tag != "" {
				visitNumbers(v.Field(i), path+"."+tag, commit, visit)
			}
		}
	case reflect.Slice, reflect.Array:
		wasEmpty := v.Kind() == reflect.Slice && v.Len() == 0
		old := reflect.New(v.Type()).Elem()
		old.Set(v)
		if wasEmpty {
			v.Set(reflect.MakeSlice(v.Type(), 1, 1))
		}
		for i := 0; i < v.Len(); i++ {
			visitNumbers(v.Index(i), fmt.Sprintf("%s[%d]", path, i), commit, visit)
		}
		if wasEmpty {
			v.Set(old)
		}
	case reflect.Map:
		old := reflect.New(v.Type()).Elem()
		old.Set(v)
		if v.IsNil() {
			v.Set(reflect.MakeMap(v.Type()))
		}
		keys := v.MapKeys()
		empty := len(keys) == 0
		if empty && v.Type().Elem().Kind() != reflect.Interface {
			k := reflect.New(v.Type().Key()).Elem()
			if k.Kind() == reflect.String {
				k.SetString("test")
			}
			keys = append(keys, k)
			v.SetMapIndex(k, reflect.Zero(v.Type().Elem()))
		}
		for _, k := range keys {
			x := reflect.New(v.Type().Elem()).Elem()
			x.Set(v.MapIndex(k))
			visitNumbers(x, fmt.Sprintf("%s[%v]", path, k.Interface()), func() { v.SetMapIndex(k, x); commit() }, visit)
			v.SetMapIndex(k, x)
		}
		if empty {
			v.Set(old)
		}
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64, reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64, reflect.Float32, reflect.Float64:
		visit(v, path, commit)
	}
}

func TestResultNumericMetamorphic(t *testing.T) {
	var total, accepted, headlined, leaves, pairs int
	// The independently assembled seed includes full nonzero core ledgers.
	made := repairReceipt()
	made.Statuses = map[string]int64{"http:200": 3}
	made.RequestLatency = Percentiles{Count: 3, P50: 1, P95: 1, P99: 1, P999: 1, Max: 1}
	made.RequestLatencyFromScheduled = made.RequestLatency
	seeds := []Result{retainedResult(t, "exact-head-mock"), made}
	// Optional export feeds the identical Go-generated corpus to a binary replay.
	var export *os.File
	if path := os.Getenv("INGESTBENCH_CORPUS_OUT"); path != "" {
		var err error
		export, err = os.Create(path)
		if err != nil {
			t.Fatal(err)
		}
		defer export.Close()
	}
	check := func(r Result, label string) {
		total++
		err := ValidateResult(r)
		p, pe := Publish(r, PublishOptions{MinSustained: time.Nanosecond})
		if err == nil {
			accepted++
			if !receiptOracle(r) {
				t.Errorf("accepted outside independent oracle (even without headline): %s", label)
			}
		}
		if err == nil && pe == nil && p.Headline.Status == "published" {
			headlined++
			if !receiptOracle(r) {
				t.Errorf("accepted and headlined outside independent oracle: %s", label)
			}
		}
		if export != nil {
			if b, e := json.Marshal(struct {
				Name   string `json:"name"`
				Result Result `json:"result"`
				Oracle bool   `json:"oracle"`
			}{label, r, receiptOracle(r)}); e == nil {
				export.Write(append(b, '\n'))
			}
		}
	}
	for seed, original := range seeds {
		if !receiptOracle(original) {
			t.Fatalf("independent seed %d invalid", seed)
		}
		if err := ValidateResult(original); err != nil {
			t.Fatalf("product seed %d invalid: %v", seed, err)
		}
		raw, _ := json.Marshal(original)
		var r Result
		json.Unmarshal(raw, &r)
		check(r, fmt.Sprintf("seed-%d", seed))
		histogramMutations(original, func(r Result, label string) { check(r, fmt.Sprintf("seed-%d/%s", seed, label)) })
		visitNumbers(reflect.ValueOf(&r).Elem(), "result", func() {}, func(v reflect.Value, path string, commit func()) {
			leaves++
			old := reflect.New(v.Type()).Elem()
			old.Set(v)
			for op := 0; op < 10; op++ {
				v.Set(old)
				switch v.Kind() {
				case reflect.Float32, reflect.Float64:
					n := old.Float()
					vals := []float64{n + 1, n - 1, n * 2, 0, -1, float64(math.MaxInt64), math.NaN(), math.Inf(1), math.Nextafter(n, math.Inf(-1)), math.Nextafter(n, math.Inf(1))}
					v.SetFloat(vals[op])
				case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
					if op >= 6 {
						continue
					}
					n := old.Int()
					v.SetInt([]int64{n + 1, n - 1, n * 2, 0, -1, math.MaxInt64}[op])
				default:
					if op >= 6 {
						continue
					}
					n := old.Uint()
					v.SetUint([]uint64{n + 1, n - 1, n * 2, 0, math.MaxUint64, math.MaxInt64}[op])
				}
				commit()
				label := fmt.Sprintf("seed-%d/%s/op-%d", seed, path, op)
				check(r, label)
				if export != nil && (v.Kind() == reflect.Float64 || v.Kind() == reflect.Float32) && (math.IsNaN(v.Float()) || math.IsInf(v.Float(), 0)) {
					// Preserve the non-JSON numeric cases too. Python's stdlib
					// decoder can carry these to the strict built-CLI decoder,
					// which must reject them before creating output files.
					token := "NaN"
					if math.IsInf(v.Float(), 0) {
						token = "Infinity"
					}
					marker := float64(1.23456789012345e30)
					v.SetFloat(marker)
					commit()
					b, err := json.Marshal(r)
					markerJSON, _ := json.Marshal(v.Float())
					if err != nil || bytes.Count(b, markerJSON) != 1 {
						t.Fatalf("cannot export nonfinite case %s: %v", label, err)
					}
					b = bytes.Replace(b, markerJSON, []byte(token), 1)
					name, _ := json.Marshal(label)
					if _, err := fmt.Fprintf(export, "{\"name\":%s,\"result\":%s,\"oracle\":false}\n", name, b); err != nil {
						t.Fatal(err)
					}
				}
				v.Set(old)
				commit()
			}
		})
		// Related pairs: same movement can preserve an identity (rows/duplicates),
		// or break a third ledger (sampled/seen). Neither is assumed invalid a priori.
		for name, mutate := range map[string]func(*Result){
			"rows-duplicates":    func(r *Result) { r.Verification.Rows++; r.Verification.DuplicateRows++ },
			"requests-responses": func(r *Result) { r.Counters.Requests++; r.Statuses["http:200"]++ },
			"sampled-seen":       func(r *Result) { r.Lag.Sampled++; r.Lag.FromScheduled.Count++ },
			"chunks-planned":     func(r *Result) { r.Verification.Chunks++; r.Verification.TotalChunks++ },
			"wire-uncompressed":  func(r *Result) { r.Counters.Bytes++; r.Counters.UncompressedBytes++ },
			"quantile-p50-p95":   func(r *Result) { r.RequestLatency.P50++; r.RequestLatency.P95++ },
		} {
			json.Unmarshal(raw, &r)
			mutate(&r)
			pairs++
			check(r, fmt.Sprintf("seed-%d/pair/%s", seed, name))
		}
	}
	retainedHistogramNeighbourhoods(t, check)
	t.Logf("metamorphic cases=%d numeric_leaves=%d related_pairs=%d accepted=%d headlined=%d", total, leaves, pairs, accepted, headlined)
	if leaves < 300 || total < 2000 || headlined < 2 {
		t.Fatal("vacuous corpus")
	}
}
