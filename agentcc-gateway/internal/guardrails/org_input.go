package guardrails

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// InputRemediator is implemented by checks that intentionally rewrite content.
// These checks run in name order before parallel readers, on a private snapshot.
// Check must finish all writes before returning. A timed-out or failed snapshot
// is never published, even when this tenant opted into fail-open.
type InputRemediator interface {
	MutatesInput() bool
}

func mutatesInput(g Guardrail) bool {
	m, ok := g.(InputRemediator)
	return ok && m.MutatesInput()
}

// cloneCheckInput also copies RawMessage byte slices, tool arguments, provider
// extras, response choices and metadata. A shallow struct copy is insufficient:
// a timed-out checker can outlive the pooled request context. The models' JSON
// codecs preserve provider extras and non-function tools. Request message
// content is copied byte for byte instead: a JSON round trip would re-escape
// '<', '>' and '&' (6 bytes each), so a check that changed nothing would hand
// back different bytes, and a large body would grow in every snapshot.
func cloneCheckInput(input *CheckInput) (*CheckInput, error) {
	shadow := *input
	var contents []json.RawMessage
	if input.Request != nil {
		req := *input.Request
		req.Messages = make([]models.Message, len(input.Request.Messages))
		copy(req.Messages, input.Request.Messages)
		contents = make([]json.RawMessage, len(req.Messages))
		for i := range req.Messages {
			contents[i] = req.Messages[i].Content
			req.Messages[i].Content = nil
		}
		shadow.Request = &req
	}
	data, err := json.Marshal(&shadow)
	if err != nil {
		return nil, fmt.Errorf("snapshot guardrail input: %w", err)
	}
	var owned CheckInput
	if err := json.Unmarshal(data, &owned); err != nil {
		return nil, fmt.Errorf("snapshot guardrail input: %w", err)
	}
	if owned.Request != nil {
		if len(owned.Request.Messages) != len(contents) {
			return nil, fmt.Errorf("snapshot guardrail input: %d messages, want %d", len(owned.Request.Messages), len(contents))
		}
		for i, content := range contents {
			owned.Request.Messages[i].Content = bytes.Clone(content)
		}
	}
	return &owned, nil
}

func (p *GuardrailPlugin) runOrgCheck(ctx context.Context, g Guardrail, input *CheckInput, timeout time.Duration, failOpen bool) *CheckResult {
	owned, err := cloneCheckInput(input)
	if err != nil {
		// No check ran. Apply the same explicit failure policy and record it.
		return &CheckResult{Err: err, FailureAdmitted: failOpen, Message: "guardrail_unavailable: could not snapshot guardrail input"}
	}
	cr := p.runGuardrailSafe(ctx, g, owned, timeout, failOpen)
	if mutatesInput(g) && cr != nil && cr.Err == nil {
		// Only content is remediated. Preserve request routing and response usage
		// accounting, including native-route projections selected by checkInput.
		if input.Request != nil && owned.Request != nil {
			input.Request.Messages = owned.Request.Messages
		}
		if input.Response != nil && owned.Response != nil {
			input.Response.Choices = owned.Response.Choices
		}
	}
	return cr
}
