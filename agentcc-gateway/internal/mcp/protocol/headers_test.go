package protocol

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"testing"
)

func TestHeaderValueBase64Form(t *testing.T) {
	for _, v := range []string{"plain", "eu-west 🌍", " padded ", "line\nbreak", "=?base64?looks-like-one?="} {
		enc := EncodeHeaderValue(v)
		if got, err := DecodeHeaderValue(enc); err != nil || got != v {
			t.Errorf("round trip %q via %q = %q, %v", v, enc, got, err)
		}
	}
	if EncodeHeaderValue("plain") != "plain" {
		t.Error("a safe value was encoded")
	}
	raw := "=?base64?" + base64.RawStdEncoding.EncodeToString([]byte("ab")) + "?="
	if got, err := DecodeHeaderValue(raw); err != nil || got != "ab" {
		t.Errorf("unpadded Base64 = %q, %v", got, err)
	}
	if _, err := DecodeHeaderValue("=?base64?!!?="); err == nil {
		t.Error("bad Base64 decoded")
	}
}

func TestCheckHeaders(t *testing.T) {
	schema := json.RawMessage(`{"type":"object","properties":{"region":{"type":"string","x-mcp-header":"Region"},"n":{"type":"integer","x-mcp-header":"N"}}}`)
	lookup := func(name string) (json.RawMessage, bool) { return schema, name == "geo" }
	meta := Meta{ProtocolVersion: Version2026}
	base := func() http.Header {
		h := http.Header{}
		h.Set(HeaderProtocolVersion, Version2026)
		h.Set(HeaderMethod, "tools/call")
		h.Set(HeaderName, "geo")
		h.Set("Mcp-Param-Region", "eu")
		h.Set("Mcp-Param-N", "3")
		return h
	}
	params := json.RawMessage(`{"name":"geo","arguments":{"region":"eu","n":3}}`)
	if err := CheckHeaders(base(), "tools/call", params, meta, lookup); err != nil {
		t.Fatalf("matching headers refused: %v", err)
	}
	for name, edit := range map[string]func(h http.Header){
		"number differs":   func(h http.Header) { h.Set("Mcp-Param-N", "4") },
		"duplicate method": func(h http.Header) { h.Add(HeaderMethod, "tools/call") },
		"param missing":    func(h http.Header) { h.Del("Mcp-Param-Region") },
		"version differs":  func(h http.Header) { h.Set(HeaderProtocolVersion, Version20251125) },
	} {
		h := base()
		edit(h)
		if err := CheckHeaders(h, "tools/call", params, meta, lookup); err == nil || err.Code != CodeHeaderMismatch || err.HTTPStatus != http.StatusBadRequest {
			t.Errorf("%s: %v, want -32020", name, err)
		}
	}
	// An unknown tool has no marked parameters: Mcp-Param headers are not judged.
	h := base()
	h.Set(HeaderName, "other")
	if err := CheckHeaders(h, "tools/call", json.RawMessage(`{"name":"other"}`), meta, lookup); err != nil {
		t.Errorf("unknown tool's param headers judged: %v", err)
	}
	// Methods without a name need only the version and method headers.
	h = http.Header{}
	h.Set(HeaderProtocolVersion, Version2026)
	h.Set(HeaderMethod, "tools/list")
	if err := CheckHeaders(h, "tools/list", nil, meta, lookup); err != nil {
		t.Errorf("tools/list refused: %v", err)
	}
	if got := ParamHeaders(base()); got["Region"] != "eu" || got["N"] != "3" || len(got) != 2 {
		t.Errorf("ParamHeaders = %v", got)
	}
}

// paramCase is one tools/call against a schema: its arguments, its
// Mcp-Param-* headers, and whether the check must pass.
type paramCase struct {
	name string
	args string
	hdr  map[string]string
	ok   bool
}

func runParamCases(t *testing.T, schema string, cases []paramCase) {
	t.Helper()
	lookup := func(string) (json.RawMessage, bool) { return json.RawMessage(schema), true }
	for _, tc := range cases {
		h := http.Header{}
		h.Set(HeaderProtocolVersion, Version2026)
		h.Set(HeaderMethod, "tools/call")
		h.Set(HeaderName, "t")
		for k, v := range tc.hdr {
			h.Add(k, v)
		}
		params := json.RawMessage(`{"name":"t","arguments":` + tc.args + `}`)
		err := CheckHeaders(h, "tools/call", params, Meta{ProtocolVersion: Version2026}, lookup)
		switch {
		case tc.ok && err != nil:
			t.Errorf("%s: refused: %v", tc.name, err)
		case !tc.ok && (err == nil || err.Code != CodeHeaderMismatch || err.HTTPStatus != http.StatusBadRequest):
			t.Errorf("%s: %v, want 400 -32020", tc.name, err)
		}
	}
}

func TestCheckParamHeaders(t *testing.T) {
	t.Run("nested properties", func(t *testing.T) {
		schema := `{"type":"object","properties":{"target":{"type":"object","properties":{"region":{"type":"string","x-mcp-header":"Region"}}}}}`
		runParamCases(t, schema, []paramCase{
			{"value and header", `{"target":{"region":"eu"}}`, map[string]string{"Mcp-Param-Region": "eu"}, true},
			{"header missing", `{"target":{"region":"eu"}}`, nil, false},
			{"header differs", `{"target":{"region":"eu"}}`, map[string]string{"Mcp-Param-Region": "us"}, false},
			{"no value", `{"target":{}}`, nil, true},
			{"no value, header sent", `{"other":1}`, map[string]string{"Mcp-Param-Region": "eu"}, false},
			{"level not an object", `{"target":"eu"}`, nil, true},
			{"level in other case", `{"TARGET":{"region":"us"}}`, nil, false},
			{"level twice", `{"target":{"region":"eu"},"target":{"region":"us"}}`, map[string]string{"Mcp-Param-Region": "us"}, false},
		})
	})
	t.Run("a boolean property schema hides no mark", func(t *testing.T) {
		schema := `{"type":"object","properties":{"other":true,"region":{"type":"string","x-mcp-header":"Region"},"none":false}}`
		runParamCases(t, schema, []paramCase{
			{"header missing", `{"region":"eu","other":1}`, nil, false},
			{"header sent", `{"region":"eu","other":1}`, map[string]string{"Mcp-Param-Region": "eu"}, true},
		})
	})
	t.Run("two properties naming one header", func(t *testing.T) {
		schema := `{"type":"object","properties":{"a":{"type":"string","x-mcp-header":"Region"},"b":{"type":"string","x-mcp-header":"REGION"}}}`
		runParamCases(t, schema, []paramCase{
			{"both equal the header", `{"a":"eu","b":"eu"}`, map[string]string{"Mcp-Param-Region": "eu"}, true},
			{"b differs", `{"a":"eu","b":"us"}`, map[string]string{"Mcp-Param-Region": "eu"}, false},
			{"a differs", `{"a":"us","b":"eu"}`, map[string]string{"Mcp-Param-Region": "eu"}, false},
		})
	})
	t.Run("null is no value", func(t *testing.T) {
		schema := `{"type":"object","properties":{"region":{"type":"string","x-mcp-header":"Region"}}}`
		runParamCases(t, schema, []paramCase{
			{"null, no header", `{"region":null}`, nil, true},
			{"null, header sent", `{"region":null}`, map[string]string{"Mcp-Param-Region": ""}, false},
		})
	})
	t.Run("numbers compare numerically", func(t *testing.T) {
		schema := `{"type":"object","properties":{"n":{"type":"integer","x-mcp-header":"N"},"b":{"type":"boolean","x-mcp-header":"B"}}}`
		runParamCases(t, schema, []paramCase{
			{"42.0 and 42", `{"n":42.0}`, map[string]string{"Mcp-Param-N": "42"}, true},
			{"42 and 4.2e1", `{"n":42}`, map[string]string{"Mcp-Param-N": "4.2e1"}, true},
			{"-7 and -7", `{"n":-7}`, map[string]string{"Mcp-Param-N": "-7"}, true},
			{"42 and 43", `{"n":42}`, map[string]string{"Mcp-Param-N": "43"}, false},
			{"hex is not a JSON number", `{"n":42}`, map[string]string{"Mcp-Param-N": "0x2A"}, false},
			{"a fraction is not 42", `{"n":42}`, map[string]string{"Mcp-Param-N": "42.0000000000000001"}, false},
			{"a huge exponent is refused", `{"n":1e999999999}`, map[string]string{"Mcp-Param-N": "1"}, false},
			{"true and true", `{"b":true}`, map[string]string{"Mcp-Param-B": "true"}, true},
			{"true and True", `{"b":true}`, map[string]string{"Mcp-Param-B": "True"}, false},
			{"a string is not a number", `{"n":"42"}`, map[string]string{"Mcp-Param-N": "42.0"}, false},
		})
	})
	t.Run("a plain value holds only header characters", func(t *testing.T) {
		schema := `{"type":"object","properties":{"region":{"type":"string","x-mcp-header":"Region"}}}`
		runParamCases(t, schema, []paramCase{
			{"plain UTF-8", `{"region":"eu-west 🌍"}`, map[string]string{"Mcp-Param-Region": "eu-west 🌍"}, false},
			{"Base64 form", `{"region":"eu-west 🌍"}`, map[string]string{"Mcp-Param-Region": EncodeHeaderValue("eu-west 🌍")}, true},
			{"tab inside", `{"region":"a\tb"}`, map[string]string{"Mcp-Param-Region": "a\tb"}, true},
		})
	})
	t.Run("schema keywords are exact", func(t *testing.T) {
		// A client reads JSON Schema keywords as spelled, so neither of these
		// marks a parameter, and no header is expected.
		runParamCases(t, `{"Properties":{"region":{"x-mcp-header":"Region"}}}`, []paramCase{{"Properties", `{"region":"eu"}`, nil, true}})
		runParamCases(t, `{"properties":{"region":{"X-MCP-HEADER":"Region"}}}`, []paramCase{{"X-MCP-HEADER", `{"region":"eu"}`, nil, true}})
	})
	t.Run("the depth is bounded", func(t *testing.T) {
		schema, args := `{"type":"string","x-mcp-header":"Deep"}`, `"v"`
		for i := 0; i < maxMarkDepth+1; i++ {
			schema = `{"type":"object","properties":{"p":` + schema + `}}`
			args = `{"p":` + args + `}`
		}
		if got, deep := markedParams(json.RawMessage(schema)); len(got) != 0 || !deep {
			t.Errorf("a mark %d levels down: marks %v, deep %v; want none read and deep", maxMarkDepth+1, got, deep)
		}
		// Too deep to check is refused, header or not, never served unchecked.
		runParamCases(t, schema, []paramCase{
			{"too deep, no header", args, nil, false},
			{"too deep, header sent", args, map[string]string{"Mcp-Param-Deep": "v"}, false},
		})
		// A deep schema that marks nothing is served.
		plain := `{"type":"string"}`
		for i := 0; i < maxMarkDepth+1; i++ {
			plain = `{"type":"object","properties":{"p":` + plain + `}}`
		}
		runParamCases(t, plain, []paramCase{{"deep, no marks", args, nil, true}})
		// The deepest mark that is read: maxMarkDepth properties down.
		at, deepArgs := `{"type":"string","x-mcp-header":"Deep"}`, `"v"`
		for i := 0; i < maxMarkDepth; i++ {
			at = `{"type":"object","properties":{"p":` + at + `}}`
			deepArgs = `{"p":` + deepArgs + `}`
		}
		runParamCases(t, at, []paramCase{
			{"deepest read, header sent", deepArgs, map[string]string{"Mcp-Param-Deep": "v"}, true},
			{"deepest read, header missing", deepArgs, nil, false},
		})
	})
}

// TestCheckHeadersReadsLikeTheHandlers: the params members the headers mirror
// are read as the method handlers read them, and a body where readers could
// disagree is refused.
func TestCheckHeadersReadsLikeTheHandlers(t *testing.T) {
	schema := json.RawMessage(`{"type":"object","properties":{"region":{"type":"string","x-mcp-header":"Region"}}}`)
	lookup := func(name string) (json.RawMessage, bool) { return schema, name == "geo" }
	meta := Meta{ProtocolVersion: Version2026}
	for _, tc := range []struct {
		name, method, params, mcpName string
		ok                            bool
	}{
		{"exact", "tools/call", `{"name":"geo","arguments":{}}`, "geo", true},
		{"escaped name is the same name", "tools/call", `{"name":"geo","arguments":{}}`, "geo", true},
		{"name twice", "tools/call", `{"name":"other","name":"geo"}`, "geo", false},
		{"case variant", "tools/call", `{"NAME":"geo"}`, "geo", false},
		{"a look-alike letter is another name", "tools/call", `{"name":"geo","arguments":{},"_meta":{},"_metа":{}}`, "geo", true},
		{"long s reads as arguments", "tools/call", `{"name":"geo","argumentſ":{"region":"eu"}}`, "geo", false},
		{"dotless i reads as uri", "resources/read", `{"urı":"file:///b","uri":"file:///a"}`, "file:///a", false},
		{"_meta twice", "tools/list", `{"_meta":{},"_meta":{}}`, "", false},
		{"other members are not judged", "tools/call", `{"name":"geo","arguments":{},"x":1,"X":2}`, "geo", true},
		{"params not an object", "tools/call", `[1]`, "geo", false},
	} {
		h := http.Header{}
		h.Set(HeaderProtocolVersion, Version2026)
		h.Set(HeaderMethod, tc.method)
		if tc.mcpName != "" {
			h.Set(HeaderName, tc.mcpName)
		}
		err := CheckHeaders(h, tc.method, json.RawMessage(tc.params), meta, lookup)
		if tc.ok != (err == nil) {
			t.Errorf("%s: %v, want ok=%v", tc.name, err, tc.ok)
		}
	}
}

func TestNumberKey(t *testing.T) {
	same := [][]string{{"42", "42.0", "4.2e1", "420e-1", "0.42E+2"}, {"0", "-0", "0.0", "0e5"}, {"-7", "-7.00", "-70e-1"}}
	for _, group := range same {
		want, ok := numberKey(group[0])
		if !ok {
			t.Fatalf("%s is not a number", group[0])
		}
		for _, s := range group[1:] {
			if got, ok := numberKey(s); !ok || got != want {
				t.Errorf("numberKey(%s) = %q, %v; want %q like %s", s, got, ok, want, group[0])
			}
		}
	}
	for _, s := range []string{"0x2A", "1_000", "Infinity", "NaN", "+1", ".5", "5.", "01", "1e", "1e9999999999", " 1", ""} {
		if k, ok := numberKey(s); ok {
			t.Errorf("numberKey(%q) = %q, want refused", s, k)
		}
	}
	if a, _ := numberKey("42"); a == func() string { k, _ := numberKey("43"); return k }() {
		t.Error("42 and 43 share a key")
	}
}

func TestFoldName(t *testing.T) {
	// Every pair is one name to some case-insensitive parser.
	for _, pair := range [][2]string{{"name", "NAME"}, {"key", "Key"}, {"arguments", "argumentſ"}, {"uri", "urı"}, {"uri", "urİ"}} {
		if foldName(pair[0]) != foldName(pair[1]) {
			t.Errorf("%q and %q fold apart", pair[0], pair[1])
		}
	}
	if foldName("name") == foldName("nam") || foldName("_meta") == foldName("_metа") {
		t.Error("different names fold together")
	}
}
