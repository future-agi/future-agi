package ingestbench

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestCanonicalHistogramGeometry(t *testing.T) {
	for i := 0; i < histogramBuckets; i++ {
		lo, hi := latencyLowerEdge(i), latencyEdge(i)
		b := []HistogramBucket{{LowerMS: lo, UpperMS: hi, Count: 1}}
		p := Percentiles{Count: 1, P50: lo, P95: lo, P99: lo, P999: lo, Max: lo}
		if err := validateHistogram("buckets", b, p, true); err != nil {
			t.Fatalf("canonical bucket %d: %v", i, err)
		}
		for _, upper := range []bool{false, true} {
			for _, direction := range []float64{math.Inf(-1), math.Inf(1)} {
				mutated := append([]HistogramBucket(nil), b...)
				if upper {
					mutated[0].UpperMS = math.Nextafter(hi, direction)
				} else {
					mutated[0].LowerMS = math.Nextafter(lo, direction)
				}
				if err := validateHistogram("buckets", mutated, p, true); err == nil || !strings.Contains(err.Error(), "canonical") {
					t.Fatalf("bucket %d upper=%v accepted noncanonical geometry: %v", i, upper, err)
				}
			}
		}
	}
}

func TestDistributionMeanExactExtrema(t *testing.T) {
	for _, count := range []int64{1, 2, 100000} {
		d := Distribution{Count: count, Min: 10169, Mean: 10169, P50: 10169, P95: 10169, P99: 10169, Max: 10169}
		if err := measurementNumbers(d, "distribution"); err != nil {
			t.Fatal(err)
		}
		for _, direction := range []float64{math.Inf(-1), math.Inf(1)} {
			d.Mean = math.Nextafter(10169, direction)
			if err := measurementNumbers(d, "distribution"); err == nil {
				t.Fatalf("constant count %d admitted mean %.17g", count, d.Mean)
			}
		}
	}
	d := Distribution{Count: 2, Min: 10, Mean: 20, P50: 10, P95: 20, P99: 20, Max: 20}
	for _, mean := range []float64{10, 15, 20} {
		d.Mean = mean
		if err := measurementNumbers(d, "distribution"); err != nil {
			t.Fatal(err)
		}
	}
	for _, mean := range []float64{math.Nextafter(10, 0), math.Nextafter(20, math.Inf(1))} {
		d.Mean = mean
		if err := measurementNumbers(d, "distribution"); err == nil {
			t.Fatal("nonconstant mean outside extrema accepted")
		}
	}
}

// Export declarative Go wire types, not production validation decisions, for
// the independently implemented Python twin. New fields enter the shape corpus
// automatically; numeric/ledger/geometry algorithms are NOT exported.
func TestReceiptWireSchemaExport(t *testing.T) {
	var schema func(reflect.Type) map[string]any
	schema = func(typ reflect.Type) map[string]any {
		if typ == reflect.TypeOf(json.RawMessage{}) {
			return map[string]any{"kind": "opaque", "nullable": true}
		}
		if typ == reflect.TypeOf(time.Time{}) {
			return map[string]any{"kind": "string"}
		}
		if typ.Kind() == reflect.Pointer {
			s := schema(typ.Elem())
			s["nullable"] = true
			return s
		}
		s := map[string]any{"kind": typ.Kind().String()}
		switch typ.Kind() {
		case reflect.Struct:
			fields := map[string]any{}
			for i := 0; i < typ.NumField(); i++ {
				f := typ.Field(i)
				tag := strings.Split(f.Tag.Get("json"), ",")
				if f.PkgPath != "" || tag[0] == "-" {
					continue
				}
				name := tag[0]
				if name == "" {
					name = f.Name
				}
				child := schema(f.Type)
				optional := f.Tag.Get("receipt") == "optional" || f.Type.Kind() == reflect.Pointer || f.Type.Kind() == reflect.Map || f.Type.Kind() == reflect.Slice
				for _, option := range tag[1:] {
					optional = optional || option == "omitempty"
				}
				child["optional"] = optional
				if f.Tag.Get("receipt") == "optional" {
					child["nullable"] = true
				}
				fields[name] = child
			}
			s["fields"] = fields
		case reflect.Slice, reflect.Array, reflect.Map:
			s["element"] = schema(typ.Elem())
			s["nullable"] = typ.Kind() != reflect.Array
			if typ.Kind() == reflect.Map {
				s["key"] = typ.Key().Kind().String()
			}
		case reflect.Interface:
			s["nullable"] = true
		}
		return s
	}
	s := schema(reflect.TypeOf(Result{}))
	b, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if name := os.Getenv("INGESTBENCH_SCHEMA_OUT"); name != "" {
		if err := os.WriteFile(name, b, 0600); err != nil {
			t.Fatal(err)
		}
	}
	if len(b) < 10000 {
		t.Fatal("schema export unexpectedly empty")
	}
}

func TestReceiptJSONOptionalPointerAndContainers(t *testing.T) {
	r := retainedResult(t, "exact-head-mock")
	r.Recovery = &RecoveryResult{}
	raw, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	if err := ValidateResultJSON(raw); err != nil {
		t.Fatal(err)
	}
	// All non-nil pointer fields and optional recovery components use their own
	// underlying schema, but nil remains a valid unobserved measurement.
	for _, field := range []string{"recovery", "workload", "provenance"} {
		var doc map[string]json.RawMessage
		json.Unmarshal(raw, &doc)
		doc[field] = json.RawMessage("null")
		b, _ := json.Marshal(doc)
		if err := ValidateResultJSON(b); err != nil {
			t.Fatalf("%s null: %v", field, err)
		}
	}
	for _, suffix := range []string{`null`, `{"COUNT":1,"count":2,"p50_ms":0,"p95_ms":0,"p99_ms":0,"p999_ms":0,"max_ms":0}`} {
		var doc map[string]json.RawMessage
		json.Unmarshal(raw, &doc)
		doc["otlp_request_latency"] = json.RawMessage(suffix)
		b, _ := json.Marshal(doc)
		if err := ValidateResultJSON(b); err == nil || !strings.Contains(err.Error(), "otlp_request_latency") {
			t.Fatal(fmt.Sprint("invalid object accepted: ", err))
		}
	}
}
