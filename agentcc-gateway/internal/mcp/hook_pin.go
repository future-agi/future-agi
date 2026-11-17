package mcp

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/futureagi/agentcc-gateway/internal/mcp/pin"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// hookCheckPin is step 9 of the tools/call chain (chain.go).
const hookCheckPin = "checkPin"

// MetaKeyRevision is the _meta key of a listed tool's revision.
const MetaKeyRevision = "io.futureagi/revision"

// metaKeyPin is the _meta key that marks a tool served in mode warn whose
// revision an admin has not accepted.
const metaKeyPin = "io.futureagi/pin"

// poolSourceOrg is PoolServer.Source of a server the org saved itself
// (tenancy.SourceOrg).
const poolSourceOrg = "org"

// PinSubject is one tool as pinning judges it: its server, its name on the
// upstream, its revision and its canonical definition.
type PinSubject struct {
	ServerID   string
	Tool       string
	Revision   string
	Definition []byte
}

type callPinKey struct{}

// checkPin records the revision of the tool the call resolved (cc.Tool.Revision,
// which the call's record carries), then has Deps.Pins check it, refusing a
// tool whose definition an admin has not accepted with tool_quarantined.
// Only a tool of a server the org saved itself (PoolServer.Source "org",
// a catalog add included) is pinned: the operator's own servers (gateway
// YAML, managed fagi.* and built-in servers) change only with a deploy.
// Keep observeHook as its first statement.
func checkPin(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookCheckPin)
	rt := cc.legacy.tool
	if rt == nil {
		return nil
	}
	rev, canon, ok := toolRevision(rt.Upstream)
	if !ok {
		return nil
	}
	cc.Tool.Revision = rev
	if pinnable(cc, rt.Server) {
		cc.ctx = context.WithValue(cc.Context(), callPinKey{}, PinSubject{ServerID: rt.Server, Tool: rt.OrigName, Revision: rev, Definition: canon})
	}
	return s.Deps().Pins.CheckCall(cc)
}

// pinnable reports whether a tool of serverID is pinned for this call: the
// call is served through its org's pool (a virtual server view) and the
// server is one the org saved.
func pinnable(cc *CallContext, serverID string) bool {
	if callViewOf(cc) == nil {
		return false
	}
	pool := TenantPoolOf(cc)
	if pool == nil {
		return false
	}
	ps, ok := pool.Server(serverID)
	return ok && ps.Source == poolSourceOrg && !ps.Builtin && !ps.Managed
}

// noPins is Deps.Pins' default: nothing pinned, nothing quarantined.
type noPins struct{}

func (noPins) CheckCall(*CallContext) *Denial                 { return nil }
func (noPins) FilterList(_ *CallContext, tools []Tool) []Tool { return tools }

// OrgPins is pinning by the org's mcp.pinning, the Deps.Pins the wiring
// installs: a tool of an org server whose revision is not the pinned one, or
// a new tool on a server that has pins, is held (hidden from tools/list and
// refused on tools/call with tool_quarantined) until an admin accepts it,
// which writes the pin; in mode warn it is listed with a warning instead.
type OrgPins struct{}

// CheckCall refuses the call's tool when pinning holds it.
func (OrgPins) CheckCall(cc *CallContext) *Denial {
	sub, ok := cc.Context().Value(callPinKey{}).(PinSubject)
	if !ok {
		return nil
	}
	v := pin.NewPolicy(cc.OrgConfig.MCPPinning()).Judge(sub.ServerID, sub.Tool, sub.Revision)
	if !v.Held {
		return nil
	}
	return pinQuarantined(cc.Tool.ExposedName, v, sub.Revision)
}

// FilterList drops every listed tool pinning holds and marks one mode warn
// serves unaccepted.
func (OrgPins) FilterList(cc *CallContext, tools []Tool) []Tool {
	subjects, _ := cc.Context().Value(listPinKey{}).(map[string]PinSubject)
	if len(subjects) == 0 {
		return tools
	}
	policy := pin.NewPolicy(cc.OrgConfig.MCPPinning())
	out := make([]Tool, 0, len(tools))
	for _, t := range tools {
		sub, ok := subjects[t.Name]
		if !ok {
			out = append(out, t)
			continue
		}
		v := policy.Judge(sub.ServerID, sub.Tool, sub.Revision)
		if v.Held {
			continue
		}
		if v.Reason != "" {
			t = withGatewayMeta(t, metaKeyPin, map[string]string{"reason": v.Reason, "revision_expected": v.Expected})
		}
		out = append(out, t)
	}
	return out
}

// pinQuarantined is the in-band refusal of a held tool: metadata
// {revision_expected, revision_seen, reason}, revision_expected left out for
// a new tool, which has none.
func pinQuarantined(name string, v pin.Verdict, seen string) *Denial {
	meta := map[string]any{"reason": v.Reason, "revision_seen": seen}
	msg := fmt.Sprintf("tool %q is held for review: its definition changed since an admin accepted it", name)
	if v.Expected != "" {
		meta["revision_expected"] = v.Expected
	} else {
		msg = fmt.Sprintf("tool %q is held for review: the server added it after an admin accepted its tools", name)
	}
	return &Denial{Type: models.ErrTypePermission, Code: "tool_quarantined", Message: msg, Metadata: meta}
}

// withGatewayMeta returns t with one more io.futureagi/* _meta entry of its
// own; a value that does not marshal is left out.
func withGatewayMeta(t Tool, key string, value any) Tool {
	raw, err := json.Marshal(value)
	if err != nil {
		return t
	}
	meta := make(map[string]json.RawMessage, len(t.GatewayMeta)+1)
	for k, v := range t.GatewayMeta {
		meta[k] = v
	}
	meta[key] = raw
	t.GatewayMeta = meta
	return t
}
