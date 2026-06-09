package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type bedrockAdapter struct {
	endpoint         string
	guardrailID      string
	guardrailVersion string
	accessKey        string
	secretKey        string
	region           string
}

type bedrockRequest struct {
	Source  string           `json:"source"`
	Content []bedrockContent `json:"content"`
}

type bedrockContent struct {
	Text bedrockText `json:"text"`
}

type bedrockText struct {
	Text string `json:"text"`
}

type bedrockResponse struct {
	Action  string `json:"action"`
	Outputs []struct {
		Text string `json:"text"`
	} `json:"outputs"`
}

func newBedrockAdapter(cfg map[string]interface{}) *bedrockAdapter {
	region := getStringConfig(cfg, "region", "us-east-1")
	endpoint := getStringConfig(cfg, "endpoint", "")
	if endpoint == "" {
		endpoint = fmt.Sprintf("https://bedrock-runtime.%s.amazonaws.com", region)
	}
	return &bedrockAdapter{
		endpoint:         endpoint,
		guardrailID:      getStringConfig(cfg, "guardrail_id", ""),
		guardrailVersion: getStringConfig(cfg, "guardrail_version", "DRAFT"),
		accessKey:        getStringConfig(cfg, "access_key", ""),
		secretKey:        getStringConfig(cfg, "secret_key", ""),
		region:           region,
	}
}

func (a *bedrockAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	url := fmt.Sprintf("%s/guardrail/%s/version/%s/apply",
		strings.TrimRight(a.endpoint, "/"), a.guardrailID, a.guardrailVersion)

	payload := bedrockRequest{
		Source: "INPUT",
		Content: []bedrockContent{
			{Text: bedrockText{Text: text}},
		},
	}

	// Note: Full AWS SigV4 signing is out of scope. For production use,
	// configure a Bedrock endpoint proxy that handles signing, or use
	// pre-signed credentials. Here we send access key as a simple header
	// for environments where an IAM proxy is fronting Bedrock.
	headers := map[string]string{}
	if a.accessKey != "" {
		headers["X-Aws-Access-Key"] = a.accessKey
	}
	if a.secretKey != "" {
		headers["X-Aws-Secret-Key"] = a.secretKey
	}

	return makeJSONRequest(ctx, url, payload, headers)
}

func (a *bedrockAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp bedrockResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse bedrock response: %v", err)}
	}

	if resp.Action == "NONE" {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	if resp.Action == "GUARDRAIL_INTERVENED" {
		msg := "content blocked by bedrock guardrail"
		if len(resp.Outputs) > 0 && resp.Outputs[0].Text != "" {
			msg = resp.Outputs[0].Text
		}
		return &guardrails.CheckResult{
			Pass:    false,
			Score:   1.0,
			Message: msg,
			Details: map[string]interface{}{
				"action":       resp.Action,
				"guardrail_id": a.guardrailID,
			},
		}
	}

	// Unknown action — treat as pass.
	return &guardrails.CheckResult{
		Pass:    true,
		Score:   0.0,
		Message: fmt.Sprintf("unknown bedrock action: %s", resp.Action),
	}
}
