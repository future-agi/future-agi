package restmcp

import (
	"encoding/json"
	"net/url"
	"reflect"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/manifest"
)

// TestRenamedQueryInputSentUnderWireName: Microsoft Graph's
// OData options ($top, $select, ...) are renamed by the example overlay, because
// a model provider may refuse "$" in a tool schema; each keeps its vendor
// name in x-wireName. The model sees and sends the readable name (top);
// the vendor gets the wire name ($top, with "$" unescaped), and neither
// x-wireName nor x-origin is in what tools/list serves.
func TestRenamedQueryInputSentUnderWireName(t *testing.T) {
	graph := newVendorFake(t, jsonAnswer(200, map[string]any{"value": []any{}}))
	rt := startRuntime(t, []*App{compileOne(t, cn4Catalog(t), "microsoft-outlook")},
		map[string]string{"https://graph.microsoft.com": graph.URL})

	res := rt.callTool(t, "/microsoft-outlook/mcp", testToken, "list_messages", map[string]any{
		"top": 5, "select": "subject,from", "filter": "isRead eq false", "orderby": "receivedDateTime desc", "search": `"budget"`})
	if res.IsError {
		t.Fatalf("list_messages: %s", res.text())
	}
	reqs := graph.requests()
	if len(reqs) != 1 {
		t.Fatalf("Graph got %d requests, want 1", len(reqs))
	}
	got, err := url.ParseQuery(reqs[0].Query)
	if err != nil {
		t.Fatalf("Graph got query %q: %v", reqs[0].Query, err)
	}
	want := url.Values{"$top": {"5"}, "$select": {"subject,from"}, "$filter": {"isRead eq false"},
		"$orderby": {"receivedDateTime desc"}, "$search": {`"budget"`}}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Graph got query %v (%q), want %v", got, reqs[0].Query, want)
	}
	if !strings.Contains(reqs[0].Query, "$top=5") {
		t.Errorf("Graph got %q: the wire name's $ is escaped", reqs[0].Query)
	}

	var listed json.RawMessage
	for _, page := range listAll(t, rt, "/microsoft-outlook/mcp") {
		for _, tool := range page.Tools {
			if tool.Name == "list_messages" {
				listed = tool.InputSchema
			}
		}
	}
	var schema struct {
		Properties map[string]json.RawMessage `json:"properties"`
	}
	if err := json.Unmarshal(listed, &schema); err != nil {
		t.Fatalf("list_messages inputSchema %s: %v", listed, err)
	}
	for _, name := range []string{"top", "select", "filter", "orderby", "search"} {
		if schema.Properties[name] == nil {
			t.Errorf("tools/list does not list input %s", name)
		}
	}
	for _, leaked := range []string{"x-wireName", "x-origin", "$top", "graph.microsoft.com"} {
		if strings.Contains(string(listed), leaked) {
			t.Errorf("tools/list serves %q in list_messages' inputSchema: %s", leaked, listed)
		}
	}
}

// TestListedSchemaStripsOnlyAnnotations: the runtime removes the generator's
// annotations (every x- keyword, at any depth) before tools/list, and
// nothing else. Values are data: an x- member inside const, enum, default
// or examples is part of the value, and a property named x-... is a name.
// Were const stripped, the model would be shown one value and refused for
// sending it, since the call is checked against the manifest's const.
func TestListedSchemaStripsOnlyAnnotations(t *testing.T) {
	const raw = `{
	  "type": "object",
	  "x-origin": "https://gmail.googleapis.com",
	  "required": ["body"],
	  "properties": {
	    "top": {"type": "integer", "x-wireName": "$top", "examples": [{"x-a": 1}]},
	    "x-request-id": {"type": "string", "description": "a property whose name starts with x-"},
	    "mode": {"type": "string", "enum": [{"x-v": 1}, {"x-v": 2}], "default": {"x-v": 1}},
	    "opts": {"type": "object", "dependentRequired": {"x-a": ["b"]}},
	    "tags": {"type": "array", "items": {"type": "object", "x-note": "n", "properties": {"x-k": {"type": "string", "x-wireName": "k"}}}},
	    "body": {"type": "object", "x-internal": true, "const": {"x-mode": "compact", "raw": true}}
	  },
	  "$defs": {"x-shared": {"type": "string", "x-ann": 1}}
	}`
	const want = `{
	  "type": "object",
	  "required": ["body"],
	  "properties": {
	    "top": {"type": "integer", "examples": [{"x-a": 1}]},
	    "x-request-id": {"type": "string", "description": "a property whose name starts with x-"},
	    "mode": {"type": "string", "enum": [{"x-v": 1}, {"x-v": 2}], "default": {"x-v": 1}},
	    "opts": {"type": "object", "dependentRequired": {"x-a": ["b"]}},
	    "tags": {"type": "array", "items": {"type": "object", "properties": {"x-k": {"type": "string"}}}},
	    "body": {"type": "object", "const": {"x-mode": "compact", "raw": true}}
	  },
	  "$defs": {"x-shared": {"type": "string"}}
	}`
	gmail, _ := gmailCopy(t)
	tool, err := compileTool(gmail, manifest.Tool{Name: "post_thing", InputSchema: json.RawMessage(raw),
		Operation: &manifest.Operation{Method: "POST", Path: "/gmail/v1/things"}}, retryPolicy{})
	if err != nil {
		t.Fatalf("compileTool: %v", err)
	}
	if got := decodeAny(tool.schema.listed); !reflect.DeepEqual(got, decodeAny(json.RawMessage(want))) {
		t.Errorf("listed inputSchema\n %s\nwant\n %s", tool.schema.listed, want)
	}
	// The model sends the const it was shown; the runtime accepts it.
	var shown struct {
		Properties struct {
			Body struct {
				Const json.RawMessage `json:"const"`
			} `json:"body"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(tool.schema.listed, &shown); err != nil {
		t.Fatal(err)
	}
	if _, err := tool.buildRequest(map[string]json.RawMessage{"body": shown.Properties.Body.Const}); err != nil {
		t.Errorf("sending the listed const %s is refused: %v", shown.Properties.Body.Const, err)
	}
}

// TestQueryInputCannotUseACredentialName: an api_key provider whose key
// (or a constant part) travels as a query parameter. A tool input sent
// under that name, by its own name or by x-wireName, would put a
// model-chosen value next to the user's key under the key's name, and a
// vendor that reads the first one would run the call as whatever account
// the model named. Such a tool is refused at load; the same provider
// without the clash compiles.
func TestQueryInputCannotUseACredentialName(t *testing.T) {
	keyInQuery := manifest.Auth{Kind: manifest.AuthAPIKey, CredentialParts: []manifest.CredentialPart{
		{In: "query", Name: "key", Secret: true}, {In: "query", Name: "api-version", Value: "2026-01", Secret: false}}}
	cases := map[string]func(map[string]any){
		"renamed onto the key": func(m map[string]any) { prop(m, "q")["x-wireName"] = "key" },
		"named as the key": func(m map[string]any) {
			m["properties"].(map[string]any)["key"] = map[string]any{"type": "string", "description": "x"}
		},
		"the key in other case": func(m map[string]any) { prop(m, "q")["x-wireName"] = "KEY" },
		"onto a constant part":  func(m map[string]any) { prop(m, "q")["x-wireName"] = "api-version" },
	}
	for name, edit := range cases {
		t.Run(name, func(t *testing.T) {
			app, p := gmailCopy(t)
			p.Auth = keyInQuery
			editSchema(t, toolNamed(t, p, "search_threads"), edit)
			_, err := Compile(&manifest.Catalog{Apps: []*manifest.App{app}})
			if err == nil || !strings.Contains(err.Error(), "tool search_threads: ") ||
				!strings.Contains(err.Error(), "the name of a credential part") {
				t.Fatalf("Compile error = %v, want search_threads refused for sending an input as a credential's name", err)
			}
		})
	}
	app, p := gmailCopy(t)
	p.Auth = keyInQuery
	if _, err := Compile(&manifest.Catalog{Apps: []*manifest.App{app}}); err != nil {
		t.Fatalf("the same provider with no clash: %v", err)
	}
	// A wrong refusal stops the whole catalog, so what is no
	// clash must keep compiling. A header part's name is no query name, and a
	// path input never goes out as a query parameter.
	noClash := map[string]struct {
		auth manifest.Auth
		edit func(*testing.T, *manifest.Tool)
	}{
		"a query input named like a header part": {
			manifest.Auth{Kind: manifest.AuthAPIKey, CredentialParts: []manifest.CredentialPart{{In: "header", Name: "X-Api-Key", Secret: true}}},
			func(t *testing.T, tool *manifest.Tool) {
				editSchema(t, tool, func(m map[string]any) { prop(m, "q")["x-wireName"] = "x-api-key" })
			},
		},
		"a path input named like a query part": {
			keyInQuery,
			func(t *testing.T, tool *manifest.Tool) {
				tool.Operation.Path = "/gmail/v1/users/me/threads/{key}"
				editSchema(t, tool, func(m map[string]any) {
					m["properties"].(map[string]any)["key"] = map[string]any{"type": "string", "description": "x"}
					required, _ := m["required"].([]any)
					m["required"] = append(required, "key")
				})
			},
		},
	}
	for name, c := range noClash {
		t.Run(name, func(t *testing.T) {
			app, p := gmailCopy(t)
			p.Auth = c.auth
			c.edit(t, toolNamed(t, p, "search_threads"))
			if _, err := Compile(&manifest.Catalog{Apps: []*manifest.App{app}}); err != nil {
				t.Fatalf("Compile refused what is no clash: %v", err)
			}
		})
	}
}

// prop is one property schema of an inputSchema being edited.
func prop(m map[string]any, name string) map[string]any {
	return m["properties"].(map[string]any)[name].(map[string]any)
}
