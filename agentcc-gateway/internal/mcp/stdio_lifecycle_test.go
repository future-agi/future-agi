package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestClientClosesStdioAfterDiscoveryDeadline(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "received-tools-list")
	c := NewClient(ClientConfig{TransportType: "stdio", Command: os.Args[0], Args: []string{"-test.run=^TestSlowDiscoveryFixture$", "--", marker}})
	t.Cleanup(func() { c.Close() })
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	err := c.Connect(ctx)
	if _, statErr := os.Stat(marker); statErr != nil {
		t.Fatalf("fixture did not reach tool discovery: %v", statErr)
	}
	if !errors.Is(err, context.DeadlineExceeded) || c.Healthy() {
		t.Fatalf("expired tool discovery: Connect returned %v, healthy=%v", err, c.Healthy())
	}
	if c.transport.(*StdioTransport).cmd.ProcessState == nil {
		t.Fatal("Connect returned before the child was reaped")
	}
}

func TestSlowDiscoveryFixture(t *testing.T) {
	if len(os.Args) < 2 || os.Args[len(os.Args)-2] != "--" {
		return
	}
	in := json.NewDecoder(os.Stdin)
	for {
		var m Message
		if err := in.Decode(&m); err != nil {
			os.Exit(0)
		}
		switch m.Method {
		case MethodInitialize:
			resp, _ := NewResponse(m.ID, struct{}{})
			if err := json.NewEncoder(os.Stdout).Encode(resp); err != nil {
				os.Exit(2)
			}
		case MethodToolsList:
			if os.WriteFile(os.Args[len(os.Args)-1], nil, 0600) != nil {
				os.Exit(2)
			}
			_, _ = io.Copy(io.Discard, os.Stdin)
			os.Exit(0)
		}
	}
}

// No subprocess or large payload needed: this pipe has a reader but no buffer.
func TestStdioWriteCanBeInterrupted(t *testing.T) {
	for _, action := range []string{"cancel", "close"} {
		t.Run(action, func(t *testing.T) {
			r, w := io.Pipe()
			defer r.Close()
			transport := NewStdioTransport("", nil)
			started := make(chan struct{})
			transport.stdin = &observedWrite{w, started}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			result := make(chan error, 1)
			go func() {
				_, err := transport.Send(ctx, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: MethodPing})
				result <- err
			}()
			select {
			case <-started:
			case <-time.After(time.Second):
				t.Fatal("write did not start")
			}
			if action == "cancel" {
				cancel()
			} else {
				go transport.Close()
			}
			select {
			case err := <-result:
				if err == nil || (action == "cancel" && !errors.Is(err, context.Canceled)) {
					t.Fatalf("unexpected %s result: %v", action, err)
				}
			case <-time.After(time.Second):
				t.Fatalf("%s did not interrupt the active write", action)
			}
		})
	}
}

type observedWrite struct {
	io.WriteCloser
	started chan struct{}
}

func (w *observedWrite) Write(data []byte) (int, error) {
	close(w.started)
	return w.WriteCloser.Write(data)
}
