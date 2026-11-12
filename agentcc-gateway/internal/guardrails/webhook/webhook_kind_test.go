package webhook

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// A webhook's kind is its config's "judges", unknown when unset; any other
// value fails the check closed, with no call.
func TestWebhookKind(t *testing.T) {
	for _, tc := range []struct {
		judges         interface{}
		shape, unknown bool
		source         string
	}{
		{nil, false, true, guardrails.KindNone},
		{"says", false, false, guardrails.KindSet},
		{"shape", true, false, guardrails.KindSet},
		{"format", false, true, guardrails.KindInvalid},
	} {
		cfg := map[string]interface{}{"url": "http://127.0.0.1:1/check"}
		if tc.judges != nil {
			cfg["judges"] = tc.judges
		}
		g := NewConfigFileRule("hook", cfg)
		if g.JudgesShape() != tc.shape || g.KindUnknown() != tc.unknown || g.KindSource() != tc.source {
			t.Errorf("judges %v: shape %v unknown %v source %q", tc.judges, g.JudgesShape(), g.KindUnknown(), g.KindSource())
		}
		if tc.source == guardrails.KindInvalid {
			r := g.Check(context.Background(), &guardrails.CheckInput{})
			if r.Pass || !strings.Contains(r.Message, `"judges" must be`) {
				t.Errorf("an invalid judges did not fail closed: %+v", r)
			}
		}
	}
}
