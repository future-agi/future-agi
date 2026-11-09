package mcp

import (
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/mcp/protocol"
)

// handleDiscover serves server/discover (2026-07-28): the revisions served,
// the capabilities and the gateway's serverInfo (in _meta, as every 2026
// result), with ttlMs and a cache scope that is always private, since
// capabilities and extensions can vary with the virtual server and the
// principal. Like every method it passes HandlePost's auth chokepoint, which
// answers a call with no credential 401 with the challenge wherever a
// principal is needed (all but the release line's keyless case,
// servesKeyless), which is how OAuth clients find the protected resource
// metadata.
//
// listChanged is not declared: on 2026-07-28 a change notification reaches a
// client only on a subscriptions/listen stream, which the gateway does not
// serve yet, so declaring it would promise notifications nothing delivers.
// The capabilities come from the gateway's registry; narrowing them to the
// caller's virtual server is M10's (the cache scope stays private for that).
//
// It is a 2026-07-28 method, and every discover request carries that
// revision's _meta (the spec's request, and its stdio probe of a server's
// era). A legacy request, whose revisions have no such method, gets the
// unknown-method answer (HTTP 200 on legacy), as ping does on 2026.
//
// Owner: M11.
func (s *Server) handleDiscover(w http.ResponseWriter, r *http.Request, msg *Message) {
	if pw := asProtocolWriter(w); pw != nil && !pw.pr.era.Stateless {
		writeMethodNotFound(w, msg)
		return
	}
	set := s.protocolSettings()
	caps := ServerCapabilities{
		Tools:   &ToolsCapability{},
		Logging: json.RawMessage(`{}`),
		// The gateway passes definitions and results through raw, extension
		// fields included, but vouches for no extension's semantics, so it
		// declares none.
		Extensions: json.RawMessage(`{}`),
	}
	if s.registry.ResourceCount() > 0 {
		caps.Resources = &ResourcesCapability{}
	}
	if s.registry.PromptCount() > 0 {
		caps.Prompts = &PromptsCapability{}
	}
	info := Implementation{Name: GatewayServerName, Version: s.serverInfo.Version}
	raw, err := json.Marshal(DiscoverResult{SupportedVersions: set.Versions, Capabilities: caps})
	if err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInternal, "failed to marshal result")
		return
	}
	infoRaw, _ := json.Marshal(info)
	env := protocol.Envelope{ServerInfo: infoRaw, Cacheable: true, TTL: set.ListTTL, CacheScope: protocol.CacheScopePrivate}
	setCacheScope(w, protocol.CacheScopePrivate)
	writeJSONRPCResult(w, msg.ID, env.Decorate(raw))
}
