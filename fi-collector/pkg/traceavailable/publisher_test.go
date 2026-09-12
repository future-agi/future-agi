package traceavailable

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/google/uuid"
)

func TestExtractRootsRequiresScopeAndEndedRoot(t *testing.T) {
	org, project, trace := uuid.NewString(), uuid.NewString(), uuid.NewString()
	row := func(parent, end, p string) map[string]any {
		return map[string]any{"parent_span_id": parent, "project_id": p, "trace_id": trace, "id": "abcdef0123456789", "end_time": end}
	}
	rows := []map[string]any{
		row("", "2026-09-12 12:00:00.123456", project),
		row("parent", "2026-09-12 12:00:00", project),
		row("", "1970-01-01 00:00:00", project),
		row("", "2026-09-12 12:00:00", uuid.NewString()),
		row("", "", project),
	}
	roots := ExtractRoots(rows, org, "", map[string]struct{}{project: {}})
	if len(roots) != 1 || roots[0].TraceID != trace || roots[0].SpanID != "abcdef0123456789" {
		t.Fatalf("unexpected roots: %+v", roots)
	}
	if got := ExtractRoots(rows, "invalid", "", map[string]struct{}{project: {}}); len(got) != 0 {
		t.Fatal("accepted unauthenticated scope")
	}
}

func TestEventsBoundedScopedAndDeduplicated(t *testing.T) {
	now := time.Now().UTC()
	org, project, workspace := uuid.NewString(), uuid.NewString(), uuid.NewString()
	roots := make([]Root, 101)
	for i := range roots {
		roots[i] = Root{org, workspace, project, uuid.NewString(), "abcdef0123456789", now}
	}
	roots = append(roots, roots[0])
	other := roots[0]
	other.ProjectID = uuid.NewString()
	roots = append(roots, other)
	events, err := BuildEvents(roots, now)
	if err != nil || len(events) != 3 {
		t.Fatalf("events=%d err=%v", len(events), err)
	}
	count := 0
	for _, event := range events {
		raw, _ := json.Marshal(event)
		if len(raw) > maxBytes || len(event.Traces) > 100 || event.WorkspaceID == nil || *event.WorkspaceID != workspace {
			t.Fatal("unbounded or unscoped event")
		}
		count += len(event.Traces)
	}
	if count != 102 {
		t.Fatalf("deduplication count=%d", count)
	}
	again, _ := BuildEvents(roots, now)
	if events[0].EventID == again[0].EventID {
		t.Fatal("new canonical writes must have new event identity")
	}
	roots[0].TraceID = "invalid"
	if _, err = BuildEvents(roots, now); err == nil {
		t.Fatal("invalid trace accepted")
	}
}

func TestNotifierDefaultOffAndInvalidConfiguration(t *testing.T) {
	t.Setenv("FI_ERROR_FEED_ENABLED", "")
	if p, err := FromEnv(nil); p != nil || err != nil {
		t.Fatal("must be off by default")
	}
	t.Setenv("FI_ERROR_FEED_ENABLED", "true")
	t.Setenv("FI_ERROR_FEED_KAFKA_BROKERS", "")
	if _, err := FromEnv(nil); err == nil {
		t.Fatal("enabled without brokers")
	}
}
