package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

func testEnvironment() map[string]string {
	return map[string]string{
		"FI_PG_DSN":                        "postgres://test:test@127.0.0.1:15543/test",
		"FI_OBSERVED_BACKFILL_CH_URL":      "http://127.0.0.1:18143",
		"FI_OBSERVED_BACKFILL_CH_DATABASE": "source", "FI_OBSERVED_BACKFILL_CH_USERNAME": "readonly",
		"FI_OBSERVED_CATALOG_KAFKA_BROKERS": "127.0.0.1:19094",
	}
}

func TestBackfillDefaultsToPreviewAndValidatesSelection(t *testing.T) {
	env := testEnvironment()
	getenv := func(key string) string { return env[key] }
	base := []string{"--project", exampleScope().ProjectID, "--since", "2026-01-01T00:00:00Z", "--until", "2026-01-02T00:00:00Z"}
	cfg, err := parseOptions(base, getenv)
	if err != nil || cfg.apply {
		t.Fatal("preview not default", err)
	}
	if _, err := parseOptions(append(base, "--apply"), getenv); err == nil {
		t.Fatal("apply without checkpoint accepted")
	}
	if _, err := parseOptions(append(base, "--legacy-epoch", "1"), getenv); err == nil {
		t.Fatal("legacy epoch applied to raw spans")
	}
	if _, err := parseOptions(append(base, "--page-size", "257"), getenv); err == nil {
		t.Fatal("unbounded page accepted")
	}
	env["FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN"] = "500"
	cfg, err = parseOptions(base, getenv)
	if err != nil || cfg.limits.MaxKeysPerSpan != 500 {
		t.Fatal("backfill differs from live extraction limits", err)
	}
	legacy := []string{"--source", "legacy", "--project", exampleScope().ProjectID, "--legacy-epoch", "1", "--legacy-revision", "3", "--legacy-build", "00000000-0000-4000-8000-000000000040"}
	if _, err := parseOptions(legacy, getenv); err != nil {
		t.Fatal("legacy preview rejected", err)
	}
	if _, err := parseOptions(append(legacy, "--apply", "--checkpoint", "/tmp/test"), getenv); err == nil {
		t.Fatal("unverified legacy apply accepted")
	}
}

func TestCanonicalNormalizationPreservesTypesAndInput(t *testing.T) {
	scope := exampleScope()
	span := exampleSpan(scope)
	encoded, _ := json.Marshal(span.Row)
	var persisted map[string]any
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	if err := decoder.Decode(&persisted); err != nil {
		t.Fatal(err)
	}
	before, _ := json.Marshal(persisted)
	canonical, err := canonicalRow(persisted)
	if err != nil {
		t.Fatal(err)
	}
	after, _ := json.Marshal(persisted)
	if !bytes.Equal(before, after) {
		t.Fatal("normalization mutated source payload")
	}
	live, _, _ := observedcatalog.Extract(span, observedcatalog.DefaultLimits())
	backfill, report, err := observedcatalog.Extract(observedcatalog.ScopedSpan{OrganizationID: scope.OrganizationID, WorkspaceID: scope.WorkspaceID, Row: canonical}, observedcatalog.DefaultLimits())
	if err != nil || !report.Complete {
		t.Fatal(report, err)
	}
	if merged := observedcatalog.Merge(live, backfill); len(merged.Values) != len(live.Values) || len(merged.Keys) != len(live.Keys) {
		t.Fatal("canonical JSON changed typed observations")
	}
}

func TestCanonicalNormalizationRejectsNullInsteadOfInventingZero(t *testing.T) {
	for _, field := range []string{"attrs_string", "attrs_number", "attrs_bool"} {
		row := exampleSpan(exampleScope()).Row
		row[field] = map[string]any{"value": nil}
		if _, err := canonicalRow(row); err == nil {
			t.Fatalf("%s null became a scalar", field)
		}
	}
}

type testScopeReader struct {
	scope    observedcatalog.Scope
	calls    int
	changeAt int
}

func (r *testScopeReader) Scope(context.Context, string) (observedcatalog.Scope, error) {
	r.calls++
	if r.changeAt > 0 && r.calls >= r.changeAt {
		return observedcatalog.Scope{}, errors.New("deleted project")
	}
	return r.scope, nil
}

type testPublisher struct {
	calls int
	fail  bool
}

func (p *testPublisher) Publish(context.Context, observedcatalog.Batch) error {
	p.calls++
	if p.fail {
		return errors.New("broker unavailable")
	}
	return nil
}

func TestFailedPublishOrOwnershipChangeNeverAdvancesCheckpoint(t *testing.T) {
	for _, scenario := range []string{"publish", "ownership"} {
		t.Run(scenario, func(t *testing.T) {
			scope := exampleScope()
			row := exampleSpan(scope).Row
			row["is_deleted"], row["_version"] = 0, "1"
			row["observation_type"], row["service_name"], row["trace_id"], row["id"] = "span", "s", "t", "a"
			payload, _ := json.Marshal(row)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Query().Get("param_limit") != "" {
					w.Write([]byte(`{"observation_type":"span","service_name":"s","trace_id":"t","id":"a"}`))
					return
				}
				w.Write(payload)
			}))
			defer server.Close()
			reader, _ := newSourceReader(server.URL, "source", "readonly", "test")
			start := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
			cfg := options{project: scope.ProjectID, source: reader, since: start, until: start.Add(time.Hour), apply: true, checkpointPath: filepath.Join(t.TempDir(), "progress.json"), maxPages: 1, pageSize: 2, limits: observedcatalog.DefaultLimits()}
			ownership := &testScopeReader{scope: scope}
			publisher := &testPublisher{fail: true}
			if scenario == "ownership" {
				ownership.changeAt = 2
			}
			if err := runSpans(context.Background(), cfg, ownership, publisher, &bytes.Buffer{}); err == nil {
				t.Fatal("failure reported success")
			}
			if _, err := os.Stat(cfg.checkpointPath); !errors.Is(err, os.ErrNotExist) {
				t.Fatal("checkpoint advanced before acknowledgement")
			}
			if scenario == "ownership" && publisher.calls != 0 {
				t.Fatal("published after project deletion")
			}
		})
	}
}

func TestCheckpointDoesNotContainCredentials(t *testing.T) {
	env := testEnvironment()
	env["FI_OBSERVED_BACKFILL_CH_PASSWORD"] = "private-test-password"
	cfg, err := parseOptions([]string{"--project", exampleScope().ProjectID, "--since", "2026-01-01T00:00:00Z", "--until", "2026-01-02T00:00:00Z"}, func(k string) string { return env[k] })
	if err != nil {
		t.Fatal(err)
	}
	first := scanBinding(cfg, exampleScope())
	cfg.source.password = "rotated-password"
	if first != scanBinding(cfg, exampleScope()) {
		t.Fatal("credential rotation changed scan binding")
	}
	cfg.kafka.Topic = "new-topic"
	if first == scanBinding(cfg, exampleScope()) {
		t.Fatal("destination change did not change binding")
	}
}

func TestSourceURLsCannotCarrySecretsOrQueryOverrides(t *testing.T) {
	for _, origin := range []string{"https://u:p@example.com", "https://example.com/?readonly=0", "file:///etc/passwd", "http://example.com/path"} {
		if _, err := newSourceReader(origin, "source", "reader", ""); err == nil {
			t.Fatal("unsafe source URL accepted")
		}
	}
	reader, _ := newSourceReader("http://127.0.0.1:1", "source", "reader", "")
	_, err := reader.selectRows(context.Background(), "SELECT 1", url.Values{"param_bad": {"one", "two"}})
	if err == nil || strings.Contains(err.Error(), "one") {
		t.Fatal("invalid parameters accepted or exposed", err)
	}
}
