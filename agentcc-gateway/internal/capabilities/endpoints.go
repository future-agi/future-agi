package capabilities

// Launch tiers (capabilities-v1 launch_tier).
const (
	TierLaunch            = "launch"
	TierProviderDependent = "provider_dependent"
	TierBeta              = "beta"
	// TierUnsupported marks an endpoint no provider in the view serves.
	TierUnsupported = "unsupported"
)

// Endpoint ids are the capability names. cost.Priced takes them as
// given ("chat.completions", "audio.speech").
const (
	EndpointChat           = "chat.completions"
	EndpointResponses      = "responses"
	EndpointMessages       = "messages"
	EndpointEmbeddings     = "embeddings"
	EndpointModels         = "models"
	EndpointCompletions    = "completions"
	EndpointImages         = "images"
	EndpointSpeech         = "audio.speech"
	EndpointTranscriptions = "audio.transcriptions"
	EndpointRerank         = "rerank"
	EndpointOCR            = "ocr"
	EndpointSearch         = "search"
	EndpointGenAI          = "genai-native"
	EndpointBatch          = "batch"
	EndpointAssistants     = "assistants"
	EndpointThreads        = "threads"
	EndpointVectorStores   = "vector_stores"
	EndpointFiles          = "files"
	EndpointVideos         = "videos"
	EndpointRealtime       = "realtime"
	EndpointScheduled      = "scheduled"
	EndpointAsync          = "async"
)

// gate is a gateway-level condition on an endpoint, independent of the
// provider's adapter.
type gate int

const (
	gateNone         gate = iota
	gateHosted            // the gateway serves it itself, for every provider (files)
	gateAdmin             // admin-token route (/-/batches): no virtual key reaches it
	gateGlobal            // runs on the global registry: refused to org-only keys (async)
	gateScheduled         // gateGlobal, and only when routing.scheduled is enabled
	gateUnconfigured      // no provider is configured for it in this build (videos)
)

// kind says which feature table an endpoint's cells read.
type kind int

const (
	kindNone       kind = iota // no generation features
	kindChat                   // the adapter's chat features
	kindDeferred               // chat features, not streamed (batch, async, scheduled)
	kindResponses              // OpenAI Responses passthrough
	kindMessages               // Anthropic Messages: native, or translated to chat
	kindGenAI                  // Google GenAI: native, or translated to chat
	kindStreamOnly             // streams, and carries no generation features
)

// endpointSpec is one published endpoint and how its support is decided.
type endpointSpec struct {
	id   string
	tier string
	// native is the adapter interface whose handler serves the endpoint.
	native Interface
	// translated: providers without native are served by translating to chat.
	translated bool
	// openAIUpstreams are the upstreams known to serve the endpoint behind the
	// generic OpenAI-compatible adapter; nil means every OpenAI-compatible
	// upstream does. Any other upstream is provider_dependent.
	openAIUpstreams []string
	gate            gate
	kind            kind
}

var openAIOnly = []string{"openai"}

// endpointSpecs is the published endpoint set, launch tier first.
var endpointSpecs = []endpointSpec{
	{id: EndpointChat, tier: TierLaunch, native: IfaceChat, kind: kindChat},
	{id: EndpointResponses, tier: TierLaunch, native: IfaceResponses, openAIUpstreams: openAIOnly, kind: kindResponses},
	{id: EndpointMessages, tier: TierLaunch, native: IfaceAnthropic, translated: true, kind: kindMessages},
	{id: EndpointEmbeddings, tier: TierLaunch, native: IfaceEmbeddings, openAIUpstreams: openAIOnly},
	{id: EndpointModels, tier: TierLaunch, native: IfaceChat},

	{id: EndpointCompletions, tier: TierProviderDependent, native: IfaceTextCompletion, translated: true, openAIUpstreams: openAIOnly, kind: kindStreamOnly},
	{id: EndpointImages, tier: TierProviderDependent, native: IfaceImages, openAIUpstreams: openAIOnly},
	{id: EndpointSpeech, tier: TierProviderDependent, native: IfaceSpeech, openAIUpstreams: openAIOnly, kind: kindStreamOnly},
	{id: EndpointTranscriptions, tier: TierProviderDependent, native: IfaceTranscription, openAIUpstreams: openAIOnly},
	{id: EndpointRerank, tier: TierProviderDependent, native: IfaceRerank},
	{id: EndpointOCR, tier: TierProviderDependent, native: IfaceOCR, openAIUpstreams: []string{"mistral"}},
	{id: EndpointSearch, tier: TierProviderDependent, native: IfaceSearch},
	{id: EndpointGenAI, tier: TierProviderDependent, native: IfaceGenAI, translated: true, kind: kindGenAI},
	{id: EndpointBatch, tier: TierProviderDependent, native: IfaceChat, gate: gateAdmin, kind: kindDeferred},

	{id: EndpointAssistants, tier: TierBeta, native: IfaceAssistants, openAIUpstreams: openAIOnly},
	{id: EndpointThreads, tier: TierBeta, native: IfaceAssistants, openAIUpstreams: openAIOnly, kind: kindStreamOnly},
	{id: EndpointVectorStores, tier: TierBeta, native: IfaceVectorStores, openAIUpstreams: openAIOnly},
	{id: EndpointFiles, tier: TierBeta, gate: gateHosted},
	{id: EndpointVideos, tier: TierBeta, native: IfaceVideo, gate: gateUnconfigured},
	{id: EndpointRealtime, tier: TierBeta, native: IfaceRealtime},
	{id: EndpointScheduled, tier: TierBeta, native: IfaceChat, gate: gateScheduled, kind: kindDeferred},
	{id: EndpointAsync, tier: TierBeta, native: IfaceChat, gate: gateGlobal, kind: kindDeferred},
}

// LaunchEndpoints are the endpoints of the launch tier, in published order.
func LaunchEndpoints() []string {
	var out []string
	for _, s := range endpointSpecs {
		if s.tier == TierLaunch {
			out = append(out, s.id)
		}
	}
	return out
}

func specFor(endpoint string) (endpointSpec, bool) {
	for _, s := range endpointSpecs {
		if s.id == endpoint {
			return s, true
		}
	}
	return endpointSpec{}, false
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}
