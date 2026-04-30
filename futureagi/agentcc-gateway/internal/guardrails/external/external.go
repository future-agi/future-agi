package external

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// providerAdapter builds HTTP requests and parses responses for a specific provider.
type providerAdapter interface {
	buildRequest(ctx context.Context, text string) (*http.Request, error)
	parseResponse(body []byte) *guardrails.CheckResult
}

// ExternalGuardrail calls a third-party guardrail provider's API.
type ExternalGuardrail struct {
	name    string
	adapter providerAdapter
	retry   int
	client  *http.Client
}

// New creates an ExternalGuardrail from rule config.
func New(name string, cfg map[string]interface{}) *ExternalGuardrail {
	g := &ExternalGuardrail{
		name:   name,
		retry:  0,
		client: &http.Client{Timeout: 5 * time.Second},
	}

	if cfg == nil {
		return g
	}

	if v, ok := cfg["retry"]; ok {
		switch n := v.(type) {
		case int:
			g.retry = n
		case float64:
			g.retry = int(n)
		}
	}
	if v, ok := cfg["timeout"].(string); ok {
		if d, err := time.ParseDuration(v); err == nil {
			g.client.Timeout = d
		}
	}

	provider, _ := cfg["provider"].(string)
	switch provider {
	case "lakera":
		g.adapter = newLakeraAdapter(cfg)
	case "azure_content_safety":
		g.adapter = newAzureAdapter(cfg)
	case "presidio":
		g.adapter = newPresidioAdapter(cfg)
	case "llama_guard":
		g.adapter = newLlamaGuardAdapter(cfg)
	case "bedrock_guardrails":
		g.adapter = newBedrockAdapter(cfg)
	case "hiddenlayer":
		g.adapter = newHiddenlayerAdapter(cfg)
	case "dynamoai":
		g.adapter = newDynamoaiAdapter(cfg)
	case "enkrypt":
		g.adapter = newEnkryptAdapter(cfg)
	case "ibm_ai":
		g.adapter = newIbmAdapter(cfg)
	case "pangea":
		g.adapter = newPangeaAdapter(cfg)
	case "zscaler":
		g.adapter = newZscalerAdapter(cfg)
	case "crowdstrike":
		g.adapter = newCrowdstrikeAdapter(cfg)
	case "aporia":
		g.adapter = newAporiaAdapter(cfg)
	case "lasso":
		g.adapter = newLassoAdapter(cfg)
	case "grayswan":
		g.adapter = newGrayswanAdapter(cfg)
	}

	return g
}

func (g *ExternalGuardrail) Name() string            { return g.name }
func (g *ExternalGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// Check evaluates text against the external provider.
func (g *ExternalGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil {
		return &guardrails.CheckResult{Pass: true}
	}
	if g.adapter == nil {
		return &guardrails.CheckResult{Pass: true, Message: "external guardrail: no adapter configured"}
	}

	var text string
	if input.Response != nil {
		text = extractOutputText(input)
	} else {
		text = extractInputText(input)
	}
	if text == "" {
		return &guardrails.CheckResult{Pass: true}
	}

	respBody, err := g.callProvider(ctx, text)
	if err != nil {
		return &guardrails.CheckResult{
			Pass:    false,
			Score:   1.0,
			Message: fmt.Sprintf("external guardrail call failed: %v", err),
		}
	}

	return g.adapter.parseResponse(respBody)
}

func (g *ExternalGuardrail) callProvider(ctx context.Context, text string) ([]byte, error) {
	var lastErr error
	attempts := 1 + g.retry
	for i := 0; i < attempts; i++ {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}

		body, err := g.doCall(ctx, text)
		if err != nil {
			lastErr = err
			continue
		}
		return body, nil
	}
	return nil, fmt.Errorf("failed after %d attempts: %w", attempts, lastErr)
}

func (g *ExternalGuardrail) doCall(ctx context.Context, text string) ([]byte, error) {
	req, err := g.adapter.buildRequest(ctx, text)
	if err != nil {
		return nil, err
	}

	resp, err := g.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusUnauthorized {
		return nil, fmt.Errorf("authentication failed (HTTP %d)", resp.StatusCode)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("unexpected status %d", resp.StatusCode)
	}

	return io.ReadAll(io.LimitReader(resp.Body, 1<<20)) // 1MB limit
}

// extractInputText concatenates all message contents from the request.
func extractInputText(input *guardrails.CheckInput) string {
	if input.Request == nil || len(input.Request.Messages) == 0 {
		return ""
	}
	var parts []string
	for _, msg := range input.Request.Messages {
		text := extractContentText(msg.Content)
		if text != "" {
			parts = append(parts, text)
		}
	}
	return strings.Join(parts, "\n")
}

// extractOutputText concatenates all choice message contents from the response.
func extractOutputText(input *guardrails.CheckInput) string {
	if input.Response == nil || len(input.Response.Choices) == 0 {
		return ""
	}
	var parts []string
	for _, choice := range input.Response.Choices {
		text := extractContentText(choice.Message.Content)
		if text != "" {
			parts = append(parts, text)
		}
	}
	return strings.Join(parts, "\n")
}

func extractContentText(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var s string
	if err := json.Unmarshal(raw, &s); err == nil {
		return s
	}
	var parts []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(raw, &parts); err == nil {
		var texts []string
		for _, p := range parts {
			if p.Type == "text" && p.Text != "" {
				texts = append(texts, p.Text)
			}
		}
		return strings.Join(texts, " ")
	}
	return ""
}

// IsExternalProviderConfig returns true if the config specifies a supported external provider.
func IsExternalProviderConfig(cfg map[string]interface{}) bool {
	if cfg == nil {
		return false
	}
	provider, ok := cfg["provider"].(string)
	if !ok {
		return false
	}
	switch provider {
	case "lakera", "azure_content_safety", "presidio", "llama_guard", "bedrock_guardrails",
		"hiddenlayer", "dynamoai", "enkrypt", "ibm_ai", "pangea",
		"zscaler", "crowdstrike", "aporia", "lasso", "grayswan":
		return true
	}
	return false
}

func expandEnv(s string) string {
	if strings.HasPrefix(s, "${") && strings.HasSuffix(s, "}") {
		return os.Getenv(s[2 : len(s)-1])
	}
	return s
}

func getStringConfig(cfg map[string]interface{}, key, defaultVal string) string {
	if v, ok := cfg[key].(string); ok && v != "" {
		return expandEnv(v)
	}
	return defaultVal
}

func getStringSliceConfig(cfg map[string]interface{}, key string) []string {
	raw, ok := cfg[key]
	if !ok {
		return nil
	}
	switch v := raw.(type) {
	case []interface{}:
		result := make([]string, 0, len(v))
		for _, item := range v {
			if s, ok := item.(string); ok {
				result = append(result, s)
			}
		}
		return result
	case []string:
		return v
	}
	return nil
}

func getFloatConfig(cfg map[string]interface{}, key string, defaultVal float64) float64 {
	if v, ok := cfg[key]; ok {
		switch n := v.(type) {
		case float64:
			return n
		case int:
			return float64(n)
		}
	}
	return defaultVal
}

func getIntConfig(cfg map[string]interface{}, key string, defaultVal int) int {
	if v, ok := cfg[key]; ok {
		switch n := v.(type) {
		case int:
			return n
		case float64:
			return int(n)
		}
	}
	return defaultVal
}

// makeJSONRequest creates a JSON POST request.
func makeJSONRequest(ctx context.Context, url string, payload interface{}, headers map[string]string) (*http.Request, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	return req, nil
}

// inputForPost creates a guardrails.CheckInput with a single user message for testing.
func inputForPost(content string) *guardrails.CheckInput {
	raw, _ := json.Marshal(content)
	return &guardrails.CheckInput{
		Response: &models.ChatCompletionResponse{
			Choices: []models.Choice{
				{Message: models.Message{Role: "assistant", Content: raw}},
			},
		},
		Request: &models.ChatCompletionRequest{Model: "test"},
	}
}
