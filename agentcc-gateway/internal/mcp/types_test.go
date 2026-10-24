package mcp

import (
	"encoding/json"
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

// TestMCPRawFieldsFilled checks the Raw fields: the decoders keep the
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

	// Encoding leaves Raw out: a decoded value encodes to the known fields only.
	out, err := json.Marshal(list)
	if err != nil {
		t.Fatal(err)
	}
	if want := `{"tools":[{"name":"echo","inputSchema":{"type":"object"}}],"nextCursor":"c2"}`; string(out) != want {
		t.Fatalf("encoded tools/list = %s, want %s", out, want)
	}
	if out, _ := json.Marshal(res); string(out) != `{"content":[{"type":"text","text":"ok"}]}` {
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
