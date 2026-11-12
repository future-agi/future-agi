package auth

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type publicationFunc func(context.Context) (*ResolveResult, error)

func (f publicationFunc) ValidateKey(ctx context.Context, _, _ string) (*ResolveResult, error) {
	return f(ctx)
}
func (publicationFunc) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return nil, nil
}
func (publicationFunc) GetOrCreateProject(context.Context, string, string, string, string) (string, error) {
	return "", nil
}
func publicationAuth(t *testing.T, f publicationFunc) *Authenticator {
	t.Helper()
	a, e := NewWithResolver(Config{CacheTTL: time.Minute, WarmTTL: time.Hour}, f, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if e != nil {
		t.Fatal(e)
	}
	return a
}

func TestNewGenerationDoesNotJoinStaleColdLookup(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	entered, release := make(chan struct{}), make(chan struct{})
	var closeRelease sync.Once
	defer closeRelease.Do(func() { close(release) })
	old, fresh := refreshResult(), refreshResult()
	fresh.WorkspaceID = "new-workspace"
	var calls atomic.Int32
	a := publicationAuth(t, func(ctx context.Context) (*ResolveResult, error) {
		if calls.Add(1) == 1 {
			close(entered)
			select {
			case <-release:
			case <-ctx.Done():
				return nil, ctx.Err()
			}
			return old, nil
		}
		return fresh, nil
	})
	oldDone := make(chan *ResolveResult, 1)
	go func() { r, _ := a.Authenticate(ctx, "k", "s"); oldDone <- r }()
	select {
	case <-entered:
	case <-ctx.Done():
		t.Fatal("first lookup did not start")
	}
	a.InvalidateKey(CacheKey("k", "s"))
	// New lookup must finish while the invalidated lookup is still held.
	got, e := a.Authenticate(ctx, "k", "s")
	if e != nil || got != fresh {
		t.Fatalf("new generation joined stale lookup: fresh=%v err=%v", got == fresh, e)
	}
	closeRelease.Do(func() { close(release) })
	select {
	case got = <-oldDone:
		if got == old {
			t.Fatal("old caller returned invalidated result")
		}
	case <-ctx.Done():
		t.Fatal("old caller did not settle")
	}
	if e, _ := a.cache.peek(CacheKey("k", "s")); e == nil || e.result != fresh {
		t.Fatal("stale waiter overwrote current cache")
	}
}

func TestWarmPublicationCannotChangeNewGeneration(t *testing.T) {
	for _, invalid := range []bool{false, true} {
		t.Run(map[bool]string{false: "positive", true: "negative"}[invalid], func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			entered, release := make(chan struct{}), make(chan struct{})
			var closeRelease sync.Once
			defer closeRelease.Do(func() { close(release) })
			old, fresh := refreshResult(), refreshResult()
			fresh.WorkspaceID = "new-workspace"
			var calls atomic.Int32
			a := publicationAuth(t, func(ctx context.Context) (*ResolveResult, error) {
				if calls.Add(1) == 1 {
					close(entered)
					select {
					case <-release:
					case <-ctx.Done():
						return nil, ctx.Err()
					}
					if invalid {
						return nil, nil
					}
					return old, nil
				}
				return fresh, nil
			})
			ck := CacheKey("k", "s")
			a.cache.m.Store(ck, &cacheEntry{result: old, fetchedAt: time.Now().Add(-2 * time.Minute)})
			if _, e := a.Authenticate(ctx, "k", "s"); e != nil {
				t.Fatal(e)
			}
			select {
			case <-entered:
			case <-ctx.Done():
				t.Fatal("warm lookup did not start")
			}
			a.InvalidateKey(ck)
			if got, e := a.Authenticate(ctx, "k", "s"); e != nil || got != fresh {
				t.Fatalf("fresh lookup failed: %v", e)
			}
			closeRelease.Do(func() { close(release) })
			a.sfKey.Do(ck+":refresh", func() (any, error) { return nil, nil })
			if e, _ := a.cache.peek(ck); e == nil || e.result != fresh {
				t.Fatal("old refresh changed the new generation")
			}
		})
	}
}

func TestKeyPublicationRetryIsBounded(t *testing.T) {
	var calls atomic.Int32
	var a *Authenticator
	a = publicationAuth(t, func(context.Context) (*ResolveResult, error) {
		calls.Add(1)
		a.InvalidateKey(CacheKey("k", "s"))
		return refreshResult(), nil
	})
	result, err := a.Authenticate(context.Background(), "k", "s")
	var changed *ProjectAuthChangedError
	if result != nil || !errors.As(err, &changed) || calls.Load() != 2 {
		t.Fatalf("unbounded/stale auth: result=%v calls=%d err=%v", result != nil, calls.Load(), err)
	}
}

func TestKeyPublicationFenceRetainsNoKeyTombstones(t *testing.T) {
	a := publicationAuth(t, func(context.Context) (*ResolveResult, error) { return nil, nil })
	for i := 0; i < 100000; i++ {
		a.InvalidateKey(CacheKey(string(rune(i)), "unknown"))
	}
	n := 0
	a.cache.m.Range(func(_, _ any) bool { n++; return true })
	if n != 0 {
		t.Fatalf("invalidation retained %d unknown keys", n)
	}
	if len(a.cache.generations) != 64 {
		t.Fatal("generation storage is not fixed size")
	}
}
