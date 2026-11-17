package genaifmt

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestUsagePresenceThoughtsAfterEarlierCounters(t *testing.T) {
	var scanner StreamUsageScanner
	scanner.Write([]byte("data: {\"usageMetadata\":{\"promptTokenCount\":10,\"candidatesTokenCount\":0}}\n\n"))
	scanner.Write([]byte("data: {\"usageMetadata\":{\"promptTokenCount\":10,\"thoughtsTokenCount\":500}}\n\n"))
	scanner.Write([]byte("data: {\"usageMetadata\":{}}\n\n"))
	u := scanner.UsageEvidence()
	if u.HasCompletionTokens() || !u.NeedsTokenEstimate() || u.CompletionTokens != 500 {
		t.Fatalf("earlier zero hid the later missing candidates dimension: %+v", u)
	}
	estimate := models.MeasuredUsage(8, 3)
	estimate.Estimated = true
	filled := billing.CompleteUsage(u, estimate)
	if filled.CompletionTokens != 503 || filled.NeedsTokenEstimate() || !filled.Estimated {
		t.Fatalf("later thinking evidence did not get the candidates estimate: %+v", filled)
	}
}

// Gemini's JSON omits zero counters, so an absent thoughtsTokenCount is 0 and
// an absent candidatesTokenCount is either 0 or not yet counted. A reading
// that closes generation (a non-streamed body, or a stream frame carrying a
// finishReason) proves the omitted candidates from a consistent total. An
// interim frame's total can lag text the stream already carried, so it never
// does: its thoughts are a floor and its candidates stay unmeasured.
func TestUsagePresenceThoughtsEvidence(t *testing.T) {
	for _, tc := range []struct {
		name, raw string
		want      int
		complete  bool
		reasoning int
		// interim is the completion an interim frame retains; interimComplete
		// is true only where candidatesTokenCount itself was reported.
		interim         int
		interimComplete bool
	}{
		{"derived-zero", `{"promptTokenCount":10,"totalTokenCount":510,"thoughtsTokenCount":500}`, 500, true, 500, 500, false},
		{"derived-positive", `{"promptTokenCount":10,"totalTokenCount":515,"thoughtsTokenCount":500}`, 505, true, 500, 500, false},
		{"derived-excludes-tool-prompt", `{"promptTokenCount":10,"totalTokenCount":520,"toolUsePromptTokenCount":5,"thoughtsTokenCount":500}`, 505, true, 500, 500, false},
		{"inconsistent-tool-prompt", `{"promptTokenCount":10,"totalTokenCount":510,"toolUsePromptTokenCount":5,"thoughtsTokenCount":500}`, 500, false, 500, 500, false},
		{"null-candidates", `{"promptTokenCount":10,"candidatesTokenCount":null,"totalTokenCount":510,"thoughtsTokenCount":500}`, 500, true, 500, 500, false},
		{"missing-total", `{"promptTokenCount":10,"thoughtsTokenCount":500}`, 500, false, 500, 500, false},
		{"null-total", `{"promptTokenCount":10,"totalTokenCount":null,"thoughtsTokenCount":500}`, 500, false, 500, 500, false},
		{"inconsistent", `{"promptTokenCount":10,"totalTokenCount":509,"thoughtsTokenCount":500}`, 500, false, 500, 500, false},
		{"missing-prompt", `{"totalTokenCount":510,"thoughtsTokenCount":500}`, 500, false, 500, 500, false},
		// An omitted thoughtsTokenCount is Gemini's zero, so the consistent
		// total proves 500 candidates, exactly as an explicit zero does.
		{"missing-thoughts", `{"promptTokenCount":10,"totalTokenCount":510}`, 500, true, 0, 0, false},
		{"zero-thoughts", `{"promptTokenCount":10,"totalTokenCount":510,"thoughtsTokenCount":0}`, 500, true, 0, 0, false},
		// A finished answer with no output (SAFETY with content {}, STOP with
		// empty parts, a blocked prompt) reports only the prompt and an equal
		// total: zero candidates is measured, not missing.
		{"zero-output", `{"promptTokenCount":10,"totalTokenCount":10}`, 0, true, 0, 0, false},
		{"zero-output-tool-prompt", `{"promptTokenCount":10,"toolUsePromptTokenCount":4,"totalTokenCount":14}`, 0, true, 0, 0, false},
		{"total-below-prompt", `{"promptTokenCount":10,"totalTokenCount":9}`, 0, false, 0, 0, false},
		{"total-below-tool-prompt", `{"promptTokenCount":10,"toolUsePromptTokenCount":4,"totalTokenCount":12}`, 0, false, 0, 0, false},
		{"prompt-only", `{"promptTokenCount":10}`, 0, false, 0, 0, false},
		// A zero total is indistinguishable from an absent one.
		{"zero-total", `{"promptTokenCount":0,"totalTokenCount":0}`, 0, false, 0, 0, false},
		{"negative-prompt", `{"promptTokenCount":-1,"totalTokenCount":10}`, 0, false, 0, 0, false},
		{"complete", `{"promptTokenCount":10,"candidatesTokenCount":5,"thoughtsTokenCount":500}`, 505, true, 500, 505, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := []byte(`{"usageMetadata":` + tc.raw + `}`)
			u := ExtractUsageEvidence(body)
			if u == nil || u.CompletionTokens != tc.want || u.HasCompletionTokens() != tc.complete {
				t.Fatalf("raw evidence=%+v complete=%t want=%d/%t", u, u.HasCompletionTokens(), tc.want, tc.complete)
			}
			if u.TotalTokens < u.PromptTokens+u.CompletionTokens {
				t.Fatalf("total is below retained token evidence: %+v", u)
			}
			if u.CompletionDetails.ReasoningTokens != tc.reasoning {
				t.Fatalf("reasoning details=%d want %d: %+v", u.CompletionDetails.ReasoningTokens, tc.reasoning, u)
			}
			estimate := models.MeasuredUsage(8, 3)
			estimate.Estimated = true
			settle := func(name string, merged *models.Usage, want int, complete bool) {
				t.Helper()
				if merged == nil || merged.CompletionTokens != want || merged.HasCompletionTokens() != complete {
					t.Fatalf("%s: evidence=%+v want=%d/%t", name, merged, want, complete)
				}
				filled := billing.CompleteUsage(merged, estimate)
				if !complete {
					want += 3
				}
				if filled.CompletionTokens != want || filled.NeedsTokenEstimate() || filled.Estimated == complete {
					t.Errorf("%s: completed=%+v want=%d estimated=%t", name, filled, want, !complete)
				}
				if again := billing.CompleteUsage(filled, estimate); again.CompletionTokens != want {
					t.Errorf("%s: estimate applied twice: %+v", name, again)
				}
			}
			// A terminal frame, then an empty trailing usageMetadata that
			// cannot erase it.
			var terminal StreamUsageScanner
			terminal.Write([]byte("data: {\"candidates\":[{\"content\":{},\"finishReason\":\"STOP\"}],\"usageMetadata\":" + tc.raw + "}\n\ndata: {\"usageMetadata\":{}}\n\n"))
			settle("terminal-frame", terminal.UsageEvidence(), tc.want, tc.complete)
			// The same reading on an interim frame (text, no finishReason).
			var interim StreamUsageScanner
			interim.Write([]byte("data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"hel\"}]}}],\"usageMetadata\":" + tc.raw + "}\n\n"))
			settle("interim-frame", interim.UsageEvidence(), tc.interim, tc.interimComplete)
		})
	}
}

// A stream is billed from the frames its scanner retained. Only a frame that
// closes generation (every candidate finished, or the prompt itself blocked)
// may prove an omitted candidates count, and once one has, a later trailing
// usage frame is past generation too.
func TestUsagePresenceGeminiTerminalFrames(t *testing.T) {
	text := func(s, usage string) string {
		member := ""
		if usage != "" {
			member = `,"usageMetadata":` + usage
		}
		return `{"candidates":[{"content":{"role":"model","parts":[{"text":"` + s + `"}]}}]` + member + `}`
	}
	end := func(reason, usage string) string {
		return `{"candidates":[{"content":{},"finishReason":"` + reason + `"}],"usageMetadata":` + usage + `}`
	}
	zero := `{"promptTokenCount":10,"totalTokenCount":10}`
	for _, tc := range []struct {
		name       string
		frames     []string
		completion int
		complete   bool
	}{
		// The regression the terminal rule exists for: text streamed after a
		// zero-output interim frame, then the stream ends with no later usage.
		{"cut-after-zero-interim", []string{text("hello", zero), text(" there", "")}, 0, false},
		{"cut-after-thoughts-interim", []string{text("hello", `{"promptTokenCount":10,"thoughtsTokenCount":500,"totalTokenCount":510}`), text(" there", "")}, 500, false},
		{"zero-interim-then-terminal", []string{text("hello", zero), end("STOP", `{"promptTokenCount":10,"totalTokenCount":15}`)}, 5, true},
		{"terminal-then-trailing-usage", []string{end("MAX_TOKENS", `{"promptTokenCount":10,"thoughtsTokenCount":500,"totalTokenCount":510}`),
			`{"usageMetadata":{"promptTokenCount":10,"thoughtsTokenCount":500,"totalTokenCount":510}}`}, 500, true},
		{"safety-empty-content", []string{end("SAFETY", zero)}, 0, true},
		{"stop-empty-parts", []string{`{"candidates":[{"content":{"role":"model","parts":[]},"finishReason":"STOP"}],"usageMetadata":` + zero + `}`}, 0, true},
		{"prompt-blocked", []string{`{"promptFeedback":{"blockReason":"SAFETY"},"usageMetadata":` + zero + `}`}, 0, true},
		// One candidate finished while another is still generating.
		{"unfinished-candidate", []string{`{"candidates":[{"content":{},"finishReason":"STOP"},{"content":{"parts":[{"text":"x"}]}}],"usageMetadata":{"promptTokenCount":10,"totalTokenCount":12}}`}, 0, false},
		{"usage-only-interim", []string{`{"usageMetadata":` + zero + `}`}, 0, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var s StreamUsageScanner
			for _, f := range tc.frames {
				s.Write([]byte("data: " + f + "\n\n"))
			}
			u := s.UsageEvidence()
			if u == nil || !u.HasPromptTokens() || u.PromptTokens != 10 {
				t.Fatalf("lost the measured prompt: %+v", u)
			}
			if u.CompletionTokens != tc.completion || u.HasCompletionTokens() != tc.complete || u.NeedsTokenEstimate() == tc.complete {
				t.Errorf("completion=%d complete=%t, want %d/%t: %+v", u.CompletionTokens, u.HasCompletionTokens(), tc.completion, tc.complete, u)
			}
		})
	}
}
