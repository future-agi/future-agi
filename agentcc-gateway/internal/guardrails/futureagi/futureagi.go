package futureagi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// defaultBaseURL is the evaluation API a rule calls when it names no base_url
// and FI_BASE_URL is unset.
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
	// configErr is why a rule built by New cannot run: an environment
	// reference in api_key, secret_key or base_url, or no key or eval id.
	// Check refuses every request while it is set. NewConfigFileRule
	// (config.yaml rules) never sets it.
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

func newGuardrail(name string) *FutureAGIGuardrail {
	return &FutureAGIGuardrail{
		name:      name,
		baseURL:   defaultBaseURL,
		callType:  "protect",
		maxTokens: 10,
		retry:     0,
		client:    &http.Client{Timeout: defaultTimeout},
	}
}

// New creates a FutureAGIGuardrail from org-pushed config: the per-org rules
// the dynamic guardrail factory builds. It is the default constructor because
// it is the safe one: the rule never takes a credential from the gateway
// process environment.
//
//   - A "${" in api_key, secret_key or base_url (the only fields
//     NewConfigFileRule resolves) is not expanded. The rule is built with a
//     configuration fault that names the field, never the value. A "${" in
//     any other field is sent as written.
//   - A rule without both keys, or without an eval id, is built with a fault
//     too.
//
// Check refuses every request on a rule with a fault and makes no call.
// FI_BASE_URL replaces the default base_url here too, as for config.yaml rules:
// it names the operator's own FutureAGI platform (the Helm chart points it at
// the in-cluster backend), not a credential, and the org's own keys go there.
// A failed call never names that host to the org (see Check).
//
// The org's base_url is caller-controlled, so the evaluation call goes through
// the gateway's guarded egress dialer (internal/mcp/egress), as an org webhook
// or external check does: after DNS, on every attempt and every redirect hop,
// a link-local or cloud-metadata address is always refused and a loopback or
// private address is refused unless the operator listed it in
// mcp.egress.private_allow; a redirect to another origin does not carry the
// org's keys. A nil dialer is the guard with no exceptions. The one
// destination that is not the org's choice is the operator's own backend,
// FI_BASE_URL: a rule that names no base_url or the built-in default (what
// the platform fills in) is sent there, and so is one whose base_url
// is exactly FI_BASE_URL (a trailing slash aside). That destination is
// operator configuration, normally an in-cluster address, so it keeps a plain
// client and needs no listing. Any other base_url, including another path,
// query or spelling of the same host, goes through the guard.
func New(name string, cfg map[string]interface{}, dialer *egress.Dialer) *FutureAGIGuardrail {
	g := newGuardrail(name)

	if field := guardrails.EnvReferenceField(cfg, "api_key", "base_url", "secret_key"); field != "" {
		g.configErr = fmt.Errorf("futureagi guardrail %q cannot run: %s holds an environment reference (${...}), "+
			"which org guardrail config may not use; set the value itself", name, field)
		g.client = dialer.CallbackClient(defaultTimeout) // Check makes no call; guarded all the same
		return g
	}

	g.readConfig(cfg, func(s string) string { return s })
	operatorBackend := g.applyBaseURLEnv()
	if !operatorBackend {
		g.client = dialer.CallbackClient(g.client.Timeout)
	}
	g.configErr = g.validate()

	return g
}

// NewConfigFileRule creates a FutureAGIGuardrail from a config.yaml rule,
// which the operator writes. A whole-string ${NAME} in api_key, secret_key or
// base_url is read from the gateway process environment, and a rule that
// names no key falls back to FI_API_KEY and FI_SECRET_KEY. Never use it for
// config an org pushes: that is New.
func NewConfigFileRule(name string, cfg map[string]interface{}) *FutureAGIGuardrail {
	g := newGuardrail(name)

	if cfg == nil {
		return g
	}

	g.readConfig(cfg, expandEnv)

	// Fallback to env vars for keys: config.yaml rules only.
	if g.apiKey == "" {
		g.apiKey = os.Getenv("FI_API_KEY")
	}
	if g.secretKey == "" {
		g.secretKey = os.Getenv("FI_SECRET_KEY")
	}
	g.applyBaseURLEnv()

	return g
}

// readConfig reads the rule's fields from cfg. resolve maps each api_key,
// secret_key and base_url value before use: expandEnv for config.yaml rules
// (NewConfigFileRule), the value itself for org rules (New).
func (g *FutureAGIGuardrail) readConfig(cfg map[string]interface{}, resolve func(string) string) {
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
		g.apiKey = resolve(v)
	}
	if v, ok := cfg["secret_key"].(string); ok {
		g.secretKey = resolve(v)
	}
	if v, ok := cfg["base_url"].(string); ok && v != "" {
		g.baseURL = resolve(v)
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
			g.client.Timeout = d
		}
	}
}

// applyBaseURLEnv replaces the default base URL with FI_BASE_URL when set.
// It reports whether the rule's calls go to the operator's own backend,
// FI_BASE_URL: the default was replaced by it, or the rule's base_url is
// exactly FI_BASE_URL (a trailing slash aside), which yields the same eval
// URL. FI_BASE_URL is operator configuration (New keeps a plain client for
// it); any other base_url is the rule's own choice.
func (g *FutureAGIGuardrail) applyBaseURLEnv() (operatorBackend bool) {
	envURL := os.Getenv("FI_BASE_URL")
	if envURL == "" {
		return false
	}
	if g.baseURL == defaultBaseURL {
		g.baseURL = envURL
		return true
	}
	return strings.TrimRight(g.baseURL, "/") == strings.TrimRight(envURL, "/")
}

// validate reports why an org rule cannot call the evaluation API: no
// api_key, no secret_key or no eval id. It names the fields, never a value.
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
	return fmt.Errorf("futureagi guardrail %q cannot run: missing %s", g.name, strings.Join(missing, ", "))
}

func (g *FutureAGIGuardrail) Name() string            { return g.name }
func (g *FutureAGIGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check evaluates text against the Future AGI evaluation API. A rule that New
// built with a configuration fault refuses before anything else and makes no
// call: a failure without a score, which triggers at any threshold. A failed
// call is reported by its kind only (guardrails.CallFailure): the message
// reaches the caller and the org's request log, and the URL called can be the
// operator's FI_BASE_URL. The error itself goes to the gateway's log.
func (g *FutureAGIGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if g.configErr != nil {
		return &guardrails.CheckResult{Pass: false, Message: g.configErr.Error()}
	}
	if input == nil {
		return &guardrails.CheckResult{Pass: true}
	}
	if g.apiKey == "" || g.secretKey == "" {
		return &guardrails.CheckResult{
			Pass:    true,
			Message: "futureagi guardrail: missing api_key or secret_key, skipping",
		}
	}
	if len(g.evalIDs) == 0 {
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
		slog.Warn("futureagi guardrail call failed", "guardrail", g.name, "error", err)
		return &guardrails.CheckResult{
			Pass:    false,
			Score:   1.0,
			Message: "futureagi evaluation failed: " + guardrails.CallFailure(err),
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

// expandEnv replaces ${VAR_NAME} with the environment variable value. For
// config.yaml rules only (NewConfigFileRule); New refuses a ${...} instead.
func expandEnv(s string) string {
	if strings.HasPrefix(s, "${") && strings.HasSuffix(s, "}") {
		return os.Getenv(s[2 : len(s)-1])
	}
	return s
}
