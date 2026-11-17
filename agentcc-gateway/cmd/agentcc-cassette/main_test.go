package main

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/record"
)

// TestMain lets a test run the binary itself: the test binary re-executed
// with CASSETTE_RUN_MAIN=1 is agentcc-cassette.
func TestMain(m *testing.M) {
	if os.Getenv("CASSETTE_RUN_MAIN") == "1" {
		main()
	}
	os.Exit(m.Run())
}

// syncBuffer is a log sink the command and the test share.
type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// runCLI runs one command to completion, in process.
func runCLI(t *testing.T, args ...string) (int, string) {
	t.Helper()
	var stdout, stderr syncBuffer
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	code := run(ctx, args, &stdout, &stderr)
	return code, stdout.String() + stderr.String()
}

// running is a long-lived command (replay, or an HTTP recording) started in
// process; stop cancels it as SIGTERM would and returns its exit code.
type running struct {
	cancel context.CancelFunc
	done   chan int
	stderr *syncBuffer
	base   string // http://host:port, once known
}

func startCLI(t *testing.T, args ...string) *running {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	r := &running{cancel: cancel, done: make(chan int, 1), stderr: &syncBuffer{}}
	go func() { r.done <- run(ctx, args, &syncBuffer{}, r.stderr) }()
	t.Cleanup(func() {
		cancel()
		select {
		case <-r.done:
		case <-time.After(10 * time.Second):
		}
	})
	return r
}

func (r *running) stop(t *testing.T) (int, string) {
	t.Helper()
	r.cancel()
	select {
	case code := <-r.done:
		r.done <- code
		return code, r.stderr.String()
	case <-time.After(15 * time.Second):
		t.Fatal("the command did not stop")
	}
	return 0, ""
}

// waitFile waits for a file a command writes once it is listening.
func waitFile(t *testing.T, path string, r *running) string {
	t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	for {
		if data, err := os.ReadFile(path); err == nil && len(data) > 0 {
			return strings.TrimSpace(string(data))
		}
		select {
		case code := <-r.done:
			r.done <- code
			t.Fatalf("the command exited %d before writing %s: %s", code, filepath.Base(path), r.stderr.String())
		default:
		}
		if time.Now().After(deadline) {
			t.Fatalf("%s never appeared", path)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// countingServer counts every request it gets and answers 200.
func countingServer(t *testing.T) (*httptest.Server, *atomic.Int64) {
	t.Helper()
	var n atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(srv.Close)
	return srv, &n
}

// TestRecordRefusedWithoutTestAccount: record refuses to run, not just to
// save, without a valid test-account label: it exits 2 naming the flag,
// sends nothing upstream, never listens, and writes no file. The library
// entry points refuse the same way.
func TestRecordRefusedWithoutTestAccount(t *testing.T) {
	upstream, hits := countingServer(t)
	dir := t.TempDir()
	out, addrFile := filepath.Join(dir, "c.cassette.json"), filepath.Join(dir, "addr")
	cases := map[string][]string{
		"mcp, no label":       {"record", "--kind", "mcp", "--url", upstream.URL + "/mcp", "--out", out},
		"mcp, empty label":    {"record", "--kind", "mcp", "--test-account", "", "--url", upstream.URL + "/mcp", "--out", out},
		"mcp, email label":    {"record", "--kind", "mcp", "--test-account", "alice@corp-test.dev", "--url", upstream.URL + "/mcp", "--out", out},
		"mcp, spaced label":   {"record", "--kind", "mcp", "--test-account", "my account", "--url", upstream.URL + "/mcp", "--out", out},
		"http, no label":      {"record", "--kind", "http", "--upstream", upstream.URL, "--addr-file", addrFile, "--out", out},
		"http, invalid label": {"record", "--kind", "http", "--test-account", "-x", "--upstream", upstream.URL, "--addr-file", addrFile, "--out", out},
	}
	for name, args := range cases {
		t.Run(name, func(t *testing.T) {
			code, logs := runCLI(t, args...)
			if code != exitUsage || !strings.Contains(logs, "record refused") || !strings.Contains(logs, "test-account") {
				t.Fatalf("exit %d, logs %q: want exit 2 naming the test-account label", code, logs)
			}
			for _, p := range []string{out, addrFile} {
				if _, err := os.Stat(p); !os.IsNotExist(err) {
					t.Fatalf("%s exists after a refused record", filepath.Base(p))
				}
			}
		})
	}
	t.Run("library", func(t *testing.T) {
		if _, err := record.MCP(context.Background(), record.MCPOptions{URL: upstream.URL + "/mcp"}); err == nil {
			t.Error("record.MCP ran without a test account")
		}
		if _, err := record.NewProxy(record.ProxyOptions{Upstream: upstream.URL}); err == nil {
			t.Error("record.NewProxy started without a test account")
		}
	})
	if n := hits.Load(); n != 0 {
		t.Fatalf("the upstream got %d request(s) from a refused record", n)
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 0 {
		t.Fatalf("a refused record left %d file(s)", len(entries))
	}
}
