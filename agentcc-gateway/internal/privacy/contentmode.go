package privacy

import (
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// LogContentHeader is the request header a caller tightens its own content
// mode with. It can only tighten.
const LogContentHeader = "X-Agentcc-Log-Content"

// KeyLogContentMetadata is the API-key metadata key that holds the key's
// content mode (Django's key serializer writes it; key sync carries it).
const KeyLogContentMetadata = "log_content"

// KeyLogContentRequestMetadata is where a request's context holds the
// presented key's log_content: the auth plugin copies every key metadata
// entry under a "key_" prefix.
const KeyLogContentRequestMetadata = "key_" + KeyLogContentMetadata

// ContentModeSources are the four places a content mode can come from, as
// their raw values; an empty value is unset. The most restrictive wins.
//
// Org is the org's resolved privacy setting, tenant.OrgConfig.ContentMode(),
// which already reads privacy.retention.store_bodies=false as metadata.
type ContentModeSources struct {
	Org     string // the org's privacy.content_mode
	Key     string // the key's log_content metadata
	Request string // the x-agentcc-log-content header
	Default string // the global privacy.default_content_mode
}

var contentModeRank = map[models.ContentMode]int{
	models.ContentModeFull:     0,
	models.ContentModeMetadata: 1,
	models.ContentModeNone:     2,
}

// ResolveContentMode returns the most restrictive of the sources' content
// modes and the source it came from. It needs no request
// context, so MCP tool calls and capability calls resolve their content mode
// through it as the chat pipeline does.
//
// The global default is the starting point, and another source replaces it
// only when strictly more restrictive, so a tie names the least specific
// source and a full result is always the default's. A value that is set but
// is not full, metadata or none reads as none: a caller who asked for less
// logging and misspelt it gets the least, never the most.
func ResolveContentMode(s ContentModeSources) models.Privacy {
	won := models.Privacy{ContentMode: models.ContentModeFull, Source: models.PrivacySourceDefault}
	if mode, ok := parseContentMode(s.Default); ok {
		won.ContentMode = mode
	}
	for _, src := range [...]struct{ raw, name string }{
		{s.Org, models.PrivacySourceOrg},
		{s.Key, models.PrivacySourceKey},
		{s.Request, models.PrivacySourceRequest},
	} {
		if mode, ok := parseContentMode(src.raw); ok && contentModeRank[mode] > contentModeRank[won.ContentMode] {
			won = models.Privacy{ContentMode: mode, Source: src.name}
		}
	}
	return won
}

// RequestContentMode is the content mode a request asked for in its
// x-agentcc-log-content header, or "" when it sent none.
func RequestContentMode(h http.Header) string {
	if h == nil {
		return ""
	}
	return h.Get(LogContentHeader)
}

// parseContentMode reads a raw content mode: ok is false only when it is
// unset, and an unknown value is none (fail closed).
func parseContentMode(raw string) (models.ContentMode, bool) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "", false
	}
	mode := models.ContentMode(strings.ToLower(raw))
	if _, known := contentModeRank[mode]; !known {
		return models.ContentModeNone, true
	}
	return mode, true
}

// defaultContentMode is the global privacy.default_content_mode, installed
// once at startup by the gateway's wiring.
var defaultContentMode atomic.Value // models.ContentMode

// DefaultContentMode is the global privacy.default_content_mode; full until
// SetDefaultContentMode installs another.
func DefaultContentMode() string {
	if mode, ok := defaultContentMode.Load().(models.ContentMode); ok {
		return string(mode)
	}
	return string(models.ContentModeFull)
}

// SetDefaultContentMode installs the global privacy.default_content_mode ("" is
// full) and returns a function that puts the previous one back, for tests.
// Any value other than full, metadata or none is refused.
func SetDefaultContentMode(mode string) (restore func(), err error) {
	next := models.ContentMode(strings.ToLower(strings.TrimSpace(mode)))
	if next == "" {
		next = models.ContentModeFull
	}
	if _, known := contentModeRank[next]; !known {
		return nil, fmt.Errorf("privacy.default_content_mode must be full, metadata or none; got %q", mode)
	}
	prev := models.ContentMode(DefaultContentMode())
	defaultContentMode.Store(next)
	return func() { defaultContentMode.Store(prev) }, nil
}
