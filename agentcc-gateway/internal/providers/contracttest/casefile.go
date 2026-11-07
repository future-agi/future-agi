package contracttest

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"mime"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// CaseFile is a case directory's optional case.json: which provider method
// the case drives and how the replay server answers. A case without one is a
// 200 chat case whose Content-Type follows its response file.
const CaseFile = "case.json"

// Endpoints: the provider method a case drives, one per capability interface
// in internal/providers/provider.go that has an implementation (plus the
// server's nativeTextCompletionProvider). Canonical endpoints take a
// request.json holding the gateway's canonical request type; native
// passthrough endpoints take the provider's own request body.
const (
	// EndpointChat drives Provider.ChatCompletion / StreamChatCompletion;
	// request.json is a models.ChatCompletionRequest. The default.
	EndpointChat = "chat"
	// EndpointChatViaResponses drives ChatViaResponsesProvider
	// (ChatCompletionViaResponses / Stream...); request.json is a
	// models.ChatCompletionRequest, the response an OpenAI Responses body.
	EndpointChatViaResponses = "chat_via_responses"
	// EndpointCompletions drives TextCompletion (legacy /v1/completions);
	// request.json is a models.CompletionRequest.
	EndpointCompletions = "completions"
	// EndpointEmbeddings drives EmbeddingProvider.CreateEmbedding.
	EndpointEmbeddings = "embeddings"
	// EndpointImages drives ImageProvider.CreateImage.
	EndpointImages = "images"
	// EndpointSpeech drives SpeechProvider.CreateSpeech; the response is
	// usually response.bin (audio bytes) with a content_type.
	EndpointSpeech = "speech"
	// EndpointTranscription drives TranscriptionProvider.CreateTranscription.
	EndpointTranscription = "transcription"
	// EndpointTranslation drives TranslationProvider.CreateTranslation.
	EndpointTranslation = "translation"
	// EndpointRerank drives RerankProvider.Rerank.
	EndpointRerank = "rerank"
	// EndpointOCR drives OCRProvider.OCR.
	EndpointOCR = "ocr"
	// EndpointResponses drives ResponsesProvider.CreateResponse /
	// StreamResponse (passthrough); request.json is the raw Responses body.
	EndpointResponses = "responses"
	// EndpointAssistants drives AssistantsProvider (passthrough).
	EndpointAssistants = "assistants"
	// EndpointVectorStores drives VectorStoresProvider (passthrough).
	EndpointVectorStores = "vector_stores"
	// EndpointAnthropicMessages drives AnthropicNativeProvider
	// CreateAnthropicMessage / StreamAnthropicMessage; request.json is the raw
	// Messages body.
	EndpointAnthropicMessages = "anthropic_messages"
	// EndpointAnthropicCountTokens drives AnthropicNativeProvider.CountAnthropicTokens.
	EndpointAnthropicCountTokens = "anthropic_count_tokens"
	// EndpointGenAIGenerate drives GenAINativeProvider GenerateContent /
	// StreamGenerateContent; request.json is the raw generateContent body.
	EndpointGenAIGenerate = "genai_generate"
	// EndpointGenAICountTokens drives GenAINativeProvider.GenAICountTokens.
	EndpointGenAICountTokens = "genai_count_tokens"
	// EndpointGenAIEmbed drives GenAINativeProvider.EmbedContent.
	EndpointGenAIEmbed = "genai_embed"
	// EndpointListModels drives Provider.ListModels; request.json is {}.
	EndpointListModels = "list_models"
)

// Endpoints is every valid case.json endpoint. SearchProvider, VideoProvider,
// RealtimeProvider and SpeechRelayProvider are absent: nothing implements the
// first three, and the speech relay is a WebSocket byte relay. Add an
// endpoint when one is wired to an upstream.
var Endpoints = []string{
	EndpointChat, EndpointChatViaResponses, EndpointCompletions, EndpointEmbeddings, EndpointImages,
	EndpointSpeech, EndpointTranscription, EndpointTranslation, EndpointRerank, EndpointOCR,
	EndpointResponses, EndpointAssistants, EndpointVectorStores,
	EndpointAnthropicMessages, EndpointAnthropicCountTokens,
	EndpointGenAIGenerate, EndpointGenAICountTokens, EndpointGenAIEmbed,
	EndpointListModels,
}

// chatEndpoints decode request.json as a models.ChatCompletionRequest.
var chatEndpoints = map[string]bool{EndpointChat: true, EndpointChatViaResponses: true}

// CaseSpec is case.json. Every field is optional; unknown keys are an error,
// as in meta.json, so a typo cannot silently change what a case tests.
type CaseSpec struct {
	// Endpoint is one of Endpoints; default "chat".
	Endpoint string `json:"endpoint,omitempty"`
	// Status is the HTTP status the replay server answers with: 2xx, or
	// 400-599 for an error case. Default 200.
	Status int `json:"status,omitempty"`
	// Headers are extra response headers to replay, e.g. retry-after and
	// rate-limit headers on a 429. Content-Type goes in ContentType.
	Headers map[string]string `json:"headers,omitempty"`
	// ContentType overrides the one the response file's extension implies,
	// e.g. "audio/mpeg" for response.bin or "text/html" for an HTML 502.
	ContentType string `json:"content_type,omitempty"`
	// Recordable false marks a case no real request can induce (429, 5xx,
	// most mid-stream errors): it stays synthetic, the recorder skips it, and
	// live mode ignores any recording of it. Default true. Notes must say why.
	Recordable *bool `json:"recordable,omitempty"`
	// Notes is free text: where the body came from, how to induce it.
	Notes string `json:"notes,omitempty"`
	// SanityGaps are failures the case's sanity check (AssertSaneError, or
	// ExpectGaps) is known to report because of an adapter gap, each with
	// its reason. A gap that no longer occurs fails, as a stale allowlist
	// entry does.
	SanityGaps []SanityGap `json:"sanity_gaps,omitempty"`
}

// SanityGap is one expected sanity failure.
type SanityGap struct {
	// Failure is a substring of the failure message.
	Failure string `json:"failure"`
	Reason  string `json:"reason"`
}

// CanRecord reports whether a real request can induce the case: true unless
// case.json says "recordable": false.
func (s CaseSpec) CanRecord() bool { return s.Recordable == nil || *s.Recordable }

// ReadCaseSpec reads and validates dir/case.json. A directory without one
// yields the zero CaseSpec (a 200 chat case). The recorder uses it to skip
// cases it cannot record.
func ReadCaseSpec(dir string) (CaseSpec, error) {
	var s CaseSpec
	raw, err := os.ReadFile(filepath.Join(dir, CaseFile))
	if errors.Is(err, fs.ErrNotExist) {
		return s, nil
	}
	if err != nil {
		return s, err
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&s); err != nil {
		return s, fmt.Errorf("%s: %v", filepath.Join(dir, CaseFile), err)
	}
	if err := s.Validate(); err != nil {
		return s, fmt.Errorf("%s: %v", filepath.Join(dir, CaseFile), err)
	}
	return s, nil
}

// Validate checks a CaseSpec against the rules above.
func (s CaseSpec) Validate() error {
	if s.Endpoint != "" && !isEndpoint(s.Endpoint) {
		return fmt.Errorf("endpoint %q is not one of %q", s.Endpoint, Endpoints)
	}
	switch {
	case s.Status == 0:
	case s.Status >= 200 && s.Status <= 299:
	case s.Status >= 400 && s.Status <= 599:
	default:
		return fmt.Errorf("status %d: want 2xx, or 400-599 for an error case", s.Status)
	}
	names := make([]string, 0, len(s.Headers))
	for name := range s.Headers {
		names = append(names, name)
	}
	sort.Strings(names)
	seen := map[string]bool{}
	for _, name := range names {
		lower := strings.ToLower(name)
		switch {
		case !isToken(name):
			return fmt.Errorf("header %q is not a valid header name", name)
		case seen[lower]:
			return fmt.Errorf("header %q is listed twice", name)
		case lower == "content-type":
			return fmt.Errorf("set the content type with content_type, not headers")
		case lower == "content-length" || lower == "transfer-encoding" || lower == "connection":
			return fmt.Errorf("header %q is set by the replay server", name)
		case strings.ContainsAny(s.Headers[name], "\r\n"):
			return fmt.Errorf("header %q value has a line break", name)
		}
		seen[lower] = true
	}
	if s.ContentType != "" {
		if _, _, err := mime.ParseMediaType(s.ContentType); err != nil {
			return fmt.Errorf("content_type %q: %v", s.ContentType, err)
		}
	}
	if !s.CanRecord() && strings.TrimSpace(s.Notes) == "" {
		return fmt.Errorf(`"recordable": false needs notes saying why the case cannot be recorded`)
	}
	for _, g := range s.SanityGaps {
		if strings.TrimSpace(g.Failure) == "" || strings.TrimSpace(g.Reason) == "" {
			return fmt.Errorf("sanity_gaps entry %q needs a failure and a reason", g.Failure)
		}
	}
	return nil
}

func isEndpoint(e string) bool {
	for _, x := range Endpoints {
		if x == e {
			return true
		}
	}
	return false
}

// isToken reports whether s is an RFC 9110 token, the syntax of a header name.
func isToken(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if 'a' <= c && c <= 'z' || 'A' <= c && c <= 'Z' || '0' <= c && c <= '9' || strings.IndexByte("!#$%&'*+-.^_`|~", c) >= 0 {
			continue
		}
		return false
	}
	return true
}
