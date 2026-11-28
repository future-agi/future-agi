package server

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/chwriter"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"go.opentelemetry.io/collector/pdata/ptrace/ptraceotlp"
)

// devTwin is a collector with no trajectory configuration over its own fake
// ClickHouse: what dev stores for the same requests.
type devTwin struct {
	ch  *fakeTrajectoryCH
	srv *Server
}

func newDevTwin(t *testing.T) *devTwin {
	t.Helper()
	ch := &fakeTrajectoryCH{}
	chSrv := httptest.NewServer(ch.handler())
	t.Cleanup(chSrv.Close)
	w, err := chwriter.New(chwriter.Config{URL: chSrv.URL, Database: "default", Table: "spans", MaxRetries: 1,
		InitialBackoff: time.Millisecond, MaxBackoff: time.Millisecond, DeadLetterFile: filepath.Join(t.TempDir(), "dev-dlq.jsonl")})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { w.Close() })
	return &devTwin{ch: ch, srv: New(Config{BatchMaxRows: 100, BatchMaxAge: time.Minute}, w, nil, NoopUsageEmitter{}, NoopMetering{},
		WithLogger(slog.New(slog.NewTextHandler(io.Discard, nil))))}
}

// attrsTurn is agentTurn with flattened OpenInference message keys added to
// the span, so a stripped flattened key would show.
func attrsTurn(t *testing.T, turn int) []byte {
	t.Helper()
	req := ptraceotlp.NewExportRequest()
	if err := req.UnmarshalProto(agentTurn(t, turn)); err != nil {
		t.Fatal(err)
	}
	attrs := req.Traces().ResourceSpans().At(0).ScopeSpans().At(0).Spans().At(0).Attributes()
	attrs.PutStr("llm.input_messages.0.message.role", "system")
	attrs.PutStr("llm.input_messages.0.message.content", "System prompt "+strings.Repeat("rules ", 120))
	attrs.PutStr("llm.output_messages.0.message.content", "Reply")
	attrs.PutStr("gen_ai.completion.0.finish_reason", "stop")
	wire, err := req.MarshalProto()
	if err != nil {
		t.Fatal(err)
	}
	return wire
}

// rowsWithoutVersion drops _version (a wall-clock stamp per conversion).
func rowsWithoutVersion(rows []map[string]any) []map[string]any {
	out := make([]map[string]any, len(rows))
	for i, r := range rows {
		c := make(map[string]any, len(r))
		for k, v := range r {
			if k != "_version" {
				c[k] = v
			}
		}
		out[i] = c
	}
	return out
}

func requireDevRows(t *testing.T, got, want []map[string]any) {
	t.Helper()
	g, w := rowsWithoutVersion(got), rowsWithoutVersion(want)
	if !reflect.DeepEqual(g, w) {
		gb, _ := json.Marshal(g)
		wb, _ := json.Marshal(w)
		t.Fatalf("stored rows differ from the dedup-off rows:\n got %s\nwant %s", gb, wb)
	}
}

// TestRestoreInlineYieldsDevRow: when the trajectory_items insert fails, the
// spans rows the drain sends are the dedup-off rows exactly: full input and
// output, input.value/output.value back in attributes_extra, no sentinel and
// no pointer column. Before the fix the sentinels stayed and the promoted
// keys were missing.
func TestRestoreInlineYieldsDevRow(t *testing.T) {
	ch := &fakeTrajectoryCH{failTrajItem: true}
	f := newTrajFixture(t, trajectory.Config{Mode: trajectory.ModeOn}, ch, true)
	dev := newDevTwin(t)
	body := attrsTurn(t, 2)
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, body)
	f.srv.drainNow(context.Background())
	postTrajOTLP(t, dev.srv, trajTestOID, trajTestPID, body)
	dev.srv.drainNow(context.Background())
	got := insertsInto(ch.snapshot(), "spans")
	want := insertsInto(dev.ch.snapshot(), "spans")
	if len(got) != 1 || len(want) != 1 {
		t.Fatalf("spans inserts dedup=%d dev=%d", len(got), len(want))
	}
	requireDevRows(t, got[0].Rows, want[0].Rows)
}

// TestObservedCatalogSameWithDedupOnAndOff: the observed catalog receives the
// same attributes for a compacted span as dedup off gives (the promoted
// input.value/output.value keys present, no __fi_promoted_* key), so the
// extracted catalog batch is identical. Before the fix the catalog was built
// from the compacted row.
func TestObservedCatalogSameWithDedupOnAndOff(t *testing.T) {
	capture := func(dst *[]observedcatalog.ScopedSpan, mu *sync.Mutex) PropertyCatalogWriter {
		return correctnessCatalog(func(rows []observedcatalog.ScopedSpan) error {
			mu.Lock()
			defer mu.Unlock()
			*dst = append(*dst, rows...)
			return nil
		})
	}
	var mu sync.Mutex
	var onSpans, offSpans []observedcatalog.ScopedSpan
	ch := &fakeTrajectoryCH{}
	f := newTrajFixture(t, trajectory.Config{Mode: trajectory.ModeOn}, ch, true)
	f.srv.propertyCatalog = capture(&onSpans, &mu)
	dev := newDevTwin(t)
	dev.srv.propertyCatalog = capture(&offSpans, &mu)

	body := attrsTurn(t, 3)
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, body)
	f.srv.drainNow(context.Background())
	postTrajOTLP(t, dev.srv, trajTestOID, trajTestPID, body)
	dev.srv.drainNow(context.Background())

	spans := insertsInto(ch.snapshot(), "spans")
	if len(spans) != 1 || spans[0].Rows[0]["payload_encoding"] != trajectory.EncodingItemsV1 {
		t.Fatal("fixture: the dedup collector must store a pointer row")
	}
	if _, ok := spans[0].Rows[0]["attributes_extra"].(map[string]any)[trajectory.SentinelPromotedInputKey]; !ok {
		t.Fatal("fixture: the stored row must be compacted")
	}
	mu.Lock()
	defer mu.Unlock()
	if len(onSpans) != 1 || len(offSpans) != 1 {
		t.Fatalf("catalog spans dedup=%d dev=%d", len(onSpans), len(offSpans))
	}
	extract := func(span observedcatalog.ScopedSpan) observedcatalog.Batch {
		b, report, err := observedcatalog.Extract(span, observedcatalog.DefaultLimits())
		if err != nil {
			t.Fatal(err)
		}
		_ = report
		return observedcatalog.Merge(b)
	}
	on, off := extract(onSpans[0]), extract(offSpans[0])
	if !reflect.DeepEqual(on, off) {
		ob, _ := json.Marshal(on.Keys)
		fb, _ := json.Marshal(off.Keys)
		t.Fatalf("catalog keys differ with dedup on:\n  on %s\n off %s", ob, fb)
	}
	var sawInputValue bool
	for _, k := range on.Keys {
		if strings.HasPrefix(k.AttributeKey, "__fi_promoted_") {
			t.Fatalf("sentinel %s catalogued", k.AttributeKey)
		}
		sawInputValue = sawInputValue || k.AttributeKey == "input.value"
	}
	if !sawInputValue {
		t.Fatal("input.value must be catalogued")
	}
}

// deadLetterRows reads the dead-letter file.
func deadLetterRows(t *testing.T, path string) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil || len(raw) == 0 {
		t.Fatalf("the dead-letter file is empty: %v", err)
	}
	var rows []map[string]any
	for _, line := range bytes.Split(bytes.TrimSpace(raw), []byte("\n")) {
		var m map[string]any
		if err := json.Unmarshal(line, &m); err != nil {
			t.Fatal(err)
		}
		rows = append(rows, m)
	}
	return rows
}

// TestDrainDeadLettersCompactedSpansInline: a pointer batch ClickHouse will
// not take (not a schema refusal) is dead-lettered as self-contained inline
// rows: each line is the dedup-off row (its _version one higher, checked in
// the trajectory package), so replaying the file never needs trajectory_items.
// Before the fix the file held the preview and the pointers.
func TestDrainDeadLettersCompactedSpansInline(t *testing.T) {
	ch := &fakeTrajectoryCH{failStrictSpans: true}
	f := newTrajFixture(t, trajectory.Config{Mode: trajectory.ModeOn}, ch, true)
	dev := newDevTwin(t)
	body := attrsTurn(t, 2)
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, body)
	f.srv.drainNow(context.Background())
	postTrajOTLP(t, dev.srv, trajTestOID, trajTestPID, body)
	dev.srv.drainNow(context.Background())
	if len(insertsInto(ch.snapshot(), "trajectory_items")) != 1 {
		t.Fatal("fixture: the items insert must succeed")
	}
	dead := deadLetterRows(t, f.dlq)
	want := insertsInto(dev.ch.snapshot(), "spans")
	if len(want) != 1 {
		t.Fatal("dev twin stored nothing")
	}
	requireDevRows(t, dead, want[0].Rows)
}

// TestDeadLetteredDedupRowsBillRaw: spans that end in the dead-letter file
// are replayed as inline rows, so their request bills its raw bytes, not the
// deduplicated size. Turns 1 and 2 are stored as pointer rows (turn 2 bills
// below raw, so the check is not vacuous); turn 3 reuses their items but its
// spans insert fails, and it must bill raw.
func TestDeadLetteredDedupRowsBillRaw(t *testing.T) {
	ch := &fakeTrajectoryCH{}
	f := newTrajFixture(t, trajectory.Config{Mode: trajectory.ModeOn}, ch, true)
	usage := &recordingTrajUsage{}
	f.srv.usage = usage
	for turn := 1; turn <= 2; turn++ {
		postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, attrsTurn(t, turn))
		f.srv.drainNow(context.Background())
	}
	ch.mu.Lock()
	ch.failStrictSpans = true
	ch.mu.Unlock()
	postTrajOTLP(t, f.srv, trajTestOID, trajTestPID, attrsTurn(t, 3))
	f.srv.drainNow(context.Background())
	if len(deadLetterRows(t, f.dlq)) != 1 {
		t.Fatal("fixture: turn 3 must be dead-lettered")
	}
	usage.mu.Lock()
	defer usage.mu.Unlock()
	if usage.legacy != 0 || len(usage.raw) != 3 {
		t.Fatalf("usage events dedup=%d legacy=%d, want three dedup events", len(usage.raw), usage.legacy)
	}
	if usage.stored[1] >= usage.raw[1] {
		t.Fatalf("control: stored turn 2 must bill below raw (stored=%d raw=%d)", usage.stored[1], usage.raw[1])
	}
	if usage.stored[2] != usage.raw[2] {
		t.Fatalf("dead-lettered turn 3 billed stored=%d, want raw=%d", usage.stored[2], usage.raw[2])
	}
}
