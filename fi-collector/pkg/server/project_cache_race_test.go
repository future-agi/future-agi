package server

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/auth"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type gatedProjectResolver struct {
	phase            string
	entered, release chan struct{}
	once             sync.Once
	persisted        atomic.Bool
	revoked          atomic.Bool
	validations      atomic.Int64
}

func (r *gatedProjectResolver) ValidateKey(context.Context, string, string) (*auth.ResolveResult, error) {
	r.validations.Add(1)
	if r.revoked.Load() {
		return nil, nil
	}
	return &auth.ResolveResult{OrgID: calOrg, WorkspaceID: calWS, Projects: map[string]string{}}, nil
}
func (r *gatedProjectResolver) pause(ctx context.Context) error {
	var err error
	r.once.Do(func() {
		close(r.entered)
		select {
		case <-r.release:
		case <-ctx.Done():
			err = ctx.Err()
		}
	})
	return err
}
func (r *gatedProjectResolver) ResolveProjects(ctx context.Context, _, _ string, names []string) (map[string]string, error) {
	if r.phase == "lookup" {
		r.persisted.Store(true)
		if err := r.pause(ctx); err != nil {
			return nil, err
		}
	}
	if r.persisted.Load() {
		return map[string]string{names[0]: calProject}, nil
	}
	return nil, nil
}
func (r *gatedProjectResolver) GetOrCreateProject(ctx context.Context, _, _, _, _ string) (string, error) {
	r.persisted.Store(true)
	if err := r.pause(ctx); err != nil {
		return "", err
	}
	return calProject, nil
}

// The request has authenticated from a prewarmed key before persistence blocks.
// Invalidate/replace that entry at the exact lookup/create boundary, using the
// same invalidation API as the revocation watcher. No timing-dependent sleeps.
func TestAuthenticatedProjectCacheRaceRecovery(t *testing.T) {
	for _, transport := range []string{"http", "grpc"} {
		for _, phase := range []string{"lookup", "create"} {
			for _, change := range []string{"evict", "replace", "revoke"} {
				t.Run(transport+"/"+phase+"/"+change, func(t *testing.T) {
					r := &gatedProjectResolver{phase: phase, entered: make(chan struct{}), release: make(chan struct{})}
					a, err := auth.NewWithResolver(auth.Config{CacheTTL: time.Minute, WarmTTL: time.Hour}, r, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
					if err != nil {
						t.Fatal(err)
					}
					ctx, cancel := context.WithTimeout(withKeys(context.Background()), 5*time.Second)
					defer cancel()
					if _, err := a.Authenticate(ctx, "k", "s"); err != nil {
						t.Fatal(err)
					}
					usage := &recordingUsage{}
					s, conn, ch := startGRPCServer(t, Config{}, func(s *Server) { attachRealAuth(s, a); s.usage = usage })
					body := authenticatedBody(t, "new-project")
					attempt := func() error {
						if transport == "grpc" {
							return exportRaw(conn, ctx, body)
						}
						rec := authPost(s, body)
						switch rec.Code {
						case 200:
							return nil
						case 503:
							if rec.Header().Get("Retry-After") == "1" {
								return status.Error(codes.Unavailable, "retry")
							}
						case 401:
							return status.Error(codes.Unauthenticated, "invalid key")
						}
						return errors.New(rec.Body.String())
					}
					done := make(chan error, 1)
					var release sync.Once
					defer release.Do(func() { close(r.release) })
					go func() { done <- attempt() }()
					select {
					case <-r.entered:
					case <-ctx.Done():
						t.Fatal("request never reached persistence")
					}
					a.InvalidateKey(auth.CacheKey("k", "s"))
					if change == "replace" {
						if _, err := a.Authenticate(ctx, "k", "s"); err != nil {
							t.Fatal(err)
						}
					} else if change == "revoke" {
						r.revoked.Store(true)
					}
					release.Do(func() { close(r.release) })
					select {
					case err := <-done:
						want := codes.OK
						if change == "revoke" {
							want = codes.Unauthenticated
						}
						if status.Code(err) != want {
							t.Fatalf("internal restart: got %v, want %v", err, want)
						}
					case <-ctx.Done():
						t.Fatal("interrupted request did not finish")
					}
					assertEmpty := func() {
						t.Helper()
						s.pendMu.Lock()
						pending := len(s.pend)
						s.pendMu.Unlock()
						if pending != 0 || usage.calls.Load() != 0 {
							t.Fatal("failed attempt enqueued rows or emitted usage")
						}
						s.drainNow(ctx)
						if total, _ := ch.lines("spans"); total != 0 {
							t.Fatal("failed attempt reached storage")
						}
					}
					if change == "revoke" {
						assertEmpty()
						if err := attempt(); status.Code(err) != codes.Unauthenticated {
							t.Fatalf("revoked key must fail closed on retry: %v", err)
						}
						assertEmpty()
						r.revoked.Store(false)
						if err := attempt(); err != nil {
							t.Fatalf("re-enabled key: %v", err)
						}
					}
					if r.validations.Load() < 2 {
						t.Fatal("recovery reused stale authentication without revalidating key")
					}
					if usage.calls.Load() != 1 || usage.spans.Load() != 1 || usage.wrongOrg.Load() {
						t.Fatal("retry must account for exactly one accepted span")
					}
					s.drainNow(ctx)
					if total, _ := ch.lines("spans"); total == 0 {
						t.Fatal("recovered attempt did not reach storage")
					}
				})
			}
		}
	}
}
