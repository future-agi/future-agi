package main

import (
	"context"
	"testing"
	"time"
)

// watcherStub records which background loops were started and the context
// each one was given.
type watcherStub struct {
	revocations chan context.Context
	columns     chan context.Context
}

func (w *watcherStub) WatchRevocations(ctx context.Context)    { w.revocations <- ctx }
func (w *watcherStub) WatchRoutingColumns(ctx context.Context) { w.columns <- ctx }

func TestStartAuthWatchersRunsBothLoopsUnderTheServerContext(t *testing.T) {
	stub := &watcherStub{revocations: make(chan context.Context, 1), columns: make(chan context.Context, 1)}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	startAuthWatchers(ctx, stub)

	for name, started := range map[string]chan context.Context{"revocations": stub.revocations, "routing columns": stub.columns} {
		select {
		case got := <-started:
			cancel()
			if got.Err() == nil {
				t.Errorf("%s loop was not given the server's context", name)
			}
		case <-time.After(5 * time.Second):
			t.Fatalf("the %s loop was not started", name)
		}
	}
}
