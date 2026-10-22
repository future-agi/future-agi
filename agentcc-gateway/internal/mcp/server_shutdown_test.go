package mcp

import (
	"context"
	"testing"
	"time"
)

// The gateway's shutdown closes the MCP server's
// upstream clients, in parallel and within its deadline, so stdio children
// that ignore stdin EOF still get the shutdown ladder (SIGTERM after the
// grace) instead of being orphaned, and three of them take one ladder, not
// three.
func TestServerShutdownClosesStdioChildrenInParallel(t *testing.T) {
	const grace = time.Second
	s := newTestServer(t)
	var pids []int
	for _, id := range []string{"a", "b", "c"} {
		command, args := stdioHelperCommand("--ignore-eof")
		c := NewClient(ClientConfig{ServerID: id, Command: command, Args: args, TransportType: "stdio", StdioGrace: grace})
		if err := c.Connect(context.Background()); err != nil {
			t.Fatalf("connect %s: %v", id, err)
		}
		tr, ok := c.transport.(*StdioTransport)
		if !ok {
			t.Fatalf("client %s transport is %T", id, c.transport)
		}
		pids = append(pids, tr.proc.Pid())
		s.clientsMu.Lock()
		s.clients[id] = c
		s.clientsMu.Unlock()
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	start := time.Now()
	if err := s.Shutdown(ctx); err != nil {
		t.Fatalf("Shutdown: %v", err)
	}
	took := time.Since(start)
	if took < grace {
		t.Errorf("Shutdown took %s: the children ignore stdin EOF, so each must wait out the %s grace", took, grace)
	}
	// One after another, three children take at least 3 graces.
	if took >= 5*grace/2 {
		t.Errorf("Shutdown took %s for three children: they were closed one after another", took)
	}
	for _, pid := range pids {
		if !processGone(pid, 3*time.Second) {
			t.Errorf("stdio child %d still running after Shutdown", pid)
		}
	}

	expired, cancelExpired := context.WithCancel(context.Background())
	cancelExpired()
	if err := newTestServer(t).Shutdown(expired); err != nil && err != context.Canceled {
		t.Errorf("Shutdown with nothing to close: %v", err)
	}
}
