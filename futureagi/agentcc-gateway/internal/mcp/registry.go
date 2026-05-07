package mcp

import (
	"encoding/json"
	"sync"
	"sync/atomic"
	"time"
)

// RegisteredTool is a tool from an upstream server, namespaced for the aggregated catalog.
type RegisteredTool struct {
	Server   string    // upstream server ID
	OrigName string    // original tool name on the server
	Name     string    // namespaced name (server_toolname)
	Tool     Tool      // full MCP tool definition
	Stats    ToolStats // usage statistics
}

// ToolStats tracks per-tool usage counters (lock-free via atomics).
type ToolStats struct {
	CallCount  atomic.Int64
	ErrorCount atomic.Int64
	TotalNanos atomic.Int64 // sum of durations for avg latency
	LastCalled atomic.Int64 // unix nano of last call
}

// RecordCall records a tool invocation.
func (s *ToolStats) RecordCall(duration time.Duration, hasError bool) {
	s.CallCount.Add(1)
	s.TotalNanos.Add(int64(duration))
	s.LastCalled.Store(time.Now().UnixNano())
	if hasError {
		s.ErrorCount.Add(1)
	}
}

// AvgLatencyMs returns the average call latency in milliseconds.
func (s *ToolStats) AvgLatencyMs() float64 {
	count := s.CallCount.Load()
	if count == 0 {
		return 0
	}
	return float64(s.TotalNanos.Load()) / float64(count) / 1e6
}

// ErrorRate returns the error rate as a fraction (0.0–1.0).
func (s *ToolStats) ErrorRate() float64 {
	count := s.CallCount.Load()
	if count == 0 {
		return 0
	}
	return float64(s.ErrorCount.Load()) / float64(count)
}

// ToolVersionInfo holds version and deprecation metadata for a tool.
type ToolVersionInfo struct {
	Version            string `json:"version,omitempty"`
	Deprecated         bool   `json:"deprecated,omitempty"`
	DeprecationMessage string `json:"deprecation_message,omitempty"`
	ReplacedBy         string `json:"replaced_by,omitempty"`
}

// RegisteredResource is a resource from an upstream server, namespaced.
type RegisteredResource struct {
	Server   string   // upstream server ID
	Resource Resource // MCP resource
}

// RegisteredPrompt is a prompt from an upstream server, namespaced.
type RegisteredPrompt struct {
	Server string // upstream server ID
	Prompt Prompt // MCP prompt
}

// Registry aggregates tools from all connected upstream MCP servers.
type Registry struct {
	mu           sync.RWMutex
	tools        map[string]*RegisteredTool // namespaced name → tool
	byServer     map[string][]string        // server ID → list of namespaced names
	separator    string
	toolVersions map[string]ToolVersionInfo     // tool name → version info (set from config)
	resources    map[string]*RegisteredResource // URI → resource
	prompts      map[string]*RegisteredPrompt   // namespaced name → prompt
}

// NewRegistry creates a new tool registry.
func NewRegistry(separator string) *Registry {
	if separator == "" {
		separator = "_"
	}
	return &Registry{
		tools:     make(map[string]*RegisteredTool),
		byServer:  make(map[string][]string),
		separator: separator,
		resources: make(map[string]*RegisteredResource),
		prompts:   make(map[string]*RegisteredPrompt),
	}
}

// RegisterServer adds or replaces tools for a server.
func (r *Registry) RegisterServer(serverID string, tools []Tool) {
	r.mu.Lock()
	defer r.mu.Unlock()

	// Remove existing tools for this server.
	r.removeServerLocked(serverID)

	// Add new tools.
	names := make([]string, 0, len(tools))
	for _, t := range tools {
		nsName := serverID + r.separator + t.Name
		rt := &RegisteredTool{
			Server:   serverID,
			OrigName: t.Name,
			Name:     nsName,
			Tool: Tool{
				Name:         nsName,
				Description:  t.Description,
				InputSchema:  t.InputSchema,
				OutputSchema: t.OutputSchema,
				Annotations:  t.Annotations,
			},
		}
		r.tools[nsName] = rt
		names = append(names, nsName)
	}
	r.byServer[serverID] = names
}

// UnregisterServer removes all tools for a server.
func (r *Registry) UnregisterServer(serverID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.removeServerLocked(serverID)
}

func (r *Registry) removeServerLocked(serverID string) {
	if names, ok := r.byServer[serverID]; ok {
		for _, name := range names {
			delete(r.tools, name)
		}
		delete(r.byServer, serverID)
	}
}

// ResolveTool looks up a tool by its namespaced name.
func (r *Registry) ResolveTool(namespacedName string) (*RegisteredTool, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	t, ok := r.tools[namespacedName]
	return t, ok
}

// ListTools returns all registered tools.
func (r *Registry) ListTools() []RegisteredTool {
	r.mu.RLock()
	defer r.mu.RUnlock()

	result := make([]RegisteredTool, 0, len(r.tools))
	for _, t := range r.tools {
		result = append(result, *t)
	}
	return result
}

// ListMCPTools returns tools in MCP Tool format for tools/list responses.
func (r *Registry) ListMCPTools() []Tool {
	r.mu.RLock()
	defer r.mu.RUnlock()

	result := make([]Tool, 0, len(r.tools))
	for _, t := range r.tools {
		result = append(result, t.Tool)
	}
	return result
}

// ServerTools returns tools for a specific server.
func (r *Registry) ServerTools(serverID string) []RegisteredTool {
	r.mu.RLock()
	defer r.mu.RUnlock()

	names, ok := r.byServer[serverID]
	if !ok {
		return nil
	}

	result := make([]RegisteredTool, 0, len(names))
	for _, name := range names {
		if t, ok := r.tools[name]; ok {
			result = append(result, *t)
		}
	}
	return result
}

// ServerIDs returns all registered server IDs.
func (r *Registry) ServerIDs() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()

	ids := make([]string, 0, len(r.byServer))
	for id := range r.byServer {
		ids = append(ids, id)
	}
	return ids
}

// ToolCount returns the total number of registered tools.
func (r *Registry) ToolCount() int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.tools)
}

// Separator returns the namespace separator used for tool/prompt names.
func (r *Registry) Separator() string {
	return r.separator
}

// SetToolVersions sets version/deprecation info for tools (from config).
func (r *Registry) SetToolVersions(versions map[string]ToolVersionInfo) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.toolVersions = versions
}

// GetToolVersion returns version info for a tool, if configured.
func (r *Registry) GetToolVersion(toolName string) (ToolVersionInfo, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	v, ok := r.toolVersions[toolName]
	return v, ok
}

// RegisterResources adds resources from an upstream server.
func (r *Registry) RegisterResources(serverID string, resources []Resource) {
	r.mu.Lock()
	defer r.mu.Unlock()
	// Remove old resources from this server.
	for uri, res := range r.resources {
		if res.Server == serverID {
			delete(r.resources, uri)
		}
	}
	for _, res := range resources {
		r.resources[res.URI] = &RegisteredResource{
			Server:   serverID,
			Resource: res,
		}
	}
}

// RegisterPrompts adds prompts from an upstream server.
func (r *Registry) RegisterPrompts(serverID string, prompts []Prompt) {
	r.mu.Lock()
	defer r.mu.Unlock()
	// Remove old prompts from this server.
	for name, p := range r.prompts {
		if p.Server == serverID {
			delete(r.prompts, name)
		}
	}
	for _, p := range prompts {
		nsName := serverID + r.separator + p.Name
		r.prompts[nsName] = &RegisteredPrompt{
			Server: serverID,
			Prompt: Prompt{
				Name:        nsName,
				Description: p.Description,
				Arguments:   p.Arguments,
			},
		}
	}
}

// ListResources returns all registered resources.
func (r *Registry) ListResources() []Resource {
	r.mu.RLock()
	defer r.mu.RUnlock()
	result := make([]Resource, 0, len(r.resources))
	for _, res := range r.resources {
		result = append(result, res.Resource)
	}
	return result
}

// ListPrompts returns all registered prompts.
func (r *Registry) ListPrompts() []Prompt {
	r.mu.RLock()
	defer r.mu.RUnlock()
	result := make([]Prompt, 0, len(r.prompts))
	for _, p := range r.prompts {
		result = append(result, p.Prompt)
	}
	return result
}

// ResolveResource looks up a resource by URI.
func (r *Registry) ResolveResource(uri string) (*RegisteredResource, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	res, ok := r.resources[uri]
	return res, ok
}

// ResolvePrompt looks up a prompt by its namespaced name.
func (r *Registry) ResolvePrompt(name string) (*RegisteredPrompt, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	p, ok := r.prompts[name]
	return p, ok
}

// ResourceCount returns the total number of registered resources.
func (r *Registry) ResourceCount() int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.resources)
}

// PromptCount returns the total number of registered prompts.
func (r *Registry) PromptCount() int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.prompts)
}

// --- JSON-serializable DTOs for admin API ---

// RegisteredToolJSON is a JSON-safe representation of RegisteredTool.
type RegisteredToolJSON struct {
	Server             string           `json:"server"`
	OriginalName       string           `json:"original_name"`
	Name               string           `json:"name"`
	Description        string           `json:"description,omitempty"`
	InputSchema        json.RawMessage  `json:"input_schema,omitempty"`
	Annotations        *ToolAnnotations `json:"annotations,omitempty"`
	Stats              ToolStatsJSON    `json:"stats"`
	Version            string           `json:"version,omitempty"`
	Deprecated         bool             `json:"deprecated,omitempty"`
	DeprecationMessage string           `json:"deprecation_message,omitempty"`
	ReplacedBy         string           `json:"replaced_by,omitempty"`
}

// ToolStatsJSON is a JSON-safe representation of ToolStats.
type ToolStatsJSON struct {
	CallCount    int64   `json:"call_count"`
	ErrorCount   int64   `json:"error_count"`
	AvgLatencyMs float64 `json:"avg_latency_ms"`
	ErrorRate    float64 `json:"error_rate"`
	LastCalled   string  `json:"last_called,omitempty"`
}

// ListToolsJSON returns all registered tools as JSON-serializable DTOs.
func (r *Registry) ListToolsJSON() []RegisteredToolJSON {
	r.mu.RLock()
	defer r.mu.RUnlock()

	result := make([]RegisteredToolJSON, 0, len(r.tools))
	for _, t := range r.tools {
		var lastCalled string
		if ns := t.Stats.LastCalled.Load(); ns > 0 {
			lastCalled = time.Unix(0, ns).UTC().Format(time.RFC3339)
		}
		dto := RegisteredToolJSON{
			Server:       t.Server,
			OriginalName: t.OrigName,
			Name:         t.Name,
			Description:  t.Tool.Description,
			InputSchema:  t.Tool.InputSchema,
			Annotations:  t.Tool.Annotations,
			Stats: ToolStatsJSON{
				CallCount:    t.Stats.CallCount.Load(),
				ErrorCount:   t.Stats.ErrorCount.Load(),
				AvgLatencyMs: t.Stats.AvgLatencyMs(),
				ErrorRate:    t.Stats.ErrorRate(),
				LastCalled:   lastCalled,
			},
		}
		// Merge version/deprecation info from config.
		if vi, ok := r.toolVersions[t.Name]; ok {
			dto.Version = vi.Version
			dto.Deprecated = vi.Deprecated
			dto.DeprecationMessage = vi.DeprecationMessage
			dto.ReplacedBy = vi.ReplacedBy
		}
		result = append(result, dto)
	}
	return result
}
