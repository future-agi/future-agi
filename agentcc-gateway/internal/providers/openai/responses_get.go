package openai

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// GetResponse retrieves a background response from the same provider instance
// that created it, retaining its endpoint, credentials and admission limits.
func (p *Provider) GetResponse(ctx context.Context, responseID string) ([]byte, int, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return nil, 0, err
	}
	defer p.releaseSemaphore()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.endpoint("/v1/responses")+"/"+url.PathEscape(responseID), nil)
	if err != nil {
		return nil, 0, models.ErrInternal(fmt.Sprintf("creating response retrieval request: %v", err))
	}
	req.Header.Set("Accept", "application/json")
	if credential := p.credential(ctx); credential != "" {
		req.Header.Set("Authorization", "Bearer "+credential)
	}
	resp, err := p.do(req)
	if err != nil {
		if common.TimedOut(ctx, err) {
			return nil, 0, models.ErrGatewayTimeout("response retrieval timed out")
		}
		return nil, 0, models.ErrUpstreamProvider(0, fmt.Sprintf("response retrieval failed: %v", err))
	}
	defer resp.Body.Close()

	const maxBodyBytes = 10 * 1024 * 1024
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBodyBytes+1))
	if err != nil {
		return nil, resp.StatusCode, common.ReadError(err, resp.StatusCode, "reading response retrieval body")
	}
	if len(body) > maxBodyBytes {
		return nil, resp.StatusCode, models.ErrUpstreamProvider(http.StatusBadGateway, "response retrieval body exceeds maximum size")
	}
	if resp.StatusCode != http.StatusOK {
		return nil, resp.StatusCode, parseProviderError(resp.StatusCode, body)
	}
	return body, resp.StatusCode, nil
}
