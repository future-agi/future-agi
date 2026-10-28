package bedrock

import (
	"bytes"
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Contract tests replay each fixture under testdata/contracts/<model>/<case>
// through the real provider (ChatCompletion / StreamChatCompletion against a
// replay server) and check what comes out. See
// internal/providers/contracttest/README.md, and contract_eventstream_test.go
// for how the binary stream fixtures are generated.
//
//	go test ./internal/providers/bedrock/ -run Contract           # check
//	go test ./internal/providers/bedrock/ -run Contract -update   # rewrite goldens
//
// The adapter has two upstream APIs, chosen by model ID (isInferenceProfile):
// inference-profile IDs (global., us., ..., arn:) go to Converse /
// ConverseStream, base model IDs to InvokeModel /
// InvokeModelWithResponseStream with an Anthropic Messages body. Each has its
// own parser, so the fixtures cover both.

// Event-stream message types the APIs document: the :event-type of an event,
// or the :exception-type of an exception.
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

// streamProtocolFields are ConverseStream payload fields the parser does not
// read because, by the protocol, they carry nothing it needs. They hold for
// every model, so they live here rather than in each meta.json.
var streamProtocolFields = map[string]string{
	"messageStart:$.role":                  `always "assistant"`,
	"contentBlockStop:$.contentBlockIndex": "only closes the block; its content arrived in its deltas",
	"metadata:$.usage.totalTokens":         "derived: total_tokens is prompt + completion, as on the non-stream path",
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

// usesConverse reports whether the adapter sends c's model to Converse.
func usesConverse(c contracttest.Case) bool { return isInferenceProfile(resolveModelName(c.Model)) }

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

func contractJSON(t *testing.T, c contracttest.Case) {
	upstream := contracttest.ReplayServer(t, c)
	p := newTestProvider(t, upstream.URL)
	t.Cleanup(func() { _ = p.Close() })
	ctx, shapes := common.WithShapeRecorder(context.Background())

	resp, err := p.ChatCompletion(ctx, c.Request)
	if err != nil {
		t.Fatalf("ChatCompletion: %v", err)
	}

	contracttest.AssertGolden(t, c.GoldenPath(), resp)
	assertUpstreamRequest(t, c, upstream)

	var paths []string
	var hasOutput bool
	if usesConverse(c) {
		paths = contracttest.UnknownFields(c.Body, reflect.TypeFor[converseResponse]())
		hasOutput = converseResponseHasOutput(t, c.Body)
	} else {
		paths = contracttest.UnknownFields(c.Body, reflect.TypeFor[bedrockResponse]())
		hasOutput = invokeResponseHasOutput(t, c.Body)
	}
	contracttest.AssertKnownPaths(t, c, paths)
	contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
	contracttest.AssertSane(t, contracttest.FromResponse(resp), hasOutput)
}

func contractStream(t *testing.T, c contracttest.Case) {
	events := decodeEventStream(t, c.Body)
	known, gaps := converseStreamEvents, []string(nil)
	if !usesConverse(c) {
		known, gaps = invokeStreamEvents, invokeStreamSanityGaps
	}
	contracttest.AssertKnownEventTypes(t, events, known...)

	upstream := contracttest.ReplayServer(t, c)
	p := newTestProvider(t, upstream.URL)
	t.Cleanup(func() { _ = p.Close() })
	ctx, shapes := common.WithShapeRecorder(context.Background())

	chunks, errs := p.StreamChatCompletion(ctx, c.Request)
	res := contracttest.DrainStream(t, chunks, errs)
	if res.Err != nil {
		t.Fatalf("StreamChatCompletion: %v", res.Err)
	}

	contracttest.AssertGolden(t, c.GoldenPath(), res)
	assertUpstreamRequest(t, c, upstream)
	if usesConverse(c) {
		contracttest.AssertKnownPaths(t, c, converseStreamUnknownFields(events))
		contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
		contracttest.AssertSaneWithGaps(t, c, contracttest.FromStream(res.Chunks), converseStreamHasOutput(t, events), gaps...)
	} else {
		contracttest.AssertKnownPaths(t, c, invokeStreamUnknownFields(events))
		contracttest.AssertNoUnknownShapes(t, c, shapes.Shapes())
		contracttest.AssertSaneWithGaps(t, c, contracttest.FromStream(res.Chunks), invokeStreamHasOutput(t, events), gaps...)
	}
}

// assertUpstreamRequest pins the outbound request and checks the fixture is
// filed under the model the gateway actually asked for. Bedrock carries the
// model in the path: /model/<id>/<operation>.
func assertUpstreamRequest(t *testing.T, c contracttest.Case, upstream *contracttest.Replay) {
	t.Helper()
	req := upstream.Request(t)
	contracttest.AssertGolden(t, c.UpstreamGoldenPath(), req)

	op := map[[2]bool]string{
		{true, false}:  "converse",
		{true, true}:   "converse-stream",
		{false, false}: "invoke",
		{false, true}:  "invoke-with-response-stream",
	}[[2]bool{usesConverse(c), c.IsStream}]
	if want := "/model/" + c.Model + "/" + op; req.Path != want {
		t.Errorf("upstream request path = %q, want %q (the fixture's model and API)", req.Path, want)
	}
}

// decodeEventStream splits a binary event-stream body into events named by
// their :event-type (or, for an exception, :exception-type), with the raw
// payload as Data, using the adapter's own decoder.
func decodeEventStream(t *testing.T, body []byte) []contracttest.Event {
	t.Helper()
	var events []contracttest.Event
	r := bytes.NewReader(body)
	for {
		msg, err := readEventStreamMessage(r)
		if err != nil {
			t.Fatalf("decoding response.eventstream: %v", err)
		}
		if msg == nil {
			return events
		}
		name := msg.Headers[":event-type"]
		if msg.Headers[":message-type"] == "exception" {
			name = msg.Headers[":exception-type"]
		}
		events = append(events, contracttest.Event{Name: name, Data: msg.Payload})
	}
}

// converseStreamUnknownFields audits each ConverseStream payload against the
// struct parseConverseEvent decodes it with. Payloads the parser does not
// decode (messageStart, contentBlockStop) are audited against an empty struct,
// so every field they carry must be a protocol field. A contentBlockStart or
// contentBlockDelta whose union member the parser does not translate is left
// to the unknown-shape report. Paths are prefixed with the event type, e.g.
// "metadata:$.metrics".
func converseStreamUnknownFields(events []contracttest.Event) []string {
	var paths []string
	for _, ev := range events {
		root := ev.Name + ":$"
		var typ reflect.Type
		switch ev.Name {
		case "messageStart", "contentBlockStop":
			typ = reflect.TypeFor[struct{}]()
		case "contentBlockStart":
			if onlyMembers(converseUnionField(ev.Data, "start"), converseTranslatedStarts) {
				typ = reflect.TypeFor[converseBlockStartEvent]()
			}
		case "contentBlockDelta":
			if onlyMembers(converseUnionField(ev.Data, "delta"), converseTranslatedDeltas) {
				typ = reflect.TypeFor[converseBlockDeltaEvent]()
			}
		case "messageStop":
			typ = reflect.TypeFor[converseMessageStopEvent]()
		case "metadata":
			typ = reflect.TypeFor[converseMetadataEvent]()
		}
		if typ != nil {
			paths = append(paths, contracttest.UnknownFieldsAt(ev.Data, typ, root)...)
		}
	}
	return dropProtocolFields(paths)
}

// invokeStreamUnknownFields audits each InvokeModelWithResponseStream payload
// against bedrockStreamEvent, what parseStreamPayload decodes it as.
func invokeStreamUnknownFields(events []contracttest.Event) []string {
	var paths []string
	for _, ev := range events {
		paths = append(paths, contracttest.UnknownFieldsAt(ev.Data, reflect.TypeFor[bedrockStreamEvent](), ev.Name+":$")...)
	}
	return paths
}

func dropProtocolFields(paths []string) []string {
	kept := paths[:0]
	for _, p := range paths {
		if _, benign := streamProtocolFields[p]; !benign {
			kept = append(kept, p)
		}
	}
	return kept
}

// onlyMembers reports whether the JSON object union has members, all of them
// in translated.
func onlyMembers(union json.RawMessage, translated map[string]bool) bool {
	var members map[string]json.RawMessage
	if json.Unmarshal(union, &members) != nil || len(members) == 0 {
		return false
	}
	for name := range members {
		if !translated[name] {
			return false
		}
	}
	return true
}

// converseResponseHasOutput reports whether a Converse response carried text
// or a tool call, which the canonical response must then carry too.
func converseResponseHasOutput(t *testing.T, body []byte) bool {
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
func invokeResponseHasOutput(t *testing.T, body []byte) bool {
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
func converseStreamHasOutput(t *testing.T, events []contracttest.Event) bool {
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
// each chunk's base64 bytes hold one Anthropic stream event.
func invokeStreamHasOutput(t *testing.T, events []contracttest.Event) bool {
	t.Helper()
	for _, ev := range events {
		if ev.Name != "chunk" {
			continue
		}
		var part struct {
			Bytes []byte `json:"bytes"`
		}
		if err := json.Unmarshal(ev.Data, &part); err != nil {
			t.Fatalf("chunk payload: %v", err)
		}
		var e struct {
			Type         string `json:"type"`
			ContentBlock struct {
				Type string `json:"type"`
			} `json:"content_block"`
			Delta struct {
				Type string `json:"type"`
				Text string `json:"text"`
			} `json:"delta"`
		}
		if err := json.Unmarshal(part.Bytes, &e); err != nil {
			t.Fatalf("chunk bytes are not an Anthropic stream event: %v", err)
		}
		if (e.Type == "content_block_start" && e.ContentBlock.Type == "tool_use") ||
			(e.Type == "content_block_delta" && e.Delta.Type == "text_delta" && e.Delta.Text != "") {
			return true
		}
	}
	return false
}
