package main

import (
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
)

// An org's own ip_acl applies whether or not the global ip_acl is enabled.
// The gateway runs as built (its main, in a child process) and loads two
// orgs' settings from a fake control plane: org-remote allows only
// 10.0.0.0/8, org-local allows 127.0.0.0/8. The test calls from 127.0.0.1.
func TestOrgIPACLAppliesWithGlobalIPACLDisabled(t *testing.T) {
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"id":"x","object":"chat.completion","created":1,"model":"gpt-4o",`+
			`"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],`+
			`"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(provider.Close)

	var orgsServed atomic.Bool
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/agentcc/org-configs/bulk/":
			fmt.Fprint(w, `{"status":true,"result":{`+
				`"org-remote":{"ip_acl":{"enabled":true,"allow":["10.0.0.0/8"]}},`+
				`"org-local":{"ip_acl":{"enabled":true,"allow":["127.0.0.0/8"]}}}}`)
			orgsServed.Store(true)
		case "/agentcc/api-keys/bulk/":
			fmt.Fprint(w, `{"status":true,"result":[]}`)
		default:
			fmt.Fprint(w, `{}`)
		}
	}))
	t.Cleanup(backend.Close)

	port := freePort(t)
	cfgPath := filepath.Join(t.TempDir(), "gateway.yaml")
	cfg := fmt.Sprintf(`server:
  host: 127.0.0.1
  port: %d
providers:
  openai:
    base_url: %s
    api_key: fake
    api_format: openai
    models: [gpt-4o]
ip_acl:
  enabled: false
auth:
  enabled: true
  keys:
    - name: remote
      key: sk-org-remote-0001
      owner: test
      key_type: internal
      metadata:
        org_id: org-remote
    - name: local
      key: sk-org-local-0001
      owner: test
      key_type: internal
      metadata:
        org_id: org-local
control_plane:
  url: %s
  sync_on_startup: true
`, port, provider.URL, backend.URL)
	if err := os.WriteFile(cfgPath, []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}

	cmd := exec.Command(os.Args[0], "-config", cfgPath)
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "AGENTCC_") {
			cmd.Env = append(cmd.Env, kv)
		}
	}
	cmd.Env = append(cmd.Env, runGatewayEnv+"=1", "GORACE="+os.Getenv("GORACE")+" atexit_sleep_ms=0")
	out := &strings.Builder{}
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
			t.Logf("gateway output:\n%s", out.String())
		}
	})

	url := fmt.Sprintf("http://127.0.0.1:%d", port)
	chat := func(key string) (int, string) {
		req, _ := http.NewRequest("POST", url+"/v1/chat/completions",
			strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+key)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return 0, err.Error()
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, string(body)
	}

	deadline := time.Now().Add(15 * time.Second)
	for {
		if resp, err := http.Get(url + "/healthz"); err == nil {
			resp.Body.Close()
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("gateway did not start")
		}
		time.Sleep(20 * time.Millisecond)
	}
	for !orgsServed.Load() {
		if time.Now().After(deadline) {
			t.Fatal("gateway never fetched the org settings")
		}
		time.Sleep(20 * time.Millisecond)
	}

	// The org settings land in the store just after they are served.
	code, body := 0, ""
	for deadline = time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(50 * time.Millisecond) {
		if code, body = chat("sk-org-remote-0001"); code == http.StatusForbidden {
			break
		}
	}
	if code != http.StatusForbidden {
		t.Errorf("org-remote key from 127.0.0.1: got %d %s, want 403 from its org ip_acl", code, body)
	}
	if code, body = chat("sk-org-local-0001"); code != http.StatusOK {
		t.Errorf("org-local key from 127.0.0.1: got %d %s, want 200", code, body)
	}
}
