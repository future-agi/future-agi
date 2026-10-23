package server

import (
	"context"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/connectivity"
	"google.golang.org/grpc/credentials/insecure"
)

// At the connection cap, shutting down must not wait for clients to hang up.
// grpc-go's GracefulStop waits for Serve to return before it drains the open
// connections, so a capped listener's Accept has to give up as soon as the
// listener is closed, even while every slot is taken.
func TestShutdownAtTheConnectionCap(t *testing.T) {
	const capConns = 2
	addr := testTCPAddr(t)
	s := New(Config{GRPCAddr: addr, MaxConnections: capConns}, nil, nil, nil, nil)
	s.cfg.HTTPAddr = ""
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.Run(ctx) }()
	if !waitPort(addr, 2*time.Second) {
		cancel()
		t.Fatalf("server didn't listen on %s", addr)
	}

	// capConns connections take every slot; one more makes Serve wait in
	// Accept.
	for i := 0; i < capConns+1; i++ {
		conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { conn.Close() })
		conn.Connect()
		if i < capConns {
			wctx, wcancel := context.WithTimeout(context.Background(), 5*time.Second)
			for st := conn.GetState(); st != connectivity.Ready; st = conn.GetState() {
				if !conn.WaitForStateChange(wctx, st) {
					wcancel()
					t.Fatalf("connection %d never became ready", i)
				}
			}
			wcancel()
		}
	}
	time.Sleep(100 * time.Millisecond)

	cancel()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("Run did not return within 10 s of shutdown with the connection cap reached")
	}
}
