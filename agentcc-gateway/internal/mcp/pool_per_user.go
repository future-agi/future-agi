package mcp

import (
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
)

// primePerUserMembers lets a person's own account list a per_user member of
// the virtual server they address. A per_user server has no account of the
// organization's own, so its pool learns its tools only through a member's
// own client (the pool lists a per_user server through the first such client
// it builds): when the caller is a person and a per_user member has listed
// nothing yet, the caller's own credential is resolved and their own client
// built, which lists it. Nothing is done for a caller with no user, or for a
// member already listed. A caller with no account of that server leaves it
// unlisted; a call on it is refused where every per_user call is (step 13),
// and is never made through another person's account.
func (s *Server) primePerUserMembers(cc *CallContext, members []string) {
	pool := TenantPoolOf(cc)
	if pool == nil || cc.Principal.UserID == "" {
		return
	}
	resolver := s.Deps().CredentialResolver
	seen := map[string]bool{}
	for _, id := range members {
		server := vserver.ParseMember(id).Server
		if seen[server] {
			continue
		}
		seen[server] = true
		ps, ok := pool.Server(server)
		if !ok || !ps.PerUser || len(ps.Tools) > 0 {
			continue
		}
		probe := &CallContext{ctx: cc.Context(), RequestID: cc.RequestID, Method: cc.Method, Principal: cc.Principal,
			OrgConfig: cc.OrgConfig, Upstream: UpstreamRef{ServerID: server}}
		auth, d := resolver.Resolve(probe)
		if d != nil || auth == nil || len(auth.Header) == 0 {
			continue
		}
		if _, err := pool.Client(server, cc.Principal.UserID, *auth); err != nil {
			slog.Warn("mcp per_user server not listed through the caller's account", "org", pool.OrgID(), "server", server, "error", scrubbed(err))
		}
	}
}
