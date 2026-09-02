package catalogwriter

import (
	"path/filepath"
	"testing"
)

func TestStageCanonicalSpansByProjectIsDeterministicAndTenantScoped(t *testing.T) {
	writer, err := New(enabledConfig(filepath.Join(t.TempDir(), "spool")), &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	secondProject := "22222222-2222-4222-8222-222222222222"
	second := canonicalSpan("2026-08-13 12:00:00.000002", map[string]string{"b": "2"})
	second["project_id"] = secondProject
	first := canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"a": "1"})
	invalid := canonicalSpan("2026-08-13 12:00:00.000003", map[string]string{"x": "3"})
	invalid["project_id"] = "not-a-project"

	staged := writer.StageCanonicalSpansByProject([]map[string]any{second, invalid, first})
	if len(staged) != 3 {
		t.Fatalf("jobs=%d want 3", len(staged))
	}
	if got := staged[0].Job.Metadata().Projects[0].ProjectID; got != secondProject {
		t.Fatalf("first project=%q", got)
	}
	if got := staged[1].Job.Metadata().Projects[0].ProjectID; got != testProjectID {
		t.Fatalf("second project=%q", got)
	}
	if metadata := staged[2].Job.Metadata(); metadata.UnscopedRejectedSpans != 1 || len(metadata.Projects) != 0 {
		t.Fatalf("unscoped metadata=%+v", metadata)
	}
	for index := range staged[:2] {
		if len(staged[index].Job.Metadata().Projects) != 1 {
			t.Fatalf("job %d is not project scoped", index)
		}
	}
}

func TestStageCanonicalSpansByProjectCannotBypassWholeDrainLimit(t *testing.T) {
	cfg := enabledConfig(filepath.Join(t.TempDir(), "spool"))
	cfg.MaxJobInputSpans = 1
	writer, err := New(cfg, &recordingInserter{})
	if err != nil {
		t.Fatal(err)
	}
	other := canonicalSpan("2026-08-13 12:00:00.000002", map[string]string{"b": "2"})
	other["project_id"] = "22222222-2222-4222-8222-222222222222"
	staged := writer.StageCanonicalSpansByProject([]map[string]any{
		canonicalSpan("2026-08-13 12:00:00.000001", map[string]string{"a": "1"}), other,
	})
	if len(staged) != 1 {
		t.Fatalf("jobs=%d want one whole-drain gap", len(staged))
	}
	metadata := staged[0].Job.Metadata()
	if metadata.InputSpans != 1 || metadata.UnscopedRejectedSpans != 1 || metadata.OverflowSpans != 1 {
		t.Fatalf("metadata=%+v", metadata)
	}
}
