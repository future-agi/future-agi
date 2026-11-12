package server

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/anthropicfmt"
	"github.com/futureagi/agentcc-gateway/internal/genaifmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestUsagePresencePassthrough(t *testing.T) {
	for _, content := range []string{`{}`, `{"parts":[{"text":"hello"}]}`} {
		t.Run("gemini-thoughts/"+content, func(t *testing.T) {
			body := `{"candidates":[{"content":` + content + `,"finishReason":"MAX_TOKENS"}],"usageMetadata":{"promptTokenCount":1124,"totalTokenCount":1171,"thoughtsTokenCount":47}}`
			rc, src := models.AcquireRequestContext(), models.AcquireRequestContext()
			defer rc.Release()
			defer src.Release()
			applyGenAIUsage(rc, []byte(body))
			var scanner genaifmt.StreamUsageScanner
			scanner.Write([]byte("data: " + body + "\n\n"))
			applyGenAIStreamUsage(src, &scanner)
			for _, ctx := range []*models.RequestContext{rc, src} {
				if ctx.Response == nil || ctx.Response.Usage == nil {
					t.Fatal("missing usage")
				}
				u := ctx.Response.Usage
				if u.CompletionTokens != 47 || !u.HasCompletionTokens() || u.CompletionDetails.ReasoningTokens != 47 {
					t.Errorf("lost thinking-only evidence: %+v", u)
				}
			}
		})
	}
	for _, tc := range []struct {
		name, anthropic, gemini, responses string
		prompt, completion                 bool
	}{
		{"absent", `{}`, `{}`, `{}`, false, false},
		{"null", `{"usage":null}`, `{"usageMetadata":null}`, `{"usage":null}`, false, false},
		{"empty", `{"usage":{}}`, `{"usageMetadata":{}}`, `{"usage":{}}`, false, false},
		{"prompt-only", `{"usage":{"input_tokens":1000}}`, `{"usageMetadata":{"promptTokenCount":1000}}`, `{"usage":{"input_tokens":1000}}`, true, false},
		{"output-only", `{"usage":{"output_tokens":500}}`, `{"usageMetadata":{"candidatesTokenCount":500}}`, `{"usage":{"output_tokens":500}}`, false, true},
		{"zero", `{"usage":{"input_tokens":0,"output_tokens":0}}`, `{"usageMetadata":{"promptTokenCount":0,"candidatesTokenCount":0}}`, `{"usage":{"input_tokens":0,"output_tokens":0}}`, true, true},
		{"complete", `{"usage":{"input_tokens":10,"output_tokens":5}}`, `{"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":5}}`, `{"usage":{"input_tokens":10,"output_tokens":5}}`, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			check := func(name string, usage *models.Usage) {
				t.Helper()
				if usage.HasPromptTokens() != tc.prompt || usage.HasCompletionTokens() != tc.completion {
					t.Errorf("%s: invented or lost presence: %+v", name, usage)
				}
			}
			a, g := models.AcquireRequestContext(), models.AcquireRequestContext()
			defer a.Release()
			defer g.Release()
			applyAnthropicUsage(a, []byte(tc.anthropic))
			applyGenAIUsage(g, []byte(tc.gemini))
			var au, gu *models.Usage
			if a.Response != nil {
				au = a.Response.Usage
			}
			if g.Response != nil {
				gu = g.Response.Usage
			}
			check("anthropic", au)
			check("genai", gu)
			_, ru := responsesUsage([]byte(tc.responses))
			check("responses", ru)
			var as anthropicfmt.StreamUsageScanner
			as.Write([]byte("data: " + tc.anthropic + "\n\n"))
			check("anthropic-stream", as.UsageEvidence())
			var gs genaifmt.StreamUsageScanner
			gs.Write([]byte("data: " + tc.gemini + "\n\n"))
			check("genai-stream", gs.UsageEvidence())
		})
	}
}

// The BYOK GenAI passthrough meters the relayed body (cost and budgets) with
// the same terminal rule as the adapter: Gemini's finished zero-output shapes
// are a measured zero output streamed or not, and an interim frame's total
// never proves candidates for text that follows it.
func TestUsagePresencePassthroughZeroOutput(t *testing.T) {
	text := func(s, usage string) string {
		member := ""
		if usage != "" {
			member = `,"usageMetadata":` + usage
		}
		return `{"candidates":[{"content":{"role":"model","parts":[{"text":"` + s + `"}]}}]` + member + `}`
	}
	thoughts := `{"promptTokenCount":10,"thoughtsTokenCount":500,"totalTokenCount":510}`
	cases := []struct {
		name       string
		frames     []string
		completion int
		complete   bool
	}{
		{"cut-after-zero-interim", []string{text("hello", presenceZeroOutput), text(" there", "")}, 0, false},
		{"cut-after-thoughts-interim", []string{text("hello", thoughts), text(" there", "")}, 500, false},
		{"terminal-then-trailing-usage", []string{`{"candidates":[{"content":{},"finishReason":"MAX_TOKENS"}],"usageMetadata":` + thoughts + `}`,
			`{"usageMetadata":` + thoughts + `}`}, 500, true},
	}
	for _, b := range presenceZeroOutputBodies {
		cases = append(cases, struct {
			name       string
			frames     []string
			completion int
			complete   bool
		}{b.name, []string{b.body}, 0, true})
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			check := func(mode string, ctx *models.RequestContext) {
				t.Helper()
				if ctx.Response == nil || ctx.Response.Usage == nil {
					t.Fatalf("%s: missing usage", mode)
				}
				u := ctx.Response.Usage
				if !u.HasPromptTokens() || u.PromptTokens != 10 || u.CompletionTokens != tc.completion || u.HasCompletionTokens() != tc.complete {
					t.Errorf("%s: usage=%+v complete=%t, want completion %d complete=%t", mode, u, u.HasCompletionTokens(), tc.completion, tc.complete)
				}
			}
			if len(tc.frames) == 1 {
				rc := models.AcquireRequestContext()
				defer rc.Release()
				applyGenAIUsage(rc, []byte(tc.frames[0]))
				check("non-stream", rc)
			}
			src := models.AcquireRequestContext()
			defer src.Release()
			var scanner genaifmt.StreamUsageScanner
			for _, f := range tc.frames {
				scanner.Write([]byte("data: " + f + "\n\n"))
			}
			applyGenAIStreamUsage(src, &scanner)
			check("stream", src)
		})
	}
}
