package auth

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
)

func TestProjectCacheChangeBeforeResolution(t *testing.T) {
	for _, cached := range []bool{false, true} {
		for _, change := range []string{"expire", "evict", "replace"} {
			t.Run(change+map[bool]string{false: "/new", true: "/cached"}[cached], func(t *testing.T) {
				r := &recoveringProjectResolver{}
				a, result := projectTestAuth(r)
				if cached {
					result.SetProjects(map[string]string{"new": "project-id"})
				}
				switch change {
				case "expire":
					a.cache.m.Store("key", &cacheEntry{result: result, fetchedAt: time.Now().Add(-2 * time.Hour)})
				case "evict":
					a.InvalidateKey("key")
				case "replace":
					a.cache.putPositive("key", &ResolveResult{OrgID: result.OrgID, WorkspaceID: result.WorkspaceID})
				}
				td := namedTraces("new")
				_, err := StampResourceAttrs(context.Background(), a, "key", td, result)
				var changed *ProjectAuthChangedError
				if !errors.Is(err, ErrProjectResolutionUnavailable) || !errors.As(err, &changed) {
					t.Errorf("stale authentication must produce typed retryable error: %v", err)
				}
				if r.lookups != 0 || r.creates != 0 || getStrAttr(td.ResourceSpans().At(0).Resource().Attributes(), "fi.project_id") != "" {
					t.Error("stale request reached persistence or stamped project")
				}
			})
		}
	}
}

type interruptedFailedCreate struct {
	interruptedProjectResolver
}

func (r *interruptedFailedCreate) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	r.create()
	return "", &pgconn.PgError{Code: "23514", Message: "project check"}
}

func TestProjectCacheChangeDuringFailedCreate(t *testing.T) {
	r := &interruptedFailedCreate{}
	a, result := projectTestAuth(r)
	r.create = func() { a.InvalidateKey("key") }
	td := namedTraces("new")
	_, err := StampResourceAttrs(context.Background(), a, "key", td, result)
	var changed *ProjectAuthChangedError
	if !errors.Is(err, ErrProjectResolutionUnavailable) || !errors.As(err, &changed) {
		t.Errorf("cache change must win over permanent project failure: %v", err)
	}
	if len(result.failed) != 0 || td.SpanCount() != 1 {
		t.Error("stale request negatively cached or dropped project")
	}
}
