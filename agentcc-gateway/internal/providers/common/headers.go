package common

import (
	"errors"
	"net/http"
	"strings"
)

// HeaderDefaults owns a validated snapshot of provider-configured headers.
// Authentication, routing and gateway identity remain adapter-owned. These
// defaults fill absent headers only: per-call and forwarded headers win, even
// when their value is empty. No header values are included in errors or logs.
type HeaderDefaults struct {
	values map[string]string
}

// NewHeaderDefaults validates and copies headers. internal names are adapter
// settings carried in cfg.Headers (for example Azure's auth-type), not headers
// to put on the wire. Case-ambiguous duplicates are rejected rather than chosen
// nondeterministically by map iteration.
func NewHeaderDefaults(headers map[string]string, internal ...string) (HeaderDefaults, error) {
	out := HeaderDefaults{values: make(map[string]string, len(headers))}
	seen := make(map[string]bool, len(headers))
	for name, value := range headers {
		if !validProviderHeaderName(name) {
			return HeaderDefaults{}, errors.New("invalid provider header name")
		}
		for _, c := range []byte(value) {
			if (c < ' ' && c != '\t') || c == 0x7f {
				return HeaderDefaults{}, errors.New("invalid provider header value")
			}
		}
		canonical := http.CanonicalHeaderKey(name)
		if seen[canonical] {
			return HeaderDefaults{}, errors.New("duplicate provider header name")
		}
		seen[canonical] = true
		if reservedProviderHeader(name) {
			continue
		}
		isInternal := false
		for _, setting := range internal {
			if strings.EqualFold(name, setting) {
				isInternal = true
				break
			}
		}
		if !isInternal {
			out.values[canonical] = value
		}
	}
	return out, nil
}

// Apply does not mutate the snapshot, so one provider can serve concurrent calls.
func (h HeaderDefaults) Apply(req *http.Request) {
	if len(h.values) == 0 {
		return
	}
	if req.Header == nil {
		req.Header = make(http.Header)
	}
	for name, value := range h.values {
		present := false
		for existing := range req.Header {
			if strings.EqualFold(existing, name) {
				present = true
				break
			}
		}
		if !present {
			req.Header.Set(name, value)
		}
	}
}

func validProviderHeaderName(name string) bool {
	if name == "" {
		return false
	}
	for _, c := range []byte(name) {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.ContainsRune("!#$%&'*+-.^_`|~", rune(c))) {
			return false
		}
	}
	return true
}

func reservedProviderHeader(name string) bool {
	name = strings.ToLower(name)
	if strings.HasPrefix(name, "x-agentcc-") || strings.HasPrefix(name, "x-omega-") {
		return true
	}
	switch name {
	case "authorization", "api-key", "x-api-key", "proxy-authorization",
		"host", "content-length", "connection", "proxy-connection", "keep-alive",
		"transfer-encoding", "te", "trailer", "upgrade":
		return true
	}
	return false
}
