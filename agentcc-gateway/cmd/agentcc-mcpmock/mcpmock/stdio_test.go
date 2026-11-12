package mcpmock

import (
	"bufio"
	"encoding/json"
	"io"
	"strings"
	"testing"
	"time"
)

// stdioClient drives ServeStdio over pipes.
type stdioClient struct {
	t    *testing.T
	in   *io.PipeWriter
	msgs chan Message
	done chan error
}

func startStdio(t *testing.T, era string) (*Mock, *stdioClient) {
	t.Helper()
	e, err := ParseEra(era)
	if err != nil {
		t.Fatal(err)
	}
	fix, err := LoadFixtures(fixtureDir)
	if err != nil {
		t.Fatal(err)
	}
	m, err := New(Config{Era: e, Stdio: true, ElicitTimeout: 5 * time.Second}, fix)
	if err != nil {
		t.Fatal(err)
	}
	inR, inW := io.Pipe()
	outR, outW := io.Pipe()
	c := &stdioClient{t: t, in: inW, msgs: make(chan Message, 64), done: make(chan error, 1)}
	go func() {
		c.done <- m.ServeStdio(inR, outW)
		outW.Close()
	}()
	go func() {
		sc := bufio.NewScanner(outR)
		sc.Buffer(make([]byte, 0, 64<<10), 16<<20)
		for sc.Scan() {
			var msg Message
			if err := json.Unmarshal(sc.Bytes(), &msg); err != nil {
				t.Errorf("stdout line %q is not JSON-RPC: %v", sc.Text(), err)
				continue
			}
			c.msgs <- msg
		}
		close(c.msgs)
	}()
	t.Cleanup(func() { inW.Close() })
	return m, c
}

func (c *stdioClient) send(v any) {
	c.t.Helper()
	data, _ := json.Marshal(v)
	if _, err := c.in.Write(append(data, '\n')); err != nil {
		c.t.Fatal(err)
	}
}

func (c *stdioClient) next() Message {
	c.t.Helper()
	select {
	case msg, ok := <-c.msgs:
		if !ok {
			c.t.Fatal("stdout closed")
		}
		return msg
	case <-time.After(5 * time.Second):
		c.t.Fatal("no stdout message within 5s")
	}
	return Message{}
}

func (c *stdioClient) request(id int, method string, params any) Message {
	c.t.Helper()
	c.send(map[string]any{"jsonrpc": "2.0", "id": id, "method": method, "params": params})
	return c.next()
}

// TestStdioHandshakesAndEnvNames: a stdio mock answers the legacy handshake
// and 2026 requests, lists env_names, and env_names returns the variable
// names it sees and never their values.
func TestStdioHandshakesAndEnvNames(t *testing.T) {
	t.Setenv("MCPMOCK_TEST_SECRET", "value-that-must-not-leak")
	_, c := startStdio(t, "dual")
	init := c.request(1, "initialize", map[string]any{"protocolVersion": V0618, "capabilities": map[string]any{}})
	if !strings.Contains(string(init.Result), `"protocolVersion":"`+V0618+`"`) {
		t.Fatalf("initialize = %s", init.Result)
	}
	c.send(map[string]any{"jsonrpc": "2.0", "method": "notifications/initialized"})
	list := c.request(2, "tools/list", nil)
	if !strings.Contains(string(list.Result), `"env_names"`) || !strings.Contains(string(list.Result), `"echo"`) {
		t.Fatalf("tools/list = %s", list.Result)
	}
	call := c.request(3, "tools/call", map[string]any{"name": "env_names"})
	if !strings.Contains(string(call.Result), "MCPMOCK_TEST_SECRET") || strings.Contains(string(call.Result), "value-that-must-not-leak") {
		t.Fatalf("env_names = %s", call.Result)
	}
	discover := c.request(4, "server/discover", meta2026(nil))
	if !strings.Contains(string(discover.Result), `"resultType":"complete"`) {
		t.Fatalf("2026 discover over stdio = %s %+v", discover.Result, discover.Error)
	}
	_, c26 := startStdio(t, "2026")
	if r := c26.request(1, "tools/list", nil); r.Error == nil || r.Error.Code != CodeMissingClientCapability {
		t.Fatalf("a 2026 stdio request without _meta = %+v", r.Error)
	}
	_, c25 := startStdio(t, V1125)
	if r := c25.request(1, "tools/list", nil); r.Error == nil {
		t.Fatal("a legacy stdio mock served a request before initialize")
	}
}

// TestStdioNotificationsCancelAndClose: /__control/tools reaches the stdio
// peer as list_changed; notifications/cancelled stops an in-flight call
// (charged, not completed); ServeStdio returns when stdin closes.
func TestStdioNotificationsCancelAndClose(t *testing.T) {
	m, c := startStdio(t, V1125)
	c.request(1, "initialize", map[string]any{"protocolVersion": V1125, "capabilities": map[string]any{}})
	m.State().SetTools(nil)
	m.Broadcast("notifications/tools/list_changed", nil)
	if n := c.next(); n.Method != "notifications/tools/list_changed" {
		t.Fatalf("got %+v, want list_changed", n)
	}
	fix, _ := LoadFixtures(fixtureDir)
	m.State().SetTools(fix.Tools)
	m.State().SetBehavior(Behavior{LatencyMS: 5000})
	c.send(map[string]any{"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": echoParams(nil)})
	waitFor(t, "the call's charge", func() bool { got, _ := m.State().Charges(); return len(got) == 1 })
	c.send(map[string]any{"jsonrpc": "2.0", "method": "notifications/cancelled", "params": map[string]any{"requestId": 9}})
	if r := c.next(); string(r.ID) != "9" || r.Error == nil {
		t.Fatalf("the cancelled call answered %+v", r)
	}
	if got, _ := m.State().Charges(); got[0].Completed {
		t.Fatal("a cancelled call was marked completed")
	}
	c.in.Close()
	select {
	case err := <-c.done:
		if err != nil {
			t.Fatalf("ServeStdio = %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("ServeStdio did not return after stdin closed")
	}
}

// TestStdioCloseEndsPendingElicitation: when stdin closes while a call waits
// for the client's answer to elicitation/create, the call fails at once
// (the answer could only come on stdin) instead of waiting out
// ElicitTimeout (5 s here), so ServeStdio returns and the binary can write
// its graceful marker inside a client's shutdown grace.
func TestStdioCloseEndsPendingElicitation(t *testing.T) {
	m, c := startStdio(t, V1125)
	c.request(1, "initialize", map[string]any{"protocolVersion": V1125, "capabilities": map[string]any{"elicitation": map[string]any{}}})
	m.State().SetBehavior(Behavior{Elicit: true})
	c.send(map[string]any{"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": echoParams(nil)})
	if ask := c.next(); ask.Method != "elicitation/create" {
		t.Fatalf("got %+v, want an elicitation/create request", ask)
	}
	start := time.Now()
	c.in.Close()
	if r := c.next(); string(r.ID) != "7" || r.Error == nil || !strings.Contains(r.Error.Message, "stdin closed") {
		t.Fatalf("the pending call answered %+v", r)
	}
	select {
	case err := <-c.done:
		if err != nil {
			t.Fatalf("ServeStdio = %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("ServeStdio did not return after stdin closed")
	}
	if waited := time.Since(start); waited > 2*time.Second {
		t.Fatalf("ServeStdio took %s after stdin closed: it waited out the elicitation", waited)
	}
	if got, _ := m.State().Charges(); len(got) != 1 || got[0].Completed {
		t.Fatalf("charges = %+v: the call is charged at receipt and never completed", got)
	}
}
