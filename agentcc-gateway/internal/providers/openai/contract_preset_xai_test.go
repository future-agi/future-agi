package openai

import "github.com/futureagi/agentcc-gateway/internal/providers/contracttest"

// The xai preset (api.x.ai). Its value sets come from xAI's OpenAPI spec as
// published in its official SDK, github.com/xai-org/xai-sdk-ts
// spec/openapi.json at 51bfc686c74310226d601e6037fa6c2e562244f7 (pinned as
// contractspec/subsets/xai, whose source.json has the hash), and, for the
// Responses stream, which the spec does not model, from the same SDK's
// src/types.ts and src/sse.ts.
//
// The spec gives finish_reason no enum (a string whose description names
// stop, length and end_turn), so the spec check skips the adapter's
// finish-reason check (SpecFreeStrings) and the fixtures pin the values
// instead. It models no error body (400 and 422 have none), no Responses
// stream and no audio: the fixtures are the only coverage of those. It also
// has /v1/messages (Anthropic-compatible), which the preset never calls.
//
// xAI serves speech at /v1/tts and transcription at /v1/stt, not at
// /v1/audio/*, and has no translation, assistants, vector stores or OCR, so
// those cells are not served.
func init() {
	registerContractPreset(contractPreset{
		Name:            "xai",
		Spec:            true,
		SpecFreeStrings: []string{"$.choices[].finish_reason", "chunk:$.choices[].finish_reason"},
		Enums: map[string][]contracttest.EnumSpec{
			contracttest.EndpointChat: {
				{Spec: "ChatResponse / ChatResponseChunk choices[].finish_reason (the description's values; no enum)",
					Paths: []string{"$.choices[].finish_reason", "chunk:$.choices[].finish_reason"}, Values: []string{"stop", "length", "end_turn"}},
				{Spec: "ServiceTier", Paths: []string{"$.service_tier", "chunk:$.service_tier"}, Values: []string{"default", "fast", "priority"}},
			},
			contracttest.EndpointCompletions: {
				{Spec: "SampleResponse choices[].finish_reason (the description's values; no enum)", Paths: []string{"$.choices[].finish_reason"},
					Values: []string{"stop", "length", "end_turn"}},
			},
			contracttest.EndpointEmbeddings: nil,
			contracttest.EndpointImages:     nil,
			contracttest.EndpointResponses:  xaiResponsesEnums,
			contracttest.EndpointListModels: nil,
		},
		// The SDK's stream tests frame a Responses stream as data lines
		// alone, ending with data: [DONE] (test/helpers.ts sseResponse;
		// src/sse.ts stops there).
		ResponsesStream: &responsesFraming{Events: xaiResponsesEvents, DataOnly: true, Sentinel: "[DONE]"},
	})
}

// xaiResponsesEvents are src/types.ts ResponseStreamEvent's types (the spec
// models no stream events).
var xaiResponsesEvents = []string{
	"error", "ping", "response.completed", "response.content_part.added", "response.content_part.done", "response.created",
	"response.failed", "response.function_call_arguments.delta", "response.function_call_arguments.done",
	"response.image_generation_call.completed", "response.image_generation_call.generating",
	"response.image_generation_call.in_progress", "response.in_progress", "response.incomplete", "response.output_item.added",
	"response.output_item.done", "response.output_text.delta", "response.output_text.done", "response.reasoning_summary_text.delta",
	"response.reasoning_summary_text.done", "response.reasoning_text.delta", "response.reasoning_text.done",
}

// xaiResponsesEnums are ModelResponse's (the spec) and the stream events'
// (src/types.ts): the /v1/responses passthrough relays the body or stream,
// and the server prices it from model and usage. "*:" roots are the stream
// events.
var xaiResponsesEnums = []contracttest.EnumSpec{
	{Spec: "src/types.ts ResponseStreamEvent (discriminator)", Paths: []string{"*:$.type"}, Values: xaiResponsesEvents},
	{Spec: "ModelResponse output item types (the spec enumerates image_generation_call, tool_search_call and tool_search_output; " +
		"the other variants' type is a plain string naming the variant)",
		Paths: []string{"$.output[].type", "*:$.response.output[].type", "*:$.item.type"},
		Values: []string{"code_interpreter_call", "custom_tool_call", "file_search_call", "function_call", "image_generation_call",
			"mcp_call", "message", "reasoning", "shell_call", "tool_search_call", "tool_search_output", "web_search_call"}},
	{Spec: "OutputMessageContent (discriminator)", Paths: []string{"$.output[].content[].type", "*:$.response.output[].content[].type",
		"*:$.item.content[].type"}, Values: []string{"output_text", "refusal"}},
	{Spec: "web search action type", Paths: []string{"$.output[].action.type", "*:$.response.output[].action.type", "*:$.item.action.type"},
		Values: []string{"find_in_page", "open_page", "search"}},
	{Spec: "code interpreter output type", Paths: []string{"$.output[].outputs[].type", "*:$.response.output[].outputs[].type",
		"*:$.item.outputs[].type"}, Values: []string{"image", "logs"}},
	{Spec: "IncompleteDetails.reason", Paths: []string{"$.incomplete_details.reason", "*:$.response.incomplete_details.reason"},
		Values: []string{"max_output_tokens", "max_prompt_tokens", "max_time_limit"}},
	{Spec: "ServiceTier", Paths: []string{"$.service_tier", "*:$.response.service_tier"}, Values: []string{"default", "fast", "priority"}},
}
