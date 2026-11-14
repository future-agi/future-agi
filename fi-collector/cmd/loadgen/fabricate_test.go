package main

import (
	"context"
	"math/rand"
	"strings"
	"testing"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/adapter"
)

func cfg(n, m int) FabricateConfig {
	return FabricateConfig{
		ProjectID: "11111111-1111-1111-1111-111111111111",
		OrgID:     "22222222-2222-2222-2222-222222222222",
		Shape:     "llm", Traces: n, SpansPerTrace: m, Sessions: 2,
		Start: mustTime("2026-01-01T00:00:00Z"), End: mustTime("2026-01-31T00:00:00Z"),
	}
}

// Same seed → byte-identical output (CI determinism).
func TestFabricateDeterministic(t *testing.T) {
	a := fabricateBatch(cfg(10, 5), rand.New(rand.NewSource(42)))
	b := fabricateBatch(cfg(10, 5), rand.New(rand.NewSource(42)))
	if a.SpanCount() != b.SpanCount() || a.SpanCount() != 50 {
		t.Fatalf("span counts: %d vs %d", a.SpanCount(), b.SpanCount())
	}
	ra, _ := chexp.Convert(a)
	rb, _ := chexp.Convert(b)
	for i := range ra {
		if ra[i]["id"] != rb[i]["id"] || ra[i]["trace_id"] != rb[i]["trace_id"] {
			t.Fatalf("row %d differs across same-seed runs", i)
		}
	}
}

// Fabricated spans must survive the production converter: project stamped,
// exactly one root per trace, curated trace identities collected.
func TestFabricateConvertsCleanly(t *testing.T) {
	traces := fabricateBatch(cfg(10, 5), rand.New(rand.NewSource(1)))
	rows, ids, err := chexp.ConvertWithIdentities(context.Background(), traces, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 50 {
		t.Fatalf("rows: %d", len(rows))
	}
	roots := map[any]int{}
	for _, r := range rows {
		if r["project_id"] != "11111111-1111-1111-1111-111111111111" {
			t.Fatalf("project_id not stamped: %v", r["project_id"])
		}
		if r["parent_span_id"] == "" {
			roots[r["trace_id"]]++
		}
	}
	if len(roots) != 10 {
		t.Fatalf("expected 10 rooted traces, got %d", len(roots))
	}
	if len(ids.Traces()) != 10 {
		t.Fatalf("curated traces: %d", len(ids.Traces()))
	}
}

// resAttrProjectType is copied from the converter's unexported resAttrProjectTy;
// a converted row must echo it under that key so the two stay aligned.
func TestFabricateStampsProjectType(t *testing.T) {
	rows, _ := chexp.Convert(fabricateBatch(cfg(1, 1), rand.New(rand.NewSource(7))))
	ra, _ := rows[0]["resource_attrs"].(map[string]any)
	if ra["project_type"] != "observe" {
		t.Fatalf("project_type resource attr: %v", ra["project_type"])
	}
}

// voice: the overflow-routed transcript makes every root's attributes_extra
// JSON payload ≥ 1 MiB; children stay lean.
func TestVoiceShapeFatRoots(t *testing.T) {
	c := cfg(5, 4)
	c.Shape = shapeVoice
	rows, _ := chexp.Convert(fabricateBatch(c, rand.New(rand.NewSource(3))))
	roots := 0
	for _, row := range rows {
		of, ok := row["attributes_extra"].(map[string]any)
		if !ok {
			t.Fatalf("attributes_extra not a map: %T", row["attributes_extra"])
		}
		size := len(adapter.OverflowToJSON(of))
		if row["parent_span_id"] == "" {
			roots++
			if size <= 1<<20 {
				t.Fatalf("root attributes_extra JSON = %d bytes, want > %d", size, 1<<20)
			}
		} else if size > 1<<20 {
			t.Fatalf("child attributes_extra unexpectedly fat: %d bytes", size)
		}
	}
	if roots != 5 {
		t.Fatalf("roots: %d, want 5", roots)
	}
}

// agent-deep: 50–200 spans/trace, and each trace carries adversarial start
// ordering — one child starts before its root and one child ties the root
// (LIMIT 1 BY parity stressors).
func TestAgentDeepAdversarialOrdering(t *testing.T) {
	c := cfg(20, 0) // SpansPerTrace ignored: agent-deep draws its own count
	c.Shape = shapeAgentDeep
	rows, _ := chexp.Convert(fabricateBatch(c, rand.New(rand.NewSource(9))))

	rootStart := map[string]string{}
	childStarts := map[string][]string{}
	perTrace := map[string]int{}
	for _, row := range rows {
		tid := row["trace_id"].(string)
		perTrace[tid]++
		st := row["start_time"].(string)
		if row["parent_span_id"] == "" {
			rootStart[tid] = st
		} else {
			childStarts[tid] = append(childStarts[tid], st)
		}
	}
	for tid, n := range perTrace {
		if n < 50 || n > 200 {
			t.Fatalf("trace %s has %d spans, want [50,200]", tid, n)
		}
	}
	// At least one trace must exhibit BOTH a precede and a tie (lexicographic
	// compare is chronological for fixed-width DateTime64 text).
	found := false
	for tid, rs := range rootStart {
		precedes, ties := false, false
		for _, cs := range childStarts[tid] {
			if cs < rs {
				precedes = true
			}
			if cs == rs {
				ties = true
			}
		}
		if precedes && ties {
			found = true
			break
		}
	}
	if !found {
		t.Fatal("no trace has both a child preceding its root and a child tying its root")
	}
}

// fat-attrs: ≥ 80 distinct high-cardinality attr keys land on every span
// (across the typed maps and the overflow tier).
func TestFatAttrsCardinality(t *testing.T) {
	c := cfg(3, 4)
	c.Shape = shapeFatAttrs
	rows, _ := chexp.Convert(fabricateBatch(c, rand.New(rand.NewSource(5))))
	for _, row := range rows {
		if n := countAttrKeys(row, "attr.k"); n < 80 {
			t.Fatalf("span has %d attr.k* keys, want >= 80", n)
		}
	}
}

// countAttrKeys tallies keys with the given prefix across every attribute
// destination the converter splits into.
func countAttrKeys(row map[string]any, prefix string) int {
	n := 0
	if m, ok := row["attrs_string"].(map[string]string); ok {
		for k := range m {
			if strings.HasPrefix(k, prefix) {
				n++
			}
		}
	}
	if m, ok := row["attrs_number"].(map[string]float64); ok {
		for k := range m {
			if strings.HasPrefix(k, prefix) {
				n++
			}
		}
	}
	if m, ok := row["attrs_bool"].(map[string]uint8); ok {
		for k := range m {
			if strings.HasPrefix(k, prefix) {
				n++
			}
		}
	}
	if m, ok := row["attributes_extra"].(map[string]any); ok {
		for k := range m {
			if strings.HasPrefix(k, prefix) {
				n++
			}
		}
	}
	return n
}
