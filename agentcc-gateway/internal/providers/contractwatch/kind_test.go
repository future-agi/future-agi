package contractwatch

import (
	"strings"
	"testing"
)

func TestModelKind(t *testing.T) {
	for _, tc := range []struct {
		id   string
		meta *Model
		want string
	}{
		{"gpt-6-sol", nil, KindChat},
		{"text-embedding-3-large", nil, KindEmbeddings},
		{"embed-v5.0-fast", nil, KindEmbeddings},
		{"gpt-4o-mini-tts-2025-12-15", nil, KindSpeech},
		{"gemini-3.8-flash-tts", nil, KindSpeech},
		{"whisper-1", nil, KindTranscription},
		{"gpt-4o-transcribe-diarize", nil, KindTranscription},
		{"dall-e-3", nil, KindImages},
		{"gpt-image-2-2026-04-21", nil, KindImages},
		{"imagen-4.0-generate-001", nil, KindImages},
		{"rerank-v5.0-pro", nil, KindRerank},
		{"mistral-ocr-latest", nil, KindOCR},
		{"meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", nil, KindChat},
		{"accounts/fireworks/models/kimi-k2-instruct-0905", nil, KindChat},
		// Metadata outranks the ID.
		{"gemini-vector-1", &Model{Capabilities: []string{"embedContent", "countTextTokens"}}, KindEmbeddings},
		{"command-x", &Model{Capabilities: []string{"endpoint:rerank"}}, KindRerank},
		{"command-y", &Model{Capabilities: []string{"endpoint:embed"}}, KindEmbeddings},
		{"pixtral-doc", &Model{Capabilities: []string{"ocr"}}, KindOCR},
		{"voxtral-mini-2607", &Model{Capabilities: []string{"audio", "audio_transcription", "type:base"}}, KindTranscription},
		{"voxtral-voice-1", &Model{Capabilities: []string{"audio", "audio_speech", "type:base"}}, KindSpeech},
		{"voxtral-small-2607", &Model{Capabilities: []string{"audio", "audio_transcription", "completion_chat"}}, KindChat},
		{"gemini-4-argon", &Model{Capabilities: []string{"generateContent", "embedContent"}}, KindChat},
	} {
		if got := ModelKind(tc.id, tc.meta); got != tc.want {
			t.Errorf("ModelKind(%q) = %s, want %s", tc.id, got, tc.want)
		}
	}
}

// TestFixtureKindsOnTheRepo reads kinds from the committed fixtures: a
// model's kind is what its cases' endpoints exercise, whatever its ID says.
func TestFixtureKindsOnTheRepo(t *testing.T) {
	for _, tc := range []struct{ provider, key, want string }{
		{"openai", "gpt-6-sol", "chat"},
		{"openai", "gpt-3.5-turbo-instruct", "completions"},
		{"openai", "whisper-1", "transcription"},
		{"openai", "dall-e-3", "images"},
		{"openai", "mistral@mistral-ocr-latest", "ocr"},
		{"gemini", "gemini-embedding-2", "embeddings"},
		{"gemini", "gemini-3.8-flash-tts", "speech"},
		{"cohere", "rerank-v5.0-pro", "rerank"},
		{"anthropic", "claude-opus-5-5", "chat"},
	} {
		if got := strings.Join(FixtureKinds("..", tc.provider, tc.key), ","); got != tc.want {
			t.Errorf("%s/%s: kinds %q, want %q", tc.provider, tc.key, got, tc.want)
		}
	}
	if k := FixtureKinds("..", "openai", "no-such-model"); k != nil {
		t.Errorf("a missing model has no kinds, got %v", k)
	}
	for ep := range endpointKinds {
		if ep != "" && EndpointKind(ep) == "" {
			t.Errorf("endpoint %q has no kind", ep)
		}
	}
}

// TestSelectTemplateSameKind: a template's cases drive one endpoint class,
// so only covered models of the new model's kind are candidates, whatever
// their names share.
func TestSelectTemplateSameKind(t *testing.T) {
	covered := []Candidate{
		{ID: "gpt-6-sol", Kinds: []string{KindChat}},
		{ID: "gpt-5.5-2026-04-23", Kinds: []string{KindChat}},
		{ID: "gpt-4o-mini-transcribe-2025-12-15", Kinds: []string{KindTranscription}},
		{ID: "gpt-4o-mini-tts-2025-12-15", Kinds: []string{KindSpeech}},
		{ID: "dall-e-3", Kinds: []string{KindImages}},
		{ID: "gpt-image-2-2026-04-21", Kinds: []string{KindImages}},
		{ID: "gpt-3.5-turbo-instruct", Kinds: []string{KindCompletions}},
		{ID: "text-embedding-3-small", Kinds: []string{KindEmbeddings}},
	}
	for _, tc := range []struct{ id, want string }{
		{"gpt-5-mini", "gpt-5.5-2026-04-23"},          // shares "mini" with the transcription model only
		{"o3", "gpt-5.5-2026-04-23"},                  // shares version 3 with dall-e-3 only
		{"gpt-4-turbo-preview", "gpt-5.5-2026-04-23"}, // shares "turbo" with the completions model only
		{"gpt-4o-transcribe", "gpt-4o-mini-transcribe-2025-12-15"},
		{"gpt-image-2.5-flare", "gpt-image-2-2026-04-21"},
		{"text-embedding-4", "text-embedding-3-small"},
	} {
		ch, ok := SelectTemplate("openai", tc.id, nil, covered)
		if !ok || ch.Template != tc.want {
			t.Errorf("%s: template %q (ok %v), want %s", tc.id, ch.Template, ok, tc.want)
		}
	}
	if ch, ok := SelectTemplate("openai", "gpt-realtime-tts-x", nil, covered[:2]); ok {
		t.Errorf("a speech model with only chat templates must have none, got %s", ch.Template)
	}
}
