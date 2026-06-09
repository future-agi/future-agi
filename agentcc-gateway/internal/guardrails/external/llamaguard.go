package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type llamaGuardAdapter struct {
	endpoint string
	apiKey   string
	model    string
}

type llamaGuardRequest struct {
	Model    string              `json:"model"`
	Messages []llamaGuardMessage `json:"messages"`
}

type llamaGuardMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type llamaGuardResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
}

func newLlamaGuardAdapter(cfg map[string]interface{}) *llamaGuardAdapter {
	return &llamaGuardAdapter{
		endpoint: getStringConfig(cfg, "endpoint", ""),
		apiKey:   getStringConfig(cfg, "api_key", ""),
		model:    getStringConfig(cfg, "model", "meta-llama/Llama-Guard-3-8B"),
	}
}

func (a *llamaGuardAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	url := strings.TrimRight(a.endpoint, "/") + "/v1/chat/completions"
	payload := llamaGuardRequest{
		Model: a.model,
		Messages: []llamaGuardMessage{
			{Role: "user", Content: text},
		},
	}
	headers := map[string]string{}
	if a.apiKey != "" {
		headers["Authorization"] = "Bearer " + a.apiKey
	}
	return makeJSONRequest(ctx, url, payload, headers)
}

func (a *llamaGuardAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp llamaGuardResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse llama guard response: %v", err)}
	}

	if len(resp.Choices) == 0 {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "no classification output"}
	}

	output := strings.TrimSpace(resp.Choices[0].Message.Content)
	lower := strings.ToLower(output)

	if strings.HasPrefix(lower, "safe") {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: output}
	}

	if strings.HasPrefix(lower, "unsafe") {
		return &guardrails.CheckResult{
			Pass:    false,
			Score:   1.0,
			Message: output,
			Details: map[string]interface{}{
				"classification": output,
			},
		}
	}

	// Unknown classification — treat as safe with warning.
	return &guardrails.CheckResult{
		Pass:    true,
		Score:   0.0,
		Message: fmt.Sprintf("unknown classification: %s", output),
	}
}
