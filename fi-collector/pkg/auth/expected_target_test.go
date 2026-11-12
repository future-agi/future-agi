package auth

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.opentelemetry.io/collector/pdata/ptrace"
	"google.golang.org/grpc/metadata"
)

func expectedHeaders(org, workspace, project string) http.Header {
	h := http.Header{}
	h.Set(ExpectedOrganizationHeader, org)
	h.Set(ExpectedWorkspaceHeader, workspace)
	h.Set(ExpectedProjectHeader, project)
	return h
}

func TestExpectedTargetHeadersAreOptionalButAtomic(t *testing.T) {
	if target, err := ParseExpectedHTTPHeaders(http.Header{}); target != nil || err != nil {
		t.Fatalf("legacy: %v %v", target, err)
	}
	for _, which := range []string{ExpectedOrganizationHeader, ExpectedWorkspaceHeader, ExpectedProjectHeader} {
		for _, mode := range []string{"missing", "empty", "repeated", "padded"} {
			t.Run(which+mode, func(t *testing.T) {
				h := expectedHeaders("org", "workspace", "project")
				switch mode {
				case "missing":
					h.Del(which)
				case "empty":
					h.Set(which, "")
				case "repeated":
					h.Add(which, h.Get(which))
				case "padded":
					h.Set(which, " "+h.Get(which))
				}
				if _, err := ParseExpectedHTTPHeaders(h); !errors.Is(err, ErrExpectedTargetMismatch) {
					t.Fatalf("expected denial: %v", err)
				}
			})
		}
	}
	md := metadata.New(map[string]string{ExpectedOrganizationHeader: "org", ExpectedWorkspaceHeader: "workspace", ExpectedProjectHeader: "project"})
	target, err := ParseExpectedMetadata(md)
	if err != nil || target.CheckScope(&ResolveResult{OrgID: "org", WorkspaceID: "workspace"}) != nil {
		t.Fatalf("valid gRPC target: %+v %v", target, err)
	}
}

func TestExpectedTargetRefusesStaleCachedKeyScopeBeforeHandler(t *testing.T) {
	const apiKey, secretKey = "repinned-key", "secret"
	a := &Authenticator{cache: newCache(time.Hour, time.Hour), log: slog.Default()}
	stale := &ResolveResult{OrgID: "same-org", WorkspaceID: "old-workspace", Projects: map[string]string{"shared": "old-project"}}
	a.cache.putPositive(CacheKey(apiKey, secretKey), stale)
	reached := 0
	handler := a.HTTPMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { reached++; w.WriteHeader(http.StatusOK) }))
	for _, route := range []string{"/tracer/v1/traces", "/tracer/v1/traces/target?project_name=shared"} {
		req := httptest.NewRequest(http.MethodPost, route, nil)
		req.Header = expectedHeaders("same-org", "new-workspace", "new-project")
		req.Header.Set("X-Api-Key", apiKey)
		req.Header.Set("X-Secret-Key", secretKey)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusForbidden || reached != 0 {
			t.Fatalf("stale auth entered handler: %d %d", rec.Code, reached)
		}
	}
	// Absence of the new contract preserves existing clients, including ordinary cache behavior.
	req := httptest.NewRequest(http.MethodPost, "/v1/traces", nil)
	req.Header.Set("X-Api-Key", apiKey)
	req.Header.Set("X-Secret-Key", secretKey)
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK || reached != 1 {
		t.Fatalf("legacy request changed: %d %d", rec.Code, reached)
	}
}

func TestExpectedProjectLookupNeverCreatesAndRejectsCachedSameNameReplacement(t *testing.T) {
	resolver := &workspaceProjectResolver{byWorkspace: map[string]map[string]string{"workspace": {"existing": "project", "wrong": "other-project"}}}
	a := &Authenticator{cache: newCache(time.Hour, time.Hour), projects: resolver, log: slog.Default()}
	result := &ResolveResult{OrgID: "org", WorkspaceID: "workspace", Projects: map[string]string{"stale": "old-project"}}
	a.cache.putPositive("key", result)
	for _, name := range []string{"stale", "missing", "wrong"} {
		if err := a.ResolveProjectsForExpectedTarget(context.Background(), "key", result, []string{name}, "project"); !errors.Is(err, ErrExpectedTargetMismatch) {
			t.Fatalf("%s allowed: %v", name, err)
		}
	}
	if err := a.ResolveProjectsForExpectedTarget(context.Background(), "key", result, []string{"existing"}, "project"); err != nil {
		t.Fatal(err)
	}
	for _, call := range resolver.calls {
		if strings.HasPrefix(call, "create:") {
			t.Fatalf("expected target auto-created project: %v", resolver.calls)
		}
	}
	traces := ptrace.NewTraces()
	for _, name := range []string{"existing", "wrong"} {
		traces.ResourceSpans().AppendEmpty().Resource().Attributes().PutStr("project_name", name)
	}
	if _, err := StampResourceAttrsExpected(context.Background(), a, "key", traces, result, &ExpectedTarget{"org", "workspace", "project"}); !errors.Is(err, ErrExpectedTargetMismatch) {
		t.Fatalf("mixed request not rejected: %v", err)
	}
	if _, ok := traces.ResourceSpans().At(0).Resource().Attributes().Get("fi.project_id"); ok {
		t.Fatal("mixed request stamped before all targets were validated")
	}
}
