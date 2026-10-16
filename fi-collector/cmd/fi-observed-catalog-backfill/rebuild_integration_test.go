package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/observedcatalog"
	"github.com/google/uuid"
)

func seedCatalog(t *testing.T, sink *observedcatalog.ClickHouseSink, scope observedcatalog.Scope, seen string, attrs map[string]string) {
	t.Helper()
	span := exampleSpan(scope)
	span.Row["start_time"], span.Row["attrs_string"] = seen, attrs
	batch, report, err := observedcatalog.Extract(span, observedcatalog.DefaultLimits())
	if err != nil || !report.Complete {
		t.Fatalf("seed extraction: %v %+v", err, report)
	}
	if err := sink.Insert(context.Background(), batch); err != nil {
		t.Fatal(err)
	}
}

// catalogListing renders a project's logical keys and values, one line each,
// without reading attribute values back out of the tables.
func catalogListing(t *testing.T, origin, database string, scope observedcatalog.Scope) string {
	t.Helper()
	keys := localSQL(t, origin, `SELECT attribute_key, attribute_type, toString(min(first_seen)) FROM `+database+`.observed_attribute_keys
 WHERE organization_id = '`+scope.OrganizationID+`' AND workspace_id = '`+scope.WorkspaceID+`' AND project_id = '`+scope.ProjectID+`'
 GROUP BY attribute_key, attribute_type ORDER BY attribute_key, attribute_type`)
	values := localSQL(t, origin, `SELECT attribute_key, attribute_type, value_fingerprint, toString(min(first_seen)) FROM `+database+`.observed_attribute_values
 WHERE organization_id = '`+scope.OrganizationID+`' AND workspace_id = '`+scope.WorkspaceID+`' AND project_id = '`+scope.ProjectID+`'
 GROUP BY attribute_key, attribute_type, value_fingerprint ORDER BY attribute_key, attribute_type, value_fingerprint`)
	return keys + "--\n" + values
}

func expectedListing(batch observedcatalog.Batch) string {
	var keys, values []string
	for _, row := range batch.Keys {
		keys = append(keys, fmt.Sprintf("%s\t%s\t%s\n", row.AttributeKey, row.AttributeType, row.FirstSeen))
	}
	for _, row := range batch.Values {
		values = append(values, fmt.Sprintf("%s\t%s\t%s\t%s\n", row.AttributeKey, row.AttributeType, row.ValueFingerprint, row.FirstSeen))
	}
	sort.Strings(keys)
	sort.Strings(values)
	return strings.Join(keys, "") + "--\n" + strings.Join(values, "")
}

func createSourceSpans(t *testing.T, origin, database string) {
	t.Helper()
	localSQL(t, origin, `CREATE TABLE `+database+`.spans (
 project_id UUID, org_id Nullable(UUID), observation_type String, service_name String, trace_id String, id String,
 start_time DateTime64(6, 'UTC'), attrs_string Map(String, String), attrs_number Map(String, Float64),
 attrs_bool Map(String, UInt8), attributes_extra JSON, model String, _version UInt64, is_deleted UInt8,
 resource_attrs JSON(max_dynamic_paths=512))
 ENGINE=MergeTree PARTITION BY toDate(start_time) ORDER BY (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)`)
}

func TestClickHouseReplaceRebuildsOneProjectWithoutSimulatedSpans(t *testing.T) {
	origin, sourceDB := localClickHouse(t)
	_, catalogDB, sink := localCatalog(t)
	a := exampleScope()
	b := a
	b.ProjectID = "00000000-0000-4000-8000-000000000031"
	createSourceSpans(t, origin, sourceDB)
	localSQL(t, origin, `INSERT INTO `+sourceDB+`.spans FORMAT JSONEachRow
{"project_id":"`+a.ProjectID+`","org_id":"`+a.OrganizationID+`","observation_type":"span","service_name":"s","trace_id":"t","id":"marked","start_time":"2026-01-01 12:01:00","attrs_string":{"only_marked":"x","real":"z"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"","_version":1,"is_deleted":0,"resource_attrs":{"fi.simulation.origin":"simulation","service.name":"s"}}
{"project_id":"`+a.ProjectID+`","org_id":"`+a.OrganizationID+`","observation_type":"span","service_name":"s","trace_id":"t","id":"plain","start_time":"2026-01-01 12:02:00","attrs_string":{"real":"z"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"","_version":1,"is_deleted":0,"resource_attrs":{"service.name":"s"}}`)

	// Before the rebuild, project A holds what the marked span put there (and
	// older history), project B holds its own rows.
	seedCatalog(t, sink, a, "2025-12-01 12:00:00.000000", map[string]string{"only_marked": "x", "legacy_only": "y", "real": "z"})
	seedCatalog(t, sink, b, "2025-12-01 12:00:00.000000", map[string]string{"b_only": "x", "real": "z"})
	beforeA := catalogListing(t, origin, catalogDB, a)
	beforeB := catalogListing(t, origin, catalogDB, b)
	if !strings.Contains(beforeA, "only_marked") || !strings.Contains(beforeA, "legacy_only") || beforeB == "--\n" {
		t.Fatalf("fixture did not seed the catalog: %q %q", beforeA, beforeB)
	}

	reader, err := newSourceReader(origin, sourceDB, "test", "test")
	if err != nil {
		t.Fatal(err)
	}
	checkExistence(t, reader, a.ProjectID, b.ProjectID)

	cfg := options{
		project: a.ProjectID, source: reader, apply: true, replace: true, purger: sink,
		catalog:        observedcatalog.ClickHouseConfig{URL: origin, Database: catalogDB},
		checkpointPath: t.TempDir() + "/progress.json",
		since:          time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC), until: time.Date(2026, 1, 1, 14, 0, 0, 0, time.UTC),
		maxPages: 20, pageSize: 10, limits: observedcatalog.DefaultLimits(),
		now: func() time.Time { return time.Date(2026, 1, 1, 12, 30, 0, 0, time.UTC) },
	}
	var output bytes.Buffer
	if err := runSpans(context.Background(), cfg, &testScopeReader{scope: a}, sink, &output); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(output.String(), `"catalog_purged":true`) {
		t.Fatalf("no purge receipt: %s", output.String())
	}

	// A holds exactly what the unmarked span contributes, first seen at that
	// span rather than at the older rows the purge removed.
	plain := observedcatalog.ScopedSpan{OrganizationID: a.OrganizationID, WorkspaceID: a.WorkspaceID, Row: map[string]any{
		"project_id": a.ProjectID, "org_id": a.OrganizationID, "start_time": "2026-01-01 12:02:00.000000",
		"attrs_string": map[string]string{"real": "z"}, "attrs_number": map[string]float64{}, "attrs_bool": map[string]uint8{},
		"attributes_extra": map[string]any{}, "model": "",
	}}
	want, report, err := observedcatalog.Extract(plain, observedcatalog.DefaultLimits())
	if err != nil || !report.Complete || len(want.Keys) == 0 {
		t.Fatalf("expected batch: %v %+v", err, report)
	}
	if got := catalogListing(t, origin, catalogDB, a); got != expectedListing(want) {
		t.Fatalf("project A after the rebuild:\n%s\nwant:\n%s", got, expectedListing(want))
	}
	if after := catalogListing(t, origin, catalogDB, b); after != beforeB {
		t.Fatalf("project B changed:\n%s\nwas:\n%s", after, beforeB)
	}
	// Resuming the finished checkpoint changes nothing and does not delete.
	if err := runSpans(context.Background(), cfg, &testScopeReader{scope: a}, sink, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if got := catalogListing(t, origin, catalogDB, a); got != expectedListing(want) {
		t.Fatal("resume changed project A")
	}
}

// checkExistence runs the three bounded reads against the real server: the
// project's spans are at 12:01 and 12:02, and project empty has none.
func checkExistence(t *testing.T, reader *sourceReader, project, empty string) {
	t.Helper()
	ctx := context.Background()
	at := func(hour, minute, second int) time.Time {
		return time.Date(2026, 1, 1, hour, minute, second, 0, time.UTC)
	}
	for name, c := range map[string]struct {
		got  func(string) (bool, error)
		want bool
	}{
		"span before 12:01:01":      {func(p string) (bool, error) { return reader.spanBefore(ctx, p, at(12, 1, 1)) }, true},
		"span before 12:01:00":      {func(p string) (bool, error) { return reader.spanBefore(ctx, p, at(12, 1, 0)) }, false},
		"span at or after 12:02:00": {func(p string) (bool, error) { return reader.spanNotBefore(ctx, p, at(12, 2, 0)) }, true},
		"span at or after 12:02:01": {func(p string) (bool, error) { return reader.spanNotBefore(ctx, p, at(12, 2, 1)) }, false},
		"span in [12:00, 12:02:00)": {func(p string) (bool, error) { return reader.spanBetween(ctx, p, at(12, 0, 0), at(12, 2, 0)) }, true},
		"span in [12:03, 14:00)":    {func(p string) (bool, error) { return reader.spanBetween(ctx, p, at(12, 3, 0), at(14, 0, 0)) }, false},
	} {
		if got, err := c.got(project); err != nil || got != c.want {
			t.Fatalf("%s: got %v, %v; want %v", name, got, err, c.want)
		}
		if got, err := c.got(empty); err != nil || got {
			t.Fatalf("%s: project without spans reported one: %v, %v", name, got, err)
		}
	}
}

func TestClickHouseReplaceRefusesAnUnsafeRangeBeforeDeleting(t *testing.T) {
	origin, sourceDB := localClickHouse(t)
	_, catalogDB, sink := localCatalog(t)
	a := exampleScope()
	b := a
	b.ProjectID = "00000000-0000-4000-8000-000000000031"
	createSourceSpans(t, origin, sourceDB)
	localSQL(t, origin, `INSERT INTO `+sourceDB+`.spans FORMAT JSONEachRow
{"project_id":"`+a.ProjectID+`","org_id":"`+a.OrganizationID+`","observation_type":"span","service_name":"s","trace_id":"t","id":"first","start_time":"2026-01-01 12:01:00","attrs_string":{"real":"z"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"","_version":1,"is_deleted":0,"resource_attrs":{"service.name":"s"}}
{"project_id":"`+a.ProjectID+`","org_id":"`+a.OrganizationID+`","observation_type":"span","service_name":"s","trace_id":"t","id":"second","start_time":"2026-01-01 12:02:00","attrs_string":{"real":"z"},"attrs_number":{},"attrs_bool":{},"attributes_extra":{},"model":"","_version":1,"is_deleted":0,"resource_attrs":{"service.name":"s"}}`)
	seedCatalog(t, sink, a, "2025-12-01 12:00:00.000000", map[string]string{"legacy_only": "y", "real": "z"})
	seedCatalog(t, sink, b, "2025-12-01 12:00:00.000000", map[string]string{"b_only": "x"})
	beforeA := catalogListing(t, origin, catalogDB, a)
	beforeB := catalogListing(t, origin, catalogDB, b)
	reader, err := newSourceReader(origin, sourceDB, "test", "test")
	if err != nil {
		t.Fatal(err)
	}
	at := func(hour, minute, second int) time.Time {
		return time.Date(2026, 1, 1, hour, minute, second, 0, time.UTC)
	}
	run := func(scope observedcatalog.Scope, mutate func(*options)) (string, error) {
		cfg := options{
			project: scope.ProjectID, source: reader, apply: true, replace: true, purger: sink,
			catalog:        observedcatalog.ClickHouseConfig{URL: origin, Database: catalogDB},
			checkpointPath: t.TempDir() + "/progress.json",
			since:          at(12, 0, 0), until: at(14, 0, 0), maxPages: 20, pageSize: 10, limits: observedcatalog.DefaultLimits(),
			now: func() time.Time { return at(12, 30, 0) },
		}
		mutate(&cfg)
		var output bytes.Buffer
		err := runSpans(context.Background(), cfg, &testScopeReader{scope: scope}, sink, &output)
		if err != nil {
			if _, statErr := os.Stat(cfg.checkpointPath); !errors.Is(statErr, os.ErrNotExist) {
				t.Fatal("checkpoint written by a refused run")
			}
		}
		return output.String(), err
	}
	for name, c := range map[string]struct {
		scope   observedcatalog.Scope
		mutate  func(*options)
		wantErr string
	}{
		"a span before since":               {a, func(c *options) { c.since = at(12, 1, 30) }, "oldest stored span"},
		"a span at until":                   {a, func(c *options) { c.until, c.now = at(12, 2, 0), func() time.Time { return at(10, 0, 0) } }, "newest stored span"},
		"a span after until":                {a, func(c *options) { c.until, c.now = at(12, 1, 30), func() time.Time { return at(10, 0, 0) } }, "newest stored span"},
		"an empty project without the flag": {b, func(*options) {}, "--allow-empty-rebuild"},
	} {
		if _, err := run(c.scope, c.mutate); err == nil || !strings.Contains(err.Error(), c.wantErr) {
			t.Fatalf("%s: not refused (%q): %v", name, c.wantErr, err)
		}
		if got := catalogListing(t, origin, catalogDB, a); got != beforeA {
			t.Fatalf("%s: project A changed by a refused run:\n%s", name, got)
		}
		if got := catalogListing(t, origin, catalogDB, b); got != beforeB {
			t.Fatalf("%s: project B changed by a refused run:\n%s", name, got)
		}
	}
	// With the flag the empty project is purged and nothing is rebuilt; A is untouched.
	output, err := run(b, func(c *options) { c.allowEmpty = true })
	if err != nil || !strings.Contains(output, `"project_has_spans":false`) {
		t.Fatalf("empty rebuild allowed: %v %s", err, output)
	}
	if got := catalogListing(t, origin, catalogDB, b); got != "--\n" {
		t.Fatalf("project B not emptied:\n%s", got)
	}
	if got := catalogListing(t, origin, catalogDB, a); got != beforeA {
		t.Fatalf("project A changed by B's rebuild:\n%s", got)
	}
}

func TestPostgresHoldingReadsTheProviderMarkWithoutFilters(t *testing.T) {
	dsn, fixture := localPostgres(t)
	ctx := context.Background()
	if _, err := fixture.Exec(ctx, `CREATE TABLE tracer_observability_provider(id uuid PRIMARY KEY, project_id uuid, workspace_id uuid, deleted boolean DEFAULT false, metadata jsonb)`); err != nil {
		t.Fatal(err)
	}
	reader, err := newPostgresScope(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.conn.Close(ctx)
	insert := func(project string, workspace string, deleted bool, metadata any) {
		t.Helper()
		if _, err := fixture.Exec(ctx, `INSERT INTO tracer_observability_provider(id, project_id, workspace_id, deleted, metadata) VALUES ($1, $2, $3, $4, $5::jsonb)`,
			uuid.NewString(), project, workspace, deleted, metadata); err != nil {
			t.Fatal(err)
		}
	}
	scope := exampleScope()
	otherWorkspace := "00000000-0000-4000-8000-000000000099"
	for name, c := range map[string]struct {
		metadata  any
		workspace string
		deleted   bool
		want      bool
	}{
		"true":                        {metadata: `{"holds_simulated": true}`, workspace: scope.WorkspaceID, want: true},
		"true with other keys":        {metadata: `{"other": 1, "holds_simulated": true}`, workspace: scope.WorkspaceID, want: true},
		"false":                       {metadata: `{"holds_simulated": false}`, workspace: scope.WorkspaceID},
		"string true":                 {metadata: `{"holds_simulated": "true"}`, workspace: scope.WorkspaceID},
		"number one":                  {metadata: `{"holds_simulated": 1}`, workspace: scope.WorkspaceID},
		"sql null":                    {metadata: nil, workspace: scope.WorkspaceID},
		"json null":                   {metadata: `null`, workspace: scope.WorkspaceID},
		"array":                       {metadata: `[{"holds_simulated": true}]`, workspace: scope.WorkspaceID},
		"nested":                      {metadata: `{"inner": {"holds_simulated": true}}`, workspace: scope.WorkspaceID},
		"soft-deleted provider":       {metadata: `{"holds_simulated": true}`, workspace: scope.WorkspaceID, deleted: true, want: true},
		"provider in other workspace": {metadata: `{"holds_simulated": true}`, workspace: otherWorkspace, want: true},
	} {
		t.Run(name, func(t *testing.T) {
			project := uuid.NewString()
			insert(project, c.workspace, c.deleted, c.metadata)
			got, err := reader.Holding(ctx, project)
			if err != nil || got != c.want {
				t.Fatalf("Holding = %v, %v; want %v", got, err, c.want)
			}
		})
	}
	t.Run("no provider row", func(t *testing.T) {
		if got, err := reader.Holding(ctx, uuid.NewString()); err != nil || got {
			t.Fatalf("project without a provider held: %v %v", got, err)
		}
	})
	t.Run("another project's mark does not count", func(t *testing.T) {
		insert(uuid.NewString(), scope.WorkspaceID, false, `{"holds_simulated": true}`)
		if got, err := reader.Holding(ctx, uuid.NewString()); err != nil || got {
			t.Fatalf("mark leaked across projects: %v %v", got, err)
		}
	})
	t.Run("one holding row among others", func(t *testing.T) {
		project := uuid.NewString()
		insert(project, scope.WorkspaceID, false, `{"holds_simulated": false}`)
		insert(project, scope.WorkspaceID, false, nil)
		insert(project, scope.WorkspaceID, true, `{"holds_simulated": true}`)
		if got, err := reader.Holding(ctx, project); err != nil || !got {
			t.Fatalf("holding row not found among others: %v %v", got, err)
		}
	})
	t.Run("read failure is an error", func(t *testing.T) {
		if _, err := fixture.Exec(ctx, `ALTER TABLE tracer_observability_provider RENAME TO tracer_observability_provider_moved`); err != nil {
			t.Fatal(err)
		}
		defer fixture.Exec(ctx, `ALTER TABLE tracer_observability_provider_moved RENAME TO tracer_observability_provider`)
		if got, err := reader.Holding(ctx, uuid.NewString()); err == nil || got {
			t.Fatalf("a failed read was answered: %v %v", got, err)
		}
	})
}
