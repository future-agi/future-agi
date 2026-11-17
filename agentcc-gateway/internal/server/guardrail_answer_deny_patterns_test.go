package server

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
)

// input-validation's deny_patterns judge what the text says (a regex
// data-loss rule), so they read a model's refusal as content checks do. Its
// length limits and require patterns judge the answer's shape and still skip
// the refusal. Plain and streamed, block mode; the rule has a deny pattern
// and a 40-character message limit.

func denyAndLengthRule() guardrails.Guardrail {
	return validation.New(map[string]interface{}{
		"deny_patterns":      []interface{}{`(?i)drop table`},
		"max_message_length": 40,
	})
}

func TestDenyPatternsReadARefusal(t *testing.T) {
	leak := refusalOnlyAnswer(postBlockLeak) // matches the deny pattern; over the limit
	long := refusalOnlyAnswer(shapeRefusal)  // over the limit only
	setup := func(upstream refusalAudioMessage) postBlockSetup {
		return postBlockSetup{staticRule: "input-validation", capture: true,
			checks: map[string]guardrails.Guardrail{"input-validation": denyAndLengthRule()}, upstream: refusalAudioUpstream(upstream)}
	}
	for _, stream := range []bool{false, true} {
		t.Run("a refusal the deny pattern matches / "+streamLabel(stream), func(t *testing.T) {
			g := newPostBlockGateway(t, setup(leak))
			code, body := g.chat(t, stream)
			assertRefusalBlocked(t, stream, code, body, leak, "input-validation")
			if !strings.Contains(body, "matched deny pattern") || strings.Contains(body, "exceeds max length") {
				t.Errorf("want the deny pattern, not the length limit, to refuse it; body %s", body)
			}
		})
		t.Run("a refusal over the length limit only / "+streamLabel(stream), func(t *testing.T) {
			g := newPostBlockGateway(t, setup(long))
			code, body := g.chat(t, stream)
			assertRefusalServed(t, stream, code, body, long)
		})
		// Control: an ordinary answer with the same text breaks both rules.
		t.Run("the same text as content / "+streamLabel(stream), func(t *testing.T) {
			s := setup(leak)
			s.upstream = nil
			g := newPostBlockGateway(t, s)
			code, body := g.chat(t, stream)
			if strings.Contains(body, "DROP TABLE") || !strings.Contains(body, "matched deny pattern") ||
				!strings.Contains(body, "exceeds max length") {
				t.Errorf("want the content refused by both rules: %d %s", code, body)
			}
		})
	}
}
