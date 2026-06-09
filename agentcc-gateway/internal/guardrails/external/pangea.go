package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type pangeaAdapter struct {
	token    string
	domain   string
	services []string
}

type pangeaRequest struct {
	Text   string `json:"text"`
	Recipe string `json:"recipe,omitempty"`
}

type pangeaResponse struct {
	Result pangeaResult `json:"result"`
}

type pangeaResult struct {
	Detected bool            `json:"detected"`
	Findings []pangeaFinding `json:"findings"`
}

type pangeaFinding struct {
	Type    string  `json:"type"`
	Score   float64 `json:"score"`
	Details string  `json:"details"`
}

func newPangeaAdapter(cfg map[string]interface{}) *pangeaAdapter {
	return &pangeaAdapter{
		token:    getStringConfig(cfg, "token", ""),
		domain:   getStringConfig(cfg, "domain", "aws.us.pangea.cloud"),
		services: getStringSliceConfig(cfg, "services"),
	}
}

func (a *pangeaAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	url := fmt.Sprintf("https://ai-guard.%s/v1beta/text/guard", a.domain)
	payload := pangeaRequest{Text: text, Recipe: "pangea_prompt_guard"}
	return makeJSONRequest(ctx, url, payload, map[string]string{
		"Authorization": "Bearer " + a.token,
	})
}

func (a *pangeaAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp pangeaResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse pangea response: %v", err)}
	}

	if !resp.Result.Detected {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	var types []string
	maxScore := 0.0
	for _, f := range resp.Result.Findings {
		types = append(types, f.Type)
		if f.Score > maxScore {
			maxScore = f.Score
		}
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   maxScore,
		Message: fmt.Sprintf("pangea detected: %s", strings.Join(types, ", ")),
		Details: map[string]interface{}{
			"findings": resp.Result.Findings,
		},
	}
}
