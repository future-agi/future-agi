package server

import (
	"context"
	"errors"
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
)

// streamingServer is a gateway whose openai, anthropic and gemini providers
// all point at upstreamURL.
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
	releaseTestServer(t, srv)
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

// streamRoute is one streaming request: a name, a path and a body.
type streamRoute struct {
	name, path, body string
}

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

// postStream sends route and reads the whole stream; a stream must end
// cleanly, with any error inside it.
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

// The stream-end helpers below came with public dev's Anthropic Messages
// timeout tests (handlers_anthropic_timeout_test.go): a fake upstream that ends
// each route's stream as a test says, and a plugin that records what the
// post-plugins were given. The line keeps its own stream tests; its managed
// tests already name a type upstreamStream, so the fake upstream's events are a
// streamEndUpstream here.

// streamEndUpstream is what a fake upstream sends on one route: the events
// before the point where a test intervenes, the events that finish the
// stream, and the fragments a test cuts the stream at.
type streamEndUpstream struct {
	head, tail string
	midEvent   string // the head, then part of the next event
	inTerminal string // everything up to the middle of the terminal event
	errorEvent string // an error the upstream reports in its own stream
}

// chatUsageChunk is sent by an OpenAI-format upstream only when the request
// asks for it with stream_options.include_usage.
const chatUsageChunk = "data: {\"id\":\"c1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":\"gpt-4o\",\"choices\":[],\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":2,\"total_tokens\":7}}\n\n"

var streamEndUpstreams = map[string]streamEndUpstream{
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

// anthropicUpstreamRejection is the 429 body an Anthropic upstream sends.
const anthropicUpstreamRejection = `{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}`

type upstreamEnd int

const (
	upstreamFinishes            upstreamEnd = iota
	upstreamStalls                          // until the gateway gives up on it
	upstreamDrops                           // the connection, between events
	upstreamDropsMidEvent                   // the connection, inside an event
	upstreamDropsInTerminal                 // the connection, inside the last event
	upstreamSendsError                      // an error event, then closes
	upstreamTrickles                        // one chunk every few milliseconds, for ever
	upstreamRejects                         // with a 429, before any chunk
	upstreamStallsBeforeHeaders             // before its status line, until the gateway gives up on it
)

// streamingUpstream serves the stream for whichever route the gateway calls,
// ended as end says. It records the connection each stream arrived on.
func streamingUpstream(t *testing.T, end upstreamEnd) (*httptest.Server, *sync.Map) {
	t.Helper()
	conns := &sync.Map{} // path → *sync.Map of remote addresses; "include_usage <path>" → true
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var stream streamEndUpstream
		for suffix, s := range streamEndUpstreams {
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
			if strings.HasSuffix(r.URL.Path, "/messages") {
				io.WriteString(w, anthropicUpstreamRejection)
				return
			}
			io.WriteString(w, `{"error":{"message":"slow down","type":"rate_limit_error","code":"rate_limited"}}`)
			return
		}
		if end == upstreamStallsBeforeHeaders {
			// The server notices the gateway hang up only once the body is read.
			_, _ = io.Copy(io.Discard, r.Body)
			select {
			case <-r.Context().Done():
			case <-time.After(10 * time.Second):
			}
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

// The Anthropic Messages routes the timeout tests stream on.
var (
	nativeRoute     = streamRoute{"messages native", "/v1/messages", `{"model":"claude-sonnet-5-5","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"hi"}]}`}
	translatedRoute = streamRoute{"messages translated", "/v1/messages", `{"model":"gpt-4o","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"hi"}]}`}
)

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
	p.estimated.Store(rc.Metadata["usage_estimated"] == "true")
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
