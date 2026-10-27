package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
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

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Runs the actual main() child, including production plugin construction,
// control-plane bulk sync, ordinary BYOK authentication and org routing. The
// trusted operator supplies the local upstream address; the tenant supplies its
// own credential without a base_url override. No SSRF policy bypass is needed.
func TestManagedGuardrailsStartWithoutGlobalEnable(t *testing.T) {
	for _, disabledStatic := range []bool{false, true} {
		t.Run(fmt.Sprintf("disabled-static-rules=%t", disabledStatic), func(t *testing.T) {
			const orgID = "managed-guardrail-org"
			const gatewayKey = "sk-agentcc-managed-guardrail-fixture"
			const adminToken = "managed-admin-fixture"
			var providerCalls, wrongCredentials atomic.Int32
			provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
					w.WriteHeader(404)
					return
				}
				providerCalls.Add(1)
				if r.Header.Get("Authorization") != "Bearer tenant-provider-fixture" {
					wrongCredentials.Add(1)
					http.Error(w, "wrong provider identity", 401)
					return
				}
				var request models.ChatCompletionRequest
				if json.NewDecoder(r.Body).Decode(&request) != nil {
					http.Error(w, "invalid request", 400)
					return
				}
				text := "accepted answer"
				if len(request.Messages) > 0 && strings.Contains(string(request.Messages[len(request.Messages)-1].Content), "unsafe") {
					text = "PR334_PRIVATE /etc/passwd stack trace"
				}
				content, _ := json.Marshal(text)
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(models.ChatCompletionResponse{ID: "managed-candidate", Object: "chat.completion", Model: "gpt-4o", Choices: []models.Choice{{Index: 0, Message: models.Message{Role: "assistant", Content: content}, FinishReason: "stop"}}, Usage: &models.Usage{PromptTokens: 1, CompletionTokens: 1, TotalTokens: 2}})
			}))
			t.Cleanup(provider.Close)

			orgConfig := func(reflects bool) map[string]interface{} {
				checkConfig := map[string]interface{}{"stage": "post"}
				if reflects {
					checkConfig["on_block"] = "reflect"
					checkConfig["reflect_max_attempts"] = 1
					checkConfig["reflect_debug"] = true
				}
				return map[string]interface{}{
					"providers":  map[string]interface{}{"openai": map[string]interface{}{"enabled": true, "api_key": "tenant-provider-fixture", "models": []string{"gpt-4o"}}},
					"guardrails": map[string]interface{}{"checks": map[string]interface{}{"data-leakage-prevention": map[string]interface{}{"enabled": true, "action": "block", "confidence_threshold": 0.8, "config": checkConfig}}},
				}
			}
			var logMu sync.Mutex
			var logs []struct {
				RequestID          string            `json:"request_id"`
				StatusCode         int               `json:"status_code"`
				GuardrailTriggered bool              `json:"guardrail_triggered"`
				Metadata           map[string]string `json:"metadata"`
			}
			backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				switch r.URL.Path {
				case "/agentcc/org-configs/bulk/":
					json.NewEncoder(w).Encode(map[string]interface{}{"status": true, "result": map[string]interface{}{orgID: orgConfig(false)}})
				case "/agentcc/api-keys/bulk/":
					json.NewEncoder(w).Encode(map[string]interface{}{"status": true, "result": []interface{}{map[string]interface{}{"id": "synced-byok-key", "name": "ordinary-byok", "owner": orgID, "key_hash": auth.HashKey(gatewayKey), "models": []string{"gpt-4o"}, "providers": []string{"openai"}, "metadata": map[string]string{"org_id": orgID}}}})
				case "/agentcc/webhook/logs/":
					var payload struct {
						Logs []struct {
							RequestID          string            `json:"request_id"`
							StatusCode         int               `json:"status_code"`
							GuardrailTriggered bool              `json:"guardrail_triggered"`
							Metadata           map[string]string `json:"metadata"`
						} `json:"logs"`
					}
					if json.NewDecoder(r.Body).Decode(&payload) == nil {
						logMu.Lock()
						logs = append(logs, payload.Logs...)
						logMu.Unlock()
					}
					fmt.Fprint(w, `{"status":true}`)
				default:
					w.WriteHeader(404)
				}
			}))
			t.Cleanup(backend.Close)

			port := freePort(t)
			cfg := fmt.Sprintf(`server:
  host: 127.0.0.1
  port: %d
  shutdown_timeout: 2s
auth:
  enabled: true
admin:
  token: %s
providers:
  openai:
    base_url: %s
    api_key: operator-must-not-be-used
    api_format: openai
    models: [gpt-4o]
control_plane:
  url: %s
  admin_token: %s
  sync_on_startup: true
  sync_interval: 1h
cache:
  enabled: false
logging:
  request_logging:
    enabled: true
`, port, adminToken, provider.URL, backend.URL, adminToken)
			if disabledStatic {
				cfg += `guardrails:
  enabled: false
  rules:
    - name: keyword-blocklist
      stage: pre
      mode: sync
      action: block
      config:
        words: [safe]
`
			}
			cfgPath := filepath.Join(t.TempDir(), "gateway.yaml")
			if err := os.WriteFile(cfgPath, []byte(cfg), 0600); err != nil {
				t.Fatal(err)
			}
			var output bytes.Buffer
			cmd := exec.Command(os.Args[0], "-config", cfgPath)
			for _, kv := range os.Environ() {
				if !strings.HasPrefix(kv, "AGENTCC_") {
					cmd.Env = append(cmd.Env, kv)
				}
			}
			cmd.Env = append(cmd.Env, runGatewayEnv+"=1", "GORACE="+os.Getenv("GORACE")+" atexit_sleep_ms=0")
			cmd.Stdout, cmd.Stderr = &output, &output
			if err := cmd.Start(); err != nil {
				t.Fatal(err)
			}
			exited := make(chan struct{})
			exitErr := make(chan error, 1)
			go func() { exitErr <- cmd.Wait(); close(exited) }()
			t.Cleanup(func() {
				_ = cmd.Process.Kill()
				<-exited
				if t.Failed() {
					t.Logf("gateway output:\n%s", &output)
				}
			})
			base := fmt.Sprintf("http://127.0.0.1:%d", port)
			client := &http.Client{Timeout: 3 * time.Second}
			request := func(method, path, key string, body []byte) (int, http.Header, []byte, error) {
				r, err := http.NewRequest(method, base+path, bytes.NewReader(body))
				if err != nil {
					return 0, nil, nil, err
				}
				r.Header.Set("Authorization", "Bearer "+key)
				r.Header.Set("Content-Type", "application/json")
				resp, err := client.Do(r)
				if err != nil {
					return 0, nil, nil, err
				}
				defer resp.Body.Close()
				b, err := io.ReadAll(resp.Body)
				return resp.StatusCode, resp.Header, b, err
			}
			for deadline := time.Now().Add(15 * time.Second); ; time.Sleep(20 * time.Millisecond) {
				orgStatus, _, orgBody, orgErr := request("GET", "/-/orgs/"+orgID+"/config", adminToken, nil)
				keyStatus, _, keyBody, keyErr := request("GET", "/-/keys", adminToken, nil)
				if orgErr == nil && keyErr == nil && orgStatus == 200 && keyStatus == 200 && bytes.Contains(orgBody, []byte("data-leakage-prevention")) && bytes.Contains(keyBody, []byte(`"key_type":"byok"`)) {
					break
				}
				select {
				case <-exited:
					t.Fatalf("gateway exited before managed sync: %s", &output)
				default:
				}
				if time.Now().After(deadline) {
					t.Fatalf("managed config/key sync not ready: org=%d keys=%d", orgStatus, keyStatus)
				}
			}
			chat := func(text string) (int, http.Header, []byte) {
				b, _ := json.Marshal(map[string]interface{}{"model": "gpt-4o", "messages": []map[string]string{{"role": "user", "content": text}}})
				status, headers, body, err := request("POST", "/v1/chat/completions", gatewayKey, b)
				if err != nil {
					t.Fatal(err)
				}
				return status, headers, body
			}
			status, _, body := chat("safe")
			if status != 200 || !bytes.Contains(body, []byte("accepted answer")) {
				t.Fatalf("disabled static rules affected managed safe request: status=%d body=%s", status, body)
			}
			status, headers, body := chat("unsafe")
			if status != 403 || bytes.Contains(body, []byte("PR334_PRIVATE")) || providerCalls.Load() != 2 {
				t.Fatalf("managed DLP not enforced: status=%d calls=%d body=%s", status, providerCalls.Load(), body)
			}
			blockedRequestID := headers.Get("x-agentcc-request-id")
			updated, _ := json.Marshal(orgConfig(true))
			status, _, body, err := request("PUT", "/-/orgs/"+orgID+"/config", adminToken, updated)
			if err != nil || status != 200 {
				t.Fatalf("update managed reflection: status=%d error=%v body=%s", status, err, body)
			}
			status, headers, body = chat("unsafe")
			var repaired models.ChatCompletionResponse
			if json.Unmarshal(body, &repaired) != nil || status != 200 || headers.Get("x-agentcc-reflexion-attempts") != "1" || repaired.Usage == nil || repaired.Usage.TotalTokens != 4 || bytes.Contains(body, []byte("PR334_PRIVATE")) || providerCalls.Load() != 4 || wrongCredentials.Load() != 0 {
				t.Fatalf("managed BYOK reflection failed: status=%d calls=%d wrong-key=%d headers=%v body=%s", status, providerCalls.Load(), wrongCredentials.Load(), headers, body)
			}
			if err := cmd.Process.Signal(syscall.SIGTERM); err != nil {
				t.Fatal(err)
			}
			select {
			case <-exited:
			case <-time.After(5 * time.Second):
				t.Fatal("gateway did not flush and stop")
			}
			if err := <-exitErr; err != nil {
				t.Fatalf("gateway exited with %v", err)
			}
			logMu.Lock()
			defer logMu.Unlock()
			found := false
			for _, record := range logs {
				if record.RequestID == blockedRequestID && record.StatusCode == 403 && record.GuardrailTriggered && record.Metadata["key_type"] == "byok" {
					found = true
				}
			}
			if !found {
				t.Fatalf("missing blocked ordinary-BYOK log record %q: %+v", blockedRequestID, logs)
			}
		})
	}
}
