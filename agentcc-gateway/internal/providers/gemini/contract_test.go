package gemini

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/url"
	"reflect"
	"slices"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// Contract tests replay each fixture under testdata/contracts/<model>/<case>
// through the real provider against a replay server and check what comes
// out. See internal/providers/contracttest/README.md.
//
//	go test ./internal/providers/gemini/ -run Contract           # check
//	go test ./internal/providers/gemini/ -run Contract -update   # rewrite goldens
//
// Gemini specifics, against the Anthropic template:
//
//   - The stream is data-only SSE (alt=sse): no event names, and a chunk has
//     no type. Every chunk is a whole GenerateContentResponse, decoded with
//     the same geminiResponse as a non-streaming body, so stream and
//     non-stream paths share one root ("$") and one allowlist entry covers
//     both. AssertKnownEventTypes does not apply; instead every event must be
//     unnamed and carry a JSON object, and a chunk that is an error object
//     ({"error": ...}, how a failing upstream ends a stream) is rooted at
//     contracttest.ErrorRoot like an HTTP error body.
//   - Parts are an untagged union, so a new part kind shows up as an unknown
//     field on the part ($.candidates[].content.parts[].<kind>) and, when it
//     is the only key, as an unknown_block report named by its keys. The
//     documented kinds are ContractPartKinds; checkPartKinds holds every
//     fixture's parts to them.
//   - The model goes in the URL path, not the body, so the upstream check is
//     on the path.
//   - Fixture keys: a model directory is a Gemini API (AI Studio) model ID, or
//     vertex@<model> for the same adapter in Vertex AI mode (see
//     fixtureTarget). Vertex AI documents are rooted "vertex:$" (and
//     "vertex_<endpoint>:$"), because the two APIs document different enum
//     sets for the same fields.
//   - Non-chat endpoints root their documents at "<endpoint>:$" (contractDocs)
//     so one model's allowlists, enums and the opaque golden never confuse a
//     chat path with, say, the native passthrough's.
//
// The non-chat endpoints are in contract_surfaces_test.go.

// vertexKeyPrefix marks a Vertex AI fixture key: vertex@<publisher model>.
const vertexKeyPrefix = "vertex@"

// The Vertex AI project the suite configures, as config.example.yaml does
// (x-gcp-project / x-gcp-location headers), and the host it pretends to
// reach.
const (
	vertexHost     = "aiplatform.googleapis.com"
	vertexProject  = "test-project"
	vertexLocation = "global"
)

// fixtureKey is a model directory's key taken apart.
type fixtureKey struct {
	// vertex is true for a vertex@<model> key: the adapter in Vertex AI mode.
	vertex bool
	// model is the upstream model ID, as the gateway puts it in the URL.
	model string
}

// fixtureTarget splits a fixture's model key. "vertex@gemini-3.8-flash" is
// publisher model gemini-3.8-flash served through Vertex AI (the <preset>@<model>
// convention the openai suite uses for OpenAI-compatible presets); any other
// key is a Gemini API model ID. Gemini model IDs carry no "@".
func fixtureTarget(key string) fixtureKey {
	if m, ok := strings.CutPrefix(key, vertexKeyPrefix); ok {
		return fixtureKey{vertex: true, model: m}
	}
	return fixtureKey{model: key}
}

// root is the strict-mode root of a document of endpoint name ("" for chat)
// under k's API.
func (k fixtureKey) root(name string) string {
	switch {
	case name == "" && k.vertex:
		return "vertex:$"
	case name == "":
		return "$"
	case k.vertex:
		return "vertex_" + name + ":$"
	default:
		return name + ":$"
	}
}

// modelsPath is where the adapter puts model-scoped calls: the Gemini API's
// /v1beta/models/, or the Vertex AI publisher-model path New builds from the
// configured project and location.
func (k fixtureKey) modelsPath() string {
	if k.vertex {
		return "/v1beta1/projects/" + vertexProject + "/locations/" + vertexLocation + "/publishers/google/models/"
	}
	return "/v1beta/models/"
}

// api names k's API in messages and in documentedMethods.
func (k fixtureKey) api() string {
	if k.vertex {
		return "Vertex AI"
	}
	return "Gemini API"
}

// documentedMethods are the model-scoped methods each API's discovery document
// lists, for the endpoints this adapter calls: generativelanguage v1beta
// resources.models (revision 20261028) and aiplatform v1beta1
// projects.locations.publishers.models (revision 20261012).
var documentedMethods = map[string][]string{
	"Gemini API": {"generateContent", "streamGenerateContent", "countTokens", "embedContent", "batchEmbedContents", "predict",
		"predictLongRunning", "batchGenerateContent", "asyncBatchEmbedContent"},
	"Vertex AI": {"generateContent", "streamGenerateContent", "countTokens", "computeTokens", "embedContent", "predict",
		"predictLongRunning", "rawPredict", "streamRawPredict", "serverStreamingPredict"},
}

// Spec sources for ContractEnums.
const (
	specGemini = "generativelanguage v1beta discovery (rev 20261028) "
	specVertex = "aiplatform v1beta1 discovery (rev 20261012) GoogleCloudAiplatformV1beta1"
)

// The documented value sets, by API. Gemini API values from the
// generativelanguage v1beta discovery document, Vertex AI's from aiplatform
// v1beta1 (the version New builds Vertex AI URLs with).
var (
	geminiFinishReasons = []string{"FINISH_REASON_UNSPECIFIED", "STOP", "MAX_TOKENS", "SAFETY", "RECITATION", "LANGUAGE", "OTHER",
		"BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "MALFORMED_FUNCTION_CALL", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT",
		"IMAGE_OTHER", "NO_IMAGE", "IMAGE_RECITATION", "UNEXPECTED_TOOL_CALL", "TOO_MANY_TOOL_CALLS", "MISSING_THOUGHT_SIGNATURE",
		"MALFORMED_RESPONSE", "ESCALATION", "PUP_LIMITED_DISABLED", "CONTINUATION"}
	vertexFinishReasons = []string{"FINISH_REASON_UNSPECIFIED", "STOP", "MAX_TOKENS", "SAFETY", "RECITATION", "OTHER", "BLOCKLIST",
		"PROHIBITED_CONTENT", "SPII", "MALFORMED_FUNCTION_CALL", "MODEL_ARMOR", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT",
		"IMAGE_RECITATION", "IMAGE_OTHER", "UNEXPECTED_TOOL_CALL", "NO_IMAGE", "MALFORMED_RESPONSE", "CONTINUATION"}
	geminiBlockReasons = []string{"BLOCK_REASON_UNSPECIFIED", "SAFETY", "OTHER", "BLOCKLIST", "PROHIBITED_CONTENT", "IMAGE_SAFETY"}
	vertexBlockReasons = []string{"BLOCKED_REASON_UNSPECIFIED", "SAFETY", "OTHER", "BLOCKLIST", "PROHIBITED_CONTENT", "MODEL_ARMOR",
		"IMAGE_SAFETY", "JAILBREAK"}
	geminiHarmCategories = []string{"HARM_CATEGORY_UNSPECIFIED", "HARM_CATEGORY_DEROGATORY", "HARM_CATEGORY_TOXICITY",
		"HARM_CATEGORY_VIOLENCE", "HARM_CATEGORY_SEXUAL", "HARM_CATEGORY_MEDICAL", "HARM_CATEGORY_DANGEROUS",
		"HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT",
		"HARM_CATEGORY_DANGEROUS_CONTENT", "HARM_CATEGORY_CIVIC_INTEGRITY", "HARM_CATEGORY_JAILBREAK"}
	vertexHarmCategories = []string{"HARM_CATEGORY_UNSPECIFIED", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_DANGEROUS_CONTENT",
		"HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_CIVIC_INTEGRITY",
		"HARM_CATEGORY_IMAGE_HATE", "HARM_CATEGORY_IMAGE_DANGEROUS_CONTENT", "HARM_CATEGORY_IMAGE_HARASSMENT",
		"HARM_CATEGORY_IMAGE_SEXUALLY_EXPLICIT", "HARM_CATEGORY_JAILBREAK"}
	harmProbabilities = []string{"HARM_PROBABILITY_UNSPECIFIED", "NEGLIGIBLE", "LOW", "MEDIUM", "HIGH"}
	vertexSeverities  = []string{"HARM_SEVERITY_UNSPECIFIED", "HARM_SEVERITY_NEGLIGIBLE", "HARM_SEVERITY_LOW",
		"HARM_SEVERITY_MEDIUM", "HARM_SEVERITY_HIGH"}
	modalities = []string{"MODALITY_UNSPECIFIED", "TEXT", "IMAGE", "VIDEO", "AUDIO", "DOCUMENT"}
	// rpcCodes are the google.rpc.Code names (googleapis google/rpc/code.proto)
	// an error's status can carry; OK is not an error.
	rpcCodes = []string{"CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED", "NOT_FOUND", "ALREADY_EXISTS",
		"PERMISSION_DENIED", "UNAUTHENTICATED", "RESOURCE_EXHAUSTED", "FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE",
		"UNIMPLEMENTED", "INTERNAL", "UNAVAILABLE", "DATA_LOSS"}
	// rpcDetailTypes are the error detail messages googleapis
	// google/rpc/error_details.proto defines, as their @type URLs.
	rpcDetailTypes = []string{"type.googleapis.com/google.rpc.ErrorInfo", "type.googleapis.com/google.rpc.RetryInfo",
		"type.googleapis.com/google.rpc.DebugInfo", "type.googleapis.com/google.rpc.QuotaFailure",
		"type.googleapis.com/google.rpc.PreconditionFailure", "type.googleapis.com/google.rpc.BadRequest",
		"type.googleapis.com/google.rpc.RequestInfo", "type.googleapis.com/google.rpc.ResourceInfo",
		"type.googleapis.com/google.rpc.Help", "type.googleapis.com/google.rpc.LocalizedMessage"}
)

// ContractPartKinds are the keys a Part may carry, per API: Part in the
// generativelanguage v1beta and aiplatform v1beta1 discovery documents. A
// part is an untagged union, so its kind is its key; checkPartKinds holds
// every fixture part to this set, whatever geminiPart models, and the
// spec-diff check compares it with the spec.
var ContractPartKinds = map[string][]string{
	"Gemini API": {"text", "inlineData", "fileData", "functionCall", "functionResponse", "executableCode", "codeExecutionResult",
		"toolCall", "toolResponse", "thought", "thoughtSignature", "videoMetadata", "mediaResolution", "mediaProcessing",
		"speechMetadata", "partMetadata", "audioTranscription"},
	"Vertex AI": {"text", "inlineData", "fileData", "functionCall", "functionResponse", "executableCode", "codeExecutionResult",
		"thought", "thoughtSignature", "videoMetadata", "mediaResolution", "mediaProcessing", "speechMetadata",
		"audioTranscription"},
}

// Roots carrying a GenerateContentResponse, by API: chat, the speech route,
// and (usage only, see ContractEnums) the native passthrough.
var (
	geminiResponseRoots = []string{"$", "tts:$"}
	vertexResponseRoots = []string{"vertex:$", "vertex_tts:$"}
	// usageRoots also include the passthrough, which bills from usage.
	geminiUsageRoots = []string{"$", "tts:$", "genai:$"}
	vertexUsageRoots = []string{"vertex:$", "vertex_tts:$", "vertex_genai:$"}
)

// under prefixes each root with suffix: under([$, tts:$], ".x") = [$.x, tts:$.x].
func under(roots []string, suffixes ...string) []string {
	var out []string
	for _, r := range roots {
		for _, s := range suffixes {
			out = append(out, r+s)
		}
	}
	return out
}

// ContractEnums are the value sets the Gemini API and Vertex AI document for
// the enum-like fields the adapter reads or passes on. Every value found at
// one of these paths in a fixture (or a live recording) must be listed, so a
// new finish reason, block reason, harm category, modality, tier or error
// status fails the suite even where the adapter skips it without a trace.
// Spec names the discovery-document schema the values come from (the
// snapshots read 2026-10-28); the spec-diff check compares the two. The
// long-decode continuation protocol has no enum of its own beyond the
// CONTINUATION finish reason (Candidate.continuationToken is an opaque
// string). The native passthrough's candidates are relayed unread, so only
// its usage enums are checked.
var ContractEnums = []contracttest.EnumSpec{
	{Spec: specGemini + "Candidate.finishReason", Paths: under(geminiResponseRoots, ".candidates[].finishReason"),
		Values: geminiFinishReasons},
	{Spec: specVertex + "Candidate.finishReason", Paths: under(vertexResponseRoots, ".candidates[].finishReason"),
		Values: vertexFinishReasons},
	{Spec: specGemini + "PromptFeedback.blockReason", Paths: under(geminiResponseRoots, ".promptFeedback.blockReason"),
		Values: geminiBlockReasons},
	{Spec: specVertex + "GenerateContentResponsePromptFeedback.blockReason",
		Paths: under(vertexResponseRoots, ".promptFeedback.blockReason"), Values: vertexBlockReasons},
	{Spec: specGemini + "SafetyRating.category (HarmCategory)",
		Paths:  under(geminiResponseRoots, ".candidates[].safetyRatings[].category", ".promptFeedback.safetyRatings[].category"),
		Values: geminiHarmCategories},
	{Spec: specVertex + "SafetyRating.category (HarmCategory)",
		Paths:  under(vertexResponseRoots, ".candidates[].safetyRatings[].category", ".promptFeedback.safetyRatings[].category"),
		Values: vertexHarmCategories},
	{Spec: specGemini + "SafetyRating.probability and " + specVertex + "SafetyRating.probability (identical)",
		Paths: under(append(slices.Clone(geminiResponseRoots), vertexResponseRoots...),
			".candidates[].safetyRatings[].probability", ".promptFeedback.safetyRatings[].probability"),
		Values: harmProbabilities},
	{Spec: specVertex + "SafetyRating.severity",
		Paths:  under(vertexResponseRoots, ".candidates[].safetyRatings[].severity", ".promptFeedback.safetyRatings[].severity"),
		Values: vertexSeverities},
	{Spec: specGemini + "ModalityTokenCount.modality and " + specVertex + "ModalityTokenCount.modality (identical)",
		Paths: append(append(
			under(append(slices.Clone(geminiUsageRoots), vertexUsageRoots...),
				".usageMetadata.promptTokensDetails[].modality", ".usageMetadata.candidatesTokensDetails[].modality",
				".usageMetadata.toolUsePromptTokensDetails[].modality", ".usageMetadata.cacheTokensDetails[].modality"),
			under([]string{"embed:$", "batch_embed:$", "genai_embed:$"}, ".usageMetadata.promptTokenDetails[].modality")...),
			"vertex_embed:$.usageMetadata.promptTokensDetails[].modality",
			"vertex_genai_embed:$.usageMetadata.promptTokensDetails[].modality",
			"count_tokens:$.promptTokensDetails[].modality", "count_tokens:$.cacheTokensDetails[].modality",
			"vertex_count_tokens:$.promptTokensDetails[].modality"),
		Values: modalities},
	{Spec: specGemini + "Content.role and " + specVertex + "Content.role (\"Must be either 'user' or 'model'\")",
		Paths:  under(append(slices.Clone(geminiResponseRoots), vertexResponseRoots...), ".candidates[].content.role"),
		Values: []string{"user", "model"}},
	{Spec: specGemini + "UrlMetadata.urlRetrievalStatus",
		Paths: under(geminiResponseRoots, ".candidates[].urlContextMetadata.urlMetadata[].urlRetrievalStatus"),
		Values: []string{"URL_RETRIEVAL_STATUS_UNSPECIFIED", "URL_RETRIEVAL_STATUS_SUCCESS", "URL_RETRIEVAL_STATUS_ERROR",
			"URL_RETRIEVAL_STATUS_PAYWALL", "URL_RETRIEVAL_STATUS_UNSAFE"}},
	{Spec: specVertex + "UrlMetadata.urlRetrievalStatus",
		Paths:  under(vertexResponseRoots, ".candidates[].urlContextMetadata.urlMetadata[].urlRetrievalStatus"),
		Values: []string{"URL_RETRIEVAL_STATUS_UNSPECIFIED", "URL_RETRIEVAL_STATUS_SUCCESS", "URL_RETRIEVAL_STATUS_ERROR"}},
	{Spec: specGemini + "UsageMetadata.serviceTier", Paths: under(geminiUsageRoots, ".usageMetadata.serviceTier"),
		Values: []string{"unspecified", "standard", "flex", "priority"}},
	{Spec: specVertex + "GenerateContentResponseUsageMetadata.trafficType and UsageMetadata.trafficType",
		Paths: append(under(vertexUsageRoots, ".usageMetadata.trafficType"), "vertex_embed:$.usageMetadata.trafficType",
			"vertex_genai_embed:$.usageMetadata.trafficType"),
		Values: []string{"TRAFFIC_TYPE_UNSPECIFIED", "ON_DEMAND", "ON_DEMAND_PRIORITY", "ON_DEMAND_FLEX", "ON_DEMAND_OFFPEAK",
			"PROVISIONED_THROUGHPUT"}},
	{Spec: specGemini + "ExecutableCode.language and " + specVertex + "ExecutableCode.language (identical)",
		Paths:  under(append(slices.Clone(geminiResponseRoots), vertexResponseRoots...), ".candidates[].content.parts[].executableCode.language"),
		Values: []string{"LANGUAGE_UNSPECIFIED", "PYTHON"}},
	{Spec: specGemini + "CodeExecutionResult.outcome and " + specVertex + "CodeExecutionResult.outcome (identical)",
		Paths: under(append(slices.Clone(geminiResponseRoots), vertexResponseRoots...),
			".candidates[].content.parts[].codeExecutionResult.outcome"),
		Values: []string{"OUTCOME_UNSPECIFIED", "OUTCOME_OK", "OUTCOME_FAILED", "OUTCOME_DEADLINE_EXCEEDED"}},
	{Spec: specGemini + "ToolCall.toolType and ToolResponse.toolType",
		Paths: under(geminiResponseRoots, ".candidates[].content.parts[].toolCall.toolType",
			".candidates[].content.parts[].toolResponse.toolType"),
		Values: []string{"TOOL_TYPE_UNSPECIFIED", "GOOGLE_SEARCH_WEB", "GOOGLE_SEARCH_IMAGE", "URL_CONTEXT", "GOOGLE_MAPS",
			"FILE_SEARCH", "MEDIA_PROCESSING"}},
	{Spec: specGemini + "ModelStatus.modelStage (also on ListModels entries: the cookbook's recorded Models_REST output)",
		Paths: append(under(geminiResponseRoots, ".modelStatus.modelStage"), "list_models:$.models[].modelStage"),
		Values: []string{"MODEL_STAGE_UNSPECIFIED", "UNSTABLE_EXPERIMENTAL", "EXPERIMENTAL", "PREVIEW", "STABLE", "LEGACY",
			"DEPRECATED", "RETIRED"}},
	{Spec: "google.rpc.Code (googleapis google/rpc/code.proto), as google.rpc.Status.status in the HTTP JSON mapping",
		Paths: []string{"error:$.error.status", "genai_error:$.error.status"}, Values: rpcCodes},
	{Spec: "google.rpc error detail @type URLs (googleapis google/rpc/error_details.proto)",
		Paths: []string{"error:$.error.details[].@type", "genai_error:$.error.details[].@type"}, Values: rpcDetailTypes},
}

// contractStrict is how the adapter decodes Gemini and Vertex AI bodies, for
// the strict checks (contracttest.Strict). geminiResponse decodes every
// GenerateContentResponse in full, so nothing is decoded later; function-call
// arguments are whatever the model passed.
var contractStrict = &contracttest.Strict{
	Docs:  contractDocs,
	Enums: ContractEnums,
	Free: []string{"$.candidates[].content.parts[].functionCall.args", "vertex:$.candidates[].content.parts[].functionCall.args",
		"tts:$.candidates[].content.parts[].functionCall.args"},
	Ignore: protocolFields,
}

// protocolFields are upstream fields the adapter does not decode because, for
// every model, they carry nothing the canonical response could use. They live
// here rather than in each meta.json.
var protocolFields = map[string]string{
	"$.candidates[].index": "the candidate's position; the gateway never sets candidateCount, so there is " +
		"one candidate and it is always index 0, which becomes choice 0",
	"vertex:$.candidates[].index": "as $.candidates[].index (Vertex AI omits it when 0, proto3 JSON's default)",
	"tts:$.candidates[].index":    "as $.candidates[].index",
}

// contractDocs splits a case's body into what the adapter decodes: a
// GenerateContentResponse (or, per chunk, a stream of them) as geminiResponse,
// an error body or error chunk as geminiErrorResponse (parseGeminiError), and
// the non-chat bodies as surfaceDocs describes.
func contractDocs(c contracttest.Case) []contracttest.Doc {
	k := fixtureTarget(c.Model)
	if c.Endpoint != contracttest.EndpointChat {
		return surfaceDocs(c, k)
	}
	switch {
	case c.IsError():
		return errorDocs(c.Body)
	case c.IsStream:
		events := contracttest.ParseSSE(c.Body)
		docs := make([]contracttest.Doc, len(events))
		for i, ev := range events {
			docs[i] = contracttest.Doc{Root: k.root(""), Data: ev.Data, Type: reflect.TypeFor[geminiResponse]()}
			if isErrorChunk(ev.Data) {
				docs[i] = contracttest.Doc{Root: contracttest.ErrorRoot, Data: ev.Data, Type: reflect.TypeFor[geminiErrorResponse]()}
			}
		}
		return docs
	default:
		return []contracttest.Doc{{Root: k.root(""), Data: c.Body, Type: reflect.TypeFor[geminiResponse]()}}
	}
}

// errorArrayRoot roots an error body wrapped in a JSON array
// ([{"error": ...}]), which geminiErrorResponse cannot decode (gap G21). It
// is a root of its own so that allowlisting that mismatch does not also
// excuse a retyped body at contracttest.ErrorRoot.
const errorArrayRoot = "error_array:$"

// errorDocs is the document of an HTTP error body: parseGeminiError decodes
// it as geminiErrorResponse.
func errorDocs(body []byte) []contracttest.Doc {
	root := contracttest.ErrorRoot
	if b := bytes.TrimSpace(body); len(b) > 0 && b[0] == '[' {
		root = errorArrayRoot
	}
	return []contracttest.Doc{{Root: root, Data: body, Type: reflect.TypeFor[geminiErrorResponse]()}}
}

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

	// The non-chat endpoints' results (contract_surfaces_test.go).
	native  *nativeResult
	embed   *models.EmbeddingResponse
	image   *models.ImageResponse
	speech  *speechResult
	models  []models.ModelObject
	listRan bool
}

// newContractProvider builds the real provider against a replay server. It
// takes a testing.TB so mutants can run it.
//
// A Vertex AI provider is configured as config.example.yaml configures one:
// base URL https://aiplatform.googleapis.com (here http, to reach the replay
// server) and the x-gcp-project / x-gcp-location headers, from which New
// builds the .../v1beta1/projects/<p>/locations/<l> base. The adapter detects
// Vertex AI by the base URL's host, so the URL names the real host, and
// ProviderConfig.DialContext (the hook the recorder uses to tee traffic)
// routes every connection to the replay server instead. No test-only switch
// is needed, and none of the adapter's own Vertex AI detection is bypassed.
func newContractProvider(t testing.TB, replayURL string, k fixtureKey) *Provider {
	t.Helper()
	cfg := config.ProviderConfig{BaseURL: replayURL, APIKey: "test-api-key", DefaultTimeout: 10 * time.Second,
		MaxConcurrent: 10, ConnPoolSize: 5}
	if k.vertex {
		u, err := url.Parse(replayURL)
		if err != nil {
			t.Fatalf("replay URL: %v", err)
		}
		replayAddr := u.Host
		var d net.Dialer
		cfg.BaseURL = "http://" + vertexHost
		cfg.Headers = map[string]string{"x-gcp-project": vertexProject, "x-gcp-location": vertexLocation}
		cfg.DialContext = func(ctx context.Context, network, _ string) (net.Conn, error) {
			return d.DialContext(ctx, network, replayAddr)
		}
	}
	p, err := New("test-gemini", cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if p.vertexAI != k.vertex {
		t.Fatalf("the adapter's Vertex AI detection says %v for fixture key %q", p.vertexAI, k.model)
	}
	t.Cleanup(func() { _ = p.Close() })
	return p
}

// runCase sends c's request through the real provider against a replay of
// c's body.
func runCase(t testing.TB, c contracttest.Case) caseOutput {
	t.Helper()
	k := fixtureTarget(c.Model)
	upstream := contracttest.ReplayServer(t, c)
	p := newContractProvider(t, upstream.URL, k)
	ctx, shapes := common.WithShapeRecorder(context.Background())

	out := caseOutput{upstream: upstream}
	switch {
	case c.Endpoint != contracttest.EndpointChat:
		runSurface(t, c, k, p, ctx, &out)
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
	if c.Endpoint != contracttest.EndpointChat {
		assertSurfaceGoldens(t, c, out)
		return
	}
	switch {
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
	k := fixtureTarget(c.Model)
	if c.Endpoint == contracttest.EndpointChat {
		checkChat(t, c, k, out)
	} else {
		checkSurface(t, c, k, out)
	}
	checkPartKinds(t, c, k)
	contractStrict.Check(t, c)
	contracttest.AssertNoUnknownShapes(t, c, out.shapes)
	// One ExpectGaps for every sanity rule, so case.json's sanity_gaps are
	// judged against all of them at once.
	contracttest.ExpectGaps(t, c, func(t testing.TB) {
		if c.Endpoint == contracttest.EndpointChat {
			saneChat(t, c, out)
		} else {
			saneSurface(t, c, k, out)
		}
	})
}

// checkChat holds the chat path's hard rules: the request went to the
// fixture's model, a 2xx case did not fail, and a stream is what alt=sse
// sends.
func checkChat(t testing.TB, c contracttest.Case, k fixtureKey, out caseOutput) {
	t.Helper()
	method, query := "generateContent", ""
	if c.IsStream {
		method, query = "streamGenerateContent", "alt=sse"
	}
	assertUpstreamCall(t, c, k, out.upstream, "POST", method, query)

	switch {
	case c.IsError():
		if c.IsStream && len(out.stream.Chunks) > 0 {
			t.Errorf("an upstream %d answer produced %d stream chunks", c.Status, len(out.stream.Chunks))
		}
	case c.IsStream:
		events := contracttest.ParseSSE(c.Body)
		assertDataOnlyChunks(t, events)
		upstreamFailed := slices.ContainsFunc(events, func(ev contracttest.Event) bool { return isErrorChunk(ev.Data) })
		switch {
		case out.err != nil && !upstreamFailed:
			t.Fatalf("StreamChatCompletion: %v", out.err)
		case out.err == nil && upstreamFailed:
			t.Errorf("the upstream stream carries an error chunk, but the stream ended without an error")
		}
	case out.err != nil:
		t.Fatalf("ChatCompletion: %v", out.err)
	}
}

// saneChat is the chat path's sanity: AssertSaneError for an error, a
// mid-stream error judged by the status its chunk names, AssertSane
// otherwise.
func saneChat(t testing.TB, c contracttest.Case, out caseOutput) {
	t.Helper()
	switch {
	case c.IsError():
		saneError(t, c, out.err)
	case !c.IsStream:
		contracttest.AssertSane(t, contracttest.FromResponse(out.resp), chunkHasOutput(t, c.Body))
	default:
		events := contracttest.ParseSSE(c.Body)
		hasOutput := false
		for _, ev := range events {
			if e := errorChunk(ev.Data); e != nil {
				assertMidStreamError(t, c, out, e, hasOutput)
				return
			}
			hasOutput = hasOutput || chunkHasOutput(t, ev.Data)
		}
		contracttest.AssertSane(t, contracttest.FromStream(out.stream.Chunks), hasOutput)
	}
}

// assertMidStreamError checks a 200 stream that carries an error chunk ends
// with that error: the canonical error is judged as if the upstream had
// answered with the HTTP status the chunk names (error.code), its message
// survives, and the output streamed before it reached the client.
func assertMidStreamError(t testing.TB, c contracttest.Case, out caseOutput, e *geminiErrorResponse, hadOutput bool) {
	t.Helper()
	asIf := c
	asIf.Status, asIf.Headers, asIf.SanityGaps = e.Error.Code, nil, nil
	if asIf.Status < 400 || asIf.Status > 599 {
		asIf.Status = 500
	}
	contracttest.AssertSaneError(t, asIf, out.err)
	var apiErr *models.APIError
	if errors.As(out.err, &apiErr) && apiErr.Message != e.Error.Message {
		t.Errorf("the stream ended with message %q; the upstream's error chunk says %q", apiErr.Message, e.Error.Message)
	}
	if hadOutput {
		got := contracttest.FromStream(out.stream.Chunks)
		if got.Content == "" && len(got.ToolCalls) == 0 {
			t.Errorf("the output the upstream streamed before its error chunk did not reach the client")
		}
	}
}

// retryHinter is what an error carrying the upstream's retry hint implements
// (contracttest.ErrorGolden reads the same method).
type retryHinter interface {
	RetryAfter() time.Duration
}

// saneError is contracttest.AssertSaneError plus Google's own retry hint:
// RetryInfo.retryDelay in error.details, which a Gemini 429 carries instead
// of a retry-after header. The caller's ExpectGaps applies case.json's
// sanity_gaps, so AssertSaneError gets none of its own.
func saneError(t testing.TB, c contracttest.Case, err error) {
	t.Helper()
	plain := c
	plain.SanityGaps = nil
	contracttest.AssertSaneError(t, plain, err)
	if delay := retryInfoDelay(c.Body); delay != "" {
		var rh retryHinter
		if !errors.As(err, &rh) || rh.RetryAfter() <= 0 {
			t.Errorf("upstream sent RetryInfo retryDelay %q but the canonical error carries no retry hint, so the gateway "+
				"retries on its own schedule", delay)
		}
	}
}

// retryInfoDelay is the retryDelay of a google.rpc.RetryInfo in an error
// body's details (bare or wrapped in a one-element array), or "".
func retryInfoDelay(body []byte) string {
	var one struct {
		Error struct {
			Details []map[string]any `json:"details"`
		} `json:"error"`
	}
	var wrapped []json.RawMessage
	if json.Unmarshal(body, &wrapped) == nil && len(wrapped) == 1 {
		body = wrapped[0]
	}
	if json.Unmarshal(body, &one) != nil {
		return ""
	}
	for _, d := range one.Error.Details {
		if d["@type"] == "type.googleapis.com/google.rpc.RetryInfo" {
			if s, ok := d["retryDelay"].(string); ok {
				return s
			}
		}
	}
	return ""
}

// assertUpstreamCall checks the adapter made exactly one request, method
// verb, to k's model path with action and query: the fixture is filed under
// the model the gateway actually asked for. Gemini takes the model in the URL
// path.
func assertUpstreamCall(t testing.TB, c contracttest.Case, k fixtureKey, upstream *contracttest.Replay, verb, action, query string) {
	t.Helper()
	req := upstream.Request(t)
	want := k.modelsPath() + k.model + ":" + action
	if req.Method != verb || req.Path != want || req.Query != query {
		t.Errorf("upstream request was %s %s?%s, want %s %s?%s (the fixture's model directory is %q)",
			req.Method, req.Path, req.Query, verb, want, query, c.Model)
	}
}

// upstreamAction is the method suffix of the one upstream request's path
// (":generateContent" -> "generateContent").
func upstreamAction(upstream *contracttest.Replay) string {
	reqs := upstream.Requests()
	if len(reqs) != 1 {
		return ""
	}
	_, action, _ := strings.Cut(reqs[0].Path[strings.LastIndex(reqs[0].Path, "/")+1:], ":")
	return action
}

// saneMethod checks the upstream method is one k's API documents for
// publisher models.
func saneMethod(t testing.TB, k fixtureKey, upstream *contracttest.Replay) {
	t.Helper()
	action := upstreamAction(upstream)
	if action != "" && !slices.Contains(documentedMethods[k.api()], action) {
		t.Errorf("upstream method %q is not a method %s documents for models (%s)", action, k.api(),
			strings.Join(documentedMethods[k.api()], ", "))
	}
}

// isErrorChunk reports whether a stream chunk is the {"error": {...}} object
// a failing upstream sends in place of a response chunk.
func isErrorChunk(data []byte) bool { return errorChunk(data) != nil }

// errorChunk decodes a stream chunk that is an error object, or returns nil.
func errorChunk(data []byte) *geminiErrorResponse {
	var obj struct {
		Error json.RawMessage `json:"error"`
	}
	if json.Unmarshal(data, &obj) != nil || len(obj.Error) == 0 || string(obj.Error) == "null" {
		return nil
	}
	var e geminiErrorResponse
	_ = json.Unmarshal(data, &e)
	return &e
}

// assertDataOnlyChunks checks the stream is what alt=sse sends: unnamed events,
// each one JSON object.
func assertDataOnlyChunks(t testing.TB, events []contracttest.Event) {
	t.Helper()
	if len(events) == 0 {
		t.Fatalf("stream fixture has no events")
	}
	for i, ev := range events {
		if ev.Name != "" {
			t.Errorf("event %d is named %q; Gemini's alt=sse stream sends data-only events", i, ev.Name)
		}
		var obj map[string]json.RawMessage
		if err := json.Unmarshal(ev.Data, &obj); err != nil {
			t.Errorf("event %d: data is not a JSON object: %v", i, err)
		}
	}
}

// checkPartKinds holds every part of every GenerateContentResponse document
// of c to ContractPartKinds: a key outside the documented set is a part kind
// the spec does not know (or a fixture authored wrong), whatever geminiPart
// models.
func checkPartKinds(t testing.TB, c contracttest.Case, k fixtureKey) {
	t.Helper()
	documented := ContractPartKinds[k.api()]
	var bad []string
	for _, d := range contractDocs(c) {
		var resp struct {
			Candidates []struct {
				Content struct {
					Parts []map[string]json.RawMessage `json:"parts"`
				} `json:"content"`
			} `json:"candidates"`
		}
		if d.Root == contracttest.ErrorRoot || json.Unmarshal(d.Data, &resp) != nil {
			continue
		}
		for _, cand := range resp.Candidates {
			for _, part := range cand.Content.Parts {
				for key := range part {
					if !slices.Contains(documented, key) {
						bad = append(bad, d.Root+".candidates[].content.parts[]."+key)
					}
				}
			}
		}
	}
	if len(bad) > 0 {
		slices.Sort(bad)
		t.Errorf("parts carry keys %s does not document as part kinds (ContractPartKinds):\n  %s",
			k.api(), strings.Join(slices.Compact(bad), "\n  "))
	}
}

// chunkHasOutput reports whether a generateContent body, or one stream chunk,
// carried answer text, a function call or inline media in candidate 0, which
// the canonical form must then carry too. Thought summaries are not output.
func chunkHasOutput(t testing.TB, body []byte) bool {
	t.Helper()
	var resp geminiResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		// A mutant's retyped field: the strict checks report it.
		return false
	}
	if len(resp.Candidates) == 0 {
		return false
	}
	for _, p := range resp.Candidates[0].Content.Parts {
		if p.FunctionCall != nil || p.InlineData != nil || (p.Text != "" && !p.Thought) {
			return true
		}
	}
	return false
}

// readAll reads and closes r.
func readAll(r io.ReadCloser) ([]byte, error) {
	b, err := io.ReadAll(r)
	if cerr := r.Close(); err == nil {
		err = cerr
	}
	return b, err
}

// sortedKeys is m's keys in order.
func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
