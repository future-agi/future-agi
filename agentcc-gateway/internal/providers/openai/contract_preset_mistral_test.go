package openai

import "github.com/futureagi/agentcc-gateway/internal/providers/contracttest"

// The mistral preset (api.mistral.ai). Its spec is not public
// (contractspec/subsets/unfetchable.json); the value sets come from its
// official SDK's generated types: mistralai client-python 90e3009
// models/*.py.
//
// Mistral serves no /v1/completions (its FIM is /v1/fim/completions, another
// request), /v1/images/generations, /v1/audio/translations, assistants or
// vector stores (the SDK's 290 operation paths have none), so those cells are
// not served. Its speech answer is JSON, not audio (gap MI4).
func init() {
	registerContractPreset(contractPreset{
		Name: "mistral",
		Enums: map[string][]contracttest.EnumSpec{
			contracttest.EndpointChat: withErrors(mistralErrorEnums, []contracttest.EnumSpec{
				{Spec: "models/chatcompletionchoice.py ChatCompletionChoiceFinishReason", Paths: []string{"$.choices[].finish_reason"},
					Values: []string{"stop", "length", "model_length", "error", "tool_calls"}},
				{Spec: "models/completionresponsestreamchoice.py CompletionResponseStreamChoiceFinishReason", Paths: []string{"chunk:$.choices[].finish_reason"},
					Values: []string{"stop", "length", "error", "tool_calls"}},
				{Spec: "models/assistantmessage.py AssistantMessage.role", Paths: []string{"$.choices[].message.role"}, Values: []string{"assistant"}},
			}),
			contracttest.EndpointOCR: mistralErrorEnums,
			// EmbeddingResponse's object fields are free strings.
			contracttest.EndpointEmbeddings: mistralErrorEnums,
			// SpeechResponse is {audio_data} alone.
			contracttest.EndpointSpeech: mistralErrorEnums,
			contracttest.EndpointTranscription: withErrors(mistralErrorEnums, []contracttest.EnumSpec{
				{Spec: "models/transcriptionsegmentchunk.py TranscriptionSegmentChunk.type", Paths: []string{"$.segments[].type"},
					Values: []string{"transcription_segment"}},
			}),
			contracttest.EndpointResponses: withErrors(mistralErrorEnums, mistralResponsesEnums),
			contracttest.EndpointListModels: {
				{Spec: "models/modellist.py ModelListData (discriminator)", Paths: []string{"$.data[].type"}, Values: []string{"base", "fine-tuned"}},
			},
		},
		// ResponsesStreamFrame is {event, data}: every event is named, and
		// its data is one of 16 event types, discriminated by the same type.
		// The SDK's EventStream for it has no sentinel.
		ResponsesStream: &responsesFraming{Events: mistralResponsesEvents},
	})
}

// mistralErrorEnums are the generic error body's ({"object": "error", ...},
// which the SDK's error hook reads: extra/observability/otel.py).
var mistralErrorEnums = []contracttest.EnumSpec{
	{Spec: "the API error body's object (extra/observability/otel.py)", Paths: []string{"error:$.object"}, Values: []string{"error"}},
}

// mistralResponsesEvents are models/responsesstreamframe.py
// ResponsesStreamFrameData's types (its discriminator).
var mistralResponsesEvents = []string{
	"error", "response.completed", "response.content_part.added", "response.content_part.done", "response.created",
	"response.failed", "response.function_call_arguments.delta", "response.function_call_arguments.done",
	"response.in_progress", "response.incomplete", "response.output_item.added", "response.output_item.done",
	"response.output_text.delta", "response.output_text.done", "response.reasoning_text.delta", "response.reasoning_text.done",
}

// mistralResponsesEnums are CreateResponseResponse's and the stream events'
// (models/createresponseresponse.py and models/responses*.py): the
// /v1/responses passthrough relays the body or stream, and the server prices
// it from model and usage. "*:" roots are the stream events, whose type
// checkStreamFraming checks against mistralResponsesEvents and the event's
// name (an "*:$.type" set would also match the error body's type).
var mistralResponsesEnums = []contracttest.EnumSpec{
	{Spec: "CreateResponseResponse.object", Paths: []string{"$.object", "*:$.response.object"}, Values: []string{"response"}},
	{Spec: "CreateResponseResponseStatus", Paths: []string{"$.status", "*:$.response.status"},
		Values: []string{"completed", "failed", "in_progress", "cancelled", "queued", "incomplete"}},
	{Spec: "ResponsesIncompleteDetails.reason", Paths: []string{"$.incomplete_details.reason", "*:$.response.incomplete_details.reason"},
		Values: []string{"max_output_tokens", "content_filter"}},
	{Spec: "CreateResponseResponseOutput (discriminator)", Paths: []string{"$.output[].type", "*:$.response.output[].type", "*:$.item.type"},
		Values: []string{"message", "function_call", "reasoning"}},
	{Spec: "ResponsesOutputMessageStatus / ResponsesFunctionToolCallStatus / ResponsesReasoningItemStatus", Paths: []string{
		"$.output[].status", "*:$.response.output[].status", "*:$.item.status"}, Values: []string{"in_progress", "completed", "incomplete"}},
	{Spec: "ResponsesOutputMessage.role", Paths: []string{"$.output[].role", "*:$.response.output[].role", "*:$.item.role"},
		Values: []string{"assistant"}},
	{Spec: "ResponsesOutputMessagePhase", Paths: []string{"$.output[].phase", "*:$.response.output[].phase", "*:$.item.phase"},
		Values: []string{"commentary", "final_answer"}},
	{Spec: "ResponsesOutputMessage.content[] / ResponsesReasoningItem.content[] / ResponsesContentPart{Added,Done}Event.part (discriminators)",
		Paths: []string{"$.output[].content[].type", "*:$.response.output[].content[].type", "*:$.item.content[].type",
			"response.content_part.added:$.part.type", "response.content_part.done:$.part.type"},
		Values: []string{"output_text", "refusal", "reasoning_text"}},
	{Spec: "ResponsesSummary.type", Paths: []string{"$.output[].summary[].type", "*:$.response.output[].summary[].type", "*:$.item.summary[].type"},
		Values: []string{"summary_text"}},
	{Spec: "ResponsesOutputText.annotations[] (discriminator)", Paths: []string{
		"$.output[].content[].annotations[].type", "*:$.response.output[].content[].annotations[].type", "*:$.item.content[].annotations[].type",
		"response.content_part.added:$.part.annotations[].type", "response.content_part.done:$.part.annotations[].type"},
		Values: []string{"container_file_citation", "file_citation", "file_path", "url_citation"}},
	{Spec: "CreateResponseResponseTool (discriminator)", Paths: []string{"$.tools[].type", "*:$.response.tools[].type"},
		Values: []string{"function", "namespace"}},
	{Spec: "ResponsesSupportedReasoningEffort", Paths: []string{"$.reasoning.effort", "*:$.response.reasoning.effort"},
		Values: []string{"none", "minimal", "low", "medium", "high", "xhigh", "max"}},
	{Spec: "ResponsesTextConfigFormat (discriminator)", Paths: []string{"$.text.format.type", "*:$.response.text.format.type"},
		Values: []string{"text", "json_object", "json_schema"}},
	{Spec: "ResponsesTextConfigVerbosity", Paths: []string{"$.text.verbosity", "*:$.response.text.verbosity"}, Values: []string{"low", "medium", "high"}},
	{Spec: "PromptCacheRetention", Paths: []string{"$.prompt_cache_retention", "*:$.response.prompt_cache_retention"}, Values: []string{"in_memory", "24h"}},
	{Spec: "ServiceTier", Paths: []string{"$.service_tier", "*:$.response.service_tier"}, Values: []string{"auto", "default", "flex", "scale", "priority"}},
	{Spec: "Truncation", Paths: []string{"$.truncation", "*:$.response.truncation"}, Values: []string{"auto", "disabled"}},
	{Spec: "ResponsesError.code", Paths: []string{"$.error.code", "*:$.response.error.code"},
		Values: []string{"server_error", "rate_limit_exceeded", "invalid_prompt", "bio_policy", "vector_store_timeout", "invalid_image",
			"invalid_image_format", "invalid_base64_image", "invalid_image_url", "image_too_large", "image_too_small", "image_parse_error",
			"image_content_policy_violation", "invalid_image_mode", "image_file_too_large", "unsupported_image_media_type",
			"empty_image_file", "failed_to_download_image", "image_file_not_found"}},
}
