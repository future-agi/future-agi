package openai

import "github.com/futureagi/agentcc-gateway/internal/providers/contracttest"

// The cerebras preset (api.cerebras.ai). Its spec is not public
// (contractspec/subsets/unfetchable.json); the value sets come from its
// official SDK's generated types: cerebras-cloud-sdk-python cacaf75
// types/chat/chat_completion.py, types/completion.py and
// types/model_list_response.py.
//
// The SDK's api.md (and .stats.yml's configured_endpoints: 4) lists
// /v1/chat/completions, /v1/completions and /v1/models alone, so the other
// endpoints are not served. It types no error body, so no endpoint declares
// an error value set.
func init() {
	registerContractPreset(contractPreset{
		Name: "cerebras",
		Enums: map[string][]contracttest.EnumSpec{
			contracttest.EndpointChat: {
				{Spec: "ChatCompletionResponse.object", Paths: []string{"$.object"}, Values: []string{"chat.completion"}},
				{Spec: "ChatChunkResponse.object", Paths: []string{"chunk:$.object"}, Values: []string{"chat.completion.chunk"}},
				{Spec: "ChatCompletionResponseChoice.finish_reason / ChatChunkResponseChoice.finish_reason",
					Paths:  []string{"$.choices[].finish_reason", "chunk:$.choices[].finish_reason"},
					Values: []string{"stop", "length", "content_filter", "tool_calls"}},
				{Spec: "ChatCompletionResponseChoiceMessage.role / ChatChunkResponseChoiceDelta.role",
					Paths: []string{"$.choices[].message.role", "chunk:$.choices[].delta.role"}, Values: []string{"assistant", "user", "system", "tool"}},
				{Spec: "ChatCompletionResponseChoiceMessageToolCall.type", Paths: []string{
					"$.choices[].message.tool_calls[].type", "chunk:$.choices[].delta.tool_calls[].type"}, Values: []string{"function"}},
			},
			contracttest.EndpointCompletions: {
				{Spec: "CompletionResponse.object", Paths: []string{"$.object"}, Values: []string{"text_completion"}},
				{Spec: "CompletionResponseChoice.finish_reason", Paths: []string{"$.choices[].finish_reason"},
					Values: []string{"stop", "length", "content_filter", "tool_calls"}},
			},
			contracttest.EndpointListModels: {
				{Spec: "ModelMetadataList.object", Paths: []string{"$.object"}, Values: []string{"list"}},
				{Spec: "ModelMetadataListData.object", Paths: []string{"$.data[].object"}, Values: []string{"model"}},
			},
		},
	})
}
