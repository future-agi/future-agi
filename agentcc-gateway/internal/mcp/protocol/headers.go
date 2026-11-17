package protocol

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
)

// Base64 header form: a header value that is not safe as a plain header
// value travels as =?base64?<standard Base64>?=, and every reader must accept
// it (2026-07-28 Streamable HTTP).
const (
	base64Prefix = "=?base64?"
	base64Suffix = "?="
)

// xMCPHeader is the inputSchema property annotation that names a tool
// parameter's mirrored header: "x-mcp-header": "Region" mirrors the argument
// as Mcp-Param-Region.
const xMCPHeader = "x-mcp-header"

// errBadBase64 is a =?base64?...?= value that does not decode.
var errBadBase64 = errors.New("invalid base64 header value")

// DecodeHeaderValue returns a mirrored header's value: the decoded text of the
// Base64 form, else the value as sent.
func DecodeHeaderValue(v string) (string, error) {
	if !strings.HasPrefix(v, base64Prefix) || !strings.HasSuffix(v, base64Suffix) || len(v) < len(base64Prefix)+len(base64Suffix) {
		return v, nil
	}
	enc := v[len(base64Prefix) : len(v)-len(base64Suffix)]
	if b, err := base64.StdEncoding.DecodeString(enc); err == nil {
		return string(b), nil
	}
	if b, err := base64.RawStdEncoding.DecodeString(enc); err == nil {
		return string(b), nil
	}
	return "", errBadBase64
}

// EncodeHeaderValue is DecodeHeaderValue's inverse for a writer: the value
// itself when it is printable ASCII without surrounding spaces, else the
// Base64 form.
func EncodeHeaderValue(v string) string {
	safe := v == strings.TrimSpace(v) && !strings.HasPrefix(v, base64Prefix)
	for i := 0; safe && i < len(v); i++ {
		safe = v[i] >= 0x20 && v[i] < 0x7f
	}
	if safe {
		return v
	}
	return base64Prefix + base64.StdEncoding.EncodeToString([]byte(v)) + base64Suffix
}

// SchemaLookup returns the inputSchema of the tool a tools/call names, as the
// client named it, or false when the gateway does not know the tool. It must
// resolve the tool the method handlers will run for that name.
type SchemaLookup func(tool string) (json.RawMessage, bool)

// namedMethods are the methods whose Mcp-Name header mirrors a params member.
var namedMethods = map[string]string{
	"tools/call":     "name",
	"prompts/get":    "name",
	"resources/read": "uri",
}

// mirroredBody holds the params members the headers mirror, read the way the
// method handlers read them: by the json tags of ToolCallParams,
// ReadResourceParams and GetPromptParams (package mcp), with encoding/json's
// rule (an exact name first, else a case-insensitive match, the last match
// kept). CheckHeaders refuses first any body where that rule and another
// parser's could read a different value (oneSpelling).
type mirroredBody struct {
	Name      json.RawMessage `json:"name"`
	URI       json.RawMessage `json:"uri"`
	Arguments json.RawMessage `json:"arguments"`
}

// CheckHeaders validates a stateless request's mirrored headers against its
// body, before anything routes on them or applies policy:
//   - MCP-Protocol-Version equals _meta's protocolVersion;
//   - Mcp-Method equals the method;
//   - on tools/call and prompts/get, Mcp-Name equals params.name, and on
//     resources/read params.uri;
//   - on tools/call, every argument the tool's inputSchema marks with
//     x-mcp-header (at any depth of properties) is mirrored as
//     Mcp-Param-{Name} with the same value, and no such header is sent for
//     an argument the body does not carry or carries as null.
//
// Each member a header mirrors (_meta, name or uri, arguments, and each
// marked argument on its path) must be sent once and spelled exactly, so
// every JSON parser reads the value the header was checked against: the
// method handlers' case-insensitive match, an exact-name reader, the
// upstream's own parser. The values are then read as the handlers read them,
// and the inputSchema is the one of the tool they will run.
//
// A missing required header is a mismatch. Values in the Base64 form are
// decoded first, and a plain value with a character a header value cannot
// carry (outside visible ASCII, space and tab) is refused. An Mcp-Param-*
// header that no marked parameter names is not the gateway's to judge: it
// stays on the request, and the upstream client carries it on by value, as
// the spec requires of intermediaries.
func CheckHeaders(h http.Header, method string, params json.RawMessage, meta Meta, schemas SchemaLookup) *Error {
	if err := mirrors(h, HeaderProtocolVersion, meta.ProtocolVersion); err != nil {
		return err
	}
	if err := mirrors(h, HeaderMethod, method); err != nil {
		return err
	}
	member, named := namedMethods[method]
	mirrored := []string{"_meta"}
	if named {
		mirrored = append(mirrored, member)
	}
	if method == "tools/call" {
		mirrored = append(mirrored, "arguments")
	}
	if len(params) > 0 {
		ms, err := objectMembers(params)
		if err != nil {
			return HeaderMismatch("params must be an object")
		}
		if perr := oneSpelling(ms, "params", mirrored...); perr != nil {
			return perr
		}
	}
	if !named {
		return nil
	}
	var body mirroredBody
	if len(params) > 0 && json.Unmarshal(params, &body) != nil {
		return HeaderMismatch("params must be an object")
	}
	var name string
	if member == "uri" {
		_ = json.Unmarshal(body.URI, &name)
	} else {
		_ = json.Unmarshal(body.Name, &name)
	}
	if err := mirrors(h, HeaderName, name); err != nil {
		return err
	}
	if method != "tools/call" || schemas == nil {
		return nil
	}
	schema, ok := schemas(name)
	if !ok {
		return nil
	}
	return checkParamHeaders(h, schema, body.Arguments)
}

// mirrors checks that standard header name carries exactly one value, equal
// to want once decoded. These headers mirror the client's own body values,
// so the messages quote both.
func mirrors(h http.Header, name, want string) *Error {
	vs := h.Values(name)
	switch {
	case len(vs) == 0:
		return HeaderMismatch(fmt.Sprintf("the %s header is required and must equal the body's %s", name, strconvQuote(want)))
	case len(vs) > 1:
		return HeaderMismatch(fmt.Sprintf("the %s header was sent more than once", name))
	}
	got, err := headerValue(name, vs[0])
	if err != nil {
		return err
	}
	if got != want {
		return HeaderMismatch(fmt.Sprintf("the %s header %s does not match the body's %s", name, strconvQuote(got), strconvQuote(want)))
	}
	return nil
}

// headerValue returns a mirrored header's value as sent: decoded from the
// Base64 form, else the plain value, which may hold only what a header value
// can carry plainly (visible ASCII, space and tab); anything else had to be
// sent in the Base64 form, and the spec has servers refuse it.
func headerValue(name, v string) (string, *Error) {
	if !strings.HasPrefix(v, base64Prefix) || !strings.HasSuffix(v, base64Suffix) {
		for i := 0; i < len(v); i++ {
			if c := v[i]; c != '\t' && (c < 0x20 || c > 0x7e) {
				return "", HeaderMismatch(fmt.Sprintf("the %s header carries a character a header value cannot carry plainly; send it as =?base64?...?=", name))
			}
		}
	}
	got, err := DecodeHeaderValue(v)
	if err != nil {
		return "", HeaderMismatch(fmt.Sprintf("the %s header: %v", name, err))
	}
	return got, nil
}

// ParamHeaders returns every Mcp-Param-* header of h, by the name after the
// prefix, as sent (Base64 form included): the values the upstream client regenerates for the
// upstream.
func ParamHeaders(h http.Header) map[string]string {
	out := map[string]string{}
	for k, vs := range h {
		if len(vs) > 0 && len(k) > len(HeaderParamPrefix) && strings.EqualFold(k[:len(HeaderParamPrefix)], HeaderParamPrefix) {
			out[k[len(HeaderParamPrefix):]] = vs[0]
		}
	}
	return out
}
