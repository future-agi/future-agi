package futureagi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// evalRequest is the payload sent to the evaluation API.
type evalRequest struct {
	Inputs []evalInput           `json:"inputs"`
	Config map[string]evalConfig `json:"config"`
}

type evalInput struct {
	Input     string `json:"input"`
	CallType  string `json:"call_type,omitempty"`
	MaxTokens int    `json:"max_tokens,omitempty"`
}

type evalConfig struct {
	CallType  string `json:"call_type,omitempty"`
	MaxTokens int    `json:"max_tokens,omitempty"`
}

// evalResponse is the top-level API response.
type evalResponse struct {
	Result []evalResultGroup `json:"result"`
}

type evalResultGroup struct {
	Evaluations []evaluation `json:"evaluations"`
}

type evaluation struct {
	Name       string      `json:"name"`
	Output     interface{} `json:"output"`
	Reason     string      `json:"reason"`
	Runtime    float64     `json:"runtime"`
	OutputType string      `json:"outputType"`
	EvalID     string      `json:"evalId"`
}

// callAPI sends the evaluation request to Future AGI.
func (g *FutureAGIGuardrail) callAPI(ctx context.Context, text string, principal evalPrincipal) (*evalResponse, error) {
	configMap := make(map[string]evalConfig, len(g.evalIDs))
	for _, id := range g.evalIDs {
		configMap[id] = evalConfig{CallType: g.callType}
	}
	payload := evalRequest{
		Inputs: []evalInput{
			{Input: text, CallType: g.callType, MaxTokens: g.maxTokens},
		},
		Config: configMap,
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	url := strings.TrimRight(g.baseURL, "/") + "/sdk/api/v1/eval/"

	var lastErr error
	attempts := 1 + g.retry
	for i := 0; i < attempts; i++ {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}

		resp, err := g.doRequest(ctx, url, body, principal)
		if err != nil {
			lastErr = err
			continue
		}
		return resp, nil
	}

	return nil, fmt.Errorf("failed after %d attempts: %w", attempts, lastErr)
}

// doRequest makes one evaluation call. A failure that could carry where the
// gateway connected (the URL, a dial, DNS or proxy address) is reported
// through guardrails.CallError, which names neither: the message reaches the
// org whose request is checked, and the base_url may be the operator's
// FI_BASE_URL.
func (g *FutureAGIGuardrail) doRequest(ctx context.Context, url string, body []byte, principal evalPrincipal) (*evalResponse, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, guardrails.CallError(g.name, err)
	}

	req.Header.Set("Content-Type", "application/json")
	if g.authMode == "gateway_service" {
		if err := g.signAttempt(req, body, principal); err != nil {
			return nil, err
		}
	} else {
		req.Header.Set("X-Api-Key", g.apiKey)
		req.Header.Set("X-Secret-Key", g.secretKey)
	}

	resp, err := g.client.Do(req)
	if err != nil {
		return nil, guardrails.CallError(g.name, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusForbidden {
		return nil, fmt.Errorf("authentication failed (HTTP 403)")
	}
	if resp.StatusCode == http.StatusBadRequest {
		return nil, fmt.Errorf("bad request (HTTP 400)")
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("unexpected status %d", resp.StatusCode)
	}

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20)) // 1MB limit
	if err != nil {
		return nil, fmt.Errorf("read response: %w", guardrails.CallError(g.name, err))
	}

	var result evalResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}

	return &result, nil
}

// parseResult maps the evaluation API response to a CheckResult.
// When multiple eval_ids are checked, the result fails if any single eval fails;
// the failing eval's reason and details are surfaced.
func parseResult(resp *evalResponse) *guardrails.CheckResult {
	if resp == nil || len(resp.Result) == 0 {
		return &guardrails.CheckResult{Pass: true, Message: "no evaluation results"}
	}

	var firstEval *evaluation
	var firstFail *evaluation

	for i := range resp.Result {
		for j := range resp.Result[i].Evaluations {
			eval := &resp.Result[i].Evaluations[j]
			if firstEval == nil {
				firstEval = eval
			}
			if isFailedOutput(eval.Output) && firstFail == nil {
				firstFail = eval
			}
		}
	}

	if firstFail != nil {
		return buildResult(firstFail, false, 1.0)
	}
	if firstEval != nil {
		return buildResult(firstEval, true, 0.0)
	}
	return &guardrails.CheckResult{Pass: true, Message: "no evaluation results"}
}

func isFailedOutput(output interface{}) bool {
	switch v := output.(type) {
	case string:
		return strings.EqualFold(v, "failed")
	case bool:
		return v
	}
	return false
}

func buildResult(eval *evaluation, pass bool, score float64) *guardrails.CheckResult {
	return &guardrails.CheckResult{
		Pass:    pass,
		Score:   score,
		Message: eval.Reason,
		Details: map[string]interface{}{
			"eval_id":     eval.EvalID,
			"eval_name":   eval.Name,
			"runtime_ms":  eval.Runtime,
			"output_type": eval.OutputType,
			"output":      eval.Output,
		},
	}
}
