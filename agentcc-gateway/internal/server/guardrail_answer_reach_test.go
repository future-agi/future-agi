package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// An output check reads the answer the client gets on every surface that
// serves one (chat, completions, /v1/messages, /v1beta generateContent and
// /v1/responses), and a block withholds it: through a provider whose own API
// the surface passes the request to (pass-through), and on a stream whether
// or not the request log keeps bodies. The checks here read the answer's text:
// data-leakage-prevention as a rule in the config file and as an
// organization's check, each in block mode.

// answerCheckShapes are the two shapes of the check: a rule in the config
// file and an organization's check, both reading the answer's text.
var answerCheckShapes = []struct {
	name  string
	setup postBlockSetup
}{
	{"config-file rule", postBlockSetup{staticRule: leakageCheck}},
	{"org check", postBlockSetup{orgCheck: true}},
}

type answerSurface struct {
	name   string
	path   string
	body   string
	format wireFormat
}

// answerSurfaces are the non-stream surfaces whose answer a check reads.
var answerSurfaces = []answerSurface{
	{"messages, pass-through", "/v1/messages", messagesBody(postBlockClaude, false), anthropicWire},
	{"generateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":generateContent", genaiBody(), genaiWire},
	{"responses", "/v1/responses", responsesBody(false), openaiWire},
}

// answerStreamSurfaces are the stream surfaces whose answer a check reads
// at the stream's end.
var answerStreamSurfaces = []answerSurface{
	{"messages stream, pass-through", "/v1/messages", messagesBody(postBlockClaude, true), anthropicWire},
	{"messages stream, translated", "/v1/messages", messagesBody("gpt-4o", true), anthropicWire},
	{"streamGenerateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":streamGenerateContent", genaiBody(), genaiWire},
	{"streamGenerateContent, translated", "/v1beta/models/gpt-4o:streamGenerateContent", genaiBody(), genaiWire},
	{"completions stream", "/v1/completions", completionsBody("gpt-4o", true), openaiWire},
	{"chat stream", "/v1/chat/completions", answerChatBody(true), openaiWire},
	{"responses stream", "/v1/responses", responsesBody(true), openaiWire},
}

func answerChatBody(stream bool) string {
	return fmt.Sprintf(`{"model":"gpt-4o","stream":%t,"messages":[{"role":"user","content":%q}]}`, stream, postBlockPrompt)
}

func TestAnswerCheck_EverySurfaceWithholdsABlockedAnswer(t *testing.T) {
	for _, shape := range answerCheckShapes {
		for _, tc := range answerSurfaces {
			t.Run(shape.name+"/"+tc.name, func(t *testing.T) {
				g := newPostBlockGateway(t, shape.setup)
				code, body := g.do(t, tc.path, tc.body)
				if strings.Contains(body, "DROP TABLE") {
					t.Errorf("the blocked answer was served: status %d, body %s", code, body)
				}
				if code != http.StatusForbidden {
					t.Errorf("status %d, want the guardrail's 403; body %s", code, body)
				}
				assertWireError(t, tc.format, strings.TrimSpace(body), leakageCheck)
				e := g.entry(t)
				if e["status_code"] != float64(code) {
					t.Errorf("the log records status %v, the client got %d", e["status_code"], code)
				}
				assertBlockLogged(t, e, leakageCheck)
			})
		}
	}
}

func TestAnswerCheckStream_EverySurfaceEndsWithTheGuardrailError(t *testing.T) {
	for _, shape := range answerCheckShapes {
		for _, bodies := range []bool{false, true} {
			for _, tc := range answerStreamSurfaces {
				name := shape.name + "/" + tc.name + "/bodies not logged"
				if bodies {
					name = shape.name + "/" + tc.name + "/bodies logged"
				}
				t.Run(name, func(t *testing.T) {
					setup := shape.setup
					setup.capture = bodies
					g := newPostBlockGateway(t, setup)
					code, body := g.do(t, tc.path, tc.body)
					assertStreamEndsWithBlock(t, tc, code, body)
					assertBlockLogged(t, g.entry(t), leakageCheck)
				})
			}
		}
	}
}

// assertStreamEndsWithBlock: the stream sent its text, then exactly one
// error event, the guardrail's, in the surface's own format.
func assertStreamEndsWithBlock(t *testing.T, tc answerSurface, code int, body string) {
	t.Helper()
	if code != http.StatusOK {
		t.Fatalf("status %d, want the stream's 200; body %s", code, body)
	}
	frames := sseFrames(body)
	leak, errAt, errs := -1, -1, 0
	for i, f := range frames {
		if leak < 0 && strings.Contains(f.data, "DROP TABLE") {
			leak = i
		}
		if isStreamError(f) {
			errAt = i
			errs++
		}
	}
	if errs != 1 {
		t.Fatalf("the stream carries %d error events, want 1; body %s", errs, body)
	}
	if leak < 0 || leak > errAt {
		t.Errorf("want the text before the error event (text at %d, error at %d); body %s", leak, errAt, body)
	}
	data := frames[errAt].data
	if tc.path == "/v1/responses" {
		// A Responses stream's error event: {"type":"error","code","message"}.
		var e struct{ Type, Code, Message string }
		if json.Unmarshal([]byte(data), &e) != nil || e.Type != "error" || e.Code != "content_blocked" || !strings.Contains(e.Message, leakageCheck) {
			t.Errorf("error event %s, want a Responses error event, content_blocked naming %s", data, leakageCheck)
		}
	} else {
		assertWireError(t, tc.format, data, leakageCheck)
	}
	if tc.format == anthropicWire && frames[errAt].event != "error" {
		t.Errorf("the Anthropic error is sent as event %q, want error", frames[errAt].event)
	}
	if tc.path == "/v1/completions" || tc.path == "/v1/chat/completions" {
		if last := frames[len(frames)-1].data; last != "[DONE]" {
			t.Errorf("the stream ends with %q, want [DONE]; body %s", last, body)
		}
	}
}

// Control: the same checks in warn mode read the answer, the answer is
// served with 200, and the log records the warning.
func TestAnswerCheck_EverySurfaceReadsTheAnswerInWarnMode(t *testing.T) {
	for _, shape := range answerCheckShapes {
		for _, tc := range append(append([]answerSurface{}, answerSurfaces...), answerStreamSurfaces...) {
			t.Run(shape.name+"/"+tc.name, func(t *testing.T) {
				setup := shape.setup
				setup.action = "warn"
				g := newPostBlockGateway(t, setup)
				code, body := g.do(t, tc.path, tc.body)
				if code != http.StatusOK || !strings.Contains(body, "DROP TABLE") {
					t.Fatalf("status %d, body %s; want 200 with the answer", code, body)
				}
				for _, f := range sseFrames(body) {
					if isStreamError(f) {
						t.Errorf("a warned answer's stream got an error event %+v; body %s", f, body)
					}
				}
				e := g.entry(t)
				if e["status_code"] != float64(http.StatusOK) || e["is_error"] != false {
					t.Errorf("log status_code %v is_error %v, want 200 and false", e["status_code"], e["is_error"])
				}
				meta, _ := e["metadata"].(map[string]any)
				if e["guardrail_triggered"] != true || meta["guardrail_action"] != "warned" || meta["guardrail_name"] != leakageCheck {
					t.Errorf("log guardrail_triggered %v, action %v, name %v; want the check's warning on the answer it read",
						e["guardrail_triggered"], meta["guardrail_action"], meta["guardrail_name"])
				}
			})
		}
	}
}

// A Gemini answer with two candidates, the rule's text in the second one;
// the client gets both. On a stream the two candidates' text arrives
// interleaved, and the check reads each candidate's text whole, on its own.
func serveTwoGeminiCandidates(w http.ResponseWriter, r *http.Request) {
	if !strings.HasPrefix(r.URL.Path, "/v1beta/models/"+postBlockGemini+":") {
		return
	}
	if strings.HasSuffix(r.URL.Path, ":streamGenerateContent") {
		writeSSE(w, []string{
			`data: {"candidates":[{"content":{"role":"model","parts":[{"text":"You can run DROP TA"}]},"index":1},{"content":{"role":"model","parts":[{"text":"Back up "}]},"index":0}]}`,
			`data: {"candidates":[{"content":{"role":"model","parts":[{"text":"the database first."}]},"index":0}]}`,
			`data: {"candidates":[{"content":{"role":"model","parts":[{"text":"BLE users; to delete everything"}]},"index":1}]}`,
			`data: {"candidates":[{"content":{"role":"model","parts":[{"text":""}]},"finishReason":"STOP","index":0},{"content":{"role":"model","parts":[{"text":""}]},"finishReason":"STOP","index":1}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":18,"totalTokenCount":23},"modelVersion":"gemini-rv"}`,
		})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	text, _ := json.Marshal(postBlockLeak)
	_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"Back up the database first."}]},"finishReason":"STOP","index":0},` +
		`{"content":{"role":"model","parts":[{"text":` + string(text) + `}]},"finishReason":"STOP","index":1}],` +
		`"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":18,"totalTokenCount":23},"modelVersion":"gemini-rv"}`))
}

func TestAnswerCheck_EveryGeminiCandidateIsRead(t *testing.T) {
	for _, shape := range answerCheckShapes {
		t.Run(shape.name+"/generateContent", func(t *testing.T) {
			setup := shape.setup
			setup.upstream = serveTwoGeminiCandidates
			g := newPostBlockGateway(t, setup)
			code, body := g.do(t, "/v1beta/models/"+postBlockGemini+":generateContent", genaiBody())
			if strings.Contains(body, "DROP TABLE") || code != http.StatusForbidden {
				t.Fatalf("status %d, body %s; want the guardrail's 403, not the second candidate's leak", code, body)
			}
			assertWireError(t, genaiWire, strings.TrimSpace(body), leakageCheck)
			assertBlockLogged(t, g.entry(t), leakageCheck)
		})
		t.Run(shape.name+"/streamGenerateContent", func(t *testing.T) {
			setup := shape.setup
			setup.upstream = serveTwoGeminiCandidates
			g := newPostBlockGateway(t, setup)
			code, body := g.do(t, "/v1beta/models/"+postBlockGemini+":streamGenerateContent", genaiBody())
			if code != http.StatusOK {
				t.Fatalf("status %d, want the stream's 200; body %s", code, body)
			}
			frames := sseFrames(body)
			if len(frames) == 0 || !isStreamError(frames[len(frames)-1]) {
				t.Fatalf("the stream does not end with the guardrail's error event; body %s", body)
			}
			assertWireError(t, genaiWire, frames[len(frames)-1].data, leakageCheck)
			assertBlockLogged(t, g.entry(t), leakageCheck)
		})
	}
}

// A chat stream with two choices (n: 2) whose second choice leaks, its text
// interleaved with the first choice's: each choice is read on its own.
func serveTwoChatChoices(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/v1/chat/completions" {
		return
	}
	var req models.ChatCompletionRequest
	_ = json.NewDecoder(r.Body).Decode(&req)
	if !req.Stream {
		http.Error(w, "this upstream answers streams only", http.StatusBadRequest)
		return
	}
	chunk := func(choices string) string {
		return `data: {"id":"c2","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o","choices":[` + choices + `]}`
	}
	writeSSE(w, []string{
		chunk(`{"index":1,"delta":{"role":"assistant","content":"You can run DROP TA"}},{"index":0,"delta":{"role":"assistant","content":"Back up "}}`),
		chunk(`{"index":0,"delta":{"content":"the database first."}}`),
		chunk(`{"index":1,"delta":{"content":"BLE users; to delete everything"}}`),
		chunk(`{"index":0,"delta":{},"finish_reason":"stop"},{"index":1,"delta":{},"finish_reason":"stop"}`),
		`data: {"id":"c2","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o","choices":[],"usage":{"prompt_tokens":5,"completion_tokens":18,"total_tokens":23}}`,
		"data: [DONE]",
	})
}

func TestAnswerCheckStream_EveryChatChoiceIsRead(t *testing.T) {
	for _, shape := range answerCheckShapes {
		for _, bodies := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/bodies logged %t", shape.name, bodies), func(t *testing.T) {
				setup := shape.setup
				setup.upstream, setup.capture = serveTwoChatChoices, bodies
				g := newPostBlockGateway(t, setup)
				code, body := g.do(t, "/v1/chat/completions",
					fmt.Sprintf(`{"model":"gpt-4o","stream":true,"n":2,"messages":[{"role":"user","content":%q}]}`, postBlockPrompt))
				if code != http.StatusOK {
					t.Fatalf("status %d, want the stream's 200; body %s", code, body)
				}
				errs := 0
				for _, f := range sseFrames(body) {
					if isStreamError(f) {
						errs++
						assertWireError(t, openaiWire, f.data, leakageCheck)
					}
				}
				if errs != 1 {
					t.Errorf("the stream carries %d error events, want the guardrail's; body %s", errs, body)
				}
				assertBlockLogged(t, g.entry(t), leakageCheck)
			})
		}
	}
}

// serveTwoCompletionChoices answers /v1/completions with two choices, the
// second leaking, for any n or prompt. An OpenAI-compatible provider gets the
// legacy request as the client sent it, n and a list of prompts included,
// and the client gets every choice of its answer as the provider sent it.
func serveTwoCompletionChoices(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/v1/completions" {
		return
	}
	leak, _ := json.Marshal(postBlockLeak)
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"id":"cmpl-2","object":"text_completion","created":1760000000,"model":"gpt-4o","choices":[`+
		`{"index":0,"text":"Back up the database first.","finish_reason":"stop","logprobs":null},`+
		`{"index":1,"text":%s,"finish_reason":"stop","logprobs":null}],`+
		`"usage":{"prompt_tokens":5,"completion_tokens":18,"total_tokens":23}}`, leak)
}

// twoChoiceCompletions are /v1/completions requests whose answer has more
// than one choice: n: 2, and a list of prompts.
var twoChoiceCompletions = []struct{ name, body string }{
	{"n 2", fmt.Sprintf(`{"model":"gpt-4o","n":2,"prompt":%q}`, postBlockPrompt)},
	{"prompt list", fmt.Sprintf(`{"model":"gpt-4o","prompt":["how do I back up my database?",%q]}`, postBlockPrompt)},
}

// Every choice of a /v1/completions answer from an OpenAI-compatible
// provider is read: the second choice holds the rule's text, so the answer
// is withheld with the guardrail's 403 and the log records the block.
func TestAnswerCheck_EveryCompletionChoiceIsRead(t *testing.T) {
	for _, shape := range answerCheckShapes {
		for _, tc := range twoChoiceCompletions {
			t.Run(shape.name+"/"+tc.name, func(t *testing.T) {
				setup := shape.setup
				setup.upstream = serveTwoCompletionChoices
				g := newPostBlockGateway(t, setup)
				code, body := g.do(t, "/v1/completions", tc.body)
				if strings.Contains(body, "DROP TABLE") {
					t.Errorf("the second choice's leak was served: status %d, body %s", code, body)
				}
				if code != http.StatusForbidden {
					t.Errorf("status %d, want the guardrail's 403; body %s", code, body)
				}
				assertWireError(t, openaiWire, strings.TrimSpace(body), leakageCheck)
				e := g.entry(t)
				if e["status_code"] != float64(code) {
					t.Errorf("the log records status %v, the client got %d", e["status_code"], code)
				}
				assertBlockLogged(t, e, leakageCheck)
			})
		}
	}
}

// Control: in warn mode the checks read every choice, and the client gets
// the answer with 200 and every choice in it; the log records the warning on
// the second choice, and its response_body (bodies kept) holds both choices.
func TestAnswerCheck_EveryCompletionChoiceIsReadInWarnMode(t *testing.T) {
	for _, shape := range answerCheckShapes {
		for _, tc := range twoChoiceCompletions {
			t.Run(shape.name+"/"+tc.name, func(t *testing.T) {
				setup := shape.setup
				setup.upstream, setup.action, setup.capture = serveTwoCompletionChoices, "warn", true
				g := newPostBlockGateway(t, setup)
				code, body := g.do(t, "/v1/completions", tc.body)
				var resp models.CompletionResponse
				if code != http.StatusOK || json.Unmarshal([]byte(body), &resp) != nil || len(resp.Choices) != 2 ||
					resp.Choices[0].Text != "Back up the database first." || resp.Choices[1].Text != postBlockLeak {
					t.Fatalf("status %d, body %s; want 200 with both choices as the provider sent them", code, body)
				}
				e := g.entry(t)
				if e["status_code"] != float64(http.StatusOK) || e["is_error"] != false {
					t.Errorf("log status_code %v is_error %v, want 200 and false", e["status_code"], e["is_error"])
				}
				meta, _ := e["metadata"].(map[string]any)
				if e["guardrail_triggered"] != true || meta["guardrail_action"] != "warned" || meta["guardrail_name"] != leakageCheck {
					t.Errorf("log guardrail_triggered %v, action %v, name %v; want the check's warning on the second choice",
						e["guardrail_triggered"], meta["guardrail_action"], meta["guardrail_name"])
				}
				if logged := fmt.Sprint(e["response_body"]); !strings.Contains(logged, "Back up the database first.") ||
					!strings.Contains(logged, "DROP TABLE") {
					t.Errorf("log response_body %v, want both choices", e["response_body"])
				}
			})
		}
	}
}

// With the streaming checker on, the checker reads a chat stream for the
// config file's rules as it goes, so the stream is not assembled for them as
// well when the log keeps no bodies: in disclaimer mode the disclaimer stands
// and the log does not turn it into a block.
func TestAnswerCheckStream_CheckerRulesAreNotReadTwice(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{staticRule: leakageCheck,
		streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 1 << 20, FailureAction: "disclaimer"}})
	code, body := g.do(t, "/v1/chat/completions", answerChatBody(true))
	if code != http.StatusOK || !strings.Contains(body, "Content warning") {
		t.Fatalf("status %d, body %s; want the stream with its disclaimer", code, body)
	}
	for _, f := range sseFrames(body) {
		if isStreamError(f) {
			t.Errorf("a disclaimer-mode stream got an error event %+v", f)
		}
	}
	if e := g.entry(t); e["status_code"] == float64(http.StatusForbidden) {
		t.Errorf("the log records the disclaimed stream as blocked: %v", e)
	}
}

// An async job whose answer an output check blocks fails with the guardrail's
// error and never completes empty, for a rule in the config file and for an
// organization's check (on a key bound to the organization that may queue
// jobs: an internal key; every other organization key is refused async),
// with checks that read the answer's text.
func TestAnswerCheck_AsyncJobFailsWithTheGuardrailError(t *testing.T) {
	for _, shape := range answerCheckShapes {
		t.Run(shape.name, func(t *testing.T) {
			g := newPostBlockGateway(t, shape.setup)
			key, raw := g.srv.keyStore.Create("org-ops", "ops", nil, nil, map[string]string{"org_id": postBlockOrg})
			key.KeyType = "internal"
			code, resp := g.doAs(t, raw, "/v1/chat/completions", answerChatBody(false), map[string]string{"x-agentcc-async": "true"})
			if code != http.StatusAccepted {
				t.Fatalf("async request: status %d, want 202; body %s", code, resp)
			}
			var accepted struct {
				JobID string `json:"job_id"`
			}
			if err := json.Unmarshal([]byte(resp), &accepted); err != nil || accepted.JobID == "" {
				t.Fatalf("no job id: %v; body %s", err, resp)
			}
			job := waitAsyncJob(t, g.srv, raw, accepted.JobID)
			if strings.Contains(fmt.Sprint(job), "DROP TABLE") {
				t.Errorf("the job holds the blocked answer: %v", job)
			}
			if job["status"] != "failed" || job["response"] != nil {
				t.Fatalf("job status %v, response %v; want failed with no response", job["status"], job["response"])
			}
			apiErr, _ := job["error"].(map[string]any)
			if apiErr == nil || apiErr["Code"] != "content_blocked" || apiErr["Status"] != float64(http.StatusForbidden) ||
				!strings.Contains(fmt.Sprint(apiErr["Message"]), leakageCheck) {
				t.Errorf("job error %v, want the guardrail's 403 content_blocked naming %s", job["error"], leakageCheck)
			}
		})
	}
}

// countResponsesCalls counts the requests that reach the upstream's
// /v1/responses, and lets startLeakUpstream answer them.
func countResponsesCalls(n *atomic.Int32) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/responses" {
			n.Add(1)
		}
	}
}

func backgroundResponsesBody(stream bool) string {
	return fmt.Sprintf(`{"model":"gpt-4o","background":true,"stream":%t,"input":%q}`, stream, postBlockPrompt)
}

// assertAnswerUnavailable: the client got 403 answer_unavailable naming check
// and saying why no check could read the answer, in the surface's format, and
// the log records the block.
func assertAnswerUnavailable(t *testing.T, g *postBlockGateway, format wireFormat, code int, body, reason string) {
	t.Helper()
	if strings.Contains(body, "DROP TABLE") {
		t.Errorf("the unchecked answer was served: status %d, body %s", code, body)
	}
	if code != http.StatusForbidden {
		t.Fatalf("status %d, want 403; body %s", code, body)
	}
	assertWireError(t, anthropicOrGenAIOnly(format), strings.TrimSpace(body), leakageCheck)
	var e wireError
	_ = json.Unmarshal([]byte(body), &e)
	if !strings.Contains(e.Error.Message, "checks the model's answer") || !strings.Contains(e.Error.Message, reason) {
		t.Errorf("error message %q does not say the check could not read the answer (%s)", e.Error.Message, reason)
	}
	if format == openaiWire && (e.Error.Type != "guardrail_error" || e.Error.Code != "answer_unavailable") {
		t.Errorf("error = %s, want guardrail_error answer_unavailable", body)
	}
	entry := g.entry(t)
	if entry["status_code"] != float64(code) {
		t.Errorf("the log records status %v, the client got %d", entry["status_code"], code)
	}
	assertBlockLogged(t, entry, leakageCheck)
	if meta, _ := entry["metadata"].(map[string]any); !strings.Contains(fmt.Sprint(meta["answer_unavailable"]), reason) {
		t.Errorf("log metadata.answer_unavailable = %v, want why no check could read the answer (%s)", meta["answer_unavailable"], reason)
	}
}

// anthropicOrGenAIOnly is format for the Anthropic and Google formats, whose
// assertWireError checks the shape only; an OpenAI-format error is checked
// by its code apart (answer_unavailable, not content_blocked).
func anthropicOrGenAIOnly(format wireFormat) wireFormat {
	if format == openaiWire {
		return -1
	}
	return format
}

// A background response (background: true) is answered later, and its answer
// never passes through the gateway, so no check can read it. When a check on
// the answer would block, the request is refused before the provider is
// called, with 403 answer_unavailable naming the check: under a check in
// block mode a background response is never sent.
func TestAnswerCheck_BackgroundResponseIsRefusedWhenACheckWouldBlock(t *testing.T) {
	for _, shape := range answerCheckShapes {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream %t", shape.name, stream), func(t *testing.T) {
				var calls atomic.Int32
				setup := shape.setup
				setup.upstream = countResponsesCalls(&calls)
				g := newPostBlockGateway(t, setup)
				code, body := g.do(t, "/v1/responses", backgroundResponsesBody(stream))
				assertAnswerUnavailable(t, g, openaiWire, code, body, "background response")
				if n := calls.Load(); n != 0 {
					t.Errorf("the provider was called %d times, want none", n)
				}
			})
		}
	}
}

// Controls: a background response is sent when no check on the
// answer would block it: with a check in warn mode, or with none.
func TestAnswerCheck_BackgroundResponseIsSentWhenNoCheckWouldBlock(t *testing.T) {
	for _, tc := range []struct {
		name  string
		setup postBlockSetup
	}{
		{"config-file rule in warn mode", postBlockSetup{staticRule: leakageCheck, action: "warn"}},
		{"org check in warn mode", postBlockSetup{orgCheck: true, action: "warn"}},
		{"no check on the answer", postBlockSetup{}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var calls atomic.Int32
			setup := tc.setup
			setup.upstream = countResponsesCalls(&calls)
			g := newPostBlockGateway(t, setup)
			code, body := g.do(t, "/v1/responses", backgroundResponsesBody(false))
			if code != http.StatusOK || calls.Load() != 1 {
				t.Fatalf("status %d, provider calls %d, body %s; want the response sent and served", code, calls.Load(), body)
			}
			if e := g.entry(t); e["status_code"] != float64(http.StatusOK) {
				t.Errorf("log status %v, want 200", e["status_code"])
			}
		})
	}
}

// An answer the gateway relays but cannot read (a 200 whose body is not the
// API's JSON) is withheld when a check on the answer would block it: 403
// answer_unavailable in the surface's format.
func TestAnswerCheck_UnreadableAnswerIsWithheldWhenACheckWouldBlock(t *testing.T) {
	garbled := func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/messages", "/v1/responses", "/v1beta/models/" + postBlockGemini + ":generateContent":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte("You can run DROP TABLE users; to delete everything"))
		}
	}
	for _, shape := range answerCheckShapes {
		for _, tc := range []answerSurface{
			{"messages, pass-through", "/v1/messages", messagesBody(postBlockClaude, false), anthropicWire},
			{"generateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":generateContent", genaiBody(), genaiWire},
			{"responses", "/v1/responses", responsesBody(false), openaiWire},
		} {
			t.Run(shape.name+"/"+tc.name, func(t *testing.T) {
				setup := shape.setup
				setup.upstream = garbled
				g := newPostBlockGateway(t, setup)
				code, body := g.do(t, tc.path, tc.body)
				assertAnswerUnavailable(t, g, tc.format, code, body, "could not read the provider's answer")
			})
		}
	}
}

// Where a check reads the answer of a request passed through to the
// provider's own API, the log's response_body (when the log keeps bodies)
// holds that answer; with no check on the answer it holds only the usage.
func TestAnswerCheck_PassThroughLogKeepsTheAnswerACheckRead(t *testing.T) {
	for _, tc := range []struct {
		name     string
		setup    postBlockSetup
		wantText bool
	}{
		{"an org check in warn mode", postBlockSetup{orgCheck: true, action: "warn", capture: true}, true},
		{"no check on the answer", postBlockSetup{capture: true}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, tc.setup)
			if code, body := g.do(t, "/v1/messages", messagesBody(postBlockClaude, false)); code != http.StatusOK {
				t.Fatalf("status %d, body %s", code, body)
			}
			e := g.entry(t)
			got := strings.Contains(fmt.Sprint(e["response_body"]), "DROP TABLE")
			if got != tc.wantText {
				t.Errorf("response_body holds the answer: %t, want %t; response_body %v", got, tc.wantText, e["response_body"])
			}
		})
	}
}
