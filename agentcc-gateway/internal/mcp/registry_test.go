package mcp

import (
	"encoding/json"
	"testing"
)

func TestRegistryRegisterAndResolve(t *testing.T) {
	r := NewRegistry("_")

	tools := []Tool{
		{Name: "create_issue", Description: "Create an issue", InputSchema: json.RawMessage(`{}`)},
		{Name: "list_repos", Description: "List repos", InputSchema: json.RawMessage(`{}`)},
	}
	r.RegisterServer("github", tools)

	// Should have 2 tools.
	if r.ToolCount() != 2 {
		t.Fatalf("expected 2 tools, got %d", r.ToolCount())
	}

	// Resolve by namespaced name.
	rt, ok := r.ResolveTool("github_create_issue")
	if !ok {
		t.Fatal("expected to find github_create_issue")
	}
	if rt.Server != "github" {
		t.Fatalf("expected server 'github', got %s", rt.Server)
	}
	if rt.OrigName != "create_issue" {
		t.Fatalf("expected orig name 'create_issue', got %s", rt.OrigName)
	}

	// Non-existent tool.
	_, ok = r.ResolveTool("github_nonexistent")
	if ok {
		t.Fatal("expected not found")
	}
}

func TestRegistryUnregisterServer(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterServer("github", []Tool{
		{Name: "create_issue", InputSchema: json.RawMessage(`{}`)},
	})
	r.RegisterServer("slack", []Tool{
		{Name: "send_message", InputSchema: json.RawMessage(`{}`)},
	})

	if r.ToolCount() != 2 {
		t.Fatalf("expected 2 tools, got %d", r.ToolCount())
	}

	r.UnregisterServer("github")
	if r.ToolCount() != 1 {
		t.Fatalf("expected 1 tool, got %d", r.ToolCount())
	}

	_, ok := r.ResolveTool("github_create_issue")
	if ok {
		t.Fatal("expected github tool removed")
	}

	_, ok = r.ResolveTool("slack_send_message")
	if !ok {
		t.Fatal("expected slack tool still present")
	}
}

func TestRegistryReRegisterReplaces(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterServer("github", []Tool{
		{Name: "create_issue", InputSchema: json.RawMessage(`{}`)},
		{Name: "list_repos", InputSchema: json.RawMessage(`{}`)},
	})

	// Re-register with different tools.
	r.RegisterServer("github", []Tool{
		{Name: "create_pr", InputSchema: json.RawMessage(`{}`)},
	})

	if r.ToolCount() != 1 {
		t.Fatalf("expected 1 tool after re-register, got %d", r.ToolCount())
	}

	_, ok := r.ResolveTool("github_create_issue")
	if ok {
		t.Fatal("old tool should be gone")
	}

	_, ok = r.ResolveTool("github_create_pr")
	if !ok {
		t.Fatal("new tool should be present")
	}
}

func TestRegistryListTools(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterServer("a", []Tool{{Name: "t1", InputSchema: json.RawMessage(`{}`)}})
	r.RegisterServer("b", []Tool{{Name: "t2", InputSchema: json.RawMessage(`{}`)}})

	tools := r.ListTools()
	if len(tools) != 2 {
		t.Fatalf("expected 2 tools, got %d", len(tools))
	}
}

func TestRegistryListMCPTools(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterServer("github", []Tool{
		{Name: "create_issue", Description: "Create", InputSchema: json.RawMessage(`{"type":"object"}`)},
	})

	mcpTools := r.ListMCPTools()
	if len(mcpTools) != 1 {
		t.Fatalf("expected 1 tool, got %d", len(mcpTools))
	}
	if mcpTools[0].Name != "github_create_issue" {
		t.Fatalf("expected namespaced name, got %s", mcpTools[0].Name)
	}
	if mcpTools[0].Description != "Create" {
		t.Fatalf("expected description preserved")
	}
}

func TestRegistryServerTools(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterServer("a", []Tool{{Name: "t1", InputSchema: json.RawMessage(`{}`)}})
	r.RegisterServer("b", []Tool{{Name: "t2", InputSchema: json.RawMessage(`{}`)}})

	aTools := r.ServerTools("a")
	if len(aTools) != 1 {
		t.Fatalf("expected 1 tool for server a, got %d", len(aTools))
	}

	cTools := r.ServerTools("c")
	if cTools != nil {
		t.Fatal("expected nil for unknown server")
	}
}

func TestRegistryServerIDs(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterServer("alpha", []Tool{{Name: "t1", InputSchema: json.RawMessage(`{}`)}})
	r.RegisterServer("beta", []Tool{{Name: "t2", InputSchema: json.RawMessage(`{}`)}})

	ids := r.ServerIDs()
	if len(ids) != 2 {
		t.Fatalf("expected 2 server IDs, got %d", len(ids))
	}
}

func TestRegistryCustomSeparator(t *testing.T) {
	r := NewRegistry(".")

	r.RegisterServer("github", []Tool{
		{Name: "create_issue", InputSchema: json.RawMessage(`{}`)},
	})

	_, ok := r.ResolveTool("github.create_issue")
	if !ok {
		t.Fatal("expected to find tool with dot separator")
	}
}

func TestToolStatsRecordCall(t *testing.T) {
	r := NewRegistry("_")
	r.RegisterServer("s", []Tool{{Name: "t", InputSchema: json.RawMessage(`{}`)}})

	rt, _ := r.ResolveTool("s_t")
	rt.Stats.RecordCall(100_000_000, false) // 100ms
	rt.Stats.RecordCall(200_000_000, true)  // 200ms, error

	if rt.Stats.CallCount.Load() != 2 {
		t.Fatalf("expected 2 calls, got %d", rt.Stats.CallCount.Load())
	}
	if rt.Stats.ErrorCount.Load() != 1 {
		t.Fatalf("expected 1 error, got %d", rt.Stats.ErrorCount.Load())
	}

	avg := rt.Stats.AvgLatencyMs()
	if avg < 140 || avg > 160 {
		t.Fatalf("expected avg ~150ms, got %f", avg)
	}

	errRate := rt.Stats.ErrorRate()
	if errRate < 0.49 || errRate > 0.51 {
		t.Fatalf("expected error rate ~0.5, got %f", errRate)
	}
}

// ============================================================
// 14.7.6: Resources & Prompts Registry
// ============================================================

func TestRegistryRegisterResources(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterResources("files", []Resource{
		{URI: "file:///a.txt", Name: "A", MIMEType: "text/plain"},
		{URI: "file:///b.json", Name: "B", MIMEType: "application/json"},
	})

	if r.ResourceCount() != 2 {
		t.Fatalf("expected 2 resources, got %d", r.ResourceCount())
	}

	resources := r.ListResources()
	if len(resources) != 2 {
		t.Fatalf("expected 2 resources in list, got %d", len(resources))
	}
}

func TestRegistryResolveResource(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterResources("files", []Resource{
		{URI: "file:///a.txt", Name: "A"},
	})

	res, ok := r.ResolveResource("file:///a.txt")
	if !ok {
		t.Fatal("expected to find resource")
	}
	if res.Server != "files" {
		t.Fatalf("expected server 'files', got %s", res.Server)
	}

	_, ok = r.ResolveResource("file:///nonexistent")
	if ok {
		t.Fatal("expected not found")
	}
}

func TestRegistryReRegisterResourcesReplaces(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterResources("files", []Resource{
		{URI: "file:///a.txt", Name: "A"},
		{URI: "file:///b.txt", Name: "B"},
	})

	// Re-register with different set.
	r.RegisterResources("files", []Resource{
		{URI: "file:///c.txt", Name: "C"},
	})

	if r.ResourceCount() != 1 {
		t.Fatalf("expected 1 resource after re-register, got %d", r.ResourceCount())
	}

	_, ok := r.ResolveResource("file:///a.txt")
	if ok {
		t.Fatal("old resource should be gone")
	}

	_, ok = r.ResolveResource("file:///c.txt")
	if !ok {
		t.Fatal("new resource should be present")
	}
}

func TestRegistryMultipleServerResources(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterResources("files", []Resource{
		{URI: "file:///a.txt", Name: "A"},
	})
	r.RegisterResources("db", []Resource{
		{URI: "db://users", Name: "Users"},
	})

	if r.ResourceCount() != 2 {
		t.Fatalf("expected 2 resources from 2 servers, got %d", r.ResourceCount())
	}
}

func TestRegistryRegisterPrompts(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterPrompts("ai", []Prompt{
		{Name: "summarize", Description: "Summarize text", Arguments: []PromptArgument{{Name: "text", Required: true}}},
		{Name: "translate", Description: "Translate text"},
	})

	if r.PromptCount() != 2 {
		t.Fatalf("expected 2 prompts, got %d", r.PromptCount())
	}

	prompts := r.ListPrompts()
	if len(prompts) != 2 {
		t.Fatalf("expected 2 prompts in list, got %d", len(prompts))
	}
}

func TestRegistryResolvePrompt(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterPrompts("ai", []Prompt{
		{Name: "summarize", Description: "Summarize"},
	})

	// Prompts are namespaced: "ai_summarize".
	p, ok := r.ResolvePrompt("ai_summarize")
	if !ok {
		t.Fatal("expected to find prompt ai_summarize")
	}
	if p.Server != "ai" {
		t.Fatalf("expected server 'ai', got %s", p.Server)
	}

	_, ok = r.ResolvePrompt("nonexistent_prompt")
	if ok {
		t.Fatal("expected not found")
	}
}

func TestRegistryReRegisterPromptsReplaces(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterPrompts("ai", []Prompt{
		{Name: "old_prompt"},
	})

	r.RegisterPrompts("ai", []Prompt{
		{Name: "new_prompt"},
	})

	if r.PromptCount() != 1 {
		t.Fatalf("expected 1 prompt after re-register, got %d", r.PromptCount())
	}

	_, ok := r.ResolvePrompt("ai_old_prompt")
	if ok {
		t.Fatal("old prompt should be gone")
	}

	_, ok = r.ResolvePrompt("ai_new_prompt")
	if !ok {
		t.Fatal("new prompt should be present")
	}
}

func TestRegistryPromptNamespacing(t *testing.T) {
	r := NewRegistry(".")

	r.RegisterPrompts("server", []Prompt{
		{Name: "my_prompt", Description: "Test"},
	})

	// With dot separator: "server.my_prompt".
	p, ok := r.ResolvePrompt("server.my_prompt")
	if !ok {
		t.Fatal("expected to find prompt with dot separator")
	}
	if p.Prompt.Name != "server.my_prompt" {
		t.Fatalf("expected namespaced name 'server.my_prompt', got %s", p.Prompt.Name)
	}
}

// ============================================================
// 14.7.7: Tool Versioning & Deprecation (Registry)
// ============================================================

func TestRegistrySetToolVersions(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterServer("srv", []Tool{
		{Name: "v1_api", InputSchema: json.RawMessage(`{}`)},
		{Name: "v2_api", InputSchema: json.RawMessage(`{}`)},
	})

	r.SetToolVersions(map[string]ToolVersionInfo{
		"srv_v1_api": {Version: "1.0", Deprecated: true, DeprecationMessage: "Use v2", ReplacedBy: "srv_v2_api"},
		"srv_v2_api": {Version: "2.0", Deprecated: false},
	})

	v1, ok := r.GetToolVersion("srv_v1_api")
	if !ok {
		t.Fatal("expected version info for srv_v1_api")
	}
	if !v1.Deprecated {
		t.Fatal("expected srv_v1_api to be deprecated")
	}
	if v1.ReplacedBy != "srv_v2_api" {
		t.Fatalf("expected replaced_by='srv_v2_api', got %s", v1.ReplacedBy)
	}

	v2, ok := r.GetToolVersion("srv_v2_api")
	if !ok {
		t.Fatal("expected version info for srv_v2_api")
	}
	if v2.Deprecated {
		t.Fatal("expected srv_v2_api to not be deprecated")
	}
	if v2.Version != "2.0" {
		t.Fatalf("expected version '2.0', got %s", v2.Version)
	}

	// Non-existent tool.
	_, ok = r.GetToolVersion("nonexistent")
	if ok {
		t.Fatal("expected not found for nonexistent tool version")
	}
}

func TestRegistryListToolsJSON_WithVersions(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterServer("srv", []Tool{
		{Name: "tool1", Description: "Tool 1", InputSchema: json.RawMessage(`{"type":"object"}`)},
		{Name: "tool2", Description: "Tool 2", InputSchema: json.RawMessage(`{"type":"object"}`)},
	})

	r.SetToolVersions(map[string]ToolVersionInfo{
		"srv_tool1": {Version: "1.0", Deprecated: true, DeprecationMessage: "Old", ReplacedBy: "srv_tool2"},
	})

	dtos := r.ListToolsJSON()
	if len(dtos) != 2 {
		t.Fatalf("expected 2 DTOs, got %d", len(dtos))
	}

	// Find tool1 DTO and verify version info merged.
	var tool1DTO *RegisteredToolJSON
	for i := range dtos {
		if dtos[i].Name == "srv_tool1" {
			tool1DTO = &dtos[i]
			break
		}
	}
	if tool1DTO == nil {
		t.Fatal("expected to find srv_tool1 in DTOs")
	}
	if tool1DTO.Version != "1.0" {
		t.Fatalf("expected version '1.0', got %s", tool1DTO.Version)
	}
	if !tool1DTO.Deprecated {
		t.Fatal("expected deprecated=true")
	}
	if tool1DTO.ReplacedBy != "srv_tool2" {
		t.Fatalf("expected replaced_by='srv_tool2', got %s", tool1DTO.ReplacedBy)
	}

	// tool2 should have no version info.
	var tool2DTO *RegisteredToolJSON
	for i := range dtos {
		if dtos[i].Name == "srv_tool2" {
			tool2DTO = &dtos[i]
			break
		}
	}
	if tool2DTO == nil {
		t.Fatal("expected to find srv_tool2 in DTOs")
	}
	if tool2DTO.Version != "" {
		t.Fatalf("expected empty version for tool2, got %s", tool2DTO.Version)
	}
	if tool2DTO.Deprecated {
		t.Fatal("expected deprecated=false for tool2")
	}
}

func TestRegistryListToolsJSON_StatsIncluded(t *testing.T) {
	r := NewRegistry("_")

	r.RegisterServer("srv", []Tool{
		{Name: "tool", InputSchema: json.RawMessage(`{}`)},
	})

	// Record some stats.
	rt, _ := r.ResolveTool("srv_tool")
	rt.Stats.RecordCall(100_000_000, false) // 100ms, no error

	dtos := r.ListToolsJSON()
	if len(dtos) != 1 {
		t.Fatalf("expected 1 DTO, got %d", len(dtos))
	}
	if dtos[0].Stats.CallCount != 1 {
		t.Fatalf("expected call_count=1, got %d", dtos[0].Stats.CallCount)
	}
	if dtos[0].Stats.AvgLatencyMs < 90 || dtos[0].Stats.AvgLatencyMs > 110 {
		t.Fatalf("expected avg_latency_ms ~100, got %f", dtos[0].Stats.AvgLatencyMs)
	}
}
