package auth

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"

	"github.com/google/uuid"
)

// Same nullability and live-version uniqueness as ProjectVersion/BaseModel;
// defaults are supplied by the writer, not by this fixture.
const projectVersionSchema = `
CREATE TABLE tracer_project_version (
 id uuid PRIMARY KEY,
 project_id uuid NOT NULL REFERENCES tracer_project(id),
 name varchar(255) NOT NULL,
 version varchar(10) NOT NULL,
 start_time timestamptz NULL,
 end_time timestamptz NULL,
 error jsonb NULL,
 metadata jsonb NULL,
 eval_tags jsonb NULL,
 avg_eval_score double precision NULL,
 config jsonb NULL,
 annotations_id uuid NULL,
 created_at timestamptz NOT NULL,
 updated_at timestamptz NOT NULL,
 deleted boolean NOT NULL,
 deleted_at timestamptz NULL
);
CREATE UNIQUE INDEX unique_version_per_project ON tracer_project_version(project_id, version) WHERE NOT deleted;
`

func versionDatabase(t *testing.T) (routingFixture, *PGResolver, string, string, string) {
	t.Helper()
	pool := routingDatabase(t)
	f := routingFixture{t: t, pool: pool}
	f.exec(projectVersionSchema)
	org := uuid.NewString()
	ws := f.workspace(org, true, true, false)
	project := versionProject(f, org, ws, "experiment")
	return f, routingResolver(pool, columnsPresent), org, ws, project
}

func versionProject(f routingFixture, org, workspace, traceType string) string {
	id := uuid.NewString()
	f.exec(`INSERT INTO tracer_project (id, name, organization_id, workspace_id, model_type, trace_type, source, tags, created_at, updated_at, deleted)
 VALUES ($1, $5, $2, $3, 'GenerativeLLM', $4, 'prototype', '[]', now(), now(), false)`, id, org, workspace, traceType, "project-"+id)
	return id
}

func TestPGProjectVersionCreatesAndReusesSDKID(t *testing.T) {
	f, r, org, ws, project := versionDatabase(t)
	ctx := context.Background()
	id := uuid.NewString()
	got, err := r.GetOrCreateProjectVersion(ctx, org, ws, project, strings.ToUpper(id), "candidate")
	if err != nil || got != id {
		t.Fatalf("create = %q, %v", got, err)
	}
	got, err = r.GetOrCreateProjectVersion(ctx, org, ws, project, id, "do not rename")
	if err != nil || got != id || f.count(`SELECT count(*) FROM tracer_project_version`) != 1 {
		t.Fatalf("reuse = %q, %v", got, err)
	}
	var name, version string
	var config, evalTags, metadata []byte
	var started bool
	if err := f.pool.QueryRow(ctx, `SELECT name, version, config, eval_tags, metadata, start_time IS NOT NULL FROM tracer_project_version WHERE id=$1`, id).Scan(&name, &version, &config, &evalTags, &metadata, &started); err != nil {
		t.Fatal(err)
	}
	if name != "candidate" || version != "v1" || string(evalTags) != "[]" || string(metadata) != "{}" || !started {
		t.Fatalf("created defaults: name=%s version=%s eval_tags=%s metadata=%s started=%v", name, version, evalTags, metadata, started)
	}
	var fields []map[string]any
	if err := json.Unmarshal(config, &fields); err != nil {
		t.Fatal(err)
	}
	wantIDs := []string{"trace_name", "input", "output", "start_time", "status", "latency", "total_tokens", "cost", "model", "tags", "user_id", "trace_id", "prompt_tokens", "completion_tokens", "provider", "session_id"}
	if len(fields) != len(wantIDs) {
		t.Fatalf("default trace config has %d fields, want %d", len(fields), len(wantIDs))
	}
	for i, field := range fields {
		canonical := wantIDs[i]
		if canonical == "user_id" {
			canonical = "user"
		}
		if canonical == "session_id" {
			canonical = "session"
		}
		if field["id"] != wantIDs[i] || field["is_visible"] != (i < 11) || field["property_id"] != "system_attribute:traces:"+canonical || field["property_source"] != "traces" || field["property_kind"] != "system_attribute" {
			t.Errorf("trace config field %d = %v", i, field)
		}
	}
	second := uuid.NewString()
	if got, err := r.GetOrCreateProjectVersion(ctx, org, ws, project, second, ""); err != nil || got != second {
		t.Fatalf("default name create = %q, %v", got, err)
	}
	if err := f.pool.QueryRow(ctx, `SELECT name, version FROM tracer_project_version WHERE id=$1`, second).Scan(&name, &version); err != nil {
		t.Fatal(err)
	}
	if name != "Version v2" || version != "v2" {
		t.Fatalf("next version name=%q version=%q", name, version)
	}
}

func TestPGProjectVersionNameOnlyReusesLatestDuplicate(t *testing.T) {
	f, r, org, ws, project := versionDatabase(t)
	ctx := context.Background()
	first, err := r.GetOrCreateProjectVersion(ctx, org, ws, project, "", "repeat")
	if err != nil || first == "" {
		t.Fatalf("name create = %q, %v", first, err)
	}
	// Duplicate names are legal; explicit IDs remain independent identities.
	second := uuid.NewString()
	if got, err := r.GetOrCreateProjectVersion(ctx, org, ws, project, second, "repeat"); err != nil || got != second {
		t.Fatalf("duplicate-name create = %q, %v", got, err)
	}
	f.exec(`UPDATE tracer_project_version SET created_at='2026-01-01' WHERE id=$1`, first)
	f.exec(`UPDATE tracer_project_version SET created_at='2026-01-02' WHERE id=$1`, second)
	if got, err := r.GetOrCreateProjectVersion(ctx, org, ws, project, "", "repeat"); err != nil || got != second {
		t.Fatalf("latest name reuse = %q, %v", got, err)
	}
	if got, err := r.GetOrCreateProjectVersion(ctx, org, ws, project, first, "repeat"); err != nil || got != first {
		t.Fatalf("explicit older ID = %q, %v", got, err)
	}
	f.exec(`UPDATE tracer_project_version SET deleted=true WHERE id=$1`, second)
	if got, err := r.GetOrCreateProjectVersion(ctx, org, ws, project, "", "repeat"); err != nil || got != first {
		t.Fatalf("deleted name excluded = %q, %v", got, err)
	}
	if f.count(`SELECT count(*) FROM tracer_project_version`) != 2 {
		t.Fatal("name reuse created another version")
	}
}

func TestPGProjectVersionScopeObserveAndInvalidInput(t *testing.T) {
	f, r, org, ws, project := versionDatabase(t)
	ctx := context.Background()
	id := uuid.NewString()
	if _, err := r.GetOrCreateProjectVersion(ctx, org, ws, project, id, "private"); err != nil {
		t.Fatal(err)
	}
	otherProject := versionProject(f, org, ws, "experiment")
	otherWS := f.workspace(org, false, true, false)
	otherOrg := uuid.NewString()
	foreignWS := f.workspace(otherOrg, true, true, false)
	foreignProject := versionProject(f, otherOrg, foreignWS, "experiment")
	for _, tc := range []struct{ name, org, ws, project, id string }{
		{"another project", org, ws, otherProject, id},
		{"another organization", otherOrg, foreignWS, foreignProject, id},
		{"wrong workspace for project", org, otherWS, project, id},
		{"wrong organization for project", otherOrg, ws, project, id},
		{"missing project", org, ws, uuid.NewString(), id},
		{"invalid id", org, ws, project, "invalid"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := r.GetOrCreateProjectVersion(ctx, tc.org, tc.ws, tc.project, tc.id, "private")
			if got != "" || !errors.Is(err, ErrProjectVersionIgnored) {
				t.Fatalf("ignored = %q, %v", got, err)
			}
		})
	}
	observe := versionProject(f, org, ws, "observe")
	for _, versionID := range []string{id, uuid.NewString(), ""} {
		if got, err := r.GetOrCreateProjectVersion(ctx, org, ws, observe, versionID, "private"); got != "" || err != nil {
			t.Fatalf("observe = %q, %v", got, err)
		}
	}
	f.exec(`UPDATE tracer_project_version SET deleted=true WHERE id=$1`, id)
	if got, err := r.GetOrCreateProjectVersion(ctx, org, ws, project, id, "private"); got != "" || !errors.Is(err, ErrProjectVersionIgnored) {
		t.Fatalf("deleted version = %q, %v", got, err)
	}
	f.exec(`UPDATE tracer_project SET deleted=true WHERE id=$1`, project)
	if got, err := r.GetOrCreateProjectVersion(ctx, org, ws, project, uuid.NewString(), "new"); got != "" || !errors.Is(err, ErrProjectVersionIgnored) {
		t.Fatalf("deleted project = %q, %v", got, err)
	}
	if f.count(`SELECT count(*) FROM tracer_project_version`) != 1 {
		t.Fatal("ignored input created a version")
	}
}

func TestPGProjectVersionConcurrentCreation(t *testing.T) {
	for _, mode := range []string{"same ID", "same name", "distinct IDs"} {
		t.Run(mode, func(t *testing.T) {
			f, r, org, ws, project := versionDatabase(t)
			const n = 8
			commonID := uuid.NewString()
			ids, errs := make([]string, n), make([]error, n)
			var wg sync.WaitGroup
			for i := 0; i < n; i++ {
				wg.Add(1)
				go func(i int) {
					defer wg.Done()
					id := commonID
					if mode == "same name" {
						id = ""
					}
					if mode == "distinct IDs" {
						id = uuid.NewString()
					}
					ids[i], errs[i] = r.GetOrCreateProjectVersion(context.Background(), org, ws, project, id, "parallel")
				}(i)
			}
			wg.Wait()
			for i, err := range errs {
				if err != nil || ids[i] == "" {
					t.Fatalf("call %d = %q, %v", i, ids[i], err)
				}
				if mode != "distinct IDs" && ids[i] != ids[0] {
					t.Fatal("concurrent calls resolved different versions")
				}
			}
			want := 1
			if mode == "distinct IDs" {
				want = n
			}
			if got := f.count(`SELECT count(*) FROM tracer_project_version`); got != want {
				t.Fatalf("rows=%d want=%d", got, want)
			}
			for i := 1; i <= want; i++ {
				if f.count(`SELECT count(*) FROM tracer_project_version WHERE version=$1`, fmt.Sprintf("v%d", i)) != 1 {
					t.Fatalf("missing version v%d", i)
				}
			}
		})
	}
}

func TestPGProjectVersionConcurrentCrossProjectID(t *testing.T) {
	f, r, org, ws, project := versionDatabase(t)
	projects := []string{project, versionProject(f, org, ws, "experiment")}
	id := uuid.NewString()
	ids, errs := make([]string, 2), make([]error, 2)
	var wg sync.WaitGroup
	for i := range projects {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			ids[i], errs[i] = r.GetOrCreateProjectVersion(context.Background(), org, ws, projects[i], id, "same-id")
		}(i)
	}
	wg.Wait()
	winners, ignored := 0, 0
	for i := range ids {
		if ids[i] == id && errs[i] == nil {
			winners++
		} else if ids[i] == "" && errors.Is(errs[i], ErrProjectVersionIgnored) {
			ignored++
		} else {
			t.Fatalf("result = %q, %v", ids[i], errs[i])
		}
	}
	if winners != 1 || ignored != 1 || f.count(`SELECT count(*) FROM tracer_project_version`) != 1 {
		t.Fatalf("winners=%d ignored=%d", winners, ignored)
	}
}
