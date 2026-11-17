package vendors

import (
	"strings"
	"testing"
)

// A caller reads a refusal's message, so it names no internal code and says
// in words that the connection is not served yet.
func TestComingSoonRefusalSaysItInWords(t *testing.T) {
	const want = "Connections to this app are not available in this release."
	for _, reason := range []string{"connection_disabled", "provider_unavailable", "binding_changed"} {
		d := k2Refusal(reason, false)
		if strings.Contains(d.Message, "K2") {
			t.Fatalf("%s: the message names an internal code: %q", reason, d.Message)
		}
		if d.Message != want {
			t.Fatalf("%s: message %q, want %q", reason, d.Message, want)
		}
	}
}
