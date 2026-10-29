package openai

import "github.com/futureagi/agentcc-gateway/internal/providers/contracttest"

// The perplexity preset (api.perplexity.ai). Its spec is not public
// (contractspec/subsets/unfetchable.json); the value sets come from its
// official SDK's generated types: perplexity-py v0.46.1
// types/shared/choice.py and types/shared/chat_message_output.py for chat,
// and perplexity-py 1a79c5d generated/api.py (generated from Perplexity's
// OpenAPI spec) for the rest.
//
// Perplexity serves no /v1/completions, images, audio, assistants, vector
// stores or OCR (generated/api.py's 32 operations have none), so those cells
// are not served. The SDK types no error body but a 422, so no endpoint
// declares an error value set.
func init() {
	registerContractPreset(contractPreset{
		Name: "perplexity",
		Enums: map[string][]contracttest.EnumSpec{
			contracttest.EndpointChat: {
				{Spec: "types/shared/choice.py Choice.finish_reason", Paths: []string{"$.choices[].finish_reason", "chunk:$.choices[].finish_reason"},
					Values: []string{"stop", "length"}},
				{Spec: "types/shared/chat_message_output.py ChatMessageOutput.role", Paths: []string{
					"$.choices[].message.role", "$.choices[].delta.role", "chunk:$.choices[].delta.role", "chunk:$.choices[].message.role"},
					Values: []string{"system", "user", "assistant", "tool"}},
			},
			contracttest.EndpointEmbeddings: {
				{Spec: "generated/api.py EmbeddingsUsageCostOutput.currency", Paths: []string{"$.usage.cost.currency"}, Values: []string{"USD"}},
			},
			contracttest.EndpointResponses: perplexityResponsesEnums,
			contracttest.EndpointListModels: {
				{Spec: "generated/api.py ModelListOutput.object", Paths: []string{"$.object"}, Values: []string{"list"}},
				{Spec: "generated/api.py ModelOutput.object", Paths: []string{"$.data[].object"}, Values: []string{"model"}},
			},
		},
		// The SDK frames a Responses stream as data lines alone, each
		// event's type in its data, with no sentinel
		// (tests/test_generated_api_integration.py; its _streaming.py
		// would stop at a [DONE]).
		ResponsesStream: &responsesFraming{Events: perplexityResponsesEvents, DataOnly: true},
	})
}

// perplexityResponsesEvents are generated/api.py ResponseStreamEventOutput's
// types.
var perplexityResponsesEvents = []string{
	"response.completed", "response.created", "response.failed", "response.in_progress", "response.output_item.added",
	"response.output_item.done", "response.output_text.delta", "response.output_text.done",
	"response.reasoning.fetch_url_queries", "response.reasoning.fetch_url_results", "response.reasoning.image_search_queries",
	"response.reasoning.image_search_results", "response.reasoning.search_queries", "response.reasoning.search_results",
	"response.reasoning.started", "response.reasoning.stopped", "response.skill.loaded",
}

// perplexityResponsesEnums are generated/api.py ResponsesResponseOutput's and
// ResponseStreamEventOutput's: the /v1/responses passthrough relays the body
// or stream, and the server prices it from model and usage. "*:" roots are
// the stream events.
var perplexityResponsesEnums = []contracttest.EnumSpec{
	{Spec: "ResponseStreamEventOutput (discriminator)", Paths: []string{"*:$.type"}, Values: perplexityResponsesEvents},
	{Spec: "ResponsesObjectTypeOutput", Paths: []string{"$.object", "*:$.response.object"}, Values: []string{"response"}},
	{Spec: "StatusOutput", Paths: []string{"$.status", "*:$.response.status", "$.output[].status", "*:$.response.output[].status", "*:$.item.status"},
		Values: []string{"completed", "failed", "in_progress", "queued", "cancelled", "requires_action"}},
	{Spec: "OutputItemOutput (discriminator)", Paths: []string{"$.output[].type", "*:$.response.output[].type", "*:$.item.type"},
		Values: []string{"message", "search_results", "image_search_results", "fetch_url_results", "function_call", "mcp_list_tools",
			"mcp_call", "mcp_approval_request", "tool_search_output", "skill_loaded", "sandbox_results", "sandbox_write_file",
			"sandbox_read_file", "sandbox_edit_file", "sandbox_grep", "sandbox_glob", "sandbox_apply_patch", "share_file", "unknown"}},
	{Spec: "RoleTypeOutput", Paths: []string{"$.output[].role", "*:$.response.output[].role", "*:$.item.role"}, Values: []string{"assistant"}},
	{Spec: "ContentPartTypeOutput", Paths: []string{"$.output[].content[].type", "*:$.response.output[].content[].type", "*:$.item.content[].type"},
		Values: []string{"output_text"}},
	{Spec: "CurrencyOutput", Paths: []string{"$.usage.cost.currency", "*:$.response.usage.cost.currency"}, Values: []string{"USD"}},
}
