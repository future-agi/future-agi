package futureagi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/evaltarget"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// defaultBaseURL is the evaluation API a rule that names no base_url calls,
// unless the operator set FI_BASE_URL.
const defaultBaseURL = "https://api.futureagi.com"

// defaultTimeout bounds one evaluation call when the rule sets none.
const defaultTimeout = 15 * time.Second

// FutureAGIGuardrail calls Future AGI's evaluation API for ML-based guardrail checks.
type FutureAGIGuardrail struct {
	name            string
	evalIDs         []string
	apiKey          string
	secretKey       string
	baseURL         string
	callType        string
	maxTokens       int
	retry           int
	client          *http.Client
	authMode        string
	serviceSecret   string
	operatorBackend bool
	// configErr is why the rule cannot call the evaluation API (a missing
	// key or eval id), or nil. Check refuses every request while it is set.
	configErr error
}

// NewConfigFileRule creates a FutureAGIGuardrail from a rule in the gateway's
// own config file, the one constructor that reads the environment. A
// config-file rule is operator-trusted, so a ${VAR} api_key, secret_key or
// base_url is expanded from the gateway's environment, the convenience
// config.yaml rules have always had, and it keeps a plain client (a
// config.yaml rule may name the operator's own evaluation API on a private or
// loopback address without listing it), as it always has. A rule that lacks a
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
	if v, present := cfg["auth"]; present {
		var ok bool
		g.authMode, ok = v.(string)
		if !ok {
			g.authMode = "invalid"
		}
	}
	if g.authMode != "gateway_service" {
		if v, ok := cfg["api_key"].(string); ok {
			g.apiKey = expand(v)
		}
		if v, ok := cfg["secret_key"].(string); ok {
			g.secretKey = expand(v)
		}
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

	// A rule names its own credentials: a process-wide FI_API_KEY or
	// FI_SECRET_KEY would evaluate every org's traffic under one account.
	// The evaluation API's address is the operator's to set: FI_BASE_URL
	// stands in for the built-in default.
	if g.authMode == "gateway_service" {
		base, ok := cfg["base_url"].(string)
		g.baseURL, g.configErr = evaltarget.ServiceBase(expand(base), os.Getenv("FI_BASE_URL"))
		if _, present := cfg["base_url"]; present && !ok {
			g.configErr = fmt.Errorf("gateway_service base_url must be a string matching FI_BASE_URL")
		}
		g.operatorBackend = g.configErr == nil
		// Never resolve a rule-controlled secret or use a process-wide FI pair.
		g.serviceSecret = os.Getenv("AGENTCC_ADMIN_TOKEN")
	} else if envURL := os.Getenv("FI_BASE_URL"); envURL != "" {
		// Preserve the legacy pair mode's fallback and exact-match behavior.
		if g.baseURL == defaultBaseURL {
			g.baseURL = envURL
			g.operatorBackend = true
		} else if strings.TrimRight(g.baseURL, "/") == strings.TrimRight(envURL, "/") {
			g.operatorBackend = true
		}
	}
	g.client = mkClient(timeout, g.operatorBackend)
	if g.authMode == "gateway_service" {
		// net/http otherwise forwards custom auth headers on redirects, even
		// to a different origin. No redirect is part of the signed eval route.
		g.client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	}

	if g.configErr == nil {
		g.configErr = g.validate()
	}
	return g
}

// ValidateConfig reports a missing pair in pair mode, a missing deployment
// signer or non-operator destination in gateway_service mode, or no eval id.
// Check refuses every request on a rule this rejects. The error names the
// rule and configuration fields, never a credential.
func ValidateConfig(name string, cfg map[string]any) error {
	return NewConfigFileRule(name, cfg).configErr
}

// validate is ValidateConfig over the parsed rule.
func (g *FutureAGIGuardrail) validate() error {
	var missing []string
	switch g.authMode {
	case "gateway_service":
		if !g.operatorBackend {
			return fmt.Errorf("futureagi guardrail %q requires the operator FI_BASE_URL for gateway_service", g.name)
		}
		if len(g.serviceSecret) < 32 {
			return fmt.Errorf("futureagi guardrail %q requires AGENTCC_ADMIN_TOKEN of at least 32 characters for gateway_service", g.name)
		}
	case "", "pair":
		if g.apiKey == "" {
			missing = append(missing, "api_key")
		}
		if g.secretKey == "" {
			missing = append(missing, "secret_key")
		}
	default:
		return fmt.Errorf("futureagi guardrail %q has unsupported auth (use pair or gateway_service)", g.name)
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
	var principal evalPrincipal
	if g.authMode == "gateway_service" {
		var err error
		principal, err = requestPrincipal(ctx, input)
		if err != nil {
			return &guardrails.CheckResult{Pass: false, Message: err.Error()}
		}
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

	resp, err := g.callAPI(ctx, text, principal)
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
