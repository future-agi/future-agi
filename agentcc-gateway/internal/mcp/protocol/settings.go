package protocol

import "time"

// Defaults of the settings the operator config does not carry.
const (
	// DefaultMaxBodyBytes matches mcp.max_body_bytes' default (4 MiB).
	DefaultMaxBodyBytes int64 = 4 << 20
	// DefaultListTTL is the ttlMs of a 2026 list and discover result.
	DefaultListTTL = time.Minute
	// DefaultPageSize is how many items one page of a merged list holds.
	DefaultPageSize = 1000
	// DefaultStreamAfter is how long a call that may stream runs before its
	// response turns into an SSE stream (when the client accepts SSE).
	DefaultStreamAfter = 5 * time.Second
	// DefaultKeepAlive spaces the keep-alive comments of a streamed response.
	DefaultKeepAlive = 15 * time.Second
)

// AllVersions are every revision the gateway can serve, newest first; they
// equal config.MCPProtocolVersions (decision 31).
func AllVersions() []string {
	return []string{Version2026, Version20251125, Version20250618, Version20250326, Version20241105}
}

// Settings are the protocol rules' operator inputs, one set per MCP server.
type Settings struct {
	// Versions are the revisions served (mcp.protocol.versions), newest
	// first.
	Versions []string
	// MaxBodyBytes caps a POST body (mcp.max_body_bytes).
	MaxBodyBytes int64
	// AllowedOrigins are the browser origins accepted besides the gateway's
	// own host (mcp.allowed_origins).
	AllowedOrigins []string
	// OwnHosts are the gateway's own host[:port] names, which an Origin may
	// always name (the host of mcp.public_url).
	OwnHosts []string
	// ListTTL is the ttlMs of a 2026 list or discover result.
	ListTTL time.Duration
	// PageSize is the number of items per page of a merged list.
	PageSize int
	// StreamAfter and KeepAlive govern a response streamed as SSE.
	StreamAfter time.Duration
	KeepAlive   time.Duration
	// Cursors seals list cursors (M1x's state codec, cursor purpose).
	Cursors Sealer
}

// WithDefaults returns s with every zero field at its default. Cursors has no
// default here: the caller supplies a codec.
func (s Settings) WithDefaults() Settings {
	if len(s.Versions) == 0 {
		s.Versions = AllVersions()
	}
	if s.MaxBodyBytes <= 0 {
		s.MaxBodyBytes = DefaultMaxBodyBytes
	}
	if s.ListTTL <= 0 {
		s.ListTTL = DefaultListTTL
	}
	if s.PageSize <= 0 {
		s.PageSize = DefaultPageSize
	}
	if s.StreamAfter <= 0 {
		s.StreamAfter = DefaultStreamAfter
	}
	if s.KeepAlive <= 0 {
		s.KeepAlive = DefaultKeepAlive
	}
	return s
}
