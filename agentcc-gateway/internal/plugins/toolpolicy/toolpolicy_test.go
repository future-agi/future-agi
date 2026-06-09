package toolpolicy

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeTool(name string) models.Tool {
	return models.Tool{
		Type: "function",
		Function: models.ToolFunction{
			Name:        name,
			Description: "test tool " + name,
			Parameters:  json.RawMessage(`{"type":"object"}`),
		},
	}
}

func makeRC(tools ...string) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.Request = &models.ChatCompletionRequest{
		Model:    "gpt-4",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello"`)}},
	}
	for _, t := range tools {
		rc.Request.Tools = append(rc.Request.Tools, makeTool(t))
	}
	return rc
}

func TestDisabled(t *testing.T) {
	p := New(config.ToolPolicyConfig{Enabled: false}, nil)
	rc := makeRC("get_weather", "send_email")
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("expected continue, got error: %v", result.Error)
	}
	if len(rc.Request.Tools) != 2 {
		t.Fatalf("expected 2 tools, got %d", len(rc.Request.Tools))
	}
}

func TestNoTools(t *testing.T) {
	p := New(config.ToolPolicyConfig{Enabled: true}, nil)
	rc := makeRC() // no tools
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("expected continue, got error: %v", result.Error)
	}
}

func TestGlobalDenyStrip(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled:       true,
		DefaultAction: "strip",
		Deny:          []string{"send_email", "delete_file"},
	}, nil)
	rc := makeRC("get_weather", "send_email", "search", "delete_file")
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("expected continue, got error: %v", result.Error)
	}
	if len(rc.Request.Tools) != 2 {
		t.Fatalf("expected 2 tools after strip, got %d", len(rc.Request.Tools))
	}
	if rc.Request.Tools[0].Function.Name != "get_weather" || rc.Request.Tools[1].Function.Name != "search" {
		t.Fatalf("wrong tools remaining: %v", rc.Request.Tools)
	}
	if rc.Metadata["tools_filtered"] != "2" {
		t.Fatalf("expected tools_filtered=2, got %q", rc.Metadata["tools_filtered"])
	}
	if rc.Metadata["tools_requested"] != "4" {
		t.Fatalf("expected tools_requested=4, got %q", rc.Metadata["tools_requested"])
	}
}

func TestGlobalDenyReject(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled:       true,
		DefaultAction: "reject",
		Deny:          []string{"send_email"},
	}, nil)
	rc := makeRC("get_weather", "send_email")
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for rejected tool")
	}
}

func TestGlobalAllowStrip(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled:       true,
		DefaultAction: "strip",
		Allow:         []string{"get_weather", "search"},
	}, nil)
	rc := makeRC("get_weather", "send_email", "search")
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("expected continue, got error: %v", result.Error)
	}
	if len(rc.Request.Tools) != 2 {
		t.Fatalf("expected 2 tools, got %d", len(rc.Request.Tools))
	}
}

func TestGlobalAllowReject(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled:       true,
		DefaultAction: "reject",
		Allow:         []string{"get_weather"},
	}, nil)
	rc := makeRC("get_weather", "send_email")
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for tool not in allow list")
	}
}

func TestMaxToolsPerRequest(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled:            true,
		MaxToolsPerRequest: 2,
	}, nil)
	rc := makeRC("a", "b", "c")
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for exceeding max tools")
	}
}

func TestMaxToolsPerRequestOk(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled:            true,
		MaxToolsPerRequest: 3,
	}, nil)
	rc := makeRC("a", "b", "c")
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("expected continue, got error: %v", result.Error)
	}
}

func TestPerKeyDenyStrip(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled:       true,
		DefaultAction: "strip",
	}, nil)
	rc := makeRC("get_weather", "send_email", "search")
	rc.Metadata["auth_denied_tools"] = "send_email"
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("expected continue, got error: %v", result.Error)
	}
	if len(rc.Request.Tools) != 2 {
		t.Fatalf("expected 2 tools, got %d", len(rc.Request.Tools))
	}
}

func TestPerKeyAllowStrip(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled:       true,
		DefaultAction: "strip",
	}, nil)
	rc := makeRC("get_weather", "send_email", "search")
	rc.Metadata["auth_allowed_tools"] = "get_weather,search"
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("expected continue, got error: %v", result.Error)
	}
	if len(rc.Request.Tools) != 2 {
		t.Fatalf("expected 2 tools, got %d", len(rc.Request.Tools))
	}
}

func TestPerKeyDenyReject(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled:       true,
		DefaultAction: "reject",
	}, nil)
	rc := makeRC("get_weather", "send_email")
	rc.Metadata["auth_denied_tools"] = "send_email"
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error == nil {
		t.Fatal("expected error for per-key denied tool")
	}
}

func TestDenyTakesPrecedenceOverAllow(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled:       true,
		DefaultAction: "strip",
		Allow:         []string{"send_email", "get_weather"},
		Deny:          []string{"send_email"},
	}, nil)
	rc := makeRC("get_weather", "send_email")
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("expected continue, got error: %v", result.Error)
	}
	if len(rc.Request.Tools) != 1 {
		t.Fatalf("expected 1 tool (deny takes precedence), got %d", len(rc.Request.Tools))
	}
	if rc.Request.Tools[0].Function.Name != "get_weather" {
		t.Fatalf("expected get_weather, got %s", rc.Request.Tools[0].Function.Name)
	}
}

func TestAllToolsAllowed(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled: true,
	}, nil)
	rc := makeRC("a", "b", "c")
	result := p.ProcessRequest(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("expected continue, got error: %v", result.Error)
	}
	if len(rc.Request.Tools) != 3 {
		t.Fatalf("expected 3 tools, got %d", len(rc.Request.Tools))
	}
	if rc.Metadata["tools_filtered"] != "" {
		t.Fatalf("expected no tools_filtered, got %q", rc.Metadata["tools_filtered"])
	}
}

func TestDefaultActionDefaultsToStrip(t *testing.T) {
	p := New(config.ToolPolicyConfig{
		Enabled: true,
		Deny:    []string{"bad_tool"},
	}, nil)
	if p.defaultAction != "strip" {
		t.Fatalf("expected default action 'strip', got %q", p.defaultAction)
	}
}

func TestPluginMetadata(t *testing.T) {
	p := New(config.ToolPolicyConfig{Enabled: true}, nil)
	if p.Name() != "toolpolicy" {
		t.Fatalf("expected name 'toolpolicy', got %q", p.Name())
	}
	if p.Priority() != 60 {
		t.Fatalf("expected priority 60, got %d", p.Priority())
	}
}

func TestProcessResponseNoop(t *testing.T) {
	p := New(config.ToolPolicyConfig{Enabled: true}, nil)
	rc := makeRC("a")
	result := p.ProcessResponse(context.Background(), rc)
	if result.Error != nil {
		t.Fatalf("expected continue, got error: %v", result.Error)
	}
}

func TestParseCSV(t *testing.T) {
	tests := []struct {
		input string
		want  int
	}{
		{"", 0},
		{"a,b,c", 3},
		{" a , b ", 2},
		{"single", 1},
	}
	for _, tt := range tests {
		got := parseCSV(tt.input)
		if tt.want == 0 && got != nil {
			t.Errorf("parseCSV(%q): expected nil, got %v", tt.input, got)
		}
		if tt.want > 0 && len(got) != tt.want {
			t.Errorf("parseCSV(%q): expected %d entries, got %d", tt.input, tt.want, len(got))
		}
	}
}
