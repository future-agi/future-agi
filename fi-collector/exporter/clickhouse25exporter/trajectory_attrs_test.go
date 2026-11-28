package clickhouse25exporter

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

const (
	attrsTestPID = "11111111-1111-4111-8111-111111111111"
	attrsTestOID = "99999999-9999-4999-8999-999999999999"
)

// attrsTrace is one LLM span carrying attrs exactly as given (strings), with
// optional map-valued attributes.
func attrsTrace(t *testing.T, attrs map[string]string, maps map[string]map[string]any) ptrace.Traces {
	t.Helper()
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "agent-svc")
	rs.Resource().Attributes().PutStr("fi.project_id", attrsTestPID)
	rs.Resource().Attributes().PutStr("fi.org_id", attrsTestOID)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetTraceID(pcommon.TraceID([16]byte{0x7a, 15: 0x01}))
	sp.SetSpanID(pcommon.SpanID([8]byte{0x7b, 7: 0x02}))
	sp.SetName("llm.chat")
	now := time.Now().UTC()
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(now))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(now.Add(time.Second)))
	sp.Attributes().PutStr("fi.span.kind", "LLM")
	sp.Attributes().PutStr("session.id", "sess-attrs")
	for k, v := range attrs {
		sp.Attributes().PutStr(k, v)
	}
	for k, v := range maps {
		if err := sp.Attributes().PutEmptyMap(k).FromRaw(v); err != nil {
			t.Fatal(err)
		}
	}
	return td
}

// convertOnAndOff converts the same trace with dedup on (open gate) and off.
func convertOnAndOff(t *testing.T, td ptrace.Traces) (on ConvertResult, dev map[string]any) {
	t.Helper()
	devTraces := ptrace.NewTraces()
	td.CopyTo(devTraces)
	res, err := ConvertWithOptions(context.Background(), td, nil, ConvertOptions{
		Trajectory: trajectory.Config{Mode: trajectory.ModeOn}.Normalize(), Gate: trajectory.FixedGate(true),
	})
	if err != nil {
		t.Fatal(err)
	}
	devRows, _, err := ConvertWithIdentities(context.Background(), devTraces, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Rows) != 1 || len(devRows) != 1 {
		t.Fatalf("rows on=%d off=%d", len(res.Rows), len(devRows))
	}
	return res, devRows[0]
}

func messagesJSON(t *testing.T, msgs ...map[string]any) string {
	t.Helper()
	b, err := json.Marshal(map[string]any{"model": "gpt-4o", "messages": msgs})
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func choicesJSON(t *testing.T, reply string) string {
	t.Helper()
	b, err := json.Marshal(map[string]any{"choices": []any{map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": reply}}}})
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// withoutKeys returns a copy of m without keys.
func withoutKeys(m map[string]any, keys ...string) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	for _, k := range keys {
		delete(out, k)
	}
	return out
}

// TestConvertWithOptionsKeepsFlattenedAttrsBothSides: with both sides
// extracted, every flattened message/tool key (llm.input_messages.*,
// llm.output_messages.*, gen_ai.prompt.N.*, gen_ai.completion.N.*,
// llm.tools.N.*, and the llm.prompts overflow key) stays exactly as on dev.
// Only the input.value/output.value copies leave attributes_extra, replaced by
// the sentinels. Before the fix these keys were stripped.
func TestConvertWithOptionsKeepsFlattenedAttrsBothSides(t *testing.T) {
	in := messagesJSON(t, map[string]any{"role": "system", "content": "Be brief. " + strings.Repeat("s", 600)}, map[string]any{"role": "user", "content": "Hello"})
	out := choicesJSON(t, "Hi there "+strings.Repeat("a", 600))
	attrs := map[string]string{
		"input.value":                          in,
		"output.value":                         out,
		"llm.input_messages.0.message.role":    "system",
		"llm.input_messages.0.message.content": "Be brief.",
		"llm.input_messages.1.message.role":    "user",
		"llm.output_messages.0.message.role":   "assistant",
		"gen_ai.prompt.0.content":              "Hello",
		"gen_ai.completion.0.finish_reason":    "stop",
		"llm.tools.0.tool.json_schema":         `{"name":"lookup"}`,
		"gen_ai.input.messages.0.message.role": "user",
		"llm.prompts.0":                        "template",
	}
	res, dev := convertOnAndOff(t, attrsTrace(t, attrs, nil))
	row := res.Rows[0]
	if row["payload_encoding"] != trajectory.EncodingItemsV1 {
		t.Fatal("the span must be extracted")
	}
	if !reflect.DeepEqual(row["attrs_string"], dev["attrs_string"]) {
		t.Fatalf("attrs_string\n%v\nwant dev\n%v", row["attrs_string"], dev["attrs_string"])
	}
	devExtra := dev["attributes_extra"].(map[string]any)
	if _, ok := devExtra["llm.prompts.0"]; !ok {
		t.Fatal("fixture: llm.prompts.0 must land in attributes_extra")
	}
	want := withoutKeys(devExtra, "input.value", "output.value")
	want[trajectory.SentinelPromotedInputKey] = "input.value"
	want[trajectory.SentinelPromotedOutputKey] = "output.value"
	if got := row["attributes_extra"]; !reflect.DeepEqual(got, want) {
		t.Fatalf("attributes_extra\n%v\nwant\n%v", got, want)
	}
}

// TestPhase0NeverStripsAttrsString: content read from an attrs_string key
// (gen_ai.prompt, span.output) is extracted, but attrs_string is never changed
// and no sentinel is written for those sides: attrs_string and
// attributes_extra are the dev maps. Before the fix the key was deleted from
// attrs_string.
func TestPhase0NeverStripsAttrsString(t *testing.T) {
	prompt := `[{"role":"system","content":"` + strings.Repeat("rules ", 120) + `"},{"role":"user","content":"Hi"}]`
	completion := choicesJSON(t, "Hello "+strings.Repeat("b", 700))
	attrs := map[string]string{"gen_ai.prompt": prompt, "span.output": completion, "gen_ai.prompt.0.content": "Hi"}
	res, dev := convertOnAndOff(t, attrsTrace(t, attrs, nil))
	row := res.Rows[0]
	if row["payload_encoding"] != trajectory.EncodingItemsV1 || dev["input"] != prompt {
		t.Fatalf("fixture: the gen_ai.prompt input must be extracted (encoding %v)", row["payload_encoding"])
	}
	if !reflect.DeepEqual(row["attrs_string"], dev["attrs_string"]) {
		t.Fatalf("attrs_string changed:\n%v\nwant\n%v", row["attrs_string"], dev["attrs_string"])
	}
	if !reflect.DeepEqual(row["attributes_extra"], dev["attributes_extra"]) {
		t.Fatalf("attributes_extra changed:\n%v\nwant\n%v", row["attributes_extra"], dev["attributes_extra"])
	}
	if res.Usage.OrigExtractedBytes != 0 {
		t.Fatalf("content still stored in attrs_string earns no storage credit, got %d", res.Usage.OrigExtractedBytes)
	}
}

// TestConvertDedupDoesNotCompactUnextractedSide: the input is a message array
// (extracted) and the output plain text (not extracted). Only input.value
// leaves attributes_extra; output.value stays with no output sentinel and
// spans.output is the dev value. Before the fix both sides were compacted.
func TestConvertDedupDoesNotCompactUnextractedSide(t *testing.T) {
	in := messagesJSON(t, map[string]any{"role": "system", "content": strings.Repeat("policy ", 120)}, map[string]any{"role": "user", "content": "Question"})
	attrs := map[string]string{"input.value": in, "output.value": "plain text answer"}
	res, dev := convertOnAndOff(t, attrsTrace(t, attrs, nil))
	row := res.Rows[0]
	extra := row["attributes_extra"].(map[string]any)
	if extra["output.value"] != "plain text answer" {
		t.Fatalf("output.value of the inline side was removed: %v", extra)
	}
	for _, k := range []string{trajectory.SentinelPromotedOutputKey, trajectory.SentinelPromotedOutputJSON} {
		if _, ok := extra[k]; ok {
			t.Fatalf("output sentinel %s written for a side that was not extracted", k)
		}
	}
	if _, ok := extra["input.value"]; ok || extra[trajectory.SentinelPromotedInputKey] != "input.value" {
		t.Fatalf("the extracted input must be compacted: %v", extra)
	}
	if row["output"] != dev["output"] {
		t.Fatal("the inline output differs from dev")
	}
	if res.Usage.OrigExtractedBytes != int64(len(in)) {
		t.Fatalf("credit %d, want only the extracted input's %d bytes", res.Usage.OrigExtractedBytes, len(in))
	}
	// Kept overhead is the input preview and the pointer columns; the inline
	// output is neither credited nor counted as overhead.
	overhead := len(row["input"].(string)) + len(row["item_scope_id"].(string)) + len(row["payload_envelope"].(string))
	for _, col := range []string{"input_item_hashes", "output_item_hashes", "tool_def_hashes"} {
		for _, h := range row[col].([]string) {
			overhead += len(h)
		}
	}
	if res.Usage.KeptSpanOverheadBytes != int64(overhead) {
		t.Fatalf("kept overhead %d, want %d (input preview + pointers only)", res.Usage.KeptSpanOverheadBytes, overhead)
	}
	// Restoring gives the dev row back, sentinel-free.
	res.Trajectory.RestoreInline()
	assertDeepEqualIgnoringVersion(t, row, dev)
}

// TestConvertDedupNonConversationSpanIsDevRow: plain text on both sides is
// not extracted, so a dedup project's row is the dev row and no trajectory
// work happens.
func TestConvertDedupNonConversationSpanIsDevRow(t *testing.T) {
	attrs := map[string]string{"input.value": "what is the weather", "output.value": "sunny", "llm.input_messages.0.message.content": "what is the weather"}
	res, dev := convertOnAndOff(t, attrsTrace(t, attrs, nil))
	assertDeepEqualIgnoringVersion(t, res.Rows[0], dev)
	if res.Trajectory != nil || res.Usage.DedupApplied {
		t.Fatal("a non-conversation span must not produce trajectory work")
	}
}

// TestRestoreInlineMapValuedAttrIsDevRow: input.value sent as an OTLP map is
// compacted with the JSON flag; RestoreInline puts back the same Go value
// (a map, not its JSON text), so the row deep-equals the dev row.
func TestRestoreInlineMapValuedAttrIsDevRow(t *testing.T) {
	inMap := map[string]any{"model": "gpt-4o", "messages": []any{
		map[string]any{"role": "system", "content": strings.Repeat("policy ", 120)},
		map[string]any{"role": "user", "content": "Question"},
	}}
	res, dev := convertOnAndOff(t, attrsTrace(t, map[string]string{"output.value": choicesJSON(t, "ok")}, map[string]map[string]any{"input.value": inMap}))
	row := res.Rows[0]
	extra := row["attributes_extra"].(map[string]any)
	if extra[trajectory.SentinelPromotedInputJSON] != true {
		t.Fatalf("fixture: the map-valued input must be extracted and flagged JSON: %v", extra)
	}
	res.Trajectory.RestoreInline()
	assertDeepEqualIgnoringVersion(t, row, dev)
}

// TestConvertSkipsCompactionWhenSentinelNameIsAnAttribute: a producer
// attribute named like a sentinel is never overwritten or deleted.
func TestConvertSkipsCompactionWhenSentinelNameIsAnAttribute(t *testing.T) {
	in := messagesJSON(t, map[string]any{"role": "system", "content": strings.Repeat("policy ", 120)}, map[string]any{"role": "user", "content": "Question"})
	attrs := map[string]string{"input.value": in, "output.value": choicesJSON(t, "ok"), trajectory.SentinelPromotedInputKey: "mine"}
	res, dev := convertOnAndOff(t, attrsTrace(t, attrs, nil))
	row := res.Rows[0]
	if row["payload_encoding"] != trajectory.EncodingItemsV1 {
		t.Fatal("the span is still extracted")
	}
	if !reflect.DeepEqual(row["attributes_extra"], dev["attributes_extra"]) || !reflect.DeepEqual(row["attrs_string"], dev["attrs_string"]) {
		t.Fatal("attributes must stay the dev maps")
	}
	res.Trajectory.RestoreInline()
	assertDeepEqualIgnoringVersion(t, row, dev)
}

// TestUsageReportCountsOnlyRemovedBytes is the worked OpenInference turn from
// the billing review: input.value 40,000 B with 38,000 B of flattened input
// messages, output.value 1,000 B with 900 B flattened, all items already
// stored. The credit is each extracted side once (raw carries it once), never
// the flattened bytes, so the bill never falls below the bytes still stored
// on the span (the flattened copies and every other attribute).
func TestUsageReportCountsOnlyRemovedBytes(t *testing.T) {
	sys := strings.Repeat("S", 20_000)
	user := strings.Repeat("U", 19_900)
	in := messagesJSON(t, map[string]any{"role": "system", "content": sys}, map[string]any{"role": "user", "content": user})
	out := choicesJSON(t, strings.Repeat("A", 900))
	attrs := map[string]string{
		"input.value":                           in,
		"output.value":                          out,
		"llm.input_messages.0.message.content":  sys[:19_000],
		"llm.input_messages.1.message.content":  user[:19_000],
		"llm.output_messages.0.message.content": strings.Repeat("A", 900),
		"custom.other":                          strings.Repeat("o", 1_500),
	}
	cfg := trajectory.Config{Mode: trajectory.ModeOn}.Normalize()
	seen := trajectory.NewSeenStore(nil, 1000, time.Hour, time.Hour)
	// Turn 1 stores the items; turn 2 (same payload) reuses all of them.
	var res ConvertResult
	var raw int64
	for turn := 0; turn < 2; turn++ {
		td := attrsTrace(t, attrs, nil)
		wire, err := ptraceotlp.NewExportRequestFromTraces(td).MarshalProto()
		if err != nil {
			t.Fatal(err)
		}
		raw = int64(len(wire))
		res, err = ConvertWithOptions(context.Background(), td, nil, ConvertOptions{Trajectory: cfg, Seen: seen, Gate: trajectory.FixedGate(true)})
		if err != nil {
			t.Fatal(err)
		}
		seen.MarkCommitted(context.Background(), res.Trajectory.Items())
	}
	r := res.Usage
	r.RawBytes = raw
	if r.NewItems != 0 || r.ReusedItems == 0 {
		t.Fatalf("turn 2 must reuse every item: new=%d reused=%d", r.NewItems, r.ReusedItems)
	}
	if want := int64(len(in) + len(out)); r.OrigExtractedBytes != want {
		t.Fatalf("credit %d, want len(input)+len(output) = %d (flattened bytes are stored, the attribute copy is in raw once)", r.OrigExtractedBytes, want)
	}
	flattened := int64(19_000 + 19_000 + 900 + 1_500)
	if stored := r.StoredTotalBytes(); stored < flattened {
		t.Fatalf("billed %d B, below the %d B of attributes still stored on the span", stored, flattened)
	}
}

// assertDeepEqualIgnoringVersion compares two rows with reflect.DeepEqual,
// so Go value types count (a restored map must be a map).
func assertDeepEqualIgnoringVersion(t *testing.T, got, want map[string]any) {
	t.Helper()
	g, w := withoutKeys(got, "_version"), withoutKeys(want, "_version")
	if !reflect.DeepEqual(g, w) {
		gb, _ := json.Marshal(g)
		wb, _ := json.Marshal(w)
		t.Fatalf("row differs from the dedup-off row:\n got %s\nwant %s", gb, wb)
	}
}
