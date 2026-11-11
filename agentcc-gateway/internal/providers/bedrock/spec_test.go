package bedrock

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contractspec"
)

// TestSpecCoverage checks the adapter against the official API specs (the
// subsets in internal/providers/contractspec/subsets): the Bedrock Runtime
// Smithy model (bedrock/*, pinned from github.com/aws/api-models-aws) for
// Converse, ConverseStream, their exceptions and InvokeModelWithResponseStream's
// envelope, and Anthropic's Messages API (anthropic/*) for the InvokeModel
// bodies Claude returns, which the Smithy model leaves a blob: the Messages
// response, and the stream events a chunk's bytes carry. Every response path
// is mapped by the structs below or allowlisted in
// testdata/spec_allowlist.json, and every stop reason, block, delta, stream
// event and exception the specs list is handled or allowlisted. It runs
// offline. See internal/providers/contractspec/README.md.
//
//	go test ./internal/providers/bedrock/ -run SpecCoverage -v                      # coverage figures
//	CONTRACTSPEC_DRAFT=1 go test ./internal/providers/bedrock/ -run SpecCoverage    # rewrite the allowlist, TODO reasons for new findings
//
// The anthropic/stream root is bound the way parseStreamPayload is written
// to read an Anthropic event. It never gets one today: it decodes the chunk
// envelope {"bytes": ...} as if it were the event (gap B1, allowlisted on
// bedrock/invoke_stream), so what this root reports as mapped is what the
// parser would keep once B1 is fixed.
//
// Bedrock's model list (ListFoundationModels, ListInferenceProfiles) is in
// the control-plane Smithy model, which is not vendored, so it has no subset
// to list in Elsewhere; the adapter's ListModels calls no upstream
// (TestContractListModelsCallsNoUpstream).
func TestSpecCoverage(t *testing.T) {
	contractspec.Check(t, contractspec.Coverage{
		Provider: "bedrock",
		Roots: []contractspec.Root{
			{
				Subset:   "bedrock/converse",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[converseResponse]())},
				Enums: []contractspec.EnumCheck{
					{Path: "$.stopReason", By: "lookupStopReason", Handled: stopReasonMapped},
					{Path: "$.output.message.content[].<union>", By: "translateConverseResponse", Handled: converseBlockTranslated},
				},
			},
			{
				Subset: "bedrock/converse_stream",
				Bindings: []contractspec.Binding{
					// parseConverseEvent decodes each event by :event-type;
					// messageStart and contentBlockStop it does not decode.
					contractspec.Bind("messageStart:$", reflect.TypeFor[struct{}]()),
					contractspec.Bind("contentBlockStart:$", reflect.TypeFor[converseBlockStartEvent]()),
					contractspec.Bind("contentBlockDelta:$", reflect.TypeFor[converseBlockDeltaEvent]()),
					contractspec.Bind("contentBlockStop:$", reflect.TypeFor[struct{}]()),
					contractspec.Bind("messageStop:$", reflect.TypeFor[converseMessageStopEvent]()),
					contractspec.Bind("metadata:$", reflect.TypeFor[converseMetadataEvent]()),
				},
				Enums: []contractspec.EnumCheck{
					{Path: "messageStop:$.stopReason", By: "lookupStopReason", Handled: stopReasonMapped},
					{Path: "contentBlockStart:$.start.<union>", By: "parseConverseEvent", Handled: converseStartHandled},
					{Path: "contentBlockDelta:$.delta.<union>", By: "parseConverseEvent", Handled: converseDeltaHandled},
				},
				Events: converseEventHandled,
			},
			{
				// An HTTP error: x-amzn-ErrorType names the exception, the
				// body is its members, decoded by parseBedrockError.
				Subset:    "bedrock/converse_errors",
				EachEvent: []reflect.Type{reflect.TypeFor[bedrockErrorResponse]()},
				Events:    exceptionMapped,
			},
			{
				Subset:   "bedrock/invoke_stream",
				Bindings: []contractspec.Binding{contractspec.Bind("chunk:$", reflect.TypeFor[bedrockStreamEvent]())},
				Events:   invokeEventHandled,
			},
			{
				Subset:   "anthropic/messages",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[bedrockResponse]())},
				Enums: []contractspec.EnumCheck{
					{Path: "$.stop_reason", By: "lookupStopReason", Handled: stopReasonMapped},
					{Path: "$.content[].type", By: "translateResponse", Handled: invokeBlockTranslated},
				},
			},
			{
				Subset:    "anthropic/stream",
				EachEvent: []reflect.Type{reflect.TypeFor[bedrockStreamEvent]()},
				Bindings: []contractspec.Binding{
					// The payloads parseStreamPayload decodes in a second step.
					contractspec.Bind("content_block_start:$", reflect.TypeFor[invokeBlockStartPayload]()),
					contractspec.Bind("message_start:$.message", reflect.TypeFor[invokeMessageStart]()),
					contractspec.Bind("content_block_delta:$.delta", reflect.TypeFor[invokeTextDelta](), reflect.TypeFor[invokeInputJSONDelta]()),
					contractspec.Bind("message_delta:$.delta", reflect.TypeFor[invokeMessageDelta]()),
				},
				Enums: []contractspec.EnumCheck{
					{Path: "message_delta:$.delta.stop_reason", By: "lookupStopReason", Handled: stopReasonMapped},
					{Path: "content_block_start:$.content_block.type", By: "parseStreamPayload", Handled: invokeStreamBlockHandled},
					{Path: "content_block_delta:$.delta.type", By: "parseStreamPayload", Handled: invokeStreamDeltaHandled},
				},
				Events: anthropicEventHandled,
			},
		},
	})
}

// TestSpecEnums checks the value sets the contract tests accept
// (ContractEnums) against the specs: a value a spec lists that the
// declaration lacks would fail the first fixture or live response carrying
// it. Converse declarations are compared with the Smithy subsets, and the
// InvokeModel ones (Anthropic bodies; "chunk:$.bytes.X" in a stream, which
// is the X of whichever Anthropic event has it) with Anthropic's.
func TestSpecEnums(t *testing.T) {
	var converse, invoke []contractspec.EnumDecl
	for _, e := range ContractEnums {
		d := contractspec.EnumDecl{Name: e.Spec, Paths: append([]string(nil), e.Paths...), Values: e.Values}
		if strings.HasPrefix(e.Spec, "com.amazonaws.bedrockruntime#") {
			converse = append(converse, d)
			continue
		}
		for i, p := range d.Paths {
			if rest, ok := strings.CutPrefix(p, "chunk:$.bytes"); ok {
				d.Paths[i] = "*:$" + rest
			}
		}
		invoke = append(invoke, d)
	}
	contractspec.CheckDeclaredEnums(t, contractspec.EnumRouting{Body: "bedrock/converse", Stream: "bedrock/converse_stream"}, converse)
	contractspec.CheckDeclaredEnums(t, contractspec.EnumRouting{Body: "anthropic/messages", Stream: "anthropic/stream"}, invoke)
}

func stopReasonMapped(v string) bool {
	_, ok := lookupStopReason(v)
	return ok
}

// reports runs fn with a shape reporter and returns what it reported: the
// adapter's own record of what it skipped.
func reports(fn func(shape *common.ShapeReporter)) []common.UnknownShape {
	ctx, rec := common.WithShapeRecorder(context.Background())
	fn(common.NewShapeReporter(ctx, adapterName, "spec-test"))
	return rec.Shapes()
}

func reported(shapes []common.UnknownShape, kind common.ShapeKind, value string) bool {
	for _, s := range shapes {
		if s.Kind == kind && s.Value == value {
			return true
		}
	}
	return false
}

// unionMember is a one-member union value for member m: a string for text,
// an empty object otherwise.
func unionMember(m string) map[string]any {
	if m == "text" {
		return map[string]any{m: "x"}
	}
	return map[string]any{m: map[string]any{}}
}

// converseBlockTranslated reports whether a Converse response content block
// with member v is translated rather than reported unknown.
func converseBlockTranslated(v string) bool {
	body, _ := json.Marshal(map[string]any{"output": map[string]any{"message": map[string]any{
		"role": "assistant", "content": []any{unionMember(v)}}}, "stopReason": "end_turn"})
	return !reported(reports(func(shape *common.ShapeReporter) { reportUnknownConverseBlocks(shape, body) }),
		common.ShapeUnknownBlock, v)
}

// converseEvent feeds one ConverseStream message to the real parser.
func converseEvent(event string, payload any) []common.UnknownShape {
	raw, _ := json.Marshal(payload)
	msg := &eventStreamMessage{Headers: map[string]string{":event-type": event, ":message-type": "event"}, Payload: raw}
	if isException(event) {
		msg.Headers = map[string]string{":exception-type": event, ":message-type": "exception"}
	}
	return reports(func(shape *common.ShapeReporter) {
		s := newConverseStreamState("spec-test")
		s.shape = shape
		_, _, _ = s.parseConverseEvent(msg)
	})
}

func converseStartHandled(v string) bool {
	return !reported(converseEvent("contentBlockStart", map[string]any{"start": unionMember(v), "contentBlockIndex": 0}),
		common.ShapeUnknownBlock, v)
}

func converseDeltaHandled(v string) bool {
	return !reported(converseEvent("contentBlockDelta", map[string]any{"delta": unionMember(v), "contentBlockIndex": 0}),
		common.ShapeUnknownDelta, v)
}

// converseEventHandled: the parser translates the event, or for an
// exception ends the stream with an error, rather than skipping it.
func converseEventHandled(v string) bool {
	return !reported(converseEvent(v, map[string]any{}), common.ShapeUnknownEvent, v)
}

// invokeEvent feeds one InvokeModelWithResponseStream message to the real
// parser, its payload as given.
func invokeEvent(event string, payload any) []common.UnknownShape {
	raw, _ := json.Marshal(payload)
	msg := &eventStreamMessage{Headers: map[string]string{":event-type": event, ":message-type": "event"}, Payload: raw}
	if isException(event) {
		msg.Headers = map[string]string{":exception-type": event, ":message-type": "exception"}
	}
	return reports(func(shape *common.ShapeReporter) {
		s := newBedrockStreamState("spec-test")
		s.shape = shape
		_, _, _ = s.parseStreamPayload(msg)
	})
}

// invokeEventHandled feeds the envelope Bedrock sends: a chunk is
// {"bytes": <base64 Anthropic event>}.
func invokeEventHandled(v string) bool {
	payload := any(map[string]any{})
	if v == "chunk" {
		payload = map[string]any{"bytes": []byte(`{"type":"message_stop"}`)}
	}
	return !reported(invokeEvent(v, payload), common.ShapeUnknownEvent, v)
}

// anthropicEventHandled feeds the parser an Anthropic event as a chunk
// payload, the way it is written to read one (see gap B1 above).
func anthropicEventHandled(v string) bool {
	return !reported(invokeEvent("chunk", map[string]any{"type": v}), common.ShapeUnknownEvent, v)
}

func invokeStreamBlockHandled(v string) bool {
	return !reported(invokeEvent("chunk", map[string]any{
		"type": "content_block_start", "index": 0, "content_block": map[string]any{"type": v},
	}), common.ShapeUnknownBlock, v)
}

func invokeStreamDeltaHandled(v string) bool {
	return !reported(invokeEvent("chunk", map[string]any{
		"type": "content_block_delta", "index": 0, "delta": map[string]any{"type": v},
	}), common.ShapeUnknownDelta, v)
}

// invokeBlockTranslated reports whether translateResponse handles an
// InvokeModel content block of type v rather than reporting it unknown.
func invokeBlockTranslated(v string) bool {
	return !reported(reports(func(shape *common.ShapeReporter) {
		translateResponse(&bedrockResponse{Content: []bedrockContentBlock{{Type: v}}}, shape)
	}), common.ShapeUnknownBlock, v)
}

// exceptionMeaning is what each Bedrock Runtime exception is, as the
// gateway's canonical error types say it: its @httpError status in the
// Smithy model, and the type that describes it.
var exceptionMeaning = map[string]struct {
	status  int
	errType string
}{
	"ValidationException":           {400, models.ErrTypeInvalidRequest},
	"ServiceQuotaExceededException": {400, models.ErrTypeRateLimit},
	"AccessDeniedException":         {403, models.ErrTypePermission},
	"ResourceNotFoundException":     {404, models.ErrTypeNotFound},
	"ModelTimeoutException":         {408, models.ErrTypeTimeout},
	"ModelErrorException":           {424, models.ErrTypeUpstream},
	"ThrottlingException":           {429, models.ErrTypeRateLimit},
	"ModelNotReadyException":        {429, models.ErrTypeServer},
	"InternalServerException":       {500, models.ErrTypeUpstream},
	"ServiceUnavailableException":   {503, models.ErrTypeUpstream},
}

// exceptionMapped reports whether parseBedrockError turns exception v, at
// its documented status, into the canonical error that describes it: the
// status class kept (a 4xx a 4xx, a 429 a 429, a 5xx a 5xx) and the type
// that says what happened. It decides by status alone (the
// x-amzn-ErrorType header is not read), so exceptions that share a status
// cannot both be right.
func exceptionMapped(v string) bool {
	m, ok := exceptionMeaning[v]
	if !ok {
		return false
	}
	got := parseBedrockError(m.status, []byte(`{"message":"spec test"}`))
	sameClass := got.Status/100 == m.status/100 && (m.status != http.StatusTooManyRequests || got.Status == m.status)
	return sameClass && got.Type == m.errType
}
