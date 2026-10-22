package server

import (
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
)

// The checks on the answer run before the response cache stores it (the
// guardrails' post-processing at 34, the cache's at 35): an answer a check
// rewrites (pii-detection's mask on the answer) is stored as the check left
// it, so a request the check does not apply to (X-Guardrail-Policy:
// disabled, from a key allowed to send it) is served the masked answer on a
// hit; and an answer a check withholds is not stored, so the same request is
// sent to the provider again and refused again, on every cache backend.

const answerOrderSSN = "123-45-6789"

func TestCache_AnAnswerACheckMasksIsStoredMasked(t *testing.T) {
	for _, disk := range []bool{false, true} {
		t.Run(map[bool]string{false: "in memory", true: "on disk"}[disk], func(t *testing.T) {
			g := newCacheOrderGateway(t, cacheOrderSetup{exactOnly: true, disk: disk,
				answer:      "The SSN on file is " + answerOrderSSN,
				answerRule:  &config.GuardrailRuleConfig{Name: "pii-detection", Stage: "post", Mode: "sync", Action: "log"},
				answerCheck: pii.New(map[string]interface{}{"remediation": "mask"})})
			first := g.chat(t, cacheOrderKey, "Which SSN is on file?", nil)
			if first.status != http.StatusOK || first.cache != "MISS" || strings.Contains(first.content, answerOrderSSN) {
				t.Fatalf("first call: %+v, want the masked answer, a cache miss", first)
			}
			second := g.chat(t, cacheOrderOverrideKey, "Which SSN is on file?", map[string]string{"X-Guardrail-Policy": "disabled"})
			if second.status != http.StatusOK || second.cache != "HIT" || g.calls.Load() != 1 {
				t.Fatalf("second call: %+v (provider calls %d), want a hit on the first's entry", second, g.calls.Load())
			}
			if second.content != first.content || strings.Contains(second.content, answerOrderSSN) {
				t.Errorf("served %q from the cache, want the answer as the check left it, %q", second.content, first.content)
			}
		})
	}
}

func TestCache_AnAnswerACheckWithholdsIsNotStored(t *testing.T) {
	for _, disk := range []bool{false, true} {
		t.Run(map[bool]string{false: "in memory", true: "on disk"}[disk], func(t *testing.T) {
			g := newCacheOrderGateway(t, cacheOrderSetup{exactOnly: true, disk: disk,
				answer:      "The SSN on file is " + answerOrderSSN,
				answerRule:  &config.GuardrailRuleConfig{Name: "pii-detection", Stage: "post", Mode: "sync", Action: "block"},
				answerCheck: pii.New(nil)})
			for i := 1; i <= 2; i++ {
				c := g.chat(t, cacheOrderKey, "Which SSN is on file?", nil)
				if c.status != http.StatusForbidden || strings.Contains(c.body, answerOrderSSN) || c.cache == "HIT" {
					t.Fatalf("call %d: %+v, want the answer withheld (403), not served from the cache", i, c)
				}
				if n := g.calls.Load(); n != int64(i) {
					t.Errorf("call %d: provider called %d times, want %d: a withheld answer was stored", i, n, i)
				}
			}
		})
	}
}
