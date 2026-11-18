package auth

import (
	"context"
	"runtime"
	"testing"
	"time"
)

func TestResolvedNameAndTypeBecomeVisibleTogether(t *testing.T) {
	a, result, key := newTypedAuth(&typedRecordingResolver{known: map[string]ResolvedProject{
		"chat": {ID: "project-id", TraceType: ProjectTypeObserve},
	}})
	// Hold a read lock so the resolver queues to publish its lookup. A second
	// export waiting to read the cache must then get the complete result.
	result.mu.RLock()
	published := make(chan error, 1)
	go func() { published <- a.ResolveProjectsForKey(context.Background(), key, result, []string{"chat"}) }()
	deadline := time.Now().Add(5 * time.Second)
	for result.mu.TryRLock() {
		result.mu.RUnlock()
		if time.Now().After(deadline) {
			result.mu.RUnlock()
			t.Fatal("resolver did not reach publication")
		}
		runtime.Gosched()
	}
	// Readers already waiting behind the first write run before a subsequent
	// write can acquire the lock. Snapshot both fields as that reader.
	type snapshot struct{ id, kind string }
	observed := make(chan snapshot, 1)
	started := make(chan struct{})
	go func() {
		close(started)
		result.mu.RLock()
		defer result.mu.RUnlock()
		id := result.Projects["chat"]
		observed <- snapshot{id, result.projectTypes[id]}
	}()
	<-started
	result.mu.RUnlock()
	if err := <-published; err != nil {
		t.Fatal(err)
	}
	if got := <-observed; got != (snapshot{"project-id", ProjectTypeObserve}) {
		t.Fatalf("first visible project = %+v; want project-id with type observe", got)
	}
}
