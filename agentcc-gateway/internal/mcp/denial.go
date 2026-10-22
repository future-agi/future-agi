package mcp

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Denial is a hook's refusal. A hook that returns
// one stops the chain; the finalizers still run.
//
// By default a denial on tools/call is answered in band: HTTP 200 with an
// isError result whose _meta["io.futureagi/error"] is {type, code, message,
// param: null, metadata}. A denial with RPCCode set is a protocol-level
// answer instead: a JSON-RPC error with that code, sent with HTTPStatus (200
// when 0), Header and Data. On the other methods every denial is a JSON-RPC
// error (code -32600 when RPCCode is 0).
type Denial struct {
	Type    string
	Code    string
	Message string
	// Metadata is the error object's metadata: a map, or a typed value such
	// as models.PaymentMeta for the credit and limit codes, whose object is
	// exactly the model path's (error-402-v1).
	Metadata any
	// RetryAfterSec is the seconds after which a retry may succeed; in band
	// it travels in metadata (retry_after_s), never as a header.
	RetryAfterSec int

	RPCCode    int
	HTTPStatus int
	Header     http.Header
	Data       json.RawMessage
}

// Error makes a denial usable as an error.
func (d *Denial) Error() string {
	if d.Code == "" {
		return d.Message
	}
	return d.Message + " [" + d.Code + "]"
}

// InBand reports whether the denial is answered as an isError tool result.
func (d *Denial) InBand() bool { return d.RPCCode == 0 }

// ErrorObject is the io.futureagi/error object of an in-band denial.
type ErrorObject struct {
	Type     string  `json:"type"`
	Code     string  `json:"code"`
	Message  string  `json:"message"`
	Param    *string `json:"param"`
	Metadata any     `json:"metadata"`
}

// Object returns the denial's error object: metadata is {} when unset, and
// carries retry_after_s when RetryAfterSec is set.
func (d *Denial) Object() ErrorObject {
	meta := d.Metadata
	if d.RetryAfterSec > 0 {
		meta = withRetryAfter(meta, d.RetryAfterSec)
	}
	if meta == nil {
		meta = map[string]any{}
	}
	return ErrorObject{Type: d.Type, Code: d.Code, Message: d.Message, Metadata: meta}
}

// withRetryAfter adds retry_after_s to meta's JSON object. Metadata that is
// not an object is returned unchanged.
func withRetryAfter(meta any, sec int) any {
	fields := map[string]json.RawMessage{}
	if meta != nil {
		raw, err := json.Marshal(meta)
		if err != nil || json.Unmarshal(raw, &fields) != nil {
			return meta
		}
	}
	if fields == nil { // the metadata marshalled as null
		fields = map[string]json.RawMessage{}
	}
	fields["retry_after_s"] = json.RawMessage(strconv.Itoa(sec))
	return fields
}

// DenialFromAPIError turns a model-path error, such as a credit or key-limit
// refusal, into an in-band denial carrying the same object.
func DenialFromAPIError(e *models.APIError) *Denial {
	if e == nil {
		return nil
	}
	return &Denial{Type: e.Type, Code: e.Code, Message: e.Message, Metadata: e.Metadata, RetryAfterSec: e.RetryAfterSec}
}

// rpcDenial is a protocol-level denial with a plain JSON-RPC code and
// message, which the default hooks return.
func rpcDenial(rpcCode int, code, message string) *Denial {
	return &Denial{Type: models.ErrTypeInvalidRequest, Code: code, Message: message, RPCCode: rpcCode}
}

// inBandResult is the isError result an in-band denial is answered with.
type inBandResult struct {
	Content []ContentPart              `json:"content"`
	IsError bool                       `json:"isError"`
	Meta    map[string]json.RawMessage `json:"_meta"`
}

// inBandDenialResult builds the in-band result for d, with the call's response
// meta merged in.
func inBandDenialResult(d *Denial, meta map[string]json.RawMessage) (inBandResult, error) {
	obj, err := json.Marshal(d.Object())
	if err != nil {
		return inBandResult{}, err
	}
	out := inBandResult{
		Content: []ContentPart{{Type: "text", Text: d.Error()}},
		IsError: true,
		Meta:    make(map[string]json.RawMessage, len(meta)+1),
	}
	for k, v := range meta {
		out.Meta[k] = v
	}
	out.Meta[MetaKeyError] = obj
	return out, nil
}

// _meta keys the gateway writes.
const (
	MetaKeyError    = "io.futureagi/error"
	MetaKeyToolCall = "io.futureagi/tool_call"
)
