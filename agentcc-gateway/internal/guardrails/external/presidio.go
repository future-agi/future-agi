package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type presidioAdapter struct {
	endpoint       string
	apiKey         string
	language       string
	entities       []string
	scoreThreshold float64
}

type presidioRequest struct {
	Text     string   `json:"text"`
	Language string   `json:"language"`
	Entities []string `json:"entities,omitempty"`
}

type presidioEntity struct {
	EntityType string  `json:"entity_type"`
	Score      float64 `json:"score"`
	Start      int     `json:"start"`
	End        int     `json:"end"`
}

func newPresidioAdapter(cfg map[string]interface{}) *presidioAdapter {
	return &presidioAdapter{
		endpoint:       getStringConfig(cfg, "endpoint", "http://localhost:5001"),
		apiKey:         getStringConfig(cfg, "api_key", ""),
		language:       getStringConfig(cfg, "language", "en"),
		entities:       getStringSliceConfig(cfg, "entities"),
		scoreThreshold: getFloatConfig(cfg, "score_threshold", 0.5),
	}
}

func (a *presidioAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	url := strings.TrimRight(a.endpoint, "/") + "/analyze"
	payload := presidioRequest{
		Text:     text,
		Language: a.language,
	}
	if len(a.entities) > 0 {
		payload.Entities = a.entities
	}
	headers := map[string]string{}
	if a.apiKey != "" {
		headers["Authorization"] = "Bearer " + a.apiKey
	}
	return makeJSONRequest(ctx, url, payload, headers)
}

func (a *presidioAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var entities []presidioEntity
	if err := json.Unmarshal(body, &entities); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse presidio response: %v", err)}
	}

	var detected []string
	maxScore := 0.0

	for _, ent := range entities {
		if ent.Score >= a.scoreThreshold {
			detected = append(detected, ent.EntityType)
			if ent.Score > maxScore {
				maxScore = ent.Score
			}
		}
	}

	if len(detected) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "no PII detected"}
	}

	// Deduplicate entity types.
	seen := make(map[string]bool)
	var unique []string
	for _, d := range detected {
		if !seen[d] {
			seen[d] = true
			unique = append(unique, d)
		}
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   maxScore,
		Message: fmt.Sprintf("PII detected: %s", strings.Join(unique, ", ")),
		Details: map[string]interface{}{
			"entities":       entities,
			"detected_types": unique,
		},
	}
}
