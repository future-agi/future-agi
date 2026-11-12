package wire

import (
	"encoding/base64"
	"encoding/json"
	"net/http"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Mirrored-header names of 2026-07-28 Streamable HTTP.
const (
	HeaderMethod      = "Mcp-Method"
	HeaderName        = "Mcp-Name"
	HeaderParamPrefix = "Mcp-Param-"
	// schemaHeaderKey marks an inputSchema property mirrored as a header:
	// true names the header after the property, a string names it itself.
	schemaHeaderKey = "x-mcp-header"
)

// Mirrored returns the 2026-07-28 mirrored headers of one request, built from
// the body as the gateway sends it, never copied from the client's:
// Mcp-Method; Mcp-Name on tools/call and prompts/get (params.name) and
// resources/read (params.uri); and, on tools/call, Mcp-Param-{Name} for each
// argument the upstream tool's inputSchema marks x-mcp-header and for each
// Mcp-Param-{Name} the client sent (validated against its own body)
// that names an argument, so a parameter the gateway does not know is still
// carried, by value.
func Mirrored(method string, params, inputSchema json.RawMessage, inbound http.Header) http.Header {
	h := http.Header{}
	h.Set(HeaderMethod, method)
	var p struct {
		Name      string                     `json:"name"`
		URI       string                     `json:"uri"`
		Arguments map[string]json.RawMessage `json:"arguments"`
	}
	_ = json.Unmarshal(params, &p)
	switch method {
	case "tools/call", "prompts/get":
		h.Set(HeaderName, EncodeHeaderValue(p.Name))
	case "resources/read":
		h.Set(HeaderName, EncodeHeaderValue(p.URI))
	}
	if method != "tools/call" || len(p.Arguments) == 0 {
		return h
	}
	for header, arg := range schemaHeaderParams(inputSchema) {
		if v, ok := p.Arguments[arg]; ok {
			h.Set(HeaderParamPrefix+header, EncodeHeaderValue(argumentText(v)))
		}
	}
	for name := range inbound {
		canon := http.CanonicalHeaderKey(name)
		if !strings.HasPrefix(canon, HeaderParamPrefix) {
			continue
		}
		header := canon[len(HeaderParamPrefix):]
		if h.Get(HeaderParamPrefix+header) != "" {
			continue
		}
		if arg, ok := argumentNamed(p.Arguments, header); ok {
			h.Set(HeaderParamPrefix+header, EncodeHeaderValue(argumentText(p.Arguments[arg])))
		}
	}
	return h
}

// schemaHeaderParams maps each header name an inputSchema declares to the
// argument it mirrors.
func schemaHeaderParams(schema json.RawMessage) map[string]string {
	var s struct {
		Properties map[string]map[string]json.RawMessage `json:"properties"`
	}
	if json.Unmarshal(schema, &s) != nil {
		return nil
	}
	out := map[string]string{}
	args := make([]string, 0, len(s.Properties))
	for arg := range s.Properties {
		args = append(args, arg)
	}
	sort.Strings(args)
	for _, arg := range args {
		raw, ok := s.Properties[arg][schemaHeaderKey]
		if !ok {
			continue
		}
		var named string
		var flag bool
		switch {
		case json.Unmarshal(raw, &named) == nil && validHeaderToken(named):
			out[named] = arg
		case json.Unmarshal(raw, &flag) == nil && flag && validHeaderToken(arg):
			out[arg] = arg
		}
	}
	return out
}

// argumentNamed finds the argument a header name mirrors, ignoring case.
func argumentNamed(args map[string]json.RawMessage, header string) (string, bool) {
	if _, ok := args[header]; ok {
		return header, true
	}
	for arg := range args {
		if strings.EqualFold(arg, header) {
			return arg, true
		}
	}
	return "", false
}

// argumentText is a header's text for an argument: a string as itself, any
// other JSON value as its JSON text.
func argumentText(v json.RawMessage) string {
	var s string
	if json.Unmarshal(v, &s) == nil {
		return s
	}
	return string(v)
}

// base64Prefix and base64Suffix wrap a header value that cannot be sent as
// itself (initialize decodes the same form).
const (
	base64Prefix = "=?base64?"
	base64Suffix = "?="
)

// EncodeHeaderValue is v as a header value: itself when it is printable
// ASCII without leading or trailing space and cannot be read as the Base64
// form, else =?base64?<standard Base64 of its UTF-8>?=.
func EncodeHeaderValue(v string) string {
	if safeHeaderValue(v) {
		return v
	}
	return base64Prefix + base64.StdEncoding.EncodeToString([]byte(v)) + base64Suffix
}

func safeHeaderValue(v string) bool {
	if v == "" {
		return true
	}
	if v[0] == ' ' || v[len(v)-1] == ' ' || strings.HasPrefix(v, base64Prefix) {
		return false
	}
	for i := 0; i < len(v); i++ {
		if c := v[i]; c < 0x20 || c > 0x7e {
			return false
		}
	}
	return true
}

func validHeaderToken(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !(c == '-' || c == '_' || ('0' <= c && c <= '9') || ('a' <= c && c <= 'z') || ('A' <= c && c <= 'Z')) {
			return false
		}
	}
	return true
}

// Forwarded returns the inbound headers a server's forward_headers names, and
// only those: never one config.ForwardHeaderRefused names, whichever config
// (YAML or org) listed it, and never an Mcp-* header, which the gateway
// regenerates.
func Forwarded(inbound http.Header, allow []string) http.Header {
	out := http.Header{}
	for _, name := range allow {
		canon := http.CanonicalHeaderKey(strings.TrimSpace(name))
		if canon == "" || config.ForwardHeaderRefused(canon) || strings.HasPrefix(canon, "Mcp-") {
			continue
		}
		if vs := inbound.Values(canon); len(vs) > 0 {
			out[canon] = append([]string(nil), vs...)
		}
	}
	return out
}
