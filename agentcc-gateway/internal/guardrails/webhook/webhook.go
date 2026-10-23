package webhook

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/credentials"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/mcp/egress"
)

// webhookTimeout bounds one webhook call.
const webhookTimeout = 10 * time.Second

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

// New creates a WebhookGuardrail from an organization's config. Its endpoint
// is caller-controlled, so the call goes through the gateway's guarded egress
// dialer (internal/mcp/egress): after DNS, a link-local or cloud-metadata URL
// is always refused, a loopback or private URL is refused unless the operator
// listed it in mcp.egress.private_allow, and every redirect hop is
// re-checked. A nil dialer is the guard with no exceptions. It is the
// constructor the org-config factory uses.
func New(name string, cfg map[string]interface{}, dialer *egress.Dialer) *WebhookGuardrail {
	g := build(name, cfg)
	g.client = dialer.CallbackClient(webhookTimeout)
	return g
}

// NewConfigFileRule creates a WebhookGuardrail from a rule in the gateway's
// own config file. An operator-trusted rule keeps a plain client (a
// config.yaml webhook may point at the operator's own host without listing
// it). Only the config.yaml registry loop calls it.
func NewConfigFileRule(name string, cfg map[string]interface{}) *WebhookGuardrail {
	g := build(name, cfg)
	g.client = &http.Client{Timeout: webhookTimeout}
	return g
}

// build parses a rule into a WebhookGuardrail without its client; New and
// NewConfigFileRule set the client.
func build(name string, cfg map[string]interface{}) *WebhookGuardrail {
	g := &WebhookGuardrail{
		name:   name,
		method: "POST",
		retry:  0,
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

// Check calls the webhook endpoint.
func (g *WebhookGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if g.url == "" {
		return &guardrails.CheckResult{Pass: true}
	}

	payload := webhookRequest{
		// The request's metadata without the caller's credentials: the
		// guardrail plugin gives every check that copy, and this check sends
		// it out of the gateway, so it never relies on its caller for that.
		Metadata: credentials.WithoutCredentials(input.Metadata),
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

	// The message reaches the caller and the org's request log, and a
	// config.yaml webhook's url is operator config: report the failure by its
	// kind only, and log the error itself for the operator.
	slog.Warn("webhook guardrail call failed", "guardrail", g.name, "error", lastErr)
	return errorResult(fmt.Sprintf("webhook failed after %d attempts: %s", attempts, guardrails.CallFailure(lastErr)))
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

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20)) // 1MB limit
	if err != nil {
		return nil, fmt.Errorf("failed to read webhook response: %w", err)
	}

	var wr webhookResponse
	if err := json.Unmarshal(respBody, &wr); err != nil {
		return nil, fmt.Errorf("failed to parse webhook response: %v", err)
	}

	return &guardrails.CheckResult{
		Pass:    wr.Pass,
		Score:   wr.Score,
		Message: wr.Message,
		Details: wr.Details,
	}, nil
}

func errorResult(msg string) *guardrails.CheckResult {
	return &guardrails.CheckResult{
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
