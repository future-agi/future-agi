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
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
)

// eventLog records, in order, what the purge and the publisher saw.
type eventLog struct {
	mu     sync.Mutex
	events []string
}

func (l *eventLog) add(event string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.events = append(l.events, event)
}

func (l *eventLog) all() []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]string(nil), l.events...)
}

func (l *eventLog) count(prefix string) int {
	n := 0
	for _, event := range l.all() {
		if strings.HasPrefix(event, prefix) {
			n++
		}
	}
	return n
}

// purgeRecorded reads the checkpoint file as it is on disk right now.
func purgeRecorded(path string) bool {
	data, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	var saved checkpoint
	return json.Unmarshal(data, &saved) == nil && saved.Purged
}

type recordingPurger struct {
	log    *eventLog
	path   string
	err    error
	scopes []observedcatalog.Scope
}

func (p *recordingPurger) DeleteProject(_ context.Context, scope observedcatalog.Scope) error {
	p.log.add(fmt.Sprintf("purge:recorded=%t", purgeRecorded(p.path)))
	p.scopes = append(p.scopes, scope)
	return p.err
}

type recordingPublisher struct {
	log  *eventLog
	path string
}

func (p *recordingPublisher) Publish(context.Context, observedcatalog.Batch) error {
	p.log.add(fmt.Sprintf("publish:recorded=%t", purgeRecorded(p.path)))
	return nil
}

// oneSpan is the project's only stored span, inside the default range.
var oneSpan = []time.Time{time.Date(2026, 1, 1, 12, 1, 0, 0, time.UTC)}

// replaceSource answers the reads a --replace run makes: the three bounded span
// existence checks (evaluated over the stored span start times), the identity
// pages and the payload pages. A project without stored spans has none.
func replaceSource(t *testing.T, spans []time.Time, failExistence bool) *sourceReader {
	t.Helper()
	row := policyRow()
	row["observation_type"], row["service_name"], row["trace_id"], row["id"] = "span", "s", "t", "a"
	payload, _ := json.Marshal(row)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		query := r.URL.Query()
		bound := func(name string) time.Time {
			at, err := time.Parse("2006-01-02 15:04:05.000000", query.Get(name))
			if err != nil {
				t.Errorf("unparsable bound %s: %v", name, err)
			}
			return at
		}
		switch {
		case strings.Contains(string(body), "AS present"):
			if failExistence {
				w.WriteHeader(http.StatusInternalServerError)
				return
			}
			if !strings.Contains(string(body), "project_id = {project:UUID}") || !strings.Contains(string(body), "LIMIT 1") || query.Get("param_project") != exampleScope().ProjectID {
				t.Error("existence read is not bound to the project or not limited to one row")
			}
			hit := false
			for _, at := range spans {
				switch {
				case query.Get("param_from") != "":
					hit = hit || (!at.Before(bound("param_from")) && at.Before(bound("param_to")))
				case strings.Contains(string(body), "start_time < {bound"):
					hit = hit || at.Before(bound("param_bound"))
				case strings.Contains(string(body), "start_time >= {bound"):
					hit = hit || !at.Before(bound("param_bound"))
				default:
					t.Error("unexpected existence read")
				}
			}
			if hit {
				w.Write([]byte(`{"present":1}`))
			}
		case len(spans) == 0:
			// A project without spans has no identities and no payloads.
		case query.Get("param_limit") != "":
			w.Write([]byte(`{"observation_type":"span","service_name":"s","trace_id":"t","id":"a"}`))
		default:
			w.Write(payload)
		}
	}))
	t.Cleanup(server.Close)
	reader, err := newSourceReader(server.URL, "source", "readonly", "test")
	if err != nil {
		t.Fatal(err)
	}
	return reader
}

var replaceNow = time.Date(2026, 1, 1, 12, 30, 0, 0, time.UTC)

// Two hours (13:00 and 12:00), one page each; the one stored span is at 12:01.
func replaceOptions(t *testing.T, reader *sourceReader, log *eventLog) (options, *recordingPurger, *recordingPublisher) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "progress.json")
	purger := &recordingPurger{log: log, path: path}
	cfg := options{
		project: exampleScope().ProjectID, source: reader, apply: true, replace: true, checkpointPath: path,
		since: time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC), until: time.Date(2026, 1, 1, 14, 0, 0, 0, time.UTC),
		maxPages: 20, pageSize: 2, limits: observedcatalog.DefaultLimits(),
		catalog: observedcatalog.ClickHouseConfig{URL: "http://127.0.0.1:18123", Database: "catalog"},
		purger:  purger, now: func() time.Time { return replaceNow },
	}
	return cfg, purger, &recordingPublisher{log: log, path: path}
}

func replaceEnvironment() map[string]string {
	env := testEnvironment()
	env["FI_OBSERVED_CATALOG_CH_URL"] = "http://127.0.0.1:18124"
	env["FI_OBSERVED_CATALOG_CH_DATABASE"] = "catalog"
	return env
}

func TestReplaceOptions(t *testing.T) {
	project := exampleScope().ProjectID
	spans := []string{"--project", project, "--since", "2026-01-01T00:00:00Z", "--until", "2026-01-02T00:00:00Z", "--apply", "--checkpoint", "/tmp/progress.json", "--replace"}
	legacy := []string{"--source", "legacy", "--project", project, "--legacy-epoch", "1", "--legacy-revision", "3",
		"--legacy-build", "00000000-0000-4000-8000-000000000040", "--apply", "--verified-legacy", "--checkpoint", "/tmp/progress.json", "--replace"}
	without := func(args []string, drop string) []string {
		var kept []string
		for _, arg := range args {
			if arg != drop {
				kept = append(kept, arg)
			}
		}
		return kept
	}
	allowEmpty := append(append([]string(nil), spans...), "--allow-empty-rebuild")
	for name, c := range map[string]struct {
		args   []string
		env    func(map[string]string)
		accept bool
	}{
		"empty rebuild allowed with --replace":    {args: allowEmpty, accept: true},
		"empty rebuild allowed without --replace": {args: without(allowEmpty, "--replace")},
		"kafka mode with the index connection":    {args: spans, accept: true},
		"direct mode":                             {args: spans, env: func(e map[string]string) { e["FI_OBSERVED_CATALOG_MODE"] = "direct" }, accept: true},
		"preview":                                 {args: without(without(without(spans, "--apply"), "--checkpoint"), "/tmp/progress.json")},
		"legacy source":                           {args: legacy},
		"no index url":                            {args: spans, env: func(e map[string]string) { delete(e, "FI_OBSERVED_CATALOG_CH_URL") }},
		"no index database":                       {args: spans, env: func(e map[string]string) { delete(e, "FI_OBSERVED_CATALOG_CH_DATABASE") }},
		"invalid index url":                       {args: spans, env: func(e map[string]string) { e["FI_OBSERVED_CATALOG_CH_URL"] = "http://u:p@127.0.0.1:1" }},
	} {
		t.Run(name, func(t *testing.T) {
			env := replaceEnvironment()
			if c.env != nil {
				c.env(env)
			}
			cfg, err := parseOptions(c.args, func(k string) string { return env[k] })
			if c.accept {
				if err != nil || !cfg.replace || cfg.catalog.URL != "http://127.0.0.1:18124" || cfg.catalog.Database != "catalog" {
					t.Fatalf("valid --replace refused or index not read: %+v %v", cfg.catalog, err)
				}
				return
			}
			if err == nil {
				t.Fatal("unsafe --replace accepted")
			}
		})
	}
	env := replaceEnvironment()
	if cfg, err := parseOptions(allowEmpty, func(k string) string { return env[k] }); err != nil || !cfg.allowEmpty {
		t.Fatalf("--allow-empty-rebuild not read: %v", err)
	}
	if cfg, err := parseOptions(spans, func(k string) string { return env[k] }); err != nil || cfg.allowEmpty {
		t.Fatalf("empty rebuild allowed without the flag: %v", err)
	}
	// Kafka mode keeps publishing through Kafka: the index connection is only
	// for the delete.
	cfg, err := parseOptions(spans, func(k string) string { return env[k] })
	if err != nil || cfg.index.URL != "" || len(cfg.kafka.Brokers) == 0 {
		t.Fatalf("--replace switched the publish path: %+v %v", cfg, err)
	}
	// And a missing index connection is only an error with --replace.
	delete(env, "FI_OBSERVED_CATALOG_CH_URL")
	if _, err := parseOptions(without(spans, "--replace"), func(k string) string { return env[k] }); err != nil {
		t.Fatalf("plain apply now needs the index connection: %v", err)
	}
}

func TestReplacePurgesOnceBeforeTheFirstPublishAndRecordsIt(t *testing.T) {
	log := &eventLog{}
	cfg, purger, publisher := replaceOptions(t, replaceSource(t, oneSpan, false), log)
	scope := &testScopeReader{scope: exampleScope()}
	var output bytes.Buffer

	// A one-page budget stops after the first hour, as an interrupted run would.
	cfg.maxPages = 1
	if err := runSpans(context.Background(), cfg, scope, publisher, &output); err == nil || !strings.Contains(err.Error(), "page budget reached") {
		t.Fatalf("expected the page budget to stop the first run: %v", err)
	}
	events := log.all()
	if len(events) != 2 || events[0] != "purge:recorded=false" || events[1] != "publish:recorded=true" {
		t.Fatalf("purge must run first, before the checkpoint records it, and publishing must see it recorded: %v", events)
	}
	if len(purger.scopes) != 1 || purger.scopes[0] != exampleScope() {
		t.Fatalf("purge not scoped to the verified project: %+v", purger.scopes)
	}
	if !purgeRecorded(cfg.checkpointPath) || !strings.Contains(output.String(), `"catalog_purged":true`) || !strings.Contains(output.String(), `"project_has_spans":true`) {
		t.Fatalf("purge not recorded or not reported: %s", output.String())
	}

	// Resuming the same checkpoint rebuilds the rest and never deletes again.
	cfg.maxPages = 20
	if err := runSpans(context.Background(), cfg, scope, publisher, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if log.count("purge") != 1 || log.count("publish") != 2 {
		t.Fatalf("resume purged again or did not finish: %v", log.all())
	}
	// A finished checkpoint run again is still not a reason to delete.
	if err := runSpans(context.Background(), cfg, scope, publisher, &bytes.Buffer{}); err != nil || log.count("purge") != 1 {
		t.Fatalf("completed checkpoint purged again: %v %v", err, log.all())
	}
}

func TestReplacePurgeFailurePublishesNothingAndIsNotRecorded(t *testing.T) {
	log := &eventLog{}
	cfg, purger, publisher := replaceOptions(t, replaceSource(t, oneSpan, false), log)
	purger.err = errors.New("index unavailable")
	err := runSpans(context.Background(), cfg, &testScopeReader{scope: exampleScope()}, publisher, &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "nothing published") {
		t.Fatalf("purge failure not reported: %v", err)
	}
	if log.count("publish") != 0 {
		t.Fatal("published after a failed purge")
	}
	if _, statErr := os.Stat(cfg.checkpointPath); !errors.Is(statErr, os.ErrNotExist) {
		t.Fatal("checkpoint written after a failed purge")
	}
	// The retry repeats the deletes, then rebuilds.
	purger.err = nil
	if err := runSpans(context.Background(), cfg, &testScopeReader{scope: exampleScope()}, publisher, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if log.count("purge") != 2 || !purgeRecorded(cfg.checkpointPath) {
		t.Fatalf("retry did not purge and record: %v", log.all())
	}
}

func TestReplaceNeverDeletesInTheMiddleOfAScan(t *testing.T) {
	log := &eventLog{}
	cfg, _, publisher := replaceOptions(t, replaceSource(t, oneSpan, false), log)
	scope := exampleScope()
	progress := checkpoint{Binding: scanBinding(cfg, scope), Hour: lastHour(cfg.until), Pages: 1}
	if err := saveCheckpoint(cfg.checkpointPath, progress); err != nil {
		t.Fatal(err)
	}
	err := runSpans(context.Background(), cfg, &testScopeReader{scope: scope}, publisher, &bytes.Buffer{})
	if err == nil || len(log.all()) != 0 {
		t.Fatalf("a scan in progress without a recorded purge was purged or resumed: %v %v", err, log.all())
	}
}

func TestReplaceRangeGuardsRefuseBeforeDeleting(t *testing.T) {
	at := func(hour, minute int) time.Time { return time.Date(2026, 1, 1, hour, minute, 0, 0, time.UTC) }
	for name, c := range map[string]struct {
		spans         []time.Time // nil means oneSpan
		none          bool        // the project has no stored span
		mutate        func(*options)
		failExistence bool
		wantErr       string
		purges        int
	}{
		"until before now": {
			mutate: func(c *options) { c.since, c.until = replaceNow.Add(-2*time.Hour), replaceNow.Add(-time.Hour) }, wantErr: "at least",
		},
		"until at now": {
			mutate: func(c *options) { c.until = replaceNow }, wantErr: "at least",
		},
		"until just short of the margin": {
			mutate: func(c *options) { c.until = replaceNow.Add(replaceMinUntilLead - time.Nanosecond) }, wantErr: "at least",
		},
		"until at the margin": {
			mutate: func(c *options) { c.until = replaceNow.Add(replaceMinUntilLead) }, purges: 1,
		},
		"until just beyond the cap": {
			mutate: func(c *options) { c.until = replaceNow.Add(replaceMaxUntilLead + time.Nanosecond) }, wantErr: "at most",
		},
		"until at the cap": {
			mutate: func(c *options) { c.until, c.maxPages = replaceNow.Add(replaceMaxUntilLead), 100 }, purges: 1,
		},
		"a span before since": {
			mutate: func(c *options) { c.since = time.Date(2026, 1, 1, 12, 1, 0, 1000, time.UTC) }, wantErr: "oldest stored span",
		},
		"one of two spans before since": {
			spans:   []time.Time{at(11, 0), at(12, 1)},
			mutate:  func(c *options) {},
			wantErr: "oldest stored span",
		},
		"since at the oldest span": {
			mutate: func(c *options) { c.since = at(12, 1) }, purges: 1,
		},
		"a span at until": {
			spans:   []time.Time{at(12, 1), at(13, 30)},
			mutate:  func(c *options) { c.until = at(13, 30) },
			wantErr: "newest stored span",
		},
		"a span after until": {
			spans:   []time.Time{at(12, 1), at(14, 0)},
			mutate:  func(c *options) { c.until = at(13, 30) },
			wantErr: "newest stored span",
		},
		"the last span just before until": {
			spans:  []time.Time{at(12, 1), at(13, 29)},
			mutate: func(c *options) { c.until = at(13, 30) }, purges: 1,
		},
		"existence read unreadable": {
			mutate: func(*options) {}, failExistence: true, wantErr: "source SELECT failed",
		},
		// A source pointed at the wrong database looks like a project without spans.
		"no stored spans": {
			none: true, mutate: func(*options) {}, wantErr: "--allow-empty-rebuild",
		},
		"no stored spans, empty rebuild allowed": {
			none: true, mutate: func(c *options) { c.allowEmpty = true }, purges: 1,
		},
		"stored spans, empty rebuild allowed": {
			mutate: func(c *options) { c.allowEmpty = true }, purges: 1,
		},
	} {
		t.Run(name, func(t *testing.T) {
			spans := c.spans
			if spans == nil && !c.none {
				spans = oneSpan
			}
			log := &eventLog{}
			cfg, _, publisher := replaceOptions(t, replaceSource(t, spans, c.failExistence), log)
			c.mutate(&cfg)
			var output bytes.Buffer
			err := runSpans(context.Background(), cfg, &testScopeReader{scope: exampleScope()}, publisher, &output)
			if c.wantErr != "" {
				if err == nil || !strings.Contains(err.Error(), c.wantErr) {
					t.Fatalf("guard did not refuse (%q): %v", c.wantErr, err)
				}
				if log.count("purge") != 0 || log.count("publish") != 0 {
					t.Fatalf("deleted or published before refusing: %v", log.all())
				}
				if _, statErr := os.Stat(cfg.checkpointPath); !errors.Is(statErr, os.ErrNotExist) {
					t.Fatal("checkpoint written by a refused run")
				}
				return
			}
			if err != nil || log.count("purge") != c.purges {
				t.Fatalf("valid range refused or not purged: %v %v", err, log.all())
			}
			if c.none && (!strings.Contains(output.String(), `"project_has_spans":false`) || log.count("publish") != 0) {
				t.Fatalf("project without spans not reported or published: %s", output.String())
			}
			if !c.none && !strings.Contains(output.String(), `"project_has_spans":true`) {
				t.Fatalf("project with spans not reported: %s", output.String())
			}
		})
	}
}

// changingScope answers the ownership read with another scope from the second
// call on, as a project moved to another workspace between the reads.
type changingScope struct {
	first, later observedcatalog.Scope
	calls        int
}

func (r *changingScope) Scope(context.Context, string) (observedcatalog.Scope, error) {
	r.calls++
	if r.calls >= 2 {
		return r.later, nil
	}
	return r.first, nil
}

func (r *changingScope) Holding(context.Context, string) (bool, error) { return false, nil }

func TestReplaceOwnershipChangeBeforeTheDeleteDeletesNothing(t *testing.T) {
	moved := exampleScope()
	moved.WorkspaceID = "00000000-0000-4000-8000-000000000099"
	for name, scopes := range map[string]scopeReader{
		"project deleted": &testScopeReader{scope: exampleScope(), changeAt: 2},
		"project moved":   &changingScope{first: exampleScope(), later: moved},
	} {
		t.Run(name, func(t *testing.T) {
			log := &eventLog{}
			cfg, purger, publisher := replaceOptions(t, replaceSource(t, oneSpan, false), log)
			err := runSpans(context.Background(), cfg, scopes, publisher, &bytes.Buffer{})
			if err == nil || !strings.Contains(err.Error(), "ownership changed before the purge") {
				t.Fatalf("ownership change before the delete not refused: %v", err)
			}
			if len(purger.scopes) != 0 || log.count("publish") != 0 {
				t.Fatalf("deleted or published after the ownership changed: %v", log.all())
			}
			if _, statErr := os.Stat(cfg.checkpointPath); !errors.Is(statErr, os.ErrNotExist) {
				t.Fatal("checkpoint written by a refused run")
			}
		})
	}
}

func TestReplaceBindingSeparatesPurgeFromPlainCheckpoints(t *testing.T) {
	cfg, _, _ := replaceOptions(t, replaceSource(t, oneSpan, false), &eventLog{})
	scope := exampleScope()
	replaced := scanBinding(cfg, scope)
	plain := cfg
	plain.replace = false
	if replaced == scanBinding(plain, scope) {
		t.Fatal("a --replace checkpoint shares a binding with a plain run")
	}
	// The index connection only binds a purge: plain bindings, and so every
	// checkpoint written before this flag existed, are unchanged by it.
	if scanBinding(plain, scope) != scanBinding(options{project: cfg.project, source: cfg.source, since: cfg.since, until: cfg.until, limits: cfg.limits}, scope) {
		t.Fatal("the index connection changed a plain binding")
	}
	// Retrying with --allow-empty-rebuild must resume the same checkpoint.
	empty := cfg
	empty.allowEmpty = true
	if replaced != scanBinding(empty, scope) {
		t.Fatal("--allow-empty-rebuild changed the checkpoint binding")
	}
	moved := cfg
	moved.catalog.URL = "http://127.0.0.1:18999"
	if replaced == scanBinding(moved, scope) {
		t.Fatal("a purge of another index resumed this checkpoint")
	}
}

func TestReplaceCheckpointDoesNotResumeAPlainRunOrTheReverse(t *testing.T) {
	for _, order := range []string{"replace then plain", "plain then replace"} {
		t.Run(order, func(t *testing.T) {
			log := &eventLog{}
			cfg, purger, publisher := replaceOptions(t, replaceSource(t, oneSpan, false), log)
			plain := cfg
			plain.replace = false
			first, second := cfg, plain
			if order == "plain then replace" {
				first, second = plain, cfg
			}
			first.maxPages = 1
			if err := runSpans(context.Background(), first, &testScopeReader{scope: exampleScope()}, publisher, &bytes.Buffer{}); err == nil || !strings.Contains(err.Error(), "page budget reached") {
				t.Fatalf("first run: %v", err)
			}
			before := len(purger.scopes)
			published := log.count("publish")
			err := runSpans(context.Background(), second, &testScopeReader{scope: exampleScope()}, publisher, &bytes.Buffer{})
			if err == nil || !strings.Contains(err.Error(), "different scan") {
				t.Fatalf("checkpoint crossed between a plain and a --replace run: %v", err)
			}
			if len(purger.scopes) != before || log.count("publish") != published {
				t.Fatal("refused run still deleted or published")
			}
		})
	}
}

func TestReplaceRequiresApply(t *testing.T) {
	cfg, _, publisher := replaceOptions(t, replaceSource(t, oneSpan, false), &eventLog{})
	cfg.apply = false
	if err := runSpans(context.Background(), cfg, &testScopeReader{scope: exampleScope()}, publisher, &bytes.Buffer{}); err == nil {
		t.Fatal("a preview deleted catalog rows")
	}
}

// legacyHoldingSource serves definitions one per page (the second one has a
// later binding) and an empty value page. A request while `forbidden` is set
// fails the test: a refused import must not read its source.
func legacyHoldingSource(t *testing.T, forbidden bool) *sourceReader {
	t.Helper()
	var mu sync.Mutex
	definitions := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if forbidden {
			t.Error("a refused legacy import read its source")
		}
		body, _ := io.ReadAll(r.Body)
		if strings.Contains(string(body), "span_attribute_value_catalog") {
			return
		}
		mu.Lock()
		definitions++
		n := definitions
		mu.Unlock()
		json.NewEncoder(w).Encode(legacyTestDefinition(fmt.Sprintf("key%d", n), fmt.Sprint(n), "string"))
	}))
	t.Cleanup(server.Close)
	reader, err := newSourceReader(server.URL, "old_verified_catalog", "readonly_user", "local_test")
	if err != nil {
		t.Fatal(err)
	}
	return reader
}

func legacyHoldingOptions(t *testing.T, reader *sourceReader, apply bool, pageSize int) options {
	t.Helper()
	s := legacyTestSelection
	return options{project: s.Scope.ProjectID, mode: "legacy", source: reader, legacyEpoch: uint(s.Epoch), legacyRevision: s.Revision,
		legacyBuild: s.BuildToken, verifiedLegacy: apply, apply: apply, since: time.Unix(0, 0).UTC(), checkpointPath: filepath.Join(t.TempDir(), "progress.json"),
		maxPages: 10, pageSize: pageSize, limits: observedcatalog.DefaultLimits()}
}

func TestLegacyImportIsRefusedForAHoldingProject(t *testing.T) {
	for _, apply := range []bool{false, true} {
		t.Run(fmt.Sprintf("apply=%t", apply), func(t *testing.T) {
			cfg := legacyHoldingOptions(t, legacyHoldingSource(t, true), apply, 2)
			scopes := &testScopeReader{scope: legacyTestSelection.Scope, holding: true}
			published := &testPublisher{}
			var output bytes.Buffer
			err := runLegacy(context.Background(), cfg, scopes, published, &output)
			if !errors.Is(err, errHoldingProject) || !strings.Contains(err.Error(), "--replace") {
				t.Fatalf("holding project not refused with the remedy: %v", err)
			}
			if published.calls != 0 || output.Len() != 0 {
				t.Fatal("a refused import published or reported progress")
			}
			for _, path := range []string{cfg.checkpointPath, cfg.checkpointPath + ".lock"} {
				if _, statErr := os.Stat(path); !errors.Is(statErr, os.ErrNotExist) {
					t.Fatalf("a refused import left %s", filepath.Base(path))
				}
			}
		})
	}
}

func TestLegacyImportRunsForAProjectThatDoesNotHold(t *testing.T) {
	cfg := legacyHoldingOptions(t, legacyHoldingSource(t, false), true, 2)
	scopes := &testScopeReader{scope: legacyTestSelection.Scope}
	published := &testPublisher{}
	if err := runLegacy(context.Background(), cfg, scopes, published, io.Discard); err != nil {
		t.Fatalf("import of a non-holding project changed: %v", err)
	}
	// One definitions page and one (empty) values page; the project is asked
	// before the first page and again before each publish.
	if published.calls != 1 || scopes.holdingCall != 3 {
		t.Fatalf("published %d batches after %d holding reads", published.calls, scopes.holdingCall)
	}
	saved, err := loadCheckpoint(cfg.checkpointPath, scanBinding(cfg, legacyTestSelection.Scope), cfg.since)
	if err != nil || !saved.Complete || saved.Pages != 2 {
		t.Fatalf("checkpoint not complete: %+v %v", saved, err)
	}
	preview := legacyHoldingOptions(t, legacyHoldingSource(t, false), false, 2)
	if err := runLegacy(context.Background(), preview, &testScopeReader{scope: legacyTestSelection.Scope}, nil, io.Discard); err != nil {
		t.Fatalf("preview of a non-holding project changed: %v", err)
	}
}

func TestLegacyImportStopsWhenTheProjectStartsHolding(t *testing.T) {
	cfg := legacyHoldingOptions(t, legacyHoldingSource(t, false), true, 1)
	// Reads: 1 before the import, 2 before page one's publish, 3 before page two's.
	scopes := &testScopeReader{scope: legacyTestSelection.Scope, holdingFrom: 3}
	published := &testPublisher{}
	err := runLegacy(context.Background(), cfg, scopes, published, io.Discard)
	if !errors.Is(err, errHoldingProject) {
		t.Fatalf("a project that started holding was not refused: %v", err)
	}
	if published.calls != 1 {
		t.Fatalf("published %d pages; only the page before the change may go out", published.calls)
	}
	saved, err := loadCheckpoint(cfg.checkpointPath, scanBinding(cfg, legacyTestSelection.Scope), cfg.since)
	if err != nil || saved.Pages != 1 || saved.Complete {
		t.Fatalf("checkpoint moved past the refused page: %+v %v", saved, err)
	}
}

func TestLegacyImportStopsWhenTheHoldingReadFails(t *testing.T) {
	for name, c := range map[string]struct {
		errFrom       int
		published     int
		sourceAllowed bool
	}{
		"before the first page": {errFrom: 1, published: 0},
		"between pages":         {errFrom: 3, published: 1, sourceAllowed: true},
	} {
		for _, apply := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s apply=%t", name, apply), func(t *testing.T) {
				cfg := legacyHoldingOptions(t, legacyHoldingSource(t, !c.sourceAllowed), apply, 1)
				scopes := &testScopeReader{scope: legacyTestSelection.Scope, holdingErr: errors.New("marker unavailable"), holdingErrFrom: c.errFrom}
				published := &testPublisher{}
				err := runLegacy(context.Background(), cfg, scopes, published, io.Discard)
				if err == nil || !strings.Contains(err.Error(), "could not check") || errors.Is(err, errHoldingProject) {
					t.Fatalf("a failed read was not reported as a failed read: %v", err)
				}
				want := c.published
				if !apply {
					want = 0
				}
				if published.calls != want {
					t.Fatalf("published %d batches, want %d", published.calls, want)
				}
			})
		}
	}
}

func TestReplacePurgerDeletesFromTheCatalogNotTheSource(t *testing.T) {
	var mu sync.Mutex
	var databases []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		databases = append(databases, r.URL.Query().Get("database"))
		mu.Unlock()
	}))
	defer server.Close()
	env := replaceEnvironment()
	// One server holds both, so only the database names tell them apart.
	env["FI_OBSERVED_BACKFILL_CH_URL"], env["FI_OBSERVED_CATALOG_CH_URL"] = server.URL, server.URL
	args := []string{"--project", exampleScope().ProjectID, "--since", "2026-01-01T00:00:00Z", "--until", "2026-01-02T00:00:00Z",
		"--apply", "--checkpoint", filepath.Join(t.TempDir(), "progress.json"), "--replace"}
	cfg, err := parseOptions(args, func(k string) string { return env[k] })
	if err != nil {
		t.Fatal(err)
	}
	purger, err := newPurger(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if err := purger.DeleteProject(context.Background(), exampleScope()); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(databases) == 0 {
		t.Fatal("the purge sent nothing")
	}
	for _, database := range databases {
		if database != "catalog" {
			t.Fatalf("the purge reached database %q, want only the catalog", database)
		}
	}
}
