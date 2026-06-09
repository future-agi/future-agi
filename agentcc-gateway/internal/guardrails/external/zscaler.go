package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type zscalerAdapter struct {
	apiKey      string
	cloud       string
	tenantID    string
	dlpProfiles []string
}

type zscalerRequest struct {
	Content  string   `json:"content"`
	Profiles []string `json:"profiles,omitempty"`
}

type zscalerResponse struct {
	Matched    bool                `json:"matched"`
	Violations []zscalerViolation `json:"violations"`
}

type zscalerViolation struct {
	Profile  string `json:"profile"`
	Severity string `json:"severity"`
	Details  string `json:"details"`
}

func newZscalerAdapter(cfg map[string]interface{}) *zscalerAdapter {
	return &zscalerAdapter{
		apiKey:      getStringConfig(cfg, "api_key", ""),
		cloud:       getStringConfig(cfg, "cloud", "zscaler.net"),
		tenantID:    getStringConfig(cfg, "tenant_id", ""),
		dlpProfiles: getStringSliceConfig(cfg, "dlp_profiles"),
	}
}

func (a *zscalerAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	url := fmt.Sprintf("https://api.%s/v1/dlp/scan", a.cloud)
	payload := zscalerRequest{Content: text}
	if len(a.dlpProfiles) > 0 {
		payload.Profiles = a.dlpProfiles
	}
	return makeJSONRequest(ctx, url, payload, map[string]string{
		"Authorization": "Bearer " + a.apiKey,
		"X-Tenant-ID":   a.tenantID,
	})
}

func (a *zscalerAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp zscalerResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse zscaler response: %v", err)}
	}

	if !resp.Matched {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	var profiles []string
	for _, v := range resp.Violations {
		profiles = append(profiles, v.Profile)
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: fmt.Sprintf("zscaler dlp matched: %s", strings.Join(profiles, ", ")),
		Details: map[string]interface{}{
			"violations": resp.Violations,
		},
	}
}
