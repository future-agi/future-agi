package protocol

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"unicode/utf8"
)

func TestClassifyEra(t *testing.T) {
	meta := `{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"logLevel":"info"}}`
	hdr := func(v string) http.Header {
		h := http.Header{}
		if v != "" {
			h.Set(HeaderProtocolVersion, v)
		}
		return h
	}
	cases := []struct {
		name, method, params, header string
		stateless                    bool
		served                       string
	}{
		{"2026 _meta", "tools/list", meta, Version2026, true, Version2026},
		{"2026 _meta, session id ignored by the caller", "tools/call", meta, "", true, Version2026},
		{"2026 header, no _meta", "tools/list", `{}`, Version2026, true, ""},
		{"_meta with only clientInfo is legacy", "tools/list", `{"_meta":{"io.modelcontextprotocol/clientInfo":{}}}`, "", false, DefaultLegacyVersion},
		{"protocolVersion key, empty value", "tools/list", `{"_meta":{"io.modelcontextprotocol/protocolVersion":""}}`, "", true, ""},
		{"legacy session sending another reserved key", "tools/call", `{"_meta":{"io.modelcontextprotocol/related-task":{"taskId":"t1"}}}`, Version20251125, false, Version20251125},
		{"initialize with _meta is legacy", "initialize", meta, "", false, DefaultLegacyVersion},
		{"legacy with header", "tools/list", `{"_meta":{"progressToken":1}}`, Version20250618, false, Version20250618},
		{"legacy without header", "tools/list", ``, "", false, DefaultLegacyVersion},
	}
	for _, tc := range cases {
		e := Classify(tc.method, json.RawMessage(tc.params), hdr(tc.header))
		if e.Stateless != tc.stateless || e.Served() != tc.served {
			t.Errorf("%s: stateless %v served %q, want %v %q", tc.name, e.Stateless, e.Served(), tc.stateless, tc.served)
		}
	}
	if e := Classify("initialize", nil, hdr("")); !e.Initialize || e.Stateless {
		t.Errorf("initialize = %+v", e)
	}
	if m := ParseMeta(json.RawMessage(`{"_meta":{"io.modelcontextprotocol/clientCapabilities":{}}}`)); m.Present {
		t.Errorf("_meta without protocolVersion counted as 2026: %+v", m)
	}
	if m := ParseMeta(json.RawMessage(meta)); m.LogLevel != "info" || !m.Present || len(m.ClientCapabilities) == 0 {
		t.Errorf("ParseMeta = %+v", m)
	}
	if m := ParseMeta(json.RawMessage(`{"_meta":{"io.modelcontextprotocol/logLevel":"error"}}`)); m.LogLevel != "error" {
		t.Errorf("namespaced logLevel = %q", m.LogLevel)
	}
}

func TestValidateEra(t *testing.T) {
	all := AllVersions()
	ok := Era{Stateless: true, Meta: Meta{Present: true, ProtocolVersion: Version2026, ClientCapabilities: json.RawMessage(`{}`)}}
	if err := ok.Validate(all); err != nil {
		t.Fatalf("valid 2026 request refused: %v", err)
	}
	noCaps := ok
	noCaps.Meta.ClientCapabilities = nil
	if err := noCaps.Validate(all); err == nil || err.Code != CodeMissingClientCapability || err.HTTPStatus != http.StatusBadRequest {
		t.Fatalf("no capabilities = %+v", err)
	}
	future := ok
	future.Meta.ProtocolVersion = "2099-01-01"
	err := future.Validate(all)
	if err == nil || err.Code != CodeUnsupportedVersion || string(err.Data) != `{"supported":["2026-07-28","2025-11-25","2025-06-18","2025-03-26","2024-11-05"]}` {
		t.Fatalf("future version = %+v", err)
	}
	if err := ok.Validate([]string{Version20251125}); err == nil || err.Code != CodeUnsupportedVersion {
		t.Fatalf("2026 off = %+v", err)
	}
	if err := (Era{Version: "1999-01-01"}).Validate(all); err == nil || err.Code != CodeUnsupportedVersion {
		t.Fatalf("legacy unknown header = %+v", err)
	}
	if err := (Era{}).Validate(all); err != nil {
		t.Fatalf("legacy without header = %+v", err)
	}
	// Without 2025-03-26 served, a request without the header is refused
	// with the revisions served; initialize negotiates in its params.
	noOld := []string{Version2026, Version20251125}
	if err := (Era{}).Validate(noOld); err == nil || err.Code != CodeUnsupportedVersion || err.HTTPStatus != http.StatusBadRequest ||
		string(err.Data) != `{"supported":["2026-07-28","2025-11-25"]}` {
		t.Fatalf("legacy without header, 2025-03-26 not served = %+v", err)
	}
	if err := (Era{Initialize: true}).Validate(noOld); err != nil {
		t.Fatalf("initialize without header refused: %+v", err)
	}
	if err := (Era{Initialize: true, Version: Version20250326}).Validate(noOld); err == nil || err.Code != CodeUnsupportedVersion {
		t.Fatalf("initialize naming an unserved header revision = %+v", err)
	}
	long := Era{Version: strings.Repeat("é", 500)}.Validate(all)
	if long == nil || len(long.Message) > 200 || !utf8.ValidString(long.Message) {
		t.Fatalf("a long client version is echoed whole: %d bytes", len(long.Message))
	}
}
