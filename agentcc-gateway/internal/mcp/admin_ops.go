package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The org-scoped MCP admin API: every answer comes
// from one org's pool, in the mcp-admin-*-v1 shapes.

var (
	// ErrTenancyUnavailable: the server has no tenancy installed.
	ErrTenancyUnavailable = errors.New("mcp tenancy is not available")
	// ErrUnknownOrg: the org has no config and is not the static org (404).
	ErrUnknownOrg = errors.New("unknown org")
	// ErrUnknownVirtualServer: the org has no such virtual server (404).
	ErrUnknownVirtualServer = errors.New("unknown virtual server")
	// ErrPolicyUnavailable: the org's MCP policy cannot be evaluated (503).
	ErrPolicyUnavailable = errors.New(CodePolicyUnavailable)
)

// ClientStatus returns status info for an upstream client.
type ClientStatus struct {
	ServerID  string `json:"server_id"`
	Healthy   bool   `json:"healthy"`
	ToolCount int    `json:"tool_count"`
}

// ClientStatuses returns the status of the clients registered on the server
// itself (today's global registry), as the route setup's legacy status did.
func (s *Server) ClientStatuses() []ClientStatus {
	s.clientsMu.RLock()
	defer s.clientsMu.RUnlock()

	statuses := make([]ClientStatus, 0, len(s.clients))
	for id, client := range s.clients {
		statuses = append(statuses, ClientStatus{
			ServerID:  id,
			Healthy:   client.Healthy(),
			ToolCount: client.ToolCount(),
		})
	}
	return statuses
}

// SessionCount returns the number of active downstream sessions.
func (s *Server) SessionCount() int {
	return s.sessions.Count()
}

// orgPool resolves an admin call's org: unknown orgs are ErrUnknownOrg, and
// an org whose policy cannot be evaluated is that error.
func (s *Server) orgPool(orgID string) (*poolView, error) {
	t := s.Tenancy()
	if t == nil {
		return nil, ErrTenancyUnavailable
	}
	if orgID == "" || !t.Known(orgID) {
		return nil, ErrUnknownOrg
	}
	snapshot, pool, err := t.Resolve(orgID)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrPolicyUnavailable, err)
	}
	return makePoolView(snapshot, pool, s.vsNamed(orgID, snapshot)), nil
}

// statusPool is like orgPool but accepts a pool returned with a policy error.
// Manager uses that path for a first invalid config so status can show its
// per-server load errors even though calls remain denied.
func (s *Server) statusPool(orgID string) (*poolView, error) {
	t := s.Tenancy()
	if t == nil {
		return nil, ErrTenancyUnavailable
	}
	if orgID == "" || !t.Known(orgID) {
		return nil, ErrUnknownOrg
	}
	var snapshot *tenant.OrgConfig
	var pool TenantPool
	var err error
	if sr, ok := t.(interface {
		ResolveStatus(string) (*tenant.OrgConfig, TenantPool, error)
	}); ok {
		snapshot, pool, err = sr.ResolveStatus(orgID)
	} else {
		snapshot, pool, err = t.Resolve(orgID)
	}
	if err != nil && pool == nil {
		return nil, fmt.Errorf("%w: %v", ErrPolicyUnavailable, err)
	}
	if pool == nil {
		return nil, ErrPolicyUnavailable
	}
	return makePoolView(snapshot, pool, s.vsNamed(orgID, snapshot)), nil
}

func makePoolView(snapshot *tenant.OrgConfig, pool TenantPool, vsNames bool) *poolView {
	v := &poolView{orgEnabled: snapshot != nil && snapshot.MCP != nil && snapshot.MCP.Enabled, pool: pool, vsNames: vsNames, snapshot: snapshot}
	if snapshot == nil || snapshot.MCP == nil {
		v.vs = func(string) ([]string, bool) { return nil, false }
		return v
	}
	v.vs = func(id string) ([]string, bool) {
		vs, ok := snapshot.MCPVirtualServer(id)
		return vs.Members, ok
	}
	return v
}

// poolView is an org pool as the admin API reads it.
type poolView struct {
	orgEnabled bool
	// vsNames: the org's principals are served through virtual servers, so
	// its tools are named the virtual-server way (adminToolName).
	vsNames bool
	pool    TenantPool
	vs      func(idOrSlug string) (members []string, ok bool)
	// snapshot is the org's policy snapshot (its pinning); nil when
	// the org has no config.
	snapshot *tenant.OrgConfig
}

// servers returns the pool's servers, narrowed to a virtual server's members
// when vs is set (ErrUnknownVirtualServer when the org has none by that id).
// The virtual server's full composition (globs, overrides, names) belongs to the virtual servers.
func (v *poolView) servers(vs string) ([]PoolServer, error) {
	all := v.pool.Servers()
	if vs == "" {
		return all, nil
	}
	members, ok := v.vs(vs)
	if !ok {
		return nil, ErrUnknownVirtualServer
	}
	keep := map[string]bool{}
	for _, m := range members {
		id, _, _ := strings.Cut(m, "@") // <server_id>@<account_label>
		keep[id] = true
	}
	out := all[:0:0]
	for _, ps := range all {
		if keep[ps.ID] {
			out = append(out, ps)
		}
	}
	return out, nil
}

// AdminStatus is GET /-/mcp/status?org_id= (mcp-admin-status-v1). The counts
// cover the org's own servers only. Sessions are not bound to an org until
// Sessions bind them to their principal, so none is counted for an org.
func (s *Server) AdminStatus(orgID string) (*gatewayadmin.MCPAdminStatus, error) {
	v, err := s.statusPool(orgID)
	if err != nil {
		return nil, err
	}
	out := &gatewayadmin.MCPAdminStatus{Enabled: true, OrgEnabled: v.orgEnabled, Servers: []*gatewayadmin.MCPAdminServerStatus{}}
	for _, ps := range v.pool.Servers() {
		row := &gatewayadmin.MCPAdminServerStatus{
			ServerID: ps.ID, Era: strPtr(ps.Era), Transport: strPtr(ps.Transport),
			Healthy: ps.Healthy, ToolCount: len(ps.Tools),
		}
		if ps.LastError != "" {
			row.LastError = strPtr(ps.LastError)
		}
		if !ps.LastRefreshedAt.IsZero() {
			row.LastRefreshedAt = strPtr(ps.LastRefreshedAt.UTC().Format(time.RFC3339))
		}
		out.Servers = append(out.Servers, row)
		out.Tools += len(ps.Tools)
		out.Resources += len(ps.Resources)
		out.Prompts += len(ps.Prompts)
	}
	return out, nil
}

// AdminTools is GET /-/mcp/tools?org_id=&vs= (mcp-admin-tools-v1), named by
// adminToolName.
func (s *Server) AdminTools(orgID, vs string) (*gatewayadmin.MCPAdminTools, error) {
	if vs != "" && !s.virtualServersEnabled {
		return nil, ErrUnknownVirtualServer
	}
	v, err := s.orgPool(orgID)
	if err != nil {
		return nil, err
	}
	servers, err := v.servers(vs)
	if err != nil {
		return nil, err
	}
	out := &gatewayadmin.MCPAdminTools{Tools: []*gatewayadmin.MCPAdminTool{}}
	policy := pinPolicy(v.snapshot)
	for _, ps := range servers {
		for _, tool := range ps.Tools {
			// Each tool's revision, and whether pinning holds it.
			rev, held := s.adminPinState(policy, ps, tool)
			out.Tools = append(out.Tools, &gatewayadmin.MCPAdminTool{
				Name: s.adminToolName(v.vsNames, ps.ID, tool.Name), ServerID: ps.ID,
				UpstreamName: tool.Name, Annotations: rawAnnotations(tool),
				Revision: rev, Quarantined: held,
			})
		}
	}
	sort.Slice(out.Tools, func(i, j int) bool { return out.Tools[i].Name < out.Tools[j].Name })
	return out, nil
}

// AdminResource is one item of GET /-/mcp/resources?org_id=.
type AdminResource struct {
	URI         string `json:"uri"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	MIMEType    string `json:"mimeType,omitempty"`
	ServerID    string `json:"server_id"`
}

// AdminPrompt is one item of GET /-/mcp/prompts?org_id=.
type AdminPrompt struct {
	Name        string           `json:"name"`
	Description string           `json:"description,omitempty"`
	Arguments   []PromptArgument `json:"arguments,omitempty"`
	ServerID    string           `json:"server_id"`
}

// AdminResources lists the org's resources, each with its server_id.
func (s *Server) AdminResources(orgID string) ([]AdminResource, error) {
	v, err := s.orgPool(orgID)
	if err != nil {
		return nil, err
	}
	out := []AdminResource{}
	for _, ps := range v.pool.Servers() {
		for _, r := range ps.Resources {
			out = append(out, AdminResource{URI: r.URI, Name: r.Name, Description: r.Description, MIMEType: r.MIMEType, ServerID: ps.ID})
		}
	}
	return out, nil
}

// AdminPrompts lists the org's prompts, each with its server_id.
func (s *Server) AdminPrompts(orgID string) ([]AdminPrompt, error) {
	v, err := s.orgPool(orgID)
	if err != nil {
		return nil, err
	}
	out := []AdminPrompt{}
	for _, ps := range v.pool.Servers() {
		for _, p := range ps.Prompts {
			out = append(out, AdminPrompt{Name: p.Name, Description: p.Description, Arguments: p.Arguments, ServerID: ps.ID})
		}
	}
	return out, nil
}

// TestToolResult is the result of a tool execution playground test.
type TestToolResult struct {
	Content       []ContentPart `json:"content,omitempty"`
	IsError       bool          `json:"is_error"`
	DurationMs    float64       `json:"duration_ms"`
	GuardrailPre  string        `json:"guardrail_pre"`  // "pass", "blocked", or "skipped"
	GuardrailPost string        `json:"guardrail_post"` // "pass", "blocked", or "skipped"
	Error         string        `json:"error,omitempty"`
	Server        string        `json:"server,omitempty"`
	ServerID      string        `json:"server_id,omitempty"`
	Code          string        `json:"code,omitempty"`
	// Rule is the rule that refused the call, from a refusal's metadata
	// (tool_denied: access.default, access.rules[N], key.allowed, header,
	// policy:<id>, ...), so a caller can say why without reading Error.
	Rule string `json:"rule,omitempty"`
}

// TestTool runs a tool of the server's own registry, the route setup's
// legacy playground (routes_mcp.go). The org-scoped POST /-/mcp/test
// (AdminTestTool) replaces that route; this stays for its caller.
func (s *Server) TestTool(ctx context.Context, toolName string, arguments map[string]interface{}) TestToolResult {
	regTool, ok := s.registry.ResolveTool(toolName)
	if !ok {
		return TestToolResult{Error: "tool not found: " + toolName, GuardrailPre: "skipped", GuardrailPost: "skipped"}
	}

	result := TestToolResult{Server: regTool.Server, GuardrailPre: "skipped", GuardrailPost: "skipped"}

	if s.guard != nil {
		if errMsg := s.guard.CheckPre(toolName, regTool.Server, arguments); errMsg != "" {
			result.GuardrailPre = "blocked"
			result.Error = "guardrail blocked: " + errMsg
			return result
		}
		result.GuardrailPre = "pass"
	}

	s.clientsMu.RLock()
	client, ok := s.clients[regTool.Server]
	s.clientsMu.RUnlock()
	if !ok || !client.Healthy() {
		result.Error = fmt.Sprintf("upstream server %q is unavailable", regTool.Server)
		return result
	}

	start := time.Now()
	callCtx, cancel := context.WithTimeout(ctx, s.toolTimeout)
	defer cancel()

	callResult, err := client.CallTool(callCtx, regTool.OrigName, arguments)
	result.DurationMs = float64(time.Since(start).Microseconds()) / 1000.0

	if err != nil {
		result.Error = "tool call failed: " + err.Error()
		result.IsError = true
		return result
	}

	result.Content = callResult.Content
	result.IsError = callResult.IsError

	if s.guard != nil {
		if errMsg := s.guard.CheckPost(toolName, regTool.Server, callResult); errMsg != "" {
			result.GuardrailPost = "blocked"
			result.Error = "guardrail blocked output: " + errMsg
			return result
		}
		result.GuardrailPost = "pass"
	}

	return result
}

// AdminTestTool is POST /-/mcp/test {org_id, vs?, name, arguments}: it runs
// the full tools/call chain as principal admin:<org>, set before the chain
// runs. The admin may call every org tool (the key ACL is lifted); every
// other step applies, the depth check included (the admin request's headers
// are the call's, its credentials removed). The name is resolved in the
// org's pool first, so another org's tool is never found.
func (s *Server) AdminTestTool(r *http.Request, orgID, vs, name string, args json.RawMessage) (TestToolResult, error) {
	if vs != "" && !s.virtualServersEnabled {
		return TestToolResult{}, ErrUnknownVirtualServer
	}
	v, err := s.orgPool(orgID)
	if err != nil {
		return TestToolResult{}, err
	}
	servers, err := v.servers(vs)
	if err != nil {
		return TestToolResult{}, err
	}
	skipped := TestToolResult{GuardrailPre: "skipped", GuardrailPost: "skipped"}
	serverID, msg := s.resolvePoolTool(v.vsNames, servers, name)
	if msg != "" {
		skipped.Error = msg
		return skipped, nil
	}
	params, err := json.Marshal(struct {
		Name      string          `json:"name"`
		Arguments json.RawMessage `json:"arguments,omitempty"`
	}{name, args})
	if err != nil {
		return TestToolResult{}, err
	}
	call := &Message{JSONRPC: "2.0", ID: json.RawMessage(`"admin-test"`), Method: MethodToolsCall, Params: params}
	cc := s.newCallContext(withoutCredentials(r), call, nil)
	defer cc.runFinalizers()
	cc.Principal = Principal{Kind: PrincipalAdmin, OrgID: orgID}
	cc.VirtualServer = vs
	cc.Tool.ExposedName = name
	cc.Args = args
	if err := decodeArgs(args, &cc.legacy.arguments); err != nil {
		skipped.Error = err.Error()
		return skipped, nil
	}
	s.runChain(cc, toolsCallChain)
	return s.testResult(cc, serverID), nil
}

// withoutCredentials is the admin request as the chain sees it: the admin
// token is the gateway's own credential and no hook (or upstream) ever
// reads it.
func withoutCredentials(r *http.Request) *http.Request {
	c := r.Clone(r.Context())
	c.Header.Del("Authorization")
	c.Header.Del("X-Api-Key")
	return c
}

// resolvePoolTool finds the server of an exposed name among servers; ""
// and a message when no tool, or more than one, has that name.
func (s *Server) resolvePoolTool(vsNames bool, servers []PoolServer, name string) (string, string) {
	var found []string
	for _, ps := range servers {
		for _, tool := range ps.Tools {
			if s.adminToolName(vsNames, ps.ID, tool.Name) == name {
				found = append(found, ps.ID)
			}
		}
	}
	switch len(found) {
	case 0:
		return "", "tool not found: " + name
	case 1:
		return found[0], ""
	}
	return "", fmt.Sprintf("tool name %q is ambiguous: it names a tool of each of %s", name, strings.Join(found, ", "))
}

// testResult is the playground's view of a finished chain.
func (s *Server) testResult(cc *CallContext, serverID string) TestToolResult {
	out := TestToolResult{Server: serverID, ServerID: serverID, GuardrailPre: "skipped", GuardrailPost: "skipped"}
	if cc.Upstream.ServerID != "" {
		out.Server, out.ServerID = cc.Upstream.ServerID, cc.Upstream.ServerID
	}
	if !cc.Timings.UpstreamStarted.IsZero() && !cc.Timings.UpstreamEnded.IsZero() {
		out.DurationMs = float64(cc.Timings.UpstreamEnded.Sub(cc.Timings.UpstreamStarted).Microseconds()) / 1000.0
	}
	if s.guard != nil {
		out.GuardrailPre = "pass"
		if cc.Executed {
			out.GuardrailPost = "pass"
		}
	}
	if d := cc.Decision; d != nil {
		out.Error, out.Code, out.IsError = d.Error(), d.Code, cc.Executed || d.Code == "upstream_error"
		if meta, ok := d.Metadata.(map[string]any); ok {
			out.Rule, _ = meta["rule"].(string)
		}
		switch d.Code {
		case "tool_call_blocked":
			out.GuardrailPre = "blocked"
		case "tool_result_blocked":
			out.GuardrailPost = "blocked"
		}
		return out
	}
	var result ToolCallResult
	if res := cc.legacy.toolResult; res != nil {
		result = *res
	} else if len(cc.Result) > 0 {
		_ = json.Unmarshal(cc.Result, &result)
	}
	out.Content, out.IsError = result.Content, result.IsError || cc.IsError
	return out
}

// adminToolName is the name the admin API lists a pool tool under and
// resolves /-/mcp/test's name by. An org served through virtual servers
// (vsNamed) names it as the virtual servers' prefixed naming does, <server_id>__<tool>
// normalized, so /-/mcp/tools names what
// /-/mcp/test and the org's default /mcp view accept; the legacy view keeps
// today's <server_id><separator><tool>, the static registry's name. A virtual
// server's own composition (overrides, flat naming, account members) is not
// applied here: a later admin view of a virtual server composes it (the virtual servers'
// Rejections and the composition are its read APIs).
func (s *Server) adminToolName(vsNames bool, serverID, tool string) string {
	if vsNames {
		return vserver.EncodePrefixed(serverID, tool)
	}
	return serverID + s.registry.Separator() + tool
}

// vsNamed reports whether orgID's principals are served through virtual
// servers, as step 4 decides for them (usesVirtualServers): an org whose
// config has an mcp section, or any org but mcp.static.org_id ("", no org,
// included while static.org_id is set).
func (s *Server) vsNamed(orgID string, snapshot *tenant.OrgConfig) bool {
	if snapshot != nil && snapshot.MCP != nil {
		return true
	}
	return orgID != s.registry.StaticOrg()
}

// rawAnnotations is a tool's annotations as the upstream sent them.
func rawAnnotations(t Tool) map[string]interface{} {
	var obj struct {
		Annotations map[string]interface{} `json:"annotations"`
	}
	if len(t.Raw) > 0 && json.Unmarshal(t.Raw, &obj) == nil && obj.Annotations != nil {
		return obj.Annotations
	}
	return nil
}

func strPtr(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
