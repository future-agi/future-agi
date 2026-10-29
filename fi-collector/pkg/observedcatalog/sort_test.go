package observedcatalog

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math/rand"
	"runtime"
	"sort"
	"strings"
	"testing"
)

// Independent historical comparator: do not share the optimized sort helper.
func legacySorted(b Batch) Batch {
	b.Keys = append([]KeyRow{}, b.Keys...)
	b.Values = append([]ValueRow{}, b.Values...)
	sort.Slice(b.Keys, func(i, j int) bool {
		a, _ := json.Marshal(b.Keys[i])
		c, _ := json.Marshal(b.Keys[j])
		return string(a) < string(c)
	})
	sort.Slice(b.Values, func(i, j int) bool {
		a, _ := json.Marshal(b.Values[i])
		c, _ := json.Marshal(b.Values[j])
		return string(a) < string(c)
	})
	return b
}

func assertSortParity(t *testing.T, b Batch) {
	t.Helper()
	got := Merge(b, b)
	want := legacySorted(got)
	g, _ := json.Marshal(got)
	w, _ := json.Marshal(want)
	if !bytes.Equal(g, w) {
		t.Fatalf("JSON ordering changed\ngot %s\nwant %s", g, w)
	}
	// Both valid encodings and invalid-input errors must remain identical.
	gc, ge := Chunk(got)
	wc, we := Chunk(want)
	if fmt.Sprint(ge) != fmt.Sprint(we) {
		t.Fatalf("chunk error changed: %v / %v", ge, we)
	}
	if ge == nil {
		if len(gc) != len(wc) {
			t.Fatal("chunk count changed")
		}
		for i := range gc {
			a, ae := Encode(gc[i])
			b, be := Encode(wc[i])
			if !bytes.Equal(a, b) || fmt.Sprint(ae) != fmt.Sprint(be) {
				t.Fatal("encoded chunk changed")
			}
		}
	}
}

func TestMergeSortRandomizedGoldenParity(t *testing.T) {
	rng := rand.New(rand.NewSource(8745))
	texts := []string{"", "alpha", "A", "z", "é", "ΐ", "日本語", "😀", "  ", "<&>", "\"\\", "\x00\t\n\r", "\xff", "a/b", strings.Repeat("x", 4096)}
	for iteration := 0; iteration < 100; iteration++ {
		var b Batch
		for i := 0; i < 1+rng.Intn(220); i++ {
			s := testSpan()
			key := texts[rng.Intn(len(texts))] + fmt.Sprint(rng.Intn(12))
			value := texts[rng.Intn(len(texts))]
			s.Row["attrs_string"] = map[string]string{key: value}
			s.Row["attrs_number"] = map[string]float64{"numeric": float64(rng.Intn(12)) - 4.5}
			s.Row["attrs_bool"] = map[string]uint8{"bool": uint8(rng.Intn(2))}
			s.Row["attributes_extra"] = map[string]any{"array": []any{value, true, float64(2)}, "map": map[string]any{"nested": value}}
			s.Row["start_time"] = fmt.Sprintf("2026-10-%02d 12:34:56.123456", 1+rng.Intn(27))
			if rng.Intn(2) == 0 {
				s.WorkspaceID = "22222222-2222-4222-8222-222222222222"
			}
			built, _, err := Extract(s, DefaultLimits())
			if err != nil {
				t.Fatal(err)
			}
			b.Keys = append(b.Keys, built.Keys...)
			b.Values = append(b.Values, built.Values...)
		}
		assertSortParity(t, b)
	}
	// Invalid identities, timestamps/types/values, UTF-8 replacement collisions,
	// and the oversized fallback must preserve bytes and validation outcomes.
	for _, text := range texts {
		b := Batch{}
		for i := 0; i < 30; i++ {
			scope := Scope{OrganizationID: text, WorkspaceID: fmt.Sprint(i % 3), ProjectID: "invalid"}
			b.Keys = append(b.Keys, KeyRow{Scope: scope, SourceKind: text, AttributeKey: fmt.Sprint(i) + text, AttributeType: "bad", FirstSeen: text, LastSeen: "bad"})
			b.Values = append(b.Values, ValueRow{Scope: scope, AttributeKey: text, ValueJSON: fmt.Sprint(i) + text, ValueSearchTextFolded: text})
		}
		assertSortParity(t, b)
	}
}

func TestMergeSortScratchBound(t *testing.T) {
	// At most floor(limit/512) decorations. All row string bytes together
	// have an encoded upper bound of 128KiB, BEFORE allocating any sort keys.
	for _, large := range []bool{false, true} {
		rows := make([]ValueRow, mergeSortKeyLimit/512)
		if large {
			rows = rows[:2]
			rows[0].ValueJSON = strings.Repeat("\x00", 10000)
			rows[1].ValueJSON = strings.Repeat("\x01", 10000)
		}
		runtime.GC()
		var before, after runtime.MemStats
		runtime.ReadMemStats(&before)
		sortJSONRows(rows, valueStringBytes)
		runtime.ReadMemStats(&after)
		if delta := after.TotalAlloc - before.TotalAlloc; delta > MergeSortScratchBytes {
			t.Fatalf("sort scratch allocated %d, charged %d", delta, MergeSortScratchBytes)
		}
		runtime.KeepAlive(rows)
	}
}

func BenchmarkMergeHeavySort(b *testing.B) {
	span := testSpan()
	attrs := map[string]string{}
	for i := 0; i < 50; i++ {
		attrs[fmt.Sprintf("attribute.%02d", i)] = "short-value"
	}
	span.Row["attrs_string"] = attrs
	batch, _, err := Extract(span, DefaultLimits())
	if err != nil {
		b.Fatal(err)
	}
	for i := 0; i < b.N; i++ {
		Merge(batch, batch, batch)
	}
}
