package mcp

import (
	"context"
	"net/http"
	"slices"
	"testing"
)

// A request for one tool, resource or prompt checks the
// caller's credential, builds a client and lists only for the per_user
// member it names. Lists, and names no member prefixes, still compose every
// member's personal catalog.
type twoPersonalPool struct {
	TenantPool
	built []string
}

func (p *twoPersonalPool) OrgID() string { return "org-a" }
func (p *twoPersonalPool) Server(id string) (PoolServer, bool) {
	if id != "alpha" && id != "beta" {
		return PoolServer{}, false
	}
	return PoolServer{ID: id, PerUser: true, Tools: []Tool{{Name: "common"}}}, true
}
func (p *twoPersonalPool) Client(server, user string, _ AuthOverride) (UpstreamClient, error) {
	p.built = append(p.built, server+"/"+user)
	return &personalCatalogFixture{account: server}, nil
}

type resolvedServers struct{ servers []string }

func (r *resolvedServers) Resolve(cc *CallContext) (*AuthOverride, *Denial) {
	r.servers = append(r.servers, cc.Upstream.ServerID)
	return &AuthOverride{Header: http.Header{"Authorization": {"Bearer " + cc.Upstream.ServerID}}}, nil
}

func TestPersonalCatalogSingleItemRequestResolvesOnlyItsMember(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	pool := &twoPersonalPool{}
	resolver := &resolvedServers{}
	s.SetDeps(Deps{CredentialResolver: resolver})
	s.registry.SetOrgSource(func(string) MemberSource { return PoolMembers(pool, s.registry.Separator()) })
	cfg := orgConfig(t, `{"mcp":{"enabled":true,"access":{"default":"allow"},"virtual_servers":{"both":{"id":"both","members":["alpha","beta"],"access":{"default_for_org":true}}}}}`)
	view := func(method string, set func(*CallContext)) *CallContext {
		t.Helper()
		resolver.servers, pool.built = nil, nil
		cc := &CallContext{ctx: context.WithValue(context.Background(), tenantPoolKey{}, pool),
			Principal: oauthPrincipal("member-a"), OrgConfig: cfg, Method: method}
		set(cc)
		if d := resolveVirtualServer(s, cc); d != nil {
			t.Fatalf("%s: %v", method, d)
		}
		return cc
	}
	for _, tc := range []struct {
		method string
		set    func(*CallContext)
	}{
		{MethodToolsCall, func(cc *CallContext) { cc.Tool.ExposedName = "beta__beta" }},
		{MethodResourcesRead, func(cc *CallContext) { cc.legacy.resourceURI = "mcp://beta/file:///beta" }},
		{MethodPromptsGet, func(cc *CallContext) { cc.legacy.promptName = "beta__beta" }},
	} {
		cc := view(tc.method, tc.set)
		if !slices.Equal(resolver.servers, []string{"beta"}) || !slices.Equal(pool.built, []string{"beta/member-a"}) {
			t.Errorf("%s naming beta checked credentials for %v and built clients %v; want beta only", tc.method, resolver.servers, pool.built)
		}
		// The named member's own catalog is still in the view.
		switch tc.method {
		case MethodToolsCall:
			if _, ok := callViewOf(cc).tools.Lookup("beta__beta"); !ok {
				t.Errorf("tools/call view lacks beta's personal tool")
			}
		case MethodResourcesRead:
			if d := authorizeResource(s, cc); d != nil {
				t.Errorf("resources/read of beta's resource: %v", d)
			}
		case MethodPromptsGet:
			if d := authorizePrompt(s, cc); d != nil {
				t.Errorf("prompts/get of beta's prompt: %v", d)
			}
		}
	}
	for _, tc := range []struct {
		method, name string
	}{
		{MethodToolsList, ""},
		{MethodToolsCall, "call_tool"},
	} {
		view(tc.method, func(cc *CallContext) { cc.Tool.ExposedName = tc.name })
		if !slices.Equal(resolver.servers, []string{"alpha", "beta"}) || len(pool.built) != 2 {
			t.Errorf("%s %q checked credentials for %v and built clients %v; want every per_user member", tc.method, tc.name, resolver.servers, pool.built)
		}
	}
}
