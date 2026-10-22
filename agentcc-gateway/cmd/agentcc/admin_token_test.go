package main

import (
	"bytes"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// runGatewayWithAdminToken starts this test binary as a gateway on a free port
// with the given admin token in its config and the given extra environment.
// It returns the process, its output and a channel closed when it exits.
func runGatewayWithAdminToken(t *testing.T, token string, env ...string) (*exec.Cmd, *bytes.Buffer, chan struct{}, string) {
	t.Helper()
	port := freePort(t)
	cfgPath := filepath.Join(t.TempDir(), "gateway.yaml")
	cfg := fmt.Sprintf("server:\n  host: 127.0.0.1\n  port: %d\nadmin:\n  token: %q\n", port, token)
	if err := os.WriteFile(cfgPath, []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(os.Args[0], "-config", cfgPath)
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "AGENTCC_") { // they would override the config
			cmd.Env = append(cmd.Env, kv)
		}
	}
	cmd.Env = append(cmd.Env, runGatewayEnv+"=1", "GORACE="+os.Getenv("GORACE")+" atexit_sleep_ms=0")
	cmd.Env = append(cmd.Env, env...)
	out := &bytes.Buffer{}
	cmd.Stdout, cmd.Stderr = out, out
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	exited := make(chan struct{})
	go func() {
		_ = cmd.Wait()
		close(exited)
	}()
	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		<-exited
		if t.Failed() {
			t.Logf("gateway output:\n%s", out)
		}
	})
	return cmd, out, exited, fmt.Sprintf("http://127.0.0.1:%d", port)
}

// A gateway configured with a well-known default admin token refuses to
// start unless the dev-only flag is set.
func TestStartup_RefusesAWellKnownAdminToken(t *testing.T) {
	const wellKnown = "agentcc-admin-secret"
	cmd, out, exited, url := runGatewayWithAdminToken(t, wellKnown)

	select {
	case <-exited:
	case <-time.After(15 * time.Second):
		if resp, err := http.Get(url + "/healthz"); err == nil {
			resp.Body.Close()
			t.Fatalf("the gateway started and serves with admin token %q; want it to refuse to start", wellKnown)
		}
		t.Fatal("the gateway neither exited nor served within 15s")
	}
	if code := cmd.ProcessState.ExitCode(); code != 1 {
		t.Errorf("exit code %d, want 1", code)
	}
	for _, want := range []string{"admin.token", "AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN"} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("startup error does not mention %q:\n%s", want, out)
		}
	}
	if strings.Contains(out.String(), wellKnown) {
		t.Errorf("startup output echoes the token:\n%s", out)
	}
}

// With the dev-only flag the same gateway starts and serves, so a local
// stack can opt in to a well-known token.
func TestStartup_DevFlagAllowsAnInsecureAdminToken(t *testing.T) {
	_, out, exited, url := runGatewayWithAdminToken(t, "agentcc-admin-secret",
		"AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN=1")
	for deadline := time.Now().Add(15 * time.Second); ; time.Sleep(20 * time.Millisecond) {
		select {
		case <-exited:
			t.Fatalf("the gateway exited with the dev flag set:\n%s", out)
		default:
		}
		if resp, err := http.Get(url + "/healthz"); err == nil {
			resp.Body.Close()
			return
		}
		if time.Now().After(deadline) {
			t.Fatal("gateway did not start")
		}
	}
}
