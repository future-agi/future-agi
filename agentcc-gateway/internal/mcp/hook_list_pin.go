package mcp

import "context"

// hookPinFilter is a tools/list step (chain.go).
const hookPinFilter = "pinFilter"

type listPinKey struct{}

// pinFilter gives every tool a virtual server lists its revision, in
// _meta["io.futureagi/revision"], taken from the upstream's own definition
// (before any override), then has Deps.Pins drop the tools pinning holds. The
// legacy view lists the operator's servers, which are not pinned.
// Keep observeHook as its first statement.
func pinFilter(s *Server, cc *CallContext) *Denial {
	observeHook(cc, hookPinFilter)
	v := callViewOf(cc)
	if v == nil || len(cc.ToolList) == 0 {
		return nil
	}
	byExposed := make(map[string]*RegisteredTool, len(v.tools.Entries))
	for _, e := range v.tools.Entries {
		if rt := v.byRef[e.Ref()]; rt != nil {
			byExposed[e.Exposed] = rt
		}
	}
	subjects := map[string]PinSubject{}
	for i, t := range cc.ToolList {
		rt := byExposed[t.Name]
		if rt == nil {
			continue
		}
		rev, canon, ok := toolRevision(rt.Upstream)
		if !ok {
			continue
		}
		cc.ToolList[i] = withGatewayMeta(t, MetaKeyRevision, rev)
		if pinnable(cc, rt.Server) {
			subjects[t.Name] = PinSubject{ServerID: rt.Server, Tool: rt.OrigName, Revision: rev, Definition: canon}
		}
	}
	cc.ctx = context.WithValue(cc.Context(), listPinKey{}, subjects)
	cc.ToolList = s.Deps().Pins.FilterList(cc, cc.ToolList)
	return nil
}
