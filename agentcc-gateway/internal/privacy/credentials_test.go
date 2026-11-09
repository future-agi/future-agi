package privacy_test

import (
	"errors"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// credentialSamples has one credential per shared pattern, by name; each
// secret is a fake value.
var credentialSamples = map[string]struct{ text, secret, kept string }{
	"bearer":     {"upstream said: Authorization: Bearer abc.def-123_456", "abc.def-123_456", ""},
	"sk_key":     {"invalid key sk-test0123456789abcdef", "sk-test0123456789abcdef", ""},
	"google_key": {"key AIzaSyA0123456789abcdefghijk rejected", "AIzaSyA0123456789abcdefghijk", ""},
	"aws_key":    {"AKIAABCDEFGHIJKLMNOP is not allowed", "AKIAABCDEFGHIJKLMNOP", ""},
	"secret":     {"GET /v1?api_key=hunter2hunter2&x=1 failed", "hunter2hunter2", "api_key="},
}

// TestCredentialPatternsSharedWithErrclass: routing's attempt error scrubber
// is the shared list (M5 IR-3): for every shared pattern it removes exactly
// what RedactCredentials removes, keeps a key=value pair's name, and every
// pattern has a sample here, so a pattern added to the list is covered.
func TestCredentialPatternsSharedWithErrclass(t *testing.T) {
	patterns := privacy.CredentialPatterns()
	if len(patterns) != len(credentialSamples) {
		t.Fatalf("%d shared patterns, %d samples: give every pattern a sample", len(patterns), len(credentialSamples))
	}
	redacted := func(string) string { return "[redacted]" }
	for _, p := range patterns {
		s, ok := credentialSamples[p.Name]
		if !ok {
			t.Errorf("pattern %q has no sample", p.Name)
			continue
		}
		shared := privacy.RedactCredentials(s.text, redacted)
		if strings.Contains(shared, s.secret) || !strings.Contains(shared, s.kept+"[redacted]") {
			t.Errorf("%s: RedactCredentials(%q) = %q", p.Name, s.text, shared)
		}
		if got := routing.AttemptErrorMessage(errors.New(s.text)); got != shared {
			t.Errorf("%s: routing scrubbed %q to %q, the shared list to %q", p.Name, s.text, got, shared)
		}
	}
	named := privacy.RedactCredentials("token: abc123 and Bearer xyz", func(n string) string { return "<" + n + "$1>" })
	if named != "token: <secret$1> and <bearer$1>" {
		t.Errorf("a marker with a $ is not kept literally: %q", named)
	}
}
