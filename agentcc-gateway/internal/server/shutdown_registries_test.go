package server

import (
	"context"
	"sync"
	"testing"
	"time"
)

// The package keeps per-server state in registries keyed by the server or
// its handlers. Shutdown removes the server's entries, so a server that has
// shut down is not kept reachable through them.
func TestShutdownRemovesTheServerFromPackageRegistries(t *testing.T) {
	g := newManagedTestGateway(t, managedOptIn())
	h := g.srv.handlers
	byServer := map[string]*sync.Map{"outcomeStores": &outcomeStores, "spendGuards": &spendGuards}
	byHandlers := map[string]*sync.Map{"capabilityCores": &capabilityCores, "runBudgets": &runBudgets, "globalRoutings": &globalRoutings}
	for _, m := range byServer {
		m.Store(g.srv, struct{}{})
	}
	for _, m := range byHandlers {
		m.Store(h, struct{}{})
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := g.srv.Shutdown(ctx); err != nil {
		t.Fatalf("shutdown: %v", err)
	}
	for name, m := range byServer {
		if _, ok := m.Load(g.srv); ok {
			t.Errorf("%s still holds the server after Shutdown", name)
		}
	}
	for name, m := range byHandlers {
		if _, ok := m.Load(h); ok {
			t.Errorf("%s still holds the server's handlers after Shutdown", name)
		}
	}
}
