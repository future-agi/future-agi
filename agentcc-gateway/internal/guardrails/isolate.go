package guardrails

import "github.com/futureagi/agentcc-gateway/internal/models"

// A check runs in a goroutine of its own, and the gateway stops waiting for
// it at its timeout; the goroutine goes on. A check on the request that
// rewrites what it reads (RequestRewriter: pii-detection's mask, redact or
// hash) runs on a copy of the messages (isolate), and the copy is written
// back only from a check that returned in time; a check that did not refuses
// the request, since its rewrite is not applied (refuseUnappliedRewrite). So
// the request the gateway sends (rc.Request, or the request view of a body
// passed through, whose rewrite ApplyChecks writes into the body) is never
// written while the provider call serializes it. A check in async mode, which
// the request never waits for, runs on a copy of the messages it reads
// (snapshot), whatever it does, and nothing is written back.
//
// A check on the request that does not rewrite it runs on its own copy of
// the request's fields, taken before it starts: one that outlives its
// timeout reads the request as it was then, not a later check's write-back
// (which replaces the request's messages and never writes into the ones it
// read) nor a field the plugins after the checks set.

// isolated is a check's input and its own copy of the request, in one
// allocation.
type isolated struct {
	in  CheckInput
	req models.ChatCompletionRequest
}

// isolate is the input a check on the request runs on: input with its own
// copy of the request's fields; for a check that rewrites the request, also
// a copy of the request's messages (and of their tool calls, whose arguments
// a rewrite writes), and commit, which makes the copy, as the check left it,
// the request's messages. commit is nil when the check does not rewrite the
// request; with no request the check runs on input itself.
func isolate(g Guardrail, input *CheckInput) (checkInput *CheckInput, commit func()) {
	if input == nil || input.Request == nil {
		return input, nil
	}
	live := input.Request
	own := &isolated{in: *input, req: *live}
	own.in.Request = &own.req
	if !rewritesRequest(g) {
		return &own.in, nil
	}
	own.req.Messages = copyMessages(live.Messages)
	return &own.in, func() { live.Messages = own.req.Messages }
}

// snapshot is input with copies of the request's messages and the answer's
// choices, for a check that runs while the request goes on (async mode):
// whatever it reads or writes is its own.
func snapshot(input *CheckInput) *CheckInput {
	if input == nil {
		return nil
	}
	in := *input
	if input.Request != nil {
		req := *input.Request
		req.Messages = copyMessages(input.Request.Messages)
		in.Request = &req
	}
	if input.Response != nil {
		resp := *input.Response
		resp.Choices = append([]models.Choice(nil), input.Response.Choices...)
		for i := range resp.Choices {
			resp.Choices[i].Message.ToolCalls = append([]models.ToolCall(nil), resp.Choices[i].Message.ToolCalls...)
		}
		in.Response = &resp
	}
	return &in
}

// copyMessages is msgs in a new slice, each message's tool calls in a new
// slice too. Contents are shared: a check replaces a message's content, it
// never writes into it.
func copyMessages(msgs []models.Message) []models.Message {
	if msgs == nil {
		return nil
	}
	out := make([]models.Message, len(msgs))
	copy(out, msgs)
	for i := range out {
		if len(out[i].ToolCalls) > 0 {
			out[i].ToolCalls = append([]models.ToolCall(nil), out[i].ToolCalls...)
		}
	}
	return out
}
