package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

// A chat stream follows OpenAI's streaming contract. A client
// gets a chunk whose choices are empty only when it asked for usage
// (stream_options.include_usage): then exactly one, carrying the usage the
// gateway bills, right before [DONE]. Without it, every chunk has a choice,
// so the usual SDK loop over chunk.choices[0].delta.content reaches [DONE].
// x-agentcc-include-metadata is an opt-in too: that one chunk then carries
// agentcc_metadata. The gateway still asks the upstream for usage and meters
// every stream on it, whatever the client asked.
//
// A client that did not ask still gets the billed usage, on
// the last chunk with choices, the one carrying finish_reason (as Mistral's
// API sends it): the gateway holds each choice's finish chunk until the
// upstream ends, then writes it with the usage. Every other chunk goes out
// as it arrives. The published SDKs (@agentcc/client and agentcc 1.0.0)
// never ask for usage and read it from the stream.

const usageContractPrompt = `"messages":[{"role":"user","content":"hi"}]`

// usageContractUpstream answers a chat stream with its script and records the
// stream_options of each request it gets.
type usageContractUpstream struct {
	*httptest.Server
	mu   sync.Mutex
	opts []*models.StreamOptions
}

// streamScript is the data events an upstream sends for a chat stream. chunk
// is a data event with the stream's id, object, created and the request's
// model, followed by rest (its choices, its usage).
type streamScript func(chunk func(rest string) string) []string

// openAIUsageScript answers as OpenAI does when the request sets
// include_usage: a role chunk, two content chunks, a finish chunk, then a
// chunk with empty choices that carries the usage, and [DONE].
func openAIUsageScript(chunk func(rest string) string) []string {
	return []string{
		chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]`),
		chunk(`"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]`),
		chunk(`"choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]`),
		chunk(`"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`),
		chunk(`"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}`),
		"data: [DONE]",
	}
}

// startUsageContractUpstream answers every chat stream with
// openAIUsageScript: it sends the usage chunk whatever the request asked, as
// OpenRouter does.
func startUsageContractUpstream(t *testing.T) *usageContractUpstream {
	t.Helper()
	return startScriptedUpstream(t, openAIUsageScript)
}

func startScriptedUpstream(t *testing.T, script streamScript) *usageContractUpstream {
	t.Helper()
	u := &usageContractUpstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req models.ChatCompletionRequest
		if r.URL.Path != "/v1/chat/completions" || json.NewDecoder(r.Body).Decode(&req) != nil || !req.Stream {
			http.Error(w, "this upstream answers chat streams only", http.StatusBadRequest)
			return
		}
		u.mu.Lock()
		u.opts = append(u.opts, req.StreamOptions)
		u.mu.Unlock()
		writeSSE(w, script(scriptChunk(req.Model)))
	}))
	t.Cleanup(u.Close)
	return u
}

// scriptChunk is a streamScript's chunk for model.
func scriptChunk(model string) func(rest string) string {
	return func(rest string) string {
		return fmt.Sprintf(`data: {"id":"chatcmpl-uc","object":"chat.completion.chunk","created":1760000000,"model":%q,%s}`, model, rest)
	}
}

// lastIncludeUsage is whether the upstream's last request set include_usage.
func (u *usageContractUpstream) lastIncludeUsage(t *testing.T) bool {
	t.Helper()
	u.mu.Lock()
	defer u.mu.Unlock()
	if len(u.opts) == 0 {
		t.Fatal("the upstream got no request")
	}
	o := u.opts[len(u.opts)-1]
	return o != nil && o.IncludeUsage
}

// meteringProbe is a post-plugin that runs after cost: it records the usage
// and the cost each request's post-plugins got.
type meteringProbe struct {
	mu   sync.Mutex
	seen []meteredRequest
}

type meteredRequest struct{ usage, cost string }

func (p *meteringProbe) Name() string  { return "stream_metering_probe" }
func (p *meteringProbe) Priority() int { return 900 }
func (p *meteringProbe) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (p *meteringProbe) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	m := meteredRequest{cost: rc.Metadata["cost"]}
	if rc.Response != nil {
		u, _ := json.Marshal(rc.Response.Usage)
		m.usage = string(u)
	}
	p.mu.Lock()
	p.seen = append(p.seen, m)
	p.mu.Unlock()
	return pipeline.ResultContinue()
}

func (p *meteringProbe) last(t *testing.T) meteredRequest {
	t.Helper()
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.seen) == 0 {
		t.Fatal("the post-plugins never ran")
	}
	return p.seen[len(p.seen)-1]
}

// usageContractGateway: the test key, the cost plugin pricing gpt-4o from
// the bundled model database, the metering probe and the generation record.
type usageContractGateway struct {
	srv   *Server
	up    *usageContractUpstream
	probe *meteringProbe
	gens  chan *models.TraceSnapshot
}

func newUsageContractGateway(t *testing.T, extra ...pipeline.Plugin) *usageContractGateway {
	t.Helper()
	return newScriptedGateway(t, openAIUsageScript, extra...)
}

// newScriptedGateway is newUsageContractGateway with an upstream that answers
// every chat stream with script.
func newScriptedGateway(t *testing.T, script streamScript, extra ...pipeline.Plugin) *usageContractGateway {
	t.Helper()
	g := &usageContractGateway{up: startScriptedUpstream(t, script), probe: &meteringProbe{},
		gens: make(chan *models.TraceSnapshot, 8)}
	plugins := append([]pipeline.Plugin{costplugin.New(true, testModelDBPtr().Load, nil, nil), g.probe}, extra...)
	g.srv = createAuthRequiredTestServerWithPlugins(t, g.up.URL, plugins...)
	g.srv.handlers.SetGenerationRecorder(func(s *models.TraceSnapshot) { g.gens <- s })
	return g
}

func (g *usageContractGateway) post(t *testing.T, path, body string, headers map[string]string) (int, string) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer sk-test-key-001")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w.Code, w.Body.String()
}

func (g *usageContractGateway) generation(t *testing.T) *models.TraceSnapshot {
	t.Helper()
	select {
	case s := <-g.gens:
		return s
	case <-time.After(5 * time.Second):
		t.Fatal("no generation was recorded")
		return nil
	}
}

// streamOptionsCases are the request shapes of the tests: how the client
// set stream_options, and whether that asks for usage.
var streamOptionsCases = []struct {
	name, field string
	asked       bool
}{
	{"no stream_options", "", false},
	{"empty stream_options", `"stream_options":{},`, false},
	{"include_usage false", `"stream_options":{"include_usage":false},`, false},
	{"include_usage true", `"stream_options":{"include_usage":true},`, true},
}

func chatStreamBody(field string) string {
	return `{"model":"gpt-4o","stream":true,` + field + usageContractPrompt + `}`
}

func completionStreamBody(field string) string {
	return `{"model":"gpt-4o","stream":true,` + field + `"prompt":"hi"}`
}

// wireChunk is one data event of an OpenAI-format stream as a client parses
// it. Choices is nil when the event has no choices or "choices":null.
type wireChunk struct {
	raw     string
	done    bool
	isError bool
	Object  string `json:"object"`
	ID      string `json:"id"`
	Model   string `json:"model"`
	Choices []struct {
		Index        int     `json:"index"`
		Text         *string `json:"text"`
		FinishReason *string `json:"finish_reason"`
		Delta        struct {
			Content *string `json:"content"`
		} `json:"delta"`
	} `json:"choices"`
	Usage           *models.Usage                 `json:"usage"`
	AgentccMetadata *models.AgentccStreamMetadata `json:"agentcc_metadata"`
	Error           json.RawMessage               `json:"error"`
}

func parseWireStream(t *testing.T, body string) []wireChunk {
	t.Helper()
	var out []wireChunk
	for _, data := range sseEvents(body) {
		c := wireChunk{raw: data}
		if data == "[DONE]" {
			c.done = true
		} else if err := json.Unmarshal([]byte(data), &c); err != nil {
			t.Fatalf("a data event is not JSON: %q (%v)", data, err)
		}
		c.isError = len(c.Error) > 0 && string(c.Error) != "null"
		out = append(out, c)
	}
	return out
}

// emptyChoiceChunks is the positions of the chunks whose choices are empty
// or null, the chunks an SDK loop over choices[0] fails on.
func emptyChoiceChunks(chunks []wireChunk) []int {
	var at []int
	for i, c := range chunks {
		if !c.done && !c.isError && len(c.Choices) == 0 {
			at = append(at, i)
		}
	}
	return at
}

// streamedText is the text the stream's choices carry, chat or completions.
func streamedText(chunks []wireChunk) (text string, finished bool) {
	for _, c := range chunks {
		for _, ch := range c.Choices {
			if ch.Delta.Content != nil {
				text += *ch.Delta.Content
			}
			if ch.Text != nil {
				text += *ch.Text
			}
			if ch.FinishReason != nil && *ch.FinishReason == "stop" {
				finished = true
			}
		}
	}
	return text, finished
}

func assertEndsWithDone(t *testing.T, chunks []wireChunk, body string) {
	t.Helper()
	if len(chunks) == 0 || !chunks[len(chunks)-1].done {
		t.Fatalf("the stream does not end with [DONE]; body %s", body)
	}
}

// usageOnTheFinishChunk checks the contract for a client that did not ask
// for usage: no chunk has empty or null choices, and exactly one chunk
// carries usage, the last chunk with choices, which carries a finish_reason.
// It returns that chunk and its position.
func usageOnTheFinishChunk(t *testing.T, chunks []wireChunk, body string) (wireChunk, int) {
	t.Helper()
	if at := emptyChoiceChunks(chunks); len(at) != 0 {
		t.Errorf("chunks with empty or null choices at %v for a client that did not ask for usage; body %s", at, body)
	}
	last := -1
	var withUsage []int
	for i, c := range chunks {
		if c.done || c.isError {
			continue
		}
		if len(c.Choices) > 0 {
			last = i
		}
		if c.Usage != nil {
			withUsage = append(withUsage, i)
		}
	}
	if last < 0 {
		t.Fatalf("no chunk with choices; body %s", body)
	}
	if len(withUsage) != 1 || withUsage[0] != last {
		t.Fatalf("usage on the chunks at %v, want it on the last chunk with choices (%d) only; body %s", withUsage, last, body)
	}
	finished := false
	for _, ch := range chunks[last].Choices {
		finished = finished || (ch.FinishReason != nil && *ch.FinishReason != "")
	}
	if !finished {
		t.Errorf("the chunk with the usage carries no finish_reason: %s", chunks[last].raw)
	}
	return chunks[last], last
}

// usageIs reports whether u is prompt/completion/total.
func usageIs(u *models.Usage, prompt, completion, total int) bool {
	return u != nil && u.PromptTokens == prompt && u.CompletionTokens == completion && u.TotalTokens == total
}

// (1) A client that did not ask for usage gets no chunk with empty choices;
// the usage the gateway bills rides on the last chunk with choices, the
// finish chunk; its text, finish_reason and [DONE] are
// unchanged, and the upstream is still asked for usage.
func TestChatStreamUsage_NoEmptyChoicesChunkUnlessAsked(t *testing.T) {
	for _, tc := range streamOptionsCases {
		if tc.asked {
			continue
		}
		t.Run(tc.name, func(t *testing.T) {
			g := newUsageContractGateway(t)
			code, body := g.post(t, "/v1/chat/completions", chatStreamBody(tc.field), nil)
			if code != http.StatusOK {
				t.Fatalf("status %d; body %s", code, body)
			}
			chunks := parseWireStream(t, body)
			if at := emptyChoiceChunks(chunks); len(at) != 0 {
				t.Errorf("the client got %d chunk(s) with empty choices (at %v) without asking for usage; body %s", len(at), at, body)
			}
			assertEndsWithDone(t, chunks, body)
			if text, finished := streamedText(chunks); text != "Hello world" || !finished {
				t.Errorf("text %q finished %t, want %q and a stop; body %s", text, finished, "Hello world", body)
			}
			fin, _ := usageOnTheFinishChunk(t, chunks, body)
			if !usageIs(fin.Usage, 10, 20, 30) || fin.AgentccMetadata != nil {
				t.Errorf("the finish chunk is %s, want the billed usage 10/20/30 and no agentcc_metadata", fin.raw)
			}
			if billed, _ := json.Marshal(fin.Usage); string(billed) != g.probe.last(t).usage {
				t.Errorf("the finish chunk's usage %s, the post-plugins metered %s: want the same", billed, g.probe.last(t).usage)
			}
			if !g.up.lastIncludeUsage(t) {
				t.Error("the upstream was not asked for usage: the gateway meters every stream on it")
			}
		})
	}
}

// (2) A client that set include_usage gets exactly one chunk with empty
// choices, carrying the billed usage, right before [DONE]; the upstream's own
// usage chunk is not also forwarded.
func TestChatStreamUsage_IncludeUsageGetsOneUsageChunkBeforeDone(t *testing.T) {
	g := newUsageContractGateway(t)
	code, body := g.post(t, "/v1/chat/completions", chatStreamBody(`"stream_options":{"include_usage":true},`), nil)
	if code != http.StatusOK {
		t.Fatalf("status %d; body %s", code, body)
	}
	chunks := parseWireStream(t, body)
	assertEndsWithDone(t, chunks, body)
	at := emptyChoiceChunks(chunks)
	if len(at) != 1 || at[0] != len(chunks)-2 {
		t.Fatalf("chunks with empty choices at %v, want exactly one, right before [DONE] (%d); body %s", at, len(chunks)-2, body)
	}
	final := chunks[at[0]]
	if final.Choices == nil && !strings.Contains(final.raw, `"choices":[]`) {
		t.Errorf("the usage chunk's choices are %s, want []", final.raw)
	}
	if final.Usage == nil || final.Usage.PromptTokens != 10 || final.Usage.CompletionTokens != 20 || final.Usage.TotalTokens != 30 {
		t.Errorf("the usage chunk's usage = %+v, want 10/20/30", final.Usage)
	}
	if final.Object != "chat.completion.chunk" || final.ID != "chatcmpl-uc" || final.Model != "gpt-4o" {
		t.Errorf("the usage chunk is %s, want the stream's id, object and model", final.raw)
	}
	if final.AgentccMetadata != nil {
		t.Errorf("agentcc_metadata without x-agentcc-include-metadata: %s", final.raw)
	}
	if text, finished := streamedText(chunks); text != "Hello world" || !finished {
		t.Errorf("text %q finished %t, want %q and a stop", text, finished, "Hello world")
	}
}

// (3) x-agentcc-include-metadata alone is an opt-in: the client gets the one
// final chunk, with agentcc_metadata (cost and latency) and the usage, right
// before [DONE], and no other chunk with empty choices.
func TestChatStreamUsage_MetadataHeaderAloneGetsTheMetadataChunk(t *testing.T) {
	for _, value := range []string{"1", "true"} {
		t.Run(value, func(t *testing.T) {
			g := newUsageContractGateway(t, &metadataPostPlugin{})
			code, body := g.post(t, "/v1/chat/completions", chatStreamBody(""),
				map[string]string{"x-agentcc-include-metadata": value})
			if code != http.StatusOK {
				t.Fatalf("status %d; body %s", code, body)
			}
			chunks := parseWireStream(t, body)
			assertEndsWithDone(t, chunks, body)
			at := emptyChoiceChunks(chunks)
			if len(at) != 1 || at[0] != len(chunks)-2 {
				t.Fatalf("chunks with empty choices at %v, want exactly one, right before [DONE] (%d); body %s", at, len(chunks)-2, body)
			}
			final := chunks[at[0]]
			if final.AgentccMetadata == nil || final.AgentccMetadata.Cost != 0.0045 || !strings.Contains(final.raw, `"latency_ms":`) {
				t.Errorf("the final chunk's agentcc_metadata = %s, want cost 0.0045 and latency_ms", final.raw)
			}
			if final.Usage == nil || final.Usage.TotalTokens != 30 {
				t.Errorf("the final chunk's usage = %+v, want total_tokens 30", final.Usage)
			}
		})
	}
}

// (4) Control: metering does not depend on what
// the client asked. The post-plugins get the same usage and cost, and the
// generation record the same usage and cost, with or without include_usage
// or the metadata header.
func TestChatStreamUsage_MeteringIsTheSameWhateverTheClientAsked(t *testing.T) {
	type metered struct{ usage, cost, genUsage, genCost string }
	run := func(t *testing.T, field string, headers map[string]string) metered {
		g := newUsageContractGateway(t)
		code, body := g.post(t, "/v1/chat/completions", chatStreamBody(field), headers)
		if code != http.StatusOK {
			t.Fatalf("status %d; body %s", code, body)
		}
		m := g.probe.last(t)
		snap := g.generation(t)
		gen := metered{usage: m.usage, cost: m.cost, genCost: snap.Metadata["cost"]}
		if snap.Response != nil {
			u, _ := json.Marshal(snap.Response.Usage)
			gen.genUsage = string(u)
		}
		return gen
	}
	want := run(t, "", nil)
	if want.usage != `{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}` || want.cost == "" || want.genCost != want.cost || want.genUsage != want.usage {
		t.Fatalf("the reference stream metered %+v, want the upstream's usage 10/20/30 and a cost, the same in the generation record", want)
	}
	for _, tc := range streamOptionsCases[1:] {
		t.Run(tc.name, func(t *testing.T) {
			if got := run(t, tc.field, nil); got != want {
				t.Errorf("metered %+v, want %+v (no stream_options)", got, want)
			}
		})
	}
	t.Run("metadata header", func(t *testing.T) {
		if got := run(t, "", map[string]string{"x-agentcc-include-metadata": "1"}); got != want {
			t.Errorf("metered %+v, want %+v (no stream_options)", got, want)
		}
	})
}

// serveLeakThenUsageChunk answers a chat stream with postBlockLeak's answer,
// a finish chunk, then the upstream's usage chunk with empty choices.
func serveLeakThenUsageChunk(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/v1/chat/completions" {
		return
	}
	var req models.ChatCompletionRequest
	_ = json.NewDecoder(r.Body).Decode(&req)
	if !req.Stream {
		http.Error(w, "this upstream answers streams only", http.StatusBadRequest)
		return
	}
	leak, _ := json.Marshal(postBlockLeak)
	chunk := func(rest string) string {
		return `data: {"id":"c3","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o",` + rest + `}`
	}
	writeSSE(w, []string{
		chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":` + string(leak) + `}}]`),
		chunk(`"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`),
		chunk(`"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":9,"total_tokens":14}`),
		"data: [DONE]",
	})
}

// (5) A stream a guardrail blocks keeps its one error event and its ending.
// A blocking check holds the stream, so none of the blocked answer goes out:
// no chunk with choices, the finish chunk included. The final usage chunk
// follows the rule above: none unless the client asked; when it asked, one,
// after the error event and right before [DONE]. A stream the checker stops
// mid-way still ends at its error event.
func TestChatStreamUsage_GuardrailBlockKeepsItsErrorEvent(t *testing.T) {
	for _, tc := range []struct {
		name     string
		setup    postBlockSetup
		code     string
		withDone bool
	}{
		{"answer check at the end", postBlockSetup{staticRule: leakageCheck, capture: true}, "content_blocked", true},
		{"stream checker at the end", postBlockSetup{staticRule: leakageCheck,
			streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 1 << 20, FailureAction: "stop"}}, "stream_blocked", true},
		{"stream checker mid-stream", postBlockSetup{staticRule: leakageCheck,
			streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 5, FailureAction: "stop"}}, "stream_blocked", false},
	} {
		for _, asked := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/include_usage %t", tc.name, asked), func(t *testing.T) {
				setup := tc.setup
				setup.upstream = serveLeakThenUsageChunk
				g := newPostBlockGateway(t, setup)
				field := ""
				if asked {
					field = `"stream_options":{"include_usage":true},`
				}
				code, body := g.do(t, "/v1/chat/completions",
					fmt.Sprintf(`{"model":"gpt-4o","stream":true,%s"messages":[{"role":"user","content":%q}]}`, field, postBlockPrompt))
				if code != http.StatusOK {
					t.Fatalf("status %d, want the stream's 200; body %s", code, body)
				}
				_, errAt := assertStreamEndsWithGuardrailError(t, body, tc.code)
				chunks := parseWireStream(t, body)
				ended := len(chunks) > 0 && chunks[len(chunks)-1].done
				if ended != tc.withDone {
					t.Fatalf("the stream ends with [DONE]: %t, want %t; body %s", ended, tc.withDone, body)
				}
				at := emptyChoiceChunks(chunks)
				for i, c := range chunks {
					if !c.done && len(c.Choices) > 0 {
						t.Errorf("chunk %d carries choices of the blocked answer: %s", i, c.raw)
					}
				}
				switch {
				case !asked || !tc.withDone:
					if len(at) != 0 {
						t.Errorf("chunks with empty choices at %v, want none; body %s", at, body)
					}
				default:
					if len(at) != 1 || at[0] != len(chunks)-2 || at[0] < errAt {
						t.Errorf("chunks with empty choices at %v (error at %d), want one, after the error and right before [DONE]; body %s", at, errAt, body)
					}
				}
				assertBlockLogged(t, g.entry(t), "")
			})
		}
	}
}

// /v1/completions streams follow the same contract, in the legacy chunk
// shape: no chunk with empty choices unless the client asked for usage, and
// the usage on the last text chunk, the one with the finish_reason; when it
// asked, exactly one text_completion chunk with empty choices and the usage,
// right before [DONE].
func TestCompletionStreamUsage_FollowsTheSameContract(t *testing.T) {
	for _, tc := range streamOptionsCases {
		t.Run(tc.name, func(t *testing.T) {
			g := newUsageContractGateway(t)
			code, body := g.post(t, "/v1/completions", completionStreamBody(tc.field), nil)
			if code != http.StatusOK {
				t.Fatalf("status %d; body %s", code, body)
			}
			chunks := parseWireStream(t, body)
			assertEndsWithDone(t, chunks, body)
			if text, finished := streamedText(chunks); text != "Hello world" || !finished {
				t.Errorf("text %q finished %t, want %q and a stop; body %s", text, finished, "Hello world", body)
			}
			at := emptyChoiceChunks(chunks)
			if !tc.asked {
				fin, _ := usageOnTheFinishChunk(t, chunks, body)
				if fin.Object != "text_completion" || !usageIs(fin.Usage, 10, 20, 30) {
					t.Errorf("the last text chunk is %s, want a text_completion chunk with the usage 10/20/30", fin.raw)
				}
				return
			}
			if len(at) != 1 || at[0] != len(chunks)-2 {
				t.Fatalf("chunks with empty choices at %v, want exactly one, right before [DONE] (%d); body %s", at, len(chunks)-2, body)
			}
			final := chunks[at[0]]
			if final.Object != "text_completion" || final.Usage == nil || final.Usage.TotalTokens != 30 {
				t.Errorf("the usage chunk is %s, want a text_completion chunk with total_tokens 30", final.raw)
			}
			if m := g.probe.last(t); m.usage != `{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}` {
				t.Errorf("the post-plugins got usage %s", m.usage)
			}
		})
	}
}

// A managed stream is charged the same whether or not the client asked for
// usage. A client that asked gets one usage chunk, with the billed cost
// (upstream cost plus the markup), never the upstream's own usage chunk; a
// client that did not ask gets the same usage on the finish chunk.
func TestManagedStreamUsage_TheClientChoosesTheChunkNotTheCharge(t *testing.T) {
	drawn := map[bool]float64{}
	for _, asked := range []bool{false, true} {
		t.Run(fmt.Sprintf("include_usage %t", asked), func(t *testing.T) {
			g := newManagedTestGateway(t, managedOptIn())
			g.ledger.SetMicros("org-m", 1_000_000)
			field := ""
			if asked {
				field = `"stream_options":{"include_usage":true},`
			}
			w := g.chat(t, `{"model":"anthropic/claude-sonnet-5.5","stream":true,`+field+`"messages":[{"role":"user","content":"hi"}]}`, nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d; body %s", w.Code, w.Body)
			}
			drawn[asked] = 1 - g.ledger.BalanceUSD("org-m")
			body := w.Body.String()
			chunks := parseWireStream(t, body)
			assertEndsWithDone(t, chunks, body)
			at := emptyChoiceChunks(chunks)
			if !asked {
				if fin, _ := usageOnTheFinishChunk(t, chunks, body); fin.Usage.Cost == nil || fmt.Sprintf("%.6f", *fin.Usage.Cost) != "0.007350" {
					t.Errorf("the finish chunk is %s, want the billed cost 0.00735", fin.raw)
				}
			} else {
				if len(at) != 1 || at[0] != len(chunks)-2 {
					t.Fatalf("chunks with empty choices at %v, want exactly one, right before [DONE]; body %s", at, body)
				}
				if u := chunks[at[0]].Usage; u == nil || u.Cost == nil || fmt.Sprintf("%.6f", *u.Cost) != "0.007350" {
					t.Errorf("the usage chunk is %s, want the billed cost 0.00735", chunks[at[0]].raw)
				}
			}
		})
	}
	if fmt.Sprintf("%.6f", drawn[false]) != fmt.Sprintf("%.6f", drawn[true]) || drawn[false] <= 0 {
		t.Errorf("charged %.6f without include_usage and %.6f with it, want the same, above zero", drawn[false], drawn[true])
	}
}

// x-agentcc-include-metadata asks for the final chunk on /v1/completions
// as on chat: one text_completion chunk with empty choices, the billed usage
// and agentcc_metadata (cost and latency), right before [DONE], and no other
// chunk with empty choices.
func TestCompletionStreamUsage_MetadataHeaderGetsTheMetadataChunk(t *testing.T) {
	for _, value := range []string{"1", "true"} {
		t.Run(value, func(t *testing.T) {
			g := newUsageContractGateway(t, &metadataPostPlugin{})
			code, body := g.post(t, "/v1/completions", completionStreamBody(""),
				map[string]string{"x-agentcc-include-metadata": value})
			if code != http.StatusOK {
				t.Fatalf("status %d; body %s", code, body)
			}
			chunks := parseWireStream(t, body)
			assertEndsWithDone(t, chunks, body)
			at := emptyChoiceChunks(chunks)
			if len(at) != 1 || at[0] != len(chunks)-2 {
				t.Fatalf("chunks with empty choices at %v, want exactly one, right before [DONE] (%d); body %s", at, len(chunks)-2, body)
			}
			final := chunks[at[0]]
			if final.Object != "text_completion" || !usageIs(final.Usage, 10, 20, 30) {
				t.Errorf("the final chunk is %s, want a text_completion chunk with the usage 10/20/30", final.raw)
			}
			if final.AgentccMetadata == nil || final.AgentccMetadata.Cost != 0.0045 || !strings.Contains(final.raw, `"latency_ms":`) {
				t.Errorf("the final chunk's agentcc_metadata = %s, want cost 0.0045 and latency_ms", final.raw)
			}
		})
	}
}

// A managed stream whose upstream sent no usage is billed on the
// gateway's estimate, and that estimate, with the billed cost, is the usage
// the client sees: on the one usage chunk when it asked, on the finish chunk
// when it did not.
func TestManagedStreamUsage_UpstreamWithoutUsageShowsTheEstimate(t *testing.T) {
	for _, asked := range []bool{false, true} {
		t.Run(fmt.Sprintf("include_usage %t", asked), func(t *testing.T) {
			g := newManagedTestGateway(t, managedOptIn())
			g.ledger.SetMicros("org-m", 1_000_000)
			g.managed.noUsage.Store(true)
			field := ""
			if asked {
				field = `"stream_options":{"include_usage":true},`
			}
			w := g.chat(t, `{"model":"anthropic/claude-sonnet-5.5","stream":true,`+field+`"messages":[{"role":"user","content":"hi"}]}`, nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d; body %s", w.Code, w.Body)
			}
			drawn := 1 - g.ledger.BalanceUSD("org-m")
			body := w.Body.String()
			chunks := parseWireStream(t, body)
			assertEndsWithDone(t, chunks, body)
			var u *models.Usage
			if asked {
				at := emptyChoiceChunks(chunks)
				if len(at) != 1 || at[0] != len(chunks)-2 {
					t.Fatalf("chunks with empty choices at %v, want exactly one, right before [DONE]; body %s", at, body)
				}
				u = chunks[at[0]].Usage
			} else {
				fin, _ := usageOnTheFinishChunk(t, chunks, body)
				u = fin.Usage
			}
			// The estimate: the prompt from the messages, the output at about
			// four characters a token ("hi" is one).
			if u == nil || u.PromptTokens <= 0 || u.CompletionTokens != 1 || u.TotalTokens != u.PromptTokens+1 {
				t.Fatalf("the client's usage is %+v, want the managed estimate; body %s", u, body)
			}
			if drawn <= 0 || u.Cost == nil || *u.Cost-drawn > 1e-6 || drawn-*u.Cost > 1e-6 {
				t.Errorf("the client's usage cost %v, the org was charged %.6f: want the billed cost", u.Cost, drawn)
			}
		})
	}
}
