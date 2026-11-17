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
	// judges is the check's kind on the answer, as its config sets it
	// (guardrails.JudgesSays or JudgesShape), "" when unset; judgesSource
	// is how it was decided (guardrails.KindSet, KindNone or KindInvalid).
	judges, judgesSource string
	// configErr is why the rule cannot run (an invalid "judges"), or nil.
	// Check refuses every request while it is set, with no call.
	configErr error
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

// New creates a WebhookGuardrail from an organization's config. Its endpoint
// is caller-controlled, so the call goes through the gateway's guarded egress
// dialer (internal/mcp/egress): a loopback, private, link-local or
// cloud-metadata URL is refused unless the operator listed it in
// mcp.egress.private_allow, and redirects are re-checked.
// A nil dialer is the guard with no exceptions. It is
// the constructor the org-config factory uses.
func New(name string, cfg map[string]interface{}, dialer *egress.Dialer) *WebhookGuardrail {
	g := build(name, cfg)
	g.client = dialer.CallbackClient(webhookTimeout)
	return g
}

// NewConfigFileRule creates a WebhookGuardrail from a rule in the gateway's
// own config file. An operator-trusted rule keeps a plain client (a
// config.yaml webhook may point at the operator's own host without listing
// it), as it always has. Only the config.yaml registry loop calls it.
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

	g.judgesSource = guardrails.KindNone
	judges, err := guardrails.ParseJudges(cfg)
	switch {
	case err != nil:
		g.judgesSource = guardrails.KindInvalid
		g.configErr = fmt.Errorf("webhook guardrail %q: %w", name, err)
	case judges != "":
		g.judges, g.judgesSource = judges, guardrails.KindSet
	}

	return g
}

func (g *WebhookGuardrail) Name() string            { return g.name }
func (g *WebhookGuardrail) Stage() guardrails.Stage { return guardrails.StagePre }

// KindUnknown marks the check as one whose kind the gateway cannot tell
// (guardrails.UnknownKindCheck): its endpoint decides what it judges and
// its config sets no "judges". It then does not read a model's refusal, as
// in production.
func (g *WebhookGuardrail) KindUnknown() bool { return g.judges == "" }

// JudgesShape marks the check as one that judges the answer's shape
// (guardrails.ShapeCheck): its config's "judges" is "shape".
func (g *WebhookGuardrail) JudgesShape() bool { return g.judges == guardrails.JudgesShape }

// KindSource says how the check's kind was decided (guardrails.KindSourcer).
func (g *WebhookGuardrail) KindSource() string { return g.judgesSource }

// RealtimeReady validates configuration without contacting the dependency.
func (g *WebhookGuardrail) RealtimeReady() error {
	u, err := url.Parse(g.url)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || g.retry < 0 {
		return fmt.Errorf("%w: webhook is not configured", guardrails.ErrRealtimeUnavailable)
	}
	return nil
}

// Check calls the webhook endpoint. A rule whose config is invalid fails
// closed, with no call.
func (g *WebhookGuardrail) Check(ctx context.Context, input *guardrails.CheckInput) *guardrails.CheckResult {
	if g.configErr != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: g.configErr.Error()}
	}
	if input == nil || g.url == "" {
		return errorResult("webhook requires input and URL")
	}

	// The request's metadata without the caller's credentials: the
	// guardrail plugin gives every check that copy, and this check sends it
	// out of the gateway, so it never relies on its caller for that.
	payload := webhookRequest{
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

	return errorResult(fmt.Sprintf("webhook failed after %d attempts: %v", attempts, lastErr))
}

// doRequest makes one webhook call. A failure that could carry where the
// gateway connected (the URL, a dial, DNS or proxy address) is reported
// through guardrails.CallError, which names neither: the message reaches the
// org whose request is checked.
func (g *WebhookGuardrail) doRequest(ctx context.Context, body []byte) (*guardrails.CheckResult, error) {
	req, err := http.NewRequestWithContext(ctx, g.method, g.url, bytes.NewReader(body))
	if err != nil {
		return nil, guardrails.CallError(g.name, err)
	}

	req.Header.Set("Content-Type", "application/json")
	for k, v := range g.headers {
		req.Header.Set(k, v)
	}

	resp, err := g.client.Do(req)
	if err != nil {
		return nil, guardrails.CallError(g.name, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("webhook returned status %d", resp.StatusCode)
	}

	respBody, err := guardrails.ReadResponseBody(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read webhook response: %w", guardrails.CallError(g.name, err))
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
