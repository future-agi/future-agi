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
	// Supported handlers now use receipt-installing wrappers. Keep the final
	// delegation pinned: accepting a wrapper name alone would hide an omission.
	wrappers, err := os.ReadFile("handlers_inspection.go")
	if err != nil {
		t.Fatal(err)
	}
	for _, call := range []string{"return h.processInspection(", "return h.engine.Process("} {
		if !strings.Contains(string(wrappers), call) {
			t.Fatalf("inspection wrappers no longer delegate through %s", call)
		}
	}
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
		"handlers_decision.go":   {"SystemOne", "Decisions"},
	}

	for file, handlers := range billable {
		src, err := os.ReadFile(file)
		if err != nil {
			t.Fatalf("read %s: %v", file, err)
		}
		if pipelineCallIndex(string(src)) < 0 {
			t.Errorf("%s never calls the pipeline or its inspection wrapper — %s bypasses cost, budget, "+
				"credits, logging and tracing", file, strings.Join(handlers, "/"))
		}
	}
}

// pipelineCallIndex recognizes only the two handler wrappers whose delegation
// is pinned above, plus a direct pipeline call. It returns the earliest call so
// the caller-extras ordering assertion remains meaningful with mixed paths.
func pipelineCallIndex(source string) int {
	first := -1
	for _, call := range []string{"h.engine.Process(", "h.processTextInspection(", "h.processInputInspection("} {
		if index := strings.Index(source, call); index >= 0 && (first < 0 || index < first) {
			first = index
		}
	}
	return first
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
