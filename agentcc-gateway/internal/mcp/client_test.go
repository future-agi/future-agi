package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"strings"
	"testing"
	"time"
)

func TestClientClosesStdioAfterFailedHandshake(t *testing.T) {
	for _, mode := range []string{"reject", "timeout"} {
		t.Run(mode, func(t *testing.T) {
			c := NewClient(ClientConfig{
				TransportType: "stdio",
				Command:       os.Args[0],
				Args:          []string{"-test.run=^TestFailedHandshakeHelper$", "--", mode},
			})
			t.Cleanup(func() { c.Close() })
			timeout := 5 * time.Second
			if mode == "timeout" {
				timeout = 250 * time.Millisecond
			}
			ctx, cancel := context.WithTimeout(context.Background(), timeout)
			defer cancel()
			err := c.Connect(ctx)
			if mode == "timeout" && !errors.Is(err, context.DeadlineExceeded) {
				t.Fatalf("expected handshake deadline, got %v", err)
			}
			if mode == "reject" && (err == nil || !strings.Contains(err.Error(), "fixture rejected initialize")) {
				t.Fatalf("expected initialize rejection, got %v", err)
			}
			// The caller abandons this unregistered client. Connect itself must
			// have waited for the child before returning the handshake error.
			transport := c.transport.(*StdioTransport)
			if transport.cmd.ProcessState == nil || transport.Healthy() || c.Healthy() {
				t.Fatal("failed handshake returned without closing and reaping the stdio child")
			}
		})
	}
}

func TestFailedHandshakeHelper(t *testing.T) {
	if len(os.Args) < 2 || os.Args[len(os.Args)-2] != "--" {
		return
	}
	var request Message
	if err := json.NewDecoder(os.Stdin).Decode(&request); err != nil || request.Method != MethodInitialize {
		os.Exit(2)
	}
	if os.Args[len(os.Args)-1] == "reject" {
		response := Message{JSONRPC: "2.0", ID: request.ID, Error: &RPCError{Code: -32603, Message: "fixture rejected initialize"}}
		if err := json.NewEncoder(os.Stdout).Encode(response); err != nil {
			os.Exit(2)
		}
	}
	// Both a server that rejected initialize and one that never responded
	// can remain alive, waiting for further requests.
	_, _ = io.Copy(io.Discard, os.Stdin)
	os.Exit(0)
}
