package server

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/plugins/logging"
)

// Exercise the HTTP provider adapter, stream relay, logging plugin and webhook
// encoder together; decoding a chunk alone cannot prove what request logs store.
func TestStreamingTokenSplitReachesRequestLogs(t *testing.T) {
	for _, tc := range []struct {
		name, model, usage string
		anthropic          bool
		want               models.Usage
	}{
		{"aliases with total", "gpt-4o", `{"input_tokens":120,"output_tokens":180,"total_tokens":300}`, false, models.Usage{PromptTokens: 120, CompletionTokens: 180, TotalTokens: 300}},
		{"aliases without total", "gpt-4o", `{"input_tokens":120,"output_tokens":180}`, false, models.Usage{PromptTokens: 120, CompletionTokens: 180, TotalTokens: 300}},
		{"canonical zero wins", "gpt-4o", `{"prompt_tokens":0,"completion_tokens":0,"input_tokens":120,"output_tokens":180,"total_tokens":300}`, false, models.Usage{TotalTokens: 300}},
		{"canonical usage unchanged", "gpt-4o", `{"prompt_tokens":120,"completion_tokens":180,"total_tokens":300}`, false, models.Usage{PromptTokens: 120, CompletionTokens: 180, TotalTokens: 300}},
		{"deferred anthropic input", "claude-sonnet-5-5", `{"input_tokens":120,"output_tokens":180}`, true, models.Usage{PromptTokens: 120, CompletionTokens: 180, TotalTokens: 300}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					io.WriteString(w, `{"data":[]}`)
					return
				}
				w.Header().Set("Content-Type", "text/event-stream")
				if tc.anthropic {
					io.WriteString(w, "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_test\",\"model\":\""+tc.model+"\",\"usage\":{\"input_tokens\":0,\"output_tokens\":0}}}\n\n")
					io.WriteString(w, "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"hello\"}}\n\n")
					io.WriteString(w, "event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":"+tc.usage+"}\n\nevent: message_stop\ndata: {\"type\":\"message_stop\"}\n\n")
				} else {
					io.WriteString(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"model\":\""+tc.model+"\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hello\"},\"finish_reason\":\"stop\"}]}\n\n")
					io.WriteString(w, "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"model\":\""+tc.model+"\",\"choices\":[],\"usage\":"+tc.usage+"}\n\ndata: [DONE]\n\n")
				}
			}))
			t.Cleanup(upstream.Close)

			logs := make(chan json.RawMessage, 1)
			webhook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var batch struct {
					Logs []json.RawMessage `json:"logs"`
				}
				if err := json.NewDecoder(r.Body).Decode(&batch); err != nil || len(batch.Logs) != 1 {
					t.Error("expected one valid request log")
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				logs <- batch.Logs[0]
				w.WriteHeader(http.StatusOK)
			}))
			t.Cleanup(webhook.Close)
			plugin := logging.New(config.RequestLoggingConfig{Enabled: true}, nil)
			plugin.SetFlusher(logging.NewLogFlusher(webhook.URL, "", time.Hour, 10))
			t.Cleanup(plugin.Close)
			gateway := streamingGateway(t, upstream.URL, plugin)
			route := streamRoute{"token split", "/v1/chat/completions", `{"model":"` + tc.model + `","stream":true,"stream_options":{"include_usage":true},"messages":[{"role":"user","content":"hi"}]}`}
			resp, body := postStream(t, gateway, route, nil)
			if resp.StatusCode != http.StatusOK || !strings.Contains(body, "[DONE]") || !strings.Contains(body, "hello") {
				t.Fatalf("stream failed: status=%d, completed=%v, content=%v", resp.StatusCode, strings.Contains(body, "[DONE]"), strings.Contains(body, "hello"))
			}
			plugin.Close()
			select {
			case raw := <-logs:
				var got models.Usage
				if err := json.Unmarshal(raw, &got); err != nil {
					t.Fatal(err)
				}
				var status struct {
					IsStream   bool `json:"is_stream"`
					StatusCode int  `json:"status_code"`
				}
				if err := json.Unmarshal(raw, &status); err != nil {
					t.Fatal(err)
				}
				if !status.IsStream || status.StatusCode != http.StatusOK {
					t.Fatalf("log status = %+v", status)
				}
				if got.PromptTokens != tc.want.PromptTokens || got.CompletionTokens != tc.want.CompletionTokens || got.TotalTokens != tc.want.TotalTokens {
					t.Fatalf("logged tokens = %d/%d/%d, want %d/%d/%d", got.PromptTokens, got.CompletionTokens, got.TotalTokens, tc.want.PromptTokens, tc.want.CompletionTokens, tc.want.TotalTokens)
				}
				t.Logf("stream completed; logged tokens = %d/%d/%d", got.PromptTokens, got.CompletionTokens, got.TotalTokens)
			case <-time.After(2 * time.Second):
				t.Fatal("request log was not delivered")
			}
		})
	}
}
