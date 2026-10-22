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

// defaultBaseURL is the evaluation API a rule that names no base_url calls,
// unless the operator set FI_BASE_URL.
const defaultBaseURL = "https://api.futureagi.com"

// defaultTimeout bounds one evaluation call when the rule sets none.
const defaultTimeout = 15 * time.Second

// FutureAGIGuardrail calls Future AGI's evaluation API for ML-based guardrail checks.
type FutureAGIGuardrail struct {
	name      string
	evalIDs   []string
	apiKey    string
	secretKey string
	baseURL   string
	callType  string
	maxTokens int
	retry     int
	client    *http.Client
	// configErr is why the rule cannot call the evaluation API (a missing
	// key or eval id), or nil. Check refuses every request while it is set.
	configErr error
}

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

// NewConfigFileRule creates a FutureAGIGuardrail from a rule in the gateway's
// own config file, the one constructor that reads the environment. A
// config-file rule is operator-trusted, so a ${VAR} api_key, secret_key or
// base_url is expanded from the gateway's environment, and it keeps a plain
// client (a config.yaml rule may name the operator's own evaluation API on a
// private or loopback address without listing it). A rule that lacks a
// key or an eval id is still built, so the registry can hold it, but its Check
// refuses every request; ValidateConfig reports the same fault up front. Only
// the config.yaml registry loop and ValidateConfig call it: anything an
// organization configures is built with New (orgconfig.go), which reads no
// environment and dials through the guarded egress dialer.
func NewConfigFileRule(name string, cfg map[string]interface{}) *FutureAGIGuardrail {
	return build(name, cfg, expandEnv, plainClient)
}

// plainClient is NewConfigFileRule's client, and New's for the operator's own
// backend (FI_BASE_URL): no egress guard.
func plainClient(timeout time.Duration, _ bool) *http.Client {
	return &http.Client{Timeout: timeout}
}

// build parses a rule into a guardrail. expand resolves each credential and
// base_url string: NewConfigFileRule expands ${VAR} from the environment; New
// keeps the value literal. The FI_BASE_URL fallback for an unset base_url is
// operator configuration (it is never org-controllable and carries no org
// secret), so both keep it. The client comes from mkClient once the timeout
// and the destination are known; operatorBackend says the call goes to the
// operator's FI_BASE_URL rather than to a base_url the rule chose.
func build(name string, cfg map[string]interface{}, expand func(string) string, mkClient func(timeout time.Duration, operatorBackend bool) *http.Client) *FutureAGIGuardrail {
	g := &FutureAGIGuardrail{
		name:      name,
		baseURL:   defaultBaseURL,
		callType:  "protect",
		maxTokens: 10,
		retry:     0,
	}
	timeout := defaultTimeout

	// A nil cfg reads as empty: the rule is built with configErr set.
	if v, ok := cfg["eval_ids"]; ok {
		switch arr := v.(type) {
		case []interface{}:
			for _, item := range arr {
				if s, ok := item.(string); ok && s != "" {
					g.evalIDs = append(g.evalIDs, s)
				}
			}
		case []string:
			for _, s := range arr {
				if s != "" {
					g.evalIDs = append(g.evalIDs, s)
				}
			}
		}
	}
	if len(g.evalIDs) == 0 {
		if v, ok := cfg["eval_id"].(string); ok && v != "" {
			g.evalIDs = []string{v}
		}
	}
	if v, ok := cfg["api_key"].(string); ok {
		g.apiKey = expand(v)
	}
	if v, ok := cfg["secret_key"].(string); ok {
		g.secretKey = expand(v)
	}
	if v, ok := cfg["base_url"].(string); ok && v != "" {
		g.baseURL = expand(v)
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
	if v, ok := cfg["max_tokens"]; ok {
		switch n := v.(type) {
		case int:
			g.maxTokens = n
		case float64:
			g.maxTokens = int(n)
		}
	}
	if v, ok := cfg["timeout"].(string); ok {
		if d, err := time.ParseDuration(v); err == nil {
			timeout = d
		}
	}

	// A rule names its own credentials; the gateway reads no process-wide
	// key for it.
	// The evaluation API's address is the operator's to set: FI_BASE_URL
	// stands in for the built-in default.
	operatorBackend := false
	if envURL := os.Getenv("FI_BASE_URL"); envURL != "" {
		if g.baseURL == defaultBaseURL {
			g.baseURL = envURL
			operatorBackend = true
		} else if strings.TrimRight(g.baseURL, "/") == strings.TrimRight(envURL, "/") {
			operatorBackend = true
		}
	}
	g.client = mkClient(timeout, operatorBackend)

	g.configErr = g.validate()
	return g
}

// ValidateConfig reports why a provider: futureagi rule named name cannot
// call the evaluation API: api_key or secret_key empty (after ${VAR}
// expansion, so an unset variable counts as missing) or no eval id in
// eval_ids or eval_id. It returns nil for a complete rule. Check refuses
// every request on a rule this rejects. The error names the rule and the
// missing fields, never a credential.
func ValidateConfig(name string, cfg map[string]any) error {
	return NewConfigFileRule(name, cfg).configErr
}

// validate is ValidateConfig over the parsed rule.
func (g *FutureAGIGuardrail) validate() error {
	var missing []string
	if g.apiKey == "" {
		missing = append(missing, "api_key")
	}
	if g.secretKey == "" {
		missing = append(missing, "secret_key")
	}
	if len(g.evalIDs) == 0 {
		missing = append(missing, "eval_id or eval_ids")
	}
	if len(missing) == 0 {
		return nil
	}
	return fmt.Errorf("futureagi guardrail %q cannot run: missing %s (an unset ${VAR} counts as missing)",
		g.name, strings.Join(missing, ", "))
}

func (g *FutureAGIGuardrail) Name() string            { return g.name }
func (g *FutureAGIGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check evaluates text against the Future AGI evaluation API. A rule that
// ValidateConfig rejects fails closed before anything else, with no call: a
// failure without a score, which triggers at any threshold.
func (g *FutureAGIGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if g.configErr != nil {
		return &guardrails.CheckResult{Pass: false, Message: g.configErr.Error()}
	}
	if input == nil {
		return &guardrails.CheckResult{Pass: true}
	}

	// Determine text to evaluate.
	var text string
	if input.Response != nil {
		text = extractOutputText(input)
	} else {
		text = extractInputText(ctx, input)
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

// extractInputText is the text of the request's messages the service reads.
func extractInputText(ctx context.Context, input *guardrails.CheckInput) string {
	if input.Request == nil || len(input.Request.Messages) == 0 {
		return ""
	}
	// Each message's text and its tool calls' arguments
	// (guardrails.MessageText), so the service reads a value in a tool
	// call's arguments as it reads one in a message.
	var parts []string
	for i := range input.Request.Messages {
		if text, ok := guardrails.MessageText(ctx, &input.Request.Messages[i]); ok && text != "" {
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

		resp, err := g.doRequest(ctx, url, body)
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
func (g *FutureAGIGuardrail) doRequest(ctx context.Context, url string, body []byte) (*evalResponse, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, guardrails.CallError(g.name, err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Api-Key", g.apiKey)
	req.Header.Set("X-Secret-Key", g.secretKey)

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
