package protocol

// Integration tests.

import (
	"encoding/json"
	"testing"
)

// TestStripEnvelope: the legacy answer of a 2026-07-28 upstream's result
// drops only the top-level envelope members (resultType only when
// "complete") and _meta's serverInfo; a result with none, or that is not an
// object, is the same bytes.
func TestStripEnvelope(t *testing.T) {
	for in, want := range map[string]string{
		`{"content":[],"resultType":"complete","ttlMs":5,"cacheScope":"public"}`:                    `{"content":[]}`,
		`{"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"u"}},"content":[]}`:                `{"content":[]}`,
		`{"_meta":{"io.modelcontextprotocol/serverInfo":{},"k":1},"structuredContent":{"ttlMs":1}}`: `{"_meta":{"k":1},"structuredContent":{"ttlMs":1}}`,
		`{"content":[{"type":"text","text":"say \"resultType\""}]}`:                                 `{"content":[{"type":"text","text":"say \"resultType\""}]}`,
		`{"content":[],"resultType":"input_required"}`:                                              `{"content":[],"resultType":"input_required"}`,
		`[1,2]`: `[1,2]`,
		`null`:  `null`,
	} {
		if got := string(StripEnvelope(json.RawMessage(in))); got != want {
			t.Errorf("StripEnvelope(%s) = %s, want %s", in, got, want)
		}
	}
}
