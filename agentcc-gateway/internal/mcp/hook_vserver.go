package mcp

import (
	"context"
	"net/http"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// hookResolveVirtualServer is step 4 of every chain (chain.go).
const hookResolveVirtualServer = "resolveVirtualServer"

// resolveVirtualServer resolves what the call sees.
//
// The legacy view, the operator registry with today's names, is served only
// to a call with no org MCP config whose principal is of mcp.static.org_id,
// or, while that is unset, has no org (usesVirtualServers). Every other call
// is served through its org's config, an absent one or one with no mcp
// section reading as an empty one: no members, access.default deny (a
// principal with no org while static.org_id is set gets that empty view
// too). /mcp/v/{vs_id} serves that
// virtual server, looked up by id or slug in the principal's org config
// only, and /mcp the caller's default: the one bound to its key, else the org
// default, else every org server as mcp.access.default allows. An unknown,
// foreign or not-for-this-principal vs_id is the same HTTP 404, so nothing
// tells them apart. Keep observeHook as its first statement.
func resolveVirtualServer(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookResolveVirtualServer)
	requested := requestedVirtualServer(cc.Request())
	if requested != "" && !s.virtualServersEnabled {
		return virtualServerNotFound()
	}
	if !usesVirtualServers(s, cc) {
		if requested != "" {
			return virtualServerNotFound()
		}
		return nil
	}
	p := principalOf(cc)
	vs, configured := tenant.MCPVirtualServerSettings{}, true
	if requested != "" {
		var ok bool
		// An internal admin preview has no key-specific restriction. It still
		// resolves only inside its org and every tool-level rule runs below.
		// Network MCP callers cannot acquire PrincipalAdmin.
		if vs, ok = cc.OrgConfig.MCPVirtualServer(requested); !ok || (cc.Principal.Kind != PrincipalAdmin && !vserver.Usable(vs.Access, p)) {
			return virtualServerNotFound()
		}
	} else {
		vs, configured = cc.OrgConfig.MCPDefaultVirtualServer(p.KeyID)
	}
	if configured && !s.virtualServersEnabled {
		return virtualServerNotFound()
	}
	var view *callView
	if source := s.primePerUserMembers(cc, vs.Members, requestedServers(cc, vs)); source != nil {
		// Name incumbency is personal too: one member's catalog must not
		// quarantine another member's otherwise valid tool or prompt names.
		state := s.registry.state(viewKey(cc.Principal.OrgID, vs, configured) + "\x00user\x00" + cc.Principal.UserID)
		view = s.registry.composeFrom(vs, source, state)
	} else {
		view = s.registry.compose(cc.Principal.OrgID, vs, configured)
	}
	if access := cc.OrgConfig.MCPAccess(); !configured && access.Default != "allow" {
		view.denyAll = ruleAccessDefault
		view.denyAllStored = access.Stored
	}
	cc.VirtualServer = vs.ID
	cc.ctx = context.WithValue(cc.Context(), callViewKey{}, view)
	return nil
}

// usesVirtualServers is the switch between the two views. A call whose org
// config carries an mcp section is served through virtual servers. So is a
// call of any org but mcp.static.org_id without one: the operator's static
// registry is that org's, never another tenant's, so such an org sees only
// its own (empty) MCP config. Only a principal of the static org gets the
// legacy view, and, while static.org_id is unset (a YAML-only deployment,
// auth off), a principal with no org. With static.org_id set, a principal
// with no org is served an empty pool, as the gateway's tenancy resolves it
// (no org is not the static org).
func usesVirtualServers(s *Server, cc *CallContext) bool {
	if cc.OrgConfig != nil && cc.OrgConfig.MCP != nil {
		return true
	}
	return cc.Principal.OrgID != s.registry.StaticOrg()
}

func principalOf(cc *CallContext) vserver.Principal {
	return vserver.Principal{KeyID: cc.Principal.KeyID, AgentID: cc.Agent.AgentID, UserID: cc.Principal.UserID}
}

func virtualServerNotFound() *Denial {
	return &Denial{Type: models.ErrTypeNotFound, Code: "virtual_server_not_found", Message: "virtual server not found",
		RPCCode: ErrCodeInvalidRequest, HTTPStatus: http.StatusNotFound}
}

// --- The /mcp/v/{vs_id} address ---

type requestedVSKey struct{}

// WithVirtualServer marks r as addressed to the virtual server vsID, as the
// /mcp/v/{vs_id} routes do. Only this marker selects a virtual server: a
// vs_id query parameter on /mcp selects nothing.
func WithVirtualServer(r *http.Request, vsID string) *http.Request {
	return r.WithContext(context.WithValue(r.Context(), requestedVSKey{}, vsID))
}

func requestedVirtualServer(r *http.Request) string {
	if r == nil {
		return ""
	}
	id, _ := r.Context().Value(requestedVSKey{}).(string)
	return id
}

// --- The composed view of one call ---

type callViewKey struct{}

// callView is what step 4 resolved for a call that uses virtual servers.
type callView struct {
	vs      tenant.MCPVirtualServerSettings
	members []vserver.Member
	source  MemberSource
	tools   *vserver.View
	prompts *vserver.View
	byRef   map[string]*RegisteredTool // tool ref → registered tool
	sep     string                     // the registry's legacy separator
	// denyAll is the rule that refuses every tool (access.default on the
	// compatibility view), or "".
	denyAll string
	// denyAllStored is whether denyAll comes from a default the org stored
	// (mcp.access.default "deny"), not from the deny an absent one falls
	// back to.
	denyAllStored bool
}

// callViewOf returns the call's virtual-server view, nil for the legacy view.
func callViewOf(cc *CallContext) *callView {
	v, _ := cc.Context().Value(callViewKey{}).(*callView)
	return v
}

// member returns the view's member whose name prefix is key.
func (v *callView) member(key string) (vserver.Member, bool) {
	for _, m := range v.members {
		if m.Key() == key {
			return m, true
		}
	}
	return vserver.Member{}, false
}

// viewState is one virtual server's name holders and last rejections.
type viewState struct {
	mu                   sync.Mutex
	tools, prompts       vserver.Holders
	toolsRej, promptsRej []vserver.Rejection
}

// state returns the composition state of key, created on first use.
func (r *Registry) state(key string) *viewState {
	r.viewsMu.Lock()
	defer r.viewsMu.Unlock()
	st := r.views[key]
	if st == nil {
		st = &viewState{}
		r.views[key] = st
	}
	return st
}

// compose builds the view of one virtual server from the org's member
// source, keeping every incumbent's name across compositions.
func (r *Registry) compose(orgID string, vs tenant.MCPVirtualServerSettings, configured bool) *callView {
	return r.composeFrom(vs, r.sourceFor(orgID), r.state(viewKey(orgID, vs, configured)))
}

// composeFrom keeps a caller's resolved member source on the view used by both
// list assembly and resource/prompt/tool lookup. It never changes orgSource.
func (r *Registry) composeFrom(vs tenant.MCPVirtualServerSettings, source MemberSource, st *viewState) *callView {
	v := &callView{vs: vs, source: source, byRef: map[string]*RegisteredTool{}, sep: r.separator}
	var tools, prompts []vserver.Tool
	seen := map[string]bool{}
	for _, id := range vs.Members {
		if seen[id] {
			continue
		}
		seen[id] = true
		m := vserver.ParseMember(id)
		// A member whose account label fails LabelOK serves no resource;
		// Compose rejects its tools and prompts, so status shows why.
		if m.LabelOK() {
			v.members = append(v.members, m)
		}
		for _, rt := range v.source.MemberTools(m.Server) {
			t := vserver.Tool{Member: m, Name: rt.OrigName, Title: rt.Title, Description: rt.Upstream.Description}
			v.byRef[t.Ref()] = rt
			tools = append(tools, t)
		}
		for _, p := range v.source.MemberPrompts(m.Server) {
			prompts = append(prompts, vserver.Tool{Member: m, Name: p.Name, Description: p.Description})
		}
	}
	st.mu.Lock()
	defer st.mu.Unlock()
	v.tools, st.tools = vserver.Compose(vserver.SpecOf(vs), tools, st.tools)
	v.prompts, st.prompts = vserver.Compose(vserver.Spec{Naming: vs.Naming, Members: vs.Members}, prompts, st.prompts)
	st.toolsRej, st.promptsRej = v.tools.Rejected, v.prompts.Rejected
	return v
}

// Rejections returns the tools and prompts a virtual server left out at its
// last composition for their names (mcp_tool_name_collision, a quarantined
// newcomer, or a reserved name), for status and revisions. vs is
// the virtual server's id and slug, or "" for an org's compatibility view.
func (r *Registry) Rejections(orgID string, vs tenant.MCPVirtualServerSettings, configured bool) (tools, prompts []vserver.Rejection) {
	r.viewsMu.Lock()
	st := r.views[viewKey(orgID, vs, configured)]
	r.viewsMu.Unlock()
	if st == nil {
		return nil, nil
	}
	st.mu.Lock()
	defer st.mu.Unlock()
	return append([]vserver.Rejection(nil), st.toolsRej...), append([]vserver.Rejection(nil), st.promptsRej...)
}

// viewKey names a virtual server's composition state: the org's
// compatibility view, or the virtual server by its immutable id (by slug
// only when it has none), so a renamed slug keeps its name holders.
func viewKey(orgID string, vs tenant.MCPVirtualServerSettings, configured bool) string {
	switch {
	case !configured:
		return orgID + "\x00compat"
	case vs.ID != "":
		return orgID + "\x00id\x00" + vs.ID
	default:
		return orgID + "\x00slug\x00" + vs.Slug
	}
}
