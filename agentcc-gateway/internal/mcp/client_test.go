package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// TestStdioChildSurvivesConnectCancel: the connect context bounds the
// handshake only. Cancelling it, as the static pool does right after Connect,
// leaves the child running and callable.
func TestStdioChildSurvivesConnectCancel(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "marker")
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	c := connectStdioHelperCtx(t, ctx, ClientConfig{ServerID: "survive-connect"}, "--marker", marker)
	cancel() // as the static pool does right after Connect

	time.Sleep(300 * time.Millisecond)
	text, err := callText(t, c, "echo", map[string]interface{}{"text": "still here"})
	if err != nil {
		t.Fatalf("call after the connect context ended: %v (child marker: %v)", err, readMarker(marker))
	}
	if text != "still here" {
		t.Fatalf("echo = %q", text)
	}
	if !c.Healthy() {
		t.Fatal("client unhealthy after a successful call")
	}
	if got := readMarker(marker); len(got) != 1 || !strings.HasPrefix(got[0], "start") {
		t.Fatalf("child lifecycle = %v, want only its start", got)
	}
}

// stdioPid is the pid of the client's current stdio child.
func stdioPid(t *testing.T, c *Client) int {
	t.Helper()
	st, ok := c.currentTransport().(*StdioTransport)
	if !ok || st.proc == nil {
		t.Fatalf("client %s has no stdio transport", c.ServerID())
	}
	return st.proc.Pid()
}

// waitReconnected waits until the client is healthy on a child other than old,
// and returns the new child's pid.
func waitReconnected(t *testing.T, c *Client, old int, within time.Duration) int {
	t.Helper()
	for deadline := time.Now().Add(within); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if !c.Healthy() {
			continue
		}
		if st, ok := c.currentTransport().(*StdioTransport); ok && st.proc != nil && st.proc.Pid() != old {
			return st.proc.Pid()
		}
	}
	t.Fatalf("client %s did not reconnect to a new child within %s", c.ServerID(), within)
	return 0
}

// processGone reports whether pid has exited (and been reaped) within d.
func processGone(pid int, d time.Duration) bool {
	for deadline := time.Now().Add(d); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if err := syscall.Kill(pid, 0); errors.Is(err, syscall.ESRCH) {
			return true
		}
	}
	return false
}

// TestStdioReconnectSurvives: after the child dies, reconnect starts a new one
// under a 30 s handshake context; the new child outlives that context.
func TestStdioReconnectSurvives(t *testing.T) {
	c := connectStdioHelper(t, ClientConfig{ServerID: "reconnect"})
	first := stdioPid(t, c)
	if err := syscall.Kill(first, syscall.SIGKILL); err != nil {
		t.Fatalf("kill the child: %v", err)
	}

	second := waitReconnected(t, c, first, 15*time.Second)
	time.Sleep(500 * time.Millisecond) // the reconnect's handshake context has ended
	text, err := callText(t, c, "pid", nil)
	if err != nil {
		t.Fatalf("call after the reconnect: %v", err)
	}
	if text != strconv.Itoa(second) {
		t.Fatalf("answered by pid %s, want the reconnected child %d", text, second)
	}
	if processGone(second, 200*time.Millisecond) {
		t.Fatal("the reconnected child died after its handshake")
	}
}

// TestStdioOversizeFailsPendingAndReconnects: a message over the limit fails
// every pending call with upstream_message_too_large, marks the client
// unhealthy, and reconnect restarts the child.
func TestStdioOversizeFailsPendingAndReconnects(t *testing.T) {
	c := connectStdioHelper(t, ClientConfig{ServerID: "oversize", MaxMessageBytes: 64 << 10})
	first := stdioPid(t, c)

	slowErr := make(chan error, 1)
	go func() {
		_, err := callText(t, c, "echo", map[string]interface{}{"text": "slow", "delay_ms": 3000})
		slowErr <- err
	}()
	time.Sleep(200 * time.Millisecond) // the slow call is pending

	_, err := callText(t, c, "big", map[string]interface{}{"bytes": 128 << 10})
	if !errors.Is(err, ErrUpstreamMessageTooLarge) || !strings.Contains(err.Error(), "upstream_message_too_large") {
		t.Fatalf("oversize call error = %v, want upstream_message_too_large", err)
	}
	if c.Healthy() {
		t.Fatal("client still healthy after an oversize message")
	}
	select {
	case err := <-slowErr:
		if !errors.Is(err, ErrUpstreamMessageTooLarge) {
			t.Fatalf("pending call error = %v, want upstream_message_too_large", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the pending call was not failed by the oversize message")
	}

	waitReconnected(t, c, first, 15*time.Second)
	if text, err := callText(t, c, "echo", map[string]interface{}{"text": "after"}); err != nil || text != "after" {
		t.Fatalf("call after the restart = %q, %v", text, err)
	}
	if !processGone(first, 5*time.Second) {
		t.Fatal("the oversize child is still running after the restart")
	}
}

// TestStdioConcurrentIdsNoCrossTalk: concurrent calls each get their own
// result, and the transport refuses a request whose id is already pending.
func TestStdioConcurrentIdsNoCrossTalk(t *testing.T) {
	c := connectStdioHelper(t, ClientConfig{ServerID: "ids"})

	const calls = 64
	var wg sync.WaitGroup
	errs := make(chan error, calls)
	for i := 0; i < calls; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			want := "call-" + strconv.Itoa(i)
			// Varied delays make the child answer out of order.
			got, err := callText(t, c, "echo", map[string]interface{}{"text": want, "delay_ms": (i * 7) % 40})
			if err != nil || got != want {
				errs <- fmt.Errorf("call %d got %q, %v", i, got, err)
			}
		}(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}

	tr := c.currentTransport().(*StdioTransport)
	params, _ := json.Marshal(ToolCallParams{Name: "echo", Arguments: map[string]interface{}{"text": "first", "delay_ms": 2000}})
	msg := func() *Message {
		return &Message{JSONRPC: "2.0", ID: json.RawMessage(`"dup"`), Method: MethodToolsCall, Params: params}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	first := make(chan *Message, 1)
	go func() {
		resp, _ := tr.Send(ctx, msg())
		first <- resp
	}()
	for deadline, pending := time.Now().Add(5*time.Second), false; !pending; time.Sleep(5 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("the first request never became pending")
		}
		tr.pendMu.Lock()
		_, pending = tr.pending[`"dup"`]
		tr.pendMu.Unlock()
	}
	if _, err := tr.Send(ctx, msg()); !errors.Is(err, ErrDuplicateRequestID) {
		t.Fatalf("second send with a pending id: err = %v, want ErrDuplicateRequestID", err)
	}
	resp := <-first
	var res ToolCallResult
	if resp == nil || json.Unmarshal(resp.Result, &res) != nil || len(res.Content) != 1 || res.Content[0].Text != "first" {
		t.Fatalf("the first request lost its answer: %+v", resp)
	}
}

// TestStdioConnectFailureClosesChild: a handshake that fails leaves no child
// running.
func TestStdioConnectFailureClosesChild(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "marker")
	command, args := stdioHelperCommand("--marker", marker, "--fail-initialize")
	c := NewClient(ClientConfig{ServerID: "bad-init", TransportType: "stdio", Command: command, Args: args})
	defer c.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := c.Connect(ctx); err == nil || !strings.Contains(err.Error(), "initialize refused") {
		t.Fatalf("connect error = %v, want the initialize error", err)
	}
	lines := readMarker(marker)
	if len(lines) == 0 || lines[len(lines)-1] != "graceful-exit" {
		t.Fatalf("child lifecycle after a failed connect = %v, want it shut down", lines)
	}
}

// TestStdioCloseStopsReconnect: Close during a reconnect's backoff starts no
// new child.
func TestStdioCloseStopsReconnect(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "marker")
	c := connectStdioHelper(t, ClientConfig{ServerID: "close-reconnect"}, "--marker", marker)
	syscall.Kill(stdioPid(t, c), syscall.SIGKILL)
	for deadline := time.Now().Add(5 * time.Second); c.Healthy(); time.Sleep(10 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatal("the client never saw its child exit")
		}
	}
	c.Close()                           // during the reconnect's first backoff
	time.Sleep(1500 * time.Millisecond) // past the first backoff
	starts := 0
	for _, l := range readMarker(marker) {
		if strings.HasPrefix(l, "start") {
			starts++
		}
	}
	if starts != 1 {
		t.Fatalf("%d children started, want 1 (no reconnect after Close)", starts)
	}
}

// TestStdioCloseAbortsConnect: Close during a handshake that would otherwise
// wait for its context ends the handshake at once and shuts the child down.
func TestStdioCloseAbortsConnect(t *testing.T) {
	marker := filepath.Join(t.TempDir(), "marker")
	command, args := stdioHelperCommand("--marker", marker, "--silent")
	c := NewClient(ClientConfig{ServerID: "close-connect", TransportType: "stdio", Command: command, Args: args})
	done := make(chan error, 1)
	go func() { done <- c.Connect(context.Background()) }()
	waitMarker(t, marker, "start")

	c.Close()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("Connect succeeded against a server that never answers")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Close did not abort the handshake")
	}
	lines := readMarker(marker)
	if len(lines) == 0 || lines[len(lines)-1] != "graceful-exit" {
		t.Fatalf("child lifecycle after the aborted connect = %v, want it shut down", lines)
	}
}
