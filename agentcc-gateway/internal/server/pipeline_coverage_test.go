package server

import (
	"os"
	"strings"
	"testing"
)

// An endpoint that skips the pipeline is billable traffic that is invisible and
// unmetered — cost, budget, credits, logging and tracing all live in plugins.
//
// Asserted against the source because the failure is an omission, not a
// behaviour: /v1/messages and generateContent shipped without calling h.engine,
// worked perfectly, and were free and untraced for as long as they existed.
// Handlers with no provider call behind them are not listed.
func TestBillableHandlersRunThePipeline(t *testing.T) {
	billable := map[string][]string{
		"handlers.go":            {"ChatCompletion"},
		"handlers_completion.go": {"TextCompletion"},
		"handlers_embedding.go":  {"CreateEmbedding"},
		"handlers_image.go":      {"CreateImage"},
		"handlers_audio.go":      {"CreateSpeech", "CreateTranscription", "CreateTranslation"},
		"handlers_rerank.go":     {"Rerank"},
		"handlers_search.go":     {"Search"},
		"handlers_ocr.go":        {"OCR"},
		"handlers_responses.go":  {"CreateResponse"},
		"handlers_anthropic.go":  {"AnthropicMessages"},
		"handlers_genai.go":      {"GenAIHandler"},
	}

	for file, handlers := range billable {
		src, err := os.ReadFile(file)
		if err != nil {
			t.Fatalf("read %s: %v", file, err)
		}
		if !strings.Contains(string(src), "h.engine.Process(") {
			t.Errorf("%s never calls h.engine.Process — %s bypasses cost, budget, "+
				"credits, logging and tracing", file, strings.Join(handlers, "/"))
		}
	}
}

// Process skips post-plugins for streams, since usage arrives only at close, so
// a streaming handler must call RunPostPlugins itself or never be costed.
func TestStreamingHandlersRunPostPlugins(t *testing.T) {
	for _, file := range []string{
		"handlers.go",
		"handlers_completion.go",
		"handlers_responses.go",
		"handlers_anthropic.go",
		"handlers_genai.go",
	} {
		src, err := os.ReadFile(file)
		if err != nil {
			t.Fatalf("read %s: %v", file, err)
		}
		if !strings.Contains(string(src), "RunPostPlugins(") {
			t.Errorf("%s streams but never calls RunPostPlugins — streamed "+
				"requests there are never costed or logged", file)
		}
	}
}
