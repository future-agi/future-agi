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
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func TestForeignAndMissingOrganizationsAreQuarantinedNotPublished(t *testing.T) {
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	for _, org := range []any{nil, "", "00000000-0000-4000-8000-000000000099"} {
		good, rejected := policyRow(), policyRow()
		rejected["org_id"], rejected["id"] = org, "rejected"
		rejected["attrs_string"] = map[string]string{"private": "must-not-be-published"}
		before, _ := json.Marshal(rejected)
		want, _, err := buildPage([]map[string]any{good}, exampleScope(), start, start.Add(time.Hour), observedcatalog.DefaultLimits())
		if err != nil {
			t.Fatal(err)
		}
		got, report, err := buildPage([]map[string]any{good, rejected}, exampleScope(), start, start.Add(time.Hour), observedcatalog.DefaultLimits())
		if err != nil || !reflect.DeepEqual(got, want) || len(report.Quarantined) != 1 || report.PolicyExclusions != 0 {
			t.Fatalf("scope quarantine lost valid observations or leaked rejected ones: %+v %v", report, err)
		}
		if report.Quarantined[0].Key.Span != "rejected" || report.Quarantined[0].Project != exampleScope().ProjectID {
			t.Fatal("quarantine did not identify the rejected source row")
		}
		after, _ := json.Marshal(rejected)
		if !bytes.Equal(before, after) {
			t.Fatal("source row was changed")
		}
	}
}

func quarantineOptions(t *testing.T) options {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("readonly") != "1" {
			t.Error("source access is not read-only")
		}
		if r.URL.Query().Get("param_limit") != "" {
			fmt.Fprintln(w, `{"observation_type":"span","service_name":"s","trace_id":"t","id":"a"}`)
			fmt.Fprintln(w, `{"observation_type":"span","service_name":"s","trace_id":"t","id":"b"}`)
			return
		}
		good, rejected := policyRow(), policyRow()
		rejected["org_id"], rejected["id"] = "", "b"
		rejected["attrs_string"] = map[string]string{"private": "secret-value"}
		for _, row := range []map[string]any{good, rejected} {
			if err := json.NewEncoder(w).Encode(row); err != nil {
				t.Error(err)
			}
		}
	}))
	t.Cleanup(server.Close)
	reader, err := newSourceReader(server.URL, "source", "readonly", "test")
	if err != nil {
		t.Fatal(err)
	}
	start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	return options{project: exampleScope().ProjectID, source: reader, since: start, until: start.Add(time.Hour),
		apply: true, checkpointPath: filepath.Join(t.TempDir(), "progress.json"), maxPages: 1, pageSize: 3, limits: observedcatalog.DefaultLimits()}
}

func TestQuarantineIsDurableBeforeCheckpointAndResumeDoesNotRepeat(t *testing.T) {
	cfg := quarantineOptions(t)
	preview := cfg
	preview.apply = false
	if err := runSpans(context.Background(), preview, &testScopeReader{scope: exampleScope()}, nil, io.Discard); err != nil {
		t.Fatal(err)
	}
	audit := cfg.checkpointPath + ".quarantine.jsonl"
	for _, path := range []string{audit, cfg.checkpointPath} {
		if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
			t.Fatal("preview wrote state", err)
		}
	}
	publisher := &testPublisher{}
	if err := runSpans(context.Background(), cfg, &testScopeReader{scope: exampleScope()}, publisher, io.Discard); err != nil {
		t.Fatal(err)
	}
	progress, err := loadCheckpoint(cfg.checkpointPath, scanBinding(cfg, exampleScope()), lastHour(cfg.until))
	if err != nil || !progress.Complete || progress.QuarantinedSpans != 1 || progress.Rows != 2 || publisher.calls != 1 {
		t.Fatalf("incorrect quarantine checkpoint: %+v %v", progress, err)
	}
	data, err := os.ReadFile(audit)
	if err != nil {
		t.Fatal(err)
	}
	var receipt quarantinedSpan
	if err := json.Unmarshal(data, &receipt); err != nil || receipt.Key.Span != "b" || bytes.Contains(data, []byte("secret-value")) || bytes.Contains(data, []byte("attrs_string")) {
		t.Fatal("audit must contain only a recoverable identity", err)
	}
	info, err := os.Stat(audit)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatal("audit is not private", err)
	}
	if err := runSpans(context.Background(), cfg, &testScopeReader{scope: exampleScope()}, publisher, io.Discard); err != nil || publisher.calls != 1 {
		t.Fatal("completed page was republished", err)
	}
	after, _ := os.ReadFile(audit)
	if !bytes.Equal(data, after) {
		t.Fatal("completed page duplicated audit")
	}
}

func TestQuarantineFailureCannotPublishOrAdvance(t *testing.T) {
	for _, scenario := range []string{"audit-symlink", "audit-public", "publish", "ownership", "overflow"} {
		t.Run(scenario, func(t *testing.T) {
			cfg := quarantineOptions(t)
			initial := checkpoint{Binding: scanBinding(cfg, exampleScope()), Hour: lastHour(cfg.until)}
			if scenario == "overflow" {
				initial.QuarantinedSpans = ^uint64(0)
			}
			if err := saveCheckpoint(cfg.checkpointPath, initial); err != nil {
				t.Fatal(err)
			}
			before, _ := os.ReadFile(cfg.checkpointPath)
			audit := cfg.checkpointPath + ".quarantine.jsonl"
			if scenario == "audit-symlink" {
				if err := os.Symlink(cfg.checkpointPath, audit); err != nil {
					t.Fatal(err)
				}
			}
			if scenario == "audit-public" {
				if err := os.WriteFile(audit, nil, 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(audit, 0644); err != nil {
					t.Fatal(err)
				}
			}
			scopes := &testScopeReader{scope: exampleScope()}
			if scenario == "ownership" {
				scopes.changeAt = 2
			}
			publisher := &testPublisher{fail: scenario == "publish"}
			if err := runSpans(context.Background(), cfg, scopes, publisher, io.Discard); err == nil {
				t.Fatal("expected failure")
			}
			after, _ := os.ReadFile(cfg.checkpointPath)
			if !bytes.Equal(before, after) || (scenario != "publish" && publisher.calls != 0) {
				t.Fatal("failed audit/ownership/publish advanced progress")
			}
		})
	}
}
