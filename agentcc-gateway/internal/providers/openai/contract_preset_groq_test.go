package openai

import "github.com/futureagi/agentcc-gateway/internal/providers/contracttest"

// The groq preset (api.groq.com/openai). Its value sets come from Groq's
// OpenAPI spec as embedded in its official SDK, github.com/groq/groq-python
// scripts/mock at 55066d94acca028c3cb74e050f2711b80ade6298 (pinned as
// contractspec/subsets/groq, whose source.json has the hashes). The spec
// defines an ErrorResponse but no operation lists one and no source gives a
// value for its type, so no endpoint declares an error value set.
//
// Groq does not serve /openai/v1/completions or /openai/v1/images/generations
// (the spec has neither, nor any completions or image operation), so those
// cells are not served rather than path gaps.
func init() {
	registerContractPreset(contractPreset{
		Name: "groq",
		Spec: true,
		Enums: map[string][]contracttest.EnumSpec{
			contracttest.EndpointChat: {
				{Spec: "CreateChatCompletionResponse.object", Paths: []string{"$.object"}, Values: []string{"chat.completion"}},
				{Spec: "CreateChatCompletionStreamResponse.object", Paths: []string{"chunk:$.object"}, Values: []string{"chat.completion.chunk"}},
				{Spec: "CreateChatCompletionResponse.choices[].finish_reason", Paths: []string{"$.choices[].finish_reason", "chunk:$.choices[].finish_reason"},
					Values: []string{"stop", "length", "tool_calls", "function_call"}},
				{Spec: "ChatCompletionResponseMessage.role", Paths: []string{"$.choices[].message.role"}, Values: []string{"assistant"}},
				{Spec: "ChatCompletionStreamResponseDelta.role", Paths: []string{"chunk:$.choices[].delta.role"}, Values: []string{"assistant", "system", "tool", "user"}},
				{Spec: "ChatCompletionMessageToolCall.type", Paths: []string{"$.choices[].message.tool_calls[].type", "chunk:$.choices[].delta.tool_calls[].type"},
					Values: []string{"function"}},
				{Spec: "CreateChatCompletionResponse.service_tier", Paths: []string{"$.service_tier", "chunk:$.service_tier"},
					Values: []string{"auto", "default", "flex", "on_demand", "performance"}},
			},
			contracttest.EndpointEmbeddings: {
				{Spec: "CreateEmbeddingResponse.object", Paths: []string{"$.object"}, Values: []string{"list"}},
				{Spec: "Embedding.object", Paths: []string{"$.data[].object"}, Values: []string{"embedding"}},
			},
			// audio/wav bytes.
			contracttest.EndpointSpeech: nil,
			// CreateTranscriptionResponseJson / VerboseJson and
			// CreateTranslationResponseJson / VerboseJson have no enum-like
			// fields.
			contracttest.EndpointTranscription: nil,
			contracttest.EndpointTranslation:   nil,
			contracttest.EndpointResponses:     groqResponsesEnums,
			contracttest.EndpointListModels: {
				{Spec: "ListModelsResponse.object", Paths: []string{"$.object"}, Values: []string{"list"}},
				{Spec: "Model.object", Paths: []string{"$.data[].object"}, Values: []string{"model"}},
			},
		},
	})
}

// groqResponsesEnums are CreateResponseResponse's: the /v1/responses
// passthrough relays the body, and the server prices it from model and usage.
// Groq's spec has no stream event schema, so there is no event type set (and
// no stream fixture: the events would be invented).
var groqResponsesEnums = []contracttest.EnumSpec{
	{Spec: "CreateResponseResponse.object", Paths: []string{"$.object"}, Values: []string{"response"}},
	{Spec: "CreateResponseResponse.status", Paths: []string{"$.status"}, Values: []string{"completed", "failed", "in_progress", "incomplete"}},
	{Spec: "ResponseOutputItem (discriminator)", Paths: []string{"$.output[].type"}, Values: []string{"message", "function_call", "reasoning"}},
	{Spec: "ResponseOutputMessage.status / ResponseOutputFunctionCall.status", Paths: []string{"$.output[].status"},
		Values: []string{"in_progress", "completed", "incomplete"}},
	{Spec: "ResponseOutputMessage.role", Paths: []string{"$.output[].role"}, Values: []string{"assistant"}},
	{Spec: "ResponseOutputContent (discriminator)", Paths: []string{"$.output[].content[].type"}, Values: []string{"output_text"}},
	{Spec: "ResponseAnnotation (discriminator)", Paths: []string{"$.output[].content[].annotations[].type"},
		Values: []string{"file_citation", "url_citation"}},
	{Spec: "CreateResponseResponse.reasoning.effort", Paths: []string{"$.reasoning.effort"}, Values: []string{"none", "low", "medium", "high"}},
	{Spec: "CreateResponseResponse.truncation", Paths: []string{"$.truncation"}, Values: []string{"auto", "disabled"}},
	{Spec: "CreateResponseResponse.service_tier", Paths: []string{"$.service_tier"}, Values: []string{"auto", "default", "flex"}},
}
