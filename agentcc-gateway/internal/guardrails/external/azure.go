package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type azureAdapter struct {
	endpoint          string
	apiKey            string
	categories        []string
	severityThreshold int
}

type azureRequest struct {
	Text       string   `json:"text"`
	Categories []string `json:"categories,omitempty"`
}

type azureResponse struct {
	CategoriesAnalysis []azureCategory `json:"categoriesAnalysis"`
}

type azureCategory struct {
	Category string `json:"category"`
	Severity int    `json:"severity"`
}

func newAzureAdapter(cfg map[string]interface{}) *azureAdapter {
	return &azureAdapter{
		endpoint:          getStringConfig(cfg, "endpoint", ""),
		apiKey:            getStringConfig(cfg, "api_key", ""),
		categories:        getStringSliceConfig(cfg, "categories"),
		severityThreshold: getIntConfig(cfg, "severity_threshold", 2),
	}
}

func (a *azureAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	url := strings.TrimRight(a.endpoint, "/") + "/contentsafety/text:analyze?api-version=2024-09-01"
	payload := azureRequest{Text: text}
	if len(a.categories) > 0 {
		payload.Categories = a.categories
	}
	return makeJSONRequest(ctx, url, payload, map[string]string{
		"Ocp-Apim-Subscription-Key": a.apiKey,
	})
}

func (a *azureAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp azureResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse azure response: %v", err)}
	}

	var triggered []string
	maxSeverity := 0

	for _, cat := range resp.CategoriesAnalysis {
		if cat.Severity >= a.severityThreshold {
			triggered = append(triggered, fmt.Sprintf("%s(severity=%d)", cat.Category, cat.Severity))
			if cat.Severity > maxSeverity {
				maxSeverity = cat.Severity
			}
		}
	}

	if len(triggered) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	// Normalize severity to 0.0-1.0 (max severity is 6).
	score := float64(maxSeverity) / 6.0
	if score > 1.0 {
		score = 1.0
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   score,
		Message: fmt.Sprintf("azure content safety: %s", strings.Join(triggered, ", ")),
		Details: map[string]interface{}{
			"categories_analysis": resp.CategoriesAnalysis,
			"max_severity":        maxSeverity,
		},
	}
}
