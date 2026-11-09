package mcp

// The org pool as the virtual servers' member source, and the client a
// virtual-server call goes through (M9 IR-4 and M10 IR-4, made at the W3b
// merge by the integrator: M10's composition reads Registry.SetOrgSource,
// M9's pools are TenantPool, and M13's hooks dispatch through callClient).

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
)

// PoolMembers serves the member lists of an org's virtual servers from the
// org's pool (MemberSource): a member's tools, resources and prompts are its
// pool server's, as the pool last listed them, and a server the pool does not
// hold has none. sep is the registry's legacy separator, which the tools'
// legacy names (RegisteredTool.Name) use.
func PoolMembers(pool TenantPool, sep string) MemberSource {
	if pool == nil {
		return nil
	}
	return poolMembers{pool: pool, sep: sep}
}

type poolMembers struct {
	pool TenantPool
	sep  string
}

// MemberTools returns the pool server's tools in upstream order; a name the
// upstream listed twice keeps its first definition, as RegisterServer does.
func (p poolMembers) MemberTools(serverID string) []*RegisteredTool {
	ps, ok := p.pool.Server(serverID)
	if !ok || len(ps.Tools) == 0 {
		return nil
	}
	out := make([]*RegisteredTool, 0, len(ps.Tools))
	seen := make(map[string]bool, len(ps.Tools))
	for _, t := range ps.Tools {
		if seen[t.Name] {
			continue
		}
		seen[t.Name] = true
		out = append(out, newRegisteredTool(serverID, p.sep, t))
	}
	return out
}

func (p poolMembers) MemberResources(serverID string) []Resource {
	if ps, ok := p.pool.Server(serverID); ok {
		return ps.Resources
	}
	return nil
}

func (p poolMembers) MemberPrompts(serverID string) []Prompt {
	if ps, ok := p.pool.Server(serverID); ok {
		return ps.Prompts
	}
	return nil
}

// callClient returns the client a call's upstream request on serverID goes
// through. A call served through a virtual server (step 4 set a view) whose
// org pool step 3 attached goes through that pool only, never the operator's
// static clients: an org server whose id equals a YAML server's is the org's
// own upstream, called with the org's credential, and a server the pool
// cannot serve is unavailable. The legacy view (no org MCP config; a
// principal of mcp.static.org_id, or with no org while that is unset), and a
// call with no tenancy installed, use the server's static clients, as before
// M9.
//
// For per_user servers, credKey is the verified principal's UserID, including
// a person key's reserved user_id (733/IR-2), never its display owner or caller
// metadata. Identity is not a credential: the pool refuses an empty user id
// or a missing credential override. Until credential resolution supplies one,
// this path fails closed. Built-in clients dispatch through the same wrapper.
func (s *Server) callClient(cc *CallContext, serverID string) (dispatchClient, *Denial) {
	pool := TenantPoolOf(cc)
	if pool == nil || callViewOf(cc) == nil {
		return s.healthyClient(serverID)
	}
	credKey := ""
	if ps, ok := pool.Server(serverID); ok && ps.PerUser {
		credKey = cc.Principal.UserID
	}
	uc, err := pool.Client(serverID, credKey, AuthOverride{})
	if err != nil {
		var denial *Denial
		if errors.As(err, &denial) {
			return nil, denial
		}
		slog.Warn("mcp org pool cannot serve the call", "org", pool.OrgID(), "server", serverID, "error", scrubbed(err))
		return nil, upstreamUnavailable(serverID)
	}
	// The pool checks connection health at acquisition; a lifetime change
	// after that must be reported by its dispatch guard as auth_required.
	if uc == nil {
		return nil, upstreamUnavailable(serverID)
	}
	settings := CallSettings{}
	if client := clientUnder(uc); client != nil {
		settings = client.CallSettings()
	}
	return &poolDispatchClient{UpstreamClient: uc, settings: settings}, nil
}

// Only settings are unwrapped. Operations must pass through the tenancy
// lifetime guard, including when a revoke arrives after callClient returns.
type dispatchClient interface {
	CallSettings() CallSettings
	CallToolRaw(context.Context, string, json.RawMessage, CallOptions) (json.RawMessage, error)
	ReadResourceRaw(context.Context, string, CallOptions) (json.RawMessage, error)
	GetPromptRaw(context.Context, string, json.RawMessage, CallOptions) (json.RawMessage, error)
}
type poolDispatchClient struct {
	UpstreamClient
	settings CallSettings
}

func (c *poolDispatchClient) CallSettings() CallSettings { return c.settings }
func (c *poolDispatchClient) CallToolRaw(ctx context.Context, n string, a json.RawMessage, o CallOptions) (json.RawMessage, error) {
	return c.CallTool(ctx, n, a, o)
}
func (c *poolDispatchClient) ReadResourceRaw(ctx context.Context, u string, o CallOptions) (json.RawMessage, error) {
	return c.ReadResource(ctx, u, o)
}
func (c *poolDispatchClient) GetPromptRaw(ctx context.Context, n string, a json.RawMessage, o CallOptions) (json.RawMessage, error) {
	return c.GetPrompt(ctx, n, a, o)
}

// clientUnder returns the *Client an upstream client is or wraps: today's
// adapter (legacyClient, the static clients the pools adopt), M13's factory
// clients (Unwrap) and a pool's wrappers (Upstream); nil for any other kind.
func clientUnder(uc UpstreamClient) *Client {
	for i := 0; i < 8 && uc != nil; i++ {
		switch v := uc.(type) {
		case *legacyClient:
			return v.c
		case interface{ Unwrap() *Client }:
			return v.Unwrap()
		case interface{ Upstream() UpstreamClient }:
			uc = v.Upstream()
		default:
			return nil
		}
	}
	return nil
}
