package mcp

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/mcp/vserver"
)

// ErrUnknownKey deliberately conflates missing, inactive and foreign IDs.
var ErrUnknownKey = errors.New("unknown key")

// AdminServedTools is a read-only tools/list preview. The route authenticates
// the operator; Django additionally requires an org admin. No caller-supplied
// MCP headers, bearer token, agent claims or session enter this synthetic
// context. An optional same-org active key supplies its identity and ACL only;
// this is not key authentication or an execution/credential availability test.
//
// The actual runtime chain owns composition, access rules, quarantine and
// search mode. AdminTools remains the separate, unfiltered review inventory.
func (s *Server) AdminServedTools(ctx context.Context, orgID, vs, keyID string) (*gatewayadmin.MCPAdminTools, error) {
	return s.adminServedTools(ctx, orgID, vs, keyID, "")
}

func (s *Server) adminServedTools(ctx context.Context, orgID, vs, keyID, serverID string) (*gatewayadmin.MCPAdminTools, error) {
	tenancy := s.Tenancy()
	if tenancy == nil {
		return nil, ErrTenancyUnavailable
	}
	if orgID == "" || !tenancy.Known(orgID) {
		return nil, ErrUnknownOrg
	}
	caller := &callerAuth{principal: Principal{Kind: PrincipalAdmin, OrgID: orgID}}
	if keyID != "" {
		ia, ok := s.keyAuth.(*identityAuth)
		if !ok || ia.keys == nil {
			return nil, ErrUnknownKey
		}
		identity, allowed, denied, active := ia.keys.MCPReadIdentity(keyID)
		if !active || identity.OrgID != orgID {
			return nil, ErrUnknownKey
		}
		caller = &callerAuth{principal: Principal{Kind: PrincipalKey, OrgID: orgID, KeyID: identity.KeyID}, allowedTools: allowed, deniedTools: denied}
	}
	// Preserve cancellation, not ambient authentication or request narrowing.
	clean, cancel := context.WithCancel(context.Background())
	defer cancel()
	stop := context.AfterFunc(ctx, cancel)
	defer stop()
	clean = withCallerAuth(clean, caller)
	clean = withToolLists(clean, caller.allowedTools, caller.deniedTools)
	req, err := http.NewRequestWithContext(clean, "POST", "http://localhost/mcp", nil)
	if err != nil {
		return nil, err
	}
	if vs != "" {
		req = WithVirtualServer(req, vs)
	}
	cc := s.newCallContext(req, &Message{Method: MethodToolsList}, nil)
	defer cc.runFinalizers()
	s.runChain(cc, toolsListChain)
	if cc.Decision != nil {
		if cc.Decision.Code == "virtual_server_not_found" {
			return nil, ErrUnknownVirtualServer
		}
		return nil, ErrPolicyUnavailable
	}
	if serverID != "" {
		pool := TenantPoolOf(cc)
		if pool == nil {
			return nil, ErrTenancyUnavailable
		}
		if _, ok := pool.Server(serverID); !ok {
			return nil, ErrUnknownServer
		}
	}
	out := &gatewayadmin.MCPAdminTools{Tools: []*gatewayadmin.MCPAdminTool{}}
	view := callViewOf(cc)
	for _, tool := range cc.ToolList {
		// Search-mode meta-tools are gateway-owned, not invented upstream tools.
		row := &gatewayadmin.MCPAdminTool{Name: tool.Name, ServerID: "__gateway__", UpstreamName: tool.Name, Identity: toolIdentity(tool, nil)}
		if view != nil {
			if entry, ok := view.tools.Lookup(tool.Name); ok {
				row.ServerID, row.UpstreamName = entry.Member.Server, entry.Name
			} else if !view.searchMode() || !vserver.IsReserved(tool.Name) {
				return nil, ErrPolicyUnavailable
			}
		} else if registered, ok := s.registry.ResolveTool(tool.Name); ok {
			row.ServerID, row.UpstreamName = registered.Server, registered.OrigName
		} else {
			return nil, ErrPolicyUnavailable
		}
		if serverID != "" && (row.ServerID != serverID || (view != nil && view.searchMode() && vserver.IsReserved(tool.Name))) {
			continue
		}
		// Resolve display hints only after the runtime has selected the owning
		// server and original name. Aliases and search meta-tools cannot supply
		// a provider, and no other org's configuration is consulted.
		if cc.OrgConfig != nil && cc.OrgConfig.MCP != nil && row.ServerID != "__gateway__" {
			row.Identity = toolIdentity(Tool{Name: row.UpstreamName}, cc.OrgConfig.MCP.Servers[row.ServerID])
		}
		_ = json.Unmarshal(tool.GatewayMeta[MetaKeyRevision], &row.Revision)
		out.Tools = append(out.Tools, row)
	}
	return out, nil
}
