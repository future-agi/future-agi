package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type lakeraAdapter struct {
	apiKey     string
	endpoint   string
	categories []string
}

type lakeraRequest struct {
	Input string `json:"input"`
}

type lakeraResponse struct {
	Flagged        bool               `json:"flagged"`
	Categories     map[string]bool    `json:"categories"`
	CategoryScores map[string]float64 `json:"category_scores"`
}

func newLakeraAdapter(cfg map[string]interface{}) *lakeraAdapter {
	return &lakeraAdapter{
		apiKey:     getStringConfig(cfg, "api_key", ""),
		endpoint:   getStringConfig(cfg, "endpoint", "https://api.lakera.ai/v2/guard"),
		categories: getStringSliceConfig(cfg, "categories"),
	}
}

func (a *lakeraAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	return makeJSONRequest(ctx, a.endpoint, lakeraRequest{Input: text}, map[string]string{
		"Authorization": "Bearer " + a.apiKey,
	})
}

func (a *lakeraAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp lakeraResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse lakera response: %v", err)}
	}

	if !resp.Flagged {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	// Check configured categories. If none configured, any flagged category triggers.
	var triggered []string
	maxScore := 0.0

	if len(a.categories) == 0 {
		// All categories.
		for cat, flagged := range resp.Categories {
			if flagged {
				triggered = append(triggered, cat)
				if score := resp.CategoryScores[cat]; score > maxScore {
					maxScore = score
				}
			}
		}
	} else {
		for _, cat := range a.categories {
			if resp.Categories[cat] {
				triggered = append(triggered, cat)
				if score := resp.CategoryScores[cat]; score > maxScore {
					maxScore = score
				}
			}
		}
	}

	if len(triggered) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "flagged but no configured categories matched"}
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   maxScore,
		Message: fmt.Sprintf("lakera flagged: %s", strings.Join(triggered, ", ")),
		Details: map[string]interface{}{
			"categories":      triggered,
			"category_scores": resp.CategoryScores,
		},
	}
}
