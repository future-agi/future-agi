package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// Explicit cloud gate builds the actual gateway, not a test-main stand-in.
// Run in the cached Redis image with --network none and g18-redis -> loopback.
func hostedProcessBinary(t *testing.T) string {
	t.Helper()
	binary := os.Getenv("G18_GATEWAY_BINARY")
	if binary == "" {
		t.Skip("cloud process gate supplies G18_GATEWAY_BINARY")
	}
	return binary
}

func processSecret(t *testing.T) string {
	t.Helper()
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		t.Fatal(err)
	}
	return hex.EncodeToString(b)
}

type hostedProcess struct {
	cmd              *exec.Cmd
	done             chan struct{}
	err              error
	out              bytes.Buffer
	url, path, admin string
	env              []string
}

func hostedProcessFixture(t *testing.T, control string, redisPort int, password string) (*hostedProcess, string) {
	t.Helper()
	p := &hostedProcess{done: make(chan struct{}), admin: processSecret(t)}
	port := freePort(t)
	p.url = fmt.Sprintf("http://127.0.0.1:%d", port)
	p.path = filepath.Join(t.TempDir(), "synthetic.yaml")
	text := fmt.Sprintf(`server:
  host: 127.0.0.1
  port: %d
  trusted_proxies: ["192.0.2.10/32"]
  default_request_timeout: 5s
  max_request_timeout: 10s
  shutdown_timeout: 3s
auth: {enabled: true}
guardrails: {enabled: true}
redis:
  enabled: true
  address: "g18-redis:%d"
  password: "%s"
  timeout: 500ms
control_plane:
  url: "%s"
  sync_on_startup: true
  sync_interval: 200ms
mcp: {enabled: false}
tools: {enabled: false}
`, port, redisPort, password, control)
	if err := os.WriteFile(p.path, []byte(text), 0600); err != nil {
		t.Fatal(err)
	}
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "AGENTCC_") {
			p.env = append(p.env, kv)
		}
	}
	p.env = append(p.env, "AGENTCC_DEPLOYMENT_PROFILE=hosted", "AGENTCC_ADMIN_TOKEN="+p.admin,
		"AGENTCC_INTERNAL_API_KEY="+processSecret(t), "AGENTCC_WEBHOOK_SECRET="+processSecret(t))
	return p, text
}

func (p *hostedProcess) start(t *testing.T, binary string, args ...string) {
	t.Helper()
	p.cmd = exec.Command(binary, append([]string{"-config", p.path}, args...)...)
	p.cmd.Env, p.cmd.Stdout, p.cmd.Stderr = p.env, &p.out, &p.out
	if err := p.cmd.Start(); err != nil {
		t.Fatal(err)
	}
	go func() { p.err = p.cmd.Wait(); close(p.done) }()
	t.Cleanup(func() {
		select {
		case <-p.done:
		default:
			_ = p.cmd.Process.Kill()
			<-p.done
		}
		for _, entry := range p.env {
			name, value, _ := strings.Cut(entry, "=")
			if strings.HasPrefix(name, "AGENTCC_") && (strings.HasSuffix(name, "_TOKEN") || strings.HasSuffix(name, "_KEY") || strings.HasSuffix(name, "_SECRET")) && value != "" && strings.Contains(p.out.String(), value) {
				t.Error("process disclosed a synthetic secret")
			}
		}
	})
}

func (p *hostedProcess) wait(t *testing.T, success bool) {
	t.Helper()
	select {
	case <-p.done:
		if (p.err == nil) != success {
			t.Fatalf("gateway exit success=%v, want %v", p.err == nil, success)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("gateway did not exit within budget")
	}
}

func processEventually(t *testing.T, fn func() bool) {
	t.Helper()
	for end := time.Now().Add(30 * time.Second); time.Now().Before(end); time.Sleep(20 * time.Millisecond) {
		if fn() {
			return
		}
	}
	t.Fatal("process condition not reached within budget")
}

func processStatus(url string) int {
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Get(url)
	if err != nil {
		return 0
	}
	defer response.Body.Close()
	io.Copy(io.Discard, response.Body)
	return response.StatusCode
}

func TestHostedProcessCheckConfigAndUnsafeStartAreNetworkFree(t *testing.T) {
	binary := hostedProcessBinary(t)
	var contacted atomic.Int32
	sink := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { contacted.Add(1); w.WriteHeader(503) }))
	defer sink.Close()
	for _, unsafe := range []bool{false, true} {
		p, _ := hostedProcessFixture(t, sink.URL, freePort(t), processSecret(t))
		if unsafe {
			p.env = append(p.env, "AGENTCC_AUTH_ENABLED=false")
			p.start(t, binary)
		} else {
			p.start(t, binary, "-check-config")
		}
		p.wait(t, !unsafe)
	}
	if contacted.Load() != 0 {
		t.Fatal("configuration-only or refused start contacted control plane")
	}
}

func TestHostedProcessSyncRedisReloadAndShutdown(t *testing.T) {
	binary := hostedProcessBinary(t)
	redisPort := freePort(t)
	password := processSecret(t)
	redisCommand := exec.Command("redis-server", "--bind", "127.0.0.1", "--port", fmt.Sprint(redisPort), "--requirepass", password, "--save", "", "--appendonly", "no", "--dir", t.TempDir())
	if err := redisCommand.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = redisCommand.Process.Kill(); _ = redisCommand.Wait() }()
	client := redis.NewClient(&redis.Options{Addr: fmt.Sprintf("127.0.0.1:%d", redisPort), Password: password, MaxRetries: -1})
	defer client.Close()
	processEventually(t, func() bool { return client.Ping(context.Background()).Err() == nil })
	var stage, keys, orgs atomic.Int32
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/agentcc/api-keys/bulk/":
			keys.Add(1)
			if stage.Load() == 0 {
				w.WriteHeader(503)
				fmt.Fprint(w, `{"status":false,"code":"migrations_pending"}`)
				return
			}
			if stage.Load() == 1 {
				w.WriteHeader(401)
				fmt.Fprint(w, `{"status":false}`)
				return
			}
			fmt.Fprint(w, `{"status":true,"result":[]}`)
		case "/agentcc/org-configs/bulk/":
			orgs.Add(1)
			if stage.Load() < 3 {
				w.WriteHeader(503)
				fmt.Fprint(w, `{"status":false}`)
				return
			}
			fmt.Fprint(w, `{"status":true,"result":{}}`)
		default:
			fmt.Fprint(w, `{"status":true,"result":{}}`)
		}
	}))
	defer app.Close()
	p, text := hostedProcessFixture(t, app.URL, redisPort, password)
	p.start(t, binary)
	processEventually(t, func() bool { return processStatus(p.url+"/livez") == 200 && keys.Load() > 0 && orgs.Load() > 0 })
	if processStatus(p.url+"/readyz") != 503 {
		t.Fatal("hosted migrations_pending accepted")
	}
	for next := int32(1); next <= 2; next++ {
		oldKeys := keys.Load()
		stage.Store(next)
		processEventually(t, func() bool { return keys.Load() > oldKeys })
		if processStatus(p.url+"/readyz") != 503 {
			t.Fatal("incomplete or unauthorized initial sync accepted")
		}
	}
	stage.Store(3)
	processEventually(t, func() bool { return processStatus(p.url+"/readyz") == 200 })
	failed, _ := hostedProcessFixture(t, app.URL, redisPort, password)
	if err := os.WriteFile(failed.path, []byte(text), 0600); err != nil {
		t.Fatal(err)
	}
	failed.start(t, binary)
	failed.wait(t, false)
	if processStatus(p.url+"/readyz") != 200 {
		t.Fatal("failed second listener disturbed the running process")
	}
	wrong := processSecret(t)
	if err := client.ConfigSet(context.Background(), "requirepass", wrong).Err(); err != nil {
		t.Fatal(err)
	}
	// Existing authenticated Redis connections remain authenticated; force a fresh
	// readiness connection by killing clients after the password change.
	controller := redis.NewClient(&redis.Options{Addr: fmt.Sprintf("127.0.0.1:%d", redisPort), Password: wrong, MaxRetries: -1})
	defer controller.Close()
	if err := controller.Do(context.Background(), "CLIENT", "KILL", "TYPE", "normal", "SKIPME", "yes").Err(); err != nil {
		t.Fatal(err)
	}
	processEventually(t, func() bool { return processStatus(p.url+"/readyz") == 503 })
	if err := controller.ConfigSet(context.Background(), "requirepass", password).Err(); err != nil {
		t.Fatal(err)
	}
	processEventually(t, func() bool { return processStatus(p.url+"/readyz") == 200 })
	if err := os.WriteFile(p.path, []byte(strings.Replace(text, "guardrails: {enabled: true}", "guardrails: {enabled: false}", 1)), 0600); err != nil {
		t.Fatal(err)
	}
	request, _ := http.NewRequest(http.MethodPost, p.url+"/-/reload", nil)
	request.Header.Set("Authorization", "Bearer "+p.admin)
	response, err := (&http.Client{Timeout: 3 * time.Second}).Do(request)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 400 || processStatus(p.url+"/readyz") != 200 {
		t.Fatal("unsafe reload did not preserve running configuration")
	}
	if err := p.cmd.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	p.wait(t, true)
	if processStatus(p.url+"/livez") != 0 {
		t.Fatal("listener survived shutdown")
	}
}
