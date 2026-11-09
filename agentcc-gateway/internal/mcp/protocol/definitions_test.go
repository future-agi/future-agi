package protocol

import (
	"encoding/json"
	"testing"
)

func TestEditDefinition(t *testing.T) {
	raw := json.RawMessage(`{"name":"weather","title":"W","icons":[{"src":"i"}],"_meta":{"vendor/x":1,"io.futureagi/stale":true},"x-ext":{"a":1}}`)
	var got map[string]any
	gateway := map[string]json.RawMessage{"io.futureagi/price": json.RawMessage(`{"usd":1}`), "vendor/y": json.RawMessage(`2`)}
	_ = json.Unmarshal(EditDefinition(raw, "up_weather", gateway), &got)
	meta := got["_meta"].(map[string]any)
	if got["name"] != "up_weather" || got["title"] != "W" || got["icons"] == nil || got["x-ext"] == nil ||
		meta["vendor/x"] != float64(1) || meta["vendor/y"] != nil || meta["io.futureagi/stale"] != nil || meta["io.futureagi/price"] == nil {
		t.Fatalf("edited = %v", got)
	}
	// The upstream's io.futureagi/* keys go even when the gateway sets none.
	got = nil
	_ = json.Unmarshal(EditDefinition(raw, "", nil), &got)
	meta = got["_meta"].(map[string]any)
	if got["name"] != "weather" || meta["io.futureagi/stale"] != nil || meta["vendor/x"] != float64(1) {
		t.Fatalf("no gateway meta: %v", got)
	}
	got = nil
	_ = json.Unmarshal(EditDefinition(json.RawMessage(`{"name":"n","_meta":{"io.futureagi/error":{"code":"x"}}}`), "", nil), &got)
	if _, ok := got["_meta"]; ok || got["name"] != "n" {
		t.Fatalf("only-gateway-keys _meta = %v", got)
	}
}

func TestStripGatewayMeta(t *testing.T) {
	for in, want := range map[string]string{
		// An upstream's gateway-namespace keys never reach the client.
		`{"content":[],"_meta":{"io.futureagi/error":{"type":"x"},"io.futureagi/billing":{"usd":0},"vendor/k":1}}`: `{"_meta":{"vendor/k":1},"content":[]}`,
		`{"content":[],"_meta":{"io.futureagi/tool_call":{}}}`:                                                     `{"content":[]}`,
		// A duplicated _meta member collapses to the one a client reads last.
		`{"_meta":{"io.futureagi/error":{}},"_meta":{"a":1},"content":[]}`: `{"_meta":{"a":1},"content":[]}`,
		// Nested _meta objects are the upstream's data.
		`{"content":[{"type":"text","text":"t","_meta":{"io.futureagi/x":1}}]}`: `{"content":[{"type":"text","text":"t","_meta":{"io.futureagi/x":1}}]}`,
		`{"content":[],"_meta":"not an object"}`:                                `{"_meta":"not an object","content":[]}`,
		`[1]`:                                                                   `[1]`,
	} {
		if got := string(StripGatewayMeta(json.RawMessage(in))); got != want {
			t.Errorf("StripGatewayMeta(%s) = %s, want %s", in, got, want)
		}
	}
}

func TestAppendTextContent(t *testing.T) {
	var got map[string]any
	_ = json.Unmarshal(AppendTextContent(json.RawMessage(`{"content":[{"type":"text","text":"a"}],"structuredContent":{"x":1}}`), "note"), &got)
	if len(got["content"].([]any)) != 2 || got["structuredContent"] == nil {
		t.Fatalf("appended = %v", got)
	}
	if out := AppendTextContent(json.RawMessage(`{"x":1}`), "n"); string(out) != `{"x":1}` {
		t.Fatalf("no content array: %s", out)
	}
}
