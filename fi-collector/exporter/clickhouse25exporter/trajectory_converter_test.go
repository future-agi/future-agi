package clickhouse25exporter

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
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

// TestDormantCompactEnvDoesNotStripInputValue: standalone compaction is gone.
// Even a config that sets CompactSpanAttrs without going through Validate
// (which rejects it) must produce the dev row: input.value / output.value stay
// in attributes_extra and no sentinel is written.
func TestDormantCompactEnvDoesNotStripInputValue(t *testing.T) {
	pid := "11111111-1111-4111-8111-111111111111"
	oid := "99999999-9999-4999-8999-999999999999"
	inPayload := `{"messages":[{"role":"user","content":"` + strings.Repeat("hello ", 500) + `"}]}`
	outPayload := `{"choices":[{"message":{"role":"assistant","content":"world"}}]}`

	for _, opts := range []ConvertOptions{
		{Trajectory: trajectory.Config{CompactSpanAttrs: true, Mode: trajectory.ModeOff}},
		{Trajectory: trajectory.Config{CompactSpanAttrs: true, Mode: trajectory.ModeOff}, Gate: trajectory.FixedGate(true)},
	} {
		td := buildConversationTrace(pid, oid, "sess-p0", 0xaa, 0x01, inPayload, outPayload, false)
		dev := buildConversationTrace(pid, oid, "sess-p0", 0xaa, 0x01, inPayload, outPayload, false)
		res, err := ConvertWithOptions(context.Background(), td, nil, opts)
		if err != nil {
			t.Fatalf("ConvertWithOptions: %v", err)
		}
		want, _, err := ConvertWithIdentities(context.Background(), dev, nil)
		if err != nil {
			t.Fatalf("ConvertWithIdentities: %v", err)
		}
		row := res.Rows[0]
		of := row["attributes_extra"].(map[string]any)
		if _, ok := of["input.value"]; !ok {
			t.Fatalf("input.value was stripped from attributes_extra without dedup")
		}
		if _, ok := of[trajectory.SentinelPromotedInputKey]; ok {
			t.Fatalf("a compaction sentinel was written without dedup")
		}
		assertRowsEqualIgnoringVersion(t, res.Rows, want)
		if res.Trajectory != nil || res.Usage.DedupApplied {
			t.Fatalf("no trajectory work may happen with dedup off")
		}
	}
}

// assertRowsEqualIgnoringVersion compares converted rows; _version is the
// wall-clock insert stamp and differs between two conversions.
func assertRowsEqualIgnoringVersion(t *testing.T, got, want []map[string]any) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("got %d rows, want %d", len(got), len(want))
	}
	for i := range got {
		g, w := map[string]any{}, map[string]any{}
		for k, v := range got[i] {
			g[k] = v
		}
		for k, v := range want[i] {
			w[k] = v
		}
		delete(g, "_version")
		delete(w, "_version")
		gb, _ := json.Marshal(g)
		wb, _ := json.Marshal(w)
		if string(gb) != string(wb) {
			t.Fatalf("row %d differs from the dedup-off row:\n got %s\nwant %s", i, gb, wb)
		}
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
	resOther, err := ConvertWithOptions(context.Background(), tdOther, nil, ConvertOptions{Trajectory: cfg, Seen: seen, Gate: trajectory.FixedGate(true)})
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
		res, err := ConvertWithOptions(context.Background(), td, nil, ConvertOptions{Trajectory: cfg, Seen: seen, Gate: trajectory.FixedGate(true)})
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

// TestConvertWithOptionsConcurrentCanary: a canary config normalized once (as
// server.WithTrajectory does) is shared by concurrent requests. Before the fix
// every ConvertWithOptions re-normalized it and wrote into the shared
// CanaryProjects map, which the Go runtime aborts on ("concurrent map
// writes"), even without -race.
func TestConvertWithOptionsConcurrentCanary(t *testing.T) {
	pid := "11111111-1111-4111-8111-111111111111"
	oid := "99999999-9999-4999-8999-999999999999"
	cfg := trajectory.Config{Mode: trajectory.ModeCanary, CanaryProjectList: []string{pid}}.Normalize()
	seen := trajectory.NewSeenStore(nil, 10_000, time.Hour, time.Hour)
	in := `{"messages":[{"role":"system","content":"System prompt ` + strings.Repeat("s", 200) + `"},{"role":"user","content":"Hello"}]}`
	out := `{"choices":[{"index":0,"message":{"role":"assistant","content":"Hi"}}]}`
	var wg sync.WaitGroup
	errs := make(chan error, 32)
	for g := 0; g < 32; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; i < 50; i++ {
				td := buildConversationTrace(pid, oid, fmt.Sprintf("sess-%d", g), byte(g), byte(i), in, out, false)
				res, err := ConvertWithOptions(context.Background(), td, nil, ConvertOptions{Trajectory: cfg, Seen: seen, Gate: trajectory.FixedGate(true)})
				if err != nil {
					errs <- err
					return
				}
				if res.Rows[0]["payload_encoding"] != trajectory.EncodingItemsV1 {
					errs <- fmt.Errorf("canary project was not deduplicated")
					return
				}
			}
		}(g)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatal(err)
	}
	if len(cfg.CanaryProjects) != 1 {
		t.Fatalf("the shared canary map changed: %v", cfg.CanaryProjects)
	}
}

// TestConvertDedupProjectGateClosedRowsByteIdenticalToDev: a dedup project
// whose gate is closed (or absent) gets the dev row, byte for byte: no pointer
// columns, no compaction, no trajectory batch, no dedup usage.
func TestConvertDedupProjectGateClosedRowsByteIdenticalToDev(t *testing.T) {
	pid := "11111111-1111-4111-8111-111111111111"
	oid := "99999999-9999-4999-8999-999999999999"
	in := `{"messages":[{"role":"system","content":"System prompt ` + strings.Repeat("s", 600) + `"},{"role":"user","content":"Hello"}]}`
	out := `{"choices":[{"index":0,"message":{"role":"assistant","content":"Hi"}}]}`
	cfg := trajectory.Config{Mode: trajectory.ModeOn}.Normalize()
	for name, gate := range map[string]trajectory.DedupGate{"closed": trajectory.FixedGate(false), "absent": nil} {
		t.Run(name, func(t *testing.T) {
			td := buildConversationTrace(pid, oid, "sess", 0x31, 0x01, in, out, true)
			dev := buildConversationTrace(pid, oid, "sess", 0x31, 0x01, in, out, true)
			res, err := ConvertWithOptions(context.Background(), td, nil, ConvertOptions{Trajectory: cfg, Seen: trajectory.NewSeenStore(nil, 100, time.Hour, time.Hour), Gate: gate})
			if err != nil {
				t.Fatal(err)
			}
			want, _, err := ConvertWithIdentities(context.Background(), dev, nil)
			if err != nil {
				t.Fatal(err)
			}
			assertRowsEqualIgnoringVersion(t, res.Rows, want)
			if res.Trajectory != nil || res.Usage.DedupApplied {
				t.Fatal("a closed gate must not produce trajectory work")
			}
		})
	}
	// The same request with the gate open is deduplicated, so the comparison
	// above is not vacuous.
	td := buildConversationTrace(pid, oid, "sess", 0x31, 0x01, in, out, true)
	res, err := ConvertWithOptions(context.Background(), td, nil, ConvertOptions{Trajectory: cfg, Gate: trajectory.FixedGate(true)})
	if err != nil {
		t.Fatal(err)
	}
	if res.Rows[0]["payload_encoding"] != trajectory.EncodingItemsV1 {
		t.Fatal("open gate: the span must be extracted")
	}
}
