package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

const fixtures = "../../scripts/scenarios/fixtures/mcp/common"

// TestMain lets a test run the binary itself: the test binary re-executed
// with MCPMOCK_RUN_MAIN=1 is agentcc-mcpmock.
func TestMain(m *testing.M) {
	if os.Getenv("MCPMOCK_RUN_MAIN") == "1" {
		main()
		os.Exit(0)
	}
	os.Exit(m.Run())
}

type child struct {
	cmd   *exec.Cmd
	stdin io.WriteCloser
	out   *bufio.Scanner
	done  chan error
}

func startMock(t *testing.T, args ...string) *child {
	t.Helper()
	cmd := exec.Command(os.Args[0], args...)
	cmd.Env = append(os.Environ(), "MCPMOCK_RUN_MAIN=1")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	c := &child{cmd: cmd, stdin: stdin, out: bufio.NewScanner(stdout), done: make(chan error, 1)}
	go func() { c.done <- cmd.Wait() }()
	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		<-c.done
	})
	return c
}

func (c *child) waitExit(t *testing.T, within time.Duration) error {
	t.Helper()
	select {
	case err := <-c.done:
		c.done <- err // keep it for the cleanup
		return err
	case <-time.After(within):
		t.Fatalf("the mock did not exit within %s", within)
	}
	return nil
}

func (c *child) alive() bool {
	select {
	case err := <-c.done:
		c.done <- err
		return false
	default:
		return true
	}
}

func waitFile(t *testing.T, path string) string {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for {
		if data, err := os.ReadFile(path); err == nil && len(data) > 0 {
			return strings.TrimSpace(string(data))
		}
		if time.Now().After(deadline) {
			t.Fatalf("%s never appeared", path)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// TestStdioShutdownMarker: a stdio mock answers on stdout, serves its control
// API on the side port, and writes the marker when it exits after stdin
// closes; SIGTERM without --ignore-sigterm ends it without the marker.
func TestStdioShutdownMarker(t *testing.T) {
	dir := t.TempDir()
	marker, addrFile := filepath.Join(dir, "marker"), filepath.Join(dir, "addr")
	c := startMock(t, "--mode", "stdio", "--era", "2025-11-25", "--fixtures", fixtures,
		"--addr", "127.0.0.1:0", "--addr-file", addrFile, "--marker", marker)
	io.WriteString(c.stdin, `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{}}}`+"\n")
	if !c.out.Scan() || !strings.Contains(c.out.Text(), `"protocolVersion":"2025-11-25"`) {
		t.Fatalf("stdout = %q", c.out.Text())
	}
	resp, err := http.Get("http://" + waitFile(t, addrFile) + "/__health")
	if err != nil {
		t.Fatal(err)
	}
	var health map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&health)
	resp.Body.Close()
	if health["mode"] != "stdio" || health["tools"].(float64) != 4 { // three fixtures and env_names
		t.Fatalf("/__health = %v", health)
	}
	c.stdin.Close()
	if err := c.waitExit(t, 10*time.Second); err != nil {
		t.Fatalf("exit after stdin close = %v, want 0", err)
	}
	if got := waitFile(t, marker); !strings.HasPrefix(got, "graceful stdin close") {
		t.Fatalf("marker = %q", got)
	}

	marker2, addr2 := filepath.Join(dir, "marker2"), filepath.Join(dir, "addr2")
	c2 := startMock(t, "--mode", "stdio", "--fixtures", fixtures, "--marker", marker2,
		"--addr", "127.0.0.1:0", "--addr-file", addr2)
	waitFile(t, addr2) // running
	_ = c2.cmd.Process.Signal(syscall.SIGTERM)
	c2.waitExit(t, 10*time.Second)
	if _, err := os.Stat(marker2); err == nil {
		t.Fatal("a SIGTERMed mock wrote the graceful marker")
	}
}

// TestIgnoreSigtermNeedsSigkill: with --ignore-sigterm the mock survives
// SIGTERM (so a client's shutdown ladder must reach SIGKILL); --linger keeps
// it running after stdin closes.
func TestIgnoreSigtermNeedsSigkill(t *testing.T) {
	dir := t.TempDir()
	marker, addrFile := filepath.Join(dir, "marker"), filepath.Join(dir, "addr")
	c := startMock(t, "--mode", "stdio", "--fixtures", fixtures, "--ignore-sigterm", "--linger", "30s",
		"--marker", marker, "--addr", "127.0.0.1:0", "--addr-file", addrFile)
	waitFile(t, addrFile) // SIGTERM is ignored before the side port opens
	c.stdin.Close()
	_ = c.cmd.Process.Signal(syscall.SIGTERM)
	time.Sleep(500 * time.Millisecond)
	if !c.alive() {
		t.Fatal("the mock exited on SIGTERM despite --ignore-sigterm")
	}
	_ = c.cmd.Process.Kill()
	c.waitExit(t, 10*time.Second)
	if _, err := os.Stat(marker); err == nil {
		t.Fatal("a SIGKILLed mock wrote the graceful marker")
	}
}

// TestHTTPModeServesAndStopsOnSigterm: HTTP mode on port 0 reports its
// address, serves the endpoint and the control API, and exits on SIGTERM.
func TestHTTPModeServesAndStopsOnSigterm(t *testing.T) {
	addrFile := filepath.Join(t.TempDir(), "addr")
	c := startMock(t, "--mode", "http", "--addr", "127.0.0.1:0", "--addr-file", addrFile,
		"--era", "dual", "--fixtures", fixtures, "--auth", "bearer:tok-1")
	base := "http://" + waitFile(t, addrFile)
	req, _ := http.NewRequest(http.MethodPost, base+"/mcp",
		strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}`))
	req.Header.Set("Authorization", "Bearer tok-1")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 200 || resp.Header.Get("Mcp-Session-Id") == "" {
		t.Fatalf("initialize = %d, session %q", resp.StatusCode, resp.Header.Get("Mcp-Session-Id"))
	}
	resp, err = http.Get(base + "/__health")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if !strings.Contains(string(body), `"auth":"bearer"`) || strings.Contains(string(body), "tok-1") {
		t.Fatalf("/__health = %s", body)
	}
	_ = c.cmd.Process.Signal(syscall.SIGTERM)
	if err := c.waitExit(t, 10*time.Second); err != nil {
		t.Fatalf("exit on SIGTERM = %v, want 0", err)
	}
}

// TestHTTPModeSigtermRightAfterReady: a SIGTERM sent the moment --addr-file
// appears (the readiness signal lib_mcp.sh and the tests wait for) still
// shuts the mock down gracefully, exit 0, on every one of 20 starts: the
// signal handler is in place before the addr file is written.
func TestHTTPModeSigtermRightAfterReady(t *testing.T) {
	dir := t.TempDir()
	for i := 0; i < 20; i++ {
		addrFile := filepath.Join(dir, fmt.Sprintf("addr-%d", i))
		c := startMock(t, "--mode", "http", "--addr", "127.0.0.1:0", "--addr-file", addrFile, "--fixtures", fixtures)
		deadline := time.Now().Add(10 * time.Second)
		for {
			if data, err := os.ReadFile(addrFile); err == nil && len(data) > 0 {
				break
			}
			if time.Now().After(deadline) {
				t.Fatalf("start %d: %s never appeared", i, addrFile)
			}
			time.Sleep(time.Millisecond)
		}
		_ = c.cmd.Process.Signal(syscall.SIGTERM)
		if err := c.waitExit(t, 10*time.Second); err != nil {
			t.Fatalf("start %d: exit on a SIGTERM right after readiness = %v, want 0", i, err)
		}
	}
}

func TestParseFlagsRefusesBadCombinations(t *testing.T) {
	for _, args := range [][]string{
		{"--mode", "tcp", "--addr", ":1"},
		{"--mode", "http"},
		{"--mode", "http", "--addr", ":1", "--ignore-sigterm"},
	} {
		if _, err := parseFlags(args); err == nil {
			t.Fatalf("parseFlags(%v) accepted", args)
		}
	}
	if _, err := parseFlags([]string{"--mode", "stdio"}); err != nil {
		t.Fatalf("stdio without --addr: %v", err)
	}
}
