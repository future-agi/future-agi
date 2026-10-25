package mcpmock

import (
	"encoding/json"
	"fmt"
)

// Protocol revisions the mock speaks.
const (
	V2026 = "2026-07-28"
	V1125 = "2025-11-25"
	V0618 = "2025-06-18"
	V0326 = "2025-03-26"
	V1105 = "2024-11-05"
)

// 2026-07-28 error codes and the JSON-RPC ones the mock uses.
const (
	CodeHeaderMismatch          = -32020
	CodeMissingClientCapability = -32021
	CodeUnsupportedVersion      = -32022
	CodeParse                   = -32700
	CodeInvalidRequest          = -32600
	CodeMethodNotFound          = -32601
	CodeInvalidParams           = -32602
	CodeInternal                = -32603
	CodeSessionNotFound         = -32001
)

// _meta keys of a 2026-07-28 request.
const (
	metaProtocolVersion = "io.modelcontextprotocol/protocolVersion"
	metaClientCaps      = "io.modelcontextprotocol/clientCapabilities"
	metaClientInfo      = "io.modelcontextprotocol/clientInfo"
	metaLogLevel        = "logLevel"
	metaCostUSD         = "io.futureagi/cost_usd"
)

// Era is what --era selects: whether stateless 2026 requests are served,
// which revisions an initialize may negotiate (newest first), and whether the
// 2024-11-05 HTTP+SSE endpoints are mounted.
type Era struct {
	Name      string
	Stateless bool
	Legacy    []string
	HTTPSSE   bool
}

// ParseEra reads --era.
func ParseEra(name string) (Era, error) {
	switch name {
	case "2026", V2026:
		return Era{Name: "2026", Stateless: true}, nil
	case V1125, V0618, V0326:
		return Era{Name: name, Legacy: []string{name}}, nil
	case V1105:
		return Era{Name: name, Legacy: []string{V1105}, HTTPSSE: true}, nil
	case "dual":
		return Era{Name: name, Stateless: true, Legacy: []string{V1125, V0618, V0326, V1105}, HTTPSSE: true}, nil
	}
	return Era{}, fmt.Errorf("unknown era %q (want 2026, 2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05 or dual)", name)
}

// streamable reports whether the Streamable HTTP endpoint serves this era: it
// does for everything but a 2024-11-05-only mock.
func (e Era) streamable() bool { return e.Stateless || e.legacyStreamable() }

func (e Era) legacyStreamable() bool {
	for _, v := range e.Legacy {
		if v != V1105 {
			return true
		}
	}
	return false
}

// negotiate picks the revision an initialize gets: the client's own when this
// era accepts it, else the newest the era has.
func (e Era) negotiate(requested string) (string, bool) {
	if len(e.Legacy) == 0 {
		return "", false
	}
	for _, v := range e.Legacy {
		if v == requested {
			return v, true
		}
	}
	return e.Legacy[0], true
}

// atLeast reports whether revision v is the same as or newer than min (the
// revisions are ISO dates, so they compare as strings).
func atLeast(v, min string) bool { return v >= min }

// RPCError is a JSON-RPC error, with the HTTP status the mock answers it with
// over Streamable HTTP (0 means 200).
type RPCError struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data,omitempty"`
	status  int
}

func rpcErr(status, code int, format string, args ...any) *RPCError {
	return &RPCError{Code: code, Message: fmt.Sprintf(format, args...), status: status}
}

// requestMeta is the parsed _meta of a 2026 request.
type requestMeta struct {
	Version  string
	Caps     json.RawMessage
	LogLevel string
	Raw      json.RawMessage
}

// checkMeta enforces the per-request metadata of a 2026-07-28 request: no
// _meta, or one without the protocol version or the client capabilities, is
// -32021; a version other than 2026-07-28 is -32022 with the supported list.
func checkMeta(params json.RawMessage) (requestMeta, *RPCError) {
	var p struct {
		Meta map[string]json.RawMessage `json:"_meta"`
	}
	if len(params) > 0 {
		if err := json.Unmarshal(params, &p); err != nil {
			return requestMeta{}, rpcErr(400, CodeInvalidParams, "params must be an object: %v", err)
		}
	}
	if p.Meta == nil {
		return requestMeta{}, rpcErr(400, CodeMissingClientCapability,
			"a %s request must carry _meta with %s and %s", V2026, metaProtocolVersion, metaClientCaps)
	}
	m := requestMeta{Caps: p.Meta[metaClientCaps]}
	m.Raw, _ = json.Marshal(p.Meta)
	_ = json.Unmarshal(p.Meta[metaProtocolVersion], &m.Version)
	_ = json.Unmarshal(p.Meta[metaLogLevel], &m.LogLevel)
	if m.Version == "" || len(m.Caps) == 0 || string(m.Caps) == "null" {
		return m, rpcErr(400, CodeMissingClientCapability,
			"_meta must carry %s and %s", metaProtocolVersion, metaClientCaps)
	}
	if m.Version != V2026 {
		e := rpcErr(400, CodeUnsupportedVersion, "unsupported protocol version %q", m.Version)
		e.Data = json.RawMessage(`{"supported":["` + V2026 + `"]}`)
		return m, e
	}
	return m, nil
}

// metaOf returns the raw _meta of any request's params, for /__received.
func metaOf(params json.RawMessage) json.RawMessage {
	var p struct {
		Meta json.RawMessage `json:"_meta"`
	}
	_ = json.Unmarshal(params, &p)
	return p.Meta
}
