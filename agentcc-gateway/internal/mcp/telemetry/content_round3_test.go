package telemetry

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func TestRound3PrivateJWKMembers(t *testing.T) {
	opaque := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("synthetic-jwk-fixture-", 2)))
	for _, field := range []string{"k", "d", "p", "q", "dp", "dq", "qi"} {
		raw, _ := json.Marshal(map[string]any{"kty": "RSA", field: opaque, "n": "public-modulus", "e": "AQAB", "safe": "retained"})
		got := Content(raw, "full", nil)
		if strings.Contains(got, opaque) || !strings.Contains(got, "public-modulus") || !strings.Contains(got, "retained") {
			t.Error("private JWK member exported or public control removed")
		}
	}
	raw := json.RawMessage(`{"d":"ordinary","p":"ordinary","k":"ordinary"}`)
	if Content(raw, "full", nil) != string(raw) { // JSON map order is canonicalized; compare values instead.
		var got map[string]string
		if json.Unmarshal([]byte(Content(raw, "full", nil)), &got) != nil || got["d"] != "ordinary" || got["k"] != "ordinary" || got["p"] != "ordinary" {
			t.Error("non-JWK fields changed")
		}
	}
}

func TestRound3EncodingAndHeaderBounds(t *testing.T) {
	opaque := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("synthetic-bound-fixture-", 2)))
	private := "Authorization: Basic " + opaque
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.RawStdEncoding, base64.URLEncoding, base64.RawURLEncoding} {
		encoded := enc.EncodeToString([]byte(private))
		for _, value := range []any{encoded, map[string]any{"message": encoded}, map[string]any{enc.EncodeToString([]byte("password")): opaque}} {
			raw, _ := json.Marshal(value)
			got := Content(raw, "full", nil)
			if strings.Contains(got, opaque) || strings.Contains(got, encoded) {
				t.Error("encoded credential exported")
			}
		}
	}
	for _, prefix := range []string{"Authorization:\r\n", "Authorization: Basic\r\n ", "X-API-Key:\n", "Cookie: SID=\n\t"} {
		raw, _ := json.Marshal(prefix + opaque + "\r\nAccept: text/plain")
		got := Content(raw, "full", nil)
		if strings.Contains(got, opaque) || !strings.Contains(got, "Accept: text/plain") {
			t.Error("logical header credential exported or adjacent safe header removed")
		}
	}
	pct := func(s string) string {
		var b strings.Builder
		for _, c := range []byte(s) {
			fmt.Fprintf(&b, "%%%02X", c)
		}
		return b.String()
	}
	for _, encode := range []func(string) string{pct, func(s string) string { return base64.RawURLEncoding.EncodeToString([]byte(s)) }} {
		encoded := private
		for i := 0; i < 7; i++ {
			encoded = encode(encoded)
		}
		raw, _ := json.Marshal(encoded)
		got := Content(raw, "full", nil)
		if strings.Contains(got, encoded) || strings.Contains(got, opaque) {
			t.Error("encoding depth bound did not fail closed")
		}
	}
}

func TestRound3HeaderDescriptorControls(t *testing.T) {
	private := base64.RawURLEncoding.EncodeToString([]byte(strings.Repeat("synthetic-fixture-", 3)))
	raw, _ := json.Marshal(map[string]any{"key": private, "value": "retained"})
	if strings.Contains(Content(raw, "full", nil), private) {
		t.Error("credential key exported beside an unrelated value field")
	}
	for _, descriptor := range []string{"name", "key", "header"} {
		raw, _ := json.Marshal(map[string]any{descriptor: "Content-Type", "value": "text/plain"})
		if Content(raw, "full", nil) != string(raw) {
			t.Error("safe header descriptor changed")
		}
	}
}

func TestRound3SafeAuthorityAndChunks(t *testing.T) {
	for _, v := range []string{"https://service.invalid:8443/path?message=a%2Bb%26c", "http://[::1]:8080/a", "https://[2001:db8::1]:443/path", "http://[fe80::1%25eth0]:8080/path", "c2FmZS10ZXh0"} {
		raw, _ := json.Marshal(v)
		if Content(raw, "full", nil) != string(raw) {
			t.Error("safe authority or encoded text changed")
		}
	}
	opaque := base64.StdEncoding.EncodeToString([]byte(strings.Repeat("synthetic-chunk-fixture-", 2)))
	raw, _ := json.Marshal(map[string]any{"content": []any{map[string]any{"type": "text", "text": "Authorization: Basic "}, map[string]any{"type": "text", "text": opaque}, map[string]any{"type": "text", "text": "\nAccept: text/plain"}}, "safe": "retained"})
	got := Content(raw, "full", nil)
	if strings.Contains(got, opaque) || !strings.Contains(got, "Accept: text/plain") || !strings.Contains(got, "retained") {
		t.Error("chunk credential exported or unrelated safe chunk removed")
	}
}
