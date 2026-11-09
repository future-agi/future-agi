package mcp

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func TestMessageIsRequest(t *testing.T) {
	msg := &Message{Method: "tools/list", ID: json.RawMessage(`1`)}
	if !msg.IsRequest() {
		t.Fatal("expected IsRequest true")
	}
	if msg.IsNotification() {
		t.Fatal("expected IsNotification false")
	}
	if msg.IsResponse() {
		t.Fatal("expected IsResponse false")
	}
}

func TestMessageIsNotification(t *testing.T) {
	msg := &Message{Method: "notifications/initialized"}
	if !msg.IsNotification() {
		t.Fatal("expected IsNotification true")
	}
	if msg.IsRequest() {
		t.Fatal("expected IsRequest false")
	}
}

func TestMessageIsResponse(t *testing.T) {
	msg := &Message{ID: json.RawMessage(`1`), Result: json.RawMessage(`{}`)}
	if !msg.IsResponse() {
		t.Fatal("expected IsResponse true")
	}
}

func TestNewResponse(t *testing.T) {
	resp, err := NewResponse(json.RawMessage(`1`), map[string]string{"key": "val"})
	if err != nil {
		t.Fatal(err)
	}
	if resp.JSONRPC != "2.0" {
		t.Fatalf("expected 2.0, got %s", resp.JSONRPC)
	}
	if string(resp.ID) != `1` {
		t.Fatalf("expected id 1, got %s", string(resp.ID))
	}
	if resp.Result == nil {
		t.Fatal("expected result")
	}
}

func TestNewErrorResponse(t *testing.T) {
	resp := NewErrorResponse(json.RawMessage(`"abc"`), ErrCodeMethodNotFound, "not found")
	if resp.Error == nil {
		t.Fatal("expected error")
	}
	if resp.Error.Code != ErrCodeMethodNotFound {
		t.Fatalf("expected code %d, got %d", ErrCodeMethodNotFound, resp.Error.Code)
	}
	if resp.Error.Message != "not found" {
		t.Fatalf("expected 'not found', got %s", resp.Error.Message)
	}
}

func TestNewNotification(t *testing.T) {
	notif, err := NewNotification("notifications/tools/list_changed", nil)
	if err != nil {
		t.Fatal(err)
	}
	if notif.Method != "notifications/tools/list_changed" {
		t.Fatalf("expected method, got %s", notif.Method)
	}
	if notif.ID != nil {
		t.Fatal("notification should have no id")
	}
}

func TestRPCErrorString(t *testing.T) {
	e := &RPCError{Code: -32601, Message: "not found"}
	s := e.Error()
	if s != "JSON-RPC error -32601: not found" {
		t.Fatalf("unexpected: %s", s)
	}
}

func TestValidateToolName(t *testing.T) {
	tests := []struct {
		name  string
		valid bool
	}{
		{"create_issue", true},
		{"github.create-issue", true},
		{"A", true},
		{"", false},
		{"name with spaces", false},
		{"name@special", false},
		{string(make([]byte, 129)), false}, // too long
	}
	for _, tt := range tests {
		if got := ValidateToolName(tt.name); got != tt.valid {
			t.Errorf("ValidateToolName(%q) = %v, want %v", tt.name, got, tt.valid)
		}
	}
}

func TestToolMarshalRoundTrip(t *testing.T) {
	tool := Tool{
		Name:        "my_tool",
		Description: "does stuff",
		InputSchema: json.RawMessage(`{"type":"object"}`),
	}
	data, err := json.Marshal(tool)
	if err != nil {
		t.Fatal(err)
	}
	var decoded Tool
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.Name != tool.Name {
		t.Fatalf("name mismatch: %s vs %s", decoded.Name, tool.Name)
	}
}

func TestInitializeParamsRoundTrip(t *testing.T) {
	p := InitializeParams{
		ProtocolVersion: ProtocolVersion,
		ClientInfo:      Implementation{Name: "test", Version: "1.0"},
		Capabilities:    ClientCapabilities{},
	}
	data, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	var decoded InitializeParams
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.ProtocolVersion != ProtocolVersion {
		t.Fatalf("protocol version mismatch")
	}
	if decoded.ClientInfo.Name != "test" {
		t.Fatalf("client name mismatch")
	}
}

// TestMCPRawFieldsFilled checks M1's MT24 fields: today's decoders keep the
// exact upstream bytes in Raw on the tool, resource and prompt types and the
// result types, unknown fields included, and Raw never reaches the encoded
// output.
func TestMCPRawFieldsFilled(t *testing.T) {
	const tool = `{"name":"echo","title":"Echo","inputSchema":{"type":"object"},"_meta":{"x":1}}`
	var list ListToolsResult
	listJSON := `{"tools":[` + tool + `],"nextCursor":"c2"}`
	if err := json.Unmarshal([]byte(listJSON), &list); err != nil {
		t.Fatal(err)
	}
	if string(list.Raw) != listJSON || len(list.Tools) != 1 || string(list.Tools[0].Raw) != tool || list.Tools[0].Name != "echo" {
		t.Fatalf("tools/list: raw %s, tool raw %s; want the input bytes", list.Raw, list.Tools[0].Raw)
	}

	const call = `{"content":[{"type":"text","text":"ok"}],"structuredContent":{"n":2},"isError":false}`
	var res ToolCallResult
	if err := json.Unmarshal([]byte(call), &res); err != nil {
		t.Fatal(err)
	}
	if string(res.Raw) != call || len(res.Content) != 1 {
		t.Fatalf("tools/call raw = %s, want %s", res.Raw, call)
	}

	const resource = `{"uri":"file:///a","name":"a","size":3}`
	resList := `{"resources":[` + resource + `]}`
	var rl ListResourcesResult
	if err := json.Unmarshal([]byte(resList), &rl); err != nil {
		t.Fatal(err)
	}
	if string(rl.Raw) != resList || string(rl.Resources[0].Raw) != resource {
		t.Fatalf("resources/list raw = %s / %s", rl.Raw, rl.Resources[0].Raw)
	}
	const read = `{"contents":[{"uri":"file:///a","text":"abc"}],"_meta":{"r":1}}`
	var rr ReadResourceResult
	if err := json.Unmarshal([]byte(read), &rr); err != nil || string(rr.Raw) != read {
		t.Fatalf("resources/read raw = %s, %v", rr.Raw, err)
	}

	const prompt = `{"name":"greet","title":"Greet","arguments":[{"name":"who"}]}`
	promptList := `{"prompts":[` + prompt + `]}`
	var pl ListPromptsResult
	if err := json.Unmarshal([]byte(promptList), &pl); err != nil {
		t.Fatal(err)
	}
	if string(pl.Raw) != promptList || string(pl.Prompts[0].Raw) != prompt || pl.Prompts[0].Arguments[0].Name != "who" {
		t.Fatalf("prompts/list raw = %s / %s", pl.Raw, pl.Prompts[0].Raw)
	}
	const get = `{"messages":[{"role":"user","content":{"type":"text","text":"hi"}}],"_meta":{"p":1}}`
	var gp GetPromptResult
	if err := json.Unmarshal([]byte(get), &gp); err != nil || string(gp.Raw) != get {
		t.Fatalf("prompts/get raw = %s, %v", gp.Raw, err)
	}

	// Raw is never written: a decoded value encodes to its typed fields only,
	// which M11 grew additively (title, _meta, structuredContent), never to a
	// second copy of the upstream bytes.
	out, err := json.Marshal(list)
	if err != nil {
		t.Fatal(err)
	}
	if want := `{"tools":[{"name":"echo","inputSchema":{"type":"object"},"title":"Echo","_meta":{"x":1}}],"nextCursor":"c2"}`; string(out) != want {
		t.Fatalf("encoded tools/list = %s, want %s", out, want)
	}
	if out, _ := json.Marshal(res); string(out) != `{"content":[{"type":"text","text":"ok"}],"structuredContent":{"n":2}}` {
		t.Fatalf("encoded tools/call = %s", out)
	}

	// The raw copy is private: changing the input buffer after decoding does
	// not change Raw.
	buf := []byte(tool)
	var tt Tool
	if err := json.Unmarshal(buf, &tt); err != nil {
		t.Fatal(err)
	}
	buf[2] = 'X'
	if string(tt.Raw) != tool {
		t.Fatal("Raw aliases the decoder's input buffer")
	}

	// A JSON null leaves the value alone, as json.Unmarshal does.
	keep := Tool{Name: "kept"}
	if err := json.Unmarshal([]byte(`null`), &keep); err != nil || keep.Name != "kept" || keep.Raw != nil {
		t.Fatalf("null decoded into %+v, %v; want the value unchanged", keep, err)
	}
}

// m1TypeFields is the exported field set of types.go as M1 left it (W2b, the
// PIN 98ab17b89a): type, field, Go type and json tag. Fields only grow after
// W2b (MT24): M10, M13, M15 and M17 compile against these in the same wave.
var m1TypeFields = [][4]string{
	{"Message", "JSONRPC", "string", "jsonrpc"},
	{"Message", "ID", "json.RawMessage", "id,omitempty"},
	{"Message", "Method", "string", "method,omitempty"},
	{"Message", "Params", "json.RawMessage", "params,omitempty"},
	{"Message", "Result", "json.RawMessage", "result,omitempty"},
	{"Message", "Error", "*mcp.RPCError", "error,omitempty"},
	{"RPCError", "Code", "int", "code"},
	{"RPCError", "Message", "string", "message"},
	{"RPCError", "Data", "json.RawMessage", "data,omitempty"},
	{"InitializeParams", "ProtocolVersion", "string", "protocolVersion"},
	{"InitializeParams", "Capabilities", "mcp.ClientCapabilities", "capabilities"},
	{"InitializeParams", "ClientInfo", "mcp.Implementation", "clientInfo"},
	{"InitializeResult", "ProtocolVersion", "string", "protocolVersion"},
	{"InitializeResult", "Capabilities", "mcp.ServerCapabilities", "capabilities"},
	{"InitializeResult", "ServerInfo", "mcp.Implementation", "serverInfo"},
	{"InitializeResult", "Instructions", "string", "instructions,omitempty"},
	{"Implementation", "Name", "string", "name"},
	{"Implementation", "Version", "string", "version"},
	{"ClientCapabilities", "Sampling", "*mcp.SamplingCapability", "sampling,omitempty"},
	{"ClientCapabilities", "Roots", "*mcp.RootsCapability", "roots,omitempty"},
	{"RootsCapability", "ListChanged", "bool", "listChanged,omitempty"},
	{"ServerCapabilities", "Tools", "*mcp.ToolsCapability", "tools,omitempty"},
	{"ServerCapabilities", "Resources", "*mcp.ResourcesCapability", "resources,omitempty"},
	{"ServerCapabilities", "Prompts", "*mcp.PromptsCapability", "prompts,omitempty"},
	{"ToolsCapability", "ListChanged", "bool", "listChanged,omitempty"},
	{"ResourcesCapability", "Subscribe", "bool", "subscribe,omitempty"},
	{"ResourcesCapability", "ListChanged", "bool", "listChanged,omitempty"},
	{"PromptsCapability", "ListChanged", "bool", "listChanged,omitempty"},
	{"Tool", "Name", "string", "name"},
	{"Tool", "Description", "string", "description,omitempty"},
	{"Tool", "InputSchema", "json.RawMessage", "inputSchema"},
	{"Tool", "OutputSchema", "json.RawMessage", "outputSchema,omitempty"},
	{"Tool", "Annotations", "*mcp.ToolAnnotations", "annotations,omitempty"},
	{"Tool", "Raw", "json.RawMessage", "-"},
	{"ToolAnnotations", "Title", "string", "title,omitempty"},
	{"ToolAnnotations", "ReadOnlyHint", "*bool", "readOnlyHint,omitempty"},
	{"ToolAnnotations", "DestructiveHint", "*bool", "destructiveHint,omitempty"},
	{"ToolAnnotations", "IdempotentHint", "*bool", "idempotentHint,omitempty"},
	{"ToolAnnotations", "OpenWorldHint", "*bool", "openWorldHint,omitempty"},
	{"ToolCallParams", "Name", "string", "name"},
	{"ToolCallParams", "Arguments", "map[string]interface {}", "arguments,omitempty"},
	{"ToolCallResult", "Content", "[]mcp.ContentPart", "content"},
	{"ToolCallResult", "IsError", "bool", "isError,omitempty"},
	{"ToolCallResult", "Raw", "json.RawMessage", "-"},
	{"ContentPart", "Type", "string", "type"},
	{"ContentPart", "Text", "string", "text,omitempty"},
	{"ContentPart", "Data", "string", "data,omitempty"},
	{"ContentPart", "MIMEType", "string", "mimeType,omitempty"},
	{"ListToolsResult", "Tools", "[]mcp.Tool", "tools"},
	{"ListToolsResult", "NextCursor", "string", "nextCursor,omitempty"},
	{"ListToolsResult", "Raw", "json.RawMessage", "-"},
	{"Resource", "URI", "string", "uri"},
	{"Resource", "Name", "string", "name"},
	{"Resource", "Description", "string", "description,omitempty"},
	{"Resource", "MIMEType", "string", "mimeType,omitempty"},
	{"Resource", "Raw", "json.RawMessage", "-"},
	{"ResourceTemplate", "URITemplate", "string", "uriTemplate"},
	{"ResourceTemplate", "Name", "string", "name"},
	{"ResourceTemplate", "Description", "string", "description,omitempty"},
	{"ResourceTemplate", "MIMEType", "string", "mimeType,omitempty"},
	{"ListResourcesResult", "Resources", "[]mcp.Resource", "resources"},
	{"ListResourcesResult", "Raw", "json.RawMessage", "-"},
	{"ReadResourceParams", "URI", "string", "uri"},
	{"ReadResourceResult", "Contents", "[]mcp.ResourceContent", "contents"},
	{"ReadResourceResult", "Raw", "json.RawMessage", "-"},
	{"ResourceContent", "URI", "string", "uri"},
	{"ResourceContent", "MIMEType", "string", "mimeType,omitempty"},
	{"ResourceContent", "Text", "string", "text,omitempty"},
	{"ResourceContent", "Blob", "string", "blob,omitempty"},
	{"Prompt", "Name", "string", "name"},
	{"Prompt", "Description", "string", "description,omitempty"},
	{"Prompt", "Arguments", "[]mcp.PromptArgument", "arguments,omitempty"},
	{"Prompt", "Raw", "json.RawMessage", "-"},
	{"PromptArgument", "Name", "string", "name"},
	{"PromptArgument", "Description", "string", "description,omitempty"},
	{"PromptArgument", "Required", "bool", "required,omitempty"},
	{"ListPromptsResult", "Prompts", "[]mcp.Prompt", "prompts"},
	{"ListPromptsResult", "Raw", "json.RawMessage", "-"},
	{"GetPromptParams", "Name", "string", "name"},
	{"GetPromptParams", "Arguments", "map[string]string", "arguments,omitempty"},
	{"GetPromptResult", "Description", "string", "description,omitempty"},
	{"GetPromptResult", "Messages", "[]mcp.PromptMessage", "messages"},
	{"GetPromptResult", "Raw", "json.RawMessage", "-"},
	{"PromptMessage", "Role", "string", "role"},
	{"PromptMessage", "Content", "mcp.ContentPart", "content"},
}

// m1Types are the struct types of types.go at the PIN, by name.
var m1Types = map[string]any{
	"Message":             Message{},
	"RPCError":            RPCError{},
	"InitializeParams":    InitializeParams{},
	"InitializeResult":    InitializeResult{},
	"Implementation":      Implementation{},
	"ClientCapabilities":  ClientCapabilities{},
	"RootsCapability":     RootsCapability{},
	"ServerCapabilities":  ServerCapabilities{},
	"ToolsCapability":     ToolsCapability{},
	"ResourcesCapability": ResourcesCapability{},
	"PromptsCapability":   PromptsCapability{},
	"Tool":                Tool{},
	"ToolAnnotations":     ToolAnnotations{},
	"ToolCallParams":      ToolCallParams{},
	"ToolCallResult":      ToolCallResult{},
	"ContentPart":         ContentPart{},
	"ListToolsResult":     ListToolsResult{},
	"Resource":            Resource{},
	"ResourceTemplate":    ResourceTemplate{},
	"ListResourcesResult": ListResourcesResult{},
	"ReadResourceParams":  ReadResourceParams{},
	"ReadResourceResult":  ReadResourceResult{},
	"ResourceContent":     ResourceContent{},
	"Prompt":              Prompt{},
	"PromptArgument":      PromptArgument{},
	"ListPromptsResult":   ListPromptsResult{},
	"GetPromptParams":     GetPromptParams{},
	"GetPromptResult":     GetPromptResult{},
	"PromptMessage":       PromptMessage{},
}

// TestTypesFieldsAdditiveOnly fails when a field types.go had at the PIN is
// renamed, removed, retyped or given another json tag; new fields pass.
func TestTypesFieldsAdditiveOnly(t *testing.T) {
	for _, f := range m1TypeFields {
		v, ok := m1Types[f[0]]
		if !ok {
			t.Fatalf("type %s is missing from the table", f[0])
		}
		field, ok := reflect.TypeOf(v).FieldByName(f[1])
		if !ok {
			t.Errorf("%s.%s was removed or renamed", f[0], f[1])
			continue
		}
		if got := field.Type.String(); got != f[2] {
			t.Errorf("%s.%s is %s, was %s", f[0], f[1], got, f[2])
		}
		if got := field.Tag.Get("json"); got != f[3] {
			t.Errorf("%s.%s has json tag %q, was %q", f[0], f[1], got, f[3])
		}
	}
	// The pass-through fields M11 added are there (additive only).
	for _, f := range [][2]string{{"Tool", "Title"}, {"Tool", "Meta"}, {"ToolCallResult", "StructuredContent"}, {"ContentPart", "URI"}, {"Resource", "Title"}, {"Prompt", "Title"},
		{"Tool", "GatewayMeta"}, {"Resource", "GatewayMeta"}, {"Prompt", "GatewayMeta"}} {
		if _, ok := reflect.TypeOf(m1Types[f[0]]).FieldByName(f[1]); !ok {
			t.Errorf("%s.%s is missing", f[0], f[1])
		}
	}
}

// TestSizeNeverFailsTheDecode: a resource or resource link whose size is not
// an integer literal (2048.0 and 1.5e3 are integers to JSON Schema, and the
// SDKs type size as a number) decodes, keeps the size as sent and loses
// nothing else (review W3b-r3 P2: *int64 failed the whole result or list).
func TestSizeNeverFailsTheDecode(t *testing.T) {
	for _, size := range []string{`2048`, `2048.0`, `1.5e3`, `"2048"`, `true`, `null`} {
		var call ToolCallResult
		in := `{"content":[{"type":"text","text":"t"},{"type":"resource_link","uri":"x","name":"n","size":` + size + `}]}`
		if err := json.Unmarshal([]byte(in), &call); err != nil || len(call.Content) != 2 || len(call.Raw) == 0 {
			t.Errorf("tools/call result with size %s: %v, %d parts", size, err, len(call.Content))
			continue
		}
		if out, _ := json.Marshal(call.Content[1]); size != `null` && !strings.Contains(string(out), `"size":`+size) {
			t.Errorf("resource link size %s kept as %s", size, out)
		}
		var list ListResourcesResult
		in = `{"resources":[{"uri":"a","name":"a"},{"uri":"b","name":"b","size":` + size + `}]}`
		if err := json.Unmarshal([]byte(in), &list); err != nil || len(list.Resources) != 2 {
			t.Errorf("resources/list with size %s: %v, %d resources", size, err, len(list.Resources))
			continue
		}
		if out, _ := json.Marshal(list.Resources[1]); size != `null` && !strings.Contains(string(out), `"size":`+size) {
			t.Errorf("resource size %s kept as %s", size, out)
		}
	}
	// A typed value marshals its size back as it came, and omits none.
	var part ContentPart
	if err := json.Unmarshal([]byte(`{"type":"resource_link","uri":"x","name":"n","size":1.5e3}`), &part); err != nil {
		t.Fatalf("resource link with size 1.5e3: %v", err)
	}
	if out, err := json.Marshal(part); err != nil || !strings.Contains(string(out), `"size":1.5e3`) {
		t.Errorf("typed resource link = %s, %v", out, err)
	}
}
