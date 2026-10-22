package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type crowdstrikeAdapter struct {
	clientID          string
	clientSecret      string
	baseURL           string
	severityThreshold int
	// client is the external guardrail's client, so the OAuth token call
	// goes through the same guard as the guard-chat call: for an org-set
	// base_url, a metadata token endpoint is always refused and a loopback or
	// private one is refused unless the operator listed it in
	// mcp.egress.private_allow.
	client *http.Client

	mu          sync.Mutex
	cachedToken string
	tokenExpiry time.Time
}

type crowdstrikeGuardRequest struct {
	GuardInput crowdstrikeGuardInput `json:"guard_input"`
}

type crowdstrikeGuardInput struct {
	Messages  []crowdstrikeMessage `json:"messages"`
	EventType string               `json:"event_type"`
}

type crowdstrikeMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type crowdstrikeResponse struct {
	Status  string            `json:"status"`
	Summary string            `json:"summary"`
	Result  crowdstrikeResult `json:"result"`
}

type crowdstrikeResult struct {
	Blocked     bool                       `json:"blocked"`
	Transformed bool                       `json:"transformed"`
	Detectors   map[string]json.RawMessage `json:"detectors"`
}

type crowdstrikeTokenResponse struct {
	AccessToken string `json:"access_token"`
	ExpiresIn   int    `json:"expires_in"`
}

func newCrowdstrikeAdapter(cfg map[string]interface{}, client *http.Client) *crowdstrikeAdapter {
	return &crowdstrikeAdapter{
		clientID:          getStringConfig(cfg, "client_id", ""),
		clientSecret:      getStringConfig(cfg, "client_secret", ""),
		baseURL:           getStringConfig(cfg, "base_url", "https://api.crowdstrike.com"),
		severityThreshold: getIntConfig(cfg, "severity_threshold", 3),
		client:            client,
	}
}

func (a *crowdstrikeAdapter) getToken(ctx context.Context) (string, error) {
	a.mu.Lock()
	defer a.mu.Unlock()

	if a.cachedToken != "" && time.Now().Before(a.tokenExpiry) {
		return a.cachedToken, nil
	}

	url := strings.TrimRight(a.baseURL, "/") + "/oauth2/token"
	payload := fmt.Sprintf("client_id=%s&client_secret=%s&grant_type=client_credentials", a.clientID, a.clientSecret)

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, strings.NewReader(payload))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := a.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("crowdstrike oauth failed: HTTP %d", resp.StatusCode)
	}

	var tokenResp crowdstrikeTokenResponse
	if err := json.NewDecoder(resp.Body).Decode(&tokenResp); err != nil {
		return "", fmt.Errorf("failed to decode token response: %w", err)
	}

	a.cachedToken = tokenResp.AccessToken
	a.tokenExpiry = time.Now().Add(time.Duration(tokenResp.ExpiresIn-60) * time.Second)
	return a.cachedToken, nil
}

func (a *crowdstrikeAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	token, err := a.getToken(ctx)
	if err != nil {
		return nil, fmt.Errorf("crowdstrike auth failed: %w", err)
	}

	url := strings.TrimRight(a.baseURL, "/") + "/aidr/aiguard/v1/guard_chat_completions"
	payload := crowdstrikeGuardRequest{
		GuardInput: crowdstrikeGuardInput{
			Messages: []crowdstrikeMessage{
				{Role: "user", Content: text},
			},
			EventType: "input",
		},
	}
	return makeJSONRequest(ctx, url, payload, map[string]string{
		"Authorization": "Bearer " + token,
	})
}

func (a *crowdstrikeAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp crowdstrikeResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse crowdstrike response: %v", err)}
	}

	if !resp.Result.Blocked {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	var detectors []string
	for name := range resp.Result.Detectors {
		detectors = append(detectors, name)
	}

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: fmt.Sprintf("crowdstrike blocked: %s — %s", strings.Join(detectors, ", "), resp.Summary),
		Details: map[string]interface{}{
			"blocked":     resp.Result.Blocked,
			"transformed": resp.Result.Transformed,
			"detectors":   detectors,
			"summary":     resp.Summary,
		},
	}
}
