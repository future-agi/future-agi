package cohere

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contractspec"
)

// TestSpecCoverage checks the adapter against Cohere's official API spec
// (the subsets in internal/providers/contractspec/subsets/cohere, pinned from
// cohere-openapi.yaml in cohere-ai/cohere-developer-experience): every path
// of the chat, stream, error, embed and rerank responses is mapped by the
// structs below or allowlisted in testdata/spec_allowlist.json, and every
// finish reason, content type and stream event the spec lists is handled.
// It runs offline. See internal/providers/contractspec/README.md.
//
//	go test ./internal/providers/cohere/ -run SpecCoverage -v                      # coverage figures
//	CONTRACTSPEC_DRAFT=1 go test ./internal/providers/cohere/ -run SpecCoverage    # rewrite the allowlist, TODO reasons for new findings
func TestSpecCoverage(t *testing.T) {
	contractspec.Check(t, contractspec.Coverage{
		Provider: "cohere",
		Roots: []contractspec.Root{
			{
				Subset:   "cohere/chat",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[cohereResponse]())},
				Enums: []contractspec.EnumCheck{
					{Path: "$.finish_reason", By: "lookupCohereFinishReason", Handled: finishReasonMapped},
					{Path: "$.message.content[].type", By: "translateResponse", Handled: contentTranslated},
				},
			},
			{
				Subset:    "cohere/chat_stream",
				EachEvent: []reflect.Type{reflect.TypeFor[streamEvent]()},
				Bindings: []contractspec.Binding{
					// The deltas parseStreamData decodes in a second step, by
					// event type.
					contractspec.Bind("content-delta:$.delta", reflect.TypeFor[contentDelta]()),
					contractspec.Bind("tool-call-start:$.delta", reflect.TypeFor[toolCallStartDelta]()),
					contractspec.Bind("tool-call-delta:$.delta", reflect.TypeFor[toolCallDeltaPayload]()),
				},
				Enums: []contractspec.EnumCheck{
					{Path: "message-end:$.delta.finish_reason", By: "parseStreamData (message-end)", Handled: streamFinishReasonMapped},
				},
				Events: streamEventHandled,
			},
			{
				Subset:   "cohere/errors",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[cohereErrorResponse]())},
			},
			{
				Subset:   "cohere/embed",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[cohereEmbedResponse]())},
			},
			{
				Subset:   "cohere/rerank",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[cohereRerankResponse]())},
			},
		},
		Elsewhere: map[string]string{
			"cohere/models": "decoded by contractwatch's CohereLister (cmd/model-watch); the adapter's ListModels returns nil without calling GET /v1/models (known gap K10, pinned by the list_models contract case)",
		},
	})
}

// TestSpecEnums checks the value sets the contract tests accept
// (ContractEnums) against the spec: a value the spec lists that the
// declaration lacks would fail the first fixture or live response carrying
// it.
func TestSpecEnums(t *testing.T) {
	decls := make([]contractspec.EnumDecl, 0, len(ContractEnums)+1)
	for _, e := range ContractEnums {
		decls = append(decls, contractspec.EnumDecl{Name: e.Spec, Paths: e.Paths, Values: e.Values})
	}
	contractspec.CheckDeclaredEnums(t, contractspec.EnumRouting{
		Body: "cohere/chat", Stream: "cohere/chat_stream", Error: "cohere/errors",
	}, decls)
	var embed []contractspec.EnumDecl
	for _, e := range embedStrict.Enums {
		embed = append(embed, contractspec.EnumDecl{Name: e.Spec, Paths: e.Paths, Values: e.Values})
	}
	contractspec.CheckDeclaredEnums(t, contractspec.EnumRouting{Body: "cohere/embed"}, embed)
}

func finishReasonMapped(v string) bool {
	_, ok := lookupCohereFinishReason(v)
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

// contentTranslated reports whether translateResponse handles a content
// part of type v rather than reporting it as unknown.
func contentTranslated(v string) bool {
	shapes := reports(func(shape *common.ShapeReporter) {
		translateResponse(&cohereResponse{Message: cohereRespMessage{Content: []cohereContentPart{{Type: v}}}}, "spec-test", shape)
	})
	return !reported(shapes, common.ShapeUnknownBlock, v)
}

// parseEvent feeds one stream event to the real parser and returns its
// chunk and what it reported.
func parseEvent(data any) (finish string, shapes []common.UnknownShape) {
	raw, _ := json.Marshal(data)
	shapes = reports(func(shape *common.ShapeReporter) {
		s := newStreamState("spec-test")
		s.shape = shape
		chunk, _, _ := s.parseStreamData(string(raw))
		if chunk != nil && len(chunk.Choices) > 0 && chunk.Choices[0].FinishReason != nil {
			finish = *chunk.Choices[0].FinishReason
		}
	})
	return finish, shapes
}

// streamFinishReasonMapped reports whether a message-end carrying finish
// reason v (under delta, where the spec puts it) ends the stream with v's
// mapping, without an unmapped-finish-reason report.
func streamFinishReasonMapped(v string) bool {
	want, ok := lookupCohereFinishReason(v)
	got, shapes := parseEvent(map[string]any{"type": "message-end", "delta": map[string]any{"finish_reason": v}})
	for _, s := range shapes {
		if s.Kind == common.ShapeUnmappedFinishReason {
			return false
		}
	}
	return ok && got == want
}

func streamEventHandled(v string) bool {
	_, shapes := parseEvent(map[string]any{"type": v})
	return !reported(shapes, common.ShapeUnknownEvent, v)
}
