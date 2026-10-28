package anthropic

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contractspec"
)

// TestSpecCoverage checks the adapter against Anthropic's official API spec
// (the subsets in internal/providers/contractspec/subsets/anthropic, pinned
// from the spec bundled in anthropic-sdk-python): every response path is
// mapped by the structs below or allowlisted in testdata/spec_allowlist.json,
// and every stop reason, block type, delta type, stream event and error type
// the spec lists is handled. It runs offline. See
// internal/providers/contractspec/README.md.
//
//	go test ./internal/providers/anthropic/ -run SpecCoverage -v                      # coverage figures
//	CONTRACTSPEC_DRAFT=1 go test ./internal/providers/anthropic/ -run SpecCoverage    # rewrite the allowlist, TODO reasons for new findings
func TestSpecCoverage(t *testing.T) {
	contractspec.Check(t, contractspec.Coverage{
		Provider: "anthropic",
		Roots: []contractspec.Root{
			{
				Subset: "anthropic/messages",
				Bindings: []contractspec.Binding{
					contractspec.Bind("$", reflect.TypeFor[anthropicResponse]()),
					// citationsForBlock decodes the raw citations later.
					contractspec.Bind("$.content[].citations", reflect.TypeFor[[]anthropicCitation]()),
				},
				Enums: []contractspec.EnumCheck{
					{Path: "$.stop_reason", By: "lookupStopReason", Handled: stopReasonMapped},
					{Path: "$.content[].type", By: "translateResponse", Handled: blockTranslated},
				},
			},
			{
				Subset:    "anthropic/stream",
				EachEvent: []reflect.Type{reflect.TypeFor[streamEvent]()},
				Bindings: []contractspec.Binding{
					// The raw payloads the handlers decode in a second step.
					contractspec.Bind("message_start:$.message", reflect.TypeFor[messageStartData]()),
					contractspec.Bind("content_block_start:$.content_block", reflect.TypeFor[contentBlockStartData]()),
					contractspec.Bind("content_block_delta:$.delta", reflect.TypeFor[textDelta](), reflect.TypeFor[inputJSONDelta]()),
					contractspec.Bind("message_delta:$.delta", reflect.TypeFor[messageDeltaData]()),
				},
				Enums: []contractspec.EnumCheck{
					{Path: "message_delta:$.delta.stop_reason", By: "lookupStopReason", Handled: stopReasonMapped},
					{Path: "content_block_start:$.content_block.type", By: "handleContentBlockStart", Handled: streamBlockHandled},
					{Path: "content_block_delta:$.delta.type", By: "handleContentBlockDelta", Handled: streamDeltaHandled},
				},
				Events: streamEventHandled,
			},
			{
				Subset:   "anthropic/errors",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[anthropicErrorResponse]())},
				Enums: []contractspec.EnumCheck{
					{Path: "$.error.type", By: "mapAnthropicStatus", Handled: errorTypeMapped},
				},
			},
		},
		Elsewhere: map[string]string{
			"anthropic/models": "decoded by contractwatch's AnthropicLister (cmd/model-watch); the adapter's ListModels returns nil",
		},
	})
}

// TestSpecEnums checks the value sets the contract tests accept
// (ContractEnums) against the spec: a value the spec lists that the
// declaration lacks would fail the first fixture or live response carrying
// it.
func TestSpecEnums(t *testing.T) {
	decls := make([]contractspec.EnumDecl, len(ContractEnums))
	for i, e := range ContractEnums {
		decls[i] = contractspec.EnumDecl{Name: e.Spec, Paths: e.Paths, Values: e.Values}
	}
	contractspec.CheckDeclaredEnums(t, contractspec.EnumRouting{
		Body: "anthropic/messages", Stream: "anthropic/stream", Error: "anthropic/errors",
	}, decls)
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

// blockTranslated reports whether translateResponse handles a content block
// of type v rather than reporting it as unknown.
func blockTranslated(v string) bool {
	shapes := reports(func(shape *common.ShapeReporter) {
		translateResponse(&anthropicResponse{Content: []anthropicContentBlock{{Type: v}}}, shape)
	})
	return !reported(shapes, common.ShapeUnknownBlock, v)
}

// parseEvent feeds one SSE event to the real stream parser.
func parseEvent(event string, data any) []common.UnknownShape {
	raw, _ := json.Marshal(data)
	return reports(func(shape *common.ShapeReporter) {
		s := newStreamState()
		s.shape = shape
		_, _, _ = s.parseSSELine(event, string(raw))
	})
}

func streamBlockHandled(v string) bool {
	shapes := parseEvent("content_block_start", map[string]any{
		"type": "content_block_start", "index": 0, "content_block": map[string]any{"type": v},
	})
	return !reported(shapes, common.ShapeUnknownBlock, v)
}

func streamDeltaHandled(v string) bool {
	shapes := parseEvent("content_block_delta", map[string]any{
		"type": "content_block_delta", "index": 0, "delta": map[string]any{"type": v},
	})
	return !reported(shapes, common.ShapeUnknownDelta, v)
}

func streamEventHandled(v string) bool {
	return !reported(parseEvent(v, map[string]any{"type": v}), common.ShapeUnknownEvent, v)
}

// errorTypeMapped reports whether an error type gets its own status rather
// than the 502 fallback every unrecognised type gets.
func errorTypeMapped(v string) bool {
	return mapAnthropicStatus(http.StatusInternalServerError, v) != http.StatusBadGateway
}
