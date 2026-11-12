package models

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
)

// The two wire formats the decision routes carry. Neither is translated into
// the other: a body goes to a vendor that speaks its format, or is refused.
const (
	// DecisionFormatSystemOne is {model, state, questions: {<id>: {type, ...}}}.
	DecisionFormatSystemOne = "systemone"
	// DecisionFormatOpenAI is OpenAI's Decisions API: questions is an array.
	DecisionFormatOpenAI = "openai"
)

// ProviderKeyHeader carries an internal caller's own provider key for one
// decision call. The handler reads it once and removes it from everything the
// request leaves behind: headers kept for logs and spans, metadata, mirrors.
const ProviderKeyHeader = "x-agentcc-provider-key"

// Codes the decision routes return for refusals the gateway makes itself,
// before any vendor is called.
const (
	CodeBYOKRequired                  = "byok_required"
	CodeDecisionProviderNotConfigured = "decision_provider_not_configured"
	CodeDecisionProviderWrongFormat   = "decision_provider_wrong_format"
	CodeProviderKeyHeaderNotAllowed   = "provider_key_header_not_allowed"
	CodeFormatMismatch                = "format_mismatch"
)

// MaxDecisionResponseBytes caps a vendor's answer. Answers are a few KB; the
// cap only stops a misbehaving upstream from filling memory.
const MaxDecisionResponseBytes = 10 * 1024 * 1024

// DecisionRequest is a decision call as the gateway forwards it: the caller's
// body byte for byte, with only the model rewritten, plus what the gateway
// read from it to route, log and price the call.
type DecisionRequest struct {
	// Format is DecisionFormatSystemOne or DecisionFormatOpenAI.
	Format string
	// Model is the model as the caller named it, provider prefix included.
	Model string
	// UpstreamModel is the model the vendor is sent.
	UpstreamModel string
	// Body is the request body forwarded upstream.
	Body []byte

	// QuestionIDs lists the questions in body order: their keys for System
	// One, their names (or "#<index>") for OpenAI Decisions.
	QuestionIDs []string
	// QuestionTypes is each question's type, parallel to QuestionIDs.
	QuestionTypes []string
	// StateBytes is the size of the state (System One) or input (OpenAI).
	StateBytes int

	// modelStart and modelEnd span the model's JSON value in Body.
	modelStart, modelEnd int
}

// DecisionResponse is a vendor's 200 answer.
type DecisionResponse struct {
	// Body is returned to the caller unchanged.
	Body []byte
	// Model is the model the vendor reports having used.
	Model string
	Usage DecisionUsage
	// Answers is the body's answers field, kept for the request log.
	Answers json.RawMessage
}

// DecisionUsage is the vendor's own token count; the gateway does not recount.
type DecisionUsage struct {
	InputTokens  int `json:"input_tokens"`
	OutputTokens int `json:"output_tokens"`
}

// jsonMember is one member of a JSON object, with its value's span in the
// object's bytes.
type jsonMember struct {
	key        string
	value      json.RawMessage
	start, end int
}

// objectMembers returns a JSON object's members in order. It fails on
// anything but a single, valid object.
func objectMembers(data []byte) ([]jsonMember, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	tok, err := dec.Token()
	if err != nil {
		return nil, err
	}
	if delim, ok := tok.(json.Delim); !ok || delim != '{' {
		return nil, errors.New("not a JSON object")
	}
	var members []jsonMember
	for dec.More() {
		tok, err := dec.Token()
		if err != nil {
			return nil, err
		}
		key, ok := tok.(string)
		if !ok {
			return nil, errors.New("object key is not a string")
		}
		var value json.RawMessage
		if err := dec.Decode(&value); err != nil {
			return nil, err
		}
		end := int(dec.InputOffset())
		members = append(members, jsonMember{key: key, value: value, start: end - len(value), end: end})
	}
	if _, err := dec.Token(); err != nil {
		return nil, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, errors.New("trailing data after the JSON object")
	}
	return members, nil
}

// jsonKind returns the first byte of a JSON value: '{', '[', '"', 'n' and so on.
func jsonKind(raw json.RawMessage) byte {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 {
		return 0
	}
	return trimmed[0]
}

// quoteQuestionID names a question in an error. The ID is the caller's own
// label, so it may be echoed, but nothing the question contains is.
func quoteQuestionID(id string) string {
	const max = 64
	if len(id) > max {
		id = id[:max] + "..."
	}
	return strconv.Quote(id)
}

func invalidQuestion(id, reason string) *APIError {
	return ErrBadRequest("invalid_question", fmt.Sprintf("question %s: %s", quoteQuestionID(id), reason))
}

// ParseDecisionRequest reads a decision body and checks only its envelope: an
// object with a non-empty model and a non-empty questions collection whose
// entries each carry a string type, plus a state for System One. Question
// types and every other field pass through unread, so a vendor can add one
// without the gateway refusing it. Errors name a question by its ID and never
// echo what it contains.
func ParseDecisionRequest(body []byte) (*DecisionRequest, *APIError) {
	members, err := objectMembers(body)
	if err != nil {
		return nil, ErrBadRequest("invalid_json", "request body must be a single JSON object")
	}
	req := &DecisionRequest{Body: body}
	fields := make(map[string]json.RawMessage, len(members))
	modelSeen := false
	for _, m := range members {
		if m.key == "model" {
			if modelSeen {
				return nil, ErrBadRequest("invalid_json", "model appears more than once")
			}
			modelSeen = true
			req.modelStart, req.modelEnd = m.start, m.end
		}
		fields[m.key] = m.value
	}

	if json.Unmarshal(fields["model"], &req.Model) != nil || strings.TrimSpace(req.Model) == "" {
		return nil, ErrBadRequest("missing_model", "model is required and must be a non-empty string")
	}
	req.UpstreamModel = req.Model

	questions := fields["questions"]
	switch jsonKind(questions) {
	case '{':
		req.Format = DecisionFormatSystemOne
		return req, req.parseSystemOne(fields)
	case '[':
		req.Format = DecisionFormatOpenAI
		return req, req.parseOpenAI(fields)
	default:
		return nil, ErrBadRequest("missing_questions",
			"questions is required: an object keyed by question id (System One) or an array (OpenAI Decisions)")
	}
}

func (r *DecisionRequest) parseSystemOne(fields map[string]json.RawMessage) *APIError {
	state := bytes.TrimSpace(fields["state"])
	if len(state) == 0 || bytes.Equal(state, []byte("null")) {
		return ErrBadRequest("missing_state", "state is required and must not be null")
	}
	r.StateBytes = len(state)

	questions, err := objectMembers(fields["questions"])
	if err != nil {
		return ErrBadRequest("invalid_json", "questions must be a JSON object")
	}
	if len(questions) == 0 {
		return ErrBadRequest("missing_questions", "questions must not be empty")
	}
	seen := make(map[string]bool, len(questions))
	for _, q := range questions {
		if seen[q.key] {
			return invalidQuestion(q.key, "appears more than once")
		}
		seen[q.key] = true
		kind, apiErr := questionType(q.key, q.value)
		if apiErr != nil {
			return apiErr
		}
		r.QuestionIDs = append(r.QuestionIDs, q.key)
		r.QuestionTypes = append(r.QuestionTypes, kind)
	}
	return nil
}

func (r *DecisionRequest) parseOpenAI(fields map[string]json.RawMessage) *APIError {
	var questions []json.RawMessage
	if json.Unmarshal(fields["questions"], &questions) != nil {
		return ErrBadRequest("invalid_json", "questions must be a JSON array")
	}
	if len(questions) == 0 {
		return ErrBadRequest("missing_questions", "questions must not be empty")
	}
	if input := bytes.TrimSpace(fields["input"]); len(input) > 0 {
		r.StateBytes = len(input)
	}
	for i, raw := range questions {
		id := "#" + strconv.Itoa(i)
		var named struct {
			Name json.RawMessage `json:"name"`
		}
		if jsonKind(raw) == '{' && json.Unmarshal(raw, &named) == nil {
			var name string
			if json.Unmarshal(named.Name, &name) == nil && name != "" {
				id = name
			}
		}
		kind, apiErr := questionType(id, raw)
		if apiErr != nil {
			return apiErr
		}
		r.QuestionIDs = append(r.QuestionIDs, id)
		r.QuestionTypes = append(r.QuestionTypes, kind)
	}
	return nil
}

// questionType checks that a question is an object with a non-empty string
// type, and returns that type.
func questionType(id string, raw json.RawMessage) (string, *APIError) {
	var question map[string]json.RawMessage
	if jsonKind(raw) != '{' || json.Unmarshal(raw, &question) != nil {
		return "", invalidQuestion(id, "must be a JSON object")
	}
	var kind string
	if json.Unmarshal(question["type"], &kind) != nil || strings.TrimSpace(kind) == "" {
		return "", invalidQuestion(id, "type must be a non-empty string")
	}
	return kind, nil
}

// SetUpstreamModel sets the model the vendor is sent, rewriting only the
// model's value in Body. Every other byte is forwarded as the caller sent it.
func (r *DecisionRequest) SetUpstreamModel(model string) {
	if model == r.UpstreamModel {
		return
	}
	encoded, _ := json.Marshal(model) // a string always encodes
	body := make([]byte, 0, len(r.Body)-(r.modelEnd-r.modelStart)+len(encoded))
	body = append(body, r.Body[:r.modelStart]...)
	body = append(body, encoded...)
	body = append(body, r.Body[r.modelEnd:]...)
	r.modelEnd = r.modelStart + len(encoded)
	r.Body = body
	r.UpstreamModel = model
}

// ParseDecisionResponse reads a vendor's 200 body. Only a JSON object is a
// usable answer; its model, usage and answers are read best effort, since the
// body goes back to the caller unchanged either way.
func ParseDecisionResponse(body []byte) (*DecisionResponse, bool) {
	members, err := objectMembers(body)
	if err != nil {
		return nil, false
	}
	resp := &DecisionResponse{Body: body}
	for _, m := range members {
		switch m.key {
		case "model":
			_ = json.Unmarshal(m.value, &resp.Model)
		case "answers":
			resp.Answers = m.value
		case "usage":
			resp.Usage = parseDecisionUsage(m.value)
		}
	}
	return resp, true
}

// parseDecisionUsage reads System One's input/output token counts, or the
// prompt/completion names OpenAI uses. A count it cannot read is zero.
func parseDecisionUsage(raw json.RawMessage) DecisionUsage {
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil {
		return DecisionUsage{}
	}
	count := func(names ...string) int {
		for _, name := range names {
			var n int
			if v, ok := fields[name]; ok && json.Unmarshal(v, &n) == nil {
				return max(n, 0)
			}
		}
		return 0
	}
	return DecisionUsage{
		InputTokens:  count("input_tokens", "prompt_tokens"),
		OutputTokens: count("output_tokens", "completion_tokens"),
	}
}

// UpstreamErrorMessage is the message a vendor's non-200 answer passes on.
// Only a 400 or 422 shows the vendor's text (DecisionUpstreamError), so only
// those parse the reply and check it against the request (up to 32 MB);
// every other status gets the fallback without that work.
func (r *DecisionRequest) UpstreamErrorMessage(status int, body []byte, apiKey, fallback string) string {
	if status != http.StatusBadRequest && status != http.StatusUnprocessableEntity {
		return fallback
	}
	return r.SafeUpstreamMessage(body, apiKey, fallback)
}

// SafeUpstreamMessage returns the vendor's error.message when it echoes
// nothing sensitive: not the key, and none of the request's content (state or
// input, and every question field but its type and name). Otherwise, and when
// the body carries no such message, it returns fallback.
func (r *DecisionRequest) SafeUpstreamMessage(body []byte, apiKey, fallback string) string {
	var envelope struct {
		Error struct {
			Message string `json:"message"`
		} `json:"error"`
	}
	if json.Unmarshal(body, &envelope) != nil || strings.TrimSpace(envelope.Error.Message) == "" {
		return fallback
	}
	message := envelope.Error.Message
	if len(message) > 500 {
		return fallback
	}
	contains := func(value string) bool {
		if value == "" {
			return false
		}
		escaped, _ := json.Marshal(value)
		return strings.Contains(message, value) || strings.Contains(message, string(escaped[1:len(escaped)-1]))
	}
	if contains(apiKey) {
		return fallback
	}
	// A key's leading or trailing characters are how a vendor quotes one.
	if len(apiKey) >= 8 && (contains(apiKey[:8]) || contains(apiKey[len(apiKey)-8:])) {
		return fallback
	}
	payloads, ok := r.contentPayloads()
	if !ok {
		return fallback
	}
	var sensitive func(any) bool
	sensitive = func(value any) bool {
		switch v := value.(type) {
		case string:
			return contains(v)
		case json.Number:
			return contains(v.String())
		case bool:
			return contains(strconv.FormatBool(v))
		case map[string]any:
			for k, item := range v {
				if contains(k) || sensitive(item) {
					return true
				}
			}
		case []any:
			for _, item := range v {
				if sensitive(item) {
					return true
				}
			}
		}
		return false
	}
	for _, raw := range payloads {
		var value any
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		if contains(string(raw)) || (decoder.Decode(&value) == nil && sensitive(value)) {
			return fallback
		}
	}
	return message
}

// contentPayloads returns the parts of the body a vendor message must not
// echo: every top-level field but model and questions, and every question
// field but its type and name.
func (r *DecisionRequest) contentPayloads() ([]json.RawMessage, bool) {
	members, err := objectMembers(r.Body)
	if err != nil {
		return nil, false
	}
	var payloads []json.RawMessage
	questionFields := func(raw json.RawMessage) bool {
		fields, err := objectMembers(raw)
		if err != nil {
			return false
		}
		for _, f := range fields {
			if f.key != "type" && f.key != "name" {
				payloads = append(payloads, f.value)
			}
		}
		return true
	}
	for _, m := range members {
		switch m.key {
		case "model":
		case "questions":
			switch jsonKind(m.value) {
			case '{':
				questions, err := objectMembers(m.value)
				if err != nil {
					return nil, false
				}
				for _, q := range questions {
					if !questionFields(q.value) {
						return nil, false
					}
				}
			case '[':
				var questions []json.RawMessage
				if json.Unmarshal(m.value, &questions) != nil {
					return nil, false
				}
				for _, q := range questions {
					if !questionFields(q) {
						return nil, false
					}
				}
			default:
				payloads = append(payloads, m.value)
			}
		default:
			payloads = append(payloads, m.value)
		}
	}
	return payloads, true
}

// retryAfter returns an upstream Retry-After worth passing on: a number of
// seconds or an HTTP date, and nothing else.
func retryAfter(header http.Header) string {
	v := strings.TrimSpace(header.Get("Retry-After"))
	if v == "" || len(v) > 64 {
		return ""
	}
	if n, err := strconv.Atoi(v); err == nil {
		if n < 0 {
			return ""
		}
		return strconv.Itoa(n)
	}
	if _, err := http.ParseTime(v); err == nil {
		return v
	}
	return ""
}

// DecisionUpstreamError maps a decision vendor's non-200 answer onto the
// gateway's error table. The gateway does not retry; Retry-After is passed on
// where the vendor set one. message is the vendor's own text, already through
// SafeUpstreamMessage; only a 400 or 422 carries it to the caller.
func DecisionUpstreamError(vendor string, status int, header http.Header, message string) *APIError {
	e := &APIError{UpstreamStatus: status}
	switch status {
	case http.StatusUnauthorized, http.StatusForbidden:
		e.Status, e.Type, e.Code = http.StatusForbidden, ErrTypePermission, "provider_key_rejected"
		e.Message = vendor + " rejected the provider key. Check the key saved for it."
	case http.StatusPaymentRequired:
		e.Status, e.Type, e.Code = http.StatusPaymentRequired, ErrTypeUpstream, "provider_payment_required"
		e.Message = vendor + " requires payment on the account that owns the provider key."
	case http.StatusNotFound:
		e.Status, e.Type, e.Code = http.StatusNotFound, ErrTypeNotFound, "model_not_found"
		e.Message = vendor + " does not serve this model."
	case http.StatusRequestEntityTooLarge:
		e.Status, e.Type, e.Code = http.StatusRequestEntityTooLarge, ErrTypeInvalidRequest, "request_too_large"
		e.Message = vendor + " rejected the request as too large."
	case http.StatusTooManyRequests:
		e.Status, e.Type, e.Code = http.StatusTooManyRequests, ErrTypeRateLimit, "rate_limit_exceeded"
		e.Message = vendor + " rate limit exceeded."
		e.RetryAfter = retryAfter(header)
	case http.StatusServiceUnavailable, 529:
		e.Status, e.Type, e.Code = http.StatusServiceUnavailable, ErrTypeUpstream, "provider_overloaded"
		e.Message = vendor + " is overloaded; retry later."
		e.RetryAfter = retryAfter(header)
	case http.StatusRequestTimeout, http.StatusGatewayTimeout:
		e.Status, e.Type, e.Code = http.StatusGatewayTimeout, ErrTypeGatewayTimeout, "gateway_timeout"
		e.Message = vendor + " timed out."
	case http.StatusBadRequest, http.StatusUnprocessableEntity:
		e.Status, e.Type, e.Code = status, ErrTypeInvalidRequest, "upstream_rejected"
		e.Message = message
	default:
		e.Status, e.Type, e.Code = http.StatusBadGateway, ErrTypeUpstream, "upstream_error"
		e.Message = vendor + " request failed."
	}
	return e
}

// DecisionInvalidResponse is the error for a 200 that is not a JSON object.
func DecisionInvalidResponse(vendor string) *APIError {
	return &APIError{
		Status:         http.StatusBadGateway,
		Type:           ErrTypeUpstream,
		Code:           "upstream_invalid_response",
		Message:        vendor + " returned a response that is not a JSON object.",
		UpstreamStatus: http.StatusOK,
	}
}

// DecisionTransportError is the error for a call that got no answer: a
// timeout when the deadline passed, an upstream failure otherwise. Neither
// carries the transport's own text, which can name the upstream's address.
func DecisionTransportError(ctx context.Context, err error, vendor string) *APIError {
	var netErr net.Error
	if ctx.Err() != nil || errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &netErr) && netErr.Timeout()) {
		return ErrGatewayTimeout(vendor + " request timed out")
	}
	return &APIError{Status: http.StatusBadGateway, Type: ErrTypeUpstream, Code: "upstream_error", Message: vendor + " request failed."}
}
