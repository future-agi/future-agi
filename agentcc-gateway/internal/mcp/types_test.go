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
