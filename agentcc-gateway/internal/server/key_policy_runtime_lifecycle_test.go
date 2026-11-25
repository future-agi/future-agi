package server

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// These are separate complete events: the violation exists only after joining
// both text deltas. Refusal happens before either event (or the upstream call).
func runtimeSplitEvents(rt guardedRoute) (string, string, string) {
	if rt.name == "responses" {
		return "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"runtime-\"}\n\n",
			"event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"sensitive\"}\n\n",
			"event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"id\":\"r\",\"model\":\"gpt-5.5\",\"status\":\"completed\",\"usage\":{\"input_tokens\":5,\"output_tokens\":3,\"total_tokens\":8}}}\n\n"
	}
	return "data: {\"id\":\"c\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-5.5\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"runtime-\"}}]}\n\n",
		"data: {\"id\":\"c\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-5.5\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"sensitive\"}}]}\n\n",
		"data: {\"id\":\"c\",\"object\":\"chat.completion.chunk\",\"model\":\"gpt-5.5\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":3,\"total_tokens\":8}}\n\ndata: [DONE]\n\n"
}

func TestKeyPolicyRuntimeSplitStreamHTTP(t *testing.T) {
	for _, rt := range []guardedRoute{guardedRoutes[0], guardedRoutes[3]} {
		for _, tc := range []struct {
			name, mode, key string
			want            int
		}{
			{"enforce", "enforce", "a", 403}, {"unselected", "enforce", "b", 200}, {"monitor", "monitor", "a", 200},
		} {
			t.Run(rt.name+"/"+tc.name, func(t *testing.T) {
				var calls atomic.Int32
				first, second, last := runtimeSplitEvents(rt)
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method != http.MethodPost {
						io.WriteString(w, `{"data":[]}`)
						return
					}
					calls.Add(1)
					w.Header().Set("Content-Type", "text/event-stream")
					for _, event := range []string{first, second, last} {
						io.WriteString(w, event)
						w.(http.Flusher).Flush()
					}
				}))
				defer upstream.Close()
				cfg := &tenant.GuardrailConfig{FailOpen: true, KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", tc.mode, "block", true, nil)}}
				srv, audit := runtimePolicyServer(t, upstream.URL, cfg, nil)
				w := runtimePolicySend(srv, rt, true, tc.key)
				if w.Code != tc.want {
					t.Fatalf("status=%d want=%d", w.Code, tc.want)
				}
				if tc.want == 403 {
					if calls.Load() != 0 || strings.Contains(w.Body.String(), "runtime-") || strings.Contains(w.Body.String(), "[DONE]") || strings.Contains(w.Body.String(), "response.completed") {
						t.Fatal("admission released a chunk or success terminal")
					}
					assertRuntimeAttribution(t, audit, "blocked", "enforce")
				} else {
					if calls.Load() != 1 || !strings.Contains(w.Body.String(), "runtime-") || !strings.Contains(w.Body.String(), "sensitive") {
						t.Fatal("unaffected stream lost split text")
					}
					if !strings.Contains(w.Body.String(), "[DONE]") && !strings.Contains(w.Body.String(), "response.completed") {
						t.Fatal("unaffected stream lost success terminal")
					}
					_, usage := audit.snapshot()
					if usage == nil || usage.TotalTokens != 8 {
						t.Fatalf("usage lost: %+v", usage)
					}
				}
			})
		}
	}
}

func TestKeyPolicyRuntimeUnselectedStreamTerminalAndCancellationHTTP(t *testing.T) {
	for _, rt := range []guardedRoute{guardedRoutes[0], guardedRoutes[3]} {
		for _, end := range []string{"terminal-error", "cancellation"} {
			t.Run(rt.name+"/"+end, func(t *testing.T) {
				var calls atomic.Int32
				upstreamClosed := make(chan struct{})
				first, second, _ := runtimeSplitEvents(rt)
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method != http.MethodPost {
						io.WriteString(w, `{"data":[]}`)
						return
					}
					calls.Add(1)
					defer close(upstreamClosed)
					io.Copy(io.Discard, r.Body)
					w.Header().Set("Content-Type", "text/event-stream")
					io.WriteString(w, first)
					w.(http.Flusher).Flush()
					if end == "cancellation" {
						select {
						case <-r.Context().Done():
						case <-time.After(5 * time.Second):
							t.Error("gateway failed to cancel upstream")
						}
						return
					}
					io.WriteString(w, second)
					if rt.name == "responses" {
						io.WriteString(w, "event: error\ndata: {\"type\":\"error\",\"code\":\"server_error\",\"message\":\"synthetic upstream failure\"}\n\n")
					} else {
						io.WriteString(w, "data: {\"error\":{\"type\":\"server_error\",\"code\":\"server_error\",\"message\":\"synthetic upstream failure\"}}\n\n")
					}
				}))
				defer upstream.Close()
				cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("runtime-post", "enforce", "block", true, nil)}}
				srv, audit := runtimePolicyServer(t, upstream.URL, cfg, nil)
				gateway := httptest.NewServer(srv.httpServer.Handler)
				defer gateway.Close()
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				req := rt.request(true, "synthetic clean prompt").WithContext(ctx)
				req.URL.Scheme = "http"
				req.URL.Host = strings.TrimPrefix(gateway.URL, "http://")
				req.RequestURI = ""
				req.Header.Set("Authorization", "Bearer sk-agentcc-runtime-b")
				response, err := gateway.Client().Do(req)
				if err != nil {
					t.Fatal(err)
				}
				defer response.Body.Close()
				if response.StatusCode != 200 {
					t.Fatalf("unselected admission status=%d", response.StatusCode)
				}
				var body strings.Builder
				if end == "cancellation" {
					reader := bufio.NewReader(response.Body)
					for {
						line, err := reader.ReadString('\n')
						body.WriteString(line)
						if strings.Contains(line, "runtime-") {
							break
						}
						if err != nil {
							t.Fatalf("missing first chunk: %v", err)
						}
					}
					cancel()
					response.Body.Close()
				} else {
					if _, err := io.Copy(&body, response.Body); err != nil {
						t.Fatal(err)
					}
					if !strings.Contains(body.String(), "error") {
						t.Fatal("terminal failure was not reported")
					}
				}
				select {
				case <-upstreamClosed:
				case <-time.After(5 * time.Second):
					t.Fatal("upstream did not close")
				}
				select {
				case <-audit.finalized:
				case <-time.After(5 * time.Second):
					t.Fatal("post accounting did not finalize")
				}
				results, usage := audit.snapshot()
				if calls.Load() != 1 || len(results) != 0 || usage == nil || usage.TotalTokens <= 0 {
					t.Fatalf("calls=%d attribution=%+v usage=%+v", calls.Load(), results, usage)
				}
				for _, terminal := range []string{"[DONE]", "response.completed", "\"finish_reason\":\"stop\""} {
					if strings.Contains(body.String(), terminal) {
						t.Fatalf("failed stream emitted success %s", terminal)
					}
				}
			})
		}
	}
}

func TestKeyPolicyRuntimeCleanPostAndValidExpressionHTTP(t *testing.T) {
	for _, rt := range guardedRoutes {
		for _, tc := range []struct {
			name   string
			stream bool
			cfg    map[string]interface{}
			want   int
		}{
			{"runtime-post", false, nil, 200},
			{"runtime-post", true, nil, 403},
			{"custom-risk", false, map[string]interface{}{"expression": "true", "message": "synthetic expression violation"}, 403},
			{"custom-risk", true, map[string]interface{}{"expression": "true", "message": "synthetic expression violation"}, 403},
		} {
			t.Run(fmt.Sprintf("%s/%s/stream=%v", rt.name, tc.name, tc.stream), func(t *testing.T) {
				up := &dialectUpstream{reply: "safe reply"}
				upstream := httptest.NewServer(up)
				defer upstream.Close()
				cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy(tc.name, "enforce", "block", true, tc.cfg)}}
				srv, audit := runtimePolicyServer(t, upstream.URL, cfg, nil)
				w := runtimePolicySend(srv, rt, tc.stream, "a")
				wantCalls := 1
				if tc.want == 403 {
					wantCalls = 0
				}
				if w.Code != tc.want || up.served() != wantCalls {
					t.Fatalf("status=%d calls=%d want=%d/%d", w.Code, up.served(), tc.want, wantCalls)
				}
				if tc.want == 403 {
					if tc.name == "custom-risk" {
						if strings.Contains(w.Body.String(), "unavailable") || strings.Contains(w.Body.String(), "stream_guardrail_unsupported") {
							t.Fatal("valid pre expression classified unavailable/unsupported")
						}
					} else if !strings.Contains(w.Body.String(), "stream_guardrail_unsupported") {
						t.Fatal("even a clean stream requires explicit unsupported admission")
					}
					assertRuntimeAttribution(t, audit, "blocked", "enforce")
				}
			})
		}
	}
}
