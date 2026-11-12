package gemini

import (
	"encoding/json"
	"testing"
)

// Gemini's JSON omits zero counters. A finished answer with no output reports
// promptTokenCount and an equal totalTokenCount only, which proves zero
// candidates. Each shape must come back as one answer (a prompt-level block
// included) whose usage measures both dimensions, streamed or not.
func TestZeroOutputAnswersAreMeasured(t *testing.T) {
	const usage = `"usageMetadata":{"promptTokenCount":10,"totalTokenCount":10}`
	for _, tc := range []struct {
		name, body, finish string
	}{
		{"safety-empty-content", `{"candidates":[{"content":{},"finishReason":"SAFETY"}],` + usage + `}`, "content_filter"},
		{"stop-empty-parts", `{"candidates":[{"content":{"role":"model","parts":[]},"finishReason":"STOP"}],` + usage + `}`, "stop"},
		{"prompt-blocked", `{"promptFeedback":{"blockReason":"SAFETY"},` + usage + `}`, "content_filter"},
		{"prompt-blocked-other", `{"promptFeedback":{"blockReason":"OTHER"},` + usage + `}`, "content_filter"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var resp geminiResponse
			if err := json.Unmarshal([]byte(tc.body), &resp); err != nil {
				t.Fatal(err)
			}
			result := translateResponse(&resp, "gemini-3-pro")
			if len(result.Choices) != 1 || result.Choices[0].FinishReason != tc.finish || result.Choices[0].Message.Role != "assistant" {
				t.Fatalf("choices = %+v, want one %s answer", result.Choices, tc.finish)
			}
			u := result.Usage
			if u == nil || !u.HasPromptTokens() || !u.HasCompletionTokens() || u.PromptTokens != 10 || u.CompletionTokens != 0 || u.NeedsTokenEstimate() {
				t.Errorf("non-streamed usage is not a measured zero output: %+v", u)
			}

			chunk, err := newStreamState("gemini-3-pro").parseStreamData(tc.body)
			if err != nil {
				t.Fatal(err)
			}
			if len(chunk.Choices) != 1 || chunk.Choices[0].FinishReason == nil || *chunk.Choices[0].FinishReason != tc.finish {
				t.Fatalf("stream choices = %+v, want one %s finish", chunk.Choices, tc.finish)
			}
			if u := chunk.Usage; u == nil || !u.HasPromptTokens() || !u.HasCompletionTokens() || u.PromptTokens != 10 || u.CompletionTokens != 0 {
				t.Errorf("terminal frame usage is not a measured zero output: %+v", u)
			}
		})
	}
}

// An interim frame's totalTokenCount can lag text it already carries, so it
// never proves the omitted candidates count; only a frame with a finishReason
// does. Thoughts stay a measured floor either way.
func TestStreamInterimFrameKeepsCandidatesUnmeasured(t *testing.T) {
	for _, tc := range []struct {
		name, usage string
		floor       int
	}{
		{"zero-output-total", `{"promptTokenCount":10,"totalTokenCount":10}`, 0},
		{"thoughts-total", `{"promptTokenCount":10,"totalTokenCount":510,"thoughtsTokenCount":500}`, 500},
		{"positive-remainder", `{"promptTokenCount":10,"totalTokenCount":15}`, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			chunk, err := newStreamState("gemini-3-pro").parseStreamData(`{"candidates":[{"content":{"role":"model","parts":[{"text":"hel"}]}}],"usageMetadata":` + tc.usage + `}`)
			if err != nil {
				t.Fatal(err)
			}
			u := chunk.Usage
			if u == nil || !u.HasPromptTokens() || u.PromptTokens != 10 {
				t.Fatalf("lost the measured prompt: %+v", u)
			}
			if u.HasCompletionTokens() || !u.NeedsTokenEstimate() || u.CompletionTokens != tc.floor {
				t.Errorf("an interim total proved the candidates count: %+v", u)
			}

			end, err := newStreamState("gemini-3-pro").parseStreamData(`{"candidates":[{"content":{},"finishReason":"STOP"}],"usageMetadata":` + tc.usage + `}`)
			if err != nil {
				t.Fatal(err)
			}
			if !end.Usage.HasCompletionTokens() || end.Usage.NeedsTokenEstimate() {
				t.Errorf("the same consistent total on the finishing frame stayed unmeasured: %+v", end.Usage)
			}
		})
	}
}

// A response with neither candidates nor a block reason keeps today's shape.
func TestNoCandidatesWithoutBlockReasonHasNoChoice(t *testing.T) {
	var resp geminiResponse
	if err := json.Unmarshal([]byte(`{"promptFeedback":{},"usageMetadata":{"promptTokenCount":10,"totalTokenCount":10}}`), &resp); err != nil {
		t.Fatal(err)
	}
	if got := translateResponse(&resp, "gemini-3-pro").Choices; len(got) != 0 {
		t.Errorf("choices = %+v, want none", got)
	}
}
