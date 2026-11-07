package openai

import "github.com/futureagi/agentcc-gateway/internal/providers/contracttest"

// The openrouter preset (openrouter.ai/api). Its value sets come from
// OpenRouter's OpenAPI spec as vendored in its official SDK,
// github.com/OpenRouterTeam/python-sdk .speakeasy/in.openapi.yaml at
// 752139b7d0b9a71baece3468203a358e07a341b5 (pinned as
// contractspec/subsets/openrouter, whose source.json has the hash). Its error
// bodies carry an integer code and no type (gap OR1), so no endpoint declares
// an error value set.
//
// OpenRouter still serves /api/v1/completions as legacy text completions
// (OpenRouterTeam/docs c1b66058, 2026-10-28,
// guides/features/router-metadata.mdx), although its spec dropped
// POST /completions in python-sdk 1cad51ab (2026-02-05); that endpoint's
// value sets come from the last revision that modeled it, python-sdk
// 0c791b04d12fa94996bc78d0e00a5440a074785c (2026-01-21). It does not serve
// /api/v1/audio/translations (never in its spec), so that cell is not
// served; it serves image generation at another path (PathGaps).
func init() {
	registerContractPreset(contractPreset{
		Name: "openrouter",
		Spec: true,
		Enums: map[string][]contracttest.EnumSpec{
			contracttest.EndpointChat: {
				{Spec: "ChatResult.object", Paths: []string{"$.object"}, Values: []string{"chat.completion"}},
				{Spec: "ChatStreamingResponse.data.object", Paths: []string{"chunk:$.object"}, Values: []string{"chat.completion.chunk"}},
				{Spec: "ChatFinishReasonEnum", Paths: []string{"$.choices[].finish_reason", "chunk:$.choices[].finish_reason"},
					Values: []string{"stop", "length", "tool_calls", "content_filter", "error"}},
				{Spec: "ChatAssistantMessage.role", Paths: []string{"$.choices[].message.role", "chunk:$.choices[].delta.role"}, Values: []string{"assistant"}},
				{Spec: "ChatToolCall.type", Paths: []string{"$.choices[].message.tool_calls[].type", "chunk:$.choices[].delta.tool_calls[].type"},
					Values: []string{"function"}},
				{Spec: "ReasoningDetail (discriminator)", Paths: []string{"$.choices[].message.reasoning_details[].type", "chunk:$.choices[].delta.reasoning_details[].type"},
					Values: []string{"reasoning.encrypted", "reasoning.server_tool_call", "reasoning.summary", "reasoning.text"}},
				{Spec: "ReasoningDetail.format", Paths: []string{"$.choices[].message.reasoning_details[].format", "chunk:$.choices[].delta.reasoning_details[].format"},
					Values: []string{"anthropic-claude-v1", "azure-openai-responses-v1", "bedrock-openai-responses-v1", "bedrock-xai-responses-v1",
						"google-gemini-v1", "meta-responses-v1", "openai-responses-v1", "unknown", "xai-responses-v1"}},
			},
			contracttest.EndpointCompletions: {
				{Spec: "0c791b04 CompletionResponse.object (const), and the docs' Response.object (api_reference/overview.mdx)",
					Paths: []string{"$.object"}, Values: []string{"text_completion", "chat.completion"}},
				{Spec: "0c791b04 CompletionFinishReason", Paths: []string{"$.choices[].finish_reason"}, Values: []string{"stop", "length", "content_filter"}},
			},
			contracttest.EndpointEmbeddings: {
				{Spec: "POST /embeddings 200 object", Paths: []string{"$.object"}, Values: []string{"list"}},
				{Spec: "POST /embeddings 200 data[].object", Paths: []string{"$.data[].object"}, Values: []string{"embedding"}},
			},
			// Raw audio bytes.
			contracttest.EndpointSpeech: nil,
			contracttest.EndpointTranscription: {
				{Spec: "STTWord.type", Paths: []string{"$.words[].type"}, Values: []string{"word", "audio_event"}},
			},
			contracttest.EndpointResponses: openRouterResponsesEnums,
			contracttest.EndpointListModels: {
				{Spec: "ModelArchitecture.input_modalities[]", Paths: []string{"$.data[].architecture.input_modalities[]"},
					Values: []string{"audio", "file", "image", "text", "video"}},
				{Spec: "ModelArchitecture.instruct_type", Paths: []string{"$.data[].architecture.instruct_type"},
					Values: []string{"airoboros", "alpaca", "alpaca-modif", "chatml", "claude", "code-llama", "deepseek-r1", "deepseek-v3.1",
						"gemma", "llama2", "llama3", "mistral", "nemotron", "neural", "none", "openchat", "phi3", "qwen3", "qwq", "rwkv",
						"vicuna", "zephyr"}},
				{Spec: "ModelArchitecture.output_modalities[]", Paths: []string{"$.data[].architecture.output_modalities[]"},
					Values: []string{"audio", "decisions", "embeddings", "image", "rerank", "speech", "text", "transcription", "video"}},
				{Spec: "ModelArchitecture.tokenizer", Paths: []string{"$.data[].architecture.tokenizer"},
					Values: []string{"Claude", "Cohere", "DeepSeek", "GPT", "Gemini", "Gemma", "Grok", "Llama2", "Llama3", "Llama4", "Media",
						"Mistral", "Nova", "Other", "PaLM", "Qwen", "Qwen3", "RWKV", "Router", "Yi"}},
				{Spec: "PricingOverride.utc_days[]", Paths: []string{"$.data[].pricing.overrides[].utc_days[]"},
					Values: []string{"friday", "monday", "saturday", "sunday", "thursday", "tuesday", "wednesday"}},
				{Spec: "ModelReasoning.default_effort / supported_efforts[]", Paths: []string{
					"$.data[].reasoning.default_effort", "$.data[].reasoning.supported_efforts[]"},
					Values: []string{"high", "low", "max", "medium", "minimal", "none", "xhigh"}},
				{Spec: "Model.supported_parameters[]", Paths: []string{"$.data[].supported_parameters[]"},
					Values: []string{"frequency_penalty", "include_reasoning", "logit_bias", "logprobs", "max_completion_tokens", "max_tokens",
						"min_p", "parallel_tool_calls", "prediction", "presence_penalty", "reasoning", "reasoning_effort", "repetition_penalty",
						"response_format", "seed", "stop", "structured_outputs", "temperature", "tool_choice", "tools", "top_a", "top_k",
						"top_logprobs", "top_p", "verbosity", "web_search_options"}},
			},
		},
		// ResponsesStreamingResponse models a data field alone, so the
		// stream is data lines without event: names, and the operation's
		// x-speakeasy-sse-sentinel ends it with [DONE].
		ResponsesStream: &responsesFraming{Events: openRouterResponsesEvents, DataOnly: true, Sentinel: "[DONE]"},
		PathGaps: []presetPathGap{{
			Endpoint:   contracttest.EndpointImages,
			Documented: []string{"/v1/images"},
			Source: "OpenRouterTeam/python-sdk@752139b7d0b9a71baece3468203a358e07a341b5:.speakeasy/in.openapi.yaml " +
				"POST /images (createImages; server https://openrouter.ai/api/v1), which has no /images/generations",
			Gap: "known gap OR7: CreateImage sends OpenRouter image requests to /api/v1/images/generations, a path OpenRouter does " +
				"not serve: its spec serves image generation at POST /api/v1/images (createImages), with its own " +
				"ImageGenerationRequest, so an image request through the openrouter preset fails upstream instead of generating " +
				"an image",
		}},
	})
}

// openRouterResponsesEvents are StreamEvents' types (its discriminator).
var openRouterResponsesEvents = []string{
	"error", "response.apply_patch_call_operation_diff.delta", "response.apply_patch_call_operation_diff.done",
	"response.code_interpreter_call.completed", "response.code_interpreter_call.in_progress", "response.code_interpreter_call.interpreting",
	"response.code_interpreter_call_code.delta", "response.code_interpreter_call_code.done",
	"response.completed", "response.content_part.added", "response.content_part.done", "response.created",
	"response.custom_tool_call_input.delta", "response.custom_tool_call_input.done", "response.debug", "response.failed",
	"response.function_call_arguments.delta", "response.function_call_arguments.done",
	"response.fusion_call.analysis.completed", "response.fusion_call.analysis.in_progress", "response.fusion_call.completed",
	"response.fusion_call.in_progress", "response.fusion_call.panel.added", "response.fusion_call.panel.completed",
	"response.fusion_call.panel.delta", "response.fusion_call.panel.failed", "response.fusion_call.panel.reasoning.delta",
	"response.image_generation_call.completed", "response.image_generation_call.generating",
	"response.image_generation_call.in_progress", "response.image_generation_call.partial_image",
	"response.in_progress", "response.incomplete", "response.output_item.added", "response.output_item.done",
	"response.output_text.annotation.added", "response.output_text.delta", "response.output_text.done",
	"response.reasoning_summary_part.added", "response.reasoning_summary_part.done",
	"response.reasoning_summary_text.delta", "response.reasoning_summary_text.done",
	"response.reasoning_text.delta", "response.reasoning_text.done", "response.refusal.delta", "response.refusal.done",
	"response.web_search_call.completed", "response.web_search_call.in_progress", "response.web_search_call.searching",
}

// openRouterResponsesEnums are OpenResponsesResult's and StreamEvents': the
// /v1/responses passthrough relays the body or stream, and the server prices
// it from model and usage. "*:" roots are the stream events.
var openRouterResponsesEnums = []contracttest.EnumSpec{
	{Spec: "StreamEvents (discriminator)", Paths: []string{"*:$.type"}, Values: openRouterResponsesEvents},
	{Spec: "BaseResponsesResult.object", Paths: []string{"$.object", "*:$.response.object"}, Values: []string{"response"}},
	{Spec: "OpenAIResponsesResponseStatus", Paths: []string{"$.status", "*:$.response.status"},
		Values: []string{"completed", "incomplete", "in_progress", "failed", "cancelled", "queued"}},
	{Spec: "IncompleteDetails.reason", Paths: []string{"$.incomplete_details.reason", "*:$.response.incomplete_details.reason"},
		Values: []string{"max_output_tokens", "content_filter"}},
	{Spec: "OutputItems (discriminator)", Paths: []string{"$.output[].type", "*:$.response.output[].type", "*:$.item.type"},
		Values: []string{"apply_patch_call", "code_interpreter_call", "computer_call", "custom_tool_call", "file_search_call", "function_call",
			"image_generation_call", "message", "openrouter:advisor", "openrouter:apply_patch", "openrouter:armor_blocked",
			"openrouter:bash", "openrouter:browser_use", "openrouter:code_interpreter", "openrouter:datetime",
			"openrouter:experimental__search_models", "openrouter:file_search", "openrouter:files", "openrouter:fusion",
			"openrouter:image_generation", "openrouter:mcp", "openrouter:memory", "openrouter:shell", "openrouter:subagent",
			"openrouter:text_editor", "openrouter:tool_search", "openrouter:web_fetch", "openrouter:web_search", "reasoning",
			"shell_call", "shell_call_output", "tool_search_call", "web_search_call"}},
	{Spec: "OutputMessage.content[] / OutputReasoningItem.content[] / ContentPart{Added,Done}Event.part (discriminators)", Paths: []string{
		"$.output[].content[].type", "*:$.response.output[].content[].type", "*:$.item.content[].type",
		"response.content_part.added:$.part.type", "response.content_part.done:$.part.type"},
		Values: []string{"output_text", "refusal", "reasoning_text"}},
	{Spec: "OpenAIResponsesAnnotation (discriminator)", Paths: []string{
		"$.output[].content[].annotations[].type", "*:$.response.output[].content[].annotations[].type", "*:$.item.content[].annotations[].type",
		"response.content_part.added:$.part.annotations[].type", "response.content_part.done:$.part.annotations[].type"},
		Values: []string{"file_citation", "url_citation", "file_path"}},
	{Spec: "ServiceTier", Paths: []string{"$.service_tier", "*:$.response.service_tier"},
		Values: []string{"auto", "default", "flex", "priority", "scale", "ultrafast"}},
	{Spec: "ResponsesErrorField.code", Paths: []string{"$.error.code", "*:$.response.error.code"},
		Values: []string{"server_error", "rate_limit_exceeded", "invalid_prompt", "vector_store_timeout", "invalid_image",
			"invalid_image_format", "invalid_base64_image", "invalid_image_url", "image_too_large", "image_too_small", "image_parse_error",
			"image_content_policy_violation", "invalid_image_mode", "image_file_too_large", "unsupported_image_media_type",
			"empty_image_file", "failed_to_download_image", "image_file_not_found", "bio_policy", "cyber_policy",
			"misalignment_policy_violation", "data_residency_mismatch", "permission_denied"}},
}
