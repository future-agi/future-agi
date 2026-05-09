package futureagi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// FutureAGIGuardrail calls Future AGI's evaluation API for ML-based guardrail checks.
type FutureAGIGuardrail struct {
	name      string
	evalID    string
	apiKey    string
	secretKey string
	baseURL   string
	callType  string
	retry     int
	client    *http.Client
}

// evalRequest is the payload sent to the evaluation API.
type evalRequest struct {
	Inputs []evalInput           `json:"inputs"`
	Config map[string]evalConfig `json:"config"`
}

type evalInput struct {
	Input    string `json:"input"`
	CallType string `json:"call_type,omitempty"`
}

type evalConfig struct {
	CallType string `json:"call_type,omitempty"`
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

// New creates a FutureAGIGuardrail from rule config.
func New(name string, cfg map[string]interface{}) *FutureAGIGuardrail {
	g := &FutureAGIGuardrail{
		name:     name,
		baseURL:  "https://api.futureagi.com",
		callType: "protect",
		retry:    0,
		client:   &http.Client{Timeout: 15 * time.Second},
	}

	if cfg == nil {
		return g
	}

	if v, ok := cfg["eval_id"].(string); ok {
		g.evalID = v
	}
	if v, ok := cfg["api_key"].(string); ok {
		g.apiKey = expandEnv(v)
	}
	if v, ok := cfg["secret_key"].(string); ok {
		g.secretKey = expandEnv(v)
	}
	if v, ok := cfg["base_url"].(string); ok && v != "" {
		g.baseURL = expandEnv(v)
	}
	if v, ok := cfg["call_type"].(string); ok && v != "" {
		g.callType = v
	}
	if v, ok := cfg["retry"]; ok {
		switch n := v.(type) {
		case int:
			g.retry = n
		case float64:
			g.retry = int(n)
		}
	}
	if v, ok := cfg["timeout"].(string); ok {
		if d, err := time.ParseDuration(v); err == nil {
			g.client.Timeout = d
		}
	}

	// Fallback to env vars for keys.
	if g.apiKey == "" {
		g.apiKey = os.Getenv("FI_API_KEY")
	}
	if g.secretKey == "" {
		g.secretKey = os.Getenv("FI_SECRET_KEY")
	}
	if g.baseURL == "https://api.futureagi.com" {
		if envURL := os.Getenv("FI_BASE_URL"); envURL != "" {
			g.baseURL = envURL
		}
	}

	return g
}

func (g *FutureAGIGuardrail) Name() string           { return g.name }
func (g *FutureAGIGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check evaluates text against the Future AGI evaluation API.
func (g *FutureAGIGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil {
		return &guardrails.CheckResult{Pass: true}
	}
	if g.apiKey == "" || g.secretKey == "" {
		return &guardrails.CheckResult{
			Pass:    true,
			Message: "futureagi guardrail: missing api_key or secret_key, skipping",
		}
	}
	if g.evalID == "" {
		return &guardrails.CheckResult{
			Pass:    true,
			Message: "futureagi guardrail: missing eval_id, skipping",
		}
	}

	// Determine text to evaluate.
	var text string
	if input.Response != nil {
		text = extractOutputText(input)
	} else {
		text = extractInputText(input)
	}
	if text == "" {
		return &guardrails.CheckResult{Pass: true}
	}

	resp, err := g.callAPI(ctx, text)
	if err != nil {
		return &guardrails.CheckResult{
			Pass:    false,
			Score:   1.0,
			Message: fmt.Sprintf("futureagi evaluation failed: %v", err),
		}
	}

	return parseResult(resp)
}

// extractInputText concatenates all message contents from the request.
func extractInputText(input *guardrails.CheckInput) string {
	if input.Request == nil || len(input.Request.Messages) == 0 {
		return ""
	}
	var parts []string
	for _, msg := range input.Request.Messages {
		text := extractContentText(msg.Content)
		if text != "" {
			parts = append(parts, text)
		}
	}
	return strings.Join(parts, "\n")
}

// extractOutputText concatenates all choice message contents from the response.
func extractOutputText(input *guardrails.CheckInput) string {
	if input.Response == nil || len(input.Response.Choices) == 0 {
		return ""
	}
	var parts []string
	for _, choice := range input.Response.Choices {
		text := extractContentText(choice.Message.Content)
		if text != "" {
			parts = append(parts, text)
		}
	}
	return strings.Join(parts, "\n")
}

// extractContentText extracts a string from json.RawMessage content.
// Content can be a JSON string or an array of content parts.
func extractContentText(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	// Try string first.
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s
	}
	// Try array of content parts.
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &parts); err == nil {
		var texts []string
		for _, p := range parts {
			if p.Type == "text" && p.Text != "" {
				texts = append(texts, p.Text)
			}
		}
		return strings.Join(texts, " ")
	}
	return ""
}

// callAPI sends the evaluation request to Future AGI.
func (g *FutureAGIGuardrail) callAPI(ctx context.Context, text string) (*evalResponse, error) {
	payload := evalRequest{
		Inputs: []evalInput{
			{Input: text, CallType: g.callType},
		},
		Config: map[string]evalConfig{
			g.evalID: {CallType: g.callType},
		},
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

		resp, err := g.doRequest(ctx, url, body)
		if err != nil {
			lastErr = err
			continue
		}
		return resp, nil
	}

	return nil, fmt.Errorf("failed after %d attempts: %w", attempts, lastErr)
}

func (g *FutureAGIGuardrail) doRequest(ctx context.Context, url string, body []byte) (*evalResponse, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Api-Key", g.apiKey)
	req.Header.Set("X-Secret-Key", g.secretKey)

	resp, err := g.client.Do(req)
	if err != nil {
		return nil, err
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
		return nil, fmt.Errorf("read response: %w", err)
	}

	var result evalResponse
	if err := json.Unmarshal(respBody, &result); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}

	return &result, nil
}

// parseResult maps the evaluation API response to a CheckResult.
func parseResult(resp *evalResponse) *guardrails.CheckResult {
	if resp == nil || len(resp.Result) == 0 {
		return &guardrails.CheckResult{Pass: true, Message: "no evaluation results"}
	}

	for _, group := range resp.Result {
		if len(group.Evaluations) == 0 {
			continue
		}
		eval := group.Evaluations[0]

		pass := true
		score := 0.0

		switch v := eval.Output.(type) {
		case string:
			if strings.EqualFold(v, "failed") {
				pass = false
				score = 1.0
			}
		case bool:
			// true = harmful = fail
			if v {
				pass = false
				score = 1.0
			}
		}

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

	return &guardrails.CheckResult{Pass: true, Message: "no evaluation results"}
}

// IsFutureAGIConfig returns true if the config map has provider set to "futureagi".
func IsFutureAGIConfig(cfg map[string]interface{}) bool {
	if cfg == nil {
		return false
	}
	provider, ok := cfg["provider"].(string)
	return ok && provider == "futureagi"
}

// expandEnv replaces ${VAR_NAME} with the environment variable value.
func expandEnv(s string) string {
	if strings.HasPrefix(s, "${") && strings.HasSuffix(s, "}") {
		return os.Getenv(s[2 : len(s)-1])
	}
	return s
}
