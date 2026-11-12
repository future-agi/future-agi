package protocol

import (
	"encoding/json"
	"net/http"
	"unicode/utf8"
)

// JSON-RPC error codes the 2026-07-28 revision adds (spec brief §2).
const (
	CodeHeaderMismatch          = -32020
	CodeMissingClientCapability = -32021
	CodeUnsupportedVersion      = -32022
)

// Error is a protocol-level refusal: an HTTP status with a JSON-RPC error.
// It is never an isError tool result (decision 28).
type Error struct {
	HTTPStatus int
	Code       int
	Message    string
	// Data is the JSON-RPC error's data member, or nil.
	Data json.RawMessage
}

func (e *Error) Error() string { return e.Message }

// HeaderMismatch is HTTP 400 with -32020: a mirrored header disagrees with
// the body, or a required one is missing.
func HeaderMismatch(message string) *Error {
	return &Error{HTTPStatus: http.StatusBadRequest, Code: CodeHeaderMismatch, Message: message}
}

// MissingClientCapability is HTTP 400 with -32021: a 2026-07-28 request
// without _meta's protocolVersion and clientCapabilities.
func MissingClientCapability() *Error {
	return &Error{
		HTTPStatus: http.StatusBadRequest,
		Code:       CodeMissingClientCapability,
		Message:    "a " + Version2026 + " request must carry _meta with " + MetaProtocolVersion + " and " + MetaClientCapabilities,
	}
}

// UnsupportedVersion is HTTP 400 with -32022 and data.supported, the
// revisions the gateway serves.
func UnsupportedVersion(version string, served []string) *Error {
	supported := append([]string{}, served...)
	data, _ := json.Marshal(struct {
		Supported []string `json:"supported"`
	}{supported})
	msg := "unsupported protocol version"
	if version != "" {
		msg += " " + strconvQuote(version)
	}
	return &Error{HTTPStatus: http.StatusBadRequest, Code: CodeUnsupportedVersion, Message: msg, Data: data}
}

// maxQuoted bounds a client-sent value echoed in an error message, so a
// large header is not reflected back whole.
const maxQuoted = 128

// strconvQuote quotes s as JSON does, so a client-sent value cannot break
// the message, cut to maxQuoted bytes on a rune boundary.
func strconvQuote(s string) string {
	if len(s) > maxQuoted {
		cut := maxQuoted
		for cut > 0 && !utf8.RuneStart(s[cut]) {
			cut--
		}
		s = s[:cut] + "…"
	}
	b, _ := json.Marshal(s)
	return string(b)
}
