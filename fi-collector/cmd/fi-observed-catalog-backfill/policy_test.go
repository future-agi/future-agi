package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func TestExtractionPolicyOnlyAllowsSizeExclusions(t *testing.T) {
	for _, c := range []struct {
		name   string
		report observedcatalog.Report
		count  uint64
		fails  bool
	}{
		{"complete", observedcatalog.Report{Complete: true}, 0, false},
		{"size", observedcatalog.Report{GapReasons: []string{"value_too_large"}}, 1, false},
		{"duplicate-size", observedcatalog.Report{GapReasons: []string{"value_too_large", "value_too_large"}}, 1, false},
		{"empty-incomplete", observedcatalog.Report{}, 0, true},
		{"inconsistent-complete", observedcatalog.Report{Complete: true, GapReasons: []string{"value_too_large"}}, 0, true},
	} {
		t.Run(c.name, func(t *testing.T) {
			count, err := extractionPolicyExclusion(c.report)
			if (err != nil) != c.fails || count != c.count {
				t.Fatalf("count=%d error=%v", count, err)
			}
		})
	}
	for _, reason := range []string{"key_too_large", "invalid_attribute_key", "max_keys", "max_array_members", "invalid_scalar", "invalid_boolean", "max_encoded_bytes", "unknown"} {
		for _, reasons := range [][]string{{reason}, {"value_too_large", reason}} {
			if count, err := extractionPolicyExclusion(observedcatalog.Report{GapReasons: reasons}); err == nil || count != 0 {
				t.Fatalf("unsafe report accepted: %v", reasons)
			}
		}
	}
}

func policyRow() map[string]any {
	row := exampleSpan(exampleScope()).Row
	row["is_deleted"], row["_version"] = 0, "1"
	row["observation_type"], row["service_name"], row["trace_id"], row["id"] = "span", "s", "t", "a"
	return row
}

func TestSizeExclusionRetainsExactLiveBatchAndSource(t *testing.T) {
	for _, c := range []struct {
		name, value string
		array       bool
		excluded    uint64
	}{
		{"scalar-boundary", strings.Repeat("x", 16384), false, 0},
		{"scalar-over", strings.Repeat("x", 16385), false, 1},
		{"copied-window-size", strings.Repeat("x", 36724), false, 1},
		{"large-copied-window", strings.Repeat("x", 1604765), false, 1},
		{"escaped-boundary", strings.Repeat("\x00", 16384), false, 0},
		{"encoded-guard", strings.Repeat("\x00", 32769), false, 1},
		{"fold-expansion", strings.Repeat("ΐ", 8192), false, 0},
		{"array-boundary", strings.Repeat("x", 4096), true, 0},
		{"array-over", strings.Repeat("x", 4097), true, 1},
	} {
		t.Run(c.name, func(t *testing.T) {
			row := policyRow()
			if c.array {
				row["attributes_extra"] = map[string]any{"candidate": []any{c.value, "kept"}}
			} else {
				row["attrs_string"] = map[string]string{"candidate": c.value, "later": "kept"}
			}
			before, _ := json.Marshal(row)
			scope := exampleScope()
			live, _, err := observedcatalog.Extract(observedcatalog.ScopedSpan{OrganizationID: scope.OrganizationID, WorkspaceID: scope.WorkspaceID, Row: row}, observedcatalog.DefaultLimits())
			if err != nil {
				t.Fatal(err)
			}
			start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
			batch, excluded, err := buildPage([]map[string]any{row}, scope, start, start.Add(time.Hour), observedcatalog.DefaultLimits())
			if err != nil || excluded != c.excluded || !reflect.DeepEqual(batch, observedcatalog.Merge(live)) {
				t.Fatalf("live/backfill parity failed: exclusions=%d err=%v", excluded, err)
			}
			after, _ := json.Marshal(row)
			if !bytes.Equal(before, after) {
				t.Fatal("source changed")
			}
			if err := batch.Validate(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestOtherExtractionGapsRejectTheWholePage(t *testing.T) {
	for _, c := range []struct {
		name   string
		mutate func(map[string]any)
		limits observedcatalog.Limits
	}{
		{"key-size", func(r map[string]any) { r["attrs_string"].(map[string]string)[strings.Repeat("k", 4097)] = "x" }, observedcatalog.DefaultLimits()},
		{"empty-key", func(r map[string]any) { r["attrs_string"].(map[string]string)[""] = "x" }, observedcatalog.DefaultLimits()},
		{"boolean", func(r map[string]any) { r["attrs_bool"] = map[string]uint8{"bad": 2} }, observedcatalog.DefaultLimits()},
		{"null-number", func(r map[string]any) { r["attrs_number"] = map[string]any{"bad": nil} }, observedcatalog.DefaultLimits()},
		{"null-string", func(r map[string]any) { r["attrs_string"] = map[string]any{"bad": nil} }, observedcatalog.DefaultLimits()},
		{"missing-maps", func(r map[string]any) { delete(r, "attrs_bool") }, observedcatalog.DefaultLimits()},
		{"overflow-json", func(r map[string]any) { r["attributes_extra"] = "{} trailing" }, observedcatalog.DefaultLimits()},
		{"scope", func(r map[string]any) { r["org_id"] = "00000000-0000-4000-8000-000000000099" }, observedcatalog.DefaultLimits()},
		{"tombstone", func(r map[string]any) { r["is_deleted"] = 2 }, observedcatalog.DefaultLimits()},
		{"timestamp", func(r map[string]any) { r["start_time"] = "invalid" }, observedcatalog.DefaultLimits()},
		{"key-cap", func(r map[string]any) {}, observedcatalog.Limits{MaxKeysPerSpan: 1, MaxArrayMembersPerSpan: 256}},
		{"array-cap", func(r map[string]any) {
			r["attributes_extra"] = map[string]any{"array": []any{strings.Repeat("x", 4097), "later"}}
		}, observedcatalog.Limits{MaxKeysPerSpan: 128, MaxArrayMembersPerSpan: 1}},
	} {
		t.Run(c.name, func(t *testing.T) {
			good, bad := policyRow(), policyRow()
			good["attrs_string"].(map[string]string)["oversize"] = strings.Repeat("x", 36724)
			bad["attrs_string"].(map[string]string)["oversize"] = strings.Repeat("x", 36724)
			c.mutate(bad)
			start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
			batch, excluded, err := buildPage([]map[string]any{good, bad}, exampleScope(), start, start.Add(time.Hour), c.limits)
			if err == nil || !batch.Empty() || excluded != 0 {
				t.Fatal("fatal or mixed gap returned a partial page")
			}
		})
	}
}

// The HTTP fake serves immutable canonical rows. All source reads retain the
// real CLI's read-only, time, memory and byte settings.
func policyOptions(t *testing.T) options {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("readonly") != "1" {
			t.Error("source read is not read-only")
		}
		if r.URL.Query().Get("param_limit") != "" {
			fmt.Fprint(w, `{"observation_type":"span","service_name":"s","trace_id":"t","id":"a"}`)
			return
		}
		row := policyRow()
		row["start_time"] = r.URL.Query().Get("param_start")
		row["attrs_string"] = map[string]string{"oversize": strings.Repeat("x", 36724), "second": strings.Repeat("y", 16385), "kept": "eligible"}
		if err := json.NewEncoder(w).Encode(row); err != nil {
			t.Error(err)
		}
	}))
	t.Cleanup(server.Close)
	reader, err := newSourceReader(server.URL, "source", "readonly", "test")
	if err != nil {
		t.Fatal(err)
	}
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	return options{project: exampleScope().ProjectID, source: reader, since: start, until: start.Add(2 * time.Hour), apply: true,
		checkpointPath: filepath.Join(t.TempDir(), "progress.json"), maxPages: 1, pageSize: 2, limits: observedcatalog.DefaultLimits()}
}

func TestExclusionCheckpointResumeAndPreview(t *testing.T) {
	cfg := policyOptions(t)
	scopes := &testScopeReader{scope: exampleScope()}
	var output bytes.Buffer
	preview := cfg
	preview.apply, preview.maxPages = false, 2
	if err := runSpans(context.Background(), preview, scopes, nil, &output); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cfg.checkpointPath); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("preview wrote checkpoint")
	}
	decoder := json.NewDecoder(&output)
	for i := 0; i < 2; i++ {
		var receipt map[string]any
		if err := decoder.Decode(&receipt); err != nil {
			t.Fatal(err)
		}
		if receipt["policy_exclusion_spans"] != float64(1) || receipt["consumer_visibility_verified"] != false {
			t.Fatal("incorrect preview receipt")
		}
		if _, ok := receipt["recorded_policy_exclusion_spans"]; ok {
			t.Fatal("preview claimed persisted exclusions")
		}
	}
	// An old strict checkpoint with no counter resumes at its existing cursor.
	initial := checkpoint{Binding: scanBinding(cfg, exampleScope()), Hour: lastHour(cfg.until)}
	if err := saveCheckpoint(cfg.checkpointPath, initial); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadFile(cfg.checkpointPath)
	if bytes.Contains(before, []byte("recorded_policy_exclusion_spans")) {
		t.Fatal("old checkpoint fixture contains counter")
	}
	publisher := &testPublisher{}
	output.Reset()
	if err := runSpans(context.Background(), cfg, scopes, publisher, &output); err == nil || !strings.Contains(err.Error(), "page budget") {
		t.Fatal("bounded scan not stopped", err)
	}
	first, err := loadCheckpoint(cfg.checkpointPath, initial.Binding, initial.Hour)
	if err != nil || first.PolicyExclusionSpans != 1 || first.Complete || publisher.calls != 1 {
		t.Fatal("incorrect first checkpoint", err)
	}
	if err := runSpans(context.Background(), cfg, scopes, publisher, &output); err != nil {
		t.Fatal(err)
	}
	last, err := loadCheckpoint(cfg.checkpointPath, initial.Binding, initial.Hour)
	if err != nil || last.PolicyExclusionSpans != 2 || !last.Complete || publisher.calls != 2 {
		t.Fatal("resume lost/doubled exclusion count", err)
	}
	completed, _ := os.ReadFile(cfg.checkpointPath)
	if err := runSpans(context.Background(), cfg, scopes, publisher, &output); err != nil || publisher.calls != 2 {
		t.Fatal("completed checkpoint replayed", err)
	}
	after, _ := os.ReadFile(cfg.checkpointPath)
	if !bytes.Equal(completed, after) {
		t.Fatal("completed checkpoint rewritten")
	}
	if bytes.Contains(output.Bytes(), []byte("eligible")) || bytes.Contains(output.Bytes(), []byte("oversize")) {
		t.Fatal("receipt exposed source attributes")
	}
}

type policyFailWriter struct{}

func (policyFailWriter) Write([]byte) (int, error) { return 0, io.ErrClosedPipe }

func TestExclusionProgressRequiresAcknowledgement(t *testing.T) {
	for _, scenario := range []string{"publish", "ownership", "output", "overflow"} {
		t.Run(scenario, func(t *testing.T) {
			cfg := policyOptions(t)
			scopes := &testScopeReader{scope: exampleScope()}
			publisher := &testPublisher{fail: scenario == "publish"}
			initial := checkpoint{Binding: scanBinding(cfg, exampleScope()), Hour: lastHour(cfg.until), PolicyExclusionSpans: 7}
			if scenario == "overflow" {
				initial.PolicyExclusionSpans = ^uint64(0)
			}
			if err := saveCheckpoint(cfg.checkpointPath, initial); err != nil {
				t.Fatal(err)
			}
			before, _ := os.ReadFile(cfg.checkpointPath)
			if scenario == "ownership" {
				scopes.changeAt = 2
			}
			var out io.Writer = &bytes.Buffer{}
			if scenario == "output" {
				out = policyFailWriter{}
			}
			if err := runSpans(context.Background(), cfg, scopes, publisher, out); err == nil {
				t.Fatal("failure returned success")
			}
			after, _ := os.ReadFile(cfg.checkpointPath)
			if scenario == "output" {
				progress, err := loadCheckpoint(cfg.checkpointPath, initial.Binding, initial.Hour)
				if err != nil || progress.PolicyExclusionSpans != 8 || publisher.calls != 1 {
					t.Fatal("acknowledged progress lost after output failure", err)
				}
			} else if !bytes.Equal(before, after) {
				t.Fatal("checkpoint advanced on failed page")
			}
			if (scenario == "ownership" || scenario == "overflow") && publisher.calls != 0 {
				t.Fatal("unsafe page published")
			}
		})
	}
}
