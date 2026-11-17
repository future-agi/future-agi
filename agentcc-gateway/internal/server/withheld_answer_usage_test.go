package server

import (
	"net/http"
	"strings"
	"testing"
)

// A withheld answer (an output check blocked it, or no output check could read
// it) is one the provider gave and counted: its request log entry keeps the
// provider's token counts, as a served answer's does, and still holds no
// answer body. The upstream reports 5 prompt, 9 completion and 14 total
// tokens.

func assertLoggedTokens(t *testing.T, e map[string]any, prompt, completion, total int) {
	t.Helper()
	for field, want := range map[string]int{"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total} {
		if got, _ := e[field].(float64); int(got) != want {
			t.Errorf("log %s = %v, want %d", field, e[field], want)
		}
	}
}

func TestWithheldAnswerLogsTheProvidersTokens(t *testing.T) {
	for _, tc := range []struct {
		name  string
		setup postBlockSetup
		code  string
	}{
		{"an output rule blocks the answer", postBlockSetup{staticRule: answerBlockRule, capture: true}, "content_blocked"},
		{"the org's output check blocks the answer", postBlockSetup{orgCheck: true, capture: true}, "content_blocked"},
		{"no output check can read the answer", postBlockSetup{staticRule: leakageCheck, capture: true,
			upstream: refusalAudioUpstream(refusalAudioCases()["audio-bare"])}, "answer_unavailable"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, tc.setup)
			code, body := g.chat(t, false)
			if code != http.StatusForbidden || !strings.Contains(body, tc.code) {
				t.Fatalf("status %d, body %s; want 403 %s", code, body, tc.code)
			}
			e := g.entry(t)
			assertLoggedTokens(t, e, 5, 9, 14)
			if b, ok := e["response_body"]; ok && b != nil {
				t.Errorf("the withheld answer's body is logged: %v", b)
			}
		})
	}
}

// A refusal because no check can read the answer has no threshold: its
// guardrail result in the log carries none (it read "Threshold: 0"). A check
// that scored the answer keeps its threshold.
func TestUncheckedRefusalLogsNoThreshold(t *testing.T) {
	for _, tc := range []struct {
		name      string
		setup     postBlockSetup
		threshold any
	}{
		{"no output check can read the answer", postBlockSetup{staticRule: leakageCheck,
			upstream: refusalAudioUpstream(refusalAudioCases()["audio-bare"])}, nil},
		{"an output rule blocks the answer", postBlockSetup{staticRule: answerBlockRule}, 0.5},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newPostBlockGateway(t, tc.setup)
			if code, body := g.chat(t, false); code != http.StatusForbidden {
				t.Fatalf("status %d, body %s; want 403", code, body)
			}
			results, _ := g.entry(t)["guardrail_results"].([]any)
			if len(results) != 1 {
				t.Fatalf("guardrail_results = %v, want one result", results)
			}
			result, _ := results[0].(map[string]any)
			if got, ok := result["threshold"]; got != tc.threshold || (tc.threshold == nil && ok) {
				t.Errorf("guardrail result %v: threshold %v (present %t), want %v", result, got, ok, tc.threshold)
			}
		})
	}
}

// The same answer served (the output rule only logs): the same counts.
func TestServedAnswerLogsTheProvidersTokens(t *testing.T) {
	g := newPostBlockGateway(t, postBlockSetup{staticRule: answerBlockRule, action: "log", capture: true})
	if code, body := g.chat(t, false); code != http.StatusOK {
		t.Fatalf("status %d, body %s; want 200", code, body)
	}
	assertLoggedTokens(t, g.entry(t), 5, 9, 14)
}
