package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
	"github.com/futureagi/agentcc-gateway/internal/plugins/logging"
)

// gpt-5.6 refuses function tools on /v1/chat/completions while it reasons, so
// a caller's own chat request with tools used to come back as OpenAI's 400.
// The gateway now carries it over the Responses API and answers in chat shape.

var gpt56Variants = []string{"gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"}

// chatToolBody is a chat request with one function tool. extra is spliced in
// as further top-level fields, e.g. `"reasoning_effort":"none",`.
func chatToolBody(model string, stream bool, extra string) string {
	return fmt.Sprintf(`{"model":%q,"stream":%t,%s
		"tools":[{"type":"function","function":{"name":"get_weather","description":"Weather for a city","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],
		"messages":[{"role":"user","content":"Weather in Paris?"}]}`, model, stream, extra)
}

func postBridgeChat(t *testing.T, srv *Server, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// chatToolCompletion is the part of a chat completion these tests check.
type chatToolCompletion struct {
	Object  string `json:"object"`
	Model   string `json:"model"`
	Choices []struct {
		Message struct {
			Role      string `json:"role"`
			ToolCalls []struct {
				ID       string `json:"id"`
				Type     string `json:"type"`
				Function struct {
					Name      string `json:"name"`
					Arguments string `json:"arguments"`
				} `json:"function"`
			} `json:"tool_calls"`
		} `json:"message"`
		FinishReason string `json:"finish_reason"`
	} `json:"choices"`
	Usage struct {
		PromptTokens     int `json:"prompt_tokens"`
		CompletionTokens int `json:"completion_tokens"`
		TotalTokens      int `json:"total_tokens"`
	} `json:"usage"`
}

func checkToolCompletion(t *testing.T, raw []byte) {
	t.Helper()
	var resp chatToolCompletion
	if err := json.Unmarshal(raw, &resp); err != nil {
		t.Fatalf("decoding %s: %v", raw, err)
	}
	if resp.Object != "chat.completion" {
		t.Errorf("object = %q, want chat.completion", resp.Object)
	}
	if len(resp.Choices) != 1 || len(resp.Choices[0].Message.ToolCalls) != 1 {
		t.Fatalf("want one choice with one tool call, got %s", raw)
	}
	call := resp.Choices[0].Message.ToolCalls[0]
	if call.ID == "" || call.Type != "function" || call.Function.Name != "get_weather" || call.Function.Arguments != `{"city":"Paris"}` {
		t.Errorf("tool call = %+v, want function get_weather {\"city\":\"Paris\"} with an id", call)
	}
	if got := resp.Choices[0].FinishReason; got != "tool_calls" {
		t.Errorf("finish_reason = %q, want tool_calls", got)
	}
	if resp.Usage.PromptTokens != 120 || resp.Usage.CompletionTokens != 40 || resp.Usage.TotalTokens != 160 {
		t.Errorf("usage = %+v, want 120/40/160", resp.Usage)
	}
}

// streamedToolCall is what a chat stream says about its tool call.
type streamedToolCall struct {
	args        string
	nameDeltas  int // tool-call deltas that carry a function name
	toolDeltas  int
	firstHasNam bool
	finish      string
	usage       *struct {
		PromptTokens     int `json:"prompt_tokens"`
		CompletionTokens int `json:"completion_tokens"`
		TotalTokens      int `json:"total_tokens"`
	}
	cost    *float64
	done    bool
	errored bool
}

func readToolStream(t *testing.T, body string) streamedToolCall {
	t.Helper()
	var out streamedToolCall
	for _, line := range strings.Split(body, "\n") {
		data, ok := strings.CutPrefix(line, "data: ")
		if !ok {
			continue
		}
		if data == "[DONE]" {
			out.done = true
			continue
		}
		var chunk struct {
			Object  string `json:"object"`
			Choices []struct {
				Delta struct {
					ToolCalls []struct {
						Function map[string]json.RawMessage `json:"function"`
					} `json:"tool_calls"`
				} `json:"delta"`
				FinishReason *string `json:"finish_reason"`
			} `json:"choices"`
			Usage           *json.RawMessage `json:"usage"`
			Error           *json.RawMessage `json:"error"`
			AgentccMetadata *struct {
				Cost float64 `json:"cost"`
			} `json:"agentcc_metadata"`
		}
		if err := json.Unmarshal([]byte(data), &chunk); err != nil {
			t.Fatalf("decoding chunk %s: %v", data, err)
		}
		if chunk.Error != nil {
			out.errored = true
			continue
		}
		if chunk.Object != "chat.completion.chunk" {
			t.Errorf("object = %q, want chat.completion.chunk: %s", chunk.Object, data)
		}
		for _, choice := range chunk.Choices {
			for _, tc := range choice.Delta.ToolCalls {
				_, named := tc.Function["name"]
				if out.toolDeltas == 0 {
					out.firstHasNam = named
				}
				out.toolDeltas++
				if named {
					out.nameDeltas++
				}
				var args string
				if raw, ok := tc.Function["arguments"]; ok {
					json.Unmarshal(raw, &args)
				}
				out.args += args
			}
			if choice.FinishReason != nil && *choice.FinishReason != "" {
				out.finish = *choice.FinishReason
			}
		}
		if chunk.Usage != nil {
			json.Unmarshal(*chunk.Usage, &out.usage)
		}
		if chunk.AgentccMetadata != nil {
			cost := chunk.AgentccMetadata.Cost
			out.cost = &cost
		}
	}
	return out
}

func checkToolStream(t *testing.T, w *httptest.ResponseRecorder) streamedToolCall {
	t.Helper()
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	got := readToolStream(t, w.Body.String())
	if !got.done || got.errored {
		t.Errorf("stream done=%v errored=%v, want [DONE] and no error:\n%s", got.done, got.errored, w.Body.String())
	}
	if got.args != `{"city":"Paris"}` {
		t.Errorf("streamed arguments = %q, want {\"city\":\"Paris\"}", got.args)
	}
	if got.toolDeltas < 2 || !got.firstHasNam || got.nameDeltas != 1 {
		t.Errorf("tool deltas = %d, named = %d (first named %v); want only the first to carry the name", got.toolDeltas, got.nameDeltas, got.firstHasNam)
	}
	if got.finish != "tool_calls" {
		t.Errorf("finish_reason = %q, want tool_calls", got.finish)
	}
	if got.usage == nil || got.usage.PromptTokens != 120 || got.usage.CompletionTokens != 40 || got.usage.TotalTokens != 160 {
		t.Errorf("usage = %+v, want 120/40/160", got.usage)
	}
	return got
}

func checkHits(t *testing.T, upstream *openAIUpstream, chat, responses int) {
	t.Helper()
	if got := upstream.count("/v1/chat/completions"); got != chat {
		t.Errorf("/v1/chat/completions hits = %d, want %d", got, chat)
	}
	if got := upstream.count("/v1/responses"); got != responses {
		t.Errorf("/v1/responses hits = %d, want %d", got, responses)
	}
}

func TestChatCompletionsCarriesGPT56ToolsOverResponses(t *testing.T) {
	for _, model := range gpt56Variants {
		t.Run(model, func(t *testing.T) {
			srv, upstream := newResponsesBridgeServer(t)
			w := postBridgeChat(t, srv, chatToolBody(model, false, ""), nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
			}
			checkToolCompletion(t, w.Body.Bytes())
			checkHits(t, upstream, 0, 1)

			var sent struct {
				Model string `json:"model"`
				Tools []struct {
					Type string `json:"type"`
					Name string `json:"name"`
				} `json:"tools"`
			}
			json.Unmarshal(upstream.body("/v1/responses"), &sent)
			if sent.Model != model || len(sent.Tools) != 1 || sent.Tools[0].Name != "get_weather" {
				t.Errorf("/v1/responses got %s, want %s with the get_weather tool", upstream.body("/v1/responses"), model)
			}
		})
	}
}

func TestChatCompletionsStreamsGPT56ToolsOverResponses(t *testing.T) {
	for _, model := range gpt56Variants {
		t.Run(model, func(t *testing.T) {
			srv, upstream := newResponsesBridgeServer(t)
			w := postBridgeChat(t, srv, chatToolBody(model, true, ""), nil)
			checkToolStream(t, w)
			checkHits(t, upstream, 0, 1)
		})
	}
}

// A caller who turned reasoning off gets tools on chat completions as asked.
func TestChatCompletionsKeepsReasoningEffortNoneOnChatCompletions(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			srv, upstream := newResponsesBridgeServer(t)
			w := postBridgeChat(t, srv, chatToolBody("gpt-5.6-terra", stream, `"reasoning_effort":"none",`), nil)
			if stream {
				checkToolStream(t, w)
			} else {
				if w.Code != http.StatusOK {
					t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
				}
				checkToolCompletion(t, w.Body.Bytes())
			}
			checkHits(t, upstream, 1, 0)
			if !bytes.Contains(upstream.body("/v1/chat/completions"), []byte(`"reasoning_effort":"none"`)) {
				t.Errorf("chat completions body lost reasoning_effort: %s", upstream.body("/v1/chat/completions"))
			}
		})
	}
}

// Models that take tools on chat completions keep that route.
func TestChatCompletionsKeepsGPT55ToolsOnChatCompletions(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			srv, upstream := newResponsesBridgeServer(t)
			w := postBridgeChat(t, srv, chatToolBody("gpt-5.5", stream, ""), nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
			}
			checkHits(t, upstream, 1, 0)
		})
	}
}

// An OpenAI-format upstream that is not the official API may not serve
// /v1/responses, so the request goes out as sent and its answer comes back.
func TestChatCompletionsLeavesOtherOpenAIHostsAlone(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{plain: true})
			w := postBridgeChat(t, srv, chatToolBody("gpt-5.6-terra", stream, ""), nil)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want the upstream's 400. Body: %s", w.Code, w.Body.String())
			}
			if !strings.Contains(w.Body.String(), "Function tools with reasoning_effort are not supported") {
				t.Errorf("body = %s, want the upstream's message", w.Body.String())
			}
			checkHits(t, upstream, 1, 0)
		})
	}
}

// A caller who asked for a JSON schema with tools gets the schema on the
// Responses API too.
func TestChatCompletionsCarriesResponseFormatOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)
	extra := `"response_format":{"type":"json_schema","json_schema":{"name":"weather","schema":{"type":"object","properties":{"city":{"type":"string"}}},"strict":true}},`
	w := postBridgeChat(t, srv, chatToolBody("gpt-5.6-terra", false, extra), nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	var sent struct {
		Text struct {
			Format map[string]any `json:"format"`
		} `json:"text"`
	}
	json.Unmarshal(upstream.body("/v1/responses"), &sent)
	f := sent.Text.Format
	if f["type"] != "json_schema" || f["name"] != "weather" || f["strict"] != true || f["schema"] == nil {
		t.Errorf("text.format = %v, want the caller's json_schema one level up", f)
	}
}

// A model fallback is decided again for the model it falls back to: gpt-5.6
// goes to the Responses API, and gpt-5.5 after it to chat completions.
func TestChatCompletionsFallsBackFromGPT56OverResponses(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{configure: func(cfg *config.Config) {
				// Failover, which model fallbacks run under, needs a router.
				cfg.Routing.DefaultStrategy = "least-latency"
				cfg.Routing.ModelFallbacks = map[string][]string{"gpt-5.6-terra": {"gpt-5.5"}}
				cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{http.StatusBadGateway}}
			}})
			upstream.failResponses = true
			w := postBridgeChat(t, srv, chatToolBody("gpt-5.6-terra", stream, ""), nil)
			if stream {
				checkToolStream(t, w)
			} else {
				if w.Code != http.StatusOK {
					t.Fatalf("status = %d, want 200 via the fallback. Body: %s", w.Code, w.Body.String())
				}
				checkToolCompletion(t, w.Body.Bytes())
			}
			if upstream.count("/v1/responses") < 1 {
				t.Errorf("gpt-5.6-terra never reached /v1/responses: %v", upstream.hits)
			}
			if upstream.count("/v1/chat/completions") != 1 {
				t.Errorf("/v1/chat/completions hits = %d, want 1 (the gpt-5.5 fallback)", upstream.count("/v1/chat/completions"))
			}
			var sent struct {
				Model string `json:"model"`
			}
			json.Unmarshal(upstream.body("/v1/chat/completions"), &sent)
			if sent.Model != "gpt-5.5" {
				t.Errorf("chat completions model = %q, want gpt-5.5", sent.Model)
			}
		})
	}
}

// An async job runs the same chat call, so it takes the same route.
func TestChatCompletionsAsyncJobCarriesGPT56ToolsOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)
	w := postBridgeChat(t, srv, chatToolBody("gpt-5.6-terra", false, ""), map[string]string{"x-agentcc-async": "true"})
	if w.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want 202. Body: %s", w.Code, w.Body.String())
	}
	var submitted struct {
		JobID string `json:"job_id"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &submitted); err != nil || submitted.JobID == "" {
		t.Fatalf("submit response: %s", w.Body.String())
	}

	var job struct {
		Status   string           `json:"status"`
		Response json.RawMessage  `json:"response"`
		Error    *json.RawMessage `json:"error"`
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		req := httptest.NewRequest(http.MethodGet, "/v1/async/"+submitted.JobID, nil)
		rec := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("job read: %d %s", rec.Code, rec.Body.String())
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &job); err != nil {
			t.Fatalf("decoding job %s: %v", rec.Body.String(), err)
		}
		if job.Status == "completed" || job.Status == "failed" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("job still %q after 5s", job.Status)
		}
		time.Sleep(10 * time.Millisecond)
	}
	if job.Status != "completed" {
		t.Fatalf("job status = %q (error %s), want completed", job.Status, string(derefRaw(job.Error)))
	}
	checkToolCompletion(t, job.Response)
	checkHits(t, upstream, 0, 1)
}

func derefRaw(raw *json.RawMessage) json.RawMessage {
	if raw == nil {
		return nil
	}
	return *raw
}

// loggedRow is the Request Logs row a webhook flusher delivers.
type loggedRow struct {
	Model            string  `json:"model"`
	PromptTokens     int     `json:"prompt_tokens"`
	CompletionTokens int     `json:"completion_tokens"`
	TotalTokens      int     `json:"total_tokens"`
	StatusCode       int     `json:"status_code"`
	IsStream         bool    `json:"is_stream"`
	Cost             float64 `json:"cost"`
}

// pricedBridgeCall makes one call on a bridge server with the cost and logging
// plugins, and returns the cost the caller saw and the row Request Logs got.
// A non-stream call's cost is the x-agentcc-cost header; a stream's is the
// agentcc_metadata on its last chunk, since its headers go out first.
func pricedBridgeCall(t *testing.T, body string, stream bool) (string, loggedRow, *openAIUpstream) {
	t.Helper()
	rows := make(chan loggedRow, 1)
	webhook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var batch struct {
			Logs []loggedRow `json:"logs"`
		}
		if err := json.NewDecoder(r.Body).Decode(&batch); err != nil || len(batch.Logs) != 1 {
			t.Error("expected one valid request log")
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		rows <- batch.Logs[0]
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(webhook.Close)
	logs := logging.New(config.RequestLoggingConfig{Enabled: true}, nil)
	logs.SetFlusher(logging.NewLogFlusher(webhook.URL, "", time.Hour, 10))
	t.Cleanup(logs.Close)
	modelDB := testModelDBPtr()
	cost := costplugin.New(true, func() *modeldb.ModelDB { return modelDB.Load() }, nil, nil)

	srv, upstream := newResponsesBridgeServerWith(t, bridgeServerOptions{plugins: []pipeline.Plugin{cost, logs}})
	w := postBridgeChat(t, srv, body, map[string]string{"x-agentcc-include-metadata": "true"})
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	var seen string
	if stream {
		got := checkToolStream(t, w)
		if got.cost == nil {
			t.Fatalf("stream carries no agentcc_metadata cost:\n%s", w.Body.String())
		}
		seen = fmt.Sprintf("%.6f", *got.cost)
	} else {
		checkToolCompletion(t, w.Body.Bytes())
		seen = w.Header().Get("x-agentcc-cost")
	}
	logs.Close()
	select {
	case row := <-rows:
		return seen, row, upstream
	case <-time.After(2 * time.Second):
		t.Fatal("request log was not delivered")
	}
	return "", loggedRow{}, nil
}

// A call carried over the Responses API costs and logs what the same call on
// chat completions does, given the same tokens.
func TestChatCompletionsOverResponsesCostsAndLogsLikeChat(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			bridged, bridgedRow, bridgedUp := pricedBridgeCall(t, chatToolBody("gpt-5.6-terra", stream, ""), stream)
			plain, plainRow, plainUp := pricedBridgeCall(t, chatToolBody("gpt-5.6-terra", stream, `"reasoning_effort":"none",`), stream)
			checkHits(t, bridgedUp, 0, 1)
			checkHits(t, plainUp, 1, 0)

			if bridged == "" || bridged == "0" || bridged == "0.000000" {
				t.Fatalf("rerouted call cost = %q, want a priced call", bridged)
			}
			if bridged != plain {
				t.Errorf("cost over Responses = %s, on chat completions = %s; want equal", bridged, plain)
			}
			want := loggedRow{Model: "gpt-5.6-terra", PromptTokens: 120, CompletionTokens: 40, TotalTokens: 160, StatusCode: http.StatusOK, IsStream: stream}
			for name, row := range map[string]loggedRow{"responses": bridgedRow, "chat": plainRow} {
				got := row
				got.Cost = 0
				if got != want {
					t.Errorf("%s log row = %+v, want %+v", name, got, want)
				}
			}
			if bridgedRow.Cost == 0 || bridgedRow.Cost != plainRow.Cost {
				t.Errorf("logged cost over Responses = %v, on chat completions = %v; want equal and priced", bridgedRow.Cost, plainRow.Cost)
			}
			t.Logf("cost %s; logged %+v", bridged, bridgedRow)
		})
	}
}
