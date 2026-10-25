package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A blocking check on the answer never lets a streamed answer it blocks
// reach the client. The checks that read the answer (an
// organization's data-leakage-prevention in block mode, or the same check as
// a rule in the config file at the post stage) read the whole answer, which a
// stream has only when it ends; a stream such a check reads is held until the
// check has passed, so a blocked answer's deltas, its done events and its
// completed event never go out: the client gets only the route's guardrail
// error event. A passing answer then goes out frame for frame as it came. A
// request with no blocking check on the answer is not held. The fixture is
// newPostBlockGateway's; the upstream here streams the answer in several
// deltas, with the done and completed events each API sends.
//
// /v1/responses is passed through to the provider's own API only: the
// gateway serves it from a provider that implements the Responses API
// (providers.ResponsesProvider, the OpenAI provider), and has no translated
// Responses path.

// heldLeakDeltas is the leaking answer (postBlockLeak) as three deltas: the
// blocked statement, whole, in the second.
var heldLeakDeltas = []string{"You can run ", "DROP TABLE users;", " to delete everything"}

// heldCleanDeltas is an answer no check blocks.
var heldCleanDeltas = []string{"You can reset ", "it from the admin ", "console."}

// heldAnswerPieces are the pieces of the leaking answer: a client that got
// any of them got part of the blocked answer.
var heldAnswerPieces = []string{"You can run", "DROP", "TABLE", "delete everything"}

// serveStreamedAnswer streams the answer made of deltas on every API a
// streaming route calls: chat completions (a role chunk, a chunk per delta,
// the finish chunk with the usage, [DONE]); Anthropic's messages
// (message_start, a content_block_delta per delta, message_delta with the
// usage, message_stop); Gemini's streamGenerateContent (a data frame per
// delta, then the finish with usageMetadata); and OpenAI's Responses API (a
// response.output_text.delta per delta, then response.output_text.done,
// response.content_part.done, response.output_item.done and
// response.completed, each carrying the whole text, as OpenAI sends them).
// A request that does not stream is left to startLeakUpstream.
func serveStreamedAnswer(deltas []string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var req struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		_ = json.Unmarshal(body, &req)
		r.Body = io.NopCloser(bytes.NewReader(body))
		path := r.URL.Path
		if !req.Stream && !strings.HasSuffix(path, ":streamGenerateContent") {
			return
		}
		writeStreamedAnswer(w, path, req.Model, deltas, nil)
	}
}

// writeStreamedAnswer writes the stream serveStreamedAnswer describes for
// path. After the first delta it calls between, when set (an upstream that
// waits for the test).
func writeStreamedAnswer(w http.ResponseWriter, path, model string, deltas []string, between func()) {
	full := strings.Join(deltas, "")
	q := func(s string) string { b, _ := json.Marshal(s); return string(b) }
	var head, perDelta, tail []string
	each := func(fn func(i int, d string) string) {
		for i, d := range deltas {
			perDelta = append(perDelta, fn(i, d))
		}
	}
	switch {
	case path == "/v1/chat/completions":
		m := q(model)
		chunk := func(choice string) string {
			return fmt.Sprintf(`data: {"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":%s,"choices":[%s]}`, m, choice)
		}
		head = []string{chunk(`{"index":0,"delta":{"role":"assistant"}}`)}
		each(func(_ int, d string) string { return chunk(`{"index":0,"delta":{"content":` + q(d) + `}}`) })
		tail = []string{
			fmt.Sprintf(`data: {"id":"c1","object":"chat.completion.chunk","created":1760000000,"model":%s,"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":9,"total_tokens":14}}`, m),
			"data: [DONE]",
		}
	case path == "/v1/messages":
		head = []string{
			fmt.Sprintf("event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"type\":\"message\",\"role\":\"assistant\",\"model\":%q,\"content\":[],\"stop_reason\":null,\"stop_sequence\":null,\"usage\":{\"input_tokens\":5,\"output_tokens\":1}}}", postBlockClaude),
			"event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}",
		}
		each(func(_ int, d string) string {
			return "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":" + q(d) + "}}"
		})
		tail = []string{
			"event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}",
			"event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\",\"stop_sequence\":null},\"usage\":{\"output_tokens\":9}}",
			"event: message_stop\ndata: {\"type\":\"message_stop\"}",
		}
	case strings.HasPrefix(path, "/v1beta/models/"):
		each(func(_ int, d string) string {
			return fmt.Sprintf(`data: {"candidates":[{"content":{"role":"model","parts":[{"text":%s}]},"index":0}],"modelVersion":%q}`, q(d), postBlockGemini)
		})
		tail = []string{fmt.Sprintf(`data: {"candidates":[{"content":{"role":"model","parts":[{"text":""}]},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":9,"totalTokenCount":14},"modelVersion":%q}`, postBlockGemini)}
	case path == "/v1/responses":
		seq := 0
		ev := func(name, data string) string {
			seq++
			return fmt.Sprintf("event: %s\ndata: {\"type\":%q,\"sequence_number\":%d,%s}", name, name, seq-1, data)
		}
		part := `{"type":"output_text","text":` + q(full) + `,"annotations":[]}`
		item := `{"type":"message","id":"item_1","status":"completed","role":"assistant","content":[` + part + `]}`
		head = []string{
			ev("response.created", `"response":{"id":"resp_1","object":"response","status":"in_progress","model":"gpt-4o","output":[]}`),
			ev("response.output_item.added", `"output_index":0,"item":{"type":"message","id":"item_1","status":"in_progress","role":"assistant","content":[]}`),
			ev("response.content_part.added", `"item_id":"item_1","output_index":0,"content_index":0,"part":{"type":"output_text","text":"","annotations":[]}`),
		}
		each(func(_ int, d string) string {
			return ev("response.output_text.delta", `"item_id":"item_1","output_index":0,"content_index":0,"delta":`+q(d))
		})
		tail = []string{
			ev("response.output_text.done", `"item_id":"item_1","output_index":0,"content_index":0,"text":`+q(full)),
			ev("response.content_part.done", `"item_id":"item_1","output_index":0,"content_index":0,"part":`+part),
			ev("response.output_item.done", `"output_index":0,"item":`+item),
			ev("response.completed", `"response":{"id":"resp_1","object":"response","status":"completed","model":"gpt-4o","output":[`+item+`],"usage":{"input_tokens":5,"output_tokens":9,"total_tokens":14}}`),
		}
	default:
		http.NotFound(w, nil)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	flusher := w.(http.Flusher)
	send := func(e string) {
		fmt.Fprintf(w, "%s\n\n", e)
		flusher.Flush()
	}
	for _, e := range head {
		send(e)
	}
	for i, e := range perDelta {
		send(e)
		if i == 0 && between != nil {
			between()
		}
	}
	for _, e := range tail {
		send(e)
	}
}

// heldRoute is a streaming route, as a client calls it.
type heldRoute struct {
	name   string
	path   string
	body   string
	format wireFormat
}

// heldRoutes are the streaming routes, translated for the provider and passed
// through to its own API.
var heldRoutes = []heldRoute{
	{"chat", "/v1/chat/completions", answerChatBody(true), openaiWire},
	{"completions", "/v1/completions", completionsBody("gpt-4o", true), openaiWire},
	{"messages, translated", "/v1/messages", messagesBody("gpt-4o", true), anthropicWire},
	{"messages, pass-through", "/v1/messages", messagesBody(postBlockClaude, true), anthropicWire},
	{"streamGenerateContent, translated", "/v1beta/models/gpt-4o:streamGenerateContent", genaiBody(), genaiWire},
	{"streamGenerateContent, pass-through", "/v1beta/models/" + postBlockGemini + ":streamGenerateContent", genaiBody(), genaiWire},
	{"responses, pass-through", "/v1/responses", responsesBody(true), openaiWire},
}

// heldChecks are the blocking checks on the answer: the config file's rule and
// the organization's check, each reading the answer's text.
var heldChecks = []struct {
	name  string
	setup postBlockSetup
}{
	{"config-file rule", postBlockSetup{staticRule: leakageCheck}},
	{"org check", postBlockSetup{orgCheck: true}},
}

// assertNoAnswerReachedTheClient: no frame the client got carries the blocked
// statement, in a delta, a done event or a completed event, and the body holds
// no piece of the answer at all.
func assertNoAnswerReachedTheClient(t *testing.T, body string) {
	t.Helper()
	for i, f := range sseFrames(body) {
		if strings.Contains(f.data, "DROP TABLE") {
			t.Errorf("frame %d (event %q) carries the blocked statement: %s", i, f.event, f.data)
		}
	}
	for _, piece := range heldAnswerPieces {
		if strings.Contains(body, piece) {
			t.Errorf("the client got %q of the blocked answer; body %s", piece, body)
		}
	}
}

// assertEndsWithTheGuardrailErrorOnly: the stream is the route's guardrail
// error event (code, naming check) and the route's end: on chat and
// completions the error then [DONE]; on the others the error is the last
// frame. It returns the frames.
func assertEndsWithTheGuardrailErrorOnly(t *testing.T, rt heldRoute, body, code, check string) []sseFrame {
	t.Helper()
	frames := sseFrames(body)
	errAt, errs := -1, 0
	for i, f := range frames {
		if isStreamError(f) {
			errAt, errs = i, errs+1
		}
	}
	if errs != 1 {
		t.Fatalf("the stream carries %d error events, want 1; body %s", errs, body)
	}
	data := frames[errAt].data
	switch {
	case rt.path == "/v1/responses":
		var e struct{ Type, Code, Message string }
		if json.Unmarshal([]byte(data), &e) != nil || e.Type != "error" || e.Code != code || !strings.Contains(e.Message, check) {
			t.Errorf("error event %s, want a Responses error event, %s naming %s", data, code, check)
		}
		if frames[errAt].event != "error" {
			t.Errorf("the Responses error is sent as event %q, want error", frames[errAt].event)
		}
	case code == "content_blocked":
		assertWireError(t, rt.format, data, check)
	default:
		// The route's error format for a guardrail's 403: only the OpenAI
		// format carries the code.
		var e wireError
		if json.Unmarshal([]byte(data), &e) != nil || !strings.Contains(e.Error.Message, check) {
			t.Errorf("error event %s, want a guardrail error naming %s", data, check)
		}
		switch rt.format {
		case openaiWire:
			if e.Error.Type != models.ErrTypeGuardrail || e.Error.Code != code {
				t.Errorf("error event %s, want guardrail_error %s", data, code)
			}
		case anthropicWire:
			if e.Type != "error" || e.Error.Type != "permission_error" {
				t.Errorf("error event %s, want an Anthropic permission_error", data)
			}
		case genaiWire:
			if e.Error.Code != float64(http.StatusForbidden) || e.Error.Status != "PERMISSION_DENIED" {
				t.Errorf("error event %s, want a Google error, code 403 PERMISSION_DENIED", data)
			}
		}
	}
	if rt.format == anthropicWire && frames[errAt].event != "error" {
		t.Errorf("the Anthropic error is sent as event %q, want error", frames[errAt].event)
	}
	want := len(frames) - 1
	if rt.path == "/v1/chat/completions" || rt.path == "/v1/completions" {
		want = len(frames) - 2
		if frames[len(frames)-1].data != "[DONE]" {
			t.Errorf("the stream ends with %q, want [DONE]; body %s", frames[len(frames)-1].data, body)
		}
	}
	if errAt != want {
		t.Errorf("the error event is frame %d of %d, want it to be the stream's only frame before its end; body %s", errAt, len(frames), body)
	}
	return frames
}

// On every streaming route, with a blocking check on the answer, a blocked
// answer never reaches the client: no delta, done event or completed event
// carries it, the stream is the guardrail's error event and the route's end,
// and the log records 403 and the block. Whether or not the log keeps bodies.
func TestStreamHold_BlockedAnswerNeverReachesTheClient(t *testing.T) {
	for _, check := range heldChecks {
		for _, bodies := range []bool{false, true} {
			for _, rt := range heldRoutes {
				t.Run(fmt.Sprintf("%s/bodies logged %t/%s", check.name, bodies, rt.name), func(t *testing.T) {
					setup := check.setup
					setup.capture = bodies
					setup.upstream = serveStreamedAnswer(heldLeakDeltas)
					g := newPostBlockGateway(t, setup)
					code, body := g.do(t, rt.path, rt.body)
					if code != http.StatusOK {
						t.Fatalf("status %d, want the stream's 200; body %s", code, body)
					}
					assertNoAnswerReachedTheClient(t, body)
					assertEndsWithTheGuardrailErrorOnly(t, rt, body, "content_blocked", leakageCheck)
					assertBlockLogged(t, g.entry(t), leakageCheck)
				})
			}
		}
	}
}

// heldIDs are the parts of a stream that differ between two runs of the
// same request (ids a translator mints, timestamps).
var heldIDs = regexp.MustCompile(`"(id|created|created_at|responseId|message_id)":("[^"]*"|[0-9]+)|msg_[A-Za-z0-9]+|chatcmpl-[A-Za-z0-9]+`)

// A passing answer on a held stream goes out as it came: the same frames, in
// the same order, as the same request with the check in warn mode, which is
// not held. The answer is whole, and the stream carries no error event.
func TestStreamHold_PassingAnswerGoesOutAsItCame(t *testing.T) {
	for _, check := range heldChecks {
		for _, rt := range heldRoutes {
			t.Run(check.name+"/"+rt.name, func(t *testing.T) {
				run := func(action string) string {
					setup := check.setup
					setup.action = action
					setup.upstream = serveStreamedAnswer(heldCleanDeltas)
					g := newPostBlockGateway(t, setup)
					code, body := g.do(t, rt.path, rt.body)
					if code != http.StatusOK {
						t.Fatalf("%s: status %d, want 200; body %s", action, code, body)
					}
					for _, f := range sseFrames(body) {
						if isStreamError(f) {
							t.Fatalf("%s: a passing answer's stream got an error event %+v; body %s", action, f, body)
						}
					}
					e := g.entry(t)
					if e["status_code"] != float64(http.StatusOK) || e["is_error"] != false {
						t.Errorf("%s: log status_code %v is_error %v, want 200 and false", action, e["status_code"], e["is_error"])
					}
					return body
				}
				held, streamed := run("block"), run("warn")
				for _, d := range heldCleanDeltas {
					if !strings.Contains(held, d) {
						t.Errorf("the held stream lacks %q of the answer; body %s", d, held)
					}
				}
				if a, b := heldIDs.ReplaceAllString(held, "ID"), heldIDs.ReplaceAllString(streamed, "ID"); a != b {
					t.Errorf("the held stream differs from the stream that is not held:\nheld:     %s\nstreamed: %s", a, b)
				}
			})
		}
	}
}

// seenWriter is the client's end of a stream: it records the body and
// closes seen once the body holds needle.
type seenWriter struct {
	*httptest.ResponseRecorder
	needle string
	mu     sync.Mutex
	seen   chan struct{}
	once   sync.Once
}

func (w *seenWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	n, err := w.ResponseRecorder.Write(p)
	got := strings.Contains(w.ResponseRecorder.Body.String(), w.needle)
	w.mu.Unlock()
	if got {
		w.once.Do(func() { close(w.seen) })
	}
	return n, err
}

func (w *seenWriter) Flush() {}

// streamTiming runs rt with setup against an upstream that sends the first
// delta of heldCleanDeltas, then waits until the client has got it (up to
// wait), then sends the rest. It reports whether the client got the first
// delta while the upstream was still streaming, and the body.
func streamTiming(t *testing.T, setup postBlockSetup, rt heldRoute, wait time.Duration) (beforeTheEnd bool, body string) {
	t.Helper()
	sw := &seenWriter{ResponseRecorder: httptest.NewRecorder(), needle: heldCleanDeltas[0], seen: make(chan struct{})}
	var early atomic.Bool
	setup.upstream = func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var req struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		_ = json.Unmarshal(raw, &req)
		if !req.Stream && !strings.HasSuffix(r.URL.Path, ":streamGenerateContent") {
			return
		}
		writeStreamedAnswer(w, r.URL.Path, req.Model, heldCleanDeltas, func() {
			select {
			case <-sw.seen:
				early.Store(true)
			case <-time.After(wait):
			}
		})
	}
	g := newPostBlockGateway(t, setup)
	req := httptest.NewRequest(http.MethodPost, rt.path, strings.NewReader(rt.body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+postBlockKey)
	req.Header.Set("x-api-key", postBlockKey)
	req.Header.Set("anthropic-version", "2023-06-01")
	ctx, cancel := context.WithTimeout(req.Context(), 10*time.Second)
	defer cancel()
	g.srv.httpServer.Handler.ServeHTTP(sw, req.WithContext(ctx))
	sw.mu.Lock()
	body = sw.Body.String()
	sw.mu.Unlock()
	for _, d := range heldCleanDeltas {
		if !strings.Contains(body, d) {
			t.Fatalf("the client did not get %q of the answer; body %s", d, body)
		}
	}
	return early.Load(), body
}

// A request with no blocking check on the answer streams as it comes: the
// client gets the first delta while the provider is still streaming. With
// the check only in warn mode, with no check, and with the streaming checker
// in disclaimer mode (which never blocks).
func TestStreamHold_NoBlockingCheckStreamsAsItComes(t *testing.T) {
	setups := []struct {
		name  string
		setup postBlockSetup
	}{
		{"no check", postBlockSetup{}},
		{"org check in warn mode", postBlockSetup{orgCheck: true, action: "warn"}},
		{"config-file rule in warn mode", postBlockSetup{staticRule: leakageCheck, action: "warn"}},
	}
	for _, s := range setups {
		for _, rt := range heldRoutes {
			t.Run(s.name+"/"+rt.name, func(t *testing.T) {
				if early, body := streamTiming(t, s.setup, rt, 3*time.Second); !early {
					t.Errorf("the client got the first delta only after the provider's stream ended; body %s", body)
				}
			})
		}
	}
	t.Run("streaming checker in disclaimer mode/chat", func(t *testing.T) {
		setup := postBlockSetup{staticRule: leakageCheck,
			streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 1 << 20, FailureAction: "disclaimer"}}
		if early, body := streamTiming(t, setup, heldRoutes[0], 3*time.Second); !early {
			t.Errorf("the client got the first delta only after the provider's stream ended; body %s", body)
		}
	})
}

// A held stream starts when the answer is complete: with a blocking check on
// the answer, the client gets nothing of it while the provider is still
// streaming, and the whole answer once the check has passed.
func TestStreamHold_HeldStreamStartsWhenTheAnswerIsComplete(t *testing.T) {
	for _, check := range heldChecks {
		for _, rt := range heldRoutes {
			t.Run(check.name+"/"+rt.name, func(t *testing.T) {
				if early, body := streamTiming(t, check.setup, rt, 300*time.Millisecond); early {
					t.Errorf("the client got the first delta before the provider's stream ended; body %s", body)
				}
			})
		}
	}
	t.Run("streaming checker that stops streams/chat", func(t *testing.T) {
		setup := postBlockSetup{staticRule: leakageCheck,
			streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 5, FailureAction: "stop"}}
		if early, body := streamTiming(t, setup, heldRoutes[0], 300*time.Millisecond); early {
			t.Errorf("the client got the first delta before the provider's stream ended; body %s", body)
		}
	})
}

// The streaming checker on chat checks the stream every check_interval
// characters. A chunk that arrives between two checks is never relayed before
// a check has read it: here the blocked statement arrives 29 characters in,
// and the first check runs at 30 (the next chunk). The client gets the
// checker's error event and none of the answer, and the log records the
// block.
func TestStreamHold_CheckerNeverRelaysAChunkItHasNotChecked(t *testing.T) {
	setup := postBlockSetup{staticRule: leakageCheck,
		streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 30, FailureAction: "stop"},
		upstream:  serveStreamedAnswer(heldLeakDeltas)}
	g := newPostBlockGateway(t, setup)
	code, body := g.do(t, "/v1/chat/completions", answerChatBody(true))
	if code != http.StatusOK {
		t.Fatalf("status %d, want the stream's 200; body %s", code, body)
	}
	assertNoAnswerReachedTheClient(t, body)
	events, _ := assertStreamEndsWithGuardrailError(t, body, "stream_blocked")
	if last := events[len(events)-1]; last == "[DONE]" {
		t.Errorf("a stream the checker stopped mid-stream ends with [DONE]; body %s", body)
	}
	assertBlockLogged(t, g.entry(t), "")
}

// Control: with the checker on in stop mode and an answer it passes, the
// client gets the whole answer, then [DONE], and no error event.
func TestStreamHold_CheckerPassesTheWholeAnswer(t *testing.T) {
	setup := postBlockSetup{staticRule: leakageCheck,
		streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 5, FailureAction: "stop"},
		upstream:  serveStreamedAnswer(heldCleanDeltas)}
	g := newPostBlockGateway(t, setup)
	code, body := g.do(t, "/v1/chat/completions", answerChatBody(true))
	if code != http.StatusOK {
		t.Fatalf("status %d, want 200 with the answer; body %s", code, body)
	}
	for _, d := range heldCleanDeltas {
		if !strings.Contains(body, d) {
			t.Errorf("the stream lacks %q of the answer; body %s", d, body)
		}
	}
	events := sseEvents(body)
	if _, at := streamError(events); at >= 0 {
		t.Errorf("a passing answer got an error event; body %s", body)
	}
	if events[len(events)-1] != "[DONE]" {
		t.Errorf("the stream ends with %q, want [DONE]", events[len(events)-1])
	}
}

// bigDeltas is a clean answer of n bytes, in deltas of 64 KiB.
func bigDeltas(n int) []string {
	piece := strings.Repeat("reset it from the admin console. ", 64<<10/33)
	var out []string
	for size := 0; size < n; size += len(piece) {
		out = append(out, piece)
	}
	return out
}

// A held stream is bounded by the gateway's response-size limit, 10 MiB: an
// answer over it is refused with the guardrail's error (answer_unavailable,
// naming the check), never relayed unchecked, and the log records the block.
func TestStreamHold_AnswerOverTheBoundIsRefused(t *testing.T) {
	deltas := bigDeltas(11 << 20)
	for _, rt := range []heldRoute{heldRoutes[0], heldRoutes[3], heldRoutes[6]} {
		t.Run(rt.name, func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{orgCheck: true, upstream: serveStreamedAnswer(deltas)})
			code, body := g.do(t, rt.path, rt.body)
			if code != http.StatusOK {
				t.Fatalf("status %d, want the stream's 200; body %.2000s", code, body)
			}
			if strings.Contains(body, "admin console") || len(body) > 64<<10 {
				t.Fatalf("the client got %d bytes of the answer over the bound; head %.500s", len(body), body)
			}
			assertEndsWithTheGuardrailErrorOnly(t, rt, body, "answer_unavailable", leakageCheck)
			e := g.entry(t)
			assertBlockLogged(t, e, leakageCheck)
			if meta, _ := e["metadata"].(map[string]any); !strings.Contains(fmt.Sprint(meta["answer_unavailable"]), "10 MiB") {
				t.Errorf("log metadata.answer_unavailable = %v, want the 10 MiB bound", meta["answer_unavailable"])
			}
		})
	}
}

// A held stream whose answer the checks cannot read whole is refused, not
// relayed with the part they did not read: a Responses event line over the
// 4 MiB a line is read within, and a Gemini stream with more candidates than
// the gateway assembles (128), each with the blocked statement where the
// checks did not read it.
func TestStreamHold_AnswerTheChecksCannotReadWholeIsRefused(t *testing.T) {
	longLine := func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/responses" {
			return
		}
		text := strings.Repeat("a", 5<<20) + " You can run DROP TABLE users; to delete everything"
		writeSSE(w, []string{
			`event: response.created` + "\n" + `data: {"type":"response.created","sequence_number":0,"response":{"id":"resp_1","object":"response","status":"in_progress","model":"gpt-4o","output":[]}}`,
			fmt.Sprintf("event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"sequence_number\":1,\"item_id\":\"item_1\",\"output_index\":0,\"content_index\":0,\"delta\":%q}", text),
			`event: response.completed` + "\n" + `data: {"type":"response.completed","sequence_number":2,"response":{"id":"resp_1","object":"response","status":"completed","model":"gpt-4o","output":[],"usage":{"input_tokens":5,"output_tokens":9,"total_tokens":14}}}`,
		})
	}
	manyCandidates := func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, ":streamGenerateContent") {
			return
		}
		var cands []string
		for i := 0; i <= maxStreamCaptureChoices; i++ {
			text := "fine"
			if i == maxStreamCaptureChoices {
				text = postBlockLeak
			}
			cands = append(cands, fmt.Sprintf(`{"content":{"role":"model","parts":[{"text":%q}]},"finishReason":"STOP","index":%d}`, text, i))
		}
		writeSSE(w, []string{fmt.Sprintf(`data: {"candidates":[%s],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":9,"totalTokenCount":14},"modelVersion":%q}`,
			strings.Join(cands, ","), postBlockGemini)})
	}
	for _, tc := range []struct {
		name     string
		rt       heldRoute
		upstream http.HandlerFunc
	}{
		{"a Responses line over 4 MiB", heldRoutes[6], longLine},
		{"a Gemini stream with 129 candidates", heldRoutes[5], manyCandidates},
	} {
		for _, check := range heldChecks {
			t.Run(tc.name+"/"+check.name, func(t *testing.T) {
				setup := check.setup
				setup.upstream = tc.upstream
				g := newPostBlockGateway(t, setup)
				code, body := g.do(t, tc.rt.path, tc.rt.body)
				if code != http.StatusOK {
					t.Fatalf("status %d, want the stream's 200; body %.2000s", code, body)
				}
				if strings.Contains(body, "DROP TABLE") || len(body) > 64<<10 {
					t.Fatalf("the client got %d bytes of an answer the checks did not read whole; head %.500s", len(body), body)
				}
				assertEndsWithTheGuardrailErrorOnly(t, tc.rt, body, "answer_unavailable", leakageCheck)
				assertBlockLogged(t, g.entry(t), leakageCheck)
			})
		}
	}
}

// A held, blocked answer is never stored in the response cache: the same
// streamed request again goes to the provider again, and is blocked again.
func TestStreamHold_BlockedAnswerIsNeverCached(t *testing.T) {
	var calls atomic.Int32
	stream := serveStreamedAnswer(heldLeakDeltas)
	store := cache.NewStore(100)
	g := newPostBlockGateway(t, postBlockSetup{orgCheck: true, cache: store,
		upstream: func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/v1/chat/completions" {
				calls.Add(1)
			}
			stream(w, r)
		}})
	for i := 0; i < 2; i++ {
		code, body := g.do(t, "/v1/chat/completions", answerChatBody(true))
		if code != http.StatusOK {
			t.Fatalf("request %d: status %d, want the stream's 200; body %s", i+1, code, body)
		}
		assertNoAnswerReachedTheClient(t, body)
		assertEndsWithTheGuardrailErrorOnly(t, heldRoutes[0], body, "content_blocked", leakageCheck)
	}
	if n := calls.Load(); n != 2 {
		t.Errorf("the provider was called %d times for two identical streamed requests, want 2", n)
	}
	if n := store.Len(); n != 0 {
		t.Errorf("the cache holds %d entries after two blocked streams, want 0", n)
	}
}

// The hold applies to the organization's checks that read the answer only
// when one is in block mode: an organization check on the request does not
// hold a stream.
func TestStreamHold_ACheckOnTheRequestDoesNotHold(t *testing.T) {
	setup := postBlockSetup{orgChecks: map[string]*tenant.GuardrailCheck{
		requestCheck: {Enabled: true, Action: "block", ConfidenceThreshold: 0.5},
	}}
	if early, body := streamTiming(t, setup, heldRoutes[0], 3*time.Second); !early {
		t.Errorf("a check on the request held the stream; body %s", body)
	}
}

// stallingStreamedAnswer streams the answer made of deltas as
// serveStreamedAnswer does, but after the first delta it waits until the
// gateway gives up on the request (the upstream request's context ends), so
// the request's deadline ends the stream mid-answer.
func stallingStreamedAnswer(deltas []string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var req struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		_ = json.Unmarshal(body, &req)
		if !req.Stream && !strings.HasSuffix(r.URL.Path, ":streamGenerateContent") {
			return
		}
		writeStreamedAnswer(w, r.URL.Path, req.Model, deltas, func() {
			select {
			case <-r.Context().Done():
			case <-time.After(5 * time.Second):
			}
		})
	}
}

// heldDeadlineLeak is the leaking answer with the blocked statement whole in
// its first delta, the part a stream the deadline ends has read.
var heldDeadlineLeak = []string{"You can run DROP TABLE users;", " to delete everything"}

// lastStreamError is the data of the stream's last error event, or "".
func lastStreamError(body string) string {
	last := ""
	for _, f := range sseFrames(body) {
		if isStreamError(f) {
			last = f.data
		}
	}
	return last
}

// The request's deadline (x-agentcc-request-timeout) that ends a held stream
// mid-answer never lets out the part of a blocked answer the stream has
// read. The checks read that part to their end even though the request's
// context has ended, with guardrails.fail_open on (the gateway's default).
// The client gets the guardrail's error event and none of the answer, on every
// streaming route, for the config file's rule and the org's check, and on
// chat for the streaming checker in stop mode, whose final check reads the
// part after its last check (check_interval 100: the stream ends 29
// characters in, before any check). Several runs each: which of the
// deadline's paths a stream takes varies.
func TestStreamHold_RequestDeadlineNeverReleasesABlockedAnswer(t *testing.T) {
	t.Run("streaming checker that stops streams/chat", func(t *testing.T) {
		g := newPostBlockGateway(t, postBlockSetup{staticRule: leakageCheck, failOpen: true,
			streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 100, FailureAction: "stop"},
			upstream:  stallingStreamedAnswer(heldDeadlineLeak)})
		for i := 0; i < 4; i++ {
			code, body := g.doAs(t, postBlockKey, heldRoutes[0].path, heldRoutes[0].body, map[string]string{"x-agentcc-request-timeout": "300"})
			if code != http.StatusOK {
				t.Fatalf("run %d: status %d, want the stream's 200; body %s", i, code, body)
			}
			assertNoAnswerReachedTheClient(t, body)
			assertStreamEndsWithGuardrailError(t, body, "stream_blocked")
		}
	})
	for _, check := range heldChecks {
		for _, rt := range heldRoutes {
			t.Run(check.name+"/"+rt.name, func(t *testing.T) {
				setup := check.setup
				setup.failOpen = true
				setup.upstream = stallingStreamedAnswer(heldDeadlineLeak)
				g := newPostBlockGateway(t, setup)
				for i := 0; i < 4; i++ {
					code, body := g.doAs(t, postBlockKey, rt.path, rt.body, map[string]string{"x-agentcc-request-timeout": "300"})
					if code != http.StatusOK {
						t.Fatalf("run %d: status %d, want the stream's 200; body %s", i, code, body)
					}
					assertNoAnswerReachedTheClient(t, body)
					if last := lastStreamError(body); !strings.Contains(last, leakageCheck) {
						t.Errorf("run %d: the stream's last error event is %q, want the guardrail's, naming %s; body %s", i, last, leakageCheck, body)
					}
				}
			})
		}
	}
}

// Control: a held stream the deadline ends, with an answer the checks pass,
// lets out the part it read, and carries no guardrail error.
func TestStreamHold_RequestDeadlineReleasesWhatPassed(t *testing.T) {
	for _, rt := range heldRoutes {
		t.Run(rt.name, func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{orgCheck: true, failOpen: true,
				upstream: stallingStreamedAnswer(heldCleanDeltas)})
			code, body := g.doAs(t, postBlockKey, rt.path, rt.body, map[string]string{"x-agentcc-request-timeout": "300"})
			if code != http.StatusOK {
				t.Fatalf("status %d, want the stream's 200; body %s", code, body)
			}
			if !strings.Contains(body, heldCleanDeltas[0]) {
				t.Errorf("the client did not get %q, the part of a passing answer the stream read; body %s", heldCleanDeltas[0], body)
			}
			if last := lastStreamError(body); strings.Contains(last, leakageCheck) {
				t.Errorf("a passing answer got the guardrail's error event %s; body %s", last, body)
			}
		})
	}
}

// The streaming checker runs the config file's rules under the policy the
// guardrail plugin runs them under: the organization's (an org check named
// as a rule overrides that rule's action) with the key's on top. An org
// policy that makes a warn rule block holds the chat stream and the checker
// blocks it, bodies logged or not: no frame carries the blocked answer.
// Control: an org policy that makes a blocking rule warn holds nothing and
// blocks nothing; the answer goes out whole, then [DONE].
func TestStreamHold_CheckerRunsTheRulesUnderTheOrgPolicy(t *testing.T) {
	streaming := &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 100, FailureAction: "stop"}
	for _, bodies := range []bool{false, true} {
		t.Run(fmt.Sprintf("org policy makes a warn rule block/bodies logged %t", bodies), func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{staticRule: leakageCheck, action: "warn", capture: bodies,
				streaming: streaming, upstream: serveStreamedAnswer(heldLeakDeltas),
				orgChecks: map[string]*tenant.GuardrailCheck{
					leakageCheck: {Enabled: true, Action: "block", ConfidenceThreshold: 0.1},
				}})
			code, body := g.do(t, heldRoutes[0].path, heldRoutes[0].body)
			if code != http.StatusOK {
				t.Fatalf("status %d, want the stream's 200; body %s", code, body)
			}
			assertNoAnswerReachedTheClient(t, body)
			assertStreamEndsWithGuardrailError(t, body, "stream_blocked")
			assertBlockLogged(t, g.entry(t), "")
		})
		t.Run(fmt.Sprintf("org policy makes a blocking rule warn/bodies logged %t", bodies), func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{staticRule: leakageCheck, capture: bodies,
				streaming: streaming, upstream: serveStreamedAnswer(heldLeakDeltas),
				orgChecks: map[string]*tenant.GuardrailCheck{
					leakageCheck: {Enabled: true, Action: "warn", ConfidenceThreshold: 0.1},
				}})
			code, body := g.do(t, heldRoutes[0].path, heldRoutes[0].body)
			if code != http.StatusOK {
				t.Fatalf("status %d, want the stream's 200; body %s", code, body)
			}
			for _, d := range heldLeakDeltas {
				if !strings.Contains(body, d) {
					t.Errorf("the stream lacks %q of an answer the org's policy only warns on; body %s", d, body)
				}
			}
			events := sseEvents(body)
			if _, at := streamError(events); at >= 0 {
				t.Errorf("an answer the org's policy only warns on got an error event; body %s", body)
			}
			if len(events) == 0 || events[len(events)-1] != "[DONE]" {
				t.Errorf("the stream does not end with [DONE]; body %s", body)
			}
		})
	}
}
