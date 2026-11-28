package trajectory

import (
	"reflect"
	"sync"
	"testing"
)

// compactedTestRow is a pointer row whose input side was compacted from a
// string input.value and whose output side was compacted from a map-valued
// output.value (JSON flag). devRow is the same span with dedup off.
func compactedTestRow(t *testing.T) (row, devRow map[string]any, b *Batch) {
	t.Helper()
	in := `{"messages":[{"role":"user","content":"full input"}]}`
	out := `{"text":"full output"}`
	outValue := map[string]any{"text": "full output"}
	devRow = map[string]any{
		"id":               "span-1",
		"input":            in,
		"output":           out,
		"attrs_string":     map[string]string{"llm.input_messages.0.message.content": "full input"},
		"attributes_extra": map[string]any{"custom": "keep", "input.value": in, "output.value": outValue},
		"_version":         uint64(41),
	}
	extra := map[string]any{"custom": "keep", "input.value": in, "output.value": outValue}
	row = map[string]any{
		"id":                 "span-1",
		"input":              "preview-in",
		"output":             "preview-out",
		"attrs_string":       devRow["attrs_string"],
		"attributes_extra":   extra,
		"_version":           uint64(41),
		"input_item_hashes":  []string{"abc"},
		"output_item_hashes": []string{},
		"tool_def_hashes":    []string{},
		"item_scope_id":      "sess-1",
		"payload_encoding":   EncodingItemsV1,
		"payload_envelope":   "{}",
	}
	fb := SpanFallback{Row: row, OrigInput: in, OrigOutput: out}
	fb.PromotedInput = CompactPromoted(extra, "input.value", false, true)
	fb.PromotedOutput = CompactPromoted(extra, "output.value", true, false)
	if fb.PromotedInput == nil || fb.PromotedOutput == nil {
		t.Fatal("both sides must compact")
	}
	want := map[string]any{"custom": "keep", SentinelPromotedInputKey: "input.value", SentinelPromotedOutputKey: "output.value", SentinelPromotedOutputJSON: true}
	if !reflect.DeepEqual(extra, want) {
		t.Fatalf("compacted attributes_extra %v, want %v", extra, want)
	}
	b = NewBatch()
	b.AddFallback(fb)
	return row, devRow, b
}

// TestRestoreInlineRowDeepEqualsDevRow: a restored row is the dedup-off row:
// promoted keys back with their original Go values (a map stays a map), no
// sentinel, no pointer column. Restoring twice changes nothing.
func TestRestoreInlineRowDeepEqualsDevRow(t *testing.T) {
	row, devRow, b := compactedTestRow(t)
	b.RestoreInline()
	if !reflect.DeepEqual(row, devRow) {
		t.Fatalf("restored row\n%v\nwant the dev row\n%v", row, devRow)
	}
	b.RestoreInlineRows([]map[string]any{row})
	if !reflect.DeepEqual(row, devRow) {
		t.Fatal("a second restore changed the row")
	}
}

// TestDeadLetterInlineCopyBumpsVersion: InlineCopy returns the dev row with
// _version one higher, leaves the live row untouched, and returns rows that
// are no longer pointer rows (or unknown rows) as they are.
func TestDeadLetterInlineCopyBumpsVersion(t *testing.T) {
	row, devRow, b := compactedTestRow(t)
	before := map[string]any{}
	for k, v := range row {
		before[k] = v
	}
	extraBefore := map[string]any{}
	for k, v := range row["attributes_extra"].(map[string]any) {
		extraBefore[k] = v
	}
	copied, ok := b.InlineCopy(row)
	if !ok {
		t.Fatal("a pointer row must be copied inline")
	}
	want := map[string]any{}
	for k, v := range devRow {
		want[k] = v
	}
	want["_version"] = uint64(42)
	if !reflect.DeepEqual(copied, want) {
		t.Fatalf("inline copy\n%v\nwant\n%v", copied, want)
	}
	if !reflect.DeepEqual(row, before) || !reflect.DeepEqual(row["attributes_extra"], extraBefore) {
		t.Fatal("InlineCopy changed the live row")
	}

	other := map[string]any{"id": "span-2", "payload_encoding": EncodingItemsV1}
	if got, ok := b.InlineCopy(other); ok || reflect.ValueOf(got).UnsafePointer() != reflect.ValueOf(other).UnsafePointer() {
		t.Fatal("a row without a fallback must be returned as it is")
	}
	b.RestoreInline()
	if got, ok := b.InlineCopy(row); ok || got["_version"] != uint64(41) {
		t.Fatal("a row already restored inline must not be copied or re-versioned")
	}
}

// TestInlineCopyConcurrentReaders: the writer calls the dead-letter hook from
// concurrent inserts (run with -race).
func TestInlineCopyConcurrentReaders(t *testing.T) {
	row, _, b := compactedTestRow(t)
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, ok := b.InlineCopy(row); !ok {
				t.Error("copy failed")
			}
			_ = b.CatalogRow(row)
		}()
	}
	wg.Wait()
}

// TestCatalogRowIsDedupOffAttributes: the catalog view of a compacted row has
// the dedup-off attributes_extra; the row itself keeps its sentinels. Merging
// batches keeps the lookup.
func TestCatalogRowIsDedupOffAttributes(t *testing.T) {
	row, devRow, b := compactedTestRow(t)
	merged := NewBatch()
	merged.Merge(b)
	for _, batch := range []*Batch{b, merged} {
		view := batch.CatalogRow(row)
		if !reflect.DeepEqual(view["attributes_extra"], devRow["attributes_extra"]) {
			t.Fatalf("catalog attributes %v, want %v", view["attributes_extra"], devRow["attributes_extra"])
		}
		if !reflect.DeepEqual(view["attrs_string"], devRow["attrs_string"]) {
			t.Fatal("catalog attrs_string differs")
		}
	}
	if _, ok := row["attributes_extra"].(map[string]any)[SentinelPromotedInputKey]; !ok {
		t.Fatal("the stored row must keep its sentinels")
	}
	plain := map[string]any{"id": "x"}
	if got := b.CatalogRow(plain); reflect.ValueOf(got).UnsafePointer() != reflect.ValueOf(plain).UnsafePointer() {
		t.Fatal("a row without a fallback is catalogued as it is")
	}
}

// TestCompactPromotedMissingKey: a key absent from attributes_extra compacts
// nothing.
func TestCompactPromotedMissingKey(t *testing.T) {
	extra := map[string]any{"a": 1}
	if CompactPromoted(extra, "input.value", false, true) != nil || len(extra) != 1 {
		t.Fatal("nothing may change")
	}
	if !HasSentinelKey(map[string]string{SentinelPromotedOutputJSON: "x"}) || HasSentinelKey(extra) {
		t.Fatal("HasSentinelKey")
	}
}
