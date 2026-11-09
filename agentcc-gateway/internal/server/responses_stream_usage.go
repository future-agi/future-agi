package server

// The estimate a budgeted key's streamed POST /v1/responses call is priced on
// when the gateway reads no usage from the stream (CN22c fix round 1, review
// R-CN22c-1; re-homed on this line by MCP-APPS-PLAN adaptation (a)).
//
// The handler prices a stream from the usage its last event reports
// (responsesStreamUsage, responses_usage.go). A stream with no usage the
// gateway can read, cut before its last event or with a last event over the
// line limit, is otherwise unpriced unless prepaid credit pays for it (the
// hold, chargeTheHold). A key with a spend budget must be priced however its
// stream ends, or a client spends past the budget by dropping each stream
// before its usage arrives. Such a stream is priced on an estimate: the
// prompt as the hold estimates it, and the text the stream's deltas carried
// at about 4 characters a token, as a chat stream's estimate counts its
// output (estimatedStreamUsage).

import (
	"bytes"
	"context"
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// maxResponsesDeltaLineBytes bounds the stream line responsesStreamOutput
// holds while it waits for the line's end. A delta event carries a few
// tokens; a longer line, such as the last event that carries the whole
// response, is skipped unread and never held.
const maxResponsesDeltaLineBytes = 1 << 20

// responsesStreamOutput counts the characters of the text the relayed
// stream's delta events carried (output text, reasoning, refusals, tool-call
// arguments: every event's string "delta"). The handler feeds it only a
// budgeted key's stream; no other stream pays for the count.
type responsesStreamOutput struct {
	line  []byte // the current line, until its newline arrives
	skip  bool   // the current line passed maxResponsesDeltaLineBytes
	chars int
}

// Write takes the next bytes of the relayed stream, which split lines
// anywhere.
func (o *responsesStreamOutput) Write(p []byte) {
	for len(p) > 0 {
		end := bytes.IndexByte(p, '\n')
		if end < 0 {
			o.hold(p)
			return
		}
		o.hold(p[:end])
		if !o.skip {
			o.readLine(o.line)
		}
		o.line, o.skip = o.line[:0], false
		p = p[end+1:]
	}
}

func (o *responsesStreamOutput) hold(p []byte) {
	if o.skip {
		return
	}
	if len(o.line)+len(p) > maxResponsesDeltaLineBytes {
		o.line, o.skip = o.line[:0], true
		return
	}
	o.line = append(o.line, p...)
}

var responsesDeltaField = []byte(`"delta"`)

// readLine counts the string delta of a data line. Only a line that names a
// delta is decoded.
func (o *responsesStreamOutput) readLine(line []byte) {
	data, ok := bytes.CutPrefix(bytes.TrimSuffix(line, []byte("\r")), []byte("data:"))
	if !ok || !bytes.Contains(data, responsesDeltaField) {
		return
	}
	var ev struct {
		Delta json.RawMessage `json:"delta"`
	}
	if json.Unmarshal(bytes.TrimSpace(data), &ev) != nil || len(ev.Delta) == 0 || ev.Delta[0] != '"' {
		return
	}
	var s string
	if json.Unmarshal(ev.Delta, &s) == nil {
		o.chars += len(s)
	}
}

// estimatedResponsesStreamUsage is the usage a budgeted key's stream with no
// readable usage is priced on: its prompt as the hold estimates it
// (billing.EstimateInputTokens over the body the provider got, read in the
// chat shape in one pass that the client's hang-up does not cut short, as
// heldResponsesUsage reads it), and outputChars, the text the stream sent,
// at about 4 characters a token.
func estimatedResponsesStreamUsage(ctx context.Context, rc *models.RequestContext, outputChars int) *models.Usage {
	var req *models.ChatCompletionRequest
	if rc.PassThrough != nil {
		req, _ = rc.PassThrough.ChatRequest(context.WithoutCancel(ctx))
	}
	in := billing.EstimateInputTokens(req)
	out := (outputChars + 3) / 4
	return &models.Usage{PromptTokens: in, CompletionTokens: out, TotalTokens: in + out}
}

// estimateBudgetedResponsesStream prices a budgeted key's stream that
// reported no usage the gateway could read on the estimate: rc.Response's
// usage (set by responsesStreamUsage.applyTo), which the cost plugin prices
// at the model's card and the keyscope plugin adds to the key's spend,
// marked usage_estimated.
func estimateBudgetedResponsesStream(ctx context.Context, rc *models.RequestContext, o *responsesStreamOutput) {
	rc.Response.Usage = estimatedResponsesStreamUsage(ctx, rc, o.chars)
	rc.SetMetadata("usage_estimated", "true")
}
