package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type aporiaAdapter struct {
	apiKey    string
	endpoint  string
	projectID string
	policies  []string
}

type aporiaRequest struct {
	Messages         []aporiaMessage `json:"messages"`
	ValidationTarget string          `json:"validation_target"`
}

type aporiaMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type aporiaResponse struct {
	Action   string          `json:"action"`
	Explain  []aporiaExplain `json:"explain_log"`
}

type aporiaExplain struct {
	PolicyID string `json:"policy_id"`
	Target   string `json:"target"`
	Result   string `json:"result"`
}

func newAporiaAdapter(cfg map[string]interface{}) *aporiaAdapter {
	return &aporiaAdapter{
		apiKey:    getStringConfig(cfg, "api_key", ""),
		endpoint:  getStringConfig(cfg, "endpoint", "https://gr-prd.aporia.com"),
		projectID: getStringConfig(cfg, "project_id", ""),
		policies:  getStringSliceConfig(cfg, "policies"),
	}
}

func (a *aporiaAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	url := fmt.Sprintf("%s/%s/validate", strings.TrimRight(a.endpoint, "/"), a.projectID)
	payload := aporiaRequest{
		Messages: []aporiaMessage{
			{Role: "user", Content: text},
		},
		ValidationTarget: "prompt",
	}
	return makeJSONRequest(ctx, url, payload, map[string]string{
		"X-Aporia-Api-Key": a.apiKey,
	})
}

func (a *aporiaAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp aporiaResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse aporia response: %v", err)}
	}

	if resp.Action == "passthrough" || resp.Action == "" {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	var violations []string
	for _, e := range resp.Explain {
		if e.Result != "pass" && e.Result != "" {
			violations = append(violations, e.PolicyID)
		}
	}

	msg := fmt.Sprintf("aporia %s", resp.Action)
	if len(violations) > 0 {
		msg = fmt.Sprintf("aporia %s: policies %s", resp.Action, strings.Join(violations, ", "))
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: msg,
		Details: map[string]interface{}{
			"action":     resp.Action,
			"explain_log": resp.Explain,
		},
	}
}
