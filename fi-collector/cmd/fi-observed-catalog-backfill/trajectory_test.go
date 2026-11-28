package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/future-agi/future-agi/fi-collector/pkg/trajectory"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

var trajectoryHour = time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)

// trajectorySpan is one OpenInference LLM span of exampleScope's project:
// a message-array input.value (string), a map-valued output.value, and a
// flattened message key.
func trajectorySpan(t *testing.T) ptrace.Traces {
	t.Helper()
	scope := exampleScope()
	td := ptrace.NewTraces()
	rs := td.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("service.name", "s")
	rs.Resource().Attributes().PutStr("fi.project_id", scope.ProjectID)
	rs.Resource().Attributes().PutStr("fi.org_id", scope.OrganizationID)
	rs.Resource().Attributes().PutStr("project_type", "observe")
	sp := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	sp.SetTraceID(pcommon.TraceID([16]byte{0x31, 15: 0x01}))
	sp.SetSpanID(pcommon.SpanID([8]byte{0x32, 7: 0x02}))
	sp.SetName("llm.chat")
	start := trajectoryHour.Add(time.Minute)
	sp.SetStartTimestamp(pcommon.NewTimestampFromTime(start))
	sp.SetEndTimestamp(pcommon.NewTimestampFromTime(start.Add(time.Second)))
	attrs := sp.Attributes()
	attrs.PutStr("fi.span.kind", "LLM")
	attrs.PutStr("session.id", "sess-backfill")
	in, _ := json.Marshal(map[string]any{"model": "gpt-4o", "messages": []any{
		map[string]any{"role": "system", "content": "Policy " + strings.Repeat("p", 900)},
		map[string]any{"role": "user", "content": "Question"},
	}})
	attrs.PutStr("input.value", string(in))
	if err := attrs.PutEmptyMap("output.value").FromRaw(map[string]any{"choices": []any{
		map[string]any{"index": 0, "message": map[string]any{"role": "assistant", "content": "Answer " + strings.Repeat("a", 700)}},
	}}); err != nil {
		t.Fatal(err)
	}
	attrs.PutStr("llm.input_messages.1.message.content", "Question")
	return td
}

// sourceRows converts the span with dedup on and off and returns both rows as
// a source SELECT returns them, plus the items dedup stored. jsonExtra
// renders attributes_extra as a JSON column does (dotted paths nested),
// otherwise as a String column (JSON text).
func sourceRows(t *testing.T, jsonExtra bool) (on, off map[string]any, items []trajectory.Item) {
	t.Helper()
	td := trajectorySpan(t)
	devTraces := ptrace.NewTraces()
	td.CopyTo(devTraces)
	res, err := chexp.ConvertWithOptions(context.Background(), td, nil, chexp.ConvertOptions{
		Trajectory: trajectory.Config{Mode: trajectory.ModeOn}.Normalize(), Gate: trajectory.FixedGate(true),
	})
	if err != nil || len(res.Rows) != 1 || res.Trajectory == nil {
		t.Fatalf("dedup conversion: %v", err)
	}
	dev, _, err := chexp.ConvertWithIdentities(context.Background(), devTraces, nil)
	if err != nil || len(dev) != 1 {
		t.Fatalf("dev conversion: %v", err)
	}
	if res.Rows[0]["payload_encoding"] != trajectory.EncodingItemsV1 {
		t.Fatal("fixture: the span must be extracted")
	}
	return asSourceRow(t, res.Rows[0], jsonExtra), asSourceRow(t, dev[0], jsonExtra), res.Trajectory.Items()
}

func asSourceRow(t *testing.T, row map[string]any, jsonExtra bool) map[string]any {
	t.Helper()
	extra := row["attributes_extra"].(map[string]any)
	data, err := json.Marshal(row)
	if err != nil {
		t.Fatal(err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var out map[string]any
	if err := decoder.Decode(&out); err != nil {
		t.Fatal(err)
	}
	out["observation_type"], out["service_name"] = "llm", "s"
	out["_version"] = "7"
	if jsonExtra {
		nested := map[string]any{}
		for k, v := range out["attributes_extra"].(map[string]any) {
			setAttribute(nested, k, v, true)
		}
		out["attributes_extra"] = nested
	} else {
		text, err := json.Marshal(extra)
		if err != nil {
			t.Fatal(err)
		}
		out["attributes_extra"] = string(text)
	}
	return out
}

// fakeTrajectorySource answers the backfill's source SELECTs: the schema
// probe, the identity page, the payload page and the items SELECT.
type fakeTrajectorySource struct {
	mu        sync.Mutex
	columns   map[string]string // system.columns answer
	row       map[string]any
	items     []trajectory.Item
	payloadSQ []string
	itemCalls int
}

func (f *fakeTrajectorySource) serve(t *testing.T) *sourceReader {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		sql := string(body)
		if r.URL.Query().Get("readonly") != "1" {
			t.Error("source read is not read-only")
		}
		f.mu.Lock()
		defer f.mu.Unlock()
		enc := json.NewEncoder(w)
		switch {
		case strings.Contains(sql, "system.columns"):
			for name, typ := range f.columns {
				_ = enc.Encode(map[string]string{"name": name, "type": typ})
			}
		case r.URL.Query().Get("param_limit") != "":
			_ = enc.Encode(map[string]string{"observation_type": "llm", "service_name": "s", "trace_id": f.row["trace_id"].(string), "id": f.row["id"].(string)})
		case strings.Contains(sql, "FROM trajectory_items"):
			f.itemCalls++
			for _, it := range f.items {
				_ = enc.Encode(map[string]string{"scope_id": it.ScopeID, "item_hash": it.ItemHash, "content": it.Content})
			}
		case strings.Contains(sql, "FROM spans"):
			f.payloadSQ = append(f.payloadSQ, strings.TrimSuffix(sql, " FORMAT JSONEachRow"))
			_ = enc.Encode(f.row)
		default:
			t.Errorf("unexpected source SQL %q", sql)
		}
	}))
	t.Cleanup(server.Close)
	reader, err := newSourceReader(server.URL, "source", "readonly", "test")
	if err != nil {
		t.Fatal(err)
	}
	return reader
}

func withPointerColumns(extraType string) map[string]string {
	columns := map[string]string{"attributes_extra": extraType}
	for _, col := range trajectory.SpansPointerColumns {
		columns[col.Name] = col.Type
	}
	return columns
}

// backfillBatch runs one page of the real scan (identity, payload, hydrate,
// extract) against the fake.
func backfillBatch(t *testing.T, reader *sourceReader) observedcatalog.Batch {
	t.Helper()
	scope := exampleScope()
	keys, err := reader.identityPage(context.Background(), scope.ProjectID, trajectoryHour, physicalKey{}, 10)
	if err != nil {
		t.Fatal(err)
	}
	rows, err := reader.payloadPage(context.Background(), scope.ProjectID, trajectoryHour, keys)
	if err != nil {
		t.Fatal(err)
	}
	batch, _, err := buildPage(rows, scope, trajectoryHour, trajectoryHour.Add(time.Hour), observedcatalog.DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	return batch
}

// TestBackfillCatalogsDedupOffAttributesForItemsV1Rows: the backfill of a
// trajectory pointer row catalogues exactly what it catalogues for the same
// span stored with dedup off (input.value and the map-valued output.value
// present, no sentinel), for a String and a JSON attributes_extra column.
// Before the fix the sentinels were catalogued and the promoted keys missing.
func TestBackfillCatalogsDedupOffAttributesForItemsV1Rows(t *testing.T) {
	for _, extraType := range []string{"String", "JSON(max_dynamic_paths=0)"} {
		t.Run(extraType, func(t *testing.T) {
			jsonExtra := strings.HasPrefix(extraType, "JSON")
			on, off, items := sourceRows(t, jsonExtra)
			dedup := &fakeTrajectorySource{columns: withPointerColumns(extraType), row: on, items: items}
			reader := dedup.serve(t)
			got := backfillBatch(t, reader)
			want := backfillBatch(t, (&fakeTrajectorySource{columns: withPointerColumns(extraType), row: off}).serve(t))
			if !reflect.DeepEqual(got, want) {
				g, _ := json.Marshal(got.Keys)
				w, _ := json.Marshal(want.Keys)
				t.Fatalf("backfill catalog differs from dedup off:\n got %s\nwant %s", g, w)
			}
			for _, k := range got.Keys {
				if strings.HasPrefix(k.AttributeKey, "__fi_promoted_") {
					t.Fatalf("sentinel %s catalogued", k.AttributeKey)
				}
			}
			if reader.contentUnavailable != 0 || dedup.itemCalls != 1 {
				t.Fatalf("unavailable=%d item queries=%d", reader.contentUnavailable, dedup.itemCalls)
			}
			if !strings.Contains(dedup.payloadSQ[0], "payload_encoding") {
				t.Fatal("the payload SELECT must read the pointer columns")
			}
		})
	}
}

// TestBackfillMissingItemsHidesSentinelsWithoutPreview: when the items are
// gone, the sentinels are still hidden, the promoted attribute is not
// catalogued (never from the preview), and the span is counted.
func TestBackfillMissingItemsHidesSentinelsWithoutPreview(t *testing.T) {
	on, _, _ := sourceRows(t, false)
	reader := (&fakeTrajectorySource{columns: withPointerColumns("String"), row: on}).serve(t)
	batch := backfillBatch(t, reader)
	for _, k := range batch.Keys {
		if strings.HasPrefix(k.AttributeKey, "__fi_promoted_") || k.AttributeKey == "input.value" || k.AttributeKey == "output.value" {
			t.Fatalf("key %s catalogued without its content", k.AttributeKey)
		}
	}
	preview, _ := on["input"].(string)
	for _, v := range batch.Values {
		if preview != "" && strings.Contains(v.ValueJSON, preview) {
			t.Fatal("the preview was catalogued")
		}
	}
	if reader.contentUnavailable != 1 {
		t.Fatalf("unavailable spans %d, want 1", reader.contentUnavailable)
	}
}

// TestBackfillWithoutPointerColumnsKeepsDevSelect: before 040 the payload
// SELECT is byte for byte the one dev sends, no items are read, and an
// attribute that happens to be named like a sentinel is catalogued as the
// customer's attribute.
func TestBackfillWithoutPointerColumnsKeepsDevSelect(t *testing.T) {
	_, off, _ := sourceRows(t, false)
	extra := map[string]any{}
	if err := json.Unmarshal([]byte(off["attributes_extra"].(string)), &extra); err != nil {
		t.Fatal(err)
	}
	extra[trajectory.SentinelPromotedInputKey] = "mine"
	text, _ := json.Marshal(extra)
	off["attributes_extra"] = string(text)
	source := &fakeTrajectorySource{columns: map[string]string{"attributes_extra": "String"}, row: off}
	reader := source.serve(t)
	batch := backfillBatch(t, reader)
	want := `SELECT project_id, org_id, observation_type, service_name, trace_id, id,
 start_time, attrs_string, attrs_number, attrs_bool,
 attributes_extra, model, _version, is_deleted
 FROM spans PREWHERE` + sourceWhere + `
 AND tuple(toString(observation_type), toString(service_name), trace_id, id) IN (tuple({k0o:String}, {k0s:String}, {k0t:String}, {k0i:String}))`
	if len(source.payloadSQ) != 1 || source.payloadSQ[0] != want {
		t.Fatalf("payload SELECT changed:\n%s\nwant\n%s", source.payloadSQ, want)
	}
	if source.itemCalls != 0 {
		t.Fatal("no items may be read without the pointer columns")
	}
	var kept bool
	for _, k := range batch.Keys {
		kept = kept || k.AttributeKey == trajectory.SentinelPromotedInputKey
	}
	if !kept {
		t.Fatal("a customer attribute named like a sentinel must be catalogued on a non-trajectory row")
	}
	if reader.trajectory.pointers {
		t.Fatal("a source without the pointer columns must be probed again")
	}
}

// TestBackfillSchemaProbeIgnoresUnrelatedRows: rows that do not describe a
// column (as the other tests' fakes return for every SELECT) mean no
// trajectory columns, not an error.
func TestBackfillSchemaProbeIgnoresUnrelatedRows(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, `{"observation_type":"span","service_name":"s","trace_id":"t","id":"a","name":"llm.chat"}`)
	}))
	t.Cleanup(server.Close)
	reader, _ := newSourceReader(server.URL, "source", "readonly", "test")
	schema, err := reader.schema(context.Background())
	if err != nil || schema.pointers {
		t.Fatalf("schema %+v %v", schema, err)
	}
}

// TestBackfillContentGapIsRecordedDurably: a scan over a trajectory span whose
// items are gone publishes the span's other attributes, and records the gap
// ahead of the checkpoint: the span's identity in the receipt file under its
// own reason, a count in the checkpoint, and the gap reason in the page
// receipt. A resumed scan does not record it again. Before, the only trace
// was a per-page stdout count.
func TestBackfillContentGapIsRecordedDurably(t *testing.T) {
	on, _, _ := sourceRows(t, false)
	reader := (&fakeTrajectorySource{columns: withPointerColumns("String"), row: on}).serve(t)
	cfg := options{project: exampleScope().ProjectID, source: reader, since: trajectoryHour, until: trajectoryHour.Add(time.Hour),
		apply: true, checkpointPath: filepath.Join(t.TempDir(), "progress.json"), maxPages: 2, pageSize: 10, limits: observedcatalog.DefaultLimits()}
	publisher := &testPublisher{}
	var output bytes.Buffer
	if err := runSpans(context.Background(), cfg, &testScopeReader{scope: exampleScope()}, publisher, &output); err != nil {
		t.Fatal(err)
	}
	progress, err := loadCheckpoint(cfg.checkpointPath, scanBinding(cfg, exampleScope()), lastHour(cfg.until))
	if err != nil || !progress.Complete || progress.ContentUnavailableSpans != 1 || progress.QuarantinedSpans != 0 || publisher.calls != 1 {
		t.Fatalf("checkpoint %+v %v, publishes %d", progress, err, publisher.calls)
	}
	audit := cfg.checkpointPath + ".quarantine.jsonl"
	data, err := os.ReadFile(audit)
	if err != nil {
		t.Fatal(err)
	}
	var receipt quarantinedSpan
	if err := json.Unmarshal(data, &receipt); err != nil || receipt.Reason != contentUnavailableReason || receipt.Key.Span != on["id"] || receipt.Key.Trace != on["trace_id"] {
		t.Fatalf("content gap receipt %s: %v", data, err)
	}
	if bytes.Contains(data, []byte("attributes_extra")) || bytes.Count(data, []byte("\n")) != 1 {
		t.Fatalf("the receipt holds exactly one identity and no attribute: %s", data)
	}
	var page map[string]any
	if err := json.Unmarshal(bytes.SplitN(output.Bytes(), []byte("\n"), 2)[0], &page); err != nil {
		t.Fatal(err)
	}
	if page["trajectory_content_unavailable_spans"] != float64(1) || page["recorded_trajectory_content_unavailable_spans"] != float64(1) ||
		!reflect.DeepEqual(page["gap_reasons"], []any{contentUnavailableReason}) {
		t.Fatalf("page receipt does not name the gap: %v", page)
	}
	// A completed checkpoint is not scanned again, so the gap is not repeated.
	if err := runSpans(context.Background(), cfg, &testScopeReader{scope: exampleScope()}, publisher, io.Discard); err != nil || publisher.calls != 1 {
		t.Fatal("completed scan was repeated", err)
	}
	if after, _ := os.ReadFile(audit); !bytes.Equal(after, data) {
		t.Fatal("resume recorded the gap again")
	}
}

// TestBackfillContentGapSkipsTombstonesAndOutOfRangeRows: only spans the scan
// catalogues are recorded as content gaps, and the marker never reaches the
// extractor.
func TestBackfillContentGapSkipsTombstonesAndOutOfRangeRows(t *testing.T) {
	on, _, _ := sourceRows(t, false)
	scope := exampleScope()
	for name, mutate := range map[string]func(map[string]any){
		"tombstone":    func(row map[string]any) { row["is_deleted"] = "1" },
		"out of range": func(map[string]any) {},
	} {
		t.Run(name, func(t *testing.T) {
			row := map[string]any{}
			for k, v := range on {
				row[k] = v
			}
			mutate(row)
			reader := (&fakeTrajectorySource{columns: withPointerColumns("String"), row: row}).serve(t)
			keys, err := reader.identityPage(context.Background(), scope.ProjectID, trajectoryHour, physicalKey{}, 10)
			if err != nil {
				t.Fatal(err)
			}
			rows, err := reader.payloadPage(context.Background(), scope.ProjectID, trajectoryHour, keys)
			if err != nil || len(rows) != 1 {
				t.Fatalf("rows %d: %v", len(rows), err)
			}
			if _, ok := rows[0][contentUnavailableMarker]; !ok {
				t.Fatal("fixture: the row must be tagged")
			}
			since := trajectoryHour
			if name == "out of range" {
				since = trajectoryHour.Add(30 * time.Minute)
			}
			_, report, err := buildPage(rows, scope, since, trajectoryHour.Add(time.Hour), observedcatalog.DefaultLimits())
			if err != nil || len(report.ContentUnavailable) != 0 {
				t.Fatalf("gap recorded for a span that is not catalogued: %+v %v", report, err)
			}
			if _, ok := rows[0][contentUnavailableMarker]; ok {
				t.Fatal("the marker must be removed")
			}
		})
	}
}
