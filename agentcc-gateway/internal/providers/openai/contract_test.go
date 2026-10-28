package openai

import (
	"bytes"
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Contract tests replay each fixture under testdata/contracts/<model>/<case>
// through the real provider (ChatCompletion / StreamChatCompletion against a
// replay server) and check what comes out. See
// internal/providers/contracttest/README.md.
//
//	go test ./internal/providers/openai/ -run Contract           # check
//	go test ./internal/providers/openai/ -run Contract -update   # rewrite goldens
//
// This adapter serves every OpenAI-format upstream, so a fixture's model key
// (meta.json's model, Case.Model) comes in two forms:
//
//   - <model-id>: the official OpenAI API, e.g. gpt-5.6-sol. The provider is
//     built as if pointed at api.openai.com, whose max_tokens handling
//     differs (normalizeMaxTokens).
//   - <preset>@<model-id>: an OpenAI-compatible upstream named by its preset
//     in providers/presets.go, e.g. groq@openai/gpt-oss-120b, since the same
//     model ID can be served by several upstreams with different shapes.
//
// The directory is the key as contracttest.ModelDir encodes it
// (groq%40openai%2Fgpt-oss-120b). The test checks the outbound model against
// the model ID after the "@".

// compatPresets are the presets (providers/presets.go KnownProviders) that
// have fixtures here. The providers package imports this one, so the test
// cannot read the table itself; a typo in a model key fails instead.
var compatPresets = map[string]bool{"groq": true, "perplexity": true}

// fixtureUpstream splits a fixture's model key into the preset ("" for the
// official API) and the model ID the gateway sends upstream.
func fixtureUpstream(t *testing.T, key string) (preset, model string) {
	t.Helper()
	preset, model, ok := strings.Cut(key, "@")
	if !ok {
		return "", key
	}
	if !compatPresets[preset] {
		t.Fatalf("fixture model key %q names preset %q, which is not in compatPresets", key, preset)
	}
	return preset, model
}

// saneGaps lists, per case ID, the AssertSane failures an adapter gap is
// known to cause (substrings of AssertSane's messages). Each is also in the
// model's meta.json allowlist under the field that is dropped; this is the
// consequence a client sees. AssertSaneWithGaps fails once a gap stops
// occurring, so an entry cannot outlive its fix.
var saneGaps = map[string][]string{
	// Gap O4: the custom tool call's custom.name / custom.input are dropped,
	// leaving a tool call with no name and empty arguments.
	"gpt-5.6-sol/custom_tool_call": {"lacks an id or name", "arguments are not valid JSON"},
	// Gap GQ3: Groq streams usage under x_groq.usage, which reaches the client
	// through StreamChunk.Extra but never becomes StreamChunk.Usage, so the
	// gateway's own accounting sees none.
	"groq@openai/gpt-oss-120b/stream_reasoning": {"usage is missing"},
}

func TestContract(t *testing.T) {
	for _, c := range contracttest.Discover(t, adapterName) {
		t.Run(c.ID(), func(t *testing.T) {
			t.Parallel()
			if c.IsStream {
				contractStream(t, c)
			} else {
				contractJSON(t, c)
			}
		})
	}
}

// newContractProvider builds the provider against the replay server. For the
// official API it sets what New derives from an api.openai.com base URL, so
// the outbound request is the one OpenAI would receive.
func newContractProvider(t *testing.T, c contracttest.Case, url string) *Provider {
	t.Helper()
	p := newTestProvider(t, url)
	t.Cleanup(func() { _ = p.Close() })
	if preset, _ := fixtureUpstream(t, c.Model); preset == "" {
		p.openAIAPI = true
	}
	return p
}

func contractJSON(t *testing.T, c contracttest.Case) {
	upstream := contracttest.ReplayServer(t, c)
	p := newContractProvider(t, c, upstream.URL)
	ctx, shapes := common.WithShapeRecorder(context.Background())

	resp, err := p.ChatCompletion(ctx, c.Request)
	if err != nil {
		t.Fatalf("ChatCompletion: %v", err)
	}

	contracttest.AssertGolden(t, c.GoldenPath(), resp)
	assertUpstreamRequest(t, c, upstream)
	contracttest.AssertKnownPaths(t, c, responseUnknownFields(t, c.Body))
	contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
	contracttest.AssertSaneWithGaps(t, contracttest.FromResponse(resp), responseHasOutput(t, c.Body), saneGaps[c.ID()]...)
}

func contractStream(t *testing.T, c contracttest.Case) {
	events := contracttest.ParseSSE(c.Body)
	chunks := streamChunks(t, events)

	upstream := contracttest.ReplayServer(t, c)
	p := newContractProvider(t, c, upstream.URL)
	ctx, shapes := common.WithShapeRecorder(context.Background())

	out, errs := p.StreamChatCompletion(ctx, c.Request)
	res := contracttest.DrainStream(t, out, errs)
	if res.Err != nil {
		t.Fatalf("StreamChatCompletion: %v", res.Err)
	}

	contracttest.AssertGolden(t, c.GoldenPath(), res)
	assertUpstreamRequest(t, c, upstream)
	contracttest.AssertKnownPaths(t, c, streamUnknownFields(chunks))
	contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
	contracttest.AssertSaneWithGaps(t, contracttest.FromStream(res.Chunks), streamHasOutput(t, chunks), saneGaps[c.ID()]...)
}

// assertUpstreamRequest pins the outbound request and checks the fixture is
// filed under the model the gateway actually asked for.
func assertUpstreamRequest(t *testing.T, c contracttest.Case, upstream *contracttest.Replay) {
	t.Helper()
	req := upstream.Request(t)
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), req)
	if req.Path != "/v1/chat/completions" {
		t.Errorf("upstream request path = %q, want /v1/chat/completions", req.Path)
	}
	_, model := fixtureUpstream(t, c.Model)
	body, _ := req.Body.(map[string]any)
	if body["model"] != model {
		t.Errorf("upstream request model = %v, want %q (from the fixture's model key %q)", body["model"], model, c.Model)
	}
}

// The adapter decodes the body into models.ChatCompletionResponse (or, per
// event, models.StreamChunk). Both capture unknown top-level keys in Extra and
// forward them, and Usage does the same for its own keys, so UnknownFields
// stops at those types. The nested types the adapter decodes into have no
// Extra: an unknown key in a choice, message, delta or tool call is dropped.
// So the audit is of choices, against the canonical choice types.

// responseUnknownFields audits a whole response's choices against
// []models.Choice. Paths look like "$.choices[].message.refusal".
func responseUnknownFields(t *testing.T, body []byte) []string {
	t.Helper()
	var r struct {
		Choices json.RawMessage `json:"choices"`
	}
	if err := json.Unmarshal(body, &r); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	return contracttest.UnknownFieldsAt(r.Choices, reflect.TypeFor[[]models.Choice](), "$.choices")
}

// streamChunk is one data event of a stream, still raw.
type streamChunk struct {
	choices json.RawMessage
}

// streamChunks checks the framing of a Chat Completions stream: data-only
// events (no event names), each a JSON object, and the [DONE] sentinel, when
// present, last. It returns the chunks before the sentinel.
func streamChunks(t *testing.T, events []contracttest.Event) []streamChunk {
	t.Helper()
	var out []streamChunk
	for i, ev := range events {
		if ev.Name != "" {
			t.Errorf("event %d is named %q; Chat Completions streams are data-only", i, ev.Name)
		}
		if string(ev.Data) == "[DONE]" {
			if i != len(events)-1 {
				t.Errorf("[DONE] is event %d of %d; it ends the stream", i, len(events))
			}
			continue
		}
		var c struct {
			Choices json.RawMessage `json:"choices"`
		}
		if !bytes.HasPrefix(bytes.TrimSpace(ev.Data), []byte("{")) || json.Unmarshal(ev.Data, &c) != nil {
			t.Errorf("event %d: data is not a JSON object: %.80s", i, ev.Data)
			continue
		}
		out = append(out, streamChunk{choices: c.Choices})
	}
	if len(out) == 0 {
		t.Fatalf("stream fixture has no chunks")
	}
	return out
}

// streamUnknownFields audits every chunk's choices against
// []models.StreamChoice. Paths are prefixed "chunk:", e.g.
// "chunk:$.choices[].delta.refusal", to tell them from a whole response's.
func streamUnknownFields(chunks []streamChunk) []string {
	var paths []string
	for _, ch := range chunks {
		if len(ch.choices) > 0 {
			paths = append(paths, contracttest.UnknownFieldsAt(ch.choices, reflect.TypeFor[[]models.StreamChoice](), "chunk:$.choices")...)
		}
	}
	return paths
}

// responseHasOutput reports whether the upstream response carried text or a
// tool call, which the canonical response must then carry too. A refusal is
// not counted: the field audit is what flags a dropped refusal.
func responseHasOutput(t *testing.T, body []byte) bool {
	t.Helper()
	var r struct {
		Choices []struct {
			Message struct {
				Content   json.RawMessage   `json:"content"`
				ToolCalls []json.RawMessage `json:"tool_calls"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(body, &r); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	for _, ch := range r.Choices {
		if hasContent(ch.Message.Content) || len(ch.Message.ToolCalls) > 0 {
			return true
		}
	}
	return false
}

// hasContent reports whether message content is a non-empty string or a
// non-empty array of parts.
func hasContent(raw json.RawMessage) bool {
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return s != ""
	}
	var parts []json.RawMessage
	return json.Unmarshal(raw, &parts) == nil && len(parts) > 0
}

// streamHasOutput is responseHasOutput for a stream.
func streamHasOutput(t *testing.T, chunks []streamChunk) bool {
	t.Helper()
	for _, ch := range chunks {
		var choices []struct {
			Delta struct {
				Content   *string           `json:"content"`
				ToolCalls []json.RawMessage `json:"tool_calls"`
			} `json:"delta"`
		}
		if len(ch.choices) == 0 || json.Unmarshal(ch.choices, &choices) != nil {
			continue
		}
		for _, c := range choices {
			if (c.Delta.Content != nil && *c.Delta.Content != "") || len(c.Delta.ToolCalls) > 0 {
				return true
			}
		}
	}
	return false
}
