package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type hiddenlayerAdapter struct {
	apiKey   string
	endpoint string
	modelID  string
}

type hiddenlayerRequest struct {
	Text    string `json:"text"`
	ModelID string `json:"model_id,omitempty"`
}

type hiddenlayerResponse struct {
	Flagged    bool                `json:"flagged"`
	RiskScore  float64             `json:"risk_score"`
	Detections []hiddenlayerDetect `json:"detections"`
}

type hiddenlayerDetect struct {
	Type  string  `json:"type"`
	Score float64 `json:"score"`
}

func newHiddenlayerAdapter(cfg map[string]interface{}) *hiddenlayerAdapter {
	return &hiddenlayerAdapter{
		apiKey:   getStringConfig(cfg, "api_key", ""),
		endpoint: getStringConfig(cfg, "endpoint", "https://api.hiddenlayer.ai/api/v2/submit/text"),
		modelID:  getStringConfig(cfg, "model_id", ""),
	}
}

func (a *hiddenlayerAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	payload := hiddenlayerRequest{Text: text}
	if a.modelID != "" {
		payload.ModelID = a.modelID
	}
	return makeJSONRequest(ctx, a.endpoint, payload, map[string]string{
		"Authorization": "Bearer " + a.apiKey,
	})
}

func (a *hiddenlayerAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp hiddenlayerResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse hiddenlayer response: %v", err)}
	}

	if !resp.Flagged {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	var types []string
	for _, d := range resp.Detections {
		types = append(types, d.Type)
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   resp.RiskScore,
		Message: fmt.Sprintf("hiddenlayer flagged: %s", strings.Join(types, ", ")),
		Details: map[string]interface{}{
			"risk_score": resp.RiskScore,
			"detections": resp.Detections,
		},
	}
}
