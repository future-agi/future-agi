package main

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
)

// A replica takes traffic once it reports ready: Helm's readiness probe and
// GKE's load-balancer health check read /readyz. Until its first key sync
// from the control plane has loaded the keys made in the app, a replica holds
// only its config.yaml keys and answers every app key 401 invalid_api_key.
// So when a control plane syncs keys, /readyz answers 503 until that first
// sync has loaded them, while /healthz and /livez (liveness) answer 200 from
// the start. A gateway that runs no key sync is ready at once.

// syncedTestKey is a key made in the app: the gateway learns it only from
// the control plane's key list.
const syncedTestKey = "sk-agentcc-readiness-synced-key-0123456789"

// readinessGateway is a gateway in a child process, as startGateway runs one,
// on the config text cfg, with %d for its port.
type readinessGateway struct {
	t   *testing.T
	url string
	out *bytes.Buffer
}

func startReadinessGateway(t *testing.T, cfg string) *readinessGateway {
	t.Helper()
	g := &readinessGateway{t: t, out: &bytes.Buffer{}}
	port := freePort(t)
	cfgPath := filepath.Join(t.TempDir(), "gateway.yaml")
	if err := os.WriteFile(cfgPath, []byte(fmt.Sprintf(cfg, port)), 0o600); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(os.Args[0], "-config", cfgPath)
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "AGENTCC_") { // they would override the config
			cmd.Env = append(cmd.Env, kv)
		}
	}
	cmd.Env = append(cmd.Env, runGatewayEnv+"=1", "GORACE="+os.Getenv("GORACE")+" atexit_sleep_ms=0")
	cmd.Stdout, cmd.Stderr = g.out, g.out // one writer: exec copies both streams in one goroutine
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
			t.Logf("gateway output:\n%s", g.out)
		}
	})
	g.url = fmt.Sprintf("http://127.0.0.1:%d", port)
	for deadline := time.Now().Add(30 * time.Second); ; time.Sleep(10 * time.Millisecond) {
		if resp, err := http.Get(g.url + "/healthz"); err == nil {
			resp.Body.Close()
			return g
		}
		select {
		case <-exited:
			t.Fatal("gateway exited before it served")
		default:
		}
		if time.Now().After(deadline) {
			t.Fatal("gateway did not start")
		}
	}
}

// get returns the status of GET path.
func (g *readinessGateway) get(path string) int {
	g.t.Helper()
	resp, err := http.Get(g.url + path)
	if err != nil {
		g.t.Fatalf("GET %s: %v", path, err)
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	return resp.StatusCode
}

// chatAs sends a chat request with key and returns its status and body.
func (g *readinessGateway) chatAs(key string) (int, string) {
	g.t.Helper()
	req, _ := http.NewRequest(http.MethodPost, g.url+"/v1/chat/completions",
		strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+key)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		g.t.Fatalf("chat: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(body)
}

// startChatProvider is an OpenAI-format upstream that answers every chat
// request.
func startChatProvider(t *testing.T) *httptest.Server {
	t.Helper()
	p := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o",`+
			`"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],`+
			`"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(p.Close)
	return p
}

// startStartingApp stands in for the app while it starts: its first key list
// answers 503, as an app still running migrations does, and every later one
// lists syncedTestKey, which belongs to org "org-r". The org's config gives it
// the gateway's openai provider with a key of its own.
func startStartingApp(t *testing.T, keyListCalls *atomic.Int32) *httptest.Server {
	t.Helper()
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/agentcc/api-keys/bulk/":
			if keyListCalls.Add(1) == 1 {
				w.WriteHeader(http.StatusServiceUnavailable)
				fmt.Fprint(w, `{"status":false,"result":"starting"}`)
				return
			}
			fmt.Fprintf(w, `{"status":true,"result":[{"id":"key-synced","name":"synced","key_hash":%q,"metadata":{"org_id":"org-r"}}]}`,
				auth.HashKey(syncedTestKey))
		case "/agentcc/org-configs/bulk/":
			fmt.Fprint(w, `{"status":true,"result":{"org-r":{"providers":{"openai":{"enabled":true,"api_key":"org-key","models":["gpt-4o"]}}}}}`)
		default:
			fmt.Fprint(w, `{}`)
		}
	}))
	t.Cleanup(app.Close)
	return app
}

// A fresh replica whose control plane is still starting reports not ready
// until its first key sync has loaded the app's keys, and from the moment it
// reports ready an app key is accepted: polled from start-up, /readyz never
// answers 200 while that key would get 401. Liveness answers 200 throughout.
func TestReadiness_WaitsForTheFirstKeySync(t *testing.T) {
	provider := startChatProvider(t)
	var keyListCalls atomic.Int32
	app := startStartingApp(t, &keyListCalls)
	g := startReadinessGateway(t, `server:
  host: 127.0.0.1
  port: %d
providers:
  openai:
    base_url: `+provider.URL+`
    api_key: fake
    api_format: openai
    models: [gpt-4o]
auth:
  enabled: true
control_plane:
  url: `+app.URL+`
  sync_on_startup: true
admin:
  token: `+testAdminToken+`
`)

	notReady := 0
	for deadline := time.Now().Add(30 * time.Second); ; time.Sleep(20 * time.Millisecond) {
		if time.Now().After(deadline) {
			t.Fatalf("/readyz still answers 503 after 30 s; the key list was asked for %d time(s)", keyListCalls.Load())
		}
		code := g.get("/readyz")
		if code == http.StatusServiceUnavailable {
			notReady++
			if live, health := g.get("/livez"), g.get("/healthz"); live != http.StatusOK || health != http.StatusOK {
				t.Fatalf("while not ready, /livez answered %d and /healthz %d; liveness must not wait for the key sync", live, health)
			}
			continue
		}
		if code != http.StatusOK {
			t.Fatalf("/readyz answered %d", code)
		}
		// The replica reports ready: an app key must be accepted from now on.
		if status, body := g.chatAs(syncedTestKey); status == http.StatusUnauthorized {
			t.Fatalf("/readyz answered 200 (after %d answer(s) of 503, with the key list asked for %d time(s)), and the app's key then got 401: %s",
				notReady, keyListCalls.Load(), body)
		}
		break
	}
	if notReady == 0 {
		t.Errorf("/readyz never answered 503 while the first key list failed (asked for %d time(s))", keyListCalls.Load())
	}
	if n := keyListCalls.Load(); n < 2 {
		t.Errorf("the replica reported ready after %d key list request(s); the first one failed", n)
	}
	if status, body := g.chatAs(syncedTestKey); status != http.StatusOK {
		t.Errorf("once ready, the app's key got %d: %s", status, body)
	}
	t.Logf("/readyz answered 503 %d time(s) before 200; key list requests: %d", notReady, keyListCalls.Load())
}

// A gateway that runs no key sync from a control plane is ready at once:
// with no control plane, or with one that only takes its request logs
// (sync_on_startup off and no sync_interval, as the scenario configs run),
// nothing would ever end a wait.
func TestReadiness_IsImmediateWithoutAKeySync(t *testing.T) {
	provider := startChatProvider(t)
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{}`)
	}))
	t.Cleanup(app.Close)
	base := `server:
  host: 127.0.0.1
  port: %d
providers:
  openai:
    base_url: ` + provider.URL + `
    api_key: fake
    api_format: openai
    models: [gpt-4o]
auth:
  enabled: true
  keys:
    - name: ops
      key: sk-agentcc-readiness-config-key-0123456789
      key_type: internal
admin:
  token: ` + testAdminToken + `
`
	for name, cfg := range map[string]string{
		"no control plane": base,
		"a control plane that syncs no keys": base + `control_plane:
  url: ` + app.URL + `
  sync_on_startup: false
  sync_interval: 0s
`,
	} {
		t.Run(name, func(t *testing.T) {
			g := startReadinessGateway(t, cfg)
			if code := g.get("/readyz"); code != http.StatusOK {
				t.Fatalf("/readyz answered %d as soon as the gateway served; want 200", code)
			}
			if status, body := g.chatAs("sk-agentcc-readiness-config-key-0123456789"); status != http.StatusOK {
				t.Errorf("the config file's key got %d: %s", status, body)
			}
		})
	}
}
