package catalogwriter

import (
	"reflect"
	"testing"
)

func TestWireJobRoundTripsOnlyCatalogRows(t *testing.T) {
	writer, err := New(enabledConfig(t.TempDir()), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	job, _ := writer.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"model": "gpt"}),
	})
	wire := ExportWireJob(job)
	rebuilt, err := writer.ImportWireJob(wire)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(ExportWireJob(rebuilt), wire) {
		t.Fatalf("round trip mismatch\nwire=%+v\nrebuilt=%+v", wire, ExportWireJob(rebuilt))
	}
	for _, row := range append(wire.KeyRows, wire.ValueRows...) {
		for _, forbidden := range []string{"id", "trace_id", "attributes_extra", "attrs_string"} {
			if _, exists := row[forbidden]; exists {
				t.Fatalf("wire retained %s: %v", forbidden, row)
			}
		}
	}
}

func TestWireJobImportRejectsUnknownAndTamperedRows(t *testing.T) {
	writer, _ := New(enabledConfig(t.TempDir()), &recordingInserter{})
	job, _ := writer.StageCanonicalSpans([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"model": "gpt"}),
	})
	wire := ExportWireJob(job)
	wire.KeyRows[0]["trace_id"] = "forbidden"
	if _, err := writer.ImportWireJob(wire); err == nil {
		t.Fatal("unknown canonical column was imported")
	}
	wire = ExportWireJob(job)
	wire.KeyRows[0]["catalog_epoch"] = uint16(999)
	if _, err := writer.ImportWireJob(wire); err == nil {
		t.Fatal("epoch tamper was imported")
	}
}
