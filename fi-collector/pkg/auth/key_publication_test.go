package auth

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"sync/atomic"
	"testing"
	"time"
)

type publicationResolver struct {
	calls   atomic.Int32
	changed atomic.Bool
	entered chan struct{}
	release chan struct{}
	revoked bool
	old     *ResolveResult
	fresh   *ResolveResult
}

func (r *publicationResolver) ValidateKey(ctx context.Context, _, _ string) (*ResolveResult, error) {
	n := r.calls.Add(1)
	result := r.old
	if r.changed.Load() {
		result = r.fresh
		if r.revoked {
			result = nil
		}
	}
	if n == 1 {
		close(r.entered)
		select {
		case <-r.release:
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	return result, nil
}
func (*publicationResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return nil, errors.New("unexpected project lookup")
}
func (*publicationResolver) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	return "", errors.New("unexpected project create")
}

// A real validation has captured its old database result before invalidation.
// Channels, not sleeps, order invalidation before that result can be published.
func TestKeyPublicationAfterInvalidation(t *testing.T) {
	for _, path := range []string{"cold", "warm"} {
		for _, change := range []string{"revoke", "scope-remap"} {
			t.Run(path+"/"+change, func(t *testing.T) {
				ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				old := &ResolveResult{OrgID: "old-org", WorkspaceID: "old-ws", Projects: map[string]string{"p": "old-project"}}
				fresh := &ResolveResult{OrgID: "new-org", WorkspaceID: "new-ws", Projects: map[string]string{"p": "new-project"}}
				r := &publicationResolver{entered: make(chan struct{}), release: make(chan struct{}), revoked: change == "revoke", old: old, fresh: fresh}
				a, err := NewWithResolver(Config{CacheTTL: time.Minute, WarmTTL: time.Hour}, r, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
				if err != nil {
					t.Fatal(err)
				}
				ck := CacheKey("publication-key", "publication-secret")
				done := make(chan struct{})
				if path == "warm" {
					a.cache.m.Store(ck, &cacheEntry{result: old, fetchedAt: time.Now().Add(-2 * time.Minute)})
					got, err := a.Authenticate(ctx, "publication-key", "publication-secret")
					if got != old || err != nil {
						t.Fatalf("warm precondition: %v", err)
					}
				} else {
					go func() { defer close(done); _, _ = a.Authenticate(ctx, "publication-key", "publication-secret") }()
				}
				select {
				case <-r.entered:
				case <-ctx.Done():
					t.Fatal("validation barrier not reached")
				}
				r.changed.Store(true)
				a.InvalidateKey(ck)
				close(r.release)
				if path == "warm" {
					a.sfKey.Do(ck+":refresh", func() (any, error) { return nil, nil })
				} else {
					select {
					case <-done:
					case <-ctx.Done():
						t.Fatal("cold authentication did not finish")
					}
				}
				if e, _ := a.cache.peek(ck); e != nil && e.result == old {
					t.Error("stale key validation published after invalidation")
				}
				got, err := a.Authenticate(ctx, "publication-key", "publication-secret")
				if change == "revoke" {
					if got != nil || !errors.Is(err, ErrUnauthenticated) {
						t.Fatalf("revoked credentials accepted after barrier: result_present=%v error=%v", got != nil, err)
					}
				} else if err != nil || got != fresh {
					t.Fatalf("scope remap resurrected old scope: fresh=%v error=%v", got == fresh, err)
				}
			})
		}
	}
}
