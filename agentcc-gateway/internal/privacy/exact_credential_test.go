package privacy

import (
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
	"testing"
)

func TestR5ExactCredentialSpellings(t *testing.T) {
	for _, key := range []string{`opaque/"\+token-abcdefghijklmnopqrstuvwxyz`, "short-key", "unicode-雪-秘密/abc", "a-very-long-opaque-token-01234567890123456789"} {
		quoted, _ := json.Marshal(key)
		inputs := []string{key, string(quoted[1 : len(quoted)-1]), url.QueryEscape(key), url.PathEscape(key), hex.EncodeToString([]byte(key)), strings.ToUpper(hex.EncodeToString([]byte(key)))}
		var percent strings.Builder
		for _, b := range []byte(key) {
			fmt.Fprintf(&percent, "%%%02x", b)
		}
		inputs = append(inputs, percent.String())
		for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.URLEncoding, base64.RawStdEncoding, base64.RawURLEncoding} {
			for n := 0; n < 3; n++ {
				inputs = append(inputs, enc.EncodeToString([]byte(strings.Repeat("!", n)+key+" trailing bytes")))
			}
		}
		for i, input := range inputs {
			t.Run(fmt.Sprintf("len%d/form%d", len(key), i), func(t *testing.T) {
				text := "prefix " + input + " suffix"
				got := ScrubExactCredential(text, key)
				if strings.Contains(got, input) || strings.Contains(got, key) {
					t.Fatal("registered credential spelling survived")
				}
				if !strings.HasPrefix(got, "prefix ") || !strings.HasSuffix(got, " suffix") {
					t.Fatal("lost surrounding diagnostics")
				}
			})
		}
	}
}
func TestR5ExactCredentialControls(t *testing.T) {
	key := "opaque-abcdefghijklmno-pqrstuvwxyz"
	text := "before " + key + " after unrelated-owner-key prefix...wxyz"
	want := "before [REDACTED] after unrelated-owner-key prefix...wxyz"
	if got := ScrubExactCredential(text, key); got != want {
		t.Fatalf("got %q", got)
	}
	if got := ScrubExactCredential(text, "", ""); got != text {
		t.Fatal("empty credentials mask content")
	}
	if got := ScrubExactCredential("no credentials here", key); got != "no credentials here" {
		t.Fatal("negative control changed")
	}
}

// A one-byte or very short registered value must not turn ordinary words into
// redaction marks through its derived spellings (base64 fragments, hex).
func TestR5ExactCredentialShortValuesDoNotMangleText(t *testing.T) {
	for _, key := range []string{"k", "ab", "tok"} {
		for _, text := range []string{"rate limit exceeded", "rate_limit_error", "upstream_error", "invalid request: 6b aw== r a"} {
			want := strings.ReplaceAll(text, key, "[REDACTED]")
			if got := ScrubExactCredential(text, key); got != want {
				t.Errorf("key %q text %q: got %q want %q", key, text, got, want)
			}
		}
	}
	if got := ScrubExactCredential("before k after", "k"); got != "before [REDACTED] after" {
		t.Fatalf("registered short value must still be removed: %q", got)
	}
}
