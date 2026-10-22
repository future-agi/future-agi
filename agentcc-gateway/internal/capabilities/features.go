package capabilities

import "github.com/futureagi/agentcc-gateway/internal/modeldb"

// Features are what a request on an endpoint can use through one provider.
type Features struct {
	Stream        bool `json:"stream"`
	Tools         bool `json:"tools"`
	JSONSchema    bool `json:"json_schema"`
	Vision        bool `json:"vision"`
	AudioInput    bool `json:"audio_input"`
	Reasoning     bool `json:"reasoning"`
	PromptCaching bool `json:"prompt_caching"`
}

// and keeps only the features both sides have.
func (f Features) and(o Features) Features {
	return Features{
		Stream: f.Stream && o.Stream, Tools: f.Tools && o.Tools, JSONSchema: f.JSONSchema && o.JSONSchema,
		Vision: f.Vision && o.Vision, AudioInput: f.AudioInput && o.AudioInput,
		Reasoning: f.Reasoning && o.Reasoning, PromptCaching: f.PromptCaching && o.PromptCaching,
	}
}

var (
	allFeatures = Features{Stream: true, Tools: true, JSONSchema: true, Vision: true, AudioInput: true, Reasoning: true, PromptCaching: true}
	streamOnly  = Features{Stream: true}
)

// chatFeatures is what each adapter carries on chat completions, read from
// its request translation:
//   - openai, azure: the canonical request is marshalled as sent, Extra
//     fields included, so every feature reaches the upstream.
//   - anthropic: tools, output_config.format and image blocks are built;
//     input_audio is dropped with a warning; no thinking config and no
//     cache_control are sent.
//   - gemini: tools, responseSchema, inline image and audio data, and
//     thinkingConfig from reasoning_effort; no explicit cache control.
//   - bedrock: tools, an output_config JSON schema and images, on both the
//     invoke and the Converse paths; input_audio is dropped; no reasoning
//     or cache points.
//   - cohere: tools only; json_schema degrades to json_object, and images
//     and audio are not translated.
var chatFeatures = map[string]Features{
	"openai":    allFeatures,
	"azure":     allFeatures,
	"anthropic": {Stream: true, Tools: true, JSONSchema: true, Vision: true},
	"gemini":    {Stream: true, Tools: true, JSONSchema: true, Vision: true, AudioInput: true, Reasoning: true},
	"bedrock":   {Stream: true, Tools: true, JSONSchema: true, Vision: true},
	"cohere":    {Stream: true, Tools: true},
}

// Native passthroughs forward the client's own body, so the upstream API's
// features all apply: Responses has no audio input, and Messages none either.
var (
	responsesFeatures = Features{Stream: true, Tools: true, JSONSchema: true, Vision: true, Reasoning: true, PromptCaching: true}
	messagesNative    = Features{Stream: true, Tools: true, JSONSchema: true, Vision: true, Reasoning: true, PromptCaching: true}
	genAINative       = allFeatures
	// The inbound translators keep tools and images; Anthropic's thinking and
	// output_config and Gemini's cachedContent and thinkingConfig are dropped.
	messagesTranslated = Features{Stream: true, Tools: true, Vision: true}
	genAITranslated    = Features{Stream: true, Tools: true, JSONSchema: true, Vision: true}
)

// adapterChatFeatures is the chat row for apiFormat; an unknown adapter
// (an a2a agent) is only known to stream.
func adapterChatFeatures(apiFormat string) Features {
	if f, ok := chatFeatures[canonicalFormat(apiFormat)]; ok {
		return f
	}
	return streamOnly
}

// adapterFeatures is what the adapter carries on spec's endpoint.
func adapterFeatures(spec endpointSpec, p Provider) Features {
	chat := adapterChatFeatures(p.APIFormat)
	native := p.Interfaces[spec.native]
	switch spec.kind {
	case kindChat:
		return chat
	case kindDeferred:
		chat.Stream = false
		return chat
	case kindResponses:
		return responsesFeatures
	case kindMessages:
		if native {
			return messagesNative
		}
		return chat.and(messagesTranslated)
	case kindGenAI:
		if native {
			return genAINative
		}
		return chat.and(genAITranslated)
	case kindStreamOnly:
		return streamOnly
	default:
		return Features{}
	}
}

// generates reports whether spec's features depend on the model.
func (s endpointSpec) generates() bool {
	switch s.kind {
	case kindChat, kindDeferred, kindResponses, kindMessages, kindGenAI:
		return true
	}
	return false
}

// modelFeatures is what one model supports, as the model database records
// it. The stream flag is the adapter's, so it is left set.
func modelFeatures(info *modeldb.ModelInfo) Features {
	c := info.Capabilities
	return Features{
		Stream: true, Tools: c.FunctionCalling, JSONSchema: c.ResponseSchema, Vision: c.Vision,
		AudioInput: c.AudioInput, Reasoning: c.Reasoning, PromptCaching: c.PromptCaching,
	}
}

// anyModelFeatures is the union of models' features: a feature at least one
// known model supports.
func anyModelFeatures(models []*modeldb.ModelInfo) Features {
	var u Features
	for _, m := range models {
		f := modelFeatures(m)
		u = Features{
			Stream: true, Tools: u.Tools || f.Tools, JSONSchema: u.JSONSchema || f.JSONSchema,
			Vision: u.Vision || f.Vision, AudioInput: u.AudioInput || f.AudioInput,
			Reasoning: u.Reasoning || f.Reasoning, PromptCaching: u.PromptCaching || f.PromptCaching,
		}
	}
	return u
}
