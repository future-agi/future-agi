package server

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tokenizer"
)

// upstreamStream is what a fake upstream sends on one route: the events
// before the point where a test intervenes, the events that finish the
// stream, and the fragments a test cuts the stream at.
type upstreamStream struct {
	head, tail string
	midEvent   string // the head, then part of the next event
	inTerminal string // everything up to the middle of the terminal event
	errorEvent string // an error the upstream reports in its own stream
}

// chatUsageChunk is sent by an OpenAI-format upstream only when the request
// asks for it with stream_options.include_usage.
const chatUsageChunk = "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[],\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":2,\"total_tokens\":7}}\n\n"

var upstreamStreams = map[string]upstreamStream{
	"/messages": {
		head: "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"type\":\"message\",\"role\":\"assistant\",\"model\":\"claude-sonnet-5-5\",\"content\":[],\"usage\":{\"input_tokens\":5,\"output_tokens\":1}}}\n\n" +
			"event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n" +
			"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"hello there\"}}\n\n",
		tail: "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"!\"}}\n\n" +
			"event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}\n\n" +
			"event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":2}}\n\n" +
			"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n",
		midEvent:   "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text",
		inTerminal: "event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":2}}\n\nevent: message_stop\n",
		errorEvent: "event: error\ndata: {\"type\":\"error\",\"error\":{\"type\":\"overloaded_error\",\"message\":\"Overloaded\"}}\n\n",
	},
	"/chat/completions": {
		head: "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"hi\"}}]}\n\n",
		tail: "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n" +
			chatUsageChunk +
			"data: [DONE]\n\n",
		midEvent: "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"cre",
		// [DONE] is only a sentinel; the chunk that finishes a chat stream is
		// the one with its finish_reason.
		inTerminal: "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_re",
	},
	":streamGenerateContent": {
		head:       "data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"hi\"}]}}],\"modelVersion\":\"gemini-3.7-flash\"}\n\n",
		tail:       "data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"!\"}]},\"finishReason\":\"STOP\"}],\"usageMetadata\":{\"promptTokenCount\":4,\"candidatesTokenCount\":2,\"totalTokenCount\":6},\"modelVersion\":\"gemini-3.7-flash\"}\n\n",
		midEvent:   "data: {\"candidates\":[{\"content\":{\"ro",
		inTerminal: "data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"!\"}]},\"finishR",
		errorEvent: "data: {\"error\":{\"code\":500,\"message\":\"backend exploded\",\"status\":\"INTERNAL\"}}\n\n",
	},
	"/audio/speech": {
		head: "audio-bytes-1",
		tail: "audio-bytes-2",
	},
	"/responses": {
		head: "event: response.created\ndata: {\"type\":\"response.created\",\"sequence_number\":0,\"response\":{\"id\":\"resp_1\",\"status\":\"in_progress\",\"model\":\"gpt-4o\"}}\n\n" +
			"event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"sequence_number\":1,\"delta\":\"hi\"}\n\n",
		tail:       "event: response.completed\ndata: {\"type\":\"response.completed\",\"sequence_number\":2,\"response\":{\"id\":\"resp_1\",\"status\":\"completed\",\"model\":\"gpt-4o\",\"usage\":{\"input_tokens\":11,\"output_tokens\":7,\"total_tokens\":18}}}\n\n",
		midEvent:   "event: response.output_text.delta\ndata: {\"type\":\"response.outp",
		inTerminal: "event: response.completed\ndata: {\"type\":\"response.completed\",\"sequence_number\":2,\"response\":{\"id\":\"resp_1\",\"status\":\"comp",
		errorEvent: "event: error\ndata: {\"type\":\"error\",\"code\":\"server_error\",\"message\":\"upstream exploded\",\"param\":null}\n\n",
	},
}

type upstreamEnd int

const (
	upstreamFinishes        upstreamEnd = iota
	upstreamStalls                      // until the gateway gives up on it
	upstreamDrops                       // the connection, between events
	upstreamDropsMidEvent               // the connection, inside an event
	upstreamDropsInTerminal             // the connection, inside the last event
	upstreamSendsError                  // an error event, then closes
	upstreamTrickles                    // one chunk every few milliseconds, for ever
	upstreamRejects                     // with a 429, before any chunk
)

// streamingUpstream serves the stream for whichever route the gateway calls,
// ended as end says. It records the connection each stream arrived on.
func streamingUpstream(t *testing.T, end upstreamEnd) (*httptest.Server, *sync.Map) {
	t.Helper()
	conns := &sync.Map{} // path → *sync.Map of remote addresses; "include_usage <path>" → true
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var stream upstreamStream
		for suffix, s := range upstreamStreams {
			if strings.HasSuffix(r.URL.Path, suffix) {
				stream = s
			}
		}
		if stream.head == "" {
			io.WriteString(w, `{"data":[]}`) // the startup connectivity check
			return
		}
		if end == upstreamRejects {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusTooManyRequests)
			io.WriteString(w, `{"error":{"message":"slow down","type":"rate_limit_error","code":"rate_limited"}}`)
			return
		}
		seen, _ := conns.LoadOrStore(r.URL.Path, &sync.Map{})
		seen.(*sync.Map).Store(r.RemoteAddr, true)
		body, _ := io.ReadAll(r.Body)
		if strings.Contains(string(body), `"include_usage":true`) {
			conns.Store("include_usage "+r.URL.Path, true)
		} else {
			stream.tail = strings.Replace(stream.tail, chatUsageChunk, "", 1)
		}

		contentType := "text/event-stream"
		if strings.HasSuffix(r.URL.Path, "/audio/speech") {
			contentType = "audio/mpeg"
		}
		w.Header().Set("Content-Type", contentType)
		w.WriteHeader(http.StatusOK)
		io.WriteString(w, stream.head)
		w.(http.Flusher).Flush()

		drop := func(rest string) {
			io.WriteString(w, rest)
			w.(http.Flusher).Flush()
			conn, _, err := http.NewResponseController(w).Hijack()
			if err != nil {
				t.Errorf("hijack: %v", err)
				return
			}
			conn.Close()
		}
		switch end {
		case upstreamStalls:
			select {
			case <-r.Context().Done():
			case <-time.After(10 * time.Second):
			}
		case upstreamDrops:
			drop("")
		case upstreamDropsMidEvent:
			drop(stream.midEvent)
		case upstreamDropsInTerminal:
			drop(stream.inTerminal)
		case upstreamSendsError:
			io.WriteString(w, stream.errorEvent)
		case upstreamTrickles:
			for {
				select {
				case <-r.Context().Done():
					return
				case <-time.After(3 * time.Millisecond):
					io.WriteString(w, stream.head)
					w.(http.Flusher).Flush()
				}
			}
		default:
			// End the body a moment after the last event, as a real upstream
			// may: a relay that stops reading at the terminal event then
			// closes the body before EOF.
			io.WriteString(w, stream.tail)
			w.(http.Flusher).Flush()
			time.Sleep(20 * time.Millisecond)
		}
	}))
	t.Cleanup(ts.Close)
	return ts, conns
}

func streamingServer(t *testing.T, upstreamURL string, plugins ...pipeline.Plugin) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Providers["anthropic"] = config.ProviderConfig{
		BaseURL: upstreamURL, APIKey: "test-key", APIFormat: "anthropic",
		Models: []string{"claude-sonnet-5-5"},
	}
	cfg.Providers["openai"] = config.ProviderConfig{
		BaseURL: upstreamURL, APIKey: "test-key", APIFormat: "openai",
		Models: []string{"gpt-4o", "tts-1"},
	}
	cfg.Providers["gemini"] = config.ProviderConfig{
		BaseURL: upstreamURL, APIKey: "test-key", APIFormat: "gemini",
		Models: []string{"gemini-3.7-flash"},
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

// streamingGateway serves the full handler chain from a real http.Server, so
// the timeouts and write deadlines a client meets are the real ones.
func streamingGateway(t *testing.T, upstreamURL string, plugins ...pipeline.Plugin) string {
	t.Helper()
	srv := streamingServer(t, upstreamURL, plugins...)
	ts := httptest.NewUnstartedServer(srv.httpServer.Handler)
	ts.Config.ReadHeaderTimeout = srv.httpServer.ReadHeaderTimeout
	ts.Config.ReadTimeout = srv.httpServer.ReadTimeout
	ts.Config.WriteTimeout = srv.httpServer.WriteTimeout
	ts.Config.IdleTimeout = srv.httpServer.IdleTimeout
	ts.Start()
	t.Cleanup(ts.Close)
	return ts.URL
}

type streamRoute struct {
	name, path, body string
}

var (
	chatRoute            = streamRoute{"chat", "/v1/chat/completions", `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`}
	nativeRoute          = streamRoute{"messages native", "/v1/messages", `{"model":"claude-sonnet-5-5","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"hi"}]}`}
	translatedRoute      = streamRoute{"messages translated", "/v1/messages", `{"model":"gpt-4o","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"hi"}]}`}
	responsesRoute       = streamRoute{"responses", "/v1/responses", `{"model":"gpt-4o","stream":true,"input":"hi"}`}
	completionsRoute     = streamRoute{"completions", "/v1/completions", `{"model":"gpt-4o","prompt":"hi","stream":true}`}
	genaiNativeRoute     = streamRoute{"genai native", "/v1beta/models/gemini-3.7-flash:streamGenerateContent", `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`}
	genaiTranslatedRoute = streamRoute{"genai translated", "/v1beta/models/gpt-4o:streamGenerateContent", `{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}`}
	speechRoute          = streamRoute{"speech", "/v1/audio/speech/stream", `{"model":"tts-1","input":"hello","voice":"alloy"}`}

	// Every route that streams: each must end a failed stream with an error.
	streamRoutes = []streamRoute{chatRoute, nativeRoute, translatedRoute, responsesRoute, completionsRoute, genaiNativeRoute, genaiTranslatedRoute, speechRoute}
	// The routes whose upstream stream is text events, which a cut can split.
	eventRoutes = []streamRoute{chatRoute, nativeRoute, translatedRoute, responsesRoute, completionsRoute, genaiNativeRoute, genaiTranslatedRoute}
)

func streamRequest(t *testing.T, gatewayURL string, route streamRoute, headers map[string]string) *http.Request {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, gatewayURL+route.path, strings.NewReader(route.body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	return req
}

func postStream(t *testing.T, gatewayURL string, route streamRoute, headers map[string]string) (*http.Response, string) {
	t.Helper()
	resp, err := http.DefaultClient.Do(streamRequest(t, gatewayURL, route, headers))
	if err != nil {
		t.Fatalf("request: %v", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("reading the stream: %v (a stream must end cleanly, with its error inside it)", err)
	}
	return resp, string(body)
}

// sseFrame is one event as a client's SSE parser sees it.
type sseFrame struct {
	event, data string
}

// sseFrames parses a stream the way a client does: an event ends at a blank
// line, and one the stream ends inside is dropped.
func sseFrames(body string) []sseFrame {
	var frames []sseFrame
	var cur sseFrame
	var lines []string
	for _, line := range strings.SplitAfter(body, "\n") {
		if !strings.HasSuffix(line, "\n") {
			break // unterminated: the stream ended inside it
		}
		line = strings.TrimRight(line, "\r\n")
		if line == "" {
			if len(lines) > 0 {
				frames = append(frames, cur)
			}
			cur, lines = sseFrame{}, nil
			continue
		}
		lines = append(lines, line)
		if v, ok := strings.CutPrefix(line, "event:"); ok {
			cur.event = strings.TrimPrefix(v, " ")
		}
		if v, ok := strings.CutPrefix(line, "data:"); ok {
			if cur.data != "" {
				cur.data += "\n"
			}
			cur.data += strings.TrimPrefix(v, " ")
		}
	}
	return frames
}

// streamError is the error a stream ended with: its last event must be the
// error, whole and parseable, in whichever form its route uses — an OpenAI
// data line with an error object, or an error event.
func streamError(t *testing.T, body string) (code, message string) {
	t.Helper()
	frames := sseFrames(body)
	if len(frames) == 0 {
		t.Fatalf("no events in the stream:\n%s", body)
	}
	last := frames[len(frames)-1]
	var payload struct {
		Error struct {
			Type    string `json:"type"`
			Code    any    `json:"code"` // a string, or a number in a GenAI stream
			Status  string `json:"status"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal([]byte(last.data), &payload); err != nil || payload.Error.Message == "" {
		t.Fatalf("stream did not end with a readable error event; last event %q %q:\n%s", last.event, last.data, body)
	}
	if strings.HasPrefix(last.data, `{"type":"error"`) && last.event != "error" {
		t.Fatalf("error payload sent without event: error, which the Anthropic SDK skips:\n%s", body)
	}
	if code, ok := payload.Error.Code.(string); ok && code != "" {
		return code, payload.Error.Message
	}
	if payload.Error.Status != "" {
		return payload.Error.Status, payload.Error.Message
	}
	return payload.Error.Type, payload.Error.Message
}

// assertNotFinished fails when a stream that failed tells the client it finished.
func assertNotFinished(t *testing.T, body string) {
	t.Helper()
	for _, frame := range sseFrames(body) {
		if frame.data == "[DONE]" || frame.event == "message_stop" || frame.event == "response.completed" ||
			strings.Contains(frame.data, `"done":true`) || strings.Contains(frame.data, `"finishReason":"STOP"`) {
			t.Errorf("a failed stream sent its finishing event %q%q:\n%s", frame.event, frame.data, body)
		}
	}
}

// capturePlugin records what the post-plugins were given: the usage the cost
// plugin prices, and the status the request log records.
type capturePlugin struct {
	usage     atomic.Pointer[models.Usage]
	status    atomic.Int32
	runs      atomic.Int32
	estimated atomic.Bool
}

func (p *capturePlugin) Name() string  { return "capture" }
func (p *capturePlugin) Priority() int { return 100 }
func (p *capturePlugin) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *capturePlugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.runs.Add(1)
	p.estimated.Store(rc.Metadata[usageEstimatedKey] == "true")
	if rc.Response != nil && rc.Response.Usage != nil {
		usage := *rc.Response.Usage
		p.usage.Store(&usage)
	}
	status := int32(http.StatusOK)
	var apiErr *models.APIError
	if len(rc.Errors) > 0 && errors.As(rc.Errors[len(rc.Errors)-1], &apiErr) {
		status = int32(apiErr.Status)
	}
	p.status.Store(status)
	return pipeline.ResultContinue()
}

// A stream that reaches its deadline has already answered 200, so the error
// has to be inside it. Without one the client reads a cut stream as finished.
func TestStreamAtDeadlineEndsWithAnErrorEvent(t *testing.T) {
	for _, route := range streamRoutes {
		t.Run(route.name, func(t *testing.T) {
			capture := &capturePlugin{}
			upstream, _ := streamingUpstream(t, upstreamStalls)
			gateway := streamingGateway(t, upstream.URL, capture)

			resp, body := postStream(t, gateway, route, map[string]string{"x-agentcc-timeout": "300ms"})

			if resp.StatusCode != http.StatusOK {
				t.Fatalf("status = %d, want the 200 the stream started with", resp.StatusCode)
			}
			if code, message := streamError(t, body); !strings.Contains(message, "timed out") {
				t.Errorf("error = %s %q, want a timeout", code, message)
			}
			assertNotFinished(t, body)
			if got := capture.status.Load(); got != http.StatusGatewayTimeout {
				t.Errorf("request recorded as %d, want 504", got)
			}
		})
	}
}

// slowWriter keeps the handler busy writing, so the deadline lands while it
// is away from its select. Every channel is then ready at once when it comes
// back: the closed chunk channel must not read as a finished stream.
type slowWriter struct {
	*httptest.ResponseRecorder
}

func (w *slowWriter) Write(b []byte) (int, error) {
	time.Sleep(3 * time.Millisecond)
	return w.ResponseRecorder.Write(b)
}

func (w *slowWriter) Flush() {}

func TestStreamAtDeadlineWhileTheHandlerIsBusy(t *testing.T) {
	upstream, _ := streamingUpstream(t, upstreamTrickles)
	srv := streamingServer(t, upstream.URL)

	for i := range 20 {
		w := &slowWriter{httptest.NewRecorder()}
		srv.httpServer.Handler.ServeHTTP(w, streamRequest(t, "", chatRoute, map[string]string{"x-agentcc-timeout": "60ms"}))

		body := w.Body.String()
		assertNotFinished(t, body)
		if code, _ := streamError(t, body); code != models.ErrTypeGatewayTimeout {
			t.Fatalf("run %d: stream ended with %q, want a gateway timeout", i, code)
		}
	}
}

// An upstream that drops the connection is an upstream failure, wherever in
// the stream it happens, and must not be dressed up as a finished stream.
func TestStreamCutByTheUpstreamEndsWithAnErrorEvent(t *testing.T) {
	cuts := map[string]upstreamEnd{
		"between events":         upstreamDrops,
		"inside an event":        upstreamDropsMidEvent,
		"inside the final event": upstreamDropsInTerminal,
	}
	for cutName, cut := range cuts {
		routes := eventRoutes
		if cut == upstreamDrops {
			routes = streamRoutes
		}
		for _, route := range routes {
			t.Run(cutName+"/"+route.name, func(t *testing.T) {
				capture := &capturePlugin{}
				upstream, _ := streamingUpstream(t, cut)
				gateway := streamingGateway(t, upstream.URL, capture)

				_, body := postStream(t, gateway, route, nil)

				streamError(t, body)
				assertNotFinished(t, body)
				if got := capture.status.Load(); got < http.StatusBadGateway {
					t.Errorf("request recorded as %d, want an upstream failure", got)
				}
			})
		}
	}
}

// An error the upstream reports in its own stream reaches the client as the
// upstream sent it, and the request is recorded as the failure it was.
func TestUpstreamErrorEventIsRecorded(t *testing.T) {
	for _, route := range []streamRoute{nativeRoute, responsesRoute, genaiNativeRoute} {
		t.Run(route.name, func(t *testing.T) {
			capture := &capturePlugin{}
			upstream, _ := streamingUpstream(t, upstreamSendsError)
			gateway := streamingGateway(t, upstream.URL, capture)

			_, body := postStream(t, gateway, route, nil)

			if !strings.HasSuffix(body, upstreamStreamFor(route).errorEvent) {
				t.Errorf("upstream error event not relayed as sent:\n%s", body)
			}
			if got := capture.status.Load(); got != http.StatusBadGateway {
				t.Errorf("request recorded as %d, want 502", got)
			}
		})
	}
}

// x-agentcc-include-metadata puts the cost on the terminal event of every
// streaming route, where the SDKs keep it as an extra field.
func TestStreamMetadataOnTheTerminalEvent(t *testing.T) {
	upstream, _ := streamingUpstream(t, upstreamFinishes)
	gateway := streamingGateway(t, upstream.URL, &metadataPostPlugin{})

	terminal := map[string]string{
		chatRoute.name:            "",
		nativeRoute.name:          "message_stop",
		translatedRoute.name:      "message_stop",
		responsesRoute.name:       "response.completed",
		completionsRoute.name:     "",
		speechRoute.name:          "",
		genaiNativeRoute.name:     "",
		genaiTranslatedRoute.name: "",
	}
	for _, route := range streamRoutes {
		t.Run(route.name, func(t *testing.T) {
			_, body := postStream(t, gateway, route, map[string]string{"x-agentcc-include-metadata": "true"})

			var data string
			for _, frame := range sseFrames(body) {
				if frame.event == terminal[route.name] && frame.data != "[DONE]" {
					data = frame.data
				}
			}
			var payload struct {
				Metadata *models.AgentccStreamMetadata `json:"agentcc_metadata"`
			}
			if err := json.Unmarshal([]byte(data), &payload); err != nil {
				t.Fatalf("terminal event is not JSON: %v\n%s", err, body)
			}
			if payload.Metadata == nil || payload.Metadata.Cost != 0.0045 {
				t.Fatalf("terminal event carries %+v, want agentcc_metadata with cost 0.0045:\n%s", payload.Metadata, body)
			}
		})
	}
}

// Without the header, the stream goes out as the upstream sent it.
func TestStreamUnchangedWithoutMetadataHeader(t *testing.T) {
	upstream, _ := streamingUpstream(t, upstreamFinishes)
	gateway := streamingGateway(t, upstream.URL, &metadataPostPlugin{})

	for _, route := range streamRoutes {
		t.Run(route.name, func(t *testing.T) {
			_, body := postStream(t, gateway, route, nil)
			if strings.Contains(body, "agentcc_metadata") {
				t.Fatalf("metadata sent without the header:\n%s", body)
			}
		})
	}
	for _, route := range []streamRoute{nativeRoute, responsesRoute, genaiNativeRoute} {
		stream := upstreamStreamFor(route)
		if _, body := postStream(t, gateway, route, nil); body != stream.head+stream.tail {
			t.Errorf("%s: relayed stream differs from the upstream's:\n got %q\nwant %q", route.name, body, stream.head+stream.tail)
		}
	}
}

// upstreamStreamFor is the fake upstream's stream for a gateway route.
func upstreamStreamFor(route streamRoute) upstreamStream {
	for suffix, s := range upstreamStreams {
		if strings.HasSuffix(route.path, suffix) {
			return s
		}
	}
	panic("no upstream stream for " + route.path)
}

// A stream ends when its upstream does. The translated GenAI route used to
// hold every finished stream open until the request deadline — 60s by
// default — and, once the deadline reports itself, would have ended a
// complete stream with a timeout.
func TestStreamEndsWhenTheUpstreamDoes(t *testing.T) {
	for _, route := range streamRoutes {
		t.Run(route.name, func(t *testing.T) {
			capture := &capturePlugin{}
			upstream, _ := streamingUpstream(t, upstreamFinishes)
			gateway := streamingGateway(t, upstream.URL, capture)

			start := time.Now()
			_, body := postStream(t, gateway, route, map[string]string{"x-agentcc-timeout": "5s"})

			if elapsed := time.Since(start); elapsed > 2*time.Second {
				t.Errorf("stream took %v to end after its upstream finished", elapsed)
			}
			if strings.Contains(body, `"error"`) {
				t.Errorf("a finished stream reported an error:\n%s", body)
			}
			if got := capture.status.Load(); got != http.StatusOK {
				t.Errorf("request recorded as %d, want 200", got)
			}
		})
	}
}

// An OpenAI-format upstream sends a stream's usage only when asked. Only
// /v1/chat/completions used to ask, so every other route that streams through
// an OpenAI-format provider went unpriced.
func TestStreamUsageIsRequestedOnEveryRoute(t *testing.T) {
	for _, route := range []streamRoute{chatRoute, completionsRoute, translatedRoute, genaiTranslatedRoute} {
		t.Run(route.name, func(t *testing.T) {
			capture := &capturePlugin{}
			upstream, conns := streamingUpstream(t, upstreamFinishes)
			gateway := streamingGateway(t, upstream.URL, capture)

			postStream(t, gateway, route, nil)

			if _, asked := conns.Load("include_usage /v1/chat/completions"); !asked {
				t.Error("the upstream was not asked for stream usage")
			}
			if got := capture.usage.Load(); got == nil || got.TotalTokens != 7 {
				t.Errorf("post-plugins saw usage %+v, want the upstream's 7 tokens", got)
			}
		})
	}
}

// The gateway asks for usage to price the stream; the chunk that carries it
// reaches a /v1/completions client only when the client asked for it too.
func TestCompletionsStreamSendsTheUsageChunkOnlyWhenAsked(t *testing.T) {
	upstream, _ := streamingUpstream(t, upstreamFinishes)
	gateway := streamingGateway(t, upstream.URL)

	if _, body := postStream(t, gateway, completionsRoute, nil); strings.Contains(body, `"usage"`) {
		t.Errorf("usage chunk sent to a client that did not ask for it:\n%s", body)
	}
	asked := streamRoute{completionsRoute.name, completionsRoute.path, `{"model":"gpt-4o","prompt":"hi","stream":true,"stream_options":{"include_usage":true}}`}
	if _, body := postStream(t, gateway, asked, nil); !strings.Contains(body, `"total_tokens":7`) {
		t.Errorf("usage chunk missing for a client that asked for it:\n%s", body)
	}
}

// refusePlugin refuses every request before the provider is called, as a
// rate limit or a budget does.
type refusePlugin struct{}

func (refusePlugin) Name() string  { return "refuse" }
func (refusePlugin) Priority() int { return 10 }
func (refusePlugin) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultError(&models.APIError{Status: http.StatusTooManyRequests, Type: "rate_limit_error", Code: "rate_limited", Message: "slow down"})
}
func (refusePlugin) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// A /v1/completions stream that never starts answers with the failure's own
// status. It used to answer 200 with the error inside the stream when the
// provider refused, and 500 when a rate limit or budget did.
func TestCompletionsStreamThatCannotStartKeepsItsStatus(t *testing.T) {
	t.Run("the provider refuses", func(t *testing.T) {
		upstream, _ := streamingUpstream(t, upstreamRejects)
		gateway := streamingGateway(t, upstream.URL)
		if resp, body := postStream(t, gateway, completionsRoute, nil); resp.StatusCode != http.StatusTooManyRequests {
			t.Errorf("status = %d, want 429:\n%s", resp.StatusCode, body)
		}
	})
	t.Run("a pre-plugin refuses", func(t *testing.T) {
		upstream, _ := streamingUpstream(t, upstreamFinishes)
		gateway := streamingGateway(t, upstream.URL, refusePlugin{})
		if resp, body := postStream(t, gateway, completionsRoute, nil); resp.StatusCode != http.StatusTooManyRequests {
			t.Errorf("status = %d, want 429:\n%s", resp.StatusCode, body)
		}
	})
}

// A GenAI stream holds its latest chunk back to carry the cost. A stream that
// fails must still send that chunk, ahead of the error.
func TestGenAIHeldChunkIsSentBeforeAnError(t *testing.T) {
	for _, route := range []streamRoute{genaiNativeRoute, genaiTranslatedRoute} {
		t.Run(route.name, func(t *testing.T) {
			upstream, _ := streamingUpstream(t, upstreamStalls)
			gateway := streamingGateway(t, upstream.URL)

			_, body := postStream(t, gateway, route, map[string]string{
				"x-agentcc-include-metadata": "true",
				"x-agentcc-timeout":          "300ms",
			})

			frames := sseFrames(body)
			if len(frames) < 2 || !strings.Contains(frames[0].data, `"hi"`) {
				t.Fatalf("the chunk that arrived before the deadline was not sent first:\n%s", body)
			}
			streamError(t, body)
		})
	}
}

// slowPostPlugin stands in for a post-stage plugin that makes a network call.
type slowPostPlugin struct{}

func (slowPostPlugin) Name() string  { return "slow_post" }
func (slowPostPlugin) Priority() int { return 100 }
func (slowPostPlugin) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (slowPostPlugin) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	time.Sleep(400 * time.Millisecond)
	return pipeline.ResultContinue()
}

// The terminal event waits for the post-plugins only when it carries their
// result. SDK helpers return the final response on that event, so holding it
// for every caller would add the post-plugins' time to every stream.
func TestTerminalEventWaitsForPostPluginsOnlyForMetadata(t *testing.T) {
	upstream, _ := streamingUpstream(t, upstreamFinishes)
	gateway := streamingGateway(t, upstream.URL, slowPostPlugin{})

	terminal := map[string]string{
		nativeRoute.name:     "event: message_stop",
		translatedRoute.name: "event: message_stop",
		responsesRoute.name:  "event: response.completed",
	}
	// timeToTerminal is how long the client waits for the terminal event.
	timeToTerminal := func(t *testing.T, route streamRoute, headers map[string]string) time.Duration {
		start := time.Now()
		resp, err := http.DefaultClient.Do(streamRequest(t, gateway, route, headers))
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		reader := bufio.NewReader(resp.Body)
		for {
			line, err := reader.ReadString('\n')
			if strings.HasPrefix(line, terminal[route.name]) {
				return time.Since(start)
			}
			if err != nil {
				t.Fatalf("no terminal event: %v", err)
			}
		}
	}
	for _, route := range []streamRoute{nativeRoute, translatedRoute, responsesRoute} {
		t.Run(route.name, func(t *testing.T) {
			if got := timeToTerminal(t, route, nil); got > 200*time.Millisecond {
				t.Errorf("without metadata the terminal event took %v, want it before the 400ms post-plugin", got)
			}
			if got := timeToTerminal(t, route, map[string]string{"x-agentcc-include-metadata": "true"}); got < 400*time.Millisecond {
				t.Errorf("with metadata the terminal event took %v, want it after the 400ms post-plugin", got)
			}
		})
	}
}

// A streamed /v1/responses call carries its usage on response.completed.
// The relay used to pass it by, so every such call went unpriced.
func TestResponsesStreamIsPricedFromTheTerminalEvent(t *testing.T) {
	capture := &capturePlugin{}
	upstream, _ := streamingUpstream(t, upstreamFinishes)
	gateway := streamingGateway(t, upstream.URL, capture)

	postStream(t, gateway, responsesRoute, nil)

	got := capture.usage.Load()
	if got == nil {
		t.Fatal("post-plugins saw no usage for a streamed /v1/responses call")
	}
	if got.PromptTokens != 11 || got.CompletionTokens != 7 || got.TotalTokens != 18 {
		t.Errorf("usage = %+v, want 11 in, 7 out, 18 total", *got)
	}
	if runs := capture.runs.Load(); runs != 1 {
		t.Errorf("post-plugins ran %d times, want once", runs)
	}
}

// The relays stop at the terminal event, but must still read the upstream to
// its end: closed early, the transport drops the connection, and every
// streamed call pays for a new TCP and TLS handshake.
func TestStreamRelayKeepsTheUpstreamConnection(t *testing.T) {
	upstream, conns := streamingUpstream(t, upstreamFinishes)
	gateway := streamingGateway(t, upstream.URL)

	for range 4 {
		postStream(t, gateway, nativeRoute, nil)
		postStream(t, gateway, responsesRoute, nil)
	}
	for _, path := range []string{"/v1/messages", "/v1/responses"} {
		seen, _ := conns.Load(path)
		count := 0
		seen.(*sync.Map).Range(func(any, any) bool { count++; return true })
		if count != 1 {
			t.Errorf("%s: four streamed calls used %d upstream connections, want 1", path, count)
		}
	}
}

// /v1/messages reports the timeout it applied, as every other route does.
func TestAnthropicMessagesReportsTheAppliedTimeout(t *testing.T) {
	upstream, _ := streamingUpstream(t, upstreamFinishes)
	gateway := streamingGateway(t, upstream.URL)

	resp, _ := postStream(t, gateway, nativeRoute, map[string]string{"x-agentcc-timeout": "7s"})

	if got := resp.Header.Get("x-agentcc-timeout-ms"); got != "7000" {
		t.Errorf("x-agentcc-timeout-ms = %q, want 7000", got)
	}
}

func TestClosedStreamError(t *testing.T) {
	live := context.Background()
	expired, cancelExpired := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancelExpired()
	gone, cancelGone := context.WithCancel(context.Background())
	cancelGone()

	closedWith := func(err error) <-chan error {
		ch := make(chan error, 1)
		if err != nil {
			ch <- err
		}
		close(ch)
		return ch
	}

	cases := []struct {
		name         string
		ctx          context.Context
		errCh        <-chan error
		wantFinished bool
		wantStatus   int
	}{
		{"closed cleanly", live, closedWith(nil), true, 0},
		{"provider error waiting", live, closedWith(models.ErrGatewayTimeout("stream read timed out")), false, http.StatusGatewayTimeout},
		{"plain error waiting", live, closedWith(errors.New("connection reset")), false, http.StatusBadGateway},
		{"deadline, no error", expired, closedWith(nil), false, http.StatusGatewayTimeout},
		{"client left", gone, closedWith(nil), false, 0},
		{"still open", live, make(chan error), true, 0},
		{"nil channel", live, nil, true, 0},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			apiErr, finished := closedStreamError(tc.ctx, tc.errCh)
			if finished != tc.wantFinished {
				t.Errorf("finished = %v, want %v", finished, tc.wantFinished)
			}
			status := 0
			if apiErr != nil {
				status = apiErr.Status
			}
			if status != tc.wantStatus {
				t.Errorf("error status = %d (%v), want %d", status, apiErr, tc.wantStatus)
			}
		})
	}
}

func TestStreamEndError(t *testing.T) {
	gone, cancel := context.WithCancel(context.Background())
	cancel()
	if got := streamEndError(gone, context.Canceled); got != nil {
		t.Errorf("client left: got %v, want nil", got)
	}

	late, cancelLate := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancelLate()
	if got := streamEndError(late, io.ErrUnexpectedEOF); got == nil || got.Status != http.StatusGatewayTimeout {
		t.Errorf("deadline passed: got %v, want a 504", got)
	}

	live := context.Background()
	if got := streamEndError(live, io.ErrUnexpectedEOF); got == nil || got.Status != http.StatusBadGateway {
		t.Errorf("upstream cut: got %v, want a 502", got)
	}
	if got := streamEndError(live, fmt.Errorf("read: %w", context.DeadlineExceeded)); got == nil || got.Status != http.StatusGatewayTimeout {
		t.Errorf("provider ceiling: got %v, want a 504", got)
	}
}

func TestUpstreamStreamError(t *testing.T) {
	for payload, want := range map[string]string{
		`{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}`:         "Overloaded",
		`{"type":"error","code":"server_error","message":"boom","param":null}`:                "boom",
		`{"type":"response.failed","response":{"status":"failed","error":{"message":"bad"}}}`: "bad",
		`not json`: "upstream reported an error in the stream",
	} {
		if got := upstreamStreamError([]byte(payload)); got.Status != http.StatusBadGateway || got.Message != want {
			t.Errorf("%s: got %d %q, want 502 %q", payload, got.Status, got.Message, want)
		}
	}
}

// A stream cut before its usage arrives has still spent tokens, which the
// provider bills. It used to be logged with none on chat and Responses, and
// with input only on /v1/messages; it is now priced from its prompt and the
// output relayed, and marked as an estimate.
func TestStreamCutShortIsPricedFromItsOutput(t *testing.T) {
	hiPrompt := chatPromptTokens(&models.ChatCompletionRequest{
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
	})
	cut := map[string]models.Usage{
		chatRoute.name:       {PromptTokens: hiPrompt, CompletionTokens: 1}, // "hi"
		translatedRoute.name: {PromptTokens: hiPrompt, CompletionTokens: 1},
		nativeRoute.name:     {PromptTokens: 5, CompletionTokens: 3}, // input from message_start, "hello there"
		responsesRoute.name:  {PromptTokens: 1, CompletionTokens: 1}, // input "hi"
	}
	finished := map[string]models.Usage{
		chatRoute.name:       {PromptTokens: 5, CompletionTokens: 2},
		translatedRoute.name: {PromptTokens: 5, CompletionTokens: 2},
		nativeRoute.name:     {PromptTokens: 5, CompletionTokens: 2},
		responsesRoute.name:  {PromptTokens: 11, CompletionTokens: 7},
	}
	for _, route := range []streamRoute{chatRoute, translatedRoute, nativeRoute, responsesRoute} {
		for _, tc := range []struct {
			end       upstreamEnd
			want      models.Usage
			estimated bool
		}{
			{upstreamStalls, cut[route.name], true},
			{upstreamFinishes, finished[route.name], false},
		} {
			name := route.name + " finished"
			if tc.estimated {
				name = route.name + " cut at its deadline"
			}
			t.Run(name, func(t *testing.T) {
				capture := &capturePlugin{}
				upstream, _ := streamingUpstream(t, tc.end)
				gateway := streamingGateway(t, upstream.URL, capture)

				postStream(t, gateway, route, map[string]string{"x-agentcc-timeout": "300ms"})

				got := capture.usage.Load()
				if got == nil {
					t.Fatal("no usage recorded, want the request priced")
				}
				if got.PromptTokens != tc.want.PromptTokens || got.CompletionTokens != tc.want.CompletionTokens {
					t.Errorf("usage = %d in / %d out, want %d / %d",
						got.PromptTokens, got.CompletionTokens, tc.want.PromptTokens, tc.want.CompletionTokens)
				}
				if capture.estimated.Load() != tc.estimated {
					t.Errorf("usage_estimated = %v, want %v", capture.estimated.Load(), tc.estimated)
				}
			})
		}
	}
}

func TestStreamedCharsCountsEachDialectsDeltas(t *testing.T) {
	var n streamedChars
	n.addEvent([]byte(`{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"abc"}}`))
	n.addEvent([]byte(`{"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"a\":1}"}}`))
	n.addEvent([]byte(`{"type":"content_block_delta","index":2,"delta":{"type":"thinking_delta","thinking":"hm"}}`))
	n.addEvent([]byte(`{"type":"response.output_text.delta","delta":"four"}`))
	n.addEvent([]byte(`{"type":"response.function_call_arguments.delta","delta":"{}"}`))
	text, reasoning := "xy", "z"
	n.addChunk(models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{
		Content:          &text,
		ReasoningContent: &reasoning,
		ToolCalls:        []models.ToolCallDelta{{Function: &models.FunctionCall{Arguments: "[1]"}}},
	}}}})
	if want := 3 + 7 + 2 + 4 + 2 + 2 + 1 + 3; int(n) != want {
		t.Errorf("streamedChars = %d, want %d", n, want)
	}
}

// Only text counts as text: an inline image, file data or encrypted reasoning
// would put a cut Responses stream's prompt at hundreds of thousands of tokens.
func TestResponsesPromptTokensCountsMediaAFixedAmount(t *testing.T) {
	image := "data:image/png;base64," + strings.Repeat("A", 1<<20)
	body, _ := json.Marshal(map[string]any{
		"model":        "gpt-4o",
		"instructions": "Be brief.",
		"input": []any{
			map[string]any{"type": "reasoning", "id": "rs_1", "encrypted_content": strings.Repeat("x", 1<<16), "summary": []any{}},
			map[string]any{"type": "message", "role": "user", "content": []any{
				map[string]any{"type": "input_text", "text": "What is this?"},
				map[string]any{"type": "input_image", "image_url": image, "detail": "auto"},
			}},
			map[string]any{"type": "function_call_output", "call_id": "c1", "output": "sunny"},
		},
	})
	want := estimatedTokens(len("Be brief.")+len("What is this?")+len("sunny")) + tokenizer.MediaPartTokens
	if got := responsesPromptTokens(body); got != want {
		t.Errorf("responsesPromptTokens = %d, want %d", got, want)
	}
	if got := responsesPromptTokens([]byte(`{"input":"hi"}`)); got != 1 {
		t.Errorf("a string input = %d tokens, want 1", got)
	}
}
