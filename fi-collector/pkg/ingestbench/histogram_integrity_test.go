package ingestbench

import (
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// histogramOracle independently transcribes METHODOLOGY's nearest-rank rule.
// It neither constructs product histograms nor calls product quantile, bucket,
// rank, equality or validation helpers. Unbounded integers determine ranks.
func histogramOracle(buckets []HistogramBucket, p Percentiles, lower bool) bool {
	total := new(big.Int)
	for i, b := range buckets {
		if b.Count <= 0 || b.LowerMS < 0 || b.LowerMS >= b.UpperMS || (i > 0 && b.LowerMS < buckets[i-1].UpperMS) {
			return false
		}
		// Independently derive the power-of-two grid from the whole-us lower
		// bound; do not use production bucket construction or search helpers.
		us := math.Round(b.LowerMS * 1000)
		if us < 0 || us > 255*math.Exp2(29) || us/1000 != b.LowerMS {
			return false
		}
		width := 1.0
		for us >= 256*width {
			width *= 2
		}
		if math.Mod(us, width) != 0 || (us+width)/1000 != b.UpperMS {
			return false
		}
		total.Add(total, big.NewInt(b.Count))
	}
	if p.Count < 0 || total.Cmp(big.NewInt(p.Count)) != 0 {
		return false
	}
	if p.Count == 0 {
		return p.P50 == 0 && p.P95 == 0 && p.P99 == 0 && p.P999 == 0 && p.Max == 0
	}
	last := buckets[len(buckets)-1]
	// Last sub-bucket below 2^37 us: [255*2^29, 256*2^29).
	overflow := last.LowerMS == float64(255*(int64(1)<<29))/1000 && last.UpperMS == float64(int64(1)<<37)/1000
	if p.Max < last.LowerMS || (!overflow && p.Max >= last.UpperMS) {
		return false
	}
	for i, perMille := range []int64{500, 950, 990, 999} {
		rank := new(big.Int).Mul(big.NewInt(p.Count), big.NewInt(perMille))
		rank.Add(rank, big.NewInt(999)).Quo(rank, big.NewInt(1000))
		cumulative := new(big.Int)
		for _, b := range buckets {
			cumulative.Add(cumulative, big.NewInt(b.Count))
			if cumulative.Cmp(rank) < 0 {
				continue
			}
			edge := b.LowerMS
			if !lower {
				edge = b.UpperMS
				if p.Max < edge {
					edge = p.Max
				}
			}
			_, exp := math.Frexp(edge)
			step := math.Ldexp(1, exp-53)
			if edge == 0 || step == 0 {
				step = math.SmallestNonzeroFloat64
			}
			got := []float64{p.P50, p.P95, p.P99, p.P999}[i]
			if math.IsNaN(got) || math.IsInf(got, 0) || math.Abs(got-edge) > 4*step {
				return false
			}
			break
		}
	}
	return true
}

type rawHistogramPair struct {
	path    string
	buckets *[]HistogramBucket
	summary *Percentiles
	lower   bool
}

// Reflection discovers EVERY raw histogram in the receipt type, not just the
// populated ones. A new histogram without a documented summary mapping fails
// the corpus instead of silently escaping it. Distribution currently has none.
func rawHistogramPairs(r *Result) []rawHistogramPair {
	var pairs []rawHistogramPair
	var visit func(reflect.Value, string)
	visit = func(v reflect.Value, path string) {
		if v.Type() == reflect.TypeOf([]HistogramBucket{}) {
			pair := rawHistogramPair{path: path, buckets: v.Addr().Interface().(*[]HistogramBucket)}
			switch path {
			case "lag.upper_bound_from_scheduled_buckets":
				pair.summary = &r.Lag.FromScheduled
			case "verification.durable_lag.insert_lag_buckets":
				pair.summary, pair.lower = &r.Verification.DurableLag.Lag, true
			default:
				panic("raw histogram lacks documented summary mapping: " + path)
			}
			pairs = append(pairs, pair)
			return
		}
		switch v.Kind() {
		case reflect.Struct:
			for i := 0; i < v.NumField(); i++ {
				f := v.Type().Field(i)
				tag := strings.Split(f.Tag.Get("json"), ",")[0]
				if f.PkgPath == "" && tag != "" && tag != "-" {
					visit(v.Field(i), strings.TrimPrefix(path+"."+tag, "."))
				}
			}
		case reflect.Pointer:
			if v.IsNil() {
				// Walk an unpopulated optional schema without changing the receipt.
				visit(reflect.New(v.Type().Elem()).Elem(), path)
			} else {
				visit(v.Elem(), path)
			}
		case reflect.Slice, reflect.Array:
			if v.Len() == 0 {
				visit(reflect.New(v.Type().Elem()).Elem(), path+"[]")
			} else {
				for i := 0; i < v.Len(); i++ {
					visit(v.Index(i), fmt.Sprintf("%s[%d]", path, i))
				}
			}
		}
	}
	visit(reflect.ValueOf(r).Elem(), "")
	return pairs
}

func TestHistogramReconciliationEdges(t *testing.T) {
	cases := []struct {
		name    string
		buckets []HistogramBucket
		p       Percentiles
		lower   bool
	}{
		{"empty", nil, Percentiles{}, false},
		{"zero-single-upper", []HistogramBucket{{0, .001, 1}}, Percentiles{1, .0005, .0005, .0005, .0005, .0005}, false},
		{"zero-single-lower", []HistogramBucket{{0, .001, 1}}, Percentiles{1, 0, 0, 0, 0, .0005}, true},
		{"single-capped", []HistogramBucket{{1.536, 1.544, 12}}, Percentiles{12, 1.537999, 1.537999, 1.537999, 1.537999, 1.537999}, false},
		{"single-lower", []HistogramBucket{{1.536, 1.544, 12}}, Percentiles{12, 1.536, 1.536, 1.536, 1.536, 1.537999}, true},
		{"even-upper-rank", []HistogramBucket{{1, 1.004, 6}, {2, 2.008, 6}}, Percentiles{12, 1.004, 2.005, 2.005, 2.005, 2.005}, false},
		{"even-lower-rank", []HistogramBucket{{1, 1.004, 6}, {2, 2.008, 6}}, Percentiles{12, 1, 2, 2, 2, 2.005}, true},
		{"overflow-upper", []HistogramBucket{{136902082.56, 137438953.472, 1}}, Percentiles{1, 137438953.472, 137438953.472, 137438953.472, 137438953.472, 200000000}, false},
		{"overflow-lower", []HistogramBucket{{136902082.56, 137438953.472, 1}}, Percentiles{1, 136902082.56, 136902082.56, 136902082.56, 136902082.56, 200000000}, true},
		// Mathematical p50 is one beyond the first bucket; float64(MaxInt64)
		// would round the rank down to the first bucket.
		{"integer-rank", []HistogramBucket{{1, 1.004, math.MaxInt64 / 2}, {2, 2.008, math.MaxInt64/2 + 1}}, Percentiles{math.MaxInt64, 2, 2, 2, 2, 2.005}, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if !histogramOracle(tc.buckets, tc.p, tc.lower) {
				t.Fatal("independent control invalid")
			}
			if err := validateHistogram(tc.name, tc.buckets, tc.p, tc.lower); err != nil {
				t.Fatal(err)
			}
			if tc.p.Count == 0 {
				return
			}
			for _, delta := range []float64{-1e-9, 1e-9} {
				p := tc.p
				// At overflow-scale use more than four ULPs, still << one bucket.
				if p.P50 > 1e8 {
					delta *= 1000
				}
				p.P50 += delta
				if histogramOracle(tc.buckets, p, tc.lower) || validateHistogram(tc.name, tc.buckets, p, tc.lower) == nil {
					t.Fatalf("accepted quantile epsilon %g", delta)
				}
			}
			p := tc.p
			p.P50 = math.Nextafter(p.P50, math.Inf(1))
			if !histogramOracle(tc.buckets, p, tc.lower) || validateHistogram(tc.name, tc.buckets, p, tc.lower) != nil {
				t.Fatal("one-ULP conversion rejected")
			}
			p = tc.p
			p.Max = math.Nextafter(tc.buckets[len(tc.buckets)-1].LowerMS, math.Inf(-1))
			if validateHistogram(tc.name, tc.buckets, p, tc.lower) == nil {
				t.Fatal("maximum below final bucket accepted")
			}
			if !strings.HasPrefix(tc.name, "overflow") {
				p.Max = tc.buckets[len(tc.buckets)-1].UpperMS
				if validateHistogram(tc.name, tc.buckets, p, tc.lower) == nil {
					t.Fatal("maximum on exclusive upper edge accepted")
				}
			}
		})
	}
}

// histogramMutations augments the numeric-leaf corpus with mutations tied to
// bucket/rank semantics. It covers BOTH mappings discovered by reflection.
func histogramMutations(r Result, check func(Result, string)) {
	raw, _ := json.Marshal(r)
	for index, pair := range rawHistogramPairs(&r) {
		if len(*pair.buckets) == 0 {
			continue
		}
		for q := 0; q < 4; q++ {
			for _, op := range []string{"bucket-minus", "bucket-plus", "epsilon-minus", "epsilon-plus", "ulp-minus", "ulp-plus", "swap"} {
				var changed Result
				json.Unmarshal(raw, &changed)
				p := rawHistogramPairs(&changed)[index]
				summary := reflect.ValueOf(p.summary).Elem()
				field := summary.Field(q + 1)
				v := field.Float()
				width := (*p.buckets)[0].UpperMS - (*p.buckets)[0].LowerMS
				for _, b := range *p.buckets {
					if b.LowerMS <= v && v <= b.UpperMS {
						width = b.UpperMS - b.LowerMS
						break
					}
				}
				switch op {
				case "bucket-minus":
					field.SetFloat(v - width)
				case "bucket-plus":
					field.SetFloat(v + width)
				case "epsilon-minus":
					field.SetFloat(v - 1e-9)
				case "epsilon-plus":
					field.SetFloat(v + 1e-9)
				case "ulp-minus":
					field.SetFloat(math.Nextafter(v, math.Inf(-1)))
				case "ulp-plus":
					field.SetFloat(math.Nextafter(v, math.Inf(1)))
				case "swap":
					other := summary.Field((q+1)%4 + 1)
					field.SetFloat(other.Float())
					other.SetFloat(v)
				}
				check(changed, fmt.Sprintf("histogram/%s/q%d/%s", pair.path, q, op))
			}
		}
		// Correlated bounds and quantiles can agree with each other yet violate
		// the canonical grid. Exercise every occupied bucket and both mappings.
		for bucket := range *pair.buckets {
			for _, delta := range []float64{-1e-7, 1e-7, 1} {
				var changed Result
				json.Unmarshal(raw, &changed)
				p := rawHistogramPairs(&changed)[index]
				b := &(*p.buckets)[bucket]
				old := b.UpperMS
				if p.lower {
					old = b.LowerMS
					b.LowerMS += delta
				} else {
					b.UpperMS += delta
				}
				s := reflect.ValueOf(p.summary).Elem()
				for q := 1; q <= 4; q++ {
					if s.Field(q).Float() == old {
						s.Field(q).SetFloat(old + delta)
					}
				}
				check(changed, fmt.Sprintf("histogram/%s/correlated-%d/%g", pair.path, bucket, delta))
			}
		}
		for _, op := range []string{"widen-final", "maximum-outside", "maximum-at-exclusive-edge", "counts-forward", "counts-backward"} {
			var changed Result
			json.Unmarshal(raw, &changed)
			p := rawHistogramPairs(&changed)[index]
			bs := *p.buckets
			switch op {
			case "widen-final":
				bs[len(bs)-1].UpperMS = 1e300
			case "maximum-outside":
				p.summary.Max = bs[len(bs)-1].UpperMS + 1
			case "maximum-at-exclusive-edge":
				p.summary.Max = bs[len(bs)-1].UpperMS
			case "counts-forward", "counts-backward":
				if len(bs) < 2 {
					continue
				}
				from, to := 0, len(bs)-1
				if op == "counts-backward" {
					from, to = to, from
				}
				bs[from].Count--
				bs[to].Count++
				if bs[from].Count == 0 {
					*p.buckets = append(bs[:from], bs[from+1:]...)
				}
			}
			check(changed, "histogram/"+pair.path+"/"+op)
		}
	}
}

func retainedHistogramNeighbourhoods(t *testing.T, check func(Result, string)) {
	t.Helper()
	files, err := filepath.Glob("testdata/round3/counterexample-*.json")
	if err != nil || len(files) != 26 {
		t.Fatalf("retained corpus: %d %v", len(files), err)
	}
	for _, file := range files {
		b, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		var r Result
		if err := json.Unmarshal(b, &r); err != nil {
			t.Fatal(err)
		}
		if receiptOracle(r) {
			t.Fatalf("independent oracle accepted %s", file)
		}
		prefix := filepath.Base(file)
		check(r, prefix)
		histogramMutations(r, func(r Result, label string) { check(r, prefix+"/"+label) })
	}
}
