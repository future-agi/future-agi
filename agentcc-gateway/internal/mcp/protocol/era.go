// Package protocol holds the MCP protocol rules the gateway's Streamable HTTP
// endpoint applies before a request reaches a method handler: which protocol
// era a request belongs to, the 2026-07-28 per-request metadata and mirrored
// headers, the HTTP rules shared by both eras, the caching fields of a
// 2026 result, per-request log levels and sealed list cursors.
//
// The package is pure: it reads headers and JSON and returns verdicts, so the
// mcp package's handlers and later lanes (the session manager's sessions, the upstream client's upstream
// client) share one copy of each rule.
package protocol

import (
	"encoding/json"
	"net/http"
)

// Protocol revisions. Version2026 is the stateless revision; the others are
// legacy (session) revisions, newest first.
const (
	Version2026     = "2026-07-28"
	Version20251125 = "2025-11-25"
	Version20250618 = "2025-06-18"
	Version20250326 = "2025-03-26"
	Version20241105 = "2024-11-05"
)

// DefaultLegacyVersion is the revision a legacy request without an
// MCP-Protocol-Version header is assumed to speak (the 2025-06-18 transport
// spec: a server SHOULD assume 2025-03-26).
const DefaultLegacyVersion = Version20250326

// Keys of a 2026-07-28 request's params._meta.
const (
	MetaProtocolVersion    = "io.modelcontextprotocol/protocolVersion"
	MetaClientCapabilities = "io.modelcontextprotocol/clientCapabilities"
	MetaClientInfo         = "io.modelcontextprotocol/clientInfo"
	// MetaLogLevel is the per-request log level. Published drafts also spell
	// it as a bare "logLevel"; both are read.
	MetaLogLevel     = "io.modelcontextprotocol/logLevel"
	metaLogLevelBare = "logLevel"
)

// MetaServerInfo is the key of a 2026-07-28 result's _meta that names the
// server (the per-response protocol field; servers SHOULD send it).
const MetaServerInfo = "io.modelcontextprotocol/serverInfo"

// Request headers of the Streamable HTTP transport.
const (
	HeaderProtocolVersion = "MCP-Protocol-Version"
	HeaderSessionID       = "Mcp-Session-Id"
	HeaderLastEventID     = "Last-Event-ID"
	HeaderMethod          = "Mcp-Method"
	HeaderName            = "Mcp-Name"
	// HeaderParamPrefix starts every mirrored tool-parameter header,
	// Mcp-Param-{Name}.
	HeaderParamPrefix = "Mcp-Param-"
)

// IsStateless reports whether version is a stateless revision: one whose
// requests carry their own metadata and need no session.
func IsStateless(version string) bool { return version == Version2026 }

// Meta is the protocol part of a request's params._meta.
type Meta struct {
	// Present is true when _meta carries the
	// io.modelcontextprotocol/protocolVersion key, whatever its value: the key
	// that marks a 2026-07-28 request ("Era per request"). Other reserved
	// keys (a legacy client may send io.modelcontextprotocol/related-task and
	// the like) do not.
	Present            bool
	ProtocolVersion    string
	ClientCapabilities json.RawMessage
	ClientInfo         json.RawMessage
	LogLevel           string
}

// ParseMeta reads the protocol keys of params._meta. Params that are absent,
// not an object, or without _meta give the zero Meta.
func ParseMeta(params json.RawMessage) Meta {
	var p struct {
		Meta map[string]json.RawMessage `json:"_meta"`
	}
	if len(params) == 0 || json.Unmarshal(params, &p) != nil || p.Meta == nil {
		return Meta{}
	}
	var m Meta
	var version json.RawMessage
	version, m.Present = p.Meta[MetaProtocolVersion]
	_ = json.Unmarshal(version, &m.ProtocolVersion)
	if c := p.Meta[MetaClientCapabilities]; len(c) > 0 && string(c) != "null" {
		m.ClientCapabilities = c
	}
	if c := p.Meta[MetaClientInfo]; len(c) > 0 && string(c) != "null" {
		m.ClientInfo = c
	}
	if raw, ok := p.Meta[MetaLogLevel]; ok {
		_ = json.Unmarshal(raw, &m.LogLevel)
	} else if raw, ok := p.Meta[metaLogLevelBare]; ok {
		_ = json.Unmarshal(raw, &m.LogLevel)
	}
	return m
}

// Era is the protocol era one request is served under.
type Era struct {
	// Stateless is true for a 2026-07-28 request: no session, and
	// Mcp-Session-Id and Last-Event-ID are ignored.
	Stateless bool
	// Version is the revision the request names: _meta's protocolVersion on
	// a stateless request (possibly empty or unsupported until Validate
	// runs), else the MCP-Protocol-Version header, else "".
	Version string
	Meta    Meta
	// Initialize is true for an initialize request, which negotiates its
	// revision in its params rather than in the header.
	Initialize bool
}

// Classify decides a request's era ("Era per request").
// initialize is always legacy: it opens a session. Any other request is
// stateless when its _meta carries io.modelcontextprotocol/protocolVersion or
// its MCP-Protocol-Version header names a stateless revision; a stateless
// client that left the key out is then refused by Validate (-32021) instead
// of being served as legacy. Everything else is legacy, Mcp-Session-Id or
// not: a legacy client in its session may send other reserved _meta keys.
func Classify(method string, params json.RawMessage, h http.Header) Era {
	header := h.Get(HeaderProtocolVersion)
	if method == "initialize" {
		return Era{Version: header, Initialize: true}
	}
	m := ParseMeta(params)
	if m.Present || IsStateless(header) {
		return Era{Stateless: true, Version: m.ProtocolVersion, Meta: m}
	}
	return Era{Version: header, Meta: m}
}

// Served returns the revision a request is served under, for the call's
// record and the request context: the stateless revision, else the legacy
// request's header, else DefaultLegacyVersion.
func (e Era) Served() string {
	if e.Stateless || e.Version != "" {
		return e.Version
	}
	return DefaultLegacyVersion
}

// Validate applies the version and metadata rules of the request's era
// against the revisions the gateway serves (mcp.protocol.versions):
//   - stateless: _meta must carry protocolVersion and clientCapabilities
//     (-32021), and the version must be a served stateless revision (-32022
//     with data.supported);
//   - legacy: an MCP-Protocol-Version header must name a served revision
//     (-32022). A request without the header is served as
//     DefaultLegacyVersion, so only while that revision is served; when the
//     operator removed it, the request is refused (-32022; the 2026-07-28
//     transport: a server that does not support such clients MUST reject a
//     request without the header). initialize negotiates its revision in its
//     params, so only its header, when sent, is checked here.
//
// It returns nil when the request may proceed.
func (e Era) Validate(served []string) *Error {
	if !e.Stateless {
		version := e.Version
		if version == "" && !e.Initialize {
			version = DefaultLegacyVersion
		}
		if version != "" && !contains(served, version) {
			return UnsupportedVersion(version, served)
		}
		return nil
	}
	if e.Meta.ProtocolVersion == "" || len(e.Meta.ClientCapabilities) == 0 {
		return MissingClientCapability()
	}
	if !IsStateless(e.Meta.ProtocolVersion) || !contains(served, e.Meta.ProtocolVersion) {
		return UnsupportedVersion(e.Meta.ProtocolVersion, served)
	}
	return nil
}

func contains(list []string, v string) bool {
	for _, s := range list {
		if s == v {
			return true
		}
	}
	return false
}
