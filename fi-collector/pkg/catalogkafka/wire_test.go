package catalogkafka

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogwriter"
)

const (
	testProject = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
	testStream  = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
	testTime    = "2026-08-13 12:00:00.000001"
)

func testDigest(value string) string {
	digest := sha256.Sum256([]byte(value))
	return hex.EncodeToString(digest[:])
}

func testKeyRow(extra int) map[string]any {
	return map[string]any{
		"project_id": testProject, "attribute_key": "model" + strings.Repeat("x", extra),
		"key_folded": "model", "attribute_type": "string",
		"first_seen": testTime, "last_seen": testTime, "catalog_epoch": uint16(1),
	}
}

func testValueRow() map[string]any {
	return map[string]any{
		"project_id": testProject, "attribute_key": "model", "attribute_type": "string",
		"value_fingerprint": testDigest("gpt"), "value_json": `"gpt"`,
		"value_search_text": "gpt", "first_seen": testTime, "last_seen": testTime,
		"catalog_epoch": uint16(1),
	}
}

func testRowBytes(t *testing.T, rows ...map[string]any) []byte {
	t.Helper()
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	for _, row := range rows {
		if err := encoder.Encode(row); err != nil {
			t.Fatal(err)
		}
	}
	return out.Bytes()
}

func testEnvelopeInput(t *testing.T) EnvelopeInput {
	t.Helper()
	return EnvelopeInput{
		ProjectID: testProject, CatalogEpoch: 1, ProducerStreamID: testStream,
		Sequence: 1, PreviousPayloadSHA256: ZeroSHA256,
		Payload: PayloadInput{
			SourceBatchDigest: testDigest("batch"), Outcome: OutcomeCommitted,
			SourceMinStart: testTime, SourceMaxStart: testTime, SourceRows: 1,
			KeyRows: 1,
			Chunks: []ChunkInput{{
				Table: KeyTable, Index: 0, RowCount: 1,
				JSONEachRow: testRowBytes(t, testKeyRow(0)),
			}},
		},
	}
}

func mustEnvelope(t *testing.T, input EnvelopeInput) WireEnvelope {
	t.Helper()
	envelope, err := NewWireEnvelope(input)
	if err != nil {
		t.Fatal(err)
	}
	return envelope
}

func TestWireEnvelopeDeterministicImmutableRoundTrip(t *testing.T) {
	input := testEnvelopeInput(t)
	input.Payload.Outcome = OutcomeGap
	input.Payload.GapReasons = []string{"z_gap", "a_gap"}
	first := mustEnvelope(t, input)
	secondInput := input
	secondInput.Payload.GapReasons = []string{"a_gap", "z_gap"}
	second := mustEnvelope(t, secondInput)
	if !bytes.Equal(first.Marshal(), second.Marshal()) || first.EnvelopeID() != second.EnvelopeID() {
		t.Fatal("equivalent input did not produce deterministic bytes")
	}

	original := first.Marshal()
	input.Payload.GapReasons[0] = "mutated"
	input.Payload.Chunks[0].JSONEachRow[0] = 'x'
	snapshot := first.Snapshot()
	snapshot.Payload.GapReasons[0] = "mutated"
	snapshot.Payload.Chunks[0].JSONEachRow[0] = 'x'
	if !bytes.Equal(first.Marshal(), original) {
		t.Fatal("caller mutation changed immutable envelope")
	}
	parsed, err := ParseWireEnvelope(original)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(parsed.Marshal(), original) || parsed.Snapshot().Version != EnvelopeVersion {
		t.Fatal("wire round trip changed bytes or version")
	}
	key, err := KafkaKey(testProject, 1, testStream)
	if err != nil || string(key) != testProject+"/1/"+testStream {
		t.Fatalf("key=%q err=%v", key, err)
	}
}

func TestWireEnvelopeRejectsTamperAndNonCanonicalEncoding(t *testing.T) {
	envelope := mustEnvelope(t, testEnvelopeInput(t))
	raw := envelope.Marshal()
	tampered := bytes.Replace(bytes.Clone(raw), []byte("committed"), []byte("committex"), 1)
	if _, err := ParseWireEnvelope(tampered); err == nil {
		t.Fatal("payload tamper was accepted")
	}
	if _, err := ParseWireEnvelope(append(raw, '\n')); err == nil || !strings.Contains(err.Error(), "canonical") {
		t.Fatalf("non-canonical error=%v", err)
	}
	bad := testEnvelopeInput(t)
	bad.Payload.Chunks[0].JSONEachRow = []byte("{ \"project_id\": \"x\" }\n")
	if _, err := NewWireEnvelope(bad); err == nil || !strings.Contains(err.Error(), "canonical JSON") {
		t.Fatalf("non-canonical row error=%v", err)
	}
	bad = testEnvelopeInput(t)
	bad.Payload.Chunks[0].Table = Table("spans")
	if _, err := NewWireEnvelope(bad); err == nil || !strings.Contains(err.Error(), "forbidden table") {
		t.Fatalf("existing-table target error=%v", err)
	}
}

func TestWireEnvelopeHardRecordSize(t *testing.T) {
	input := testEnvelopeInput(t)
	input.Payload.Chunks[0].JSONEachRow = testRowBytes(t, testKeyRow(480<<10))
	envelope := mustEnvelope(t, input)
	if size := len(envelope.Marshal()); size > MaxRecordBytes {
		t.Fatalf("accepted envelope size=%d limit=%d", size, MaxRecordBytes)
	}

	input = testEnvelopeInput(t)
	large := testRowBytes(t, testKeyRow(350<<10))
	input.Payload.KeyRows = 2
	input.Payload.Chunks = []ChunkInput{
		{Table: KeyTable, Index: 0, RowCount: 1, JSONEachRow: large},
		{Table: KeyTable, Index: 1, RowCount: 1, JSONEachRow: large},
	}
	if _, err := NewWireEnvelope(input); err == nil || !strings.Contains(err.Error(), "encoded envelope") {
		t.Fatalf("oversize envelope error=%v", err)
	}
	if _, err := ParseWireEnvelope(make([]byte, MaxRecordBytes+1)); err == nil {
		t.Fatal("oversize record parsed")
	}
}

func TestPayloadInputFromWireJobUsesExactCatalogRows(t *testing.T) {
	keyRow := testKeyRow(0)
	valueRow := testValueRow()
	keyBytes, valueBytes := testRowBytes(t, keyRow), testRowBytes(t, valueRow)
	metadata := catalogwriter.JobMetadata{
		CatalogEpoch: 1, InputSpans: 1, AcceptedSpans: 1, KeyRows: 1, ValueRows: 1,
		EncodedBytes: len(keyBytes) + len(valueBytes), MinSpanStart: testTime, MaxSpanStart: testTime,
		Projects: []catalogwriter.ProjectJobMetadata{{
			ProjectID: testProject, InputSpans: 1, AcceptedSpans: 1, KeyRows: 1, ValueRows: 1,
			MinSpanStart: testTime, MaxSpanStart: testTime,
		}},
	}
	job := catalogwriter.WireJob{
		KeyRows: []map[string]any{keyRow}, ValueRows: []map[string]any{valueRow},
		EncodedBytes: metadata.EncodedBytes, Metadata: metadata,
	}
	payload, err := PayloadInputFromWireJob(job, testProject, testDigest("source"), 1, 4<<10)
	if err != nil {
		t.Fatal(err)
	}
	if len(payload.Chunks) != 2 || payload.Chunks[0].Table != KeyTable ||
		payload.Chunks[1].Table != ValueTable || payload.Outcome != OutcomeCommitted {
		t.Fatalf("payload=%+v", payload)
	}
	input := testEnvelopeInput(t)
	input.Payload = payload
	if _, err := NewWireEnvelope(input); err != nil {
		t.Fatal(err)
	}

	job.KeyRows[0]["trace_id"] = "forbidden"
	if _, err := PayloadInputFromWireJob(job, testProject, testDigest("source"), 1, 4<<10); err == nil {
		t.Fatal("WireJob with existing-table column was accepted")
	}
}

func TestPayloadInputFromWriterCarriesBoundedSystemModelNamespace(t *testing.T) {
	cfg := catalogwriter.DefaultConfig()
	cfg.Enabled = true
	cfg.SpoolDir = t.TempDir()
	writer, err := catalogwriter.NewTransportWriter(cfg)
	if err != nil {
		t.Fatal(err)
	}
	stage := func(model string) (catalogwriter.WireJob, catalogwriter.StageReport) {
		job, report := writer.StageCanonicalSpans([]map[string]any{{
			"project_id": testProject, "start_time": testTime, "model": model,
			"attrs_string": map[string]string{}, "attrs_number": map[string]float64{},
			"attrs_bool": map[string]uint8{}, "attributes_extra": map[string]any{},
		}})
		return catalogwriter.ExportWireJob(job), report
	}

	exact, report := stage(strings.Repeat("x", 16<<10))
	if report.IncompleteSpans != 0 {
		t.Fatalf("exact-boundary stage report=%+v", report)
	}
	payload, err := PayloadInputFromWireJob(
		exact, testProject, testDigest("model-boundary"), 1, 64<<10,
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(payload.Chunks) != 2 || payload.ValueRows != 1 {
		t.Fatalf("system Model payload=%+v", payload)
	}
	for _, rows := range [][]map[string]any{exact.KeyRows, exact.ValueRows} {
		if len(rows) != 1 || rows[0]["source_kind"] != "system_attribute" || rows[0]["attribute_key"] != "model" {
			t.Fatalf("system Model namespace missing: %v", rows)
		}
	}
	input := testEnvelopeInput(t)
	input.Payload = payload
	if _, err := NewWireEnvelope(input); err != nil {
		t.Fatal(err)
	}

	oversized, report := stage(strings.Repeat("x", (16<<10)+1))
	if report.IncompleteSpans != 1 || len(oversized.KeyRows) != 0 || len(oversized.ValueRows) != 0 ||
		!contains(report.BuildGapReasons, "system_value_projection") {
		t.Fatalf("oversized Model was not a durable gap: wire=%+v report=%+v", oversized, report)
	}
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
