package gemini

import (
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/providers/contractspec"
)

// TestSpecCoverage checks the adapter against Google's official API specs:
// the generativelanguage v1beta discovery document (Gemini API) and the
// aiplatform v1beta1 one (Vertex AI, the version New builds Vertex AI URLs
// with), as flattened in internal/providers/contractspec/subsets/{gemini,vertex}.
// Every response path is mapped by the adapter's structs or allowlisted in
// testdata/spec_allowlist.json (Gemini API) and
// testdata/spec_allowlist.vertex.json (Vertex AI), and every finish reason the
// spec lists is handled by lookupGeminiFinishReason or allowlisted. It runs
// offline. See internal/providers/contractspec/README.md.
//
// Discovery documents model neither Google's error envelope
// ({"error": google.rpc.Status}) nor the alt=sse stream framing, so there is
// no errors root and no stream root here: streamGenerateContent sends the
// generate_content schema per chunk, and the error contract is covered only
// by the contract tests' error cases (testdata/contracts/*/error_*,
// stream_error*, and ContractEnums' google.rpc.Code set).
//
//	go test ./internal/providers/gemini/ -run SpecCoverage -v                      # coverage figures
//	CONTRACTSPEC_DRAFT=1 go test ./internal/providers/gemini/ -run SpecCoverage    # rewrite the allowlists, TODO reasons for new findings
func TestSpecCoverage(t *testing.T) {
	finishReasons := []contractspec.EnumCheck{
		{Path: "$.candidates[].finishReason", By: "lookupGeminiFinishReason", Handled: finishReasonMapped},
	}
	t.Run("gemini", func(t *testing.T) {
		contractspec.Check(t, contractspec.Coverage{
			Provider: "gemini",
			Roots: []contractspec.Root{
				{
					// ChatCompletion and every alt=sse chunk of
					// StreamChatCompletion (translateResponse,
					// parseStreamData), and CreateSpeech
					// (extractAudioFromResponse).
					Subset:   "gemini/generate_content",
					Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[geminiResponse]())},
					Enums:    finishReasons,
				},
				{
					Subset:   "gemini/embed_content",
					Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[geminiEmbedResponse]())},
				},
				{
					Subset:   "gemini/batch_embed_contents",
					Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[geminiBatchEmbedResponse]())},
				},
				{
					Subset:   "gemini/predict",
					Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[geminiPredictResponse]())},
				},
			},
			Elsewhere: map[string]string{
				"gemini/models": "decoded by contractwatch's GeminiLister (cmd/model-watch); the adapter's ListModels returns nil " +
					"without calling it (gap G26, contract case gemini-3.8-flash/list_models)",
			},
		})
	})
	t.Run("vertex", func(t *testing.T) {
		contractspec.Check(t, contractspec.Coverage{
			Provider:  "vertex",
			Allowlist: "testdata/spec_allowlist.vertex.json",
			Roots: []contractspec.Root{
				{
					// The same parser as the Gemini API's: New only changes
					// the URL and the credential for Vertex AI. ChatCompletion,
					// every alt=sse chunk of StreamChatCompletion, and
					// CreateSpeech (extractAudioFromResponse).
					Subset:   "vertex/generate_content",
					Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[geminiResponse]())},
					Enums:    finishReasons,
				},
				{
					Subset:   "vertex/embed_content",
					Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[geminiEmbedResponse]())},
				},
				{
					Subset:   "vertex/predict",
					Bindings: []contractspec.Binding{contractspec.Bind("$", reflect.TypeFor[geminiPredictResponse]())},
				},
			},
		})
	})
}

// finishReasonMapped reports whether lookupGeminiFinishReason maps v rather
// than falling back to "stop".
func finishReasonMapped(v string) bool {
	_, ok := lookupGeminiFinishReason(v)
	return ok
}

// specSubsetByRoot names the subset that holds each ContractEnums document
// root. Roots without one are not compared: count_tokens and
// vertex_count_tokens (no subset: countTokens is relayed unread on both APIs),
// list_models (Model in discovery has no modelStage; the recorded listing
// does) and the error roots (discovery does not model the error envelope).
// The native passthroughs' roots (genai, genai_embed and their vertex_
// forms) are relayed unread but carry the same schema as the parsed roots,
// so their enums are compared with those subsets.
var specSubsetByRoot = map[string]string{
	"$":                    "gemini/generate_content",
	"tts:$":                "gemini/generate_content",
	"genai:$":              "gemini/generate_content",
	"embed:$":              "gemini/embed_content",
	"genai_embed:$":        "gemini/embed_content",
	"batch_embed:$":        "gemini/batch_embed_contents",
	"vertex:$":             "vertex/generate_content",
	"vertex_tts:$":         "vertex/generate_content",
	"vertex_genai:$":       "vertex/generate_content",
	"vertex_embed:$":       "vertex/embed_content",
	"vertex_genai_embed:$": "vertex/embed_content",
}

// TestSpecEnums checks the value sets the contract tests accept
// (ContractEnums) against the specs: a value the spec lists that the
// declaration lacks would fail the first fixture or live response carrying
// it. ContractEnums roots each document by API and endpoint, so every
// declared path is compared with the subset of its root.
func TestSpecEnums(t *testing.T) {
	bySubset := map[string][]contractspec.EnumDecl{}
	for _, e := range ContractEnums {
		paths := map[string][]string{}
		for _, p := range e.Paths {
			root, rest := splitRoot(p)
			if subset, ok := specSubsetByRoot[root]; ok {
				paths[subset] = append(paths[subset], "$"+rest)
			}
		}
		for subset, ps := range paths {
			bySubset[subset] = append(bySubset[subset], contractspec.EnumDecl{Name: e.Spec, Paths: ps, Values: e.Values})
		}
	}
	for _, subset := range sortedKeys(bySubset) {
		t.Run(strings.ReplaceAll(subset, "/", "_"), func(t *testing.T) {
			contractspec.CheckDeclaredEnums(t, contractspec.EnumRouting{Body: subset}, bySubset[subset])
		})
	}
}

// splitRoot splits a ContractEnums path into its document root and the rest:
// "tts:$.a.b" -> ("tts:$", ".a.b"), "$.a" -> ("$", ".a").
func splitRoot(p string) (root, rest string) {
	i := strings.Index(p, "$")
	if i < 0 {
		return "", p
	}
	return p[:i+1], p[i+1:]
}
