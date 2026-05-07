package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type grayswanAdapter struct {
	apiKey        string
	endpoint      string
	policyID      string
	policyIDs     []string
	rules         map[string]string
	reasoningMode string
}

// grayswanRequest matches the Cygnal /cygnal/monitor endpoint.
// API ref: https://docs.grayswan.ai/cygnal/monitor-requests
type grayswanRequest struct {
	Messages      []grayswanMessage `json:"messages"`
	Rules         map[string]string `json:"rules,omitempty"`
	PolicyID      string            `json:"policy_id,omitempty"`
	PolicyIDs     []string          `json:"policy_ids,omitempty"`
	ReasoningMode string            `json:"reasoning_mode,omitempty"`
}

type grayswanMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type grayswanResponse struct {
	Violation                float64                   `json:"violation"`
	ViolatedRules            []int                     `json:"violated_rules"`
	Mutation                 bool                      `json:"mutation"`
	IPI                      bool                      `json:"ipi"`
	ViolatedRuleDescriptions []grayswanRuleDescription `json:"violated_rule_descriptions"`
}

type grayswanRuleDescription struct {
	Rule        int    `json:"rule"`
	Name        string `json:"name"`
	Description string `json:"description"`
}

func newGrayswanAdapter(cfg map[string]interface{}) *grayswanAdapter {
	var rules map[string]string
	if raw, ok := cfg["rules"].(map[string]interface{}); ok {
		rules = make(map[string]string, len(raw))
		for k, v := range raw {
			if s, ok := v.(string); ok {
				rules[k] = s
			}
		}
	}

	return &grayswanAdapter{
		apiKey:        getStringConfig(cfg, "api_key", ""),
		endpoint:      getStringConfig(cfg, "endpoint", "https://api.grayswan.ai/cygnal/monitor"),
		policyID:      getStringConfig(cfg, "policy_id", ""),
		policyIDs:     getStringSliceConfig(cfg, "policy_ids"),
		rules:         rules,
		reasoningMode: getStringConfig(cfg, "reasoning_mode", ""),
	}
}

// Cygnal monitor requires both Authorization Bearer AND grayswan-api-key headers.
func (a *grayswanAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	payload := grayswanRequest{
		Messages: []grayswanMessage{
			{Role: "user", Content: text},
		},
	}
	if len(a.rules) > 0 {
		payload.Rules = a.rules
	}
	if a.policyID != "" {
		payload.PolicyID = a.policyID
	}
	if len(a.policyIDs) > 0 {
		payload.PolicyIDs = a.policyIDs
	}
	if a.reasoningMode != "" {
		payload.ReasoningMode = a.reasoningMode
	}

	return makeJSONRequest(ctx, a.endpoint, payload, map[string]string{
		"Authorization":    "Bearer " + a.apiKey,
		"grayswan-api-key": a.apiKey,
	})
}

func (a *grayswanAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp grayswanResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse grayswan response: %v", err)}
	}

	if resp.Violation < 0.5 && len(resp.ViolatedRules) == 0 && !resp.IPI {
		return &guardrails.CheckResult{Pass: true, Score: resp.Violation, Message: "content is safe"}
	}

	var details []string
	if resp.IPI {
		details = append(details, "indirect-prompt-injection")
	}
	if resp.Mutation {
		details = append(details, "text-mutation")
	}
	for _, rd := range resp.ViolatedRuleDescriptions {
		name := rd.Name
		if name == "" {
			name = fmt.Sprintf("rule-%d", rd.Rule)
		}
		details = append(details, name)
	}

	msg := fmt.Sprintf("grayswan violation (score=%.2f)", resp.Violation)
	if len(details) > 0 {
		msg = fmt.Sprintf("grayswan violation (score=%.2f): %s", resp.Violation, strings.Join(details, ", "))
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   resp.Violation,
		Message: msg,
		Details: map[string]interface{}{
			"violation":                  resp.Violation,
			"violated_rules":             resp.ViolatedRules,
			"mutation":                   resp.Mutation,
			"ipi":                        resp.IPI,
			"violated_rule_descriptions": resp.ViolatedRuleDescriptions,
		},
	}
}
