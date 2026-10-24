package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A post-stage block withholds the answer on every surface that serves one:
// /v1/chat/completions, /v1/messages, /v1beta generateContent,
// /v1/completions and /v1/responses, through a provider that speaks the
// surface's own API (pass-through) or through translation, and for an answer
// a pre-plugin gave (as a cache hit). The client gets the guardrail's error
// in that surface's format, with the status the request log records, never
// the answer and never a 500. The fixture is newPostBlockGateway's.

// serveLeakOnOtherAPIs answers, with postBlockLeak, the upstream APIs other
// than chat completions that the surfaces call: OpenAI's legacy completions
// and responses, Anthropic's messages and Gemini's generateContent, as JSON
// or as each API's stream.
func serveLeakOnOtherAPIs(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Stream bool `json:"stream"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	text, _ := json.Marshal(postBlockLeak)
	path := r.URL.Path
	stream := req.Stream || strings.HasSuffix(path, ":streamGenerateContent")
	switch {
	case path == "/v1/completions":
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"cmpl-1","object":"text_completion","created":1760000000,"model":"gpt-4o","choices":[{"index":0,"text":%s,"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":9,"total_tokens":14}}`, text)
	case path == "/v1/messages" && !stream:
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"msg_1","type":"message","role":"assistant","model":%q,"content":[{"type":"text","text":%s}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":5,"output_tokens":9}}`, postBlockClaude, text)
	case path == "/v1/messages":
		writeSSE(w, []string{
			fmt.Sprintf("event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"type\":\"message\",\"role\":\"assistant\",\"model\":%q,\"content\":[],\"stop_reason\":null,\"stop_sequence\":null,\"usage\":{\"input_tokens\":5,\"output_tokens\":1}}}", postBlockClaude),
			"event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}",
			fmt.Sprintf("event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":%s}}", text),
			"event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}",
			"event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\",\"stop_sequence\":null},\"usage\":{\"output_tokens\":9}}",
			"event: message_stop\ndata: {\"type\":\"message_stop\"}",
		})
	case strings.HasPrefix(path, "/v1beta/models/") && !stream:
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"candidates":[{"content":{"role":"model","parts":[{"text":%s}]},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":9,"totalTokenCount":14},"modelVersion":%q}`, text, postBlockGemini)
	case strings.HasPrefix(path, "/v1beta/models/"):
		writeSSE(w, []string{
			fmt.Sprintf(`data: {"candidates":[{"content":{"role":"model","parts":[{"text":%s}]},"index":0}],"modelVersion":%q}`, text, postBlockGemini),
			fmt.Sprintf(`data: {"candidates":[{"content":{"role":"model","parts":[{"text":""}]},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":9,"totalTokenCount":14},"modelVersion":%q}`, postBlockGemini),
		})
	case path == "/v1/responses" && !stream:
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"resp_1","object":"response","created_at":1760000000,"status":"completed","model":"gpt-4o","output":[{"type":"message","id":"item_1","status":"completed","role":"assistant","content":[{"type":"output_text","text":%s,"annotations":[]}]}],"usage":{"input_tokens":5,"output_tokens":9,"total_tokens":14}}`, text)
	case path == "/v1/responses":
		writeSSE(w, []string{
			`event: response.created` + "\n" + `data: {"type":"response.created","sequence_number":0,"response":{"id":"resp_1","object":"response","status":"in_progress","model":"gpt-4o","output":[]}}`,
			fmt.Sprintf("event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"sequence_number\":1,\"item_id\":\"item_1\",\"output_index\":0,\"content_index\":0,\"delta\":%s}", text),
			`event: response.completed` + "\n" + `data: {"type":"response.completed","sequence_number":2,"response":{"id":"resp_1","object":"response","status":"completed","model":"gpt-4o","output":[],"usage":{"input_tokens":5,"output_tokens":9,"total_tokens":14}}}`,
		})
	default:
		http.NotFound(w, r)
	}
}

func writeSSE(w http.ResponseWriter, events []string) {
	w.Header().Set("Content-Type", "text/event-stream")
	flusher := w.(http.Flusher)
	for _, e := range events {
		fmt.Fprintf(w, "%s\n\n", e)
		flusher.Flush()
	}
}

// wireFormat is the error shape of the API a surface speaks.
type wireFormat int

const (
	openaiWire    wireFormat = iota // {"error":{"type","code","message"}}
	anthropicWire                   // {"type":"error","error":{"type","message"}}
	genaiWire                       // {"error":{"code","status","message"}}
)

// wireError is an error in any of the three formats; message is the one
// the format carries.
type wireError struct {
	Type  string `json:"type"`
	Error struct {
		Type    string `json:"type"`
		Code    any    `json:"code"`
		Status  string `json:"status"`
		Message string `json:"message"`
	} `json:"error"`
}

// assertWireError: data is an error in format f that names check.
func assertWireError(t *testing.T, f wireFormat, data, check string) {
	t.Helper()
	var e wireError
	if err := json.Unmarshal([]byte(data), &e); err != nil {
		t.Errorf("not an error in the surface's format: %v; %s", err, data)
		return
	}
	if !strings.Contains(e.Error.Message, check) {
		t.Errorf("error message %q does not name %s; %s", e.Error.Message, check, data)
	}
	switch f {
	case openaiWire:
		if e.Error.Type != "guardrail_error" || e.Error.Code != "content_blocked" {
			t.Errorf("error = %s, want guardrail_error content_blocked", data)
		}
	case anthropicWire:
		if e.Type != "error" || e.Error.Type != "permission_error" {
			t.Errorf("error = %s, want an Anthropic permission_error", data)
		}
	case genaiWire:
		if e.Error.Code != float64(http.StatusForbidden) || e.Error.Status != "PERMISSION_DENIED" {
			t.Errorf("error = %s, want a Google error, code 403 PERMISSION_DENIED", data)
		}
	}
}

func messagesBody(model string, stream bool) string {
	return fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":%q}]}`, model, stream, postBlockPrompt)
}

func genaiBody() string {
	return fmt.Sprintf(`{"contents":[{"role":"user","parts":[{"text":%q}]}]}`, postBlockPrompt)
}

func completionsBody(model string, stream bool) string {
	return fmt.Sprintf(`{"model":%q,"stream":%t,"prompt":%q}`, model, stream, postBlockPrompt)
}

func responsesBody(stream bool) string {
	return fmt.Sprintf(`{"model":"gpt-4o","stream":%t,"input":%q}`, stream, postBlockPrompt)
}

// Each surface, for a block by an org's check (data-leakage-prevention,
// which reads the answer text, so only where the gateway has it), by a rule
// in the config file (answerBlockRule, which refuses any answer, so also
// where the gateway holds only the answer's usage), and for an answer a
// pre-plugin gave: the guardrail's error, never the answer and never a 500.
func TestPostBlock_EverySurfaceAnswersTheGuardrailError(t *testing.T) {
	static := postBlockSetup{staticRule: answerBlockRule}
	org := postBlockSetup{orgCheck: true}
	preAnswered := postBlockSetup{staticRule: answerBlockRule, preAnswer: true}
	cases := []struct {
		name   string
		path   string
		body   string
		setup  postBlockSetup
		format wireFormat
		check  string
	}{
		{"messages, translated / org check", "/v1/messages", messagesBody("gpt-4o", false), org, anthropicWire, leakageCheck},
		{"messages, translated / config rule", "/v1/messages", messagesBody("gpt-4o", false), static, anthropicWire, answerBlockRule},
		{"messages, pass-through / config rule", "/v1/messages", messagesBody(postBlockClaude, false), static, anthropicWire, answerBlockRule},
		{"messages, translated / pre-answered", "/v1/messages", messagesBody("gpt-4o", false), preAnswered, anthropicWire, answerBlockRule},
		{"messages, pass-through / pre-answered", "/v1/messages", messagesBody(postBlockClaude, false), preAnswered, anthropicWire, answerBlockRule},
		{"messages stream, translated / pre-answered", "/v1/messages", messagesBody("gpt-4o", true), preAnswered, anthropicWire, answerBlockRule},
		{"messages stream, pass-through / pre-answered", "/v1/messages", messagesBody(postBlockClaude, true), preAnswered, anthropicWire, answerBlockRule},

		{"generateContent, translated / org check", "/v1beta/models/gpt-4o:generateContent", genaiBody(), org, genaiWire, leakageCheck},
		{"generateContent, translated / config rule", "/v1beta/models/gpt-4o:generateContent", genaiBody(), static, genaiWire, answerBlockRule},
		{"generateContent, pass-through / config rule", "/v1beta/models/" + postBlockGemini + ":generateContent", genaiBody(), static, genaiWire, answerBlockRule},
		{"generateContent, translated / pre-answered", "/v1beta/models/gpt-4o:generateContent", genaiBody(), preAnswered, genaiWire, answerBlockRule},
		{"generateContent, pass-through / pre-answered", "/v1beta/models/" + postBlockGemini + ":generateContent", genaiBody(), preAnswered, genaiWire, answerBlockRule},
		{"streamGenerateContent, translated / pre-answered", "/v1beta/models/gpt-4o:streamGenerateContent", genaiBody(), preAnswered, genaiWire, answerBlockRule},
		{"streamGenerateContent, pass-through / pre-answered", "/v1beta/models/" + postBlockGemini + ":streamGenerateContent", genaiBody(), preAnswered, genaiWire, answerBlockRule},

		{"completions, OpenAI-compatible / org check", "/v1/completions", completionsBody("gpt-4o", false), org, openaiWire, leakageCheck},
		{"completions, OpenAI-compatible / config rule", "/v1/completions", completionsBody("gpt-4o", false), static, openaiWire, answerBlockRule},
		{"completions, Anthropic-format / org check", "/v1/completions", completionsBody(postBlockClaude, false), org, openaiWire, leakageCheck},
		{"completions, Anthropic-format / config rule", "/v1/completions", completionsBody(postBlockClaude, false), static, openaiWire, answerBlockRule},
		{"completions stream / pre-answered", "/v1/completions", completionsBody("gpt-4o", true), preAnswered, openaiWire, answerBlockRule},
		{"completions stream / refused before the provider", "/v1/completions", completionsBody("gpt-4o", true), postBlockSetup{preRule: true}, openaiWire, promptBlockRule},

		{"responses / config rule", "/v1/responses", responsesBody(false), static, openaiWire, answerBlockRule},
		{"responses / pre-answered", "/v1/responses", responsesBody(false), preAnswered, openaiWire, answerBlockRule},
		{"responses stream / pre-answered", "/v1/responses", responsesBody(true), preAnswered, openaiWire, answerBlockRule},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, tc.setup)
			code, body := g.do(t, tc.path, tc.body)
			if strings.Contains(body, "DROP TABLE") {
				t.Errorf("the blocked answer was served: status %d, body %s", code, body)
			}
			if code != http.StatusForbidden {
				t.Errorf("status %d, want the guardrail's 403; body %s", code, body)
			}
			assertWireError(t, tc.format, strings.TrimSpace(body), tc.check)
			e := g.entry(t)
			if e["status_code"] != float64(code) {
				t.Errorf("the log records status %v, the client got %d", e["status_code"], code)
			}
			assertBlockLogged(t, e, tc.check)
		})
	}
}

// Control: with no check that fires, every surface serves its answer, logged
// with 200.
func TestPostBlock_EverySurfaceServesAPassingAnswer(t *testing.T) {
	for _, tc := range []struct{ name, path, body string }{
		{"messages, translated", "/v1/messages", messagesBody("gpt-4o", false)},
		{"messages, pass-through", "/v1/messages", messagesBody(postBlockClaude, false)},
		{"generateContent, translated", "/v1beta/models/gpt-4o:generateContent", genaiBody()},
		{"generateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":generateContent", genaiBody()},
		{"completions, OpenAI-compatible", "/v1/completions", completionsBody("gpt-4o", false)},
		{"completions, Anthropic-format", "/v1/completions", completionsBody(postBlockClaude, false)},
		{"responses", "/v1/responses", responsesBody(false)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{})
			code, body := g.do(t, tc.path, tc.body)
			if code != http.StatusOK || !strings.Contains(body, "DROP TABLE") {
				t.Fatalf("status %d, body %s; want 200 with the answer", code, body)
			}
			e := g.entry(t)
			if e["status_code"] != float64(http.StatusOK) || e["is_error"] != false {
				t.Errorf("log status_code %v is_error %v, want 200 and false", e["status_code"], e["is_error"])
			}
		})
	}
}

// An async job whose answer an output check withholds fails with the
// guardrail's error.
func TestPostBlock_AsyncJobFailsWithTheGuardrailError(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{staticRule: answerBlockRule})
	body := fmt.Sprintf(`{"model":"gpt-4o","messages":[{"role":"user","content":%q}]}`, postBlockPrompt)
	code, resp := g.doAs(t, postBlockOpsKey, "/v1/chat/completions", body, map[string]string{"x-agentcc-async": "true"})
	if code != http.StatusAccepted {
		t.Fatalf("async request: status %d, want 202; body %s", code, resp)
	}
	var accepted struct {
		JobID string `json:"job_id"`
	}
	if err := json.Unmarshal([]byte(resp), &accepted); err != nil || accepted.JobID == "" {
		t.Fatalf("no job id: %v; body %s", err, resp)
	}
	job := waitAsyncJob(t, g.srv, postBlockOpsKey, accepted.JobID)
	if strings.Contains(fmt.Sprint(job), "DROP TABLE") {
		t.Errorf("the job holds the blocked answer: %v", job)
	}
	if job["status"] != "failed" {
		t.Fatalf("job status %v, want failed; job %v", job["status"], job)
	}
	apiErr, _ := job["error"].(map[string]any)
	if apiErr == nil || apiErr["Code"] != "content_blocked" || apiErr["Status"] != float64(http.StatusForbidden) {
		t.Errorf("job error %v, want the guardrail's 403 content_blocked", job["error"])
	}
}

// A batch item whose answer an output check withholds is an error naming the
// guardrail. The gateway runs without key auth here and the submit (admin
// token) names no batch key, so the item runs with no organization.
func TestPostBlock_BatchItemFailsWithTheGuardrailError(t *testing.T) {
	upstream := startLeakUpstream(t)
	cfg := config.DefaultConfig()
	cfg.Admin.Token = "post-block-admin"
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: 2 * time.Second,
		Rules: []config.GuardrailRuleConfig{{Name: answerBlockRule, Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5}}}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	grRegistry := map[string]guardrails.Guardrail{answerBlockRule: answerBlocker{}}
	grEngine := guardrails.NewEngine(cfg.Guardrails, grRegistry)
	policies := policy.NewStore()
	engine := pipeline.NewEngine(guardrails.NewPlugin(grEngine, grRegistry, nil, policies, nil))
	srv := New(cfg, "", registry, engine, nil, grEngine, policies, nil, testModelDBPtr(), tenant.NewStore(), nil)
	srv.ready.Store(true)

	admin := func(method, path, body string) (int, map[string]any) {
		t.Helper()
		req := httptest.NewRequest(method, path, bytes.NewReader([]byte(body)))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+cfg.Admin.Token)
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		b, _ := io.ReadAll(w.Result().Body)
		var out map[string]any
		if err := json.Unmarshal(b, &out); err != nil {
			t.Fatalf("%s %s: status %d, body %s is not JSON: %v", method, path, w.Code, b, err)
		}
		return w.Code, out
	}
	code, submitted := admin(http.MethodPost, "/-/batches", fmt.Sprintf(
		`{"requests":[{"model":"gpt-4o","messages":[{"role":"user","content":%q}]}]}`, postBlockPrompt))
	if code != http.StatusAccepted {
		t.Fatalf("submit: status %d, want 202; body %v", code, submitted)
	}
	id, _ := submitted["batch_id"].(string)
	var batch map[string]any
	for deadline := time.Now().Add(5 * time.Second); ; {
		_, batch = admin(http.MethodGet, "/-/batches/"+id, "")
		if batch["status"] == "completed" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("batch %s still %v after 5s", id, batch["status"])
		}
		time.Sleep(10 * time.Millisecond)
	}
	if strings.Contains(fmt.Sprint(batch), "DROP TABLE") {
		t.Errorf("the batch holds the blocked answer: %v", batch)
	}
	results, _ := batch["results"].([]any)
	if len(results) != 1 {
		t.Fatalf("results %v, want one", batch["results"])
	}
	item, _ := results[0].(map[string]any)
	if item["status"] != "error" || !strings.Contains(fmt.Sprint(item["error"]), answerBlockRule) {
		t.Errorf("batch item %v, want an error naming %s", item, answerBlockRule)
	}
}

// sseFrame is one server-sent event: its event name (empty when the stream
// names none) and its data.
type sseFrame struct{ event, data string }

func sseFrames(body string) []sseFrame {
	var frames []sseFrame
	for _, block := range strings.Split(strings.ReplaceAll(body, "\r\n", "\n"), "\n\n") {
		var f sseFrame
		for _, line := range strings.Split(block, "\n") {
			if v, ok := strings.CutPrefix(line, "event: "); ok {
				f.event = v
			} else if v, ok := strings.CutPrefix(line, "data: "); ok {
				f.data = v
			}
		}
		if f.event != "" || f.data != "" {
			frames = append(frames, f)
		}
	}
	return frames
}

// isStreamError: f is an error event in format f's stream: an Anthropic or
// a Responses "error" event, or a data payload with an error object.
func isStreamError(fr sseFrame) bool {
	if fr.event == "error" {
		return true
	}
	var e struct {
		Error json.RawMessage `json:"error"`
	}
	return json.Unmarshal([]byte(fr.data), &e) == nil && len(e.Error) > 0 && string(e.Error) != "null"
}

// With the text already sent, an output check that blocks the answer at the
// end of a stream ends it with the guardrail's error event, in the surface's
// own format, as a chat stream does, and the log records the block; on
// /v1/completions the checks run before [DONE].
func TestPostBlockStream_EverySurfaceEndsWithTheGuardrailError(t *testing.T) {
	static := postBlockSetup{staticRule: answerBlockRule}
	cases := []struct {
		name   string
		path   string
		body   string
		setup  postBlockSetup
		format wireFormat
		check  string
	}{
		{"completions / org check, bodies kept", "/v1/completions", completionsBody("gpt-4o", true), postBlockSetup{orgCheck: true, capture: true}, openaiWire, leakageCheck},
		{"completions / config rule", "/v1/completions", completionsBody("gpt-4o", true), static, openaiWire, answerBlockRule},
		{"messages, pass-through / config rule", "/v1/messages", messagesBody(postBlockClaude, true), static, anthropicWire, answerBlockRule},
		{"messages, translated / config rule", "/v1/messages", messagesBody("gpt-4o", true), static, anthropicWire, answerBlockRule},
		{"streamGenerateContent, pass-through / config rule", "/v1beta/models/" + postBlockGemini + ":streamGenerateContent", genaiBody(), static, genaiWire, answerBlockRule},
		{"streamGenerateContent, translated / config rule", "/v1beta/models/gpt-4o:streamGenerateContent", genaiBody(), static, genaiWire, answerBlockRule},
		{"responses / config rule", "/v1/responses", responsesBody(true), static, openaiWire, answerBlockRule},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, tc.setup)
			code, body := g.do(t, tc.path, tc.body)
			if code != http.StatusOK {
				t.Fatalf("status %d, want the stream's 200; body %s", code, body)
			}
			frames := sseFrames(body)
			leak, errAt, errors := -1, -1, 0
			for i, f := range frames {
				if leak < 0 && strings.Contains(f.data, "DROP TABLE") {
					leak = i
				}
				if isStreamError(f) {
					errAt = i
					errors++
				}
			}
			if errors != 1 {
				t.Fatalf("the stream carries %d error events, want 1; body %s", errors, body)
			}
			if leak < 0 || leak > errAt {
				t.Errorf("want the text before the error event (text at %d, error at %d); body %s", leak, errAt, body)
			}
			errData := frames[errAt].data
			if tc.path == "/v1/responses" {
				// A Responses stream's error event: {"type":"error","code","message"}.
				var e struct{ Type, Code, Message string }
				if json.Unmarshal([]byte(errData), &e) != nil || e.Type != "error" || e.Code != "content_blocked" || !strings.Contains(e.Message, tc.check) {
					t.Errorf("error event %s, want a Responses error event, content_blocked naming %s", errData, tc.check)
				}
			} else {
				assertWireError(t, tc.format, errData, tc.check)
			}
			if tc.format == anthropicWire && frames[errAt].event != "error" {
				t.Errorf("the Anthropic error is sent as event %q, want error", frames[errAt].event)
			}
			if tc.path == "/v1/completions" {
				if last := frames[len(frames)-1].data; last != "[DONE]" || errAt != len(frames)-2 {
					t.Errorf("want the error event right before [DONE]; body %s", body)
				}
			}
			assertBlockLogged(t, g.entry(t), tc.check)
		})
	}
}

// Control: with no check that fires, a stream on each surface ends with no
// error event.
func TestPostBlockStream_EverySurfaceStreamsAPassingAnswer(t *testing.T) {
	for _, tc := range []struct{ name, path, body string }{
		{"completions", "/v1/completions", completionsBody("gpt-4o", true)},
		{"messages, pass-through", "/v1/messages", messagesBody(postBlockClaude, true)},
		{"messages, translated", "/v1/messages", messagesBody("gpt-4o", true)},
		{"streamGenerateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":streamGenerateContent", genaiBody()},
		{"streamGenerateContent, translated", "/v1beta/models/gpt-4o:streamGenerateContent", genaiBody()},
		{"responses", "/v1/responses", responsesBody(true)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{capture: true})
			code, body := g.do(t, tc.path, tc.body)
			if code != http.StatusOK || !strings.Contains(body, "DROP TABLE") {
				t.Fatalf("status %d, body %s; want 200 with the answer", code, body)
			}
			for _, f := range sseFrames(body) {
				if isStreamError(f) {
					t.Errorf("a passing answer's stream got an error event %+v; body %s", f, body)
				}
			}
			e := g.entry(t)
			if e["status_code"] != float64(http.StatusOK) || e["is_error"] != false {
				t.Errorf("log status_code %v is_error %v, want 200 and false", e["status_code"], e["is_error"])
			}
		})
	}
}
