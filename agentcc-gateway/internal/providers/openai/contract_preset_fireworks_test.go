package openai

import "github.com/futureagi/agentcc-gateway/internal/providers/contracttest"

// The fireworks preset (api.fireworks.ai/inference). Its spec is not public
// (contractspec/subsets/unfetchable.json); the value sets come from its
// official SDK's generated types: fw-ai-external python-sdk 374be9a
// types/chat and types/completion_create_response.py. The Stainless SDK does
// not wrap /inference/v1/embeddings or /inference/v1/models, so those value
// sets come from the official legacy SDK, PyPI fireworks-ai 0.19.20
// fireworks/client/api.py. /inference/v1/responses has no Fireworks type at
// all (only a recorded stream in fw-ai/cookbook 57968a2), so it declares no
// value set and its stream is checked against OpenAI's event types.
//
// Fireworks serves no /v1/images/generations (image generation has routes
// of its own), no /v1/audio/speech, and transcription and translation only
// on separate audio hosts, so those cells are not served. No official source
// shows a Fireworks error body, so no endpoint declares an error value set.
func init() {
	registerContractPreset(contractPreset{
		Name: "fireworks",
		Enums: map[string][]contracttest.EnumSpec{
			contracttest.EndpointChat: {
				{Spec: "types/chat/chat_completion_chunk.py Choice.finish_reason (the response's is a free string)",
					Paths: []string{"chunk:$.choices[].finish_reason"}, Values: []string{"stop", "length", "function_call", "tool_calls"}},
			},
			contracttest.EndpointCompletions: {
				{Spec: "types/completion_create_response.py CompletionCreateResponse.object (\"always text_completion\")",
					Paths: []string{"$.object"}, Values: []string{"text_completion"}},
				{Spec: "types/shared/choice.py Choice.finish_reason", Paths: []string{"$.choices[].finish_reason"},
					Values: []string{"stop", "length", "error"}},
			},
			contracttest.EndpointEmbeddings: {
				{Spec: "fireworks-ai 0.19.20 api.py EmbeddingResponse.object", Paths: []string{"$.object"}, Values: []string{"list"}},
				{Spec: "fireworks-ai 0.19.20 api.py EmbeddingInfo.object", Paths: []string{"$.data[].object"}, Values: []string{"embedding"}},
			},
			contracttest.EndpointResponses: nil,
			contracttest.EndpointListModels: {
				{Spec: "fireworks-ai 0.19.20 api.py ListModelsResponse.object (\"always list\")", Paths: []string{"$.object"}, Values: []string{"list"}},
				{Spec: "fireworks-ai 0.19.20 api.py Model.object (\"always model\")", Paths: []string{"$.data[].object"}, Values: []string{"model"}},
			},
		},
	})
}
