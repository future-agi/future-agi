package guardrails

import (
	"fmt"
	"io"
)

// ExecutionFailure carries an unavailable verdict to the engine's failure policy.
// It must never be interpreted as a scored content decision.
func ExecutionFailure(message string) *CheckResult {
	return &CheckResult{Err: fmt.Errorf("%s", message), Pass: false, Score: 1, Message: message}
}

// ReadResponseBody rejects an oversized envelope instead of accepting a valid
// prefix followed by truncated data. All HTTP verdict adapters share this limit.
func ReadResponseBody(body io.Reader) ([]byte, error) {
	const limit = 1 << 20
	data, err := io.ReadAll(io.LimitReader(body, limit+1))
	if err != nil {
		return nil, err
	}
	if len(data) > limit {
		return nil, fmt.Errorf("guardrail response exceeds %d bytes", limit)
	}
	return data, nil
}
