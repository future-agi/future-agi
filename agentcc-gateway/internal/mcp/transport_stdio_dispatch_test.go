package mcp

import (
	"context"
	"encoding/json"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// TestStdioDispatchNoticeOnlyAfterTheWrite: the stdio
// transport reports a request dispatched (WithDispatchNotice) once its write
// to the child's stdin returned without an error, and never for a request it
// did not write: one cut short because the child stopped reading, or one sent
// on a closed transport.
func TestStdioDispatchNoticeOnlyAfterTheWrite(t *testing.T) {
	opts := defaultStdioOptions()
	opts.grace, opts.termWait = 200*time.Millisecond, 200*time.Millisecond // the helper never sees stdin close
	tr := startHelperTransport(t, opts)
	var notices atomic.Int32
	noticed := WithDispatchNotice(context.Background(), func() { notices.Add(1) })
	send := func(ctx context.Context, id, name string, args map[string]interface{}, timeout time.Duration) error {
		t.Helper()
		params, _ := json.Marshal(ToolCallParams{Name: name, Arguments: args})
		ctx, cancel := context.WithTimeout(ctx, timeout)
		defer cancel()
		_, err := tr.Send(ctx, &Message{JSONRPC: "2.0", ID: json.RawMessage(id), Method: MethodToolsCall, Params: params})
		return err
	}

	if err := send(noticed, "1", "echo", map[string]interface{}{"text": "hi"}, 5*time.Second); err != nil {
		t.Fatalf("echo: %v", err)
	}
	if n := notices.Load(); n != 1 {
		t.Fatalf("a written request: %d notices, want 1", n)
	}
	if err := send(context.Background(), "2", "echo", map[string]interface{}{"text": "hi"}, 5*time.Second); err != nil {
		t.Fatalf("echo without a notice: %v", err)
	}

	// The child stops reading stdin: a 4 MiB write is cut short and never
	// completes, so the request was not dispatched.
	if err := send(noticed, "3", "stop_reading", nil, 5*time.Second); err != nil {
		t.Fatalf("stop_reading: %v", err)
	}
	if n := notices.Load(); n != 2 {
		t.Fatalf("stop_reading, written: %d notices, want 2", n)
	}
	err := send(noticed, "4", "echo", map[string]interface{}{"text": strings.Repeat("z", 4<<20)}, 300*time.Millisecond)
	if err == nil {
		t.Fatal("a 4 MiB write to a child that reads nothing succeeded")
	}
	if n := notices.Load(); n != 2 {
		t.Errorf("a write cut short: %d notices, want still 2", n)
	}

	// A closed transport writes nothing.
	_ = tr.Close()
	if err := send(noticed, "5", "echo", map[string]interface{}{"text": "hi"}, time.Second); err == nil {
		t.Fatal("a send on a closed transport succeeded")
	}
	if n := notices.Load(); n != 2 {
		t.Errorf("a closed transport: %d notices, want still 2", n)
	}
}
