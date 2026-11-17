package server

import (
	"context"
	"testing"
	"time"
)

// Shutdown stops the batch processor's retention loop, which otherwise keeps
// the whole server reachable after it has shut down, and a second Shutdown,
// or a caller that still stops the processor itself, changes nothing.
func TestShutdownStopsTheBatchProcessorAndCanRunTwice(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := g.srv.Shutdown(ctx); err != nil {
		t.Fatalf("shutdown: %v", err)
	}
	select {
	case <-g.srv.batchProcessor.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("the batch processor's retention loop outlived Shutdown")
	}
	if err := g.srv.Shutdown(ctx); err != nil {
		t.Fatalf("second shutdown: %v", err)
	}
	g.srv.batchProcessor.Stop()
}
