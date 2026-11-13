package webhook

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

// WebhookGuardrail calls an external HTTP endpoint for guardrail checks.
type WebhookGuardrail struct {
	name    string
	url     string
	method  string
	headers map[string]string
	retry   int
	client  *http.Client
}

// webhookRequest is the payload sent to the webhook.
type webhookRequest struct {
	Request  interface{}       `json:"request,omitempty"`
	Response interface{}       `json:"response,omitempty"`
	Metadata map[string]string `json:"metadata,omitempty"`
}

// webhookResponse is the expected response from the webhook.
type webhookResponse struct {
	Pass    bool                   `json:"pass"`
	Score   float64                `json:"score"`
	Message string                 `json:"message"`
	Details map[string]interface{} `json:"details,omitempty"`
}

// UnmarshalJSON requires a real verdict while retaining the optional legacy score.
func (wr *webhookResponse) UnmarshalJSON(data []byte) error {
	type fields webhookResponse
	var value fields
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	var verdict struct {
		Pass  *bool           `json:"pass"`
		Score json.RawMessage `json:"score"`
	}
	if err := json.Unmarshal(data, &verdict); err != nil {
		return err
	}
	if verdict.Pass == nil {
		return fmt.Errorf("webhook requires a boolean pass")
	}
	if len(verdict.Score) > 0 && string(verdict.Score) == "null" {
		return fmt.Errorf("webhook score must be numeric")
	}
	if math.IsNaN(value.Score) || math.IsInf(value.Score, 0) || value.Score < 0 || value.Score > 1 {
		return fmt.Errorf("webhook score must be finite and in [0,1]")
	}
	*wr = webhookResponse(value)
	return nil
}

// New creates a WebhookGuardrail from rule config.
func New(name string, cfg map[string]interface{}) *WebhookGuardrail {
	g := &WebhookGuardrail{
		name:   name,
		method: "POST",
		retry:  0,
		client: &http.Client{Timeout: 10 * time.Second},
	}

	if cfg != nil {
		if v, ok := cfg["url"].(string); ok {
			g.url = v
		}
		if v, ok := cfg["method"].(string); ok {
			g.method = v
		}
		if v, ok := cfg["headers"]; ok {
			if m, ok := v.(map[string]interface{}); ok {
				g.headers = make(map[string]string, len(m))
				for k, hv := range m {
					if s, ok := hv.(string); ok {
						g.headers[k] = s
					}
				}
			}
		}
		if v, ok := cfg["retry"]; ok {
			switch n := v.(type) {
			case int:
				g.retry = n
			case float64:
				g.retry = int(n)
			}
		}
	}

	return g
}

func (g *WebhookGuardrail) Name() string            { return g.name }
func (g *WebhookGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// RealtimeReady validates configuration without contacting the dependency.
func (g *WebhookGuardrail) RealtimeReady() error {
	u, err := url.Parse(g.url)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || g.retry < 0 {
		return fmt.Errorf("%w: webhook is not configured", guardrails.ErrRealtimeUnavailable)
	}
	return nil
}

// Check calls the webhook endpoint.
func (g *WebhookGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if input == nil || g.url == "" {
		return errorResult("webhook requires input and URL")
	}

	payload := webhookRequest{
		Metadata: input.Metadata,
	}
	if input.Request != nil {
		payload.Request = input.Request
	}
	if input.Response != nil {
		payload.Response = input.Response
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return errorResult(fmt.Sprintf("failed to marshal webhook payload: %v", err))
	}

	var lastErr error
	attempts := 1 + g.retry
	for i := 0; i < attempts; i++ {
		select {
		case <-ctx.Done():
			return errorResult("webhook check cancelled")
		default:
		}

		result, err := g.doRequest(ctx, body)
		if err != nil {
			lastErr = err
			continue
		}
		return result
	}

	return errorResult(fmt.Sprintf("webhook failed after %d attempts: %v", attempts, lastErr))
}

func (g *WebhookGuardrail) doRequest(ctx context.Context, body []byte) (*guardrails.CheckResult, error) {
	req, err := http.NewRequestWithContext(ctx, g.method, g.url, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}

	req.Header.Set("Content-Type", "application/json")
	for k, v := range g.headers {
		req.Header.Set(k, v)
	}

	resp, err := g.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("webhook returned status %d", resp.StatusCode)
	}

	respBody, err := guardrails.ReadResponseBody(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read webhook response: %v", err)
	}

	var wr webhookResponse
	if err := json.Unmarshal(respBody, &wr); err != nil {
		return nil, fmt.Errorf("failed to parse webhook response: %v", err)
	}

	// Availability stays separate from the legacy HTTP score/pass contract.
	// A realtime decision uses the exact fields that were validated, never the
	// case-insensitive struct decoder's interpretation of an ambiguous envelope.
	pass, score, unavailable := exactVerdict(respBody)
	if unavailable == nil {
		wr.Pass, wr.Score = pass, score
	}
	return &guardrails.CheckResult{
		RealtimeErr: unavailable,
		Pass:        wr.Pass,
		Score:       wr.Score,
		Message:     wr.Message,
		Details:     wr.Details,
	}, nil
}

// exactVerdict accepts one JSON object with unique, exact-case protected keys.
// An absent score preserves the historical zero-score threshold semantics.
func exactVerdict(body []byte) (bool, float64, error) {
	decoder := json.NewDecoder(bytes.NewReader(body))
	start, err := decoder.Token()
	if err != nil || start != json.Delim('{') {
		return false, 0, errors.New("webhook verdict must be an object")
	}
	fields := make(map[string]json.RawMessage)
	for decoder.More() {
		token, err := decoder.Token()
		if err != nil {
			return false, 0, err
		}
		key, ok := token.(string)
		if !ok {
			return false, 0, errors.New("invalid webhook verdict key")
		}
		for _, protected := range []string{"pass", "score", "message", "details"} {
			if strings.EqualFold(key, protected) && key != protected {
				return false, 0, errors.New("webhook verdict has a protected key alias")
			}
		}
		if _, exists := fields[key]; exists {
			return false, 0, errors.New("webhook verdict has a duplicate key")
		}
		var value json.RawMessage
		if err := decoder.Decode(&value); err != nil {
			return false, 0, err
		}
		fields[key] = value
	}
	if _, err := decoder.Token(); err != nil {
		return false, 0, err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return false, 0, errors.New("webhook verdict has trailing data")
	}
	var pass *bool
	if json.Unmarshal(fields["pass"], &pass) != nil || pass == nil {
		return false, 0, errors.New("webhook did not return a boolean pass verdict")
	}
	score := 0.0
	if raw, present := fields["score"]; present {
		var value *float64
		if json.Unmarshal(raw, &value) != nil || value == nil || math.IsNaN(*value) || math.IsInf(*value, 0) || *value < 0 || *value > 1 {
			return false, 0, errors.New("webhook returned an invalid score")
		}
		score = *value
	}
	return *pass, score, nil
}

func errorResult(msg string) *guardrails.CheckResult {
	return &guardrails.CheckResult{
		Err:     fmt.Errorf("%s", msg),
		Pass:    false,
		Score:   1.0,
		Message: msg,
	}
}

// IsWebhookConfig returns true if the config map contains a "url" key,
// indicating it should be handled as a webhook guardrail.
func IsWebhookConfig(cfg map[string]interface{}) bool {
	if cfg == nil {
		return false
	}
	_, ok := cfg["url"].(string)
	return ok
}
