package mcp

import (
	"context"
	"log/slog"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// primePerUserMembers builds a request-local source for personal members.
// Every caller resolves their own credential, even after another member has
// listed the server. The pool's lists contain only organization-level/offline
// inventory; discovery through a personal client is never published there.
// Without an account only those offline tools remain, so known tool calls can
// still explain that authentication is required. Personal resources and prompts
// have no fallback. A nil result means the view has no personal members.
// only, when not nil, names the servers whose credential is resolved; every
// other per_user member keeps its organization-level/offline entry.
func (s *Server) primePerUserMembers(cc *CallContext, members []string, only map[string]bool) MemberSource {
	pool := TenantPoolOf(cc)
	if pool == nil {
		return nil
	}
	source := poolMembers{pool: pool, sep: s.registry.Separator(), personal: map[string]PoolServer{}}
	resolver := s.Deps().CredentialResolver
	seen := map[string]bool{}
	for _, id := range members {
		server := vserver.ParseMember(id).Server
		if seen[server] {
			continue
		}
		seen[server] = true
		ps, ok := pool.Server(server)
		if !ok || !ps.PerUser {
			continue
		}
		ps.Resources, ps.Prompts = nil, nil
		source.personal[server] = ps
		if cc.Principal.UserID == "" || (only != nil && !only[server]) {
			continue
		}
		if admission, ok := pool.(ClientAdmissionPool); ok && admission.ClientAdmission(server) != nil {
			continue
		}
		probe := &CallContext{ctx: cc.Context(), RequestID: cc.RequestID, Method: cc.Method, Principal: cc.Principal,
			OrgConfig: cc.OrgConfig, Upstream: UpstreamRef{ServerID: server}}
		auth, d := resolver.Resolve(probe)
		if cc.personal == nil {
			cc.personal = map[string]personalCredential{}
		}
		cc.personal[server] = personalCredential{auth: auth, ref: probe.Credential, denial: d}
		if d != nil || auth == nil || len(auth.Header) == 0 {
			continue
		}
		client, err := pool.Client(server, cc.Principal.UserID, *auth)
		if err == nil {
			ps, err = personalServerCatalog(cc.Context(), client, ps)
		}
		if err != nil {
			slog.Warn("mcp per_user server not listed through the caller's account", "org", pool.OrgID(), "server", server, "error", scrubbed(err))
			continue
		}
		source.personal[server] = ps
	}
	if len(source.personal) == 0 {
		return nil
	}
	return source
}

// requestedServers narrows step 4 to the member a single-item request names:
// a tools/call, resources/read or prompts/get resolves the caller's
// credential, builds a client and lists only for the member whose name prefix
// its tool, resource (mcp://<member>/...) or prompt carries, so a call on one
// server never touches another member's account. nil means every member: a
// list, flat naming, a tool name an override may have changed, or a name no
// member prefixes.
func requestedServers(cc *CallContext, vs tenant.MCPVirtualServerSettings) map[string]bool {
	var match func(key string) bool
	switch cc.Method {
	case MethodResourcesRead:
		rest, ok := strings.CutPrefix(cc.legacy.resourceURI, resourceScheme)
		key, _, found := strings.Cut(rest, "/")
		if !ok || !found {
			return nil
		}
		match = func(k string) bool { return k == key }
	case MethodToolsCall, MethodPromptsGet:
		if vs.Naming == vserver.NamingFlat {
			return nil
		}
		name := cc.legacy.promptName
		if cc.Method == MethodToolsCall {
			for _, ov := range vs.Overrides {
				if ov != nil && ov.Name != "" {
					return nil
				}
			}
			name = cc.Tool.ExposedName
		}
		folded := vserver.FoldKey(name)
		match = func(k string) bool { return strings.HasPrefix(folded, vserver.FoldKey(k+vserver.Separator)) }
	default:
		return nil
	}
	only := map[string]bool{}
	for _, id := range vs.Members {
		if m := vserver.ParseMember(id); m.LabelOK() && match(m.Key()) {
			only[m.Server] = true
		}
	}
	if len(only) == 0 {
		return nil
	}
	return only
}

// personalCredential is one per_user member's credential as step 4 resolved
// it for the request: the override and the reference Resolve recorded on its
// probe, or the refusal.
type personalCredential struct {
	auth   *AuthOverride
	ref    CredentialRef
	denial *Denial
}

// primedCredential is step 4's answer for serverID, when step 13 may use it
// in place of asking the resolver again: the same request, caller and server,
// and a grant whose lifetime has not ended since.
func (cc *CallContext) primedCredential(serverID string) (personalCredential, bool) {
	p, ok := cc.personal[serverID]
	if !ok || (p.auth != nil && p.auth.Lifetime != nil && p.auth.Lifetime.Err() != nil) {
		return personalCredential{}, false
	}
	return p, true
}

// personalRefusal answers a request for an item of the member named by key
// that the view does not hold because step 4 refused the caller's own
// credential for that per_user member: the refusal (auth_required with the
// caller's own connect link), when refused finds no access rule against the
// item. Otherwise nil, and the item is not found.
func (v *callView) personalRefusal(cc *CallContext, key string, refused func(vserver.Member) string) *Denial {
	m, ok := v.member(key)
	if !ok {
		return nil
	}
	if p, ok := cc.personal[m.Server]; ok && p.denial != nil && refused(m) == "" {
		return p.denial
	}
	return nil
}

func personalServerCatalog(ctx context.Context, client UpstreamClient, ps PoolServer) (PoolServer, error) {
	raw, err := client.ListTools(ctx)
	if err != nil {
		return PoolServer{}, err
	}
	if ps.Tools, err = decodeList[Tool](raw); err != nil {
		return PoolServer{}, err
	}
	if raw, err := client.ListResources(ctx); err == nil {
		ps.Resources, _ = decodeList[Resource](raw)
	}
	if raw, err := client.ListPrompts(ctx); err == nil {
		ps.Prompts, _ = decodeList[Prompt](raw)
	}
	// An optional list failure must not hide revocation during discovery and
	// allow an earlier successful list from the ended grant into this view.
	if checked, ok := client.(interface{ CheckCredential(context.Context) error }); ok {
		if err := checked.CheckCredential(ctx); err != nil {
			return PoolServer{}, err
		}
	}
	return ps, ctx.Err()
}
