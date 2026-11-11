package bedrock

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Contract tests replay each fixture under testdata/contracts/<model>/<case>
// through the real provider (ChatCompletion / StreamChatCompletion against a
// replay server) and check what comes out. See
// internal/providers/contracttest/README.md, and contract_eventstream_test.go
// for how the binary stream fixtures are generated and split into events.
//
//	go test ./internal/providers/bedrock/ -run 'TestEventStreamFixtures|TestContract' -update   # rewrite streams and goldens
//	go test ./internal/providers/bedrock/ -run Contract                                         # check
//
// The adapter has two upstream APIs, chosen by model ID (isInferenceProfile):
// inference-profile IDs (global., us., ..., arn:) go to Converse /
// ConverseStream, base model IDs to InvokeModel /
// InvokeModelWithResponseStream with an Anthropic Messages body. Each has its
// own parser, so the fixtures cover both. Error bodies (restJson1: the
// exception name in the x-amzn-ErrorType header, {"message"} in the body)
// share parseBedrockError; mid-stream exceptions are event-stream messages
// with :message-type exception.

// Event-stream message types the APIs document (the ConverseStreamOutput and
// ResponseStream unions of the Smithy model): the :event-type of an event, or
// the :exception-type of an exception.
var (
	converseStreamEvents = []string{
		"messageStart", "contentBlockStart", "contentBlockDelta", "contentBlockStop", "messageStop", "metadata",
		"internalServerException", "modelStreamErrorException", "validationException",
		"throttlingException", "serviceUnavailableException",
	}
	invokeStreamEvents = []string{
		"chunk",
		"internalServerException", "modelStreamErrorException", "validationException",
		"throttlingException", "modelTimeoutException", "serviceUnavailableException",
	}
)

// exceptionStatus is the HTTP status of each Bedrock Runtime exception (its
// @httpError in the Smithy model), keyed by its event-stream member name. A
// mid-stream exception carries no status of its own; the canonical error
// should still say what the exception means.
var exceptionStatus = map[string]int{
	"validationException":           400,
	"serviceQuotaExceededException": 400,
	"accessDeniedException":         403,
	"resourceNotFoundException":     404,
	"modelTimeoutException":         408,
	"modelErrorException":           424,
	"modelStreamErrorException":     424,
	"throttlingException":           429,
	"modelNotReadyException":        429,
	"internalServerException":       500,
	"serviceUnavailableException":   503,
}

// exceptionMembers are the members of every Bedrock Runtime exception
// structure in the Smithy model: message on all of them, originalStatusCode
// and resourceName on ModelErrorException, originalStatusCode and
// originalMessage on ModelStreamErrorException.
var exceptionMembers = []string{"message", "originalStatusCode", "originalMessage", "resourceName"}

// anthropicStopReasons and the other anthropic* sets are the Anthropic
// Messages API's documented values (anthropic-sdk-python
// scripts/mock-spec.json.gz, read 2026-10-28, as in the anthropic package's
// ContractEnums): an InvokeModel body for Claude is an Anthropic Messages
// body, which the Bedrock Smithy model leaves an opaque blob, so its values
// come from Anthropic's spec.
var (
	anthropicStopReasons = []string{"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal", "model_context_window_exceeded"}
	anthropicBlockTypes  = []string{"text", "thinking", "redacted_thinking", "tool_use", "server_tool_use", "web_search_tool_result",
		"web_fetch_tool_result", "code_execution_tool_result", "bash_code_execution_tool_result",
		"text_editor_code_execution_tool_result", "tool_search_tool_result", "container_upload"}
	anthropicStreamEvents = []string{"message_start", "content_block_start", "content_block_delta", "content_block_stop",
		"message_delta", "message_stop", "ping", "error"}
	anthropicDeltaTypes = []string{"text_delta", "input_json_delta", "citations_delta", "thinking_delta", "signature_delta"}
)

// ContractEnums are the value sets the Bedrock Runtime API documents for the
// enum-like fields the adapter reads or passes on, from the Smithy model
// (github.com/aws/api-models-aws bedrock-runtime 2023-09-30, commit
// e10d6a1c5ac36af7c0c61d1f67b3c3ebbe41de49, read 2026-10-28) for Converse, and
// from Anthropic's Messages API for the InvokeModel bodies Claude returns.
// Every value found at one of these paths in a fixture (or a live recording)
// must be listed, so a new stop reason, block type or event fails the suite
// even where the adapter skips it without a trace. Spec names the shape the
// values come from; the spec-diff check compares the two.
var ContractEnums = []contracttest.EnumSpec{
	{Spec: "com.amazonaws.bedrockruntime#StopReason", Paths: []string{"$.stopReason", "messageStop:$.stopReason"},
		Values: []string{"end_turn", "tool_use", "max_tokens", "stop_sequence", "guardrail_intervened", "content_filtered",
			"malformed_model_output", "malformed_tool_use", "model_context_window_exceeded"}},
	{Spec: "com.amazonaws.bedrockruntime#ConversationRole", Paths: []string{"$.output.message.role", "messageStart:$.role"},
		Values: []string{"user", "assistant", "system"}},
	{Spec: "com.amazonaws.bedrockruntime#ToolUseType",
		Paths:  []string{"$.output.message.content[].toolUse.type", "contentBlockStart:$.start.toolUse.type"},
		Values: []string{"server_tool_use"}},
	{Spec: "com.amazonaws.bedrockruntime#ServiceTierType", Paths: []string{"$.serviceTier.type", "metadata:$.serviceTier.type"},
		Values: []string{"priority", "default", "flex", "reserved"}},
	{Spec: "com.amazonaws.bedrockruntime#PerformanceConfigLatency",
		Paths:  []string{"$.performanceConfig.latency", "metadata:$.performanceConfig.latency"},
		Values: []string{"standard", "optimized"}},
	{Spec: "com.amazonaws.bedrockruntime#CacheTTL", Paths: []string{"$.usage.cacheDetails[].ttl", "metadata:$.usage.cacheDetails[].ttl"},
		Values: []string{"5m", "1h"}},

	{Spec: "Anthropic StopReason (InvokeModel body)", Paths: []string{"$.stop_reason", "chunk:$.bytes.delta.stop_reason"},
		Values: anthropicStopReasons},
	{Spec: "Anthropic Message.type (InvokeModel body)", Paths: []string{"$.type", "chunk:$.bytes.message.type"}, Values: []string{"message"}},
	{Spec: "Anthropic Message.role (InvokeModel body)", Paths: []string{"$.role", "chunk:$.bytes.message.role"}, Values: []string{"assistant"}},
	{Spec: "Anthropic ContentBlock discriminator (InvokeModel body)", Paths: []string{"$.content[].type", "chunk:$.bytes.content_block.type"},
		Values: anthropicBlockTypes},
	{Spec: "Anthropic MessageStreamEvent.type (InvokeModelWithResponseStream chunk bytes)", Paths: []string{"chunk:$.bytes.type"},
		Values: anthropicStreamEvents},
	{Spec: "Anthropic ContentBlockDeltaEvent.delta discriminator (chunk bytes)", Paths: []string{"chunk:$.bytes.delta.type"},
		Values: anthropicDeltaTypes},
}

// contractStrict is how the adapter decodes Bedrock bodies, for the strict
// checks (contracttest.Strict).
var contractStrict = &contracttest.Strict{
	Docs:  contractDocs,
	Enums: ContractEnums,
	Decoded: []contracttest.Decoded{
		// An InvokeModelWithResponseStream chunk's bytes hold one Anthropic
		// stream event (inlined by eventStreamCodec). parseStreamPayload is
		// written to decode it as bedrockStreamEvent, plus the payloads below
		// by event type, though today it never gets there (gap B1: it
		// decodes the {"bytes"} envelope itself). The walk follows what the
		// parser would read, so drift inside the bytes still shows.
		{Path: "chunk:$.bytes", Tag: "type", Types: map[string]reflect.Type{
			"message_start":       reflect.TypeFor[bedrockStreamEvent](),
			"content_block_start": reflect.TypeFor[invokeBlockStartEvent](),
			"content_block_delta": reflect.TypeFor[bedrockStreamEvent](),
			"content_block_stop":  reflect.TypeFor[bedrockStreamEvent](),
			"message_delta":       reflect.TypeFor[invokeMessageDeltaEvent](),
			"message_stop":        reflect.TypeFor[bedrockStreamEvent](),
			"ping":                reflect.TypeFor[bedrockStreamEvent](),
		}},
		{Path: "chunk:$.bytes.message", Type: reflect.TypeFor[invokeMessageStart]()},
		{Path: "chunk:$.bytes.content_block", Tag: "type", Types: map[string]reflect.Type{
			"text":     reflect.TypeFor[invokeContentBlock](),
			"tool_use": reflect.TypeFor[invokeContentBlock](),
		}},
		{Path: "chunk:$.bytes.delta", Tag: "type", Types: map[string]reflect.Type{
			"text_delta":       reflect.TypeFor[invokeTextDelta](),
			"input_json_delta": reflect.TypeFor[invokeInputJSONDelta](),
		}},
	},
	// A mid-stream exception's payload is not decoded: the parsers put the
	// raw payload into the error message. Its keys are the exception
	// members the Smithy model documents.
	Watched: []contracttest.Watched{{Path: contracttest.ErrorRoot, Keys: exceptionMembers}},
	// Tool input is whatever the model passed the tool.
	Free:   []string{"$.content[].input", "$.output.message.content[].toolUse.input"},
	Ignore: streamProtocolFields,
	Codec:  &eventStreamCodec,
}

// invokeBlockStartEvent is everything parseStreamPayload reads from a
// content_block_start event: the envelope it decodes every event into, and
// the content_block it decodes from the same payload (invokeBlockStartPayload)
// for the block types it translates.
type invokeBlockStartEvent struct {
	bedrockStreamEvent
	ContentBlock json.RawMessage `json:"content_block"`
}

// invokeMessageDeltaEvent is everything parseStreamPayload reads from a
// message_delta event: the envelope, with its delta decoded as
// invokeMessageDelta.
type invokeMessageDeltaEvent struct {
	bedrockStreamEvent
	Delta *invokeMessageDelta `json:"delta"`
}

// streamProtocolFields are stream payload fields the parsers do not read
// because, by the protocol, they carry nothing they need. They hold for every
// model, so they live here rather than in each meta.json.
var streamProtocolFields = map[string]string{
	"messageStart:$.role":                  `always "assistant"`,
	"contentBlockStop:$.contentBlockIndex": "only closes the block; its content arrived in its deltas",
	"metadata:$.usage.totalTokens":         "derived: total_tokens is prompt + completion, as on the non-stream path",

	"chunk:$.bytes.message.type":          `always "message"`,
	"chunk:$.bytes.message.role":          `always "assistant"`,
	"chunk:$.bytes.message.content":       "always empty; content arrives as content blocks",
	"chunk:$.bytes.message.stop_reason":   "always null at message_start; arrives on message_delta",
	"chunk:$.bytes.message.stop_sequence": "always null at message_start; arrives on message_delta",
	"chunk:$.bytes.content_block.text":    "always empty at block start; text arrives as text_delta",
	"chunk:$.bytes.content_block.input":   "always {} at block start; input arrives as input_json_delta",
}

// usesConverse reports whether the adapter sends c's model to Converse.
func usesConverse(c contracttest.Case) bool { return isInferenceProfile(resolveModelName(c.Model)) }

// operation is the Bedrock Runtime operation the adapter calls for c, the
// last segment of its path.
func operation(c contracttest.Case) string {
	return map[[2]bool]string{
		{true, false}:  "converse",
		{true, true}:   "converse-stream",
		{false, false}: "invoke",
		{false, true}:  "invoke-with-response-stream",
	}[[2]bool{usesConverse(c), c.IsStream}]
}

// contractDocs splits a case's body into what the adapter decodes: an error
// body as bedrockErrorResponse (parseBedrockError), a Converse response as
// converseResponse, an InvokeModel response as bedrockResponse, and a stream
// event by event (eventStreamDocs).
func contractDocs(c contracttest.Case) []contracttest.Doc {
	switch {
	case c.IsError():
		if !json.Valid(c.Body) {
			return nil
		}
		return []contracttest.Doc{{Root: contracttest.ErrorRoot, Data: c.Body, Type: reflect.TypeFor[bedrockErrorResponse]()}}
	case c.IsStream:
		events, _ := decodeEventStreamBody(c.Body)
		return eventStreamDocs(events, usesConverse(c))
	case usesConverse(c):
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[converseResponse]()}}
	default:
		return []contracttest.Doc{{Root: "$", Data: c.Body, Type: reflect.TypeFor[bedrockResponse]()}}
	}
}

// eventStreamDocs is one Doc per event, rooted "<event-type>:$", typed as the
// parser decodes the payload:
//
//   - an exception at contracttest.ErrorRoot, undecoded (see Watched);
//   - ConverseStream messageStart and contentBlockStop, which the parser does
//     not decode, as struct{} (their fields are streamProtocolFields);
//   - a contentBlockStart / contentBlockDelta as converseBlockStartEvent /
//     converseBlockDeltaEvent, which is what parseConverseEvent decodes every
//     one as: a union member it does not translate (reasoningContent, say)
//     is an unknown field there, as well as an unknown-shape report;
//   - messageStop and metadata as their event structs;
//   - an InvokeModelWithResponseStream chunk as bedrockStreamEvent, which is
//     what parseStreamPayload decodes the {"bytes"} envelope as (gap B1),
//     with the bytes declared Decoded.
func eventStreamDocs(events []contracttest.Event, converse bool) []contracttest.Doc {
	docs := make([]contracttest.Doc, len(events))
	for i, ev := range events {
		d := contracttest.Doc{Root: ev.Name + ":$", Data: ev.Data}
		switch {
		case isException(ev.Name):
			d.Root = contracttest.ErrorRoot
		case !converse && ev.Name == "chunk":
			d.Type = reflect.TypeFor[bedrockStreamEvent]()
		case !converse:
		case ev.Name == "messageStart", ev.Name == "contentBlockStop":
			d.Type = reflect.TypeFor[struct{}]()
		case ev.Name == "contentBlockStart":
			d.Type = reflect.TypeFor[converseBlockStartEvent]()
		case ev.Name == "contentBlockDelta":
			d.Type = reflect.TypeFor[converseBlockDeltaEvent]()
		case ev.Name == "messageStop":
			d.Type = reflect.TypeFor[converseMessageStopEvent]()
		case ev.Name == "metadata":
			d.Type = reflect.TypeFor[converseMetadataEvent]()
		}
		docs[i] = d
	}
	return docs
}

// isException reports whether an event name is an exception's
// :exception-type: every Bedrock Runtime exception member ends so.
func isException(name string) bool { return strings.HasSuffix(name, "Exception") }

func TestContract(t *testing.T) {
	for _, c := range contracttest.Discover(t, adapterName) {
		t.Run(c.ID(), func(t *testing.T) {
			t.Parallel()
			out := runCase(t, c)
			assertGoldens(t, c, out)
			checkCase(t, c, out)
			contractStrict.AssertMutationsDetected(t, c, func(t testing.TB, m contracttest.Case) {
				checkCase(t, m, runCase(t, m))
			})
		})
	}
}

// caseOutput is what the adapter did with one case.
type caseOutput struct {
	resp     *models.ChatCompletionResponse
	stream   contracttest.StreamResult
	err      error
	shapes   []common.UnknownShape
	upstream *contracttest.Replay
}

// runCase sends c's request through the real provider against a replay of
// c's body.
func runCase(t testing.TB, c contracttest.Case) caseOutput {
	t.Helper()
	upstream := contracttest.ReplayServer(t, c)
	cfg := config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "bedrock", DefaultTimeout: 5 * time.Second, MaxConcurrent: 10}
	p, err := New("test-provider", withTestAWSCredentials(cfg))
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(func() { _ = p.Close() })
	ctx, shapes := common.WithShapeRecorder(context.Background())

	out := caseOutput{upstream: upstream}
	if c.IsStream {
		chunks, errs := p.StreamChatCompletion(ctx, c.Request)
		out.stream = contracttest.DrainStream(t, chunks, errs)
		out.err = out.stream.Err
	} else {
		out.resp, out.err = p.ChatCompletion(ctx, c.Request)
	}
	out.shapes = shapes.Shapes()
	return out
}

// streamErrorGolden is the golden form of a stream that ended in an error: the
// stream as any stream's, plus the canonical error the client is given.
type streamErrorGolden struct {
	Stream contracttest.StreamResult `json:"stream"`
	Error  contracttest.ErrorResult  `json:"canonical_error"`
}

// assertGoldens pins the canonical output (or error) and the outbound
// request.
func assertGoldens(t *testing.T, c contracttest.Case, out caseOutput) {
	t.Helper()
	switch {
	case c.IsError():
		contracttest.AssertGolden(t, c.GoldenPath(), contracttest.ErrorGolden(out.err))
	case c.IsStream && out.err != nil:
		contracttest.AssertGolden(t, c.GoldenPath(), streamErrorGolden{out.stream, contracttest.ErrorGolden(out.err)})
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
	// Bedrock carries the model in the path: /model/<id>/<operation>.
	if req, want := out.upstream.Request(t), "/model/"+c.Model+"/"+operation(c); req.Path != want {
		t.Errorf("upstream request path = %q, want %q (the fixture's model and API)", req.Path, want)
	}

	var events []contracttest.Event
	if c.IsStream && !c.IsError() {
		var err error
		if events, err = decodeEventStreamBody(c.Body); err != nil {
			t.Errorf("decoding %s: %v", contracttest.ResponseEventStreamFile, err)
		}
		known := invokeStreamEvents
		if usesConverse(c) {
			known = converseStreamEvents
		}
		contracttest.AssertKnownEventTypes(t, events, known...)
	}
	exception := firstException(events)

	switch {
	case c.IsError():
		if c.IsStream && len(out.stream.Chunks) > 0 {
			t.Errorf("an upstream %d answer produced %d stream chunks", c.Status, len(out.stream.Chunks))
		}
		contracttest.AssertSaneError(t, c, out.err)
	case exception != "":
		assertSaneStreamException(t, c, exception, out.err)
	case out.err != nil:
		t.Fatalf("adapter error: %v", out.err)
	}

	contractStrict.Check(t, c)
	contracttest.AssertNoUnknownShapes(t, c, out.shapes)

	switch {
	case c.IsError(), exception != "":
		// The stream ended in an error, checked above; what came before it
		// is no completion.
	case c.IsStream && usesConverse(c):
		contracttest.AssertSane(t, contracttest.FromStream(out.stream.Chunks), converseStreamHasOutput(t, events))
	case c.IsStream:
		contracttest.AssertSaneWithGaps(t, c, contracttest.FromStream(out.stream.Chunks), invokeStreamHasOutput(t, events), invokeStreamSanityGaps...)
	case usesConverse(c):
		contracttest.AssertSane(t, contracttest.FromResponse(out.resp), converseResponseHasOutput(t, c.Body))
	default:
		contracttest.AssertSane(t, contracttest.FromResponse(out.resp), invokeResponseHasOutput(t, c.Body))
	}
}

// invokeStreamSanityGaps are the sanity failures every InvokeModel stream has
// today (gap B1 in meta.json): parseStreamPayload decodes each chunk event's
// payload, {"bytes": "<base64 Anthropic event>"}, as if it were the Anthropic
// event itself, finds no "type", and emits nothing. A real stream therefore
// yields no content, no finish_reason and no usage. AssertSaneWithGaps fails
// once that is fixed, so this list is removed with the fix.
var invokeStreamSanityGaps = []string{
	`finish_reason "" is not canonical`,
	"no content and no tool calls",
	"usage is missing",
}

// firstException is the :exception-type of the stream's first exception
// message, or "".
func firstException(events []contracttest.Event) string {
	for _, ev := range events {
		if isException(ev.Name) {
			return ev.Name
		}
	}
	return ""
}

// assertSaneStreamException checks the error a stream ending in exception
// produced as AssertSaneError checks an HTTP error: as if the upstream had
// answered with the exception's own status (exceptionStatus), so a
// mid-stream throttlingException must still become a 429. The case's
// sanity_gaps apply.
func assertSaneStreamException(t testing.TB, c contracttest.Case, exception string, err error) {
	t.Helper()
	status, ok := exceptionStatus[exception]
	if !ok {
		t.Errorf("stream ends in %s, which has no documented status", exception)
		return
	}
	ec := c
	ec.Status = status
	contracttest.AssertSaneError(t, ec, err)
}

// converseResponseHasOutput reports whether a Converse response carried text
// or a tool call, which the canonical response must then carry too.
func converseResponseHasOutput(t testing.TB, body []byte) bool {
	t.Helper()
	var resp converseResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	for _, part := range resp.Output.Message.Content {
		if (part.Text != nil && *part.Text != "") || part.ToolUse != nil {
			return true
		}
	}
	return false
}

// invokeResponseHasOutput is converseResponseHasOutput for an InvokeModel
// (Anthropic Messages) body.
func invokeResponseHasOutput(t testing.TB, body []byte) bool {
	t.Helper()
	var resp bedrockResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("decoding fixture: %v", err)
	}
	for _, b := range resp.Content {
		if (b.Type == "text" && b.Text != "") || b.Type == "tool_use" {
			return true
		}
	}
	return false
}

// converseStreamHasOutput is converseResponseHasOutput for a ConverseStream.
func converseStreamHasOutput(t testing.TB, events []contracttest.Event) bool {
	t.Helper()
	for _, ev := range events {
		switch ev.Name {
		case "contentBlockStart":
			var e converseBlockStartEvent
			if json.Unmarshal(ev.Data, &e) == nil && e.Start.ToolUse.ToolUseID != "" {
				return true
			}
		case "contentBlockDelta":
			var e converseBlockDeltaEvent
			if json.Unmarshal(ev.Data, &e) == nil && e.Delta.Text != "" {
				return true
			}
		}
	}
	return false
}

// invokeStreamHasOutput is invokeResponseHasOutput for an
// InvokeModelWithResponseStream body. It reads what Bedrock actually sends:
// each chunk's bytes (inlined by decodeEventStreamBody) hold one Anthropic
// stream event.
func invokeStreamHasOutput(t testing.TB, events []contracttest.Event) bool {
	t.Helper()
	for _, ev := range events {
		if ev.Name != "chunk" {
			continue
		}
		var part struct {
			Bytes struct {
				Type         string `json:"type"`
				ContentBlock struct {
					Type string `json:"type"`
				} `json:"content_block"`
				Delta struct {
					Type string `json:"type"`
					Text string `json:"text"`
				} `json:"delta"`
			} `json:"bytes"`
		}
		if err := json.Unmarshal(ev.Data, &part); err != nil {
			t.Fatalf("chunk payload is not {\"bytes\": <Anthropic stream event>}: %v", err)
		}
		e := part.Bytes
		if (e.Type == "content_block_start" && e.ContentBlock.Type == "tool_use") ||
			(e.Type == "content_block_delta" && e.Delta.Type == "text_delta" && e.Delta.Text != "") {
			return true
		}
	}
	return false
}
