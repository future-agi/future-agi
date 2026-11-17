package auth

import (
	"context"
	"log/slog"
	"testing"
	"time"

	"github.com/google/uuid"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

// observeProject reads the id and source of the live Observe project name.
func (f routingFixture) observeProject(org, ws, name string) (id, source string) {
	f.t.Helper()
	err := f.pool.QueryRow(context.Background(), `
		SELECT id, source FROM tracer_project
		WHERE organization_id = $1 AND workspace_id = $2 AND name = $3
		  AND trace_type = 'observe' AND NOT deleted`, org, ws, name).Scan(&id, &source)
	if err != nil {
		f.t.Fatalf("project %s: %v", name, err)
	}
	return id, source
}

func (f routingFixture) prototypeProject(org, ws, name string) string {
	id := uuid.NewString()
	f.exec(`INSERT INTO tracer_project (id, name, organization_id, workspace_id, model_type, trace_type, source, tags, created_at, updated_at, deleted)
		VALUES ($1, $2, $3, $4, 'GenerativeLLM', 'observe', 'prototype', '[]', now(), now(), false)`, id, name, org, ws)
	return id
}

// OBS-SW-69, end to end through PostgreSQL: a project the collector
// auto-creates for a batch whose first root span is a conversation is stored
// with source "simulator", so the Observe UI shows it as a voice project. An
// LLM-rooted project stays "prototype", and a project that already exists
// keeps its source.
func TestStampAutoCreatesConversationRootProjectAsSimulatorInPostgres(t *testing.T) {
	pool := routingDatabase(t)
	f := routingFixture{t, pool}
	org := uuid.NewString()
	ws := f.workspace(org, true, true, false)
	existingID := f.prototypeProject(org, ws, "voice-existing")

	a := &Authenticator{cache: newCache(5*time.Minute, time.Hour), projects: routingResolver(pool, columnsPresent), log: slog.Default()}
	result := &ResolveResult{OrgID: org, WorkspaceID: ws, Projects: map[string]string{}}
	a.cache.putPositive("pg-voice-key", result)

	td := ptrace.NewTraces()
	for _, p := range []struct{ name, kind string }{
		{"voice-new", "CONVERSATION"},
		{"text-new", "LLM"},
		{"voice-existing", "CONVERSATION"},
	} {
		rs := td.ResourceSpans().AppendEmpty()
		rs.Resource().Attributes().PutStr("project_name", p.name)
		span := rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
		span.SetName("root")
		span.Attributes().PutStr("fi.span.kind", p.kind)
	}
	if dropped, err := StampResourceAttrs(context.Background(), a, "pg-voice-key", td, result); err != nil || dropped != 0 {
		t.Fatalf("stamp: dropped=%d err=%v", dropped, err)
	}

	for _, tc := range []struct{ name, want string }{
		{"voice-new", "simulator"},
		{"text-new", "prototype"},
		{"voice-existing", "prototype"},
	} {
		id, source := f.observeProject(org, ws, tc.name)
		if source != tc.want {
			t.Errorf("%s source = %q, want %q", tc.name, source, tc.want)
		}
		if got, _ := result.GetProjectInWorkspace(ws, tc.name); got != id {
			t.Errorf("%s stamped with %q, want project %s", tc.name, got, id)
		}
	}
	if id, _ := f.observeProject(org, ws, "voice-existing"); id != existingID {
		t.Errorf("voice-existing id = %s, want the existing %s", id, existingID)
	}
}

// GetOrCreateProject inserts a marked name as "simulator" and any other name
// as "prototype"; on conflict it never rewrites a stored source, either way.
func TestGetOrCreateProjectSourceFollowsConversationRoots(t *testing.T) {
	pool := routingDatabase(t)
	f := routingFixture{t, pool}
	r := routingResolver(pool, columnsPresent)
	org := uuid.NewString()
	ws := f.workspace(org, true, true, false)
	existingID := f.prototypeProject(org, ws, "voice-existing")
	marked := withConversationRoots(context.Background(), map[string]struct{}{"voice": {}, "voice-existing": {}})

	create := func(ctx context.Context, name string) string {
		t.Helper()
		id, err := r.GetOrCreateProject(ctx, org, ws, name, ProjectTypeObserve)
		if err != nil {
			t.Fatalf("create %s: %v", name, err)
		}
		return id
	}
	voiceID := create(marked, "voice")
	textID := create(marked, "text")
	if got := create(marked, "voice-existing"); got != existingID {
		t.Errorf("voice-existing returned %s, want the existing %s", got, existingID)
	}
	if got := create(context.Background(), "voice"); got != voiceID {
		t.Errorf("unmarked voice returned %s, want %s", got, voiceID)
	}

	for _, tc := range []struct{ name, id, want string }{
		{"voice", voiceID, "simulator"},
		{"text", textID, "prototype"},
		{"voice-existing", existingID, "prototype"},
	} {
		id, source := f.observeProject(org, ws, tc.name)
		if id != tc.id || source != tc.want {
			t.Errorf("%s = (%s, %q), want (%s, %q)", tc.name, id, source, tc.id, tc.want)
		}
	}
}
