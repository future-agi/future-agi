package wire

import (
	"encoding/json"
	"slices"
)

// Protocol revisions the client names itself.
const (
	// Version2026 is the stateless era: no initialize, no session, per
	// request _meta and mirrored headers.
	Version2026 = "2026-07-28"
	// VersionHTTPSSE is the 2024-11-05 HTTP+SSE transport's revision.
	VersionHTTPSSE = "2024-11-05"
)

// _meta keys of a 2026-07-28 request.
const (
	MetaProtocolVersion = "io.modelcontextprotocol/protocolVersion"
	MetaClientCaps      = "io.modelcontextprotocol/clientCapabilities"
	MetaClientInfo      = "io.modelcontextprotocol/clientInfo"
	MetaProgressToken   = "progressToken"
)

// JSON-RPC codes of 2026-07-28 the client acts on.
const (
	CodeUnsupportedVersion = -32022
)

// RequestMeta is the _meta every 2026-07-28 request carries.
func RequestMeta(clientInfo json.RawMessage) map[string]json.RawMessage {
	version, _ := json.Marshal(Version2026)
	return map[string]json.RawMessage{
		MetaProtocolVersion: version,
		MetaClientCaps:      json.RawMessage(`{}`),
		MetaClientInfo:      clientInfo,
	}
}

// Supports2026 reports whether a server/discover result is 2026-shaped: an
// object whose supportedVersions lists 2026-07-28. Anything else, whatever
// error or shape, means the server is of an older era (the fallback does
// not depend on one error code).
func Supports2026(result json.RawMessage) bool {
	var d struct {
		SupportedVersions []string `json:"supportedVersions"`
	}
	return json.Unmarshal(result, &d) == nil && slices.Contains(d.SupportedVersions, Version2026)
}
