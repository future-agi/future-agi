package server

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGeminiToolPolicyHTTP(t *testing.T) {
	const common = `"model":"gemini-3.7-flash","messages":[{"role":"user","content":"hello"}]`
	const tool = `"type":"function","function":{"name":"lookup","parameters":{"type":"object"}}`
	const parallel = `,"tools":[{` + tool + `}],"parallel_tool_calls":false`
	const callers = `,"tools":[{` + tool + `,"allowed_callers":["code_execution_20250825"]}]`
	const direct = `,"tools":[{` + tool + `,"allowed_callers":["direct"]}],"parallel_tool_calls":true`
	const anthropic = `,"max_tokens":32,"tools":[{"name":"lookup","input_schema":{"type":"object"}}],"tool_choice":{"type":"auto","disable_parallel_tool_use":true}`
	for _, tc := range []struct{ name, path, settings, drop string }{
		{"parallel", "/v1/chat/completions", parallel, "parallel_tool_calls_unsupported"},
		{"callers", "/v1/chat/completions", callers, "allowed_callers_unsupported"},
		{"direct", "/v1/chat/completions", direct, ""},
		{"anthropic", "/v1/messages", anthropic, "parallel_tool_calls"},
	} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", tc.name, stream), func(t *testing.T) {
				up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					const response = `{"candidates":[{"content":{"parts":[{"text":"from Gemini"}]},"finishReason":"STOP"}]}`
					if stream {
						w.Header().Set("Content-Type", "text/event-stream")
						fmt.Fprintf(w, "data: %s\n\n", response)
					} else {
						w.Header().Set("Content-Type", "application/json")
						io.WriteString(w, response)
					}
				}))
				defer up.Close()
				logs, wantLog := responseDropsLogger(t)
				srv := streamingServer(t, up.URL, logs)
				body := fmt.Sprintf(`{%s%s,"stream":%t}`, common, tc.settings, stream)
				req := httptest.NewRequest(http.MethodPost, tc.path, strings.NewReader(body))
				req.Header.Set("Content-Type", "application/json")
				req.Header.Set("anthropic-version", "2023-06-01")
				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, req)
				got := w.Result().Header.Get("x-agentcc-translation-drops")
				t.Logf("HTTP %d; x-agentcc-translation-drops=%q", w.Code, got)
				t.Run("accept", func(t *testing.T) {
					if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "from Gemini") {
						t.Fatalf("status=%d body=%s, want successful Gemini completion", w.Code, w.Body.String())
					}
				})
				t.Run("report", func(t *testing.T) {
					if got != tc.drop {
						t.Errorf("wire header=%q, want %q", got, tc.drop)
					}
				})
				if w.Code == http.StatusOK {
					wantLog(tc.drop)
				}
			})
		}
	}
}
