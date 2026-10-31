package openai

import "github.com/futureagi/agentcc-gateway/internal/providers/contracttest"

// The together preset (api.together.xyz). Its value sets come from
// Together's OpenAPI spec as embedded in its official SDK,
// github.com/togethercomputer/together-py scripts/mock at
// 3428f80887121c5715f670f3cffa71b2ed482c12 (pinned as
// contractspec/subsets/together, whose source.json has the hashes).
//
// Together does not serve /v1/responses (never in its spec), so that cell is
// not served; it lists its models at other paths than the gateway calls
// (PathGaps).
func init() {
	registerContractPreset(contractPreset{
		Name: "together",
		Spec: true,
		Enums: map[string][]contracttest.EnumSpec{
			contracttest.EndpointChat: {
				{Spec: "ChatCompletionResponse.object", Paths: []string{"$.object"}, Values: []string{"chat.completion"}},
				{Spec: "ChatCompletionChunk.object", Paths: []string{"chunk:$.object"}, Values: []string{"chat.completion.chunk"}},
				{Spec: "FinishReason", Paths: []string{"$.choices[].finish_reason", "chunk:$.choices[].finish_reason"},
					Values: []string{"stop", "eos", "length", "tool_calls", "function_call"}},
				{Spec: "ChatCompletionMessage.role", Paths: []string{"$.choices[].message.role"}, Values: []string{"assistant"}},
				{Spec: "ChatCompletionChunk.choices[].delta.role", Paths: []string{"chunk:$.choices[].delta.role"},
					Values: []string{"system", "user", "assistant", "function", "tool"}},
				{Spec: "ToolChoice.type", Paths: []string{"$.choices[].message.tool_calls[].type", "chunk:$.choices[].delta.tool_calls[].type"},
					Values: []string{"function"}},
			},
			contracttest.EndpointCompletions: withErrors(togetherErrorEnums, []contracttest.EnumSpec{
				{Spec: "CompletionResponse.object", Paths: []string{"$.object"}, Values: []string{"text.completion"}},
				{Spec: "FinishReason", Paths: []string{"$.choices[].finish_reason"}, Values: []string{"stop", "eos", "length", "tool_calls", "function_call"}},
			}),
			contracttest.EndpointEmbeddings: withErrors(togetherErrorEnums, []contracttest.EnumSpec{
				{Spec: "EmbeddingsResponse.object", Paths: []string{"$.object"}, Values: []string{"list"}},
				{Spec: "EmbeddingsResponse.data[].object", Paths: []string{"$.data[].object"}, Values: []string{"embedding"}},
			}),
			// The operation documents no error response.
			contracttest.EndpointImages: {
				{Spec: "ImageResponse.object", Paths: []string{"$.object"}, Values: []string{"list"}},
				{Spec: "ImageResponseDataB64.type / ImageResponseDataUrl.type", Paths: []string{"$.data[].type"}, Values: []string{"b64_json", "url"}},
			},
			// Audio bytes; AudioTranscriptionResponse and
			// AudioTranslationResponse have no enum-like fields.
			contracttest.EndpointSpeech:        togetherErrorEnums,
			contracttest.EndpointTranscription: togetherErrorEnums,
			contracttest.EndpointTranslation:   togetherErrorEnums,
		},
		PathGaps: []presetPathGap{{
			Endpoint:   contracttest.EndpointListModels,
			Documented: []string{"/v2/supported-models", "/v2/projects/{projectId}/models", "/v2/organizations/{organizationId}/models"},
			Source: "togethercomputer/together-py@3428f80887121c5715f670f3cffa71b2ed482c12:scripts/mock (embedded spec) " +
				"GET /v2/supported-models, /v2/projects/{projectId}/models, /v2/organizations/{organizationId}/models; " +
				"GET /v1/models (operationId models, a bare ModelInfo array) was last in c55bb3003581d8be51f8eb102debf314cb3bcfe2 " +
				"and removed by 057705f8af4458e226b688f5ca08983a4edf4147 (2026-10-19)",
			Gap: "known gap TG6: ListModels calls GET /v1/models, which Together's spec no longer documents: together-py 057705f8 " +
				"(2026-10-19) dropped it for /v2/supported-models and the project and organization model lists, whose shapes " +
				"differ, and the last revision that had it answered a bare JSON array of ModelInfo, which ListModels' " +
				"models.ModelListResponse cannot decode either, so the gateway cannot list Together's models",
		}},
	})
}

// togetherErrorEnums are ErrorData's. The spec types error.type and
// error.code as free strings; the values are the ones it documents for
// Together's errors elsewhere: the queue API ErrorResponse's categories
// ("e.g. invalid_request_error, not_found_error") and the realtime
// endpoints' error codes.
var togetherErrorEnums = []contracttest.EnumSpec{
	{Spec: "ErrorData.error.type (a free string; the categories the spec documents)", Paths: []string{"error:$.error.type"},
		Values: []string{"invalid_request_error", "not_found_error"}},
	{Spec: "ErrorData.error.code (a free string; the codes the spec documents)", Paths: []string{"error:$.error.code"},
		Values: []string{"invalid_api_key", "missing_api_key", "model_not_available"}},
}
