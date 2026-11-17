package protocol

import (
	"encoding/json"
	"testing"
	"time"
)

func TestEnvelopeDecorate(t *testing.T) {
	info := json.RawMessage(`{"name":"Future AGI gateway","version":"1"}`)
	in := json.RawMessage(`{"content":[],"_meta":{"io.modelcontextprotocol/serverInfo":{"name":"upstream"},"vendor/k":1},"x-ext":{"a":1}}`)
	var got map[string]any
	_ = json.Unmarshal(Envelope{ServerInfo: info}.Decorate(in), &got)
	meta, _ := got["_meta"].(map[string]any)
	si, _ := meta[MetaServerInfo].(map[string]any)
	if got["resultType"] != ResultComplete || si["name"] != "Future AGI gateway" || meta["vendor/k"] != float64(1) ||
		got["serverInfo"] != nil || got["x-ext"] == nil || got["ttlMs"] != nil {
		t.Fatalf("call envelope = %v", got)
	}
	got = nil
	_ = json.Unmarshal(Envelope{ServerInfo: info}.Decorate(json.RawMessage(`{"content":[]}`)), &got)
	if meta, _ := got["_meta"].(map[string]any); meta[MetaServerInfo] == nil {
		t.Fatalf("serverInfo without an upstream _meta = %v", got)
	}
	_ = json.Unmarshal(Envelope{}.Decorate(json.RawMessage(`{"resultType":"input_required"}`)), &got)
	if got["resultType"] != ResultInputRequired {
		t.Fatalf("own resultType replaced: %v", got)
	}
	list := Envelope{Cacheable: true, TTL: time.Minute, CacheScope: CacheScopePublic}
	_ = json.Unmarshal(list.Decorate(json.RawMessage(`{"tools":[]}`)), &got)
	if got["ttlMs"] != float64(60000) || got["cacheScope"] != CacheScopePublic {
		t.Fatalf("list envelope = %v", got)
	}
	_ = json.Unmarshal(Envelope{Cacheable: true, TTL: time.Minute, CacheScope: "shared"}.Decorate(json.RawMessage(`{}`)), &got)
	if got["cacheScope"] != CacheScopePrivate {
		t.Fatalf("unknown scope = %v", got["cacheScope"])
	}
	read := Envelope{Cacheable: true, TTL: time.Minute, UpstreamTTL: true}
	for in, want := range map[string]float64{`{"ttlMs":1000}`: 1000, `{"ttlMs":900000}`: 60000, `{}`: 0, `{"ttlMs":-5}`: 0} {
		_ = json.Unmarshal(read.Decorate(json.RawMessage(in)), &got)
		if got["ttlMs"] != want {
			t.Errorf("read %s ttlMs = %v, want %v", in, got["ttlMs"], want)
		}
	}
	if out := (Envelope{}).Decorate(json.RawMessage(`[1]`)); string(out) != `[1]` {
		t.Fatalf("non-object = %s", out)
	}
	if !Cacheable("server/discover") || Cacheable("tools/call") {
		t.Fatal("Cacheable")
	}
}
