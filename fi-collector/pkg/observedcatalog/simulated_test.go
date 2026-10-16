package observedcatalog

import (
	"context"
	"reflect"
	"testing"
)

var emptyBatch = Batch{Keys: []KeyRow{}, Values: []ValueRow{}}

func hasKey(batch Batch, key string) bool {
	for _, row := range batch.Keys {
		if row.AttributeKey == key {
			return true
		}
	}
	return false
}

func spanMarked() ScopedSpan {
	span := testSpan()
	span.Row["attrs_string"].(map[string]string)["fi.simulator.call_execution_id"] = "call-1"
	return span
}

func resourceMarked(value any) ScopedSpan {
	span := testSpan()
	span.Row["resource_attrs"] = map[string]any{"service.name": "svc", "fi.simulation.origin": value}
	return span
}

func requireSkipped(t *testing.T, span ScopedSpan) {
	t.Helper()
	batch, report, err := Extract(span, DefaultLimits())
	if err != nil || !report.Complete || len(report.GapReasons) != 0 {
		t.Fatalf("marked row failed: %v %+v", err, report)
	}
	if !reflect.DeepEqual(batch, emptyBatch) {
		t.Fatalf("marked row was catalogued: %d keys, %d values", len(batch.Keys), len(batch.Values))
	}
}

func TestExtractSkipsSimulatedRows(t *testing.T) {
	t.Run("span mark", func(t *testing.T) { requireSkipped(t, spanMarked()) })
	t.Run("span mark with empty value", func(t *testing.T) {
		span := testSpan()
		span.Row["attrs_string"].(map[string]string)["fi.simulator.call_execution_id"] = ""
		requireSkipped(t, span)
	})
	for name, value := range map[string]any{
		"string": "simulation", "empty string": "", "bool": true, "int": int64(3), "float": 1.5, "nil": nil,
	} {
		t.Run("resource mark "+name, func(t *testing.T) { requireSkipped(t, resourceMarked(value)) })
	}
	t.Run("both marks", func(t *testing.T) {
		span := spanMarked()
		span.Row["resource_attrs"] = map[string]any{"fi.simulation.origin": "simulation"}
		requireSkipped(t, span)
	})
}

func TestExtractKeepsUnmarkedRowsExactly(t *testing.T) {
	want, report, err := Extract(testSpan(), DefaultLimits())
	if err != nil || !report.Complete || len(want.Keys) == 0 || len(want.Values) == 0 || !hasKey(want, "model") {
		t.Fatalf("baseline: %v %+v", err, report)
	}
	withResource := testSpan()
	withResource.Row["resource_attrs"] = map[string]any{"service.name": "svc", "fi.simulation": "x", "fi.simulation.origin.extra": "x"}
	got, _, err := Extract(withResource, DefaultLimits())
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatalf("unmarked resource attributes changed the catalog: %v", err)
	}
}

func TestExtractNearMissesAreNotMarks(t *testing.T) {
	cases := map[string]struct {
		mutate func(row map[string]any)
		key    string // a custom key the row must still catalogue
	}{
		"number map only": {func(row map[string]any) {
			row["attrs_number"].(map[string]float64)["fi.simulator.call_execution_id"] = 1
		}, "fi.simulator.call_execution_id"},
		"bool map only": {func(row map[string]any) {
			row["attrs_bool"].(map[string]uint8)["fi.simulator.call_execution_id"] = 1
		}, "fi.simulator.call_execution_id"},
		"overflow only": {func(row map[string]any) {
			row["attributes_extra"].(map[string]any)["fi.simulator.call_execution_id"] = "call-1"
		}, "fi.simulator.call_execution_id"},
		"different case": {func(row map[string]any) {
			row["attrs_string"].(map[string]string)["Fi.Simulator.Call_Execution_Id"] = "call-1"
		}, "Fi.Simulator.Call_Execution_Id"},
		"trailing space": {func(row map[string]any) {
			row["attrs_string"].(map[string]string)["fi.simulator.call_execution_id "] = "call-1"
		}, "fi.simulator.call_execution_id "},
		"inner space": {func(row map[string]any) {
			row["attrs_string"].(map[string]string)["fi.simulator.call execution_id"] = "call-1"
		}, "fi.simulator.call execution_id"},
		// A span attribute named like the resource mark is not a mark: the
		// resource key is what identifies a simulated trace.
		"origin as span attribute": {func(row map[string]any) {
			row["attrs_string"].(map[string]string)["fi.simulation.origin"] = "simulation"
		}, "fi.simulation.origin"},
		"origin different case in resource": {func(row map[string]any) {
			row["resource_attrs"] = map[string]any{"FI.SIMULATION.ORIGIN": "simulation"}
		}, "a"},
		"origin with space in resource": {func(row map[string]any) {
			row["resource_attrs"] = map[string]any{"fi.simulation.origin ": "simulation"}
		}, "a"},
		"span mark key as resource key": {func(row map[string]any) {
			row["resource_attrs"] = map[string]any{"fi.simulator.call_execution_id": "call-1"}
		}, "a"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			span := testSpan()
			tc.mutate(span.Row)
			batch, report, err := Extract(span, DefaultLimits())
			if err != nil || !report.Complete {
				t.Fatalf("%v %+v", err, report)
			}
			if !hasKey(batch, tc.key) || !hasKey(batch, "model") || len(batch.Values) == 0 {
				t.Fatalf("unmarked row was skipped: %d keys, %d values", len(batch.Keys), len(batch.Values))
			}
			if isSimulated(span.Row) {
				t.Fatal("near miss reported as simulated")
			}
		})
	}
}

func TestSimulatedRowsKeepTheSameValidationErrors(t *testing.T) {
	breakers := map[string]func(ScopedSpan) ScopedSpan{
		"scope error": func(s ScopedSpan) ScopedSpan { s.ScopeError = "project_workspace_mismatch"; return s },
		"org mismatch": func(s ScopedSpan) ScopedSpan {
			s.Row["org_id"] = "44444444-4444-4444-8444-444444444444"
			return s
		},
		"noncanonical timestamp": func(s ScopedSpan) ScopedSpan { s.Row["start_time"] = "2026-09-08T01:02:03Z"; return s },
		"missing timestamp":      func(s ScopedSpan) ScopedSpan { delete(s.Row, "start_time"); return s },
		"missing number map":     func(s ScopedSpan) ScopedSpan { delete(s.Row, "attrs_number"); return s },
		"invalid overflow":       func(s ScopedSpan) ScopedSpan { s.Row["attributes_extra"] = "not json"; return s },
		"missing project":        func(s ScopedSpan) ScopedSpan { delete(s.Row, "project_id"); return s },
	}
	marks := map[string]func(ScopedSpan) ScopedSpan{
		"span mark": func(s ScopedSpan) ScopedSpan {
			s.Row["attrs_string"].(map[string]string)["fi.simulator.call_execution_id"] = "call-1"
			return s
		},
		"resource mark": func(s ScopedSpan) ScopedSpan {
			s.Row["resource_attrs"] = map[string]any{"fi.simulation.origin": "simulation"}
			return s
		},
	}
	for name, breaker := range breakers {
		_, _, plain := Extract(breaker(testSpan()), DefaultLimits())
		if plain == nil {
			t.Fatalf("%s: unmarked row accepted", name)
		}
		for markName, mark := range marks {
			_, _, got := Extract(mark(breaker(testSpan())), DefaultLimits())
			if got == nil || got.Error() != plain.Error() {
				t.Fatalf("%s with %s: got %v, want %v", name, markName, got, plain)
			}
		}
	}
	if _, _, err := Extract(spanMarked(), Limits{MaxKeysPerSpan: -1}); err == nil {
		t.Fatal("invalid limits accepted for a marked row")
	}
}

func TestIsSimulatedReadsOnlyTheTwoMarks(t *testing.T) {
	if isSimulated(map[string]any{}) || isSimulated(nil) {
		t.Fatal("empty row reported as simulated")
	}
	if !isSimulated(spanMarked().Row) || !isSimulated(resourceMarked("x").Row) {
		t.Fatal("mark not recognised")
	}
}

func TestWriterEnqueuesOnlyUnmarkedSpans(t *testing.T) {
	unmarked := testSpan()
	unmarked.Row["attrs_string"].(map[string]string)["real_only"] = "kept"
	simSpan, simResource := spanMarked(), resourceMarked("simulation")
	simSpan.Row["attrs_string"].(map[string]string)["sim_span_only"] = "dropped"
	simResource.Row["attrs_string"].(map[string]string)["sim_resource_only"] = "dropped"

	replayed := func(spans []ScopedSpan) Batch {
		t.Helper()
		writer, err := NewWriter(SpoolConfig{Directory: t.TempDir()}, DefaultLimits())
		if err != nil {
			t.Fatal(err)
		}
		defer writer.Close()
		if err := writer.EnqueueCanonicalSpans(spans); err != nil {
			t.Fatal(err)
		}
		var published []Batch
		if _, err := writer.ReplayMerged(context.Background(), publisherFunc(func(_ context.Context, b Batch) error {
			published = append(published, b)
			return nil
		})); err != nil {
			t.Fatal(err)
		}
		return Merge(published...)
	}

	got := replayed([]ScopedSpan{simSpan, unmarked, simResource})
	if !reflect.DeepEqual(got, replayed([]ScopedSpan{unmarked})) {
		t.Fatal("marked spans changed what was enqueued")
	}
	if !hasKey(got, "real_only") || hasKey(got, "sim_span_only") || hasKey(got, "sim_resource_only") {
		t.Fatalf("wrong spans catalogued: %+v", got.Keys)
	}
	for _, row := range got.Values {
		if row.AttributeKey == "sim_span_only" || row.AttributeKey == "sim_resource_only" {
			t.Fatalf("marked value enqueued: %+v", row)
		}
	}

	// A page of only marked spans enqueues nothing and is not a failure.
	writer, err := NewWriter(SpoolConfig{Directory: t.TempDir()}, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if err := writer.EnqueueCanonicalSpans([]ScopedSpan{simSpan, simResource}); err != nil || len(writer.files) != 0 {
		t.Fatalf("marked-only spans: %v, %d spooled files", err, len(writer.files))
	}
}
