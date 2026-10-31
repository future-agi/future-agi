package contractwatch

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Kinds of model: the endpoint class a model serves. A template's cases
// drive one endpoint class, so a new model can only be cloned from a covered
// model of its own kind: chat cases sent to a transcription model, or image
// cases to a chat model, record nothing useful.
const (
	KindChat          = "chat"
	KindCompletions   = "completions"
	KindEmbeddings    = "embeddings"
	KindImages        = "images"
	KindSpeech        = "speech"
	KindTranscription = "transcription"
	KindRerank        = "rerank"
	KindOCR           = "ocr"
)

// kindWords name a model's kind in its ID (text-embedding-3-small,
// gpt-4o-mini-tts, whisper-1, dall-e-3, imagen-4.0-generate-001,
// rerank-v5.0-pro, mistral-ocr-latest). An ID with none is a chat model.
// Legacy completions models carry no such word: their kind comes from their
// fixtures (FixtureKinds) or an ignore rule.
var kindWords = map[string]string{
	"embed": KindEmbeddings, "embedding": KindEmbeddings,
	"tts":        KindSpeech,
	"transcribe": KindTranscription, "whisper": KindTranscription,
	"image": KindImages, "imagen": KindImages, "dall": KindImages,
	"rerank": KindRerank,
	"ocr":    KindOCR,
}

// endpointKinds maps a case.json endpoint (contracttest.Endpoints) to the
// kind of model it exercises. Endpoints any chat model serves (its native
// passthroughs, token counting, the model list, the Responses and Assistants
// passthroughs) are chat.
var endpointKinds = map[string]string{
	"":                       KindChat,
	"chat":                   KindChat,
	"chat_via_responses":     KindChat,
	"responses":              KindChat,
	"assistants":             KindChat,
	"vector_stores":          KindChat,
	"anthropic_messages":     KindChat,
	"anthropic_count_tokens": KindChat,
	"genai_generate":         KindChat,
	"genai_count_tokens":     KindChat,
	"list_models":            KindChat,
	"completions":            KindCompletions,
	"embeddings":             KindEmbeddings,
	"genai_embed":            KindEmbeddings,
	"images":                 KindImages,
	"speech":                 KindSpeech,
	"transcription":          KindTranscription,
	"translation":            KindTranscription,
	"rerank":                 KindRerank,
	"ocr":                    KindOCR,
}

// EndpointKind is the kind of model a case.json endpoint exercises, or ""
// for an endpoint this package does not know.
func EndpointKind(endpoint string) string { return endpointKinds[endpoint] }

// idKind is the kind an ID names: the first kind word among its tokens, or
// chat.
func idKind(id string) string {
	s := strings.ToLower(id)
	if i := strings.LastIndex(s, "/"); i >= 0 {
		s = s[i+1:]
	}
	for _, tok := range strings.FieldsFunc(s, func(r rune) bool {
		return r == '-' || r == '_' || r == ':' || r == '@' || r == '.' || r == '/'
	}) {
		word := strings.TrimRightFunc(tok, func(r rune) bool { return r >= '0' && r <= '9' })
		if k, ok := kindWords[word]; ok {
			return k
		}
	}
	return KindChat
}

// ModelKind is the kind of a listed model: what the provider's metadata
// says when it says (Gemini supportedGenerationMethods, Cohere endpoints,
// Mistral capabilities), else what the ID names.
func ModelKind(id string, meta *Model) string {
	k := idKind(id)
	if meta == nil {
		return k
	}
	has := func(c string) bool { return contains(meta.Capabilities, c) }
	switch {
	// Gemini: an embedContent model with no generateContent.
	case has("embedContent") && !has("generateContent"):
		return KindEmbeddings
	// Cohere: the endpoints it serves.
	case has("endpoint:embed") && !has("endpoint:chat"):
		return KindEmbeddings
	case has("endpoint:rerank") && !has("endpoint:chat"):
		return KindRerank
	// Mistral: an OCR, transcription or speech model has no chat
	// completion (a voxtral chat model takes audio input, but chats).
	case has("ocr") && !has("completion_chat"):
		return KindOCR
	case has("audio_transcription") && !has("completion_chat"):
		return KindTranscription
	case has("audio_speech") && !has("completion_chat"):
		return KindSpeech
	}
	return k
}

// FixtureKinds lists the kinds a covered model's cases exercise (every case
// with a request.json, recordable or not), sorted; nil when its directory
// has none. case.json is read leniently, for its endpoint only.
func FixtureKinds(providersDir, fixtureProvider, key string) []string {
	dir := filepath.Join(providersDir, fixtureProvider, filepath.FromSlash(ContractsRoot), ModelDir(key))
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	seen := map[string]bool{}
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		if _, err := os.Stat(filepath.Join(dir, e.Name(), requestFile)); err != nil {
			continue
		}
		var spec struct {
			Endpoint string `json:"endpoint"`
		}
		if raw, err := os.ReadFile(filepath.Join(dir, e.Name(), caseFile)); err == nil {
			_ = json.Unmarshal(raw, &spec)
		}
		if k := EndpointKind(spec.Endpoint); k != "" {
			seen[k] = true
		}
	}
	var out []string
	for k := range seen {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// kindsOf is a candidate's kinds: its fixtures', else its ID's.
func (c Candidate) kindsOf() []string {
	if len(c.Kinds) > 0 {
		return c.Kinds
	}
	return []string{ModelKind(c.ID, c.Meta)}
}
