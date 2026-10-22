package models

// ContentMode is how much request content the gateway keeps: full, metadata (no
// bodies or request headers) or none
// (gateway-owned metadata only).
type ContentMode string

const (
	ContentModeFull     ContentMode = "full"
	ContentModeMetadata ContentMode = "metadata"
	ContentModeNone     ContentMode = "none"
)

// Where the winning content mode came from.
const (
	PrivacySourceOrg     = "org"
	PrivacySourceKey     = "key"
	PrivacySourceRequest = "request"
	PrivacySourceDefault = "default" // the global privacy.default_content_mode
)

// Privacy is a request's resolved content mode. The most restrictive of the
// org, key, request header and global default wins; Source names which one.
// Empty means it was not resolved, which consumers treat as full.
type Privacy struct {
	ContentMode ContentMode
	Source      string
}
