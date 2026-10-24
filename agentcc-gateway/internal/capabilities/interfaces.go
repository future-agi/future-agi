package capabilities

import (
	"context"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Interface names one adapter interface the gateway's handlers type-assert
// before serving an endpoint.
type Interface string

// The adapter interfaces, one per handler type assertion.
const (
	IfaceChat           Interface = "chat"            // providers.Provider: chat, models
	IfaceTextCompletion Interface = "text_completion" // native /v1/completions passthrough
	IfaceEmbeddings     Interface = "embeddings"
	IfaceImages         Interface = "images"
	IfaceSpeech         Interface = "speech"
	IfaceTranscription  Interface = "transcription"
	IfaceRerank         Interface = "rerank"
	IfaceOCR            Interface = "ocr"
	IfaceSearch         Interface = "search"
	IfaceResponses      Interface = "responses"
	IfaceAnthropic      Interface = "anthropic_native"
	IfaceGenAI          Interface = "genai_native"
	IfaceAssistants     Interface = "assistants"
	IfaceVectorStores   Interface = "vector_stores"
	IfaceVideo          Interface = "video"
	IfaceRealtime       Interface = "realtime"
)

// InterfaceSet is the set of interfaces one adapter implements.
type InterfaceSet map[Interface]bool

// textCompletionProvider is the native legacy-completions passthrough the
// completions handler asserts (server.nativeTextCompletionProvider).
type textCompletionProvider interface {
	TextCompletion(ctx context.Context, req *models.CompletionRequest) (*models.CompletionResponse, error)
}

// InterfacesOf type-asserts adapter against every interface a handler checks.
func InterfacesOf(adapter any) InterfaceSet {
	set := InterfaceSet{}
	add := func(i Interface, ok bool) {
		if ok {
			set[i] = true
		}
	}
	_, ok := adapter.(providers.Provider)
	add(IfaceChat, ok)
	_, ok = adapter.(textCompletionProvider)
	add(IfaceTextCompletion, ok)
	_, ok = adapter.(providers.EmbeddingProvider)
	add(IfaceEmbeddings, ok)
	_, ok = adapter.(providers.ImageProvider)
	add(IfaceImages, ok)
	_, ok = adapter.(providers.SpeechProvider)
	add(IfaceSpeech, ok)
	_, ok = adapter.(providers.TranscriptionProvider)
	add(IfaceTranscription, ok)
	_, ok = adapter.(providers.RerankProvider)
	add(IfaceRerank, ok)
	_, ok = adapter.(providers.OCRProvider)
	add(IfaceOCR, ok)
	_, ok = adapter.(providers.SearchProvider)
	add(IfaceSearch, ok)
	_, ok = adapter.(providers.ResponsesProvider)
	add(IfaceResponses, ok)
	_, ok = adapter.(providers.AnthropicNativeProvider)
	add(IfaceAnthropic, ok)
	_, ok = adapter.(providers.GenAINativeProvider)
	add(IfaceGenAI, ok)
	_, ok = adapter.(providers.AssistantsProvider)
	add(IfaceAssistants, ok)
	_, ok = adapter.(providers.VectorStoresProvider)
	add(IfaceVectorStores, ok)
	_, ok = adapter.(providers.VideoProvider)
	add(IfaceVideo, ok)
	_, ok = adapter.(providers.RealtimeProvider)
	add(IfaceRealtime, ok)
	return set
}

// formatInterfaces is what each built-in api_format's adapter implements, for
// a provider the gateway has not built (an org's own provider, which a read
// never builds). TestFormatInterfacesMatchAdapters
// proves each row equals InterfacesOf on the real adapter.
var formatInterfaces = map[string]InterfaceSet{
	"openai": {IfaceChat: true, IfaceTextCompletion: true, IfaceEmbeddings: true, IfaceImages: true,
		IfaceSpeech: true, IfaceTranscription: true, IfaceOCR: true, IfaceResponses: true,
		IfaceAssistants: true, IfaceVectorStores: true},
	"anthropic": {IfaceChat: true, IfaceAnthropic: true},
	"gemini":    {IfaceChat: true, IfaceEmbeddings: true, IfaceImages: true, IfaceSpeech: true, IfaceGenAI: true},
	"bedrock":   {IfaceChat: true},
	"cohere":    {IfaceChat: true, IfaceEmbeddings: true, IfaceRerank: true},
	"azure":     {IfaceChat: true, IfaceEmbeddings: true, IfaceImages: true, IfaceSpeech: true, IfaceTranscription: true},
}

// FormatInterfaces returns the interfaces an api_format's adapter
// implements; an unknown format implements only chat.
func FormatInterfaces(apiFormat string) InterfaceSet {
	if set, ok := formatInterfaces[canonicalFormat(apiFormat)]; ok {
		return set
	}
	return InterfaceSet{IfaceChat: true}
}

// canonicalFormat folds the registry's api_format aliases ("google" is
// gemini's).
func canonicalFormat(apiFormat string) string {
	if apiFormat == "google" {
		return "gemini"
	}
	return apiFormat
}
