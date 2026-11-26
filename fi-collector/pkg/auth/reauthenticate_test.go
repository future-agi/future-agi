package auth

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"testing"
	"time"
)

type refreshResolver struct {
	result func(context.Context) (*ResolveResult, error)
}

func (r *refreshResolver) ValidateKey(ctx context.Context, _, _ string) (*ResolveResult, error) {
	return r.result(ctx)
}
func (*refreshResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return map[string]string{"known": "project"}, nil
}
func (*refreshResolver) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	return "project", nil
}
func refreshResult() *ResolveResult {
	return &ResolveResult{OrgID: "org", WorkspaceID: "workspace", Projects: map[string]string{"known": "project"}}
}
func newRefreshAuth(t *testing.T, r *refreshResolver) *Authenticator {
	t.Helper()
	a, err := NewWithResolver(Config{CacheTTL: time.Minute, WarmTTL: time.Hour}, r, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// The stale age and publication barrier are explicit: no sleeps or scheduler
// race decide whether Authenticate takes its actual warm-refresh branch.
func TestReauthenticateAfterActualWarmRefresh(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	entered, release := make(chan struct{}), make(chan struct{})
	fresh := refreshResult()
	r := &refreshResolver{result: func(context.Context) (*ResolveResult, error) {
		close(entered)
		<-release
		return fresh, nil
	}}
	a := newRefreshAuth(t, r)
	ck := CacheKey("k", "s")
	old := refreshResult()
	a.cache.m.Store(ck, &cacheEntry{result: old, fetchedAt: time.Now().Add(-2 * time.Minute)})
	warm, err := a.Authenticate(ctx, "k", "s")
	if err != nil || warm != old {
		t.Fatalf("warm authentication: %v", err)
	}
	select {
	case <-entered:
	case <-ctx.Done():
		t.Fatal("refresh never reached resolver")
	}
	close(release)
	// Join the already-started refresh. Returning from Do means publication
	// finished (not just that ValidateKey returned).
	a.sfKey.Do(ck+":refresh", func() (any, error) { return nil, nil })
	entry, _ := a.cache.peek(ck)
	if entry == nil || entry.result != fresh {
		t.Fatal("refresh did not publish new generation")
	}
	var changed *ProjectAuthChangedError
	if err := a.ResolveProjectsForKey(ctx, ck, warm, []string{"known"}); !errors.As(err, &changed) {
		t.Fatalf("exact generation check weakened: %v", err)
	}
	ctx = WithResolvedContext(withRequestCredentials(ctx, "k", "s"), warm, ck)
	next, err := a.Reauthenticate(ctx)
	if err != nil || FromContext(next) != fresh {
		t.Fatalf("reauthentication: %v", err)
	}
	traces := namedTraces("known")
	if dropped, err := StampResourceAttrs(next, a, ck, traces, FromContext(next)); err != nil || dropped != 0 {
		t.Fatalf("fresh generation stamp: dropped=%d err=%v", dropped, err)
	}
	if got := getStrAttr(traces.ResourceSpans().At(0).Resource().Attributes(), "fi.project_id"); got != "project" {
		t.Fatalf("project=%q", got)
	}
}

func TestReauthenticateExpiredRevokedAndInvalidScope(t *testing.T) {
	for _, state := range []string{"expired", "revoked", "invalid-org", "invalid-workspace"} {
		t.Run(state, func(t *testing.T) {
			fresh := refreshResult()
			switch state {
			case "revoked":
				fresh = nil
			case "invalid-org":
				fresh.OrgID = ""
			case "invalid-workspace":
				fresh.WorkspaceID = ""
			}
			a := newRefreshAuth(t, &refreshResolver{result: func(context.Context) (*ResolveResult, error) { return fresh, nil }})
			ck := CacheKey("k", "s")
			old := refreshResult()
			a.cache.m.Store(ck, &cacheEntry{result: old, fetchedAt: time.Now().Add(-2 * time.Hour)})
			ctx := WithResolvedContext(withRequestCredentials(context.Background(), "k", "s"), old, ck)
			next, err := a.Reauthenticate(ctx)
			if state != "expired" {
				if !errors.Is(err, ErrUnauthenticated) || next != nil {
					t.Fatalf("invalid auth accepted: %v", err)
				}
				return
			}
			if err != nil || FromContext(next) != fresh {
				t.Fatalf("expired refresh: %v", err)
			}
			if err := a.ResolveProjectsForKey(next, ck, fresh, []string{"known"}); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestReauthenticateCannotUseResolvedContextAsCredentials(t *testing.T) {
	a := newRefreshAuth(t, &refreshResolver{result: func(context.Context) (*ResolveResult, error) { t.Fatal("lookup without credentials"); return nil, nil }})
	ctx := WithResolvedContext(context.Background(), refreshResult(), CacheKey("k", "s"))
	if next, err := a.Reauthenticate(ctx); next != nil || err == nil {
		t.Fatal("resolved context invented credentials")
	}
}

func TestAuthenticateWaitRespectsRequestCancellation(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	defer close(release)
	a := newRefreshAuth(t, &refreshResolver{result: func(context.Context) (*ResolveResult, error) {
		close(entered)
		<-release
		return refreshResult(), nil
	}})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := a.Authenticate(ctx, "k", "s"); done <- err }()
	<-entered
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("canceled lookup: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("request waited on shared lookup after cancellation")
	}
	if e, _ := a.cache.peek(CacheKey("k", "s")); e != nil {
		t.Fatal("canceled waiter populated cache")
	}
}
