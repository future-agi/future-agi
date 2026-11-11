package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

// runGatewayEnv makes this test binary run the gateway's main instead of the
// tests, so a test can start a real gateway and signal it.
const runGatewayEnv = "AGENTCC_TEST_RUN_GATEWAY"

func TestMain(m *testing.M) {
	if os.Getenv(runGatewayEnv) == "1" {
		main()
		os.Exit(0)
	}
	os.Exit(m.Run())
}

// gatewayUnderTest is a gateway running in a child process, with a fake
// provider and a fake backend that records the request logs it is sent.
type gatewayUnderTest struct {
	t      *testing.T
	cmd    *exec.Cmd
	url    string
	out    *bytes.Buffer
	exited chan struct{}

	slowStarted chan struct{} // closed when the provider gets a "slow" request
	logsPosted  chan struct{} // sent to (without blocking) on each webhook request once signalled
	signalled   atomic.Bool   // set just before the test first signals the gateway

	mu    sync.Mutex
	logs  int // request logs the webhook accepted
	early int // webhook requests before the signal: periodic flushes
}

// startGateway starts a gateway whose request-log webhook hangs when hangWebhook
// is set, and returns once it serves. A "slow" chat request is answered only
// when the test ends.
func startGateway(t *testing.T, shutdownTimeout time.Duration, hangWebhook bool) *gatewayUnderTest {
	t.Helper()
	g := &gatewayUnderTest{t: t, out: &bytes.Buffer{}, exited: make(chan struct{}),
		slowStarted: make(chan struct{}), logsPosted: make(chan struct{}, 10)}
	release := make(chan struct{})
	var slowOnce sync.Once

	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if bytes.Contains(body, []byte("slow")) {
			slowOnce.Do(func() { close(g.slowStarted) })
			select {
			case <-r.Context().Done():
			case <-release:
			}
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o",`+
			`"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],`+
			`"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if g.signalled.Load() {
			select {
			case g.logsPosted <- struct{}{}:
			default:
			}
		} else {
			g.mu.Lock()
			g.early++
			g.mu.Unlock()
		}
		if hangWebhook {
			select {
			case <-r.Context().Done():
			case <-release:
			}
			return
		}
		var payload struct {
			Logs []json.RawMessage `json:"logs"`
		}
		_ = json.NewDecoder(r.Body).Decode(&payload)
		g.mu.Lock()
		g.logs += len(payload.Logs)
		g.mu.Unlock()
	}))
	t.Cleanup(backend.Close)
	t.Cleanup(provider.Close)
	t.Cleanup(func() { close(release) })

	port := freePort(t)
	cfgPath := filepath.Join(t.TempDir(), "gateway.yaml")
	cfg := fmt.Sprintf(`server:
  host: 127.0.0.1
  port: %d
  shutdown_timeout: %s
providers:
  openai:
    base_url: %s
    api_key: fake
    api_format: openai
    models: [gpt-4o]
control_plane:
  url: %s
logging:
  request_logging:
    enabled: true
`, port, shutdownTimeout, provider.URL, backend.URL)
	if err := os.WriteFile(cfgPath, []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}

	g.cmd = exec.Command(os.Args[0], "-config", cfgPath)
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "AGENTCC_") { // they would override the config
			g.cmd.Env = append(g.cmd.Env, kv)
		}
	}
	// Under -race, exiting sleeps 1s by default, which would let goroutines
	// finish work that a plain build's exit cuts short.
	g.cmd.Env = append(g.cmd.Env, runGatewayEnv+"=1", "GORACE="+os.Getenv("GORACE")+" atexit_sleep_ms=0")
	g.cmd.Stdout, g.cmd.Stderr = g.out, g.out
	if err := g.cmd.Start(); err != nil {
		t.Fatal(err)
	}
	go func() {
		_ = g.cmd.Wait()
		close(g.exited)
	}()
	t.Cleanup(func() {
		_ = g.cmd.Process.Kill()
		<-g.exited
		if t.Failed() {
			t.Logf("gateway output:\n%s", g.out)
		}
	})

	g.url = fmt.Sprintf("http://127.0.0.1:%d", port)
	for deadline := time.Now().Add(10 * time.Second); ; time.Sleep(20 * time.Millisecond) {
		if resp, err := http.Get(g.url + "/healthz"); err == nil {
			resp.Body.Close()
			return g
		}
		if time.Now().After(deadline) {
			t.Fatal("gateway did not start")
		}
	}
}

func freePort(t *testing.T) int {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port
}

// chat sends a chat request whose message is content.
func (g *gatewayUnderTest) chat(content string) error {
	resp, err := http.Post(g.url+"/v1/chat/completions", "application/json",
		strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"`+content+`"}]}`))
	if err != nil {
		return err
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("status %d", resp.StatusCode)
	}
	return nil
}

func (g *gatewayUnderTest) signal(sig os.Signal) {
	g.t.Helper()
	g.signalled.Store(true)
	if err := g.cmd.Process.Signal(sig); err != nil {
		g.t.Fatal(err)
	}
}

// wait waits up to timeout for the gateway to exit and returns how long that
// took from start.
func (g *gatewayUnderTest) wait(start time.Time, timeout time.Duration) time.Duration {
	g.t.Helper()
	select {
	case <-g.exited:
		return time.Since(start)
	case <-time.After(timeout):
		g.t.Fatalf("gateway still running %s after the signal", timeout)
		return 0
	}
}

func (g *gatewayUnderTest) acceptedLogs() int {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.logs
}

// skipIfFlushedEarly skips the test when the gateway's periodic flush sent
// the request log before the signal, so its shutdown had none to send.
func (g *gatewayUnderTest) skipIfFlushedEarly() {
	g.t.Helper()
	g.mu.Lock()
	early := g.early
	g.mu.Unlock()
	if early > 0 {
		g.t.Skip("a periodic flush sent the request log before the signal")
	}
}

// On SIGTERM the gateway exits only after it has sent the request logs it
// buffered.
func TestShutdown_DeliversBufferedRequestLogs(t *testing.T) {
	g := startGateway(t, 5*time.Second, false)
	if err := g.chat("hi"); err != nil {
		t.Fatal(err)
	}

	g.signal(syscall.SIGTERM)
	g.wait(time.Now(), 10*time.Second)
	g.skipIfFlushedEarly()

	if code := g.cmd.ProcessState.ExitCode(); code != 0 {
		t.Errorf("exit code %d, want 0", code)
	}
	if n := g.acceptedLogs(); n != 1 {
		t.Errorf("backend got %d request logs, want 1", n)
	}
}

// A request that outlasts shutdown_timeout makes the gateway exit 1, but only
// after it has sent the request logs it buffered.
func TestShutdown_DeliversRequestLogsWhenTheDrainTimesOut(t *testing.T) {
	g := startGateway(t, 300*time.Millisecond, false)
	if err := g.chat("hi"); err != nil {
		t.Fatal(err)
	}
	go func() { _ = g.chat("slow") }()
	<-g.slowStarted

	g.signal(syscall.SIGTERM)
	g.wait(time.Now(), 10*time.Second)
	g.skipIfFlushedEarly()

	if code := g.cmd.ProcessState.ExitCode(); code != 1 {
		t.Errorf("exit code %d, want 1", code)
	}
	if n := g.acceptedLogs(); n != 1 {
		t.Errorf("backend got %d request logs, want 1", n)
	}
}

// A second signal stops a gateway that is still shutting down, here waiting
// on a webhook that does not answer.
func TestShutdown_SecondSignalStopsTheGatewayAtOnce(t *testing.T) {
	g := startGateway(t, 5*time.Second, true)
	if err := g.chat("hi"); err != nil {
		t.Fatal(err)
	}

	g.signal(syscall.SIGTERM)
	select {
	case <-g.logsPosted: // the last flush is waiting on the webhook
	case <-g.exited:
		g.skipIfFlushedEarly()
		t.Fatal("gateway exited before its last flush")
	}
	start := time.Now()
	g.signal(syscall.SIGTERM)
	if took := g.wait(start, 10*time.Second); took > time.Second {
		t.Errorf("gateway exited %s after the second signal, want at once", took)
	}

	status, _ := g.cmd.ProcessState.Sys().(syscall.WaitStatus)
	if !status.Signaled() || status.Signal() != syscall.SIGTERM {
		t.Errorf("gateway exit status %v, want killed by SIGTERM", g.cmd.ProcessState)
	}
}
