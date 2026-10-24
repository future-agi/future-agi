package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

// A /v1/responses request is relayed as the provider sent it, and priced
// after it from rc.Response's usage (the cost plugin). A non-streamed
// response carries its usage in its body. A stream carries it in its last
// event: response.completed, or response.incomplete or response.failed for a
// response that stopped early, each with the whole response object, usage
// included. A background response is answered later, and its usage never
// passes through the gateway at all.

// responsesUsage is the model and usage of a Responses API response object:
// a non-streamed response's body, or the response a stream's last event
// carries. usage is nil when the response reports none. The OpenAI-run tools
// it used are priced on top of its tokens.
func responsesUsage(response []byte) (model string, usage *models.Usage) {
	var parsed struct {
		Model string `json:"model"`
		Usage *struct {
			InputTokens  int `json:"input_tokens"`
			OutputTokens int `json:"output_tokens"`
			TotalTokens  int `json:"total_tokens"`
		} `json:"usage"`
	}
	if json.Unmarshal(response, &parsed) != nil {
		return "", nil
	}
	if parsed.Usage == nil {
		return parsed.Model, nil
	}
	return parsed.Model, &models.Usage{
		PromptTokens:     parsed.Usage.InputTokens,
		CompletionTokens: parsed.Usage.OutputTokens,
		TotalTokens:      parsed.Usage.TotalTokens,
		ServerToolUse:    openai.ResponsesServerToolUsage(response),
	}
}

// maxResponsesUsageLineBytes bounds the stream line responsesStreamUsage
// holds while it waits for the line's end: a stream's last event carries the
// whole response, so it is as large as a non-streamed response, which the
// provider reads up to 10 MiB. A longer line is skipped. A stream whose last
// event is such a line has no usage the gateway can read: on prepaid credit
// it is charged the hold (heldResponsesUsage), on any other key it is not
// priced.
const maxResponsesUsageLineBytes = 10 << 20

// eventHeadBytes is how much of a skipped line responsesStreamUsage reads to
// tell whether it was a last event: its type comes first in the event the
// providers send.
const eventHeadBytes = 256

// responsesStreamUsage reads the Responses API stream the gateway relays for
// the response object of its last event (response.completed,
// response.incomplete or response.failed). It keeps only that object, so it
// holds at most one line beyond it.
type responsesStreamUsage struct {
	line     []byte // the current line, until its newline arrives
	skip     bool   // the current line passed maxResponsesUsageLineBytes
	skipped  bool   // a line was skipped
	response []byte // the response object of the last such event
	// eventFinal: the current event's "event:" line named an event that
	// ends the response. finalSkipped: a skipped line was such an event's
	// data, by that name or by the type its first bytes name.
	eventFinal   bool
	finalSkipped bool
}

// Write takes the next bytes of the relayed stream, which split lines
// anywhere.
func (u *responsesStreamUsage) Write(p []byte) {
	for len(p) > 0 {
		end := bytes.IndexByte(p, '\n')
		if end < 0 {
			u.hold(p)
			return
		}
		u.hold(p[:end])
		if !u.skip {
			u.readLine(u.line)
		}
		u.line, u.skip = u.line[:0], false
		p = p[end+1:]
	}
}

func (u *responsesStreamUsage) hold(p []byte) {
	if u.skip {
		return
	}
	if len(u.line)+len(p) > maxResponsesUsageLineBytes {
		if u.lastEventData(p) {
			u.finalSkipped = true
		}
		u.line, u.skip, u.skipped = u.line[:0], true, true
		return
	}
	u.line = append(u.line, p...)
}

// lastEventData reports whether the line being skipped (u.line, then p) is
// the data of an event that ends the response: its event line named one, or
// its first bytes do.
func (u *responsesStreamUsage) lastEventData(p []byte) bool {
	head := make([]byte, 0, eventHeadBytes)
	head = append(head, u.line[:min(len(u.line), eventHeadBytes)]...)
	head = append(head, p[:min(len(p), eventHeadBytes-len(head))]...)
	if !bytes.HasPrefix(head, []byte("data:")) {
		return false
	}
	return u.eventFinal || namesAFinalEvent(head)
}

// The events that end a response, each carrying the response object.
var responsesFinalEvents = [][]byte{
	[]byte(`"response.completed"`), []byte(`"response.incomplete"`), []byte(`"response.failed"`),
}

// readLine keeps the response of a data line whose event ends the response.
// Only a line that names such an event is decoded.
func (u *responsesStreamUsage) readLine(line []byte) {
	line = bytes.TrimSuffix(line, []byte("\r"))
	if name, ok := bytes.CutPrefix(line, []byte("event:")); ok {
		u.eventFinal = isFinalEventName(bytes.TrimSpace(name))
		return
	}
	if len(line) == 0 {
		u.eventFinal = false // an event ends at a blank line
		return
	}
	data, ok := bytes.CutPrefix(line, []byte("data:"))
	if !ok || !namesAFinalEvent(data) {
		return
	}
	var ev struct {
		Type     string          `json:"type"`
		Response json.RawMessage `json:"response"`
	}
	if json.Unmarshal(bytes.TrimPrefix(data, []byte(" ")), &ev) != nil {
		return
	}
	switch ev.Type {
	case "response.completed", "response.incomplete", "response.failed":
		u.response = ev.Response // Unmarshal copied it out of the line
	}
}

func namesAFinalEvent(data []byte) bool {
	for _, name := range responsesFinalEvents {
		if bytes.Contains(data, name) {
			return true
		}
	}
	return false
}

// isFinalEventName reports whether an "event:" line's name is an event that
// ends the response.
func isFinalEventName(name []byte) bool {
	for _, quoted := range responsesFinalEvents {
		if bytes.Equal(name, quoted[1:len(quoted)-1]) {
			return true
		}
	}
	return false
}

// applyTo puts the stream's response on rc for the post-plugins, as
// handleResponseNonStream puts a non-streamed one: its model on
// rc.ResolvedModel, and rc.Response with that model and, when the last event
// reported it, the usage the cost plugin prices. It reports whether the
// stream's usage was read.
func (u *responsesStreamUsage) applyTo(rc *models.RequestContext) bool {
	var usage *models.Usage
	if len(u.response) > 0 {
		var model string
		if model, usage = responsesUsage(u.response); model != "" {
			rc.ResolvedModel = model
		}
	}
	rc.Response = &models.ChatCompletionResponse{Model: rc.ResolvedModel, Usage: usage}
	return usage != nil
}

// usageUnreadableMetadataKey is the request metadata key that says why a
// request was priced on an estimate rather than the usage the provider
// reported (caller metadata never sets it).
const usageUnreadableMetadataKey = "usage_unreadable"

// chargeTheHold prices a /v1/responses stream paid from prepaid credit whose
// last event the gateway could not read (over maxResponsesUsageLineBytes)
// at the hold, never at 0: rc.Response gets heldResponsesUsage, which the
// cost plugin prices at the model's card as any usage (so the cost, the
// budgets, the managed key's debit and the request log all take the same
// amount), marked usage_estimated, with the reason in usage_unreadable.
func (h *Handlers) chargeTheHold(ctx context.Context, rc *models.RequestContext) {
	rc.Response.Usage = h.heldResponsesUsage(ctx, rc)
	rc.SetMetadata("usage_estimated", "true")
	rc.SetMetadata(usageUnreadableMetadataKey, "final_event_over_limit")
}

// heldResponsesUsage is the usage a hold for rc assumes, as the credits
// plugin holds a managed request (billing.EstimateWorstCaseMicros): its
// prompt as the gateway estimates it (billing.EstimateInputTokens, from the
// body the provider got, in the chat shape), and its max_output_tokens or,
// without one, the most a hold assumes (billing.DefaultMaxOut: the model's
// maximum output, at most the org's default_max_tokens_estimate or
// managed.reserve_default_output_tokens).
//
// When no check read the body before, it is read here, in one pass over it
// that reads no value nested more than requestview.MaxDepth deep, so
// the read is bounded by the body's size. It is not cut short when the
// client goes away (context.WithoutCancel): the stream was served, and its
// charge must not depend on when the client hung up.
func (h *Handlers) heldResponsesUsage(ctx context.Context, rc *models.RequestContext) *models.Usage {
	var req *models.ChatCompletionRequest
	if rc.PassThrough != nil {
		req, _ = rc.PassThrough.ChatRequest(context.WithoutCancel(ctx))
	}
	configured := h.reserveDefaultOutputTokens
	orgID := rc.Metadata["org_id"]
	if orgID == "" {
		orgID = rc.Metadata["key_org_id"]
	}
	if n := h.OrgDefaultMaxTokensEstimate(orgID); n > 0 {
		configured = n
	}
	in := billing.EstimateInputTokens(req)
	out := billing.OutputCap(req, billing.DefaultMaxOut(h.modelMaxOutput(rc.Provider, rc.Model), configured))
	return &models.Usage{PromptTokens: in, CompletionTokens: out, TotalTokens: in + out}
}

// modelMaxOutput is model's maximum output on providerID in the model
// database, 0 when unknown (the credits plugin's maxOutput lookup).
func (h *Handlers) modelMaxOutput(providerID, model string) int {
	db := h.ModelDB()
	if db == nil {
		return 0
	}
	if info, ok := db.Get(providerID + "/" + model); ok {
		return info.MaxOutputTokens
	}
	if info, ok := db.Get(model); ok {
		return info.MaxOutputTokens
	}
	return 0
}

// paidFromPrepaidCredit reports whether rc is paid from prepaid credit,
// which must never pay for what the gateway cannot price: an org's managed
// credits (billing_mode managed) or a managed key's own balance (key_type
// managed, debited in the credits plugin).
func paidFromPrepaidCredit(rc *models.RequestContext) bool {
	return rc.Metadata["billing_mode"] == billingManaged || rc.Metadata["key_type"] == "managed"
}

// errUsageUnavailable refuses a request paid from prepaid credit whose usage
// the gateway cannot read, before the provider is called.
func errUsageUnavailable() *models.APIError {
	return &models.APIError{
		Status: http.StatusBadRequest,
		Type:   models.ErrTypeInvalidRequest,
		Code:   "usage_unavailable",
		Message: "a background response's usage does not pass through the gateway, so it cannot be billed to " +
			"this key's prepaid credit; send the request without background",
	}
}
