package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type enkryptAdapter struct {
	apiKey    string
	endpoint  string
	detectors map[string]interface{}
}

// enkryptRequest matches the Enkrypt AI /guardrails/detect endpoint.
// API ref: https://docs.enkryptai.com/api-reference/guardrails-api-reference/endpoint/detect
type enkryptRequest struct {
	Text      string                 `json:"text"`
	Detectors map[string]interface{} `json:"detectors"`
}

type enkryptResponse struct {
	Summary       map[string]interface{} `json:"summary"`
	Details       map[string]interface{} `json:"details"`
	ResultMessage *string                `json:"result_message"`
}

func newEnkryptAdapter(cfg map[string]interface{}) *enkryptAdapter {
	detectors := buildEnkryptDetectors(cfg)
	return &enkryptAdapter{
		apiKey:    getStringConfig(cfg, "api_key", ""),
		endpoint:  getStringConfig(cfg, "endpoint", "https://api.enkryptai.com/guardrails/detect"),
		detectors: detectors,
	}
}

func buildEnkryptDetectors(cfg map[string]interface{}) map[string]interface{} {
	if raw, ok := cfg["detectors"]; ok {
		if m, ok := raw.(map[string]interface{}); ok {
			return m
		}
	}

	checks := getStringSliceConfig(cfg, "checks")
	if len(checks) == 0 {
		return map[string]interface{}{
			"toxicity":         map[string]interface{}{"enabled": true},
			"injection_attack": map[string]interface{}{"enabled": true},
		}
	}

	detectors := make(map[string]interface{}, len(checks))
	for _, check := range checks {
		detectors[check] = map[string]interface{}{"enabled": true}
	}
	return detectors
}

// Auth uses "apikey" header per Enkrypt AI OpenAPI spec (securitySchemes.apiKeyAuth).
func (a *enkryptAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	payload := enkryptRequest{
		Text:      text,
		Detectors: a.detectors,
	}
	return makeJSONRequest(ctx, a.endpoint, payload, map[string]string{
		"apikey": a.apiKey,
	})
}

func (a *enkryptAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp enkryptResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse enkrypt response: %v", err)}
	}

	var flagged []string
	maxScore := 0.0

	for detector, val := range resp.Summary {
		switch v := val.(type) {
		case float64:
			if v > 0 {
				flagged = append(flagged, detector)
				if v > maxScore {
					maxScore = v
				}
			}
		case []interface{}:
			if len(v) > 0 {
				flagged = append(flagged, detector)
				if maxScore < 1.0 {
					maxScore = 1.0
				}
			}
		case bool:
			if v {
				flagged = append(flagged, detector)
				if maxScore < 1.0 {
					maxScore = 1.0
				}
			}
		}
	}

	if len(flagged) > 0 {
		extractDetailScores(resp.Details, &maxScore)
	}

	if len(flagged) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	msg := fmt.Sprintf("enkrypt flagged: %s", strings.Join(flagged, ", "))
	if resp.ResultMessage != nil && *resp.ResultMessage != "" {
		msg = *resp.ResultMessage
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   maxScore,
		Message: msg,
		Details: map[string]interface{}{
			"summary": resp.Summary,
			"details": resp.Details,
		},
	}
}

func extractDetailScores(details map[string]interface{}, maxScore *float64) {
	for _, detectorDetail := range details {
		detailMap, ok := detectorDetail.(map[string]interface{})
		if !ok {
			continue
		}
		for key, val := range detailMap {
			if key == "compliance_mapping" || key == "detected_keywords" || key == "detected_counts" || key == "redacted_text" {
				continue
			}
			if score, ok := val.(float64); ok && score > *maxScore {
				*maxScore = score
			}
		}
	}
}
