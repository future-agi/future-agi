package reqopts

import (
	"bytes"
	"encoding/json"
	"reflect"
	"sort"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

const extraBodyKey = "extra_body"

// preRead are the keys the chat handler and the agent parse step have read
// from the body before routing options are applied (the model, the
// messages, streaming, the caller's user and metadata, and session_id). An
// extra_body copy of one is accepted only when it equals the top-level
// value, because hoisting it this late would change a request the gateway
// has already acted on.
var preRead = map[string]bool{
	"model": true, "messages": true, "stream": true, "stream_options": true,
	"user": true, "metadata": true, "session_id": true,
}

// hoistExtraBody merges a literal extra_body object into the top level of
// req, so its keys go through the same parsing and checks as top-level ones
// (OpenAI-style clients that forward extra_body literally, such as Omega's
// chatBody). A key already set at the top level with a different value is a
// 400 extra_body_conflict; a non-object, or an extra_body inside extra_body,
// is a 400 invalid_extra_body. req is left unchanged on error.
func hoistExtraBody(req *models.ChatCompletionRequest) *models.APIError {
	raw, ok := req.Extra[extraBodyKey]
	if !ok {
		return nil
	}
	var extra map[string]json.RawMessage
	if !isJSONObject(raw) || json.Unmarshal(raw, &extra) != nil {
		return fieldError(CodeInvalidExtraBody, extraBodyKey, "extra_body must be a JSON object")
	}
	if _, nested := extra[extraBodyKey]; nested {
		return fieldError(CodeInvalidExtraBody, extraBodyKey+"."+extraBodyKey, "extra_body must not contain extra_body")
	}
	top, err := topLevel(req)
	if err != nil {
		return models.ErrInternal("reading the request: " + err.Error())
	}
	delete(top, extraBodyKey)
	keys := make([]string, 0, len(extra))
	for k := range extra {
		keys = append(keys, k)
	}
	sort.Strings(keys) // the first conflict reported is stable
	for _, k := range keys {
		v := extra[k]
		cur, set := top[k]
		switch {
		case set && !sameJSON(cur, v):
			return fieldError(CodeExtraBodyConflict, extraBodyKey+"."+k,
				"extra_body."+k+" differs from the top-level "+k)
		case !set && preRead[k] && !isZeroJSON(v):
			return fieldError(CodeExtraBodyConflict, extraBodyKey+"."+k,
				"send "+k+" at the top level of the request, not in extra_body")
		}
		top[k] = v
	}
	merged, err := json.Marshal(top)
	if err != nil {
		return models.ErrInternal("merging extra_body: " + err.Error())
	}
	var out models.ChatCompletionRequest
	if err := json.Unmarshal(merged, &out); err != nil {
		return fieldError(CodeInvalidExtraBody, extraBodyKey, "extra_body does not fit the request: "+err.Error())
	}
	*req = out
	return nil
}

// topLevel is req's outbound JSON object, typed fields and Extra together.
func topLevel(req *models.ChatCompletionRequest) (map[string]json.RawMessage, error) {
	b, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	var obj map[string]json.RawMessage
	err = json.Unmarshal(b, &obj)
	return obj, err
}

func isJSONObject(raw json.RawMessage) bool {
	t := bytes.TrimSpace(raw)
	return len(t) > 0 && t[0] == '{'
}

// sameJSON reports whether a and b decode to the same value, so spacing and
// key order do not count as a conflict.
func sameJSON(a, b json.RawMessage) bool {
	var va, vb any
	if json.Unmarshal(a, &va) != nil || json.Unmarshal(b, &vb) != nil {
		return false
	}
	return reflect.DeepEqual(va, vb)
}

// isZeroJSON reports whether raw is null, false, 0, "" or an empty object or
// array: the values an absent top-level key already stands for.
func isZeroJSON(raw json.RawMessage) bool {
	var v any
	if json.Unmarshal(raw, &v) != nil {
		return false
	}
	switch x := v.(type) {
	case nil:
		return true
	case bool:
		return !x
	case float64:
		return x == 0
	case string:
		return x == ""
	case map[string]any:
		return len(x) == 0
	case []any:
		return len(x) == 0
	}
	return false
}
