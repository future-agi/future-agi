package server

import (
	"context"
	"sync/atomic"
	"testing"
	"time"
)

// releaseTestServer shuts s down when the test ends, then runs each closer:
// what the test built for s that Shutdown does not close, such as the
// request-logging plugin (cmd/agentcc closes it itself after Shutdown). Until
// then every gateway a test builds stays reachable for the rest of the
// package's run: its async worker, batch processor loop and key-scope sender
// keep it, and each logging plugin keeps two workers and a 4,096-record
// buffer.
//
// Shutdown runs once, so a test that shuts s down itself is unaffected, and
// each closer must be safe to run again (the logging plugin's Close is). The
// cleanup holds only a pointer that is cleared once it has run, so a finished
// subtest, which the testing package keeps until its parent ends, holds
// nothing of s.
func releaseTestServer(t testing.TB, s *Server, closers ...func()) {
	t.Helper()
	release := new(atomic.Pointer[func()])
	stop := func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = s.Shutdown(ctx)
		for _, c := range closers {
			c()
		}
		forgetOrgRoutingOf(s.handlers)
	}
	release.Store(&stop)
	t.Cleanup(func() {
		if f := release.Swap(nil); f != nil {
			(*f)()
		}
	})
}

// forgetOrgRoutingOf drops h's entries from the package's per-org routing and
// failover caches. Their keys carry the Handlers, and an entry stays until
// 1,024 newer ones evict it: a gateway runs one Handlers, but the tests build
// thousands, and each entry keeps its gateway's catalogue and model database.
func forgetOrgRoutingOf(h *Handlers) {
	if h == nil {
		return
	}
	orgRoutings.mu.Lock()
	for key, el := range orgRoutings.entries {
		if key.h == h {
			orgRoutings.order.Remove(el)
			delete(orgRoutings.entries, key)
		}
	}
	orgRoutings.mu.Unlock()
	orgFailovers.mu.Lock()
	for key, el := range orgFailovers.entries {
		if key.h == h {
			orgFailovers.order.Remove(el)
			delete(orgFailovers.entries, key)
		}
	}
	orgFailovers.mu.Unlock()
}
