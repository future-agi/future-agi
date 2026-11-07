package cohere

import (
	"context"
	"encoding/json"
	"mime"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Contract tests replay each fixture under testdata/contracts/<model>/<case>
// through the real provider (ChatCompletion / StreamChatCompletion, or the
// case.json endpoint's method: see contract_endpoints_test.go) against a
// replay server and check what comes out. See
// internal/providers/contracttest/README.md.
//
//	go test ./internal/providers/cohere/ -run Contract           # check
//	go test ./internal/providers/cohere/ -run Contract -update   # rewrite goldens

// knownStreamEvents are the v2 Chat stream event types the API reference
// documents (StreamedChatResponseV2). A fixture with any other type fails
// before the adapter is consulted.
var knownStreamEvents = []string{
	"message-start", "content-start", "content-delta", "content-end",
	"tool-plan-delta", "tool-call-start", "tool-call-delta", "tool-call-end",
	"citation-start", "citation-end", "message-end", "debug",
}

// streamDone is the stream terminator the API reference declares for v2 Chat
// (x-fern-streaming terminator), sent as a bare data line after message-end.
const streamDone = "[DONE]"

// ContractEnums are the value sets the v2 Chat API documents for the
// enum-like fields of a chat response and stream. Every value found at one
// of these paths in a fixture (or a live recording) must be listed, so a new
// finish reason, content type, event type or citation type fails the suite
// even where the adapter skips it without a trace. Spec names the schema in
// Cohere's OpenAPI spec (cohere-openapi.yaml in
// github.com/cohere-ai/cohere-developer-experience, read 2026-10-28) the
// values come from; the spec-diff check compares the two. Cohere error
// bodies ({"id", "message"}) carry no enum.
var ContractEnums = []contracttest.EnumSpec{
	{Spec: "StreamedChatResponseV2 (discriminator)", Paths: []string{"*:$.type"}, Values: knownStreamEvents},
	{Spec: "ChatFinishReason", Paths: []string{"$.finish_reason", "message-end:$.delta.finish_reason"},
		Values: []string{"COMPLETE", "STOP_SEQUENCE", "MAX_TOKENS", "TOOL_CALL", "ERROR", "TIMEOUT"}},
	{Spec: "AssistantMessageResponse.role", Paths: []string{"$.message.role", "message-start:$.delta.message.role"},
		Values: []string{"assistant"}},
	{Spec: "AssistantMessageResponse.content[] (discriminator)", Paths: []string{"$.message.content[].type", "content-start:$.delta.message.content.type"},
		Values: []string{"text", "thinking"}},
	{Spec: "ToolCallV2.type", Paths: []string{"$.message.tool_calls[].type", "tool-call-start:$.delta.message.tool_calls.type"},
		Values: []string{"function"}},
	{Spec: "CitationType", Paths: []string{"$.message.citations[].type", "citation-start:$.delta.message.citations.type"},
		Values: []string{"TEXT_CONTENT", "THINKING_CONTENT", "PLAN"}},
	{Spec: "Source (discriminator)", Paths: []string{"$.message.citations[].sources[].type", "citation-start:$.delta.message.citations.sources[].type"},
		Values: []string{"tool", "document"}},
}

// undecoded is the type of a stream payload the parser never decodes
// (message-start, content-start and message-end deltas): every field in it
// is a protocol field (streamProtocolFields) or a known gap.
type undecoded struct{}

// contractStrict is how the adapter decodes Cohere chat bodies, for the
// strict checks (contracttest.Strict).
var contractStrict = &contracttest.Strict{
	Docs:  contractDocs,
	Enums: ContractEnums,
	// The stream parser decodes each event's raw delta by event type.
	Decoded: []contracttest.Decoded{
		{Path: "content-delta:$.delta", Type: reflect.TypeFor[contentDelta]()},
		{Path: "tool-call-start:$.delta", Type: reflect.TypeFor[toolCallStartDelta]()},
		{Path: "tool-call-delta:$.delta", Type: reflect.TypeFor[toolCallDeltaPayload]()},
		{Path: "message-start:$.delta", Type: reflect.TypeFor[undecoded]()},
		{Path: "content-start:$.delta", Type: reflect.TypeFor[undecoded]()},
		{Path: "message-end:$.delta", Type: reflect.TypeFor[undecoded]()},
	},
	Ignore: streamProtocolFields,
}

// streamProtocolFields are stream payload fields the parser does not read
// because, by the protocol, they carry nothing at the point they arrive. They
// hold for every model, so they live here rather than in each meta.json.
var streamProtocolFields = map[string]string{
	"message-start:$.delta.message": `always {role: "assistant"} with empty content, tool_plan, tool_calls and citations; content arrives in later events`,
	"content-start:$.delta.message": "the block's type with empty text or thinking; the content arrives in content-delta",
}

// streamSanityGaps are the sanity failures every stream has today (gap K1 in
// meta.json): message-end carries finish_reason and usage under "delta", and
// the parser reads them from the top level of the event, so a stream never
// reports usage. The check fails once that is fixed, so this list is removed
// with the fix.
var streamSanityGaps = []contracttest.SanityGap{{
	Failure: "usage is missing",
	Reason:  "known gap K1: message-end carries usage under delta and the parser reads it from the top level, so no stream reports usage",
}}

// contractDocs splits a chat case's body into what the adapter decodes: an
// error body (parseCohereError), each stream event as streamEvent (the
// [DONE] terminator is not JSON and not decoded), or a response.
func contractDocs(c contracttest.Case) []contracttest.Doc {
	switch {
	case c.IsError():
		return errorDocs(c)
	case c.IsStream:
		events := contracttest.ParseSSE(c.Body)
		docs := make([]contracttest.Doc, len(events))
		for i, ev := range events {
			if isDone(ev) {
				docs[i] = contracttest.Doc{Root: "done:$", Data: ev.Data}
				continue
			}
			docs[i] = contracttest.Doc{Root: eventType(ev) + ":$", Data: ev.Data, Type: reflect.TypeFor[streamEvent]()}
		}
		return docs
	default:
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[cohereResponse]()}}
	}
}

// errorDocs is an error body as parseCohereError decodes it; a body that is
// not JSON (an HTML page from a proxy) has no document.
func errorDocs(c contracttest.Case) []contracttest.Doc {
	if mt, _, err := mime.ParseMediaType(c.ContentType); err != nil || mt != "application/json" {
		return nil
	}
	return []contracttest.Doc{{Root: contracttest.ErrorRoot, Data: c.Body, Type: reflect.TypeFor[cohereErrorResponse]()}}
}

func isDone(ev contracttest.Event) bool { return ev.Name == "" && string(ev.Data) == streamDone }

// eventType is an event's SSE name, or the type its data carries.
func eventType(ev contracttest.Event) string {
	if t := ev.Type(); t != "" {
		return t
	}
	return "data"
}

func TestContract(t *testing.T) {
	for _, c := range contracttest.Discover(t, adapterName) {
		t.Run(c.ID(), func(t *testing.T) {
			t.Parallel()
			strict := strictFor(t, c)
			out := runCase(t, c)
			assertGoldens(t, c, out)
			checkCase(t, c, out)
			strict.AssertMutationsDetected(t, c, func(t testing.TB, m contracttest.Case) {
				checkCase(t, m, runCase(t, m))
			})
		})
	}
}

// strictFor is the strict declaration of c's endpoint.
func strictFor(t testing.TB, c contracttest.Case) *contracttest.Strict {
	t.Helper()
	switch c.Endpoint {
	case contracttest.EndpointChat:
		return contractStrict
	case contracttest.EndpointEmbeddings:
		return embedStrict
	case contracttest.EndpointRerank:
		return rerankStrict
	case contracttest.EndpointListModels:
		return listModelsStrict
	}
	t.Fatalf("endpoint %q: the cohere adapter does not implement it", c.Endpoint)
	return nil
}

// caseOutput is what the adapter did with one case.
type caseOutput struct {
	resp     *models.ChatCompletionResponse
	stream   contracttest.StreamResult
	embed    *models.EmbeddingResponse
	rerank   *models.RerankResponse
	models   []models.ModelObject
	err      error
	shapes   []common.UnknownShape
	upstream *contracttest.Replay
}

// newContractProvider builds the real provider against a replay server. It
// takes a testing.TB so mutants can run it.
func newContractProvider(t testing.TB, url string) *Provider {
	t.Helper()
	p, err := New("test-cohere", config.ProviderConfig{BaseURL: url, APIKey: "test-key"})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = p.Close() })
	return p
}

// runCase sends c's request through the real provider against a replay of
// c's body.
func runCase(t testing.TB, c contracttest.Case) caseOutput {
	t.Helper()
	upstream := contracttest.ReplayServer(t, c)
	p := newContractProvider(t, upstream.URL)
	ctx, shapes := common.WithShapeRecorder(context.Background())

	out := caseOutput{upstream: upstream}
	switch {
	case c.Endpoint != contracttest.EndpointChat:
		runEndpoint(t, c, p, ctx, &out)
	case c.IsStream:
		chunks, errs := p.StreamChatCompletion(ctx, c.Request)
		out.stream = contracttest.DrainStream(t, chunks, errs)
		out.err = out.stream.Err
	default:
		out.resp, out.err = p.ChatCompletion(ctx, c.Request)
	}
	out.shapes = shapes.Shapes()
	return out
}

// assertGoldens pins the canonical output (or error) and the outbound
// request.
func assertGoldens(t *testing.T, c contracttest.Case, out caseOutput) {
	t.Helper()
	switch {
	case c.Endpoint != contracttest.EndpointChat:
		assertEndpointGoldens(t, c, out)
		return
	case c.IsError():
		contracttest.AssertGolden(t, c.GoldenPath(), contracttest.ErrorGolden(out.err))
	case c.IsStream:
		contracttest.AssertGolden(t, c.GoldenPath(), out.stream)
	default:
		contracttest.AssertGolden(t, c.GoldenPath(), out.resp)
	}
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), out.upstream.Request(t))
}

// checkCase is every check except the goldens: what a live recording and the
// mutation self-test go through.
func checkCase(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	if c.Endpoint != contracttest.EndpointChat {
		checkEndpoint(t, c, out)
		return
	}
	checkUpstreamModel(t, c, out.upstream, "/v2/chat")

	var events []contracttest.Event
	if c.IsStream && !c.IsError() {
		events = streamEvents(contracttest.ParseSSE(c.Body))
	}
	switch {
	case c.IsError():
		if c.IsStream && len(out.stream.Chunks) > 0 {
			t.Errorf("an upstream %d answer produced %d stream chunks", c.Status, len(out.stream.Chunks))
		}
		contracttest.AssertSaneError(t, c, out.err)
	case out.err != nil:
		t.Fatalf("adapter error: %v", out.err)
	}
	if events != nil {
		contracttest.AssertKnownEventTypes(t, events, knownStreamEvents...)
		checkEventEnvelopes(t, events)
	}

	contractStrict.Check(t, c)
	contracttest.AssertNoUnknownShapes(t, c, out.shapes)

	switch {
	case c.IsError():
	case c.IsStream:
		sc := c
		sc.SanityGaps = append(append([]contracttest.SanityGap(nil), streamSanityGaps...), c.SanityGaps...)
		contracttest.ExpectGaps(t, sc, func(t testing.TB) {
			contracttest.AssertSane(t, contracttest.FromStream(out.stream.Chunks), streamHasOutput(events))
			checkStreamError(t, events, out.err)
		})
	default:
		contracttest.ExpectGaps(t, c, func(t testing.TB) {
			contracttest.AssertSane(t, contracttest.FromResponse(out.resp), responseHasOutput(t, c.Body))
			checkResponseError(t, c.Body, out.resp)
		})
	}
}

// checkResponseError checks a response whose generation Cohere reports as
// failed (finish_reason ERROR) does not reach the client as a success.
func checkResponseError(t testing.TB, body []byte, resp *models.ChatCompletionResponse) {
	t.Helper()
	var up struct {
		FinishReason string `json:"finish_reason"`
	}
	if json.Unmarshal(body, &up) != nil || up.FinishReason != "ERROR" {
		return
	}
	got := contracttest.FromResponse(resp)
	t.Errorf("the upstream's finish_reason is ERROR (the generation failed), but the canonical response is a success with finish_reason %q", got.FinishReason)
}

// checkUpstreamModel checks the request went to path, filed under the model
// the gateway actually asked for.
func checkUpstreamModel(t testing.TB, c contracttest.Case, upstream *contracttest.Replay, path string) {
	t.Helper()
	req := upstream.Request(t)
	if req.Method != "POST" || req.Path != path {
		t.Errorf("upstream request = %s %s, want POST %s", req.Method, req.Path, path)
	}
	body, _ := req.Body.(map[string]any)
	if body["model"] != c.Model {
		t.Errorf("upstream request model = %v, want the fixture's model directory %q", body["model"], c.Model)
	}
}

// streamEvents drops the [DONE] terminator, which is not an event.
func streamEvents(events []contracttest.Event) []contracttest.Event {
	kept := make([]contracttest.Event, 0, len(events))
	for _, ev := range events {
		if !isDone(ev) {
			kept = append(kept, ev)
		}
	}
	return kept
}

// checkEventEnvelopes checks every event's data is a JSON object whose type
// is the SSE event name, as the API sends them.
func checkEventEnvelopes(t testing.TB, events []contracttest.Event) {
	t.Helper()
	for _, ev := range events {
		var e struct {
			Type string `json:"type"`
		}
		if err := json.Unmarshal(ev.Data, &e); err != nil {
			t.Errorf("event %q: data is not a JSON object: %v", ev.Name, err)
			continue
		}
		if ev.Name != "" && e.Type != ev.Name {
			t.Errorf("event %q carries data.type %q; the API sends them equal", ev.Name, e.Type)
		}
	}
}

// checkStreamError checks a stream the upstream ended with an error
// (message-end's delta.error, or finish_reason ERROR) ends with an error.
func checkStreamError(t testing.TB, events []contracttest.Event, err error) {
	t.Helper()
	for _, ev := range events {
		var e struct {
			Type  string `json:"type"`
			Delta struct {
				Error        string `json:"error"`
				FinishReason string `json:"finish_reason"`
			} `json:"delta"`
		}
		if json.Unmarshal(ev.Data, &e) != nil || e.Type != "message-end" {
			continue
		}
		if (e.Delta.Error != "" || e.Delta.FinishReason == "ERROR") && err == nil {
			t.Errorf("the upstream's stream ended with an error (%q, finish_reason %s), but the canonical stream ended without one",
				e.Delta.Error, e.Delta.FinishReason)
		}
	}
}

// responseHasOutput reports whether the upstream response carried text or a
// tool call, which the canonical response must then carry too.
func responseHasOutput(t testing.TB, body []byte) bool {
	t.Helper()
	var resp cohereResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	if len(resp.Message.ToolCalls) > 0 {
		return true
	}
	for _, part := range resp.Message.Content {
		if part.Type == "text" && part.Text != "" {
			return true
		}
	}
	return false
}

// streamHasOutput is responseHasOutput for a stream.
func streamHasOutput(events []contracttest.Event) bool {
	for _, ev := range events {
		var e streamEvent
		if json.Unmarshal(ev.Data, &e) != nil {
			continue
		}
		switch e.Type {
		case "tool-call-start":
			return true
		case "content-delta":
			var cd contentDelta
			if json.Unmarshal(e.Delta, &cd) == nil && cd.Message.Content.Text != "" {
				return true
			}
		}
	}
	return false
}
