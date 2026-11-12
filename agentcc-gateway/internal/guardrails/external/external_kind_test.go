package external

import (
	"context"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// An external evaluator's kind is its config's "judges", unknown when unset;
// any other value fails the check closed, with no call.
func TestExternalKind(t *testing.T) {
	for _, tc := range []struct {
		judges         interface{}
		shape, unknown bool
		source         string
	}{
		{nil, false, true, guardrails.KindNone},
		{"says", false, false, guardrails.KindSet},
		{"shape", true, false, guardrails.KindSet},
		{1.0, false, true, guardrails.KindInvalid},
	} {
		cfg := map[string]interface{}{"provider": "lakera", "api_key": "k", "endpoint": "http://127.0.0.1:1/guard"}
		if tc.judges != nil {
			cfg["judges"] = tc.judges
		}
		g := NewConfigFileRule("vendor", cfg)
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
