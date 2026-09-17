package observedcatalog

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"reflect"
	"strings"
	"testing"
)

func handoffEvents(t *testing.T, raw []byte) []map[string]any {
	t.Helper()
	var events []map[string]any
	for _, line := range bytes.Split(bytes.TrimSpace(raw), []byte("\n")) {
		if len(line) == 0 {
			continue
		}
		var event map[string]any
		if err := json.Unmarshal(line, &event); err != nil {
			t.Fatal(err)
		}
		delete(event, "time")
		events = append(events, event)
	}
	return events
}

func TestHandoffGapGroupsExactScopesAndDoesNotLogSourcePayloads(t *testing.T) {
	one, earlier, later, workspace, project, organization := testSpan(), testSpan(), testSpan(), testSpan(), testSpan(), testSpan()
	one.Row["attrs_string"] = map[string]string{"private-key": "private-value"}
	one.Row["id"] = "private-span-id"
	earlier.Row["start_time"] = "2026-09-07 23:59:59.999999"
	later.Row["start_time"] = "2026-09-09 00:00:00.000001"
	workspace.WorkspaceID = "55555555-5555-4555-8555-555555555555"
	project.Row["project_id"] = "66666666-6666-4666-8666-666666666666"
	organization.OrganizationID = "77777777-7777-4777-8777-777777777777"
	organization.Row["org_id"] = organization.OrganizationID
	spans := []ScopedSpan{one, workspace, earlier, project, organization, one, later}
	before, _ := json.Marshal(spans)
	var out bytes.Buffer
	LogHandoffGap(slog.New(slog.NewJSONHandler(&out, nil)), spans, errors.New("spool full"))
	events := handoffEvents(t, out.Bytes())
	if len(events) != 5 || events[0]["repair_scopes"] != float64(4) || events[0]["unresolved_spans"] != float64(0) || events[0]["source_spans"] != float64(7) {
		t.Fatalf("summary/scopes: %+v", events)
	}
	first := events[1]
	if first["source_spans"] != float64(4) || first["source_first_seen"] != earlier.Row["start_time"] || first["source_last_seen"] != later.Row["start_time"] || first["time_bounds"] != "inclusive" || first["timezone"] != "UTC" {
		t.Fatalf("window: %+v", first)
	}
	for i, span := range []ScopedSpan{one, workspace, project, organization} {
		e := events[i+1]
		if e["event"] != "observed_catalog_repair_scope" || e["organization_id"] != span.OrganizationID || e["workspace_id"] != span.WorkspaceID || e["project_id"] != span.Row["project_id"] {
			t.Fatalf("scope %d: %+v", i, e)
		}
	}
	for _, secret := range []string{"private-key", "private-value", "private-span-id", "attrs_string", "attributes_extra"} {
		if strings.Contains(out.String(), secret) {
			t.Fatalf("source payload leaked: %s", secret)
		}
	}
	after, _ := json.Marshal(spans)
	if !bytes.Equal(before, after) {
		t.Fatal("source rows changed")
	}
	var retry bytes.Buffer
	LogHandoffGap(slog.New(slog.NewJSONHandler(&retry, nil)), spans, errors.New("spool full"))
	if !reflect.DeepEqual(events, handoffEvents(t, retry.Bytes())) {
		t.Fatal("scope order changed on retry")
	}
}

func TestHandoffGapRejectsUnverifiedScopeAndTimestampWithoutLoggingThem(t *testing.T) {
	for name, change := range map[string]func(*ScopedSpan){
		"missing proof":     func(s *ScopedSpan) { s.ScopeError = "missing_project_workspace_proof" },
		"foreign project":   func(s *ScopedSpan) { s.ScopeError = "project_workspace_mismatch" },
		"invalid org":       func(s *ScopedSpan) { s.OrganizationID = "private-invalid-org" },
		"invalid workspace": func(s *ScopedSpan) { s.WorkspaceID = "private-invalid-workspace" },
		"invalid project":   func(s *ScopedSpan) { s.Row["project_id"] = "private-invalid-project" },
		"foreign org":       func(s *ScopedSpan) { s.Row["org_id"] = "77777777-7777-4777-8777-777777777777" },
		"wrong org type":    func(s *ScopedSpan) { s.Row["org_id"] = []string{"private-invalid-org"} },
		"invalid time":      func(s *ScopedSpan) { s.Row["start_time"] = "private-invalid-time" },
		"wrong time type":   func(s *ScopedSpan) { s.Row["start_time"] = 1 },
		"nil row":           func(s *ScopedSpan) { s.Row = nil },
	} {
		t.Run(name, func(t *testing.T) {
			span := testSpan()
			change(&span)
			var out bytes.Buffer
			LogHandoffGap(slog.New(slog.NewJSONHandler(&out, nil)), []ScopedSpan{span}, errors.New("handoff failed"))
			events := handoffEvents(t, out.Bytes())
			if len(events) != 1 || events[0]["unresolved_spans"] != float64(1) || events[0]["repair_scopes"] != float64(0) || strings.Contains(out.String(), "private-") || strings.Contains(out.String(), "project_id") {
				t.Fatalf("unverified repair scope: %+v", events)
			}
		})
	}
}

func TestHandoffGapRetainsVerifiedWindowInMixedBatch(t *testing.T) {
	valid, invalid := testSpan(), testSpan()
	invalid.Row["start_time"] = "2026-09-01 00:00:00.000000"
	invalid.ScopeError = "project_workspace_mismatch"
	var out bytes.Buffer
	LogHandoffGap(slog.New(slog.NewJSONHandler(&out, nil)), []ScopedSpan{invalid, valid}, errors.New("handoff failed"))
	events := handoffEvents(t, out.Bytes())
	if len(events) != 2 || events[0]["source_spans"] != float64(2) || events[0]["unresolved_spans"] != float64(1) || events[0]["repair_scopes"] != float64(1) {
		t.Fatalf("mixed batch summary: %+v", events)
	}
	if events[1]["source_spans"] != float64(1) || events[1]["source_first_seen"] != valid.Row["start_time"] || events[1]["source_last_seen"] != valid.Row["start_time"] {
		t.Fatalf("unverified span changed repair window: %+v", events[1])
	}
}

func TestHandoffGapHasNoWorkspaceCapAndSuccessIsQuiet(t *testing.T) {
	var spans []ScopedSpan
	for i := 1; i <= 300; i++ {
		span := testSpan()
		span.WorkspaceID = fmt.Sprintf("%08x-2222-4222-8222-222222222222", i)
		spans = append(spans, span)
	}
	var out bytes.Buffer
	log := slog.New(slog.NewJSONHandler(&out, nil))
	LogHandoffGap(log, spans, nil)
	if out.Len() != 0 {
		t.Fatal("successful handoff logged a gap")
	}
	LogHandoffGap(log, spans, errors.New("handoff failed"))
	events := handoffEvents(t, out.Bytes())
	if len(events) != 301 || events[0]["repair_scopes"] != float64(300) {
		t.Fatalf("lost repair scopes: %d", len(events))
	}
}

func TestHandoffGapReportsRealSpoolAndExtractionFailures(t *testing.T) {
	for _, tc := range []struct {
		name   string
		bytes  int64
		limits Limits
		reason string
	}{
		{"spool capacity", 1, DefaultLimits(), "spool capacity exceeded"},
		{"extraction budget", 512 << 20, Limits{MaxKeysPerSpan: 1, MaxArrayMembersPerSpan: 256}, "incomplete extraction"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			writer, err := NewWriter(SpoolConfig{Directory: t.TempDir(), MaxBytes: tc.bytes}, tc.limits)
			if err != nil {
				t.Fatal(err)
			}
			defer writer.Close()
			spans := []ScopedSpan{testSpan()}
			err = writer.EnqueueCanonicalSpans(spans)
			if err == nil || !strings.Contains(err.Error(), tc.reason) {
				t.Fatalf("expected real %s failure, got %v", tc.name, err)
			}
			var out bytes.Buffer
			LogHandoffGap(slog.New(slog.NewJSONHandler(&out, nil)), spans, err)
			events := handoffEvents(t, out.Bytes())
			if len(events) != 2 || events[0]["unresolved_spans"] != float64(0) || events[1]["project_id"] != spans[0].Row["project_id"] {
				t.Fatalf("real failure lost repair scope: %+v", events)
			}
		})
	}
}
