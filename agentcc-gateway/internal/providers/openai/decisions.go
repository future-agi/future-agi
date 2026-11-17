package openai

import (
	"bytes"
	"context"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// decisionsVendor names OpenAI in decision errors.
const decisionsVendor = "OpenAI"

// CreateDecision sends an OpenAI Decisions request and returns the raw JSON
// response. Built like CreateResponse, with the decision routes' rules on top:
// no redirects, a capped answer, and errors from the decision error table that
// never carry the key or the request's content.
func (p *Provider) CreateDecision(ctx context.Context, req *models.DecisionRequest) (*models.DecisionResponse, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, models.ErrGatewayTimeout(decisionsVendor + " request timed out")
	}
	defer p.releaseSemaphore()

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, p.endpoint("/v1/decisions"), bytes.NewReader(req.Body))
	if err != nil {
		return nil, models.ErrInternal("openai: could not create decisions request")
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if p.apiKey != "" {
		httpReq.Header.Set("Authorization", "Bearer "+p.apiKey)
	}

	// The provider's client follows redirects for the endpoints that have
	// always allowed it. A decision call carries the caller's own key and
	// content, so it never follows one.
	client := *p.decisionHTTPClient
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	resp, err := client.Do(httpReq)
	if err != nil {
		return nil, models.DecisionTransportError(ctx, err, decisionsVendor)
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(io.LimitReader(resp.Body, models.MaxDecisionResponseBytes+1))
	if err != nil {
		return nil, models.DecisionTransportError(ctx, err, decisionsVendor)
	}
	if resp.StatusCode != http.StatusOK {
		message := req.UpstreamErrorMessage(resp.StatusCode, data, p.apiKey, decisionsVendor+" rejected the request.")
		return nil, models.DecisionUpstreamError(decisionsVendor, resp.StatusCode, resp.Header, message)
	}
	if len(data) > models.MaxDecisionResponseBytes {
		return nil, models.DecisionInvalidResponse(decisionsVendor)
	}
	parsed, ok := models.ParseDecisionResponse(data)
	if !ok {
		return nil, models.DecisionInvalidResponse(decisionsVendor)
	}
	return parsed, nil
}
