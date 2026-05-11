package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type dynamoaiAdapter struct {
	apiKey    string
	endpoint  string
	policyIDs []string
	modelID   string
	textType  string // "MODEL_INPUT" or "MODEL_RESPONSE"
}

// dynamoaiRequest matches the DynamoGuard /moderation/analyze endpoint.
// See: https://docs.dynamo.ai/api/3.24.3/ (ModerationController_analyzeText)
type dynamoaiRequest struct {
	Messages  []dynamoaiMessage `json:"messages"`
	TextType  string            `json:"textType"`
	PolicyIDs []string          `json:"policyIds"`
	ModelID   string            `json:"modelId,omitempty"`
}

type dynamoaiMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type dynamoaiResponse struct {
	Text            string                  `json:"text"`
	TextType        string                  `json:"textType"`
	FinalAction     string                  `json:"finalAction"` // "BLOCK", "WARN", "REDACT", "SANITIZE", "NONE"
	AppliedPolicies []dynamoaiAppliedPolicy `json:"appliedPolicies"`
	Error           string                  `json:"error"`
}

type dynamoaiAppliedPolicy struct {
	Policy dynamoaiPolicy         `json:"policy"`
	Output map[string]interface{} `json:"outputs"`
	Action string                 `json:"action"`
}

type dynamoaiPolicy struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description"`
	Method      string `json:"method"`
	Action      string `json:"action"`
}

func newDynamoaiAdapter(cfg map[string]interface{}) *dynamoaiAdapter {
	return &dynamoaiAdapter{
		apiKey:    getStringConfig(cfg, "api_key", ""),
		endpoint:  getStringConfig(cfg, "endpoint", "https://api.dynamofl.com/moderation/analyze"),
		policyIDs: getStringSliceConfig(cfg, "policy_ids"),
		modelID:   getStringConfig(cfg, "model_id", ""),
		textType:  getStringConfig(cfg, "text_type", "MODEL_INPUT"),
	}
}

func (a *dynamoaiAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	textType := a.textType
	if textType == "" {
		textType = "MODEL_INPUT"
	}

	payload := dynamoaiRequest{
		Messages: []dynamoaiMessage{
			{Role: "user", Content: text},
		},
		TextType:  textType,
		PolicyIDs: a.policyIDs,
	}
	if a.modelID != "" {
		payload.ModelID = a.modelID
	}
	return makeJSONRequest(ctx, a.endpoint, payload, map[string]string{
		"Authorization": "Bearer " + a.apiKey,
	})
}

func (a *dynamoaiAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp dynamoaiResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse dynamoai response: %v", err)}
	}

	if resp.Error != "" {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("dynamoai error: %s", resp.Error)}
	}

	if resp.FinalAction == "" || resp.FinalAction == "NONE" {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	var triggered []string
	for _, ap := range resp.AppliedPolicies {
		if ap.Action != "" && ap.Action != "NONE" {
			name := ap.Policy.Name
			if name == "" {
				name = ap.Policy.Method
			}
			triggered = append(triggered, fmt.Sprintf("%s(%s)", name, ap.Action))
		}
	}

	msg := fmt.Sprintf("dynamoai: action=%s", resp.FinalAction)
	if len(triggered) > 0 {
		msg = fmt.Sprintf("dynamoai: action=%s, policies=[%s]", resp.FinalAction, strings.Join(triggered, ", "))
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: msg,
		Details: map[string]interface{}{
			"final_action":     resp.FinalAction,
			"applied_policies": resp.AppliedPolicies,
		},
	}
}
