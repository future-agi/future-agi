package clickhouse25exporter

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

func buildConversationTrace(projectID, orgID, sessionID string, traceByte byte, spanByte byte, inputJSON, outputJSON string, includeFlattened bool) ptrace.Traces {
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "agent-svc")
	rs.Resource().Attributes().PutStr("fi.project_id", projectID)
	rs.Resource().Attributes().PutStr("fi.org_id", orgID)
	rs.Resource().Attributes().PutStr("project_type", "observe")

	ss := rs.ScopeSpans().AppendEmpty()
	sp := ss.Spans().AppendEmpty()
	var tid [16]byte
	tid[0] = traceByte
	tid[15] = 0x01
	var sid [8]byte
	sid[0] = spanByte
	sid[7] = 0x02
	sp.SetTraceID(pcommon.TraceID(tid))
	sp.SetSpanID(pcommon.SpanID(sid))
	sp.SetName("llm.chat")
	now := time.Now().UTC()
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(now))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(now.Add(250 * time.Millisecond)))
	sp.Attributes().PutStr("fi.span.kind", "LLM")
	sp.Attributes().PutStr("session.id", sessionID)
	sp.Attributes().PutStr("input.value", inputJSON)
	sp.Attributes().PutStr("output.value", outputJSON)
	if includeFlattened {
		sp.Attributes().PutStr("llm.input_messages.0.message.role", "system")
		sp.Attributes().PutStr("llm.input_messages.0.message.content", "You are a coding agent.")
		sp.Attributes().PutStr("llm.input_messages.1.message.role", "user")
		sp.Attributes().PutStr("llm.input_messages.1.message.content", "Fix tests")
	}
	return td
}

func TestConvertWithOptionsPhase0Compaction(t *testing.T) {
	pid := "11111111-1111-4111-8111-111111111111"
	oid := "99999999-9999-4999-8999-999999999999"
	inPayload := `{"messages":[{"role":"user","content":"` + strings.Repeat("hello ", 500) + `"}]}`
	outPayload := `{"choices":[{"message":{"role":"assistant","content":"world"}}]}`

	td := buildConversationTrace(pid, oid, "sess-p0", 0xaa, 0x01, inPayload, outPayload, false)
	res, err := ConvertWithOptions(context.Background(), td, nil, ConvertOptions{
		Trajectory: trajectory.Config{CompactSpanAttrs: true, Mode: trajectory.ModeOff},
	})
	if err != nil {
		t.Fatalf("ConvertWithOptions: %v", err)
	}
	if len(res.Rows) != 1 {
		t.Fatalf("got %d rows, want 1", len(res.Rows))
	}
	row := res.Rows[0]
	if row["input"] != inPayload || row["output"] != outPayload {
		t.Fatalf("Phase 0 must preserve full spans.input/output")
	}
	of := row["attributes_extra"].(map[string]any)
	if _, dup := of["input.value"]; dup {
		t.Errorf("Phase 0 failed to remove duplicate input.value from attributes_extra")
	}
	if _, dup := of["output.value"]; dup {
		t.Errorf("Phase 0 failed to remove duplicate output.value from attributes_extra")
	}
	if of[trajectory.SentinelPromotedInputKey] != "input.value" {
		t.Errorf("missing input sentinel: %v", of)
	}
	if of[trajectory.SentinelPromotedOutputKey] != "output.value" {
		t.Errorf("missing output sentinel: %v", of)
	}
	if _, hasEnc := row["payload_encoding"]; hasEnc {
		t.Errorf("Phase 0 must NOT emit Phase 1 payload_encoding column when Mode=off")
	}
	if res.Trajectory != nil && !res.Trajectory.Empty() {
		t.Errorf("Phase 0 must NOT produce trajectory_items")
	}
}

func TestConvertWithOptionsPhase1DedupAndCanary(t *testing.T) {
	canaryProj := "11111111-1111-4111-8111-111111111111"
	otherProj := "22222222-2222-4222-8222-222222222222"
	oid := "99999999-9999-4999-8999-999999999999"

	cfg := trajectory.Config{
		Mode:              trajectory.ModeCanary,
		CanaryProjectList: []string{canaryProj},
		PreviewMaxBytes:   128,
	}.Normalize()
	seen := trajectory.NewSeenStore(nil, 10_000, time.Hour, time.Hour)

	// Non-canary project stays completely inline (no Phase 1 columns).
	in1 := `{"messages":[{"role":"system","content":"System prompt"},{"role":"user","content":"Hello"}]}`
	out1 := `{"choices":[{"index":0,"message":{"role":"assistant","content":"Hi!"}}]}`
	tdOther := buildConversationTrace(otherProj, oid, "sess-other", 0xbb, 0x01, in1, out1, true)
	resOther, err := ConvertWithOptions(context.Background(), tdOther, nil, ConvertOptions{Trajectory: cfg, Seen: seen})
	if err != nil {
		t.Fatalf("ConvertWithOptions(other): %v", err)
	}
	if _, exists := resOther.Rows[0]["payload_encoding"]; exists {
		t.Fatalf("non-canary project should not have payload_encoding set")
	}
	if resOther.Usage.DedupApplied {
		t.Fatalf("non-canary project should have DedupApplied=false")
	}

	// Canary project: 5-turn conversation with growing message history.
	msgs := []map[string]any{
		{"role": "system", "content": "You are a coding agent. " + strings.Repeat("details ", 100)},
	}
	for turn := 1; turn <= 5; turn++ {
		msgs = append(msgs, map[string]any{"role": "user", "content": fmt.Sprintf("Turn %d question %s", turn, strings.Repeat("q", 200))})
		inBytes, _ := json.Marshal(map[string]any{"model": "gpt-4o", "messages": msgs})
		reply := map[string]any{"role": "assistant", "content": fmt.Sprintf("Turn %d answer %s", turn, strings.Repeat("a", 200))}
		outBytes, _ := json.Marshal(map[string]any{"choices": []any{map[string]any{"index": 0, "message": reply}}})

		td := buildConversationTrace(canaryProj, oid, "sess-canary", 0xcc, byte(turn), string(inBytes), string(outBytes), true)
		res, err := ConvertWithOptions(context.Background(), td, nil, ConvertOptions{Trajectory: cfg, Seen: seen})
		if err != nil {
			t.Fatalf("turn %d: %v", turn, err)
		}
		row := res.Rows[0]
		if row["payload_encoding"] != trajectory.EncodingItemsV1 {
			t.Fatalf("turn %d: payload_encoding = %v, want %q", turn, row["payload_encoding"], trajectory.EncodingItemsV1)
		}
		if len(row["input"].(string)) > 128 {
			t.Fatalf("turn %d: preview input exceeds 128B: %d", turn, len(row["input"].(string)))
		}
		// Flattened llm.input_messages.* should be stripped from both attrs_string and attributes_extra.
		of := row["attributes_extra"].(map[string]any)
		as := row["attrs_string"].(map[string]string)
		for k := range of {
			if strings.HasPrefix(k, "llm.input_messages.") {
				t.Errorf("turn %d: flattened key %q was not stripped from attributes_extra", turn, k)
			}
		}
		for k := range as {
			if strings.HasPrefix(k, "llm.input_messages.") {
				t.Errorf("turn %d: flattened key %q was not stripped from attrs_string", turn, k)
			}
		}
		// Trace root row MUST retain full lossless input/output (never truncated to preview!).
		traces := res.Identities.Traces()
		if len(traces) != 1 || traces[0].Input != string(inBytes) {
			t.Fatalf("turn %d: traces row did not retain lossless full input (got len %d, want %d)",
				turn, len(traces[0].Input), len(inBytes))
		}
		if turn > 1 && res.Usage.ReusedItems == 0 {
			t.Fatalf("turn %d: expected ReusedItems > 0, got 0", turn)
		}
		// Simulate durable ClickHouse commit before next turn arrives.
		seen.MarkCommitted(context.Background(), res.Trajectory.Items())
		if turn == 5 {
			// Verify RestoreInline reverts everything cleanly.
			res.Trajectory.RestoreInline()
			if row["input"] != string(inBytes) || row["output"] != string(outBytes) {
				t.Fatalf("RestoreInline failed to restore original input/output")
			}
			_, inOf := of["llm.input_messages.0.message.content"]
			_, inAs := as["llm.input_messages.0.message.content"]
			if !inOf && !inAs {
				t.Fatalf("RestoreInline failed to restore stripped flattened attributes")
			}
		}
		msgs = append(msgs, reply)
	}
}
