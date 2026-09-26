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
	"sync"
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

// The read path derives coverage from min(first_seen) in the index. Replaying
// the oldest hour first drives that floor to its final value on page one, so a
// scan that is still hours from done reports the index as covering all retained
// history. Descending keeps source spans below the floor until the scan really
// has finished, which is the condition the read path already checks. Nothing
// else pins the direction: every other case here spans a single hour, where
// ascending and descending are indistinguishable.
func TestSpanScanReplaysNewestHourFirst(t *testing.T) {
	scope := exampleScope()
	row := exampleSpan(scope).Row
	row["is_deleted"], row["_version"] = 0, "1"
	row["observation_type"], row["service_name"], row["trace_id"], row["id"] = "span", "s", "t", "a"
	payload, _ := json.Marshal(row)

	var mu sync.Mutex
	var requested []time.Time
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if start := r.URL.Query().Get("param_start"); start != "" {
			if hour, err := time.Parse("2006-01-02 15:04:05.000000", start); err == nil {
				mu.Lock()
				if len(requested) == 0 || !requested[len(requested)-1].Equal(hour) {
					requested = append(requested, hour)
				}
				mu.Unlock()
			}
		}
		if r.URL.Query().Get("param_limit") != "" {
			w.Write([]byte(`{"observation_type":"span","service_name":"s","trace_id":"t","id":"a"}`))
			return
		}
		w.Write(payload)
	}))
	defer server.Close()

	reader, _ := newSourceReader(server.URL, "source", "readonly", "test")
	since := time.Date(2026, 1, 1, 9, 0, 0, 0, time.UTC)
	until := since.Add(5 * time.Hour)
	cfg := options{
		project: scope.ProjectID, source: reader, since: since, until: until,
		checkpointPath: filepath.Join(t.TempDir(), "progress.json"),
		maxPages:       50, pageSize: 2, limits: observedcatalog.DefaultLimits(),
	}
	if err := runSpans(context.Background(), cfg, &testScopeReader{scope: scope}, &testPublisher{}, &bytes.Buffer{}); err != nil {
		t.Fatalf("scan failed: %v", err)
	}

	want := []time.Time{
		since.Add(4 * time.Hour), since.Add(3 * time.Hour), since.Add(2 * time.Hour),
		since.Add(time.Hour), since,
	}
	if len(requested) != len(want) {
		t.Fatalf("scanned %d hours, want %d: %v", len(requested), len(want), requested)
	}
	for i, hour := range want {
		if !requested[i].Equal(hour) {
			t.Fatalf("hour %d was %s, want %s (full order: %v)", i, requested[i], hour, requested)
		}
	}
}

// --until is exclusive, so a value exactly on the hour must not start the scan
// in a bucket that can hold no rows.
func TestExclusiveUntilSelectsTheLastHourThatCanHoldRows(t *testing.T) {
	base := time.Date(2026, 1, 1, 9, 0, 0, 0, time.UTC)
	for _, c := range []struct{ until, want time.Time }{
		{base, base.Add(-time.Hour)},
		{base.Add(time.Second), base},
		{base.Add(59 * time.Minute), base},
	} {
		if got := lastHour(c.until); !got.Equal(c.want) {
			t.Fatalf("lastHour(%s) = %s, want %s", c.until, got, c.want)
		}
	}
}

// A resumable scan has to accept its own freshly-seeded checkpoint. --until on
// an exact hour selects no rows in its own bucket, so seeding there put the
// scan outside its own bounds and it refused to start:
//
//	checkpoint hour is outside requested scan
//
// Only the integration suite exercised --apply, so unit coverage missed it.
func TestApplyAcceptsItsOwnFreshCheckpoint(t *testing.T) {
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

	for _, until := range []time.Time{
		time.Date(2026, 1, 1, 13, 0, 0, 0, time.UTC),  // exactly on the hour
		time.Date(2026, 1, 1, 13, 30, 0, 0, time.UTC), // mid-hour
	} {
		t.Run(until.Format("15:04"), func(t *testing.T) {
			cfg := options{
				project: scope.ProjectID, source: reader,
				since: time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC), until: until,
				apply: true, checkpointPath: filepath.Join(t.TempDir(), "progress.json"),
				maxPages: 20, pageSize: 2, limits: observedcatalog.DefaultLimits(),
			}
			if err := runSpans(context.Background(), cfg, &testScopeReader{scope: scope}, &testPublisher{}, &bytes.Buffer{}); err != nil {
				t.Fatalf("fresh --apply scan refused to run: %v", err)
			}
			// And the checkpoint it wrote must be resumable by the same command.
			if err := runSpans(context.Background(), cfg, &testScopeReader{scope: scope}, &testPublisher{}, &bytes.Buffer{}); err != nil {
				t.Fatalf("resume from own checkpoint failed: %v", err)
			}
		})
	}
}
