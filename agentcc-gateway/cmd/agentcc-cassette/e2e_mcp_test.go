package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

const mockFixtures = "../../scripts/scenarios/fixtures/mcp/common"

// recordMCPViaCLI records url through the CLI and returns the cassette's text.
func recordMCPViaCLI(t *testing.T, url, tokenEnv string, calls []map[string]any, extra ...string) (string, string) {
	t.Helper()
	dir := t.TempDir()
	callsFile, out := filepath.Join(dir, "calls.json"), filepath.Join(dir, "c.cassette.json")
	data, _ := json.Marshal(calls)
	if err := os.WriteFile(callsFile, data, 0o600); err != nil {
		t.Fatal(err)
	}
	args := append([]string{"record", "--kind", "mcp", "--test-account", "ci-mcp", "--url", url,
		"--calls", callsFile, "--auth-env", tokenEnv, "--out", out}, extra...)
	if code, logs := runCLI(t, args...); code != exitOK {
		t.Fatalf("record exited %d: %s", code, logs)
	}
	text, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	return string(text), out
}

// replayClient starts a replay of file and connects the gateway's own MCP
// client to it, as the conformance run puts a replay behind the gateway.
func replayClient(t *testing.T, file string) (*mcp.Client, *running) {
	t.Helper()
	addrFile := filepath.Join(t.TempDir(), "addr")
	r := startCLI(t, "replay", "--cassette", file, "--addr", "127.0.0.1:0", "--addr-file", addrFile)
	r.base = "http://" + waitFile(t, addrFile, r)
	client := mcp.NewClient(mcp.ClientConfig{ServerID: "replay", URL: r.base + "/mcp", TransportType: "http", Auth: mcp.AuthConfig{Type: "none"}})
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := client.Connect(ctx); err != nil {
		t.Fatalf("the gateway's MCP client could not connect to the replay: %v (%s)", err, r.stderr.String())
	}
	t.Cleanup(func() { client.Close() })
	return client, r
}

// stopReplay closes the client first, so the replay stops at once, and
// returns the replay's exit code and logs.
func stopReplay(t *testing.T, client *mcp.Client, r *running) (int, string) {
	t.Helper()
	client.Close()
	return r.stop(t)
}

func compactRaw(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	var buf bytes.Buffer
	if err := json.Compact(&buf, raw); err != nil {
		t.Fatalf("compact %s: %v", raw, err)
	}
	return buf.String()
}

// replaysEveryCall checks the gateway's client gets, for every recorded
// tools/call, exactly the recorded (scrubbed) result.
func replaysEveryCall(t *testing.T, client *mcp.Client, c *cassette.Cassette) int {
	t.Helper()
	n := 0
	for _, x := range c.MCP.Exchanges {
		if x.Method != "tools/call" || len(x.Result) == 0 {
			continue
		}
		var p struct {
			Name      string          `json:"name"`
			Arguments json.RawMessage `json:"arguments"`
		}
		if err := json.Unmarshal(x.Params, &p); err != nil {
			t.Fatal(err)
		}
		got, err := client.CallToolRaw(context.Background(), p.Name, p.Arguments, mcp.CallOptions{Timeout: 10 * time.Second})
		if err != nil {
			t.Fatalf("replayed %s: %v", p.Name, err)
		}
		if compactRaw(t, got) != compactRaw(t, x.Result) {
			t.Errorf("replayed %s = %s\nrecorded %s", p.Name, got, x.Result)
		}
		n++
	}
	return n
}

// TestRecordReplayMCP records MCP servers through the CLI (a vendor-shaped
// server with a bearer, emails, ids, an echoed credential and an SSE
// answer; mcpmock in a session era and in the stateless 2026 era), checks
// the cassette holds no credential, email or id, then replays it through
// the CLI to the gateway's own MCP client and gets every recorded result
// back exactly, with recorded vendor text kept as data.
func TestRecordReplayMCP(t *testing.T) {
	t.Run("vendor-shaped server", func(t *testing.T) {
		token := tokenShaped("vendor-bearer")
		t.Setenv("CASSETTE_TEST_VENDOR_TOKEN", token)
		vendor, srv := newVendorMCP(t, token)
		text, file := recordMCPViaCLI(t, srv.URL+"/mcp", "CASSETTE_TEST_VENDOR_TOKEN", []map[string]any{
			{"name": "search_threads", "arguments": map[string]any{"query": "from:" + vendorAlice}},
			{"name": "get_thread", "arguments": map[string]any{"thread_id": vendorThread}},
			{"name": "get_thread", "arguments": map[string]any{"thread_id": "missing-thread"}},
		}, "--redact", "vendor-mail")
		for _, v := range []string{token, vendorThread, vendorWorkspace, vendorAlice, vendorBob, fmt.Sprint(vendorUser), "vendor-mail", "vendor-session-1"} {
			if strings.Contains(text, v) {
				t.Errorf("the cassette holds a recorded value (%d bytes)", len(v))
			}
		}
		if err := cassette.Check([]byte(text)); err != nil {
			t.Fatalf("check: %v", err)
		}
		c, err := cassette.Load([]byte(text))
		if err != nil {
			t.Fatal(err)
		}
		var methods []string
		for _, x := range c.MCP.Exchanges {
			methods = append(methods, x.Method)
		}
		if got := strings.Join(methods, ","); got != "initialize,tools/list,resources/list,prompts/list,tools/call,tools/call,tools/call" ||
			c.MCP.ProtocolVersion != "2025-11-25" || c.TestAccount != "ci-mcp" {
			t.Fatalf("recorded %s (era %s, account %s)", got, c.MCP.ProtocolVersion, c.TestAccount)
		}
		if !strings.Contains(string(c.MCP.Exchanges[2].Error), "-32601") {
			t.Errorf("resources/list's error was not recorded: %s", c.MCP.Exchanges[2].Error)
		}
		before := vendor.hits.Load()
		client, r := replayClient(t, file)
		if names := toolNames(client); names != "search_threads,get_thread" {
			t.Errorf("replayed tools = %s", names)
		}
		if n := replaysEveryCall(t, client, c); n != 3 {
			t.Errorf("replayed %d calls, want 3", n)
		}
		var res struct {
			Content []struct{ Text string } `json:"content"`
		}
		_ = json.Unmarshal(c.MCP.Exchanges[5].Result, &res)
		if len(res.Content) != 1 || !strings.HasPrefix(res.Content[0].Text, "debug: authorized with scrubbed-token-") ||
			!strings.HasSuffix(res.Content[0].Text, "; "+vendorText) {
			t.Errorf("vendor text did not survive as data: %+v", res.Content)
		}
		if vendor.hits.Load() != before {
			t.Error("the replay reached the vendor server")
		}
		if code, logs := stopReplay(t, client, r); code != exitOK {
			t.Errorf("replay exited %d: %s", code, logs)
		}
	})
	for _, era := range []string{"2025-11-25", "dual"} {
		t.Run("mcpmock era "+era, func(t *testing.T) {
			token := tokenShaped("mock-bearer")
			t.Setenv("CASSETTE_TEST_MOCK_TOKEN", token)
			mockEra, err := mcpmock.ParseEra(era)
			if err != nil {
				t.Fatal(err)
			}
			fix, err := mcpmock.LoadFixtures(mockFixtures)
			if err != nil {
				t.Fatal(err)
			}
			m, err := mcpmock.New(mcpmock.Config{Era: mockEra, Auth: "bearer:" + token}, fix)
			if err != nil {
				t.Fatal(err)
			}
			srv := httptest.NewServer(m.Handler())
			t.Cleanup(srv.Close)
			m.SetPublicURL(srv.URL)
			text, file := recordMCPViaCLI(t, srv.URL+"/mcp", "CASSETTE_TEST_MOCK_TOKEN", []map[string]any{
				{"name": "echo", "arguments": map[string]any{"text": "hello " + vendorAlice}},
				{"name": "get_weather", "arguments": map[string]any{"city": "Paris"}},
			})
			if strings.Contains(text, token) || strings.Contains(text, vendorAlice) {
				t.Error("the cassette holds the credential or the email")
			}
			c, err := cassette.Load([]byte(text))
			if err != nil {
				t.Fatal(err)
			}
			wantEra := map[string]string{"2025-11-25": "2025-11-25", "dual": "2026-07-28"}[era]
			if c.MCP.ProtocolVersion != wantEra {
				t.Fatalf("recorded era %s, want %s", c.MCP.ProtocolVersion, wantEra)
			}
			client, r := replayClient(t, file)
			if client.Era() != wantEra {
				t.Errorf("the gateway's client negotiated %s with the replay, want %s", client.Era(), wantEra)
			}
			if names := toolNames(client); names != "echo,get_weather,delete_file" {
				t.Errorf("replayed tools = %s", names)
			}
			if n := replaysEveryCall(t, client, c); n != 2 {
				t.Errorf("replayed %d calls, want 2", n)
			}
			if code, logs := stopReplay(t, client, r); code != exitOK {
				t.Errorf("replay exited %d: %s", code, logs)
			}
		})
	}
}

func toolNames(client *mcp.Client) string {
	var names []string
	for _, tool := range client.Tools() {
		names = append(names, tool.Name)
	}
	return strings.Join(names, ",")
}
