package guardrails

import (
	"context"
	"encoding/json"
	"fmt"
	"time"
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
// codecs preserve provider extras and non-function tools.
func cloneCheckInput(input *CheckInput) (*CheckInput, error) {
	data, err := json.Marshal(input)
	if err != nil {
		return nil, fmt.Errorf("snapshot guardrail input: %w", err)
	}
	var owned CheckInput
	if err := json.Unmarshal(data, &owned); err != nil {
		return nil, fmt.Errorf("snapshot guardrail input: %w", err)
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
