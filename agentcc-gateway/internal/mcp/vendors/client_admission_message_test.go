package vendors

import (
	"strings"
	"testing"
)

// A caller reads a refusal's message, so it names no internal code and says
// in words that the connection is not served yet and nothing was sent.
func TestComingSoonRefusalSaysItInWords(t *testing.T) {
	const want = "Coming soon: this connection is not available in this gateway yet; no credential was read and the call was not made."
	for _, reason := range []string{"k2_connection_disabled", "k2_provider_unavailable", "k2_binding_changed"} {
		d := k2Refusal(reason)
		if strings.Contains(d.Message, "K2") {
			t.Fatalf("%s: the message names an internal code: %q", reason, d.Message)
		}
		if d.Message != want {
			t.Fatalf("%s: message %q, want %q", reason, d.Message, want)
		}
	}
}
