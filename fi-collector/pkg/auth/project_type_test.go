package auth

import (
	"context"
	"log/slog"
	"os"
	"testing"
	"time"

	chexp "github.com/future-agi/future-agi/fi-collector/exporter/clickhouse25exporter"
	"github.com/future-agi/future-agi/fi-collector/pkg/detid"
	"github.com/google/uuid"
	"go.opentelemetry.io/collector/pdata/pcommon"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// typedRecordingResolver knows some projects with their trace_type and
// auto-creates the rest, like PGResolver.
type typedRecordingResolver struct {
	known   map[string]ResolvedProject
	created []string
}

func (r *typedRecordingResolver) ResolveProjects(
	ctx context.Context, orgID, workspaceID string, names []string,
) (map[string]string, error) {
	typed, _ := r.ResolveProjectsWithTypes(ctx, orgID, workspaceID, names)
	out := make(map[string]string, len(typed))
	for n, p := range typed {
		out[n] = p.ID
	}
	return out, nil
}

func (r *typedRecordingResolver) ResolveProjectsWithTypes(
	_ context.Context, _, _ string, names []string,
) (map[string]ResolvedProject, error) {
	out := make(map[string]ResolvedProject)
	for _, n := range names {
		if p, ok := r.known[n]; ok {
			out[n] = p
		}
	}
	return out, nil
}

func (r *typedRecordingResolver) GetOrCreateProject(
	_ context.Context, _, _, name, traceType string,
) (string, error) {
	r.created = append(r.created, name+"/"+traceType)
	return "created-" + name, nil
}

var _ typedProjectResolver = (*typedRecordingResolver)(nil)
var _ typedProjectResolver = (*PGResolver)(nil)

// stampOne stamps one ResourceSpans named project with the given resource
// edit, returning its attributes after the stamp.
func stampOne(t *testing.T, a *Authenticator, ck string, result *ResolveResult, project string, edit func(pcommon.Map)) pcommon.Map {
	t.Helper()
	traces := ptrace.NewTraces()
	rs := traces.ResourceSpans().AppendEmpty()
	rs.Resource().Attributes().PutStr("project_name", project)
	if edit != nil {
		edit(rs.Resource().Attributes())
	}
	rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty().SetName("s")
	dropped, err := StampResourceAttrs(context.Background(), a, ck, traces, result)
	if err != nil || dropped != 0 {
		t.Fatalf("stamp %s: dropped=%d err=%v", project, dropped, err)
	}
	return traces.ResourceSpans().At(0).Resource().Attributes()
}

func projectTypeAttr(attrs pcommon.Map) (string, bool) {
	v, ok := attrs.Get(resourceProjectTypeKey)
	if !ok {
		return "", false
	}
	return v.AsString(), true
}

func newTypedAuth(resolver projectResolver) (*Authenticator, *ResolveResult, string) {
	a := &Authenticator{cache: newCache(5*time.Minute, time.Hour), projects: resolver, log: slog.Default()}
	result := &ResolveResult{OrgID: "org-t", WorkspaceID: "ws-t", Projects: map[string]string{}}
	ck := "typed-key"
	a.cache.putPositive(ck, result)
	return a, result, ck
}

// G4 (TH-8787): a resource that sends no project_type, stamped with an
// Observe project, gets project_type "observe" so the converter links
// user.id. Other types, unknown types and a sender's own project_type are
// left alone.
func TestStampAddsObserveProjectTypeOnlyForObserveProjects(t *testing.T) {
	resolver := &typedRecordingResolver{known: map[string]ResolvedProject{
		"obs":  {ID: "id-obs", TraceType: "observe"},
		"exp":  {ID: "id-exp", TraceType: "experiment"},
		"anon": {ID: "id-anon"}, // type unread
	}}
	a, result, ck := newTypedAuth(resolver)

	for _, tc := range []struct {
		name     string
		project  string
		edit     func(pcommon.Map)
		want     string
		wantSent bool
	}{
		{"observe project, no project_type", "obs", nil, "observe", true},
		{"observe project, empty project_type", "obs", func(m pcommon.Map) { m.PutStr("project_type", "") }, "observe", true},
		{"observe project, sender says experiment", "obs", func(m pcommon.Map) { m.PutStr("project_type", "experiment") }, "experiment", true},
		{"observe project, non-string project_type kept", "obs", func(m pcommon.Map) { m.PutInt("project_type", 7) }, "7", true},
		{"experiment project", "exp", nil, "", false},
		{"unknown type", "anon", nil, "", false},
		{"auto-created project", "new-from-header", nil, "observe", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			attrs := stampOne(t, a, ck, result, tc.project, tc.edit)
			got, ok := projectTypeAttr(attrs)
			if ok != tc.wantSent || got != tc.want {
				t.Errorf("project_type = %q (present %v), want %q (present %v)", got, ok, tc.want, tc.wantSent)
			}
		})
	}
	if len(resolver.created) != 1 || resolver.created[0] != "new-from-header/observe" {
		t.Errorf("auto-create calls = %v, want one observe project", resolver.created)
	}
}

// A resolver that cannot read trace_type leaves looked-up projects as
// before; a project it auto-creates is still an Observe one.
func TestStampWithUntypedResolver(t *testing.T) {
	resolver := &recordingProjectResolver{known: map[string]string{"known": "id-known"}}
	a, result, ck := newTypedAuth(resolver)

	if got, ok := projectTypeAttr(stampOne(t, a, ck, result, "known", nil)); ok {
		t.Errorf("looked-up project with unknown type got project_type %q, want none", got)
	}
	if got, _ := projectTypeAttr(stampOne(t, a, ck, result, "fresh", nil)); got != "observe" {
		t.Errorf("auto-created project_type = %q, want observe", got)
	}
}

// The types reach the cached entry too, so a later request that finds the
// name cached (and so does no lookup) still stamps project_type; deleting
// the project forgets its type.
func TestProjectTypesMirroredIntoCacheAndEvicted(t *testing.T) {
	resolver := &typedRecordingResolver{known: map[string]ResolvedProject{
		"obs": {ID: "id-obs", TraceType: "observe"},
	}}
	a := &Authenticator{cache: newCache(5*time.Minute, time.Hour), projects: resolver, log: slog.Default()}
	cached := &ResolveResult{OrgID: "org-t", WorkspaceID: "ws-t", Projects: map[string]string{}}
	ck := "mirror-key"
	a.cache.putPositive(ck, cached)
	request := &ResolveResult{OrgID: "org-t", WorkspaceID: "ws-t", Projects: map[string]string{}}

	stampOne(t, a, ck, request, "obs", nil)
	if got := cached.ProjectTypeInWorkspace("ws-t", "id-obs"); got != "observe" {
		t.Fatalf("cached type = %q, want observe", got)
	}
	if got := cached.ProjectTypeInWorkspace("ws-other", "id-obs"); got != "" {
		t.Errorf("type read through another workspace = %q, want empty", got)
	}

	// The cached entry now answers without a lookup and still stamps.
	resolver.known = nil
	if got, _ := projectTypeAttr(stampOne(t, a, ck, cached, "obs", nil)); got != "observe" {
		t.Errorf("cached project_type = %q, want observe", got)
	}

	if n := a.cache.evictProjectID("id-obs"); n != 1 {
		t.Fatalf("evicted entries = %d, want 1", n)
	}
	if got := cached.ProjectTypeInWorkspace("ws-t", "id-obs"); got != "" {
		t.Errorf("type after eviction = %q, want empty", got)
	}
}

// End to end on R2's real OpenRouter body: the X-Project-Name header names an
// Observe project, the stamp adds project_type "observe", and the converter
// then links user.id on the spans that carry it.
func TestOpenRouterHeaderProjectLinksUser(t *testing.T) {
	body, err := os.ReadFile("../../exporter/clickhouse25exporter/testdata/openrouter/OR5-real-free.json")
	if err != nil {
		t.Fatal(err)
	}
	projectID, orgID := uuid.NewString(), uuid.NewString()

	for _, tc := range []struct {
		name      string
		traceType string
		wantUser  bool
	}{
		{"observe project", "observe", true},
		{"experiment project", "experiment", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var u ptrace.JSONUnmarshaler
			traces, err := u.UnmarshalTraces(body)
			if err != nil {
				t.Fatal(err)
			}
			// OpenRouter can only name the project by header.
			traces.ResourceSpans().At(0).Resource().Attributes().Remove("project_name")
			if n, err := ApplyProjectNameHeader(traces, ProjectNameValues{Canonical: []string{"or-broadcast"}}); err != nil || n != 1 {
				t.Fatalf("header applied = %d, err = %v", n, err)
			}

			resolver := &typedRecordingResolver{known: map[string]ResolvedProject{
				"or-broadcast": {ID: projectID, TraceType: tc.traceType},
			}}
			a := &Authenticator{cache: newCache(5*time.Minute, time.Hour), projects: resolver, log: slog.Default()}
			result := &ResolveResult{OrgID: orgID, WorkspaceID: "ws-or", Projects: map[string]string{}}
			a.cache.putPositive("or-key", result)
			if _, err := StampResourceAttrs(context.Background(), a, "or-key", traces, result); err != nil {
				t.Fatal(err)
			}

			rows, ids, err := chexp.ConvertWithIdentities(context.Background(), traces, nil)
			if err != nil {
				t.Fatal(err)
			}
			wantUser := detid.EndUserID(projectID, orgID, "user_r2_alice", "").String()
			linked := 0
			for _, r := range rows {
				if r["end_user_id"] == nil {
					continue
				}
				linked++
				if r["end_user_id"] != wantUser {
					t.Errorf("span %s end_user_id = %v, want %s", r["id"], r["end_user_id"], wantUser)
				}
			}
			if tc.wantUser {
				if linked != 2 { // root + generation carry user.id; the attempt does not
					t.Errorf("spans linked to the user = %d, want 2", linked)
				}
				if eus := ids.EndUsers(); len(eus) != 1 || eus[0].UserID != "user_r2_alice" {
					t.Errorf("end users = %+v, want one user_r2_alice", eus)
				}
			} else if linked != 0 || len(ids.EndUsers()) != 0 {
				t.Errorf("experiment project linked %d spans / %d end users, want none", linked, len(ids.EndUsers()))
			}
		})
	}
}

// PGResolver reads each project's trace_type in the lookup statement.
func TestResolveProjectsWithTypesReadsTraceType(t *testing.T) {
	pool := routingDatabase(t)
	f := routingFixture{t: t, pool: pool}
	org := uuid.NewString()
	ws := f.workspace(org, true, true, false)
	obsID, expID := uuid.NewString(), uuid.NewString()
	for _, p := range []struct{ id, name, traceType string }{
		{obsID, "obs", "observe"},
		{expID, "exp", "experiment"},
	} {
		f.exec(`INSERT INTO tracer_project (id, name, organization_id, workspace_id, model_type, trace_type, source, tags, created_at, updated_at, deleted)
			VALUES ($1, $2, $3, $4, 'GenerativeLLM', $5, 'prototype', '[]', now(), now(), false)`, p.id, p.name, org, ws, p.traceType)
	}
	r := routingResolver(pool, columnsPresent)
	got, err := r.ResolveProjectsWithTypes(context.Background(), org, ws, []string{"obs", "exp", "missing"})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got["obs"] != (ResolvedProject{obsID, "observe"}) || got["exp"] != (ResolvedProject{expID, "experiment"}) {
		t.Errorf("ResolveProjectsWithTypes = %+v", got)
	}
	ids, err := r.ResolveProjects(context.Background(), org, ws, []string{"obs", "exp"})
	if err != nil || len(ids) != 2 || ids["obs"] != obsID || ids["exp"] != expID {
		t.Errorf("ResolveProjects = %v, %v", ids, err)
	}
}
