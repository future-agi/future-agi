package openai

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contractspec"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// TestSpecCoverage checks the adapter against OpenAI's official OpenAPI spec
// (the subsets in internal/providers/contractspec/subsets/openai, pinned
// from github.com/openai/openai-openapi): every response path is mapped by
// the canonical types the adapter decodes into, forwarded through their
// Extra, or allowlisted in testdata/spec_allowlist.json; and every finish
// reason, Responses output item type, incomplete reason and Responses stream
// event the spec lists is handled. It runs offline. See
// internal/providers/contractspec/README.md.
//
//	go test ./internal/providers/openai/ -run SpecCoverage -v                      # coverage figures
//	CONTRACTSPEC_DRAFT=1 go test ./internal/providers/openai/ -run SpecCoverage    # rewrite the allowlist, TODO reasons for new findings
//
// The OpenAI-compatible presets this adapter serves (groq, together,
// openrouter) have subsets of their own; wire each as another Check with
// Roots: openAIRoots(t, "<preset>") and its own allowlist file
// (testdata/spec_allowlist.<preset>.json).
func TestSpecCoverage(t *testing.T) {
	contractspec.Check(t, contractspec.Coverage{
		Provider: "openai",
		Roots:    openAIRoots(t, "openai"),
	})
}

// TestSpecCoveragePresets is TestSpecCoverage for the OpenAI-compatible
// presets with a fetchable spec (contractspec/subsets/<preset>), each with
// its own allowlist, testdata/spec_allowlist.<preset>.json. The presets
// whose spec cannot be fetched (contractspec/subsets/unfetchable.json) are
// covered by their contract fixtures alone.
func TestSpecCoveragePresets(t *testing.T) {
	for _, preset := range []string{"groq", "together", "openrouter"} {
		t.Run(preset, func(t *testing.T) {
			contractspec.Check(t, contractspec.Coverage{
				Provider:  preset,
				Allowlist: "testdata/spec_allowlist." + preset + ".json",
				Roots:     openAIRoots(t, preset),
			})
		})
	}
}

// TestSpecEnums checks the value sets the contract tests accept
// (ContractEnums, by dialect and endpoint) against the spec subsets of the
// dialects that have one: a value the spec lists that a declaration lacks
// would fail the first fixture or live response carrying it.
func TestSpecEnums(t *testing.T) {
	for dialect, byEndpoint := range ContractEnums {
		provider := dialect
		if dialect == officialDialect {
			provider = "openai"
		}
		have, err := contractspec.Load(provider)
		if err != nil {
			t.Fatal(err)
		}
		if len(have) == 0 {
			continue // no fetchable spec (perplexity, mistral)
		}
		present := map[string]bool{}
		for _, s := range have {
			present[s.Root] = true
		}
		subset := func(root string) string {
			if present[root] {
				return provider + "/" + root
			}
			return ""
		}
		for endpoint, specs := range byEndpoint {
			var routing contractspec.EnumRouting
			switch endpoint {
			case contracttest.EndpointChat:
				routing = contractspec.EnumRouting{Body: subset("chat"), Stream: subset("chat_stream")}
			case contracttest.EndpointChatViaResponses, contracttest.EndpointResponses:
				routing = contractspec.EnumRouting{Body: subset("responses"), Stream: subset("responses_stream")}
			case contracttest.EndpointListModels:
				routing = contractspec.EnumRouting{Body: subset("models")}
			default: // completions, embeddings, images, transcription, translation
				routing = contractspec.EnumRouting{Body: subset(endpoint)}
			}
			routing.Error = subset("errors")
			decls := make([]contractspec.EnumDecl, len(specs))
			for i, e := range specs {
				decls[i] = contractspec.EnumDecl{Name: dialect + " " + endpoint + " " + e.Spec, Paths: e.Paths, Values: e.Values}
			}
			contractspec.CheckDeclaredEnums(t, routing, decls)
		}
	}
}

// captureExtra are the canonical types whose UnmarshalJSON keeps unknown keys
// in Extra and forwards them to the client.
var captureExtra = []reflect.Type{
	reflect.TypeFor[models.ChatCompletionResponse](),
	reflect.TypeFor[models.StreamChunk](),
	reflect.TypeFor[models.Usage](),
}

// openAIRoots declares what this adapter decodes each root of an
// OpenAI-format provider's spec with, keeping the roots provider's committed
// subsets have (a preset's spec models fewer endpoints than OpenAI's).
func openAIRoots(t *testing.T, provider string) []contractspec.Root {
	t.Helper()
	have, err := contractspec.Load(provider)
	if err != nil {
		t.Fatal(err)
	}
	present := map[string]bool{}
	for _, s := range have {
		present[s.ID()] = true
	}
	var roots []contractspec.Root
	for _, r := range allOpenAIRoots(provider) {
		if present[r.Subset] {
			roots = append(roots, r)
		}
	}
	return roots
}

func allOpenAIRoots(provider string) []contractspec.Root {
	finish := contractspec.EnumCheck{By: "reportFinishReason", Handled: finishReasonCanonical}
	chatFinish, chunkFinish := finish, finish
	chatFinish.Path = "$.choices[].finish_reason"
	chunkFinish.Path = "chunk:$.choices[].finish_reason"
	return []contractspec.Root{
		{
			Subset:      provider + "/chat",
			Bindings:    []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.ChatCompletionResponse]())},
			Passthrough: captureExtra,
			Enums:       []contractspec.EnumCheck{chatFinish},
		},
		{
			Subset:      provider + "/chat_stream",
			Bindings:    []contractspec.Binding{contractspec.Bind("chunk:$", reflect.TypeFor[models.StreamChunk]())},
			Passthrough: captureExtra,
			Enums:       []contractspec.EnumCheck{chunkFinish},
		},
		{
			Subset:   provider + "/errors",
			Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.ErrorResponse]())},
		},
		{
			Subset:      provider + "/completions",
			Bindings:    []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.CompletionResponse]())},
			Passthrough: []reflect.Type{reflect.TypeFor[models.Usage]()},
		},
		{
			Subset:   provider + "/embeddings",
			Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.EmbeddingResponse]())},
		},
		{
			Subset:   provider + "/images",
			Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.ImageResponse]())},
		},
		{
			Subset:   provider + "/transcription",
			Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.TranscriptionResponse]())},
		},
		{
			Subset:   provider + "/translation",
			Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.TranslationResponse]())},
		},
		{
			Subset:   provider + "/responses",
			Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[responsesResponse]())},
			Enums: []contractspec.EnumCheck{
				{Path: "$.output[].type", By: "toChat", Handled: outputItemTranslated},
				{Path: "$.incomplete_details.reason", By: "finishReason", Handled: incompleteReasonMapped},
			},
		},
		{
			Subset:    provider + "/responses_stream",
			EachEvent: []reflect.Type{reflect.TypeFor[responsesStreamEvent]()},
			Events:    responsesEventConverted,
		},
		{
			Subset:   provider + "/models",
			Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[models.ModelListResponse]())},
		},
	}
}

// finishReasonCanonical reports whether reportFinishReason accepts a finish
// reason (the adapter forwards finish reasons as they are).
func finishReasonCanonical(v string) bool {
	ctx, rec := common.WithShapeRecorder(context.Background())
	reportFinishReason(common.NewShapeReporter(ctx, adapterName, "spec-test"), v)
	return len(rec.Shapes()) == 0
}

// outputItemTranslated reports whether toChat turns an output item of type v
// into content or a tool call. The item carries every field the translated
// types read, so only the type decides.
func outputItemTranslated(v string) bool {
	item, _ := json.Marshal(map[string]any{
		"type": v, "id": "i", "call_id": "c", "name": "f", "arguments": "{}",
		"content": []map[string]any{{"type": "output_text", "text": "x"}},
	})
	var r responsesResponse
	if err := json.Unmarshal([]byte(`{"output":[`+string(item)+`]}`), &r); err != nil {
		return false
	}
	msg := r.toChat().Choices[0].Message
	var text string
	_ = json.Unmarshal(msg.Content, &text)
	return text != "" || len(msg.ToolCalls) > 0
}

// incompleteReasonMapped reports whether an incomplete response's reason gets
// a finish reason of its own rather than the "stop" default.
func incompleteReasonMapped(v string) bool {
	r := &responsesResponse{Status: "incomplete", IncompleteDetails: &responsesIncomplete{Reason: v}}
	return r.finishReason(false) != "stop"
}

// responsesEventConverted reports whether convert acts on a stream event: it
// emits a chunk, ends the stream, or fails it. Every field the converter
// reads is set, so only the type decides.
func responsesEventConverted(v string) bool {
	c := &responsesStreamConverter{toolIndex: map[string]int{"i": 0}}
	chunks, err := c.convert(&responsesStreamEvent{
		Type: v, Delta: "x", ItemID: "i", Message: "m",
		Item:     &responsesOutputItem{Type: "function_call", ID: "j"},
		Response: &responsesResponse{},
	})
	return len(chunks) > 0 || err != nil || c.done
}
