package azure

import (
	"context"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contractspec"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// TestSpecCoverage checks the adapter against Azure OpenAI's official
// data-plane spec at the adapter's default api-version (the subsets in
// internal/providers/contractspec/subsets/azure, pinned from
// github.com/Azure/azure-rest-api-specs OpenAIInference/stable/2024-10-21):
// every response path is mapped by the canonical types the adapter decodes
// into, forwarded through their Extra, or allowlisted in
// testdata/spec_allowlist.json, and every finish reason the spec lists is
// handled. It runs offline. See internal/providers/contractspec/README.md.
//
//	go test ./internal/providers/azure/ -run SpecCoverage -v                      # coverage figures
//	CONTRACTSPEC_DRAFT=1 go test ./internal/providers/azure/ -run SpecCoverage    # rewrite the allowlist, TODO reasons for new findings
//
// The spec at this api-version has no audio/speech (binary audio, passed
// through) and no model list (the authoring spec's Models_List, not
// vendored); the contract tests cover both.
func TestSpecCoverage(t *testing.T) {
	finish := func(path string) contractspec.EnumCheck {
		return contractspec.EnumCheck{Path: path, By: "reportFinishReason", Handled: finishReasonCanonical}
	}
	contractspec.Check(t, contractspec.Coverage{
		Provider: "azure",
		Roots: []contractspec.Root{
			{
				Subset:      "azure/chat",
				Bindings:    []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.ChatCompletionResponse]())},
				Passthrough: captureExtra,
				Enums:       []contractspec.EnumCheck{finish("$.choices[].finish_reason")},
			},
			{
				Subset:      "azure/chat_stream",
				Bindings:    []contractspec.Binding{contractspec.Bind("chunk:$", reflect.TypeFor[models.StreamChunk]())},
				Passthrough: captureExtra,
				Enums:       []contractspec.EnumCheck{finish("chunk:$.choices[].finish_reason")},
			},
			{
				Subset:   "azure/errors",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.ErrorResponse]())},
			},
			{
				Subset:   "azure/embeddings",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.EmbeddingResponse]())},
			},
			{
				Subset:   "azure/images",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.ImageResponse]())},
			},
			{
				// models.NewTranscriptionResponse lifts text and passes the
				// body through unchanged.
				Subset:   "azure/transcription",
				Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.TranscriptionResponse]())},
			},
		},
	})
}

// TestSpecEnums checks the value sets the contract tests accept (each
// endpoint's EnumSpec sets) against the spec: a value the spec lists that a
// declaration lacks would fail the first fixture or live response carrying
// it.
func TestSpecEnums(t *testing.T) {
	for _, set := range []struct {
		enums   []contractspec.EnumDecl
		routing contractspec.EnumRouting
	}{
		{enumDecls(chatEnums), contractspec.EnumRouting{Body: "azure/chat", Stream: "azure/chat_stream", Error: "azure/errors"}},
		{enumDecls(embeddingEnums), contractspec.EnumRouting{Body: "azure/embeddings"}},
		{enumDecls(imageEnums), contractspec.EnumRouting{Body: "azure/images"}},
		{enumDecls(transcriptionEnums), contractspec.EnumRouting{Body: "azure/transcription"}},
		// listModelEnums: the models list is in the authoring spec, which
		// has no subset.
	} {
		contractspec.CheckDeclaredEnums(t, set.routing, set.enums)
	}
}

func enumDecls(specs []contracttest.EnumSpec) []contractspec.EnumDecl {
	out := make([]contractspec.EnumDecl, len(specs))
	for i, e := range specs {
		out[i] = contractspec.EnumDecl{Name: e.Spec, Paths: e.Paths, Values: e.Values}
	}
	return out
}

// captureExtra are the canonical types whose UnmarshalJSON keeps unknown keys
// in Extra and forwards them to the client.
var captureExtra = []reflect.Type{
	reflect.TypeFor[models.ChatCompletionResponse](),
	reflect.TypeFor[models.StreamChunk](),
	reflect.TypeFor[models.Usage](),
}

// finishReasonCanonical reports whether reportFinishReason accepts a finish
// reason (the adapter forwards finish reasons as they are, and reports the
// ones the inbound translators do not know).
func finishReasonCanonical(v string) bool {
	ctx, rec := common.WithShapeRecorder(context.Background())
	reportFinishReason(common.NewShapeReporter(ctx, adapterName, "spec-test"), v)
	return len(rec.Shapes()) == 0
}
