package external

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/guardrails"
)

type lassoAdapter struct {
	apiKey         string
	endpoint       string
	userID         string
	conversationID string
}

// lassoRequest matches the Lasso Security /gateway/v3/classify endpoint.
// API ref: https://github.com/lasso-security/mcp-gateway (official Lasso MCP gateway)
type lassoRequest struct {
	Messages    []lassoMessage `json:"messages"`
	MessageType string         `json:"messageType"`
	UserID      string         `json:"userId,omitempty"`
	SessionID   string         `json:"sessionId,omitempty"`
}

type lassoMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type lassoResponse struct {
	ViolationsDetected bool                       `json:"violations_detected"`
	Deputies           map[string]bool            `json:"deputies"`
	Findings           map[string][]lassoFinding  `json:"findings"`
}

type lassoFinding struct {
	Action   string `json:"action"`
	Severity string `json:"severity"`
}

func newLassoAdapter(cfg map[string]interface{}) *lassoAdapter {
	endpoint := getStringConfig(cfg, "endpoint", "https://server.lasso.security/gateway/v3")
	endpoint = strings.TrimRight(endpoint, "/")
	if !strings.HasSuffix(endpoint, "/classify") {
		endpoint += "/classify"
	}

	return &lassoAdapter{
		apiKey:         getStringConfig(cfg, "api_key", ""),
		endpoint:       endpoint,
		userID:         getStringConfig(cfg, "user_id", ""),
		conversationID: getStringConfig(cfg, "conversation_id", ""),
	}
}

// Lasso uses "lasso-api-key" header (not Authorization: Bearer).
func (a *lassoAdapter) buildRequest(ctx context.Context, text string) (*http.Request, error) {
	payload := lassoRequest{
		Messages: []lassoMessage{
			{Role: "user", Content: text},
		},
		MessageType: "PROMPT",
	}
	if a.userID != "" {
		payload.UserID = a.userID
	}
	if a.conversationID != "" {
		payload.SessionID = a.conversationID
	}

	headers := map[string]string{
		"lasso-api-key": a.apiKey,
	}
	if a.conversationID != "" {
		headers["lasso-conversation-id"] = a.conversationID
	}

	return makeJSONRequest(ctx, a.endpoint, payload, headers)
}

func (a *lassoAdapter) parseResponse(body []byte) *guardrails.CheckResult {
	var resp lassoResponse
	if err := json.Unmarshal(body, &resp); err != nil {
		return &guardrails.CheckResult{Pass: false, Score: 1.0, Message: fmt.Sprintf("failed to parse lasso response: %v", err)}
	}

	if !resp.ViolationsDetected {
		return &guardrails.CheckResult{Pass: true, Score: 0.0, Message: "content is safe"}
	}

	var violatedDeputies []string
	for deputy, violated := range resp.Deputies {
		if violated {
			violatedDeputies = append(violatedDeputies, deputy)
		}
	}

	var blockingViolations []string
	for deputy, findings := range resp.Findings {
		for _, f := range findings {
			if f.Action == "BLOCK" {
				blockingViolations = append(blockingViolations, deputy)
				break
			}
		}
	}

	msg := fmt.Sprintf("lasso violations: %s", strings.Join(violatedDeputies, ", "))

	return &guardrails.CheckResult{
		Pass:    false,
		Score:   1.0,
		Message: msg,
		Details: map[string]interface{}{
			"deputies":            resp.Deputies,
			"findings":            resp.Findings,
			"blocking_violations": blockingViolations,
		},
	}
}
