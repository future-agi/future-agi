package auth

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgconn"
	"go.opentelemetry.io/collector/pdata/ptrace"
)

type recoveringProjectResolver struct {
	err              error
	lookups, creates int
}

func (r *recoveringProjectResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	r.lookups++
	return nil, nil
}
func (r *recoveringProjectResolver) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	r.creates++
	return "project-id", r.err
}
func projectTestAuth(r projectResolver) (*Authenticator, *ResolveResult) {
	a := &Authenticator{cache: newCache(time.Minute, time.Hour), projects: r, log: slog.New(slog.NewTextHandler(io.Discard, nil))}
	result := &ResolveResult{OrgID: "org", WorkspaceID: "ws", Projects: map[string]string{}}
	a.cache.putPositive("key", result)
	return a, result
}
func namedTraces(names ...string) ptrace.Traces {
	td := ptrace.NewTraces()
	for _, name := range names {
		rs := td.ResourceSpans().AppendEmpty()
		rs.Resource().Attributes().PutStr("project_name", name)
		rs.ScopeSpans().AppendEmpty().Spans().AppendEmpty()
	}
	return td
}
func TestResourceProjectValidationBeforeLookup(t *testing.T) {
	for _, name := range []string{strings.Repeat("x", 60<<10), strings.Repeat("x", 256), strings.Repeat("界", 256), "bad\x00name", "bad\tname", "bad\u0085name", "​", "⠀", " ", string([]byte{0xff})} {
		r := &recoveringProjectResolver{}
		a, result := projectTestAuth(r)
		if _, err := StampResourceAttrs(context.Background(), a, "key", namedTraces("valid", name), result); err == nil {
			t.Errorf("invalid name of %d bytes accepted", len(name))
		}
		if r.lookups != 0 || r.creates != 0 || len(result.failed) != 0 || len(result.Projects) != 0 {
			t.Errorf("invalid name reached resolver/cache: lookups=%d creates=%d", r.lookups, r.creates)
		}
		if err := a.ResolveProjectsForKey(context.Background(), "key", result, []string{name}); err == nil {
			t.Errorf("direct resolution accepted invalid name of %d bytes", len(name))
		}
	}
	// Commas are ambiguous in HTTP headers, not resource attributes. Preserve
	// exact resource names, including whitespace and all 255 Unicode characters.
	for _, name := range []string{"research, development", " project ", strings.Repeat("界", 255), "café", "Café"} {
		r := &recoveringProjectResolver{}
		a, result := projectTestAuth(r)
		if dropped, err := StampResourceAttrs(context.Background(), a, "key", namedTraces(name), result); err != nil || dropped != 0 {
			t.Errorf("valid resource name rejected: dropped=%d err=%v", dropped, err)
		}
		if _, ok := result.GetProject(name); !ok {
			t.Error("resource name was normalized or lost")
		}
	}
}
func TestTransientProjectFailureRecovers(t *testing.T) {
	for _, failure := range []error{context.DeadlineExceeded, context.Canceled, errors.New("connection reset"), &pgconn.PgError{Code: "23505"}, &pgconn.PgError{Code: "23503"}} {
		r := &recoveringProjectResolver{err: failure}
		a, result := projectTestAuth(r)
		td := namedTraces("valid-new-project")
		if _, err := StampResourceAttrs(context.Background(), a, "key", td, result); !errors.Is(err, failure) {
			t.Errorf("first attempt err=%v; want original failure", err)
		}
		if td.SpanCount() != 1 || len(result.failed) != 0 {
			t.Errorf("transient failure dropped data or negatively cached name")
		}
		r.err = nil
		if dropped, err := StampResourceAttrs(context.Background(), a, "key", td, result); err != nil || dropped != 0 || td.SpanCount() != 1 {
			t.Errorf("recovery dropped=%d spans=%d err=%v", dropped, td.SpanCount(), err)
		}
		if r.creates != 2 {
			t.Errorf("create attempts=%d, want 2", r.creates)
		}
	}
}
func TestFailedProjectCacheRetainedMemory(t *testing.T) {
	runtime.GC()
	var before, after runtime.MemStats
	runtime.ReadMemStats(&before)
	r := &ResolveResult{}
	for i := 0; i < maxFailedProjects; i++ {
		// 255 characters, close to varchar(255)'s maximum UTF-8 byte size.
		r.markFailed(fmt.Sprintf("%07d", i)+strings.Repeat("😀", 248), time.Now())
	}
	runtime.GC()
	runtime.ReadMemStats(&after)
	held := int64(after.HeapAlloc) - int64(before.HeapAlloc)
	t.Logf("failed-cache entries=%d retained_heap_bytes=%d", len(r.failed), held)
	if held > 2<<20 {
		t.Errorf("failed cache retained %d bytes, want <2 MiB independent of name bytes", held)
	}
	for i := 0; i < 2*maxFailedProjects; i++ {
		r.markFailed(fmt.Sprintf("name-%d", i), time.Now())
	}
	if len(r.failed) > maxFailedProjects {
		t.Fatalf("unbounded entries: %d", len(r.failed))
	}
	runtime.KeepAlive(r)
}
