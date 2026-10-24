package server

import (
	"bufio"
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
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

// A client that did not ask for usage gets it on the last chunk with
// choices, the one carrying finish_reason. The gateway
// holds each choice's finish chunk until the upstream ends and writes it
// then, with the billed usage; it delays no other chunk. These tests cover
// the shapes around that hold: several choices, a disclaimer, a deadline
// cut, the timing of the chunks, and upstream chunks without a choice.

// With n above 1 each choice's finish chunk is held and written, in the order
// it came, once the upstream ends; the usage rides on the last one written.
// The other choice's text that arrives meanwhile is not held.
func TestChatStreamUsage_EveryChoiceFinishesAndTheLastCarriesTheUsage(t *testing.T) {
	twoChoices := func(chunk func(string) string) []string {
		return []string{
			chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]`),
			chunk(`"choices":[{"index":1,"delta":{"role":"assistant","content":"Bonjour"},"finish_reason":null}]`),
			chunk(`"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`),
			chunk(`"choices":[{"index":1,"delta":{"content":" monde"},"finish_reason":null}]`),
			chunk(`"choices":[{"index":1,"delta":{},"finish_reason":"length"}]`),
			chunk(`"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}`),
			"data: [DONE]",
		}
	}
	for _, asked := range []bool{false, true} {
		t.Run(fmt.Sprintf("include_usage %t", asked), func(t *testing.T) {
			g := newScriptedGateway(t, twoChoices)
			field := ""
			if asked {
				field = `"stream_options":{"include_usage":true},`
			}
			code, body := g.post(t, "/v1/chat/completions", `{"model":"gpt-4o","stream":true,"n":2,`+field+usageContractPrompt+`}`, nil)
			if code != http.StatusOK {
				t.Fatalf("status %d; body %s", code, body)
			}
			chunks := parseWireStream(t, body)
			assertEndsWithDone(t, chunks, body)
			// Where each event of the upstream's answer landed.
			var order []string
			text := map[int]string{}
			for _, c := range chunks {
				for _, ch := range c.Choices {
					if ch.Delta.Content != nil {
						text[ch.Index] += *ch.Delta.Content
						order = append(order, fmt.Sprintf("%d:%s", ch.Index, *ch.Delta.Content))
					}
					if ch.FinishReason != nil {
						order = append(order, fmt.Sprintf("%d:%s", ch.Index, *ch.FinishReason))
					}
				}
			}
			if text[0] != "Hello" || text[1] != "Bonjour monde" {
				t.Errorf("text %q, want each choice's text whole; body %s", text, body)
			}
			want := "0:Hello 1:Bonjour 0:stop 1: monde 1:length"
			if !asked {
				want = "0:Hello 1:Bonjour 1: monde 0:stop 1:length"
				fin, _ := usageOnTheFinishChunk(t, chunks, body)
				if len(fin.Choices) != 1 || fin.Choices[0].Index != 1 || !usageIs(fin.Usage, 10, 20, 30) {
					t.Errorf("the chunk with the usage is %s, want choice 1's finish chunk with 10/20/30", fin.raw)
				}
			}
			if got := strings.Join(order, " "); got != want {
				t.Errorf("the client got %q, want %q; body %s", got, want, body)
			}
		})
	}
}

// A disclaimer the streaming checker adds at the end goes out as soon as the
// upstream ends. For a client that did not ask for usage the held
// finish chunk follows it, with the usage, so the last chunk with choices is
// still the one carrying finish_reason. A client that asked gets the
// finish chunk, the disclaimer, the usage chunk and [DONE].
func TestChatStreamUsage_DisclaimerThenTheFinishChunk(t *testing.T) {
	for _, asked := range []bool{false, true} {
		t.Run(fmt.Sprintf("include_usage %t", asked), func(t *testing.T) {
			g := newPostBlockGateway(t, postBlockSetup{staticRule: leakageCheck, upstream: serveLeakThenUsageChunk,
				streaming: &config.StreamingGuardrailConfig{Enabled: true, CheckInterval: 1 << 20, FailureAction: "disclaimer"}})
			field := ""
			if asked {
				field = `"stream_options":{"include_usage":true},`
			}
			code, body := g.do(t, "/v1/chat/completions",
				fmt.Sprintf(`{"model":"gpt-4o","stream":true,%s"messages":[{"role":"user","content":%q}]}`, field, postBlockPrompt))
			if code != http.StatusOK {
				t.Fatalf("status %d, want the stream's 200; body %s", code, body)
			}
			chunks := parseWireStream(t, body)
			assertEndsWithDone(t, chunks, body)
			disclaimerAt, finishAt := -1, -1
			for i, c := range chunks {
				if c.isError {
					t.Errorf("a disclaimer-mode stream got an error event: %s", c.raw)
				}
				for _, ch := range c.Choices {
					if ch.Delta.Content != nil && strings.Contains(*ch.Delta.Content, "Content warning") {
						disclaimerAt = i
					}
					if ch.FinishReason != nil {
						finishAt = i
					}
				}
			}
			if disclaimerAt < 0 || finishAt < 0 {
				t.Fatalf("disclaimer at %d, finish chunk at %d; body %s", disclaimerAt, finishAt, body)
			}
			if !asked {
				fin, at := usageOnTheFinishChunk(t, chunks, body)
				if at != finishAt || at < disclaimerAt || at != len(chunks)-2 || !usageIs(fin.Usage, 5, 9, 14) {
					t.Errorf("finish chunk at %d (disclaimer at %d) is %s, want it after the disclaimer, right before [DONE], with 5/9/14", at, disclaimerAt, fin.raw)
				}
				return
			}
			at := emptyChoiceChunks(chunks)
			if finishAt > disclaimerAt || len(at) != 1 || at[0] != len(chunks)-2 || !usageIs(chunks[at[0]].Usage, 5, 9, 14) {
				t.Errorf("finish at %d, disclaimer at %d, usage chunks at %v: want finish, disclaimer, one usage chunk, [DONE]; body %s",
					finishAt, disclaimerAt, at, body)
			}
		})
	}
}

// startStallingUpstream answers a chat stream with before, then waits until
// release is closed (or the request ends), then sends after.
func startStallingUpstream(t *testing.T, before, after streamScript, release <-chan struct{}) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req models.ChatCompletionRequest
		if r.URL.Path != "/v1/chat/completions" || json.NewDecoder(r.Body).Decode(&req) != nil || !req.Stream {
			http.Error(w, "this upstream answers chat streams only", http.StatusBadRequest)
			return
		}
		writeSSE(w, before(scriptChunk(req.Model)))
		select {
		case <-release:
		case <-r.Context().Done():
			return
		case <-time.After(10 * time.Second):
		}
		if after != nil {
			writeSSE(w, after(scriptChunk(req.Model)))
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

// A stream the deadline cuts after the upstream's finish chunk: a client
// that did not ask gets the held finish chunk before the gateway_timeout
// error event, in its place in the stream, with the usage the upstream had
// sent.
// The stream still ends at the error event, without [DONE].
func TestChatStreamUsage_DeadlineCutWritesTheHeldFinishChunkFirst(t *testing.T) {
	before := func(chunk func(string) string) []string { return openAIUsageScript(chunk)[:5] } // up to the usage chunk
	for _, asked := range []bool{false, true} {
		t.Run(fmt.Sprintf("include_usage %t", asked), func(t *testing.T) {
			up := startStallingUpstream(t, before, nil, nil)
			ht, _ := deadlineServer(t, up.URL)
			field := ""
			if asked {
				field = `"stream_options":{"include_usage":true},`
			}
			w := ht.chat(t, chatStreamBody(field), "x-agentcc-deadline-ms", "400")
			body := w.Body.String()
			chunks := parseWireStream(t, body)
			if len(chunks) == 0 || !chunks[len(chunks)-1].isError || !strings.Contains(chunks[len(chunks)-1].raw, `"gateway_timeout"`) {
				t.Fatalf("want the stream to end with the gateway_timeout error event and no [DONE]; body %s", body)
			}
			errAt := len(chunks) - 1
			if text, finished := streamedText(chunks); text != "Hello world" || !finished {
				t.Errorf("text %q finished %t, want %q and the finish chunk before the error; body %s", text, finished, "Hello world", body)
			}
			if !asked {
				fin, at := usageOnTheFinishChunk(t, chunks, body)
				if at != errAt-1 || !usageIs(fin.Usage, 10, 20, 30) {
					t.Errorf("finish chunk at %d (error at %d) is %s, want it right before the error with 10/20/30", at, errAt, fin.raw)
				}
				return
			}
			if at := emptyChoiceChunks(chunks); len(at) != 0 {
				t.Errorf("a cut stream got chunks with empty choices at %v; body %s", at, body)
			}
		})
	}
}

// Never delay any other chunk: the content reaches the client while the
// upstream is still open, and only the finish chunk waits for the upstream's
// end (its usage). A second finish chunk for the same choice (an upstream
// that sends finish_reason on every chunk) releases the first, so the hold
// never grows past one chunk per choice. Real HTTP on both sides.
func TestChatStreamUsage_OnlyTheFinishChunkWaitsForTheUpstreamEnd(t *testing.T) {
	usage := func(chunk func(string) string) []string {
		return []string{chunk(`"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}`), "data: [DONE]"}
	}
	for _, tc := range []struct {
		name       string
		before     streamScript
		early      []string // the content the client gets while the upstream is open
		lateText   string   // the held chunk's text
		lateFinish string
	}{
		{"content then a finish chunk", func(chunk func(string) string) []string { return openAIUsageScript(chunk)[:4] },
			[]string{"", "Hello", " world"}, "", "stop"},
		{"finish_reason on every chunk", func(chunk func(string) string) []string {
			return []string{
				chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":"Hel"},"finish_reason":"stop"}]`),
				chunk(`"choices":[{"index":0,"delta":{"content":"lo"},"finish_reason":"stop"}]`),
			}
		}, []string{"Hel"}, "lo", "stop"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			release := make(chan struct{})
			var once sync.Once
			free := func() { once.Do(func() { close(release) }) }
			t.Cleanup(free)
			up := startStallingUpstream(t, tc.before, usage, release)
			srv := createAuthRequiredTestServerWithPlugins(t, up.URL, costplugin.New(true, testModelDBPtr().Load, nil, nil))
			gw := httptest.NewServer(srv.httpServer.Handler)
			t.Cleanup(gw.Close)

			req, _ := http.NewRequest(http.MethodPost, gw.URL+"/v1/chat/completions", strings.NewReader(chatStreamBody("")))
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer sk-test-key-001")
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			events := make(chan string, 32)
			go func() {
				defer close(events)
				sc := bufio.NewScanner(resp.Body)
				for sc.Scan() {
					if data, ok := strings.CutPrefix(sc.Text(), "data: "); ok {
						events <- data
					}
				}
			}()
			next := func(wait time.Duration) (string, bool) {
				select {
				case e, ok := <-events:
					return e, ok
				case <-time.After(wait):
					return "", false
				}
			}
			content := func(data string) (text, finish string) {
				var c wireChunk
				_ = json.Unmarshal([]byte(data), &c)
				for _, ch := range c.Choices {
					if ch.Delta.Content != nil {
						text += *ch.Delta.Content
					}
					if ch.FinishReason != nil {
						finish = *ch.FinishReason
					}
				}
				return text, finish
			}

			for i, want := range tc.early {
				e, ok := next(5 * time.Second)
				if !ok {
					t.Fatalf("chunk %d (%q) did not arrive while the upstream was open", i, want)
				}
				if text, _ := content(e); text != want {
					t.Fatalf("chunk %d is %s, want the text %q", i, e, want)
				}
			}
			if e, ok := next(300 * time.Millisecond); ok {
				t.Fatalf("the gateway sent %s while the upstream was open; the finish chunk must wait for the upstream's end", e)
			}

			free()
			e, ok := next(5 * time.Second)
			if !ok {
				t.Fatal("the held finish chunk never arrived")
			}
			var fin wireChunk
			_ = json.Unmarshal([]byte(e), &fin)
			if text, finish := content(e); text != tc.lateText || finish != tc.lateFinish || !usageIs(fin.Usage, 10, 20, 30) {
				t.Errorf("after the upstream ended the client got %s, want the finish chunk (%q, %s) with 10/20/30", e, tc.lateText, tc.lateFinish)
			}
			if e, ok := next(5 * time.Second); !ok || e != "[DONE]" {
				t.Errorf("then %q, want [DONE]", e)
			}
		})
	}
}

// Upstream chunks without a choice never reach the client: Azure's first
// chunk (prompt_filter_results, empty choices), which takes handleStream's
// first-chunk path, and a usage chunk with "choices":null. The usage they
// carry goes where the client's
// request puts it: on the finish chunk, or on the one final chunk the
// client asked for, on chat and on /v1/completions alike.
func TestChatStreamUsage_ChoicelessFirstChunkAndNullChoices(t *testing.T) {
	azure := func(chunk func(string) string) []string {
		return []string{
			`data: {"id":"","object":"","created":0,"model":"","choices":[],"prompt_filter_results":[{"prompt_index":0,"content_filter_results":{}}]}`,
			chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]`),
			chunk(`"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]`),
			chunk(`"choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]`),
			chunk(`"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`),
			chunk(`"choices":null,"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}`),
			"data: [DONE]",
		}
	}
	metadata := map[string]string{"x-agentcc-include-metadata": "1"}
	for _, tc := range []struct {
		name, path, field string
		headers           map[string]string
		final             bool // the client asked for the one final chunk
	}{
		{"chat no stream_options", "/v1/chat/completions", "", nil, false},
		{"chat include_usage true", "/v1/chat/completions", `"stream_options":{"include_usage":true},`, nil, true},
		{"chat metadata header only", "/v1/chat/completions", "", metadata, true},
		{"completions no stream_options", "/v1/completions", "", nil, false},
		{"completions include_usage true", "/v1/completions", `"stream_options":{"include_usage":true},`, nil, true},
		{"completions metadata header only", "/v1/completions", "", metadata, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newScriptedGateway(t, azure, &metadataPostPlugin{})
			body := chatStreamBody(tc.field)
			if tc.path == "/v1/completions" {
				body = completionStreamBody(tc.field)
			}
			code, out := g.post(t, tc.path, body, tc.headers)
			if code != http.StatusOK {
				t.Fatalf("status %d; body %s", code, out)
			}
			chunks := parseWireStream(t, out)
			assertEndsWithDone(t, chunks, out)
			if text, finished := streamedText(chunks); text != "Hello world" || !finished {
				t.Errorf("text %q finished %t, want %q and a stop; body %s", text, finished, "Hello world", out)
			}
			if strings.Contains(out, "prompt_filter_results") {
				t.Errorf("the choice-less first chunk reached the client; body %s", out)
			}
			if !tc.final {
				if fin, _ := usageOnTheFinishChunk(t, chunks, out); !usageIs(fin.Usage, 10, 20, 30) {
					t.Errorf("the finish chunk is %s, want the usage 10/20/30", fin.raw)
				}
				return
			}
			at := emptyChoiceChunks(chunks)
			if len(at) != 1 || at[0] != len(chunks)-2 {
				t.Fatalf("chunks with empty or null choices at %v, want exactly one right before [DONE] (%d); body %s", at, len(chunks)-2, out)
			}
			final := chunks[at[0]]
			if !usageIs(final.Usage, 10, 20, 30) || (tc.headers != nil) != (final.AgentccMetadata != nil) {
				t.Errorf("the final chunk is %s, want the usage 10/20/30, with agentcc_metadata only for the header", final.raw)
			}
		})
	}
}

// A provider's usage on a chunk that has choices reaches the client once, on
// the chunk the gateway puts the billed usage on: the finish chunk when the
// client did not ask, the one final chunk with empty choices when it did.
// The shapes: Bedrock Converse (the finish chunk, then a metadata chunk with
// one empty choice and the usage), a finish chunk that carries the usage
// (the Anthropic adapter), and usage on every chunk (Gemini's cumulative
// usageMetadata). The post-plugins meter the provider's last usage.
func TestChatStreamUsage_ProviderUsageOnAChoiceChunkGoesOutOnce(t *testing.T) {
	usage := func(completion, total int) string {
		return fmt.Sprintf(`"usage":{"prompt_tokens":10,"completion_tokens":%d,"total_tokens":%d}`, completion, total)
	}
	scripts := []struct {
		name   string
		script streamScript
	}{
		{"converse metadata chunk", func(chunk func(string) string) []string {
			return []string{
				chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]`),
				chunk(`"choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]`),
				chunk(`"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`),
				chunk(`"choices":[{"index":0,"delta":{}}],` + usage(20, 30)),
				"data: [DONE]",
			}
		}},
		{"usage on the finish chunk", func(chunk func(string) string) []string {
			return []string{
				chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]`),
				chunk(`"choices":[{"index":0,"delta":{"content":" world"},"finish_reason":"stop"}],` + usage(20, 30)),
				"data: [DONE]",
			}
		}},
		{"usage on every chunk", func(chunk func(string) string) []string {
			return []string{
				chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}],` + usage(5, 15)),
				chunk(`"choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}],` + usage(12, 22)),
				chunk(`"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],` + usage(20, 30)),
				"data: [DONE]",
			}
		}},
	}
	metadata := map[string]string{"x-agentcc-include-metadata": "1"}
	for _, s := range scripts {
		for _, tc := range []struct {
			name, path, field string
			headers           map[string]string
			final             bool // the client asked for the one final chunk
		}{
			{"chat not asked", "/v1/chat/completions", "", nil, false},
			{"chat include_usage", "/v1/chat/completions", `"stream_options":{"include_usage":true},`, nil, true},
			{"chat metadata header", "/v1/chat/completions", "", metadata, true},
			{"completions not asked", "/v1/completions", "", nil, false},
			{"completions include_usage", "/v1/completions", `"stream_options":{"include_usage":true},`, nil, true},
			{"completions metadata header", "/v1/completions", "", metadata, true},
		} {
			t.Run(s.name+"/"+tc.name, func(t *testing.T) {
				g := newScriptedGateway(t, s.script)
				body := chatStreamBody(tc.field)
				if tc.path == "/v1/completions" {
					body = completionStreamBody(tc.field)
				}
				code, out := g.post(t, tc.path, body, tc.headers)
				if code != http.StatusOK {
					t.Fatalf("status %d; body %s", code, out)
				}
				chunks := parseWireStream(t, out)
				assertEndsWithDone(t, chunks, out)
				if text, finished := streamedText(chunks); text != "Hello world" || !finished {
					t.Errorf("text %q finished %t, want %q and a stop; body %s", text, finished, "Hello world", out)
				}
				var withUsage []int
				for i, c := range chunks {
					if c.Usage != nil {
						withUsage = append(withUsage, i)
					}
				}
				if len(withUsage) != 1 {
					t.Fatalf("usage on the chunks at %v, want it on exactly one; body %s", withUsage, out)
				}
				if !tc.final {
					if fin, _ := usageOnTheFinishChunk(t, chunks, out); !usageIs(fin.Usage, 10, 20, 30) {
						t.Errorf("the finish chunk is %s, want the usage 10/20/30", fin.raw)
					}
				} else {
					at := emptyChoiceChunks(chunks)
					if len(at) != 1 || at[0] != len(chunks)-2 || withUsage[0] != at[0] || !usageIs(chunks[at[0]].Usage, 10, 20, 30) {
						t.Errorf("chunks with empty choices at %v, usage at %v: want the usage 10/20/30 only on the one final chunk, right before [DONE] (%d); body %s",
							at, withUsage, len(chunks)-2, out)
					}
				}
				if m := g.probe.last(t); m.usage != `{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}` {
					t.Errorf("the post-plugins metered %s, want the provider's last usage 10/20/30", m.usage)
				}
			})
		}
	}
}

// A server-side timeout (x-agentcc-request-timeout) that ends the request
// after the provider's finish chunk, while the provider stalls before its
// usage chunk, still gets the finish chunk to the client, which is still
// reading: a held finish chunk is written when the request's context ends.
// The post-plugins run once. On chat and on /v1/completions; a client that
// asked holds nothing (control).
func TestChatStreamUsage_RequestTimeoutStillSendsTheHeldFinishChunk(t *testing.T) {
	before := func(chunk func(string) string) []string { return openAIUsageScript(chunk)[:4] } // up to the finish chunk
	for _, path := range []string{"/v1/chat/completions", "/v1/completions"} {
		for _, asked := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s include_usage %t", path, asked), func(t *testing.T) {
				up := startStallingUpstream(t, before, nil, nil)
				field := ""
				if asked {
					field = `"stream_options":{"include_usage":true},`
				}
				body := chatStreamBody(field)
				if path == "/v1/completions" {
					body = completionStreamBody(field)
				}
				lost := 0
				const runs = 5
				for i := 0; i < runs; i++ {
					probe := &meteringProbe{}
					g := &usageContractGateway{probe: probe,
						srv: createAuthRequiredTestServerWithPlugins(t, up.URL, costplugin.New(true, testModelDBPtr().Load, nil, nil), probe)}
					code, out := g.post(t, path, body, map[string]string{"x-agentcc-request-timeout": "500"})
					if code != http.StatusOK {
						t.Fatalf("status %d; body %s", code, out)
					}
					chunks := parseWireStream(t, out)
					text, finished := streamedText(chunks)
					if text != "Hello world" {
						t.Errorf("text %q, want %q; body %s", text, "Hello world", out)
					}
					if !finished {
						lost++
						t.Logf("run %d: no finish chunk; body %s", i, out)
					}
					if at := emptyChoiceChunks(chunks); len(at) != 0 {
						t.Errorf("chunks with empty choices at %v in a stream the timeout ended; body %s", at, out)
					}
					probe.mu.Lock()
					ran := len(probe.seen)
					probe.mu.Unlock()
					if ran != 1 {
						t.Errorf("the post-plugins ran %d times, want once", ran)
					}
				}
				if lost > 0 {
					t.Errorf("the finish chunk was lost in %d of %d streams the request timeout ended", lost, runs)
				}
			})
		}
	}
}

// A managed key whose provider puts the usage, with its own charge (cost,
// is_byok, cost_details), on a chunk that has choices gets the usage once,
// with the billed cost and none of the provider's charge. The shapes:
// OpenRouter's usage chunk, Bedrock Converse's metadata chunk (one empty
// choice and the usage, after the finish chunk), a finish chunk that carries
// the usage, and usage on every chunk. A client that did not ask gets the
// usage on the finish chunk, right before [DONE]; a client that asked gets
// it only on the one final chunk with empty choices. The wallet draw is the
// same for every shape, either way.
func TestChatStreamUsage_ManagedUsageOnChoiceChunks(t *testing.T) {
	u := func(completion, total int, cost float64) string {
		return fmt.Sprintf(`"usage":{"prompt_tokens":1000,"completion_tokens":%d,"total_tokens":%d,"cost":%g,"is_byok":false,"cost_details":{"upstream_inference_cost":%g}}`,
			completion, total, cost, cost)
	}
	shapes := []struct {
		name   string
		script streamScript
	}{
		{"openrouter usage chunk", func(chunk func(string) string) []string {
			return []string{
				chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":"hi"}}]`),
				chunk(`"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`),
				chunk(`"choices":[],` + u(500, 1500, upstreamCost)),
				"data: [DONE]",
			}
		}},
		{"converse metadata chunk", func(chunk func(string) string) []string {
			return []string{
				chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":"hi"}}]`),
				chunk(`"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`),
				chunk(`"choices":[{"index":0,"delta":{}}],` + u(500, 1500, upstreamCost)),
				"data: [DONE]",
			}
		}},
		{"usage on the finish chunk", func(chunk func(string) string) []string {
			return []string{
				chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":"hi"}}]`),
				chunk(`"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],` + u(500, 1500, upstreamCost)),
				"data: [DONE]",
			}
		}},
		{"usage on every chunk", func(chunk func(string) string) []string {
			return []string{
				chunk(`"choices":[{"index":0,"delta":{"role":"assistant","content":"hi"}}],` + u(250, 1250, 0.0045)),
				chunk(`"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],` + u(500, 1500, upstreamCost)),
				"data: [DONE]",
			}
		}},
	}
	drawn := map[string]string{}
	for _, s := range shapes {
		for _, asked := range []bool{false, true} {
			key := fmt.Sprintf("%s/include_usage %t", s.name, asked)
			t.Run(key, func(t *testing.T) {
				g := newManagedTestGateway(t, managedOptIn())
				g.ledger.SetMicros("org-m", 1_000_000)
				script := s.script
				g.managed.stream.Store(&script)
				field := ""
				if asked {
					field = `"stream_options":{"include_usage":true},`
				}
				w := g.chat(t, `{"model":"anthropic/claude-sonnet-5.5","stream":true,`+field+`"messages":[{"role":"user","content":"hi"}]}`, nil)
				if w.Code != http.StatusOK {
					t.Fatalf("status = %d; body %s", w.Code, w.Body)
				}
				body := w.Body.String()
				drawn[key] = fmt.Sprintf("%.6f", 1-g.ledger.BalanceUSD("org-m"))
				chunks := parseWireStream(t, body)
				assertEndsWithDone(t, chunks, body)
				for _, field := range []string{`"is_byok"`, `"cost_details"`, `"upstream_inference_cost"`} {
					if strings.Contains(body, field) {
						t.Errorf("the stream carries the provider's %s; body %s", field, body)
					}
				}
				var withUsage []int
				for i, c := range chunks {
					if c.Usage != nil {
						withUsage = append(withUsage, i)
					}
				}
				if len(withUsage) != 1 {
					t.Fatalf("usage on the chunks at %v, want exactly one; body %s", withUsage, body)
				}
				got := chunks[withUsage[0]].Usage
				if !usageIs(got, 1000, 500, 1500) || got.Cost == nil || fmt.Sprintf("%.6f", *got.Cost) != "0.007350" {
					t.Errorf("the usage is %s, want 1000/500/1500 with the billed cost 0.00735", chunks[withUsage[0]].raw)
				}
				if !asked {
					if _, at := usageOnTheFinishChunk(t, chunks, body); at != len(chunks)-2 {
						t.Errorf("the finish chunk with the usage is at %d, want right before [DONE] (%d); body %s", at, len(chunks)-2, body)
					}
				} else if at := emptyChoiceChunks(chunks); len(at) != 1 || at[0] != len(chunks)-2 || withUsage[0] != at[0] {
					t.Errorf("chunks with empty choices at %v, usage at %v: want the usage only on the one final chunk, right before [DONE]; body %s", at, withUsage, body)
				}
			})
		}
	}
	want := drawn["openrouter usage chunk/include_usage false"]
	for k, v := range drawn {
		if v != want || v == "0.000000" {
			t.Errorf("%s drew %s, want %s (the OpenRouter shape), above zero", k, v, want)
		}
	}
}

// A server-side timeout (x-agentcc-request-timeout) that ends the request
// after the provider's usage chunk, while the provider stalls before [DONE],
// still gets the held finish chunk to a client that did not ask, with that
// usage, as the last chunk; the stream ends there without [DONE] or an error
// event and with no chunk with empty choices. A client that asked gets no
// usage on such a stream: the one final chunk is not written. The
// post-plugins run once. On chat and on /v1/completions, 5 streams each.
func TestChatStreamUsage_RequestTimeoutAfterTheUsageChunk(t *testing.T) {
	before := func(chunk func(string) string) []string { return openAIUsageScript(chunk)[:5] } // the finish chunk, the usage chunk, no [DONE]
	for _, path := range []string{"/v1/chat/completions", "/v1/completions"} {
		for _, asked := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s include_usage %t", path, asked), func(t *testing.T) {
				up := startStallingUpstream(t, before, nil, nil)
				field := ""
				if asked {
					field = `"stream_options":{"include_usage":true},`
				}
				body := chatStreamBody(field)
				if path == "/v1/completions" {
					body = completionStreamBody(field)
				}
				for i := 0; i < 5; i++ {
					probe := &meteringProbe{}
					g := &usageContractGateway{probe: probe,
						srv: createAuthRequiredTestServerWithPlugins(t, up.URL, costplugin.New(true, testModelDBPtr().Load, nil, nil), probe)}
					code, out := g.post(t, path, body, map[string]string{"x-agentcc-request-timeout": "500"})
					if code != http.StatusOK {
						t.Fatalf("status %d; body %s", code, out)
					}
					chunks := parseWireStream(t, out)
					if text, finished := streamedText(chunks); text != "Hello world" || !finished {
						t.Errorf("run %d: text %q finished %t, want %q and the finish chunk; body %s", i, text, finished, "Hello world", out)
					}
					if at := emptyChoiceChunks(chunks); len(at) != 0 {
						t.Errorf("run %d: chunks with empty choices at %v in a stream the timeout ended; body %s", i, at, out)
					}
					if len(chunks) > 0 && (chunks[len(chunks)-1].done || chunks[len(chunks)-1].isError) {
						t.Errorf("run %d: the stream the timeout ended ends with [DONE] or an error event; body %s", i, out)
					}
					var withUsage []int
					for j, c := range chunks {
						if c.Usage != nil {
							withUsage = append(withUsage, j)
						}
					}
					if !asked {
						if len(withUsage) != 1 || withUsage[0] != len(chunks)-1 || !usageIs(chunks[withUsage[0]].Usage, 10, 20, 30) {
							t.Errorf("run %d: usage on the chunks at %v, want 10/20/30 on the held finish chunk, the last one; body %s", i, withUsage, out)
						}
					} else if len(withUsage) != 0 {
						t.Errorf("run %d: a client that asked got usage at %v on a stream the timeout ended (the final chunk is not written there); body %s", i, withUsage, out)
					}
					probe.mu.Lock()
					ran := len(probe.seen)
					probe.mu.Unlock()
					if ran != 1 {
						t.Errorf("run %d: the post-plugins ran %d times, want once", i, ran)
					}
				}
			})
		}
	}
}
