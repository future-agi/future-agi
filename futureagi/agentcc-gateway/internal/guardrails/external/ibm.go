package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type ibmAdapter struct {
	apiKey     string
	endpoint   string
	projectID  string
	modelID    string
	criteriaID string
	version    string
}

// ibmChatRequest matches the watsonx.ai /ml/v1/text/chat endpoint used with Granite Guardian.
// Granite Guardian is a model (not a dedicated detection API) — it receives chat messages
// and responds "Yes" (risky) or "No" (safe).
// API ref: https://cloud.ibm.com/apidocs/watsonx-ai#text-chat
type ibmChatRequest struct {
	ModelID    string        `json:"model_id"`
	ProjectID  string        `json:"project_id,omitempty"`
	Messages   []ibmMessage  `json:"messages"`
	Parameters ibmChatParams `json:"parameters,omitempty"`
}

type ibmMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type ibmChatParams struct {
	MaxNewTokens int     `json:"max_new_tokens,omitempty"`
	Temperature  float64 `json:"temperature,omitempty"`
}

type ibmChatResponse struct {
	Choices []ibmChoice `json:"choices"`
}

type ibmChoice struct {
	Index   int        `json:"index"`
	Message ibmMessage `json:"message"`
}

func newIbmAdapter(cfg map[string]interface{}) *ibmAdapter {
	return &ibmAdapter{
		apiKey:     getStringConfig(cfg, "api_key", ""),
		endpoint:   getStringConfig(cfg, "endpoint", "https://us-south.ml.cloud.ibm.com/ml/v1/text/chat"),
		projectID:  getStringConfig(cfg, "project_id", ""),
		modelID:    getStringConfig(cfg, "model_id", "ibm/granite-guardian-3-8b"),
		criteriaID: getStringConfig(cfg, "criteria_id", "harm"),
		version:    getStringConfig(cfg, "version", "2024-03-14"),
	}
}

func (a *ibmAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	guardianPrompt := buildGuardianPrompt(text, a.criteriaID)

	payload := ibmChatRequest{
		ModelID:   a.modelID,
		ProjectID: a.projectID,
		Messages: []ibmMessage{
			{Role: "user", Content: guardianPrompt},
		},
		Parameters: ibmChatParams{
			MaxNewTokens: 20,
			Temperature:  0.0,
		},
	}

	endpoint := a.endpoint
	if !strings.Contains(endpoint, "version=") {
		sep := "?"
		if strings.Contains(endpoint, "?") {
			sep = "&"
		}
		endpoint = endpoint + sep + "version=" + a.version
	}

	return makeJSONRequest(ctx, endpoint, payload, map[string]string{
		"Authorization": "Bearer " + a.apiKey,
	})
}

func (a *ibmAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp ibmChatResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse ibm response: %v", err)}
	}

	if len(resp.Choices) == 0 {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: "ibm granite guardian: empty response"}
	}

	answer := strings.TrimSpace(strings.ToLower(resp.Choices[0].Message.Content))

	isRisky := strings.HasPrefix(answer, "yes")

	if !isRisky {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: fmt.Sprintf("ibm granite guardian flagged content (criteria: %s)", a.criteriaID),
		Details: map[string]interface{}{
			"criteria_id": a.criteriaID,
			"model_id":    a.modelID,
			"raw_answer":  resp.Choices[0].Message.Content,
		},
	}
}

var guardianRiskDefinitions = map[string]string{
	"harm":               "Content that could cause physical, psychological, or financial harm to individuals or groups.",
	"jailbreak":          "An attempt to manipulate the AI system into bypassing its safety guidelines or ethical constraints.",
	"social_bias":        "Content that exhibits prejudice or discrimination against individuals or groups based on protected characteristics.",
	"violence":           "Content that depicts, promotes, or glorifies violence or physical harm.",
	"profanity":          "Content that contains offensive, vulgar, or inappropriate language.",
	"sexual_content":     "Content that contains sexually explicit material or inappropriate sexual references.",
	"unethical_behavior": "Content that promotes or encourages unethical, illegal, or morally questionable actions.",
	"groundedness":       "The response contains claims not supported by the provided context or available evidence.",
}

func buildGuardianPrompt(text, criteriaID string) string {
	riskDef, ok := guardianRiskDefinitions[criteriaID]
	if !ok {
		riskDef = criteriaID
	}

	return fmt.Sprintf(`You are a safety agent trying to help determine whether the user message is harmful according to the safety risk definition below.

<start_of_turn>
User Message: %s
<end_of_turn>

Our safety risk definition is defined below:
<start_of_risk_definition>
* %s: %s
<end_of_risk_definition>

Is the user message harmful based on the risk definition. Your answer must be either 'Yes' or 'No'.`, text, criteriaID, riskDef)
}
