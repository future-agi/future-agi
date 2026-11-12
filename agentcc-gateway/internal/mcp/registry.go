package mcp

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
)

// RegisteredTool is a tool from an upstream server, namespaced for the aggregated catalog.
type RegisteredTool struct {
	Server   string    // upstream server ID
	OrigName string    // original tool name on the server
	Name     string    // legacy namespaced name (server<separator>tool)
	Tool     Tool      // the tool under its legacy name
	Stats    ToolStats // usage statistics

	// Upstream is the tool exactly as the upstream listed it, Raw included;
	// a virtual server names and presents it its own way.
	Upstream Tool
	// Title is the upstream's top-level title, read from Upstream.Raw.
	Title string
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

// MemberSource serves the upstream lists of a virtual server's member
// servers, every tool included whatever its legacy name. The registry
// is the source of the operator's servers; an org pool serves its org.
type MemberSource interface {
	MemberTools(serverID string) []*RegisteredTool
	MemberResources(serverID string) []Resource
	MemberPrompts(serverID string) []Prompt
}

// ErrToolNameCollision is the load error of a name that another server's
// tool already holds: the incumbent keeps it, never overwritten (bug 13).
const ErrToolNameCollision = "mcp_tool_name_collision"

// serverEntry is one server's upstream lists and the legacy names it holds.
type serverEntry struct {
	tools     []*RegisteredTool          // every upstream tool, in upstream order
	byName    map[string]*RegisteredTool // upstream name → tool
	resources []Resource
	prompts   []Prompt

	toolNames   []string // legacy tool names this server holds
	promptNames []string // legacy prompt names this server holds
	uris        []string // resource URIs this server holds

	// The names it could not take, each mcp_tool_name_collision, by list:
	// each list's refresh replaces only its own.
	toolErrors, promptErrors, resourceErrors []string
}

// Registry aggregates tools from all connected upstream MCP servers. Its
// legacy view names a tool server<separator>tool; a name the first holder
// has is never given to another server, case-folded, and every list is
// sorted by name.
type Registry struct {
	mu           sync.RWMutex
	separator    string
	toolVersions map[string]ToolVersionInfo // tool name → version info (set from config)
	servers      map[string]*serverEntry
	tools        map[string]*RegisteredTool     // legacy name → tool
	resources    map[string]*RegisteredResource // URI → resource
	prompts      map[string]*RegisteredPrompt   // legacy name → prompt
	// foldedTools and foldedPrompts map a case-folded legacy name to the
	// server holding it: two names that differ only in case collide.
	foldedTools   map[string]string
	foldedPrompts map[string]string

	// views keeps each virtual server's name holders across compositions,
	// so a tool that arrives through a refresh never takes an incumbent's
	// name (hook_vserver.go). viewsMu guards the map; each state has its own
	// lock, so one virtual server's composition never waits on another's.
	viewsMu sync.Mutex
	views   map[string]*viewState
	// orgSource, when set, gives an org's member source (the tenancy's pool).
	orgSource atomic.Pointer[func(orgID string) MemberSource]
	// staticOrg is mcp.static.org_id: the one org the legacy view serves;
	// while it is unset, a principal with no org gets it (hook_vserver.go).
	staticOrg atomic.Pointer[string]
}

// NewRegistry creates a new tool registry.
func NewRegistry(separator string) *Registry {
	if separator == "" {
		separator = "_"
	}
	return &Registry{
		separator: separator,
		servers:   make(map[string]*serverEntry),
		tools:     make(map[string]*RegisteredTool),
		resources: make(map[string]*RegisteredResource),
		prompts:   make(map[string]*RegisteredPrompt),
		views:     make(map[string]*viewState),

		foldedTools:   make(map[string]string),
		foldedPrompts: make(map[string]string),
	}
}

func (r *Registry) entryLocked(serverID string) *serverEntry {
	e, ok := r.servers[serverID]
	if !ok {
		e = &serverEntry{byName: map[string]*RegisteredTool{}}
		r.servers[serverID] = e
	}
	return e
}

// claim gives name to serverID unless a server, this one included, already
// holds it case-folded in folded; it returns the load error of a refusal.
func claim(folded map[string]string, serverID, kind, name string) (string, bool) {
	fold := vserver.FoldKey(name)
	if holder, taken := folded[fold]; taken {
		return fmt.Sprintf("%s: %s %q is already exposed by server %q", ErrToolNameCollision, kind, name, holder), false
	}
	folded[fold] = serverID
	return "", true
}

// RegisterServer adds or replaces tools for a server. A tool whose legacy
// name another server holds is kept for virtual servers but not given the
// name; the server's load errors say so.
func (r *Registry) RegisterServer(serverID string, tools []Tool) {
	r.mu.Lock()
	defer r.mu.Unlock()

	e := r.entryLocked(serverID)
	r.releaseToolsLocked(e)
	e.tools, e.byName, e.toolErrors = nil, map[string]*RegisteredTool{}, nil
	for _, t := range tools {
		if _, dup := e.byName[t.Name]; dup {
			continue
		}
		// A new RegisteredTool each time: an in-flight call may still hold
		// the previous one, which is never written again.
		rt := newRegisteredTool(serverID, r.separator, t)
		nsName := rt.Name
		e.tools = append(e.tools, rt)
		e.byName[t.Name] = rt
		if msg, ok := claim(r.foldedTools, serverID, "tool", nsName); !ok {
			e.toolErrors = append(e.toolErrors, msg)
			continue
		}
		r.tools[nsName] = rt
		e.toolNames = append(e.toolNames, nsName)
	}
}

// newRegisteredTool is the RegisteredTool of one upstream tool of serverID,
// named server<separator>tool on the legacy view. RegisterServer and an org
// pool's member source (PoolMembers) both build through it.
func newRegisteredTool(serverID, separator string, t Tool) *RegisteredTool {
	nsName := serverID + separator + t.Name
	return &RegisteredTool{
		Server:   serverID,
		OrigName: t.Name,
		Name:     nsName,
		Upstream: t,
		Title:    rawTitle(t.Raw),
		Tool: Tool{
			Name:         nsName,
			Description:  t.Description,
			InputSchema:  t.InputSchema,
			OutputSchema: t.OutputSchema,
			Annotations:  t.Annotations,
			// The upstream definition with only the name edited, so a
			// writer that passes Raw through keeps every other field.
			Raw: editRaw(t.Raw, map[string]string{"name": nsName}),
		},
	}
}

// UnregisterServer removes a server: its own entries only.
func (r *Registry) UnregisterServer(serverID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	e, ok := r.servers[serverID]
	if !ok {
		return
	}
	r.releaseToolsLocked(e)
	r.releasePromptsLocked(e)
	r.releaseResourcesLocked(e)
	delete(r.servers, serverID)
}

func (r *Registry) releaseToolsLocked(e *serverEntry) {
	for _, name := range e.toolNames {
		delete(r.tools, name)
		delete(r.foldedTools, vserver.FoldKey(name))
	}
	e.toolNames = nil
}

func (r *Registry) releasePromptsLocked(e *serverEntry) {
	for _, name := range e.promptNames {
		delete(r.prompts, name)
		delete(r.foldedPrompts, vserver.FoldKey(name))
	}
	e.promptNames = nil
}

func (r *Registry) releaseResourcesLocked(e *serverEntry) {
	for _, uri := range e.uris {
		delete(r.resources, uri)
	}
	e.uris = nil
}

// ResolveTool looks up a tool by its namespaced name.
func (r *Registry) ResolveTool(namespacedName string) (*RegisteredTool, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	t, ok := r.tools[namespacedName]
	return t, ok
}

// ListTools returns independent snapshots of all registered tools, sorted by name.
func (r *Registry) ListTools() []*RegisteredTool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	result := make([]*RegisteredTool, 0, len(r.tools))
	for _, t := range r.tools {
		snapshot := new(RegisteredTool)
		snapshotTool(snapshot, t)
		result = append(result, snapshot)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Name < result[j].Name })
	return result
}

// ListMCPTools returns tools in MCP Tool format for tools/list responses,
// sorted by name.
func (r *Registry) ListMCPTools() []Tool {
	list := r.ListTools()
	result := make([]Tool, 0, len(list))
	for _, t := range list {
		result = append(result, t.Tool)
	}
	return result
}

// ServerTools returns snapshots of the tools a server holds legacy names for,
// nil for an unknown server.
func (r *Registry) ServerTools(serverID string) []*RegisteredTool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	e, ok := r.servers[serverID]
	if !ok {
		return nil
	}
	result := make([]*RegisteredTool, 0, len(e.toolNames))
	for _, name := range e.toolNames {
		if t, ok := r.tools[name]; ok {
			snapshot := new(RegisteredTool)
			snapshotTool(snapshot, t)
			result = append(result, snapshot)
		}
	}
	return result
}

// MemberTools returns every upstream tool of a server (MemberSource).
func (r *Registry) MemberTools(serverID string) []*RegisteredTool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if e, ok := r.servers[serverID]; ok {
		return append([]*RegisteredTool(nil), e.tools...)
	}
	return nil
}

// MemberResources returns a server's upstream resources (MemberSource).
func (r *Registry) MemberResources(serverID string) []Resource {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if e, ok := r.servers[serverID]; ok {
		return append([]Resource(nil), e.resources...)
	}
	return nil
}

// MemberPrompts returns a server's upstream prompts (MemberSource).
func (r *Registry) MemberPrompts(serverID string) []Prompt {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if e, ok := r.servers[serverID]; ok {
		return append([]Prompt(nil), e.prompts...)
	}
	return nil
}

// SetOrgSource installs the per-org member source (the tenancy's pools): fn returns
// the org's source, or nil when the org has none.
func (r *Registry) SetOrgSource(fn func(orgID string) MemberSource) {
	r.orgSource.Store(&fn)
}

// sourceFor is the member source of an org's virtual servers. It fails
// closed: with no org source installed, or none for this org, a virtual
// server has no member tools, so an org's server id never resolves to an
// operator server of the same id (only the org source may serve the static
// pool, to principals of mcp.static.org_id).
func (r *Registry) sourceFor(orgID string) MemberSource {
	if fn := r.orgSource.Load(); fn != nil && *fn != nil {
		if src := (*fn)(orgID); src != nil {
			return src
		}
	}
	return noMembers{}
}

// SetStaticOrg sets mcp.static.org_id, the org the operator's YAML servers
// serve: its principals get the legacy view; every other org's principals,
// and a principal with no org, are served through their org config only. ""
// (unset) serves the legacy view to a principal with no org.
func (r *Registry) SetStaticOrg(orgID string) {
	r.staticOrg.Store(&orgID)
}

// StaticOrg returns mcp.static.org_id, "" until SetStaticOrg (none).
func (r *Registry) StaticOrg() string {
	if id := r.staticOrg.Load(); id != nil {
		return *id
	}
	return ""
}

// noMembers is the member source of an org with none.
type noMembers struct{}

func (noMembers) MemberTools(string) []*RegisteredTool { return nil }
func (noMembers) MemberResources(string) []Resource    { return nil }
func (noMembers) MemberPrompts(string) []Prompt        { return nil }

// ServerErrors returns the names a server could not take, each a
// mcp_tool_name_collision load error, for status.
func (r *Registry) ServerErrors(serverID string) []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	e, ok := r.servers[serverID]
	if !ok {
		return nil
	}
	out := append(append([]string(nil), e.toolErrors...), e.promptErrors...)
	return append(out, e.resourceErrors...)
}

// ServerIDs returns all registered server IDs, sorted.
func (r *Registry) ServerIDs() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	ids := make([]string, 0, len(r.servers))
	for id := range r.servers {
		ids = append(ids, id)
	}
	sort.Strings(ids)
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

// RegisterResources sets a server's resources. A URI another server already
// exposes stays that server's.
func (r *Registry) RegisterResources(serverID string, resources []Resource) {
	r.mu.Lock()
	defer r.mu.Unlock()
	e := r.entryLocked(serverID)
	r.releaseResourcesLocked(e)
	e.resources, e.resourceErrors = append([]Resource(nil), resources...), nil
	for _, res := range resources {
		if held, ok := r.resources[res.URI]; ok {
			if held.Server != serverID {
				e.resourceErrors = append(e.resourceErrors, fmt.Sprintf("%s: resource %q is already exposed by server %q", ErrToolNameCollision, res.URI, held.Server))
			}
			continue
		}
		r.resources[res.URI] = &RegisteredResource{Server: serverID, Resource: res}
		e.uris = append(e.uris, res.URI)
	}
}

// RegisterPrompts sets a server's prompts, named like its tools.
func (r *Registry) RegisterPrompts(serverID string, prompts []Prompt) {
	r.mu.Lock()
	defer r.mu.Unlock()
	e := r.entryLocked(serverID)
	r.releasePromptsLocked(e)
	e.prompts, e.promptErrors = append([]Prompt(nil), prompts...), nil
	for _, p := range prompts {
		nsName := serverID + r.separator + p.Name
		if msg, ok := claim(r.foldedPrompts, serverID, "prompt", nsName); !ok {
			e.promptErrors = append(e.promptErrors, msg)
			continue
		}
		r.prompts[nsName] = &RegisteredPrompt{
			Server: serverID,
			Prompt: Prompt{Name: nsName, Description: p.Description, Arguments: p.Arguments,
				Raw: editRaw(p.Raw, map[string]string{"name": nsName})},
		}
		e.promptNames = append(e.promptNames, nsName)
	}
}

// ListResources returns all registered resources, sorted by URI.
func (r *Registry) ListResources() []Resource {
	r.mu.RLock()
	defer r.mu.RUnlock()
	result := make([]Resource, 0, len(r.resources))
	for _, res := range r.resources {
		result = append(result, res.Resource)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].URI < result[j].URI })
	return result
}

// ListPrompts returns all registered prompts, sorted by name.
func (r *Registry) ListPrompts() []Prompt {
	r.mu.RLock()
	defer r.mu.RUnlock()
	result := make([]Prompt, 0, len(r.prompts))
	for _, p := range r.prompts {
		result = append(result, p.Prompt)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Name < result[j].Name })
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

// rawTitle reads the top-level title of a raw tool definition, "" if none.
func rawTitle(raw json.RawMessage) string {
	if len(raw) == 0 || !strings.Contains(string(raw), `"title"`) {
		return ""
	}
	var v struct {
		Title string `json:"title"`
	}
	_ = json.Unmarshal(raw, &v)
	return v.Title
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

// ListToolsJSON returns all registered tools as JSON-serializable DTOs,
// sorted by name.
func (r *Registry) ListToolsJSON() []RegisteredToolJSON {
	list := r.ListTools()
	r.mu.RLock()
	defer r.mu.RUnlock()

	result := make([]RegisteredToolJSON, 0, len(list))
	for _, t := range list {
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
