package auth

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace"
)

// creatingResolver knows no project and creates every one it is asked for.
type creatingResolver struct{ created []string }

func (r *creatingResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return map[string]string{}, nil
}

func (r *creatingResolver) GetOrCreateProject(_ context.Context, _, _, name, _ string) (string, error) {
	r.created = append(r.created, name)
	return fmt.Sprintf("00000000-0000-4000-8000-%012d", len(r.created)), nil
}

// A request naming more new projects than one request may add resolves (and
// creates) only the first MaxNewProjectsPerRequest of them and is refused as
// retryable; each retry adds the next ones, until the request is stamped.
func TestStampAddsAtMostMaxNewProjectsPerRequest(t *testing.T) {
	resolver := &creatingResolver{}
	a := &Authenticator{cache: newCache(5*time.Minute, time.Hour), projects: resolver, log: slog.Default()}
	result := &ResolveResult{OrgID: "org", WorkspaceID: "ws", Projects: map[string]string{}}
	a.cache.putPositive("key", result)

	const names = 2*MaxNewProjectsPerRequest + 8
	td := ptrace.NewTraces()
	for i := 0; i < names; i++ {
		rs := td.ResourceSpans().AppendEmpty()
		rs.Resource().Attributes().PutStr("project_name", fmt.Sprintf("project-%02d", i))
		rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	}

	for attempt, created := range []int{MaxNewProjectsPerRequest, 2 * MaxNewProjectsPerRequest} {
		_, err := StampResourceAttrs(context.Background(), a, "key", td, result)
		if !errors.Is(err, ErrTooManyNewProjects) {
			t.Fatalf("attempt %d: %v, want ErrTooManyNewProjects", attempt, err)
		}
		if len(resolver.created) != created {
			t.Fatalf("attempt %d: %d projects created, want %d", attempt, len(resolver.created), created)
		}
		if _, ok := td.ResourceSpans().At(0).Resource().Attributes().Get("fi.project_id"); ok {
			t.Fatalf("attempt %d: a refused request was stamped", attempt)
		}
	}
	if resolver.created[0] != "project-00" || resolver.created[MaxNewProjectsPerRequest] != fmt.Sprintf("project-%02d", MaxNewProjectsPerRequest) {
		t.Errorf("projects created out of name order: %v", resolver.created)
	}
	dropped, err := StampResourceAttrs(context.Background(), a, "key", td, result)
	if err != nil || dropped != 0 {
		t.Fatalf("last attempt: dropped %d, err %v; want every ResourceSpans stamped", dropped, err)
	}
	if len(resolver.created) != names {
		t.Errorf("%d projects created in all, want %d", len(resolver.created), names)
	}
}
