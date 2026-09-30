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
	projectID  string
	categories []string
}

// lakeraRequest matches the Guard API v2 /v2/guard endpoint.
// API ref: https://docs.lakera.ai/api-reference/lakera-api/guard/screen-content
type lakeraRequest struct {
	Messages  []lakeraMessage `json:"messages"`
	ProjectID string          `json:"project_id,omitempty"`
	Breakdown bool            `json:"breakdown"`
}

type lakeraMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type lakeraResponse struct {
	Flagged   *bool            `json:"flagged"`
	Breakdown []lakeraDetector `json:"breakdown"`
}

// lakeraDetector is one breakdown entry: a detector the policy ran and its outcome.
type lakeraDetector struct {
	DetectorType string `json:"detector_type"`
	Detected     bool   `json:"detected"`
	Result       string `json:"result"` // confidence level: l1_confident..l5_unlikely or no_level
}

// lakeraLegacyCategories maps the pre-v2 category names that saved dashboard
// configs still carry onto the v2 detector types that replaced them.
var lakeraLegacyCategories = map[string]string{
	"prompt_injection": "prompt_attack",
	"jailbreak":        "prompt_attack",
	"harmful_content":  "moderated_content",
}

func newLakeraAdapter(cfg map[string]interface{}) *lakeraAdapter {
	var categories []string
	for _, c := range getStringSliceConfig(cfg, "categories") {
		// Detector types are lowercase paths; a category that can never match
		// would quietly let every flag through.
		c = strings.TrimSuffix(strings.ToLower(strings.TrimSpace(c)), "/")
		if c == "" {
			continue
		}
		if v2, ok := lakeraLegacyCategories[c]; ok {
			c = v2
		}
		categories = append(categories, c)
	}
	return &lakeraAdapter{
		apiKey:     getStringConfig(cfg, "api_key", ""),
		endpoint:   getStringConfig(cfg, "endpoint", "https://api.lakera.ai/v2/guard"),
		projectID:  getStringConfig(cfg, "project_id", ""),
		categories: categories,
	}
}

func (a *lakeraAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	payload := lakeraRequest{
		Messages:  []lakeraMessage{{Role: "user", Content: text}},
		ProjectID: a.projectID,
		// Requested even without categories: the breakdown is the only place v2
		// names the detectors behind a flag, so it also feeds the result details.
		Breakdown: true,
	}
	return makeJSONRequest(ctx, a.endpoint, payload, map[string]string{
		"Authorization": "Bearer " + a.apiKey,
	})
}

// parseResponse triggers only on Lakera's flagged verdict, which already applies
// the policy's sensitivity level (L1-L4) and is forced to false in Detect mode.
// Score mapping: flagged scores 1.0, so it trips any confidence threshold below
// 1.0 (the dashboard default is 0.8); not flagged scores 0. Configured
// categories can only narrow a flagged verdict to matching detections.
func (a *lakeraAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp lakeraResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse lakera response: %v", err)}
	}
	// Guard always returns flagged, so a body without it is not a verdict (e.g.
	// an endpoint override pointing at another API): fail closed as above.
	if resp.Flagged == nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: "lakera response missing flagged"}
	}

	if !*resp.Flagged {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	var triggered []string
	results := make(map[string]string)
	for _, d := range resp.Breakdown {
		if !d.Detected || !a.matchesCategory(d.DetectorType) {
			continue
		}
		if _, seen := results[d.DetectorType]; !seen {
			triggered = append(triggered, d.DetectorType)
			results[d.DetectorType] = d.Result
		}
	}

	if len(a.categories) > 0 && len(triggered) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "flagged but no configured categories matched"}
	}

	msg := "lakera flagged"
	if len(triggered) > 0 {
		msg = fmt.Sprintf("lakera flagged: %s", strings.Join(triggered, ", "))
	}
	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: msg,
		Details: map[string]interface{}{
			"detector_types": triggered,
			"results":        results,
		},
	}
}

// matchesCategory reports whether a detector type is selected by the configured
// categories, where a category also covers its sub-types ("moderated_content"
// matches "moderated_content/hate"). No categories selects every detector.
func (a *lakeraAdapter) matchesCategory(detectorType string) bool {
	if len(a.categories) == 0 {
		return true
	}
	for _, c := range a.categories {
		if detectorType == c || strings.HasPrefix(detectorType, c+"/") {
			return true
		}
	}
	return false
}
