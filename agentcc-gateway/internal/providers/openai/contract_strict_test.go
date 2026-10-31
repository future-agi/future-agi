package openai

import (
	"reflect"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// The strict declarations (contracttest.Strict) of every upstream and
// endpoint this adapter serves: the documented value sets, the raw regions
// the adapter decodes later, and the fields that carry nothing by protocol.

// ContractEnums are the value sets each upstream documents for the enum-like
// fields of the bodies this adapter reads, by dialect ("openai" for the
// official API, else the preset name) and endpoint. Every value found at one
// of these paths in a fixture (or a live recording) must be listed, so a new
// finish reason, output item type, stream event or tier fails the suite even
// where the adapter passes it on without a look. Spec names the schema the
// values come from, for the spec-diff check.
//
// The literal holds the official API's, from github.com/openai/openai-openapi
// openapi.json (26a2991, 2026-10-28); error types and codes, which the spec
// leaves free strings, from its examples and the cookbook's
// How_to_handle_rate_limits.ipynb. Each preset adds its own from its
// contract_preset_<name>_test.go (registerContractPreset), which names its
// sources.
var ContractEnums = map[string]map[string][]contracttest.EnumSpec{
	officialDialect: {
		contracttest.EndpointChat:             withErrors(openAIErrorEnums, openAIChatEnums),
		contracttest.EndpointChatViaResponses: withErrors(openAIErrorEnums, openAIResponsesEnums),
		contracttest.EndpointResponses:        withErrors(openAIErrorEnums, openAIResponsesEnums),
		contracttest.EndpointCompletions: withErrors(openAIErrorEnums, []contracttest.EnumSpec{
			{Spec: "CreateCompletionResponse.object", Paths: []string{"$.object"}, Values: []string{"text_completion"}},
			{Spec: "CreateCompletionResponse.choices[].finish_reason", Paths: []string{"$.choices[].finish_reason"},
				Values: []string{"stop", "length", "content_filter"}},
		}),
		contracttest.EndpointEmbeddings: withErrors(openAIErrorEnums, []contracttest.EnumSpec{
			{Spec: "CreateEmbeddingResponse.object", Paths: []string{"$.object"}, Values: []string{"list"}},
			{Spec: "Embedding.object", Paths: []string{"$.data[].object"}, Values: []string{"embedding"}},
		}),
		contracttest.EndpointImages: withErrors(openAIErrorEnums, []contracttest.EnumSpec{
			{Spec: "ImagesResponse.background", Paths: []string{"$.background"}, Values: []string{"transparent", "opaque"}},
			{Spec: "ImagesResponse.output_format", Paths: []string{"$.output_format"}, Values: []string{"png", "webp", "jpeg"}},
			{Spec: "ImagesResponse.quality", Paths: []string{"$.quality"}, Values: []string{"low", "medium", "high", "xhigh", "max"}},
		}),
		contracttest.EndpointSpeech: openAIErrorEnums,
		contracttest.EndpointTranscription: withErrors(openAIErrorEnums, []contracttest.EnumSpec{
			{Spec: "CreateTranscriptionResponseJson.usage.type / CreateTranscriptionResponseVerboseJson.usage.type", Paths: []string{"$.usage.type"},
				Values: []string{"tokens", "duration"}},
			{Spec: "CreateTranscriptionResponseVerboseJson.task / CreateTranscriptionResponseDiarizedJson.task", Paths: []string{"$.task"},
				Values: []string{"transcribe"}},
		}),
		// CreateTranslationResponseJson / VerboseJson have no enum-like fields.
		contracttest.EndpointTranslation: openAIErrorEnums,
		contracttest.EndpointListModels: withErrors(openAIErrorEnums, []contracttest.EnumSpec{
			{Spec: "ListModelsResponse.object", Paths: []string{"$.object"}, Values: []string{"list"}},
			{Spec: "Model.object", Paths: []string{"$.data[].object"}, Values: []string{"model"}},
		}),
		contracttest.EndpointVectorStores: openAIErrorEnums,
	},
}

// withErrors joins an endpoint's value sets with the error body's.
func withErrors(errs, enums []contracttest.EnumSpec) []contracttest.EnumSpec {
	return append(append([]contracttest.EnumSpec(nil), enums...), errs...)
}

// openAIChatEnums are CreateChatCompletionResponse's and
// CreateChatCompletionStreamResponse's.
var openAIChatEnums = []contracttest.EnumSpec{
	{Spec: "CreateChatCompletionResponse.object", Paths: []string{"$.object"}, Values: []string{"chat.completion"}},
	{Spec: "CreateChatCompletionStreamResponse.object", Paths: []string{"chunk:$.object"}, Values: []string{"chat.completion.chunk"}},
	{Spec: "CreateChatCompletionResponse.choices[].finish_reason (the stream's adds null)",
		Paths:  []string{"$.choices[].finish_reason", "chunk:$.choices[].finish_reason"},
		Values: []string{"stop", "length", "tool_calls", "content_filter", "function_call"}},
	{Spec: "ChatCompletionResponseMessage.role", Paths: []string{"$.choices[].message.role"}, Values: []string{"assistant"}},
	{Spec: "ChatCompletionStreamResponseDelta.role", Paths: []string{"chunk:$.choices[].delta.role"},
		Values: []string{"developer", "system", "user", "assistant", "tool"}},
	{Spec: "ChatCompletionMessageToolCalls[] (discriminator)", Paths: []string{"$.choices[].message.tool_calls[].type"},
		Values: []string{"function", "custom"}},
	{Spec: "ChatCompletionMessageToolCallChunk.type", Paths: []string{"chunk:$.choices[].delta.tool_calls[].type"}, Values: []string{"function"}},
	{Spec: "ChatCompletionResponseMessage.annotations[].type", Paths: []string{"$.choices[].message.annotations[].type"},
		Values: []string{"url_citation"}},
	{Spec: "ServiceTier", Paths: []string{"$.service_tier", "chunk:$.service_tier"},
		Values: []string{"auto", "default", "flex", "scale", "priority", "fast"}},
	{Spec: "ChatCompletionModeration.{input,output}.type", Paths: []string{
		"$.moderation.input.type", "$.moderation.output.type", "chunk:$.moderation.input.type", "chunk:$.moderation.output.type"},
		Values: []string{"moderation_results", "error"}},
	{Spec: "ChatCompletionModerationResults.results[].type", Paths: []string{
		"$.moderation.input.results[].type", "$.moderation.output.results[].type",
		"chunk:$.moderation.input.results[].type", "chunk:$.moderation.output.results[].type"},
		Values: []string{"moderation_result"}},
}

// responsesStreamEvents are ResponseStreamEvent's 59 event types.
var responsesStreamEvents = []string{
	"error",
	"response.audio.delta", "response.audio.done", "response.audio.transcript.delta", "response.audio.transcript.done",
	"response.code_interpreter_call.completed", "response.code_interpreter_call.in_progress", "response.code_interpreter_call.interpreting",
	"response.code_interpreter_call_code.delta", "response.code_interpreter_call_code.done",
	"response.compaction.compacting", "response.completed", "response.content_part.added", "response.content_part.done", "response.created",
	"response.custom_tool_call_input.delta", "response.custom_tool_call_input.done", "response.failed",
	"response.file_search_call.completed", "response.file_search_call.in_progress", "response.file_search_call.searching",
	"response.function_call_arguments.delta", "response.function_call_arguments.done",
	"response.image_generation_call.completed", "response.image_generation_call.generating",
	"response.image_generation_call.in_progress", "response.image_generation_call.partial_image",
	"response.in_progress", "response.incomplete",
	"response.mcp_call.completed", "response.mcp_call.failed", "response.mcp_call.in_progress",
	"response.mcp_call_arguments.delta", "response.mcp_call_arguments.done",
	"response.mcp_list_tools.completed", "response.mcp_list_tools.failed", "response.mcp_list_tools.in_progress",
	"response.output_item.added", "response.output_item.done", "response.output_text.annotation.added",
	"response.output_text.delta", "response.output_text.done", "response.queued",
	"response.reasoning_summary_part.added", "response.reasoning_summary_part.done",
	"response.reasoning_summary_text.delta", "response.reasoning_summary_text.done",
	"response.reasoning_text.delta", "response.reasoning_text.done", "response.refusal.delta", "response.refusal.done",
	"response.shell_call_command.added", "response.shell_call_command.delta", "response.shell_call_command.done",
	"response.shell_call_output_content.delta", "response.shell_call_output_content.done",
	"response.web_search_call.completed", "response.web_search_call.in_progress", "response.web_search_call.searching",
}

// responsesConvertedEvents are the stream events responsesStreamConverter
// reads; it skips the rest (and reports the ones that carry output it
// drops), so their payloads are opaque to it.
var responsesConvertedEvents = map[string]bool{
	"response.created": true, "response.output_text.delta": true, "response.refusal.delta": true,
	"response.output_item.added": true, "response.function_call_arguments.delta": true,
	"response.completed": true, "response.incomplete": true, "response.failed": true, "error": true,
}

// openAIResponsesEnums are the Responses API's (Response, OutputItem,
// ResponseStreamEvent), for the passthrough and the chat translator alike.
// "*:" roots are the stream events; a Responses Strict sees no other event
// roots, so the wildcard cannot catch a chat chunk.
var openAIResponsesEnums = []contracttest.EnumSpec{
	{Spec: "ResponseStreamEvent (discriminator)", Paths: []string{"*:$.type"}, Values: responsesStreamEvents},
	{Spec: "Response.object", Paths: []string{"$.object", "*:$.response.object"}, Values: []string{"response"}},
	{Spec: "Response.status", Paths: []string{"$.status", "*:$.response.status"},
		Values: []string{"completed", "failed", "in_progress", "cancelled", "queued", "incomplete"}},
	{Spec: "Response.incomplete_details.reason", Paths: []string{"$.incomplete_details.reason", "*:$.response.incomplete_details.reason"},
		Values: []string{"max_output_tokens", "content_filter", "max_messages", "steered"}},
	{Spec: "OutputItem (discriminator)", Paths: []string{"$.output[].type", "*:$.response.output[].type", "*:$.item.type"},
		Values: []string{"additional_tools", "apply_patch_call", "apply_patch_call_output", "code_interpreter_call", "compaction",
			"computer_call", "computer_call_output", "custom_tool_call", "custom_tool_call_output", "file_search_call",
			"function_call", "function_call_output", "image_generation_call", "local_shell_call", "local_shell_call_output",
			"mcp_approval_request", "mcp_approval_response", "mcp_call", "mcp_list_tools", "message", "program", "program_output",
			"reasoning", "shell_call", "shell_call_output", "tool_search_call", "tool_search_output", "web_search_call"}},
	{Spec: "OutputMessage.content[] (discriminator)", Paths: []string{
		"$.output[].content[].type", "*:$.response.output[].content[].type", "*:$.item.content[].type",
		"response.content_part.added:$.part.type", "response.content_part.done:$.part.type"},
		Values: []string{"output_text", "refusal", "reasoning_text"}},
	{Spec: "ReasoningItem.summary[].type", Paths: []string{"$.output[].summary[].type", "*:$.response.output[].summary[].type", "*:$.item.summary[].type",
		"response.reasoning_summary_part.added:$.part.type", "response.reasoning_summary_part.done:$.part.type"},
		Values: []string{"summary_text"}},
	{Spec: "ServiceTierResponses", Paths: []string{"$.service_tier", "*:$.response.service_tier"},
		Values: []string{"auto", "default", "flex", "scale", "priority", "fast", "ultrafast"}},
	{Spec: "ResponseError.code", Paths: []string{"$.error.code", "*:$.response.error.code"},
		Values: []string{"server_error", "rate_limit_exceeded", "invalid_prompt", "bio_policy", "data_residency_mismatch",
			"misalignment_policy_violation", "vector_store_timeout", "invalid_image", "invalid_image_format", "invalid_base64_image",
			"invalid_image_url", "image_too_large", "image_too_small", "image_parse_error", "image_content_policy_violation",
			"invalid_image_mode", "image_file_too_large", "unsupported_image_media_type", "empty_image_file",
			"failed_to_download_image", "image_file_not_found"}},
}

// openAIErrorEnums are the error body's (ErrorResponse / Error, also the
// data of a failing stream's error chunk). The spec types Error.type and
// Error.code as free strings, so the values are the documented examples:
// the spec's (InferenceRateLimited slow_down, InferenceServiceUnavailable
// server_is_overloaded, the unknown_parameter and project_not_found
// examples, server_error) and the cookbook's 429 insufficient_quota. A new
// value is a new failure mode to map, which is why they are pinned.
var openAIErrorEnums = append(openAIErrorEnumsFor("Error.type (a free string; the spec's and cookbook's documented values)"),
	contracttest.EnumSpec{Spec: "Error.code (a free string; the spec's and cookbook's documented values)", Paths: []string{"error:$.error.code"},
		Values: []string{"slow_down", "server_is_overloaded", "insufficient_quota", "unknown_parameter", "project_not_found"}},
	contracttest.EnumSpec{Spec: "MisalignmentErrorDetailsResource.error_type", Paths: []string{"error:$.error.misalignment.error_type"},
		Values: []string{"potentially_unintended_data_transfer", "potentially_unintended_data_access",
			"potentially_unintended_destructive_activity", "other"}},
)

// openAIErrorEnumsFor is the error type set, attributed to spec.
func openAIErrorEnumsFor(spec string) []contracttest.EnumSpec {
	return []contracttest.EnumSpec{{Spec: spec, Paths: []string{"error:$.error.type"},
		Values: []string{"invalid_request_error", "rate_limit_error", "insufficient_quota", "server_error", "service_unavailable_error"}}}
}

// contractDecoded are the raw regions each endpoint's adapter code decodes
// later. models.ChatCompletionResponse, StreamChunk and Usage capture unknown
// top-level keys in Extra and forward them, so the walk treats them as
// opaque; their choices are declared, since the nested types have no Extra
// and drop unknown keys.
var contractDecoded = map[string][]contracttest.Decoded{
	contracttest.EndpointChat: {
		{Path: "$.choices", Type: reflect.TypeFor[[]models.Choice]()},
		{Path: "chunk:$.choices", Type: reflect.TypeFor[[]models.StreamChoice]()},
	},
}

// contractIgnore are fields that carry nothing by protocol, for every model
// of an endpoint.
var contractIgnore = map[string]map[string]string{
	contracttest.EndpointChatViaResponses: responsesProtocolFields(),
}

// responsesEcho are the Response fields that echo the request's parameters:
// the chat request already holds them.
var responsesEcho = []string{
	"background", "conversation", "instructions", "max_output_tokens", "max_tool_calls", "metadata", "parallel_tool_calls",
	"previous_response_id", "prompt", "prompt_cache_key", "prompt_cache_retention", "reasoning", "safety_identifier",
	"store", "temperature", "text", "tool_choice", "tools", "top_logprobs", "top_p", "truncation", "user",
}

// responsesProtocolFields are the Responses fields the chat translation
// does not read because they carry nothing a chat completion has a place
// for: the echoed request, the constant object type, and the stream's
// bookkeeping (sequence numbers and output positions; the converter numbers
// tool calls itself and keys deltas by item_id).
func responsesProtocolFields() map[string]string {
	out := map[string]string{}
	for _, root := range []string{"$", "response.created:$.response", "response.completed:$.response",
		"response.incomplete:$.response", "response.failed:$.response"} {
		for _, f := range responsesEcho {
			out[root+"."+f] = "echoes the request parameter, which the chat request already holds"
		}
		out[root+".object"] = `always "response"`
		out[root+".output[].role"] = `always "assistant" on a message, the chat message's role`
		out[root+".output[].status"] = "an item's own status; the response's status and incomplete_details decide the finish reason"
	}
	out["response.output_item.added:$.item.role"] = `always "assistant" on a message, the chat message's role`
	out["response.output_item.added:$.item.status"] = `always "in_progress" when an item is added`
	for ev := range responsesConvertedEvents {
		out[ev+":$.sequence_number"] = "stream bookkeeping: the events arrive in order"
	}
	for _, ev := range []string{"response.output_item.added", "response.output_text.delta", "response.refusal.delta",
		"response.function_call_arguments.delta"} {
		out[ev+":$.output_index"] = "the converter numbers tool calls itself, in the order they start, and keys deltas by item_id"
	}
	for _, ev := range []string{"response.output_text.delta", "response.refusal.delta"} {
		out[ev+":$.content_index"] = "a message's parts are concatenated in order"
	}
	return out
}

// contractStricts are the strict declarations by "<dialect>/<endpoint>",
// built on first use: the presets add their value sets to ContractEnums from
// their files' init functions, which run after package variables are set.
var contractStricts = sync.OnceValue(buildContractStricts)

func buildContractStricts() map[string]*contracttest.Strict {
	out := map[string]*contracttest.Strict{}
	for dialect, byEndpoint := range ContractEnums {
		for endpoint, enums := range byEndpoint {
			out[dialect+"/"+endpoint] = &contracttest.Strict{
				Docs:    contractDocs,
				Enums:   enums,
				Decoded: contractDecoded[endpoint],
				Ignore:  contractIgnore[endpoint],
			}
		}
	}
	return out
}
