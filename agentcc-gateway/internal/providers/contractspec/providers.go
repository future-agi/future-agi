package contractspec

import "sort"

// The subset definitions: for each provider with a fetchable official spec,
// where the spec lives and which roots the gateway parses from it. A root
// belongs here when an adapter (or a parser built on an adapter's
// endpoints, such as contractwatch's list-models listers) decodes that
// response; byte passthroughs (Anthropic count_tokens, OpenAI speech) are
// not roots, since the gateway reads nothing from them.
//
// Paths of streams of undifferentiated chunks are prefixed "chunk", matching
// the contract tests' meta.json allowlists.

const (
	descChat         = "chat completion response, decoded by the adapter's ChatCompletion"
	descChatStream   = "one chat completion stream chunk (an SSE data line), decoded by StreamChatCompletion"
	descChatErrors   = "error bodies of the chat endpoint (every modeled 4xx/5xx), decoded by the adapter's error parser"
	descEmbeddings   = "embeddings response, decoded by CreateEmbedding"
	descModels       = "list-models response, decoded by ListModels and by contractwatch's listers (new-model detection)"
	descTranscribe   = "transcription response; the gateway lifts text and passes the body through"
	descOpenAICompat = "OpenAI-compatible preset served by the openai adapter"
	// echoed prunes a request parameter a response echoes back.
	echoed = "a request parameter echoed back; the gateway never reads it"
)

var sources = []Source{
	{
		Provider: "openai", Format: FormatOpenAPI,
		Repo: "openai/openai-openapi", Path: "openapi.json",
		License: "MIT",
		Roots: []RootDef{
			{Name: "chat", Op: "POST /chat/completions", Description: descChat},
			{Name: "chat_stream", Op: "POST /chat/completions", Content: "text/event-stream", Prefix: "chunk", Description: descChatStream},
			{Name: "errors", Op: "POST /chat/completions", Status: "errors", Description: descChatErrors + " (parseProviderError; every endpoint shares this shape)"},
			{Name: "completions", Op: "POST /completions", Description: "legacy completions response, decoded by TextCompletion into models.CompletionResponse"},
			{Name: "embeddings", Op: "POST /embeddings", Description: descEmbeddings},
			{Name: "images", Op: "POST /images/generations", Description: "image generation response, decoded by CreateImage into models.ImageResponse (usage drives gpt-image cost)"},
			{Name: "transcription", Op: "POST /audio/transcriptions", Description: descTranscribe},
			{Name: "translation", Op: "POST /audio/translations", Description: "audio translation response; the gateway lifts text and passes the body through"},
			{Name: "responses", Op: "POST /responses", Prune: map[string]string{
				"$.instructions": echoed, "$.tools": echoed, "$.tool_choice": echoed, "$.prompt": echoed,
				"$.text": echoed, "$.reasoning": echoed, "$.conversation": echoed, "$.prompt_cache_options": echoed,
			}, Description: "Responses API response, translated to chat by responses_chat.go (toChat); the /v1/responses passthrough reads its usage"},
			{Name: "responses_stream", Op: "POST /responses", Content: "text/event-stream", Events: true, Elide: map[string]string{
				"#/components/schemas/Response":   "openai/responses $",
				"#/components/schemas/OutputItem": "openai/responses $.output[]",
			}, Description: "Responses API stream events, translated to chat chunks by responses_chat.go (convert)"},
			{Name: "models", Op: "GET /models", Description: descModels},
		},
	},
	{
		Provider: "anthropic", Format: FormatOpenAPI,
		Repo: "anthropics/anthropic-sdk-python", Path: "scripts/mock-spec.json.gz", Decode: DecodeGzip,
		License: "bundled build artifact of an Apache-2.0 SDK; no licence of its own, so only the extract is vendored",
		Roots: []RootDef{
			{Name: "messages", Op: "POST /v1/messages", Description: "Messages response, decoded by ChatCompletion (translateResponse); the native /v1/messages passthrough reads its usage"},
			// The spec defines the stream union but attaches it to no
			// operation, and does not model ping or error.
			{Name: "stream", Schema: "#/components/schemas/MessageStreamEvent", Events: true, Description: "Messages stream events, decoded by StreamChatCompletion (stream.go). The spec omits ping and error"},
			{Name: "errors", Op: "POST /v1/messages", Status: "errors", Description: "error bodies of /v1/messages, decoded by parseAnthropicError (also the data of a mid-stream error event)"},
			{Name: "models", Op: "GET /v1/models", Description: "list-models response, decoded by contractwatch's AnthropicLister (the adapter's ListModels returns nil)"},
		},
	},
	{
		Provider: "gemini", Format: FormatDiscovery,
		URL:     "https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta",
		License: "Google APIs Terms of Service",
		Roots: []RootDef{
			{Name: "generate_content", Method: "models.generateContent", Description: "generateContent response, decoded by ChatCompletion; streamGenerateContent (alt=sse) sends the same schema per chunk, and TTS reads its inlineData"},
			{Name: "embed_content", Method: "models.embedContent", Description: "embedContent response, decoded by translateSingleEmbedResponse"},
			{Name: "batch_embed_contents", Method: "models.batchEmbedContents", Description: "batchEmbedContents response, decoded by translateBatchEmbedResponse"},
			{Name: "predict", Method: "models.predict", Description: "Imagen predict response, decoded by translateImageResponse"},
			{Name: "models", Method: "models.list", Description: "list-models response, decoded by contractwatch's GeminiLister (the adapter's ListModels returns nil)"},
		},
	},
	{
		Provider: "vertex", Format: FormatDiscovery,
		URL:     "https://aiplatform.googleapis.com/$discovery/rest?version=v1beta1",
		License: "Google APIs Terms of Service",
		Roots: []RootDef{
			{Name: "generate_content", Method: "projects.locations.publishers.models.generateContent", Description: "Vertex generateContent response (v1beta1, the version the gemini adapter calls), decoded by the same parser as Gemini; streamGenerateContent sends the same schema per chunk"},
			{Name: "embed_content", Method: "projects.locations.publishers.models.embedContent", Description: "Vertex embedContent response"},
			{Name: "predict", Method: "projects.locations.publishers.models.predict", Description: "Vertex predict response (Imagen)"},
		},
	},
	{
		Provider: "bedrock", Format: FormatSmithy,
		Repo: "aws/api-models-aws", Path: "models/bedrock-runtime/service/2023-09-30/bedrock-runtime-2023-09-30.json",
		License: "Apache-2.0",
		Roots: []RootDef{
			{Name: "converse", Operation: "com.amazonaws.bedrockruntime#Converse", Description: "Converse response, decoded by the Converse path (inference profiles)"},
			{Name: "converse_stream", Operation: "com.amazonaws.bedrockruntime#ConverseStream", Events: true, Description: "ConverseStream event stream (binary AWS event-stream; :event-type names the event), decoded by parseConverseEvent"},
			{Name: "converse_errors", Operation: "com.amazonaws.bedrockruntime#Converse", Errors: true, Description: "Converse error shapes; AWS sends the shape name as x-amzn-ErrorType and the members as the JSON body"},
			// InvokeModel's body is a blob in the spec: the model's own
			// format, Anthropic Messages for Claude. The bedrock spec test
			// binds it with the anthropic subsets.
			{Name: "invoke_stream", Operation: "com.amazonaws.bedrockruntime#InvokeModelWithResponseStream", Events: true, Description: "InvokeModelWithResponseStream event stream: chunk{bytes} carries a base64 model-native event (Anthropic MessageStreamEvent for Claude)"},
		},
	},
	{
		Provider: "cohere", Format: FormatOpenAPI,
		Repo: "cohere-ai/cohere-developer-experience", Path: "cohere-openapi.yaml",
		License: "proprietary (info.license; cohere.com/terms-of-use): only the extract of field names and enum values is vendored, never the file",
		Roots: []RootDef{
			{Name: "chat", Schema: "#/components/schemas/ChatResponseV2", Description: "v2 chat response, decoded by ChatCompletion"},
			{Name: "chat_stream", Schema: "#/components/schemas/StreamedChatResponseV2", Events: true, Description: "v2 chat stream events (x-fern-streaming, terminator [DONE]), decoded by stream.go"},
			{Name: "errors", Op: "POST /v2/chat", Status: "errors", Description: "error bodies of /v2/chat, decoded by the adapter's error parser"},
			{Name: "embed", Op: "POST /v2/embed", Description: "v2 embed response, decoded by the embed translator"},
			{Name: "rerank", Op: "POST /v2/rerank", Description: "v2 rerank response, decoded by translateRerankResponse (billed_units drive rerank cost)"},
			{Name: "models", Op: "GET /v1/models", Description: "list-models response, decoded by contractwatch's CohereLister (the adapter's ListModels returns nil)"},
		},
	},
	{
		Provider: "azure", Format: FormatOpenAPI,
		Repo: "Azure/azure-rest-api-specs", Path: "specification/cognitiveservices/data-plane/OpenAIInference/stable/2024-10-21/inference.json",
		License: "MIT",
		Roots: []RootDef{
			// The chat operation's 200 is oneOf[response, stream response]
			// under application/json; root at each schema instead.
			{Name: "chat", Schema: "#/components/schemas/createChatCompletionResponse", Description: descChat + " (api-version 2024-10-21, the adapter's default)"},
			{Name: "chat_stream", Schema: "#/components/schemas/createChatCompletionStreamResponse", Prefix: "chunk", Description: descChatStream},
			{Name: "errors", Op: "POST /deployments/{deployment-id}/chat/completions", Status: "errors", Description: descChatErrors},
			{Name: "embeddings", Op: "POST /deployments/{deployment-id}/embeddings", Description: descEmbeddings},
			{Name: "images", Op: "POST /deployments/{deployment-id}/images/generations", Description: "image generation response, decoded by CreateImage"},
			{Name: "transcription", Op: "POST /deployments/{deployment-id}/audio/transcriptions", Description: descTranscribe},
		},
	},
	{
		Provider: "groq", Format: FormatOpenAPI,
		Repo: "groq/groq-python", Path: "scripts/mock", Decode: DecodeEmbeddedMock,
		License: "spec embedded in an Apache-2.0 SDK; termsOfService, no licence of its own",
		Roots: []RootDef{
			{Name: "chat", Op: "POST /openai/v1/chat/completions", Description: descChat + "; " + descOpenAICompat},
			// Defined, but attached to no operation.
			{Name: "chat_stream", Schema: "#/components/schemas/CreateChatCompletionStreamResponse", Prefix: "chunk", Description: descChatStream + "; " + descOpenAICompat},
			{Name: "embeddings", Op: "POST /openai/v1/embeddings", Description: descEmbeddings + "; " + descOpenAICompat},
			{Name: "transcription", Op: "POST /openai/v1/audio/transcriptions", Description: descTranscribe + "; " + descOpenAICompat},
			{Name: "models", Op: "GET /openai/v1/models", Description: descModels + "; " + descOpenAICompat},
		},
	},
	{
		Provider: "together", Format: FormatOpenAPI,
		Repo: "togethercomputer/together-py", Path: "scripts/mock", Decode: DecodeEmbeddedMock,
		License: "MIT (info.license)",
		Roots: []RootDef{
			{Name: "chat", Op: "POST /v1/chat/completions", Description: descChat + "; " + descOpenAICompat},
			// Each event is {data: chunk} or {data: "[DONE]"}: the SSE data
			// line, modeled as an envelope. Rebased so paths match the
			// other chunk roots.
			{Name: "chat_stream", Op: "POST /v1/chat/completions", Content: "text/event-stream", Rebase: "$.data", Prefix: "chunk", Description: descChatStream + "; " + descOpenAICompat},
			{Name: "errors", Op: "POST /v1/chat/completions", Status: "errors", Description: descChatErrors + "; " + descOpenAICompat},
			{Name: "embeddings", Op: "POST /v1/embeddings", Description: descEmbeddings + "; " + descOpenAICompat},
			{Name: "images", Op: "POST /v1/images/generations", Description: "image generation response; " + descOpenAICompat},
		},
	},
	{
		Provider: "openrouter", Format: FormatOpenAPI,
		Repo: "OpenRouterTeam/python-sdk", Path: ".speakeasy/in.openapi.yaml",
		License: "MIT (info.license)",
		Roots: []RootDef{
			{Name: "chat", Op: "POST /chat/completions", Description: descChat + "; " + descOpenAICompat},
			{Name: "chat_stream", Op: "POST /chat/completions", Content: "text/event-stream", Rebase: "$.data", Prefix: "chunk", Description: descChatStream + " (the spec wraps it in {data: ...}, rebased away); " + descOpenAICompat},
			{Name: "errors", Op: "POST /chat/completions", Status: "errors", Description: descChatErrors + "; " + descOpenAICompat},
			{Name: "embeddings", Op: "POST /embeddings", Description: descEmbeddings + "; " + descOpenAICompat},
			{Name: "models", Op: "GET /models", Description: descModels + "; " + descOpenAICompat},
		},
	},
	{
		// The spec models no error bodies (400 and 422 have no content), no
		// SSE framing and no Responses stream events, so xai has no errors
		// or responses_stream root.
		Provider: "xai", Format: FormatOpenAPI,
		Repo: "xai-org/xai-sdk-ts", Path: "spec/openapi.json",
		License: "spec in an Apache-2.0 SDK repository (LICENSE, package.json); info has no licence or terms of its own",
		Roots: []RootDef{
			{Name: "chat", Op: "POST /v1/chat/completions", Description: descChat + "; " + descOpenAICompat},
			// Defined, but attached to no operation.
			{Name: "chat_stream", Schema: "#/components/schemas/ChatResponseChunk", Prefix: "chunk", Description: descChatStream + "; " + descOpenAICompat},
			{Name: "completions", Op: "POST /v1/completions", Description: "legacy completions response, decoded by TextCompletion into models.CompletionResponse; " + descOpenAICompat},
			{Name: "embeddings", Op: "POST /v1/embeddings", Description: descEmbeddings + "; " + descOpenAICompat},
			{Name: "images", Op: "POST /v1/images/generations", Description: "image generation response, decoded by CreateImage into models.ImageResponse; " + descOpenAICompat},
			{Name: "responses", Op: "POST /v1/responses", Prune: map[string]string{
				"$.instructions": echoed, "$.tools": echoed, "$.tool_choice": echoed, "$.text": echoed, "$.reasoning": echoed,
			}, Description: "Responses API response, translated to chat by responses_chat.go (toChat); the /v1/responses passthrough reads its usage; " + descOpenAICompat},
			{Name: "models", Op: "GET /v1/models", Description: descModels + "; " + descOpenAICompat},
		},
	},
}

// Sources returns the subset definitions, sorted by provider.
func Sources() []Source {
	out := append([]Source(nil), sources...)
	sort.Slice(out, func(i, j int) bool { return out[i].Provider < out[j].Provider })
	return out
}

// SourceFor returns provider's definition.
func SourceFor(provider string) (Source, bool) {
	for _, s := range sources {
		if s.Provider == provider {
			return s, true
		}
	}
	return Source{}, false
}

// Unfetchable is a provider the gateway serves whose official spec cannot be
// fetched, with the reason and, where one is published, a change signal: a
// value in a public file that changes when the provider's spec changes. A
// changed signal says "the spec moved, look", with no content to diff.
type Unfetchable struct {
	Provider string `json:"provider"`
	Reason   string `json:"reason"`
	// Signal is nil when nothing public changes with the spec.
	Signal *Signal `json:"signal,omitempty"`
}

// Signal locates a change signal: Key (a dotted YAML/JSON key) in the file
// at Path of the GitHub repository Repo, or the sha256 of the whole file
// when Key is empty.
type Signal struct {
	Repo string `json:"repo"`
	Path string `json:"path"`
	Key  string `json:"key,omitempty"`
	What string `json:"what"`
}

var unfetchable = []Unfetchable{
	{
		Provider: "perplexity",
		Reason:   "the spec is published only at a Stainless bucket URL (perplexity-py .stats.yml openapi_spec_url), which answers 403 AccessDenied to anonymous callers; docs.perplexity.ai is not reachable from CI",
		Signal:   &Signal{Repo: "perplexityai/perplexity-py", Path: ".stats.yml", Key: "openapi_spec_hash", What: "Stainless openapi_spec_hash of the spec the Python SDK was generated from"},
	},
	{
		Provider: "fireworks",
		Reason:   "the spec is published only at a Stainless bucket URL (403 AccessDenied to anonymous callers); the SDK embeds no copy",
		Signal:   &Signal{Repo: "fw-ai-external/python-sdk", Path: ".stats.yml", Key: "openapi_spec_hash", What: "Stainless openapi_spec_hash of the spec the Python SDK was generated from"},
	},
	{
		Provider: "cerebras",
		Reason:   "the spec is published only at a Stainless bucket URL (403 AccessDenied to anonymous callers); the SDK embeds no copy",
		Signal:   &Signal{Repo: "Cerebras/cerebras-cloud-sdk-python", Path: ".stats.yml", Key: "openapi_spec_hash", What: "Stainless openapi_spec_hash of the spec the Python SDK was generated from"},
	},
	{
		Provider: "mistral",
		Reason:   "the spec is published in the Speakeasy registry (registry.speakeasyapi.dev, which needs auth) and on docs.mistral.ai; neither is fetchable anonymously from CI",
		Signal:   &Signal{Repo: "mistralai/client-python", Path: ".speakeasy/workflow.lock", Key: "sources.mistral-openapi.sourceBlobDigest", What: "Speakeasy sourceBlobDigest of the spec the Python SDK was generated from"},
	},
	{
		Provider: "deepseek",
		Reason:   "no official machine-readable spec is published (api-docs.deepseek.com is HTML; no public spec repository)",
	},
	{
		Provider: "deepinfra",
		Reason:   "no official machine-readable spec found; deepinfra/deepinfra-python is a sandboxes SDK without one",
	},
}

// UnfetchableProviders returns the providers without a fetchable spec.
func UnfetchableProviders() []Unfetchable {
	out := append([]Unfetchable(nil), unfetchable...)
	sort.Slice(out, func(i, j int) bool { return out[i].Provider < out[j].Provider })
	return out
}
