package auth

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type generationProjectResolver struct {
	entered, release chan struct{}
	creates          atomic.Int64
}

func (r *generationProjectResolver) ResolveProjects(context.Context, string, string, []string) (map[string]string, error) {
	return nil, nil
}
func (r *generationProjectResolver) GetOrCreateProject(ctx context.Context, _, _, _, _ string) (string, error) {
	if r.creates.Add(1) == 1 {
		close(r.entered)
		select {
		case <-r.release:
			return "old-id", nil
		case <-ctx.Done():
			return "", ctx.Err()
		}
	}
	return "new-id", nil
}

func TestProjectCacheReplacementDoesNotShareStaleCreate(t *testing.T) {
	r := &generationProjectResolver{entered: make(chan struct{}), release: make(chan struct{})}
	a, old := projectTestAuth(r)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	var release sync.Once
	defer release.Do(func() { close(r.release) })
	oldDone := make(chan error, 1)
	go func() { oldDone <- a.ResolveProjectsForKey(ctx, "key", old, []string{"new"}) }()
	select {
	case <-r.entered:
	case <-ctx.Done():
		t.Fatal("old create did not start")
	}
	fresh := &ResolveResult{OrgID: old.OrgID, WorkspaceID: old.WorkspaceID}
	a.cache.putPositive("key", fresh)
	newDone := make(chan error, 1)
	go func() { newDone <- a.ResolveProjectsForKey(ctx, "key", fresh, []string{"new"}) }()
	completed := false
	select {
	case err := <-newDone:
		completed = true
		if err != nil {
			t.Errorf("fresh request: %v", err)
		}
	case <-time.After(time.Second):
		t.Error("fresh authentication joined the stale in-flight create")
	}
	release.Do(func() { close(r.release) })
	select {
	case err := <-oldDone:
		if !errors.Is(err, ErrProjectResolutionUnavailable) {
			t.Errorf("old request must retry: %v", err)
		}
	case <-ctx.Done():
		t.Fatal("old request did not settle")
	}
	if !completed {
		select {
		case <-newDone:
		case <-ctx.Done():
			t.Fatal("new request did not settle")
		}
	}
	if id, ok := fresh.GetProject("new"); !ok || id != "new-id" || r.creates.Load() != 2 {
		t.Errorf("replacement inherited stale create: id=%q ok=%t creates=%d", id, ok, r.creates.Load())
	}
}
