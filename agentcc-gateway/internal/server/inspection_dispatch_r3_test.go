package server

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// This file intentionally uses only pre-existing APIs, so the exact tests can
// run against the pinned base as behavioral RED evidence. All HTTP is localhost
// and all keys/provider output are synthetic.
func inspectionR3HTTP(t *testing.T, gateway *httptest.Server, method, path, body string, headers map[string]string) (int, string) {
	t.Helper()
	req, err := http.NewRequest(method, gateway.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	for name, value := range headers {
		req.Header.Set(name, value)
	}
	resp, err := gateway.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, string(data)
}

func inspectionR3Headers(key string) map[string]string {
	return map[string]string{"Authorization": "Bearer sk-agentcc-runtime-" + key}
}

func TestInspectionR3ForgottenRouteHTTP(t *testing.T) {
	for _, stage := range []string{"pre", "post"} {
		for _, failOpen := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/fail_open=%v", stage, failOpen), func(t *testing.T) {
				name, check := "runtime-post", map[string]interface{}(nil)
				if stage == "pre" {
					name, check = "custom-pre", map[string]interface{}{"expression": "false"}
				}
				cfg := &tenant.GuardrailConfig{FailOpen: failOpen, KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy(name, "enforce", "block", true, check)}}
				up := httptest.NewServer(&dialectUpstream{reply: "unused"})
				defer up.Close()
				srv, _ := runtimePolicyServer(t, up.URL, cfg, nil)
				defer srv.asyncWorker.Stop()
				var calls atomic.Int32
				// A normal registration outside /v1, with no guardrail-specific code.
				srv.router.Handle("POST", "/new-vendor/forgotten", func(w http.ResponseWriter, r *http.Request) {
					calls.Add(1)
					fmt.Fprint(w, "uninspected-provider-content")
				})
				gateway := httptest.NewServer(srv.httpServer.Handler)
				defer gateway.Close()
				headers := inspectionR3Headers("a")
				headers["x-agentcc-metadata"] = `{"auth_key_id":"key_2","org_id":"org-other","inspection_complete":"true","guardrail_checked":"true"}`
				headers["x-agentcc-inspection-complete"] = "true"
				status, body := inspectionR3HTTP(t, gateway, "POST", "/new-vendor/forgotten", `{"inspection_complete":true}`, headers)
				code := "output_guardrail_unsupported"
				if stage == "pre" {
					code = "input_guardrail_unsupported"
				}
				if status != 403 || calls.Load() != 0 || !strings.Contains(body, code) || strings.Contains(body, "uninspected-provider-content") {
					t.Fatalf("status=%d handler_calls=%d code=%s body=%s", status, calls.Load(), code, body)
				}
			})
		}
	}
}

func TestInspectionR3ForgottenRouteControlsHTTP(t *testing.T) {
	for _, tc := range []struct{ name, mode, action, key string }{
		{"unselected", "enforce", "block", "b"},
		{"other-org", "enforce", "block", "c"},
		{"monitor", "monitor", "block", "a"},
		{"warn", "enforce", "warn", "a"},
		{"log", "enforce", "log", "a"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			up := httptest.NewServer(&dialectUpstream{reply: "unused"})
			defer up.Close()
			cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", tc.mode, tc.action, true, nil)}}
			srv, _ := runtimePolicyServer(t, up.URL, cfg, nil)
			defer srv.asyncWorker.Stop()
			var calls atomic.Int32
			srv.router.Handle("POST", "/new-vendor/forgotten", func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				fmt.Fprint(w, "control-content")
			})
			gateway := httptest.NewServer(srv.httpServer.Handler)
			defer gateway.Close()
			status, body := inspectionR3HTTP(t, gateway, "POST", "/new-vendor/forgotten", "{}", inspectionR3Headers(tc.key))
			if status != 200 || calls.Load() != 1 || body != "control-content" {
				t.Fatalf("status=%d calls=%d body=%s", status, calls.Load(), body)
			}
			// Unregistered URLs and methods keep normal router/auth behavior.
			status, _ = inspectionR3HTTP(t, gateway, "GET", "/unregistered", "", nil)
			if status != 404 {
				t.Fatalf("unregistered status=%d", status)
			}
			status, _ = inspectionR3HTTP(t, gateway, "GET", "/new-vendor/forgotten", "", inspectionR3Headers("a"))
			if status != 405 {
				t.Fatalf("wrong method status=%d", status)
			}
		})
	}
}

func TestInspectionR3CredentialPrecedenceHTTP(t *testing.T) {
	for _, tc := range []struct {
		name, path string
		headers    map[string]string
		status     int
	}{
		{"standard-selected-bearer", "/forgotten", map[string]string{"Authorization": "Bearer sk-agentcc-runtime-a", "x-api-key": "sk-agentcc-runtime-b"}, 403},
		{"standard-unselected-bearer", "/forgotten", map[string]string{"Authorization": "Bearer sk-agentcc-runtime-b", "x-api-key": "sk-agentcc-runtime-a"}, 200},
		{"standard-invalid-bearer", "/forgotten", map[string]string{"Authorization": "Bearer invalid", "x-api-key": "sk-agentcc-runtime-a"}, 401},
		{"genai-selected-query", "/v1beta/models/gemini-3.8-flash:countTokens?key=sk-agentcc-runtime-a", inspectionR3Headers("b"), 403},
		{"genai-selected-google-header", "/v1beta/models/gemini-3.8-flash:countTokens", map[string]string{"Authorization": "Bearer sk-agentcc-runtime-b", "x-goog-api-key": "sk-agentcc-runtime-a"}, 403},
		{"genai-invalid-query", "/v1beta/models/gemini-3.8-flash:countTokens?key=invalid", inspectionR3Headers("a"), 401},
		{"genai-qualified-selected", "/v1beta/models/gemini/gemini-3.8-flash:embedContent?key=sk-agentcc-runtime-a", inspectionR3Headers("b"), 403},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstream := &dialectUpstream{reply: "unused"}
			up := httptest.NewServer(upstream)
			defer up.Close()
			cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
			srv, _ := runtimePolicyServer(t, up.URL, cfg, nil)
			defer srv.asyncWorker.Stop()
			var calls atomic.Int32
			srv.router.Handle("POST", "/forgotten", func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				fmt.Fprint(w, "unselected content")
			})
			gateway := httptest.NewServer(srv.httpServer.Handler)
			defer gateway.Close()
			status, body := inspectionR3HTTP(t, gateway, "POST", tc.path, `{"contents":[{"parts":[{"text":"synthetic"}]}]}`, tc.headers)
			wantCalls := int32(0)
			if tc.status == 200 {
				wantCalls = 1
			}
			if status != tc.status || calls.Load() != wantCalls || upstream.served() != 0 {
				t.Fatalf("status=%d want=%d handler_calls=%d provider_calls=%d body=%s", status, tc.status, calls.Load(), upstream.served(), body)
			}
		})
	}
}

func TestInspectionR3GenAIUnselectedCredentialWinsHTTP(t *testing.T) {
	for _, mode := range []string{"query", "google-header", "qualified-query"} {
		t.Run(mode, func(t *testing.T) {
			var calls atomic.Int32
			up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == "POST" {
					calls.Add(1)
					fmt.Fprint(w, `{"totalTokens":1}`)
					return
				}
				fmt.Fprint(w, `{"data":[],"models":[]}`)
			}))
			defer up.Close()
			cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
			srv, _ := runtimePolicyServer(t, up.URL, cfg, nil)
			defer srv.asyncWorker.Stop()
			gateway := httptest.NewServer(srv.httpServer.Handler)
			defer gateway.Close()
			path := "/v1beta/models/gemini-3.8-flash:countTokens"
			headers := inspectionR3Headers("a") // lower-priority selected key
			switch mode {
			case "query":
				path += "?key=sk-agentcc-runtime-b"
			case "qualified-query":
				path = "/v1beta/models/gemini/gemini-3.8-flash:countTokens?key=sk-agentcc-runtime-b"
			case "google-header":
				headers["x-goog-api-key"] = "sk-agentcc-runtime-b"
			}
			status, body := inspectionR3HTTP(t, gateway, "POST", path, `{"contents":[{"parts":[{"text":"synthetic"}]}]}`, headers)
			if status != 200 || calls.Load() != 1 || !strings.Contains(body, `"totalTokens":1`) {
				t.Fatalf("status=%d provider_calls=%d body=%s", status, calls.Load(), body)
			}
		})
	}
}

func TestInspectionR3StoredCurrentReaderHTTP(t *testing.T) {
	upstream := &dialectUpstream{reply: "unused"}
	up := httptest.NewServer(upstream)
	defer up.Close()
	srv, _ := runtimePolicyServer(t, up.URL, &tenant.GuardrailConfig{}, nil)
	defer srv.asyncWorker.Stop()
	srv.TenantStore.Set("org-other", &tenant.OrgConfig{})
	// The original creator's output was persisted before the reader's policy.
	srv.handlers.responsesStore.Put("r3-stored", "org-runtime", []byte(`{"id":"r3-stored","output":"historical-sensitive-content","creator_key_id":"key_2"}`))
	gateway := httptest.NewServer(srv.httpServer.Handler)
	defer gateway.Close()
	for _, key := range []string{"a", "b"} {
		status, body := inspectionR3HTTP(t, gateway, "GET", "/v1/responses/r3-stored", "", inspectionR3Headers(key))
		if status != 200 || !strings.Contains(body, "historical-sensitive-content") {
			t.Fatalf("initial reader=%s status=%d body=%s", key, status, body)
		}
	}
	srv.TenantStore.Set("org-runtime", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}})
	status, body := inspectionR3HTTP(t, gateway, "GET", "/v1/responses/r3-stored", "", inspectionR3Headers("a"))
	if status != 403 || !strings.Contains(body, "output_guardrail_unsupported") || strings.Contains(body, "historical-sensitive-content") {
		t.Fatalf("selected current reader status=%d body=%s", status, body)
	}
	status, body = inspectionR3HTTP(t, gateway, "GET", "/v1/responses/r3-stored", "", inspectionR3Headers("b"))
	if status != 200 || !strings.Contains(body, "historical-sensitive-content") {
		t.Fatalf("unselected creator status=%d body=%s", status, body)
	}
	status, body = inspectionR3HTTP(t, gateway, "GET", "/v1/responses/r3-stored", "", inspectionR3Headers("c"))
	if status != 404 || strings.Contains(body, "historical-sensitive-content") || upstream.served() != 0 {
		t.Fatalf("cross-org status=%d provider_calls=%d body=%s", status, upstream.served(), body)
	}
	// Removing the current policy takes effect without touching the stored body.
	srv.TenantStore.Set("org-runtime", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{}})
	status, body = inspectionR3HTTP(t, gateway, "GET", "/v1/responses/r3-stored", "", inspectionR3Headers("a"))
	if status != 200 || !strings.Contains(body, "historical-sensitive-content") {
		t.Fatalf("policy removed status=%d body=%s", status, body)
	}
}

func TestInspectionR3BackgroundSubmissionHTTP(t *testing.T) {
	upstream := &dialectUpstream{reply: "unused"}
	up := httptest.NewServer(upstream)
	defer up.Close()
	cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
	srv, _ := runtimePolicyServer(t, up.URL, cfg, nil)
	defer srv.asyncWorker.Stop()
	gateway := httptest.NewServer(srv.httpServer.Handler)
	defer gateway.Close()
	headers := inspectionR3Headers("a")
	headers["x-agentcc-async"] = "true"
	status, body := inspectionR3HTTP(t, gateway, "POST", "/v1/chat/completions", `{"model":"gpt-5.5","messages":[{"role":"user","content":"synthetic"}]}`, headers)
	if status != 403 || srv.handlers.asyncStore.Count() != 0 || upstream.served() != 0 || !strings.Contains(body, "output_guardrail_unsupported") {
		t.Fatalf("status=%d jobs=%d provider_calls=%d body=%s", status, srv.handlers.asyncStore.Count(), upstream.served(), body)
	}
}
