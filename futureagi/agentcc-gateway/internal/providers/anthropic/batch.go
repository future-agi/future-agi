package anthropic

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// anthropicBatchRequest is the top-level request body for POST /v1/messages/batches.
type anthropicBatchRequest struct {
	Requests []anthropicBatchRequestItem `json:"requests"`
}

// anthropicBatchRequestItem is a single request within a batch.
type anthropicBatchRequestItem struct {
	CustomID string            `json:"custom_id"`
	Params   *anthropicRequest `json:"params"`
}

// anthropicBatchResponse is the response from POST /v1/messages/batches
// and GET /v1/messages/batches/{id}.
type anthropicBatchResponse struct {
	ID               string               `json:"id"`
	Type             string               `json:"type"`
	ProcessingStatus string               `json:"processing_status"`
	RequestCounts    anthropicBatchCounts `json:"request_counts"`
	CreatedAt        string               `json:"created_at"`
	EndedAt          *string              `json:"ended_at,omitempty"`
	ExpiresAt        string               `json:"expires_at,omitempty"`
	ResultsURL       *string              `json:"results_url,omitempty"`
}

// anthropicBatchCounts tracks per-status request counts in a batch.
type anthropicBatchCounts struct {
	Processing int `json:"processing"`
	Succeeded  int `json:"succeeded"`
	Errored    int `json:"errored"`
	Canceled   int `json:"canceled"`
	Expired    int `json:"expired"`
}

// SubmitBatch submits a batch of requests to Anthropic's Message Batches API.
// It translates each OpenAI-format ChatCompletionRequest into an Anthropic Messages
// API request before submitting.
func (p *Provider) SubmitBatch(ctx context.Context, requests []models.BatchRequest) (string, error) {
	if err := p.acquireSemaphore(ctx); err != nil {
		return "", models.ErrGatewayTimeout("anthropic: concurrency limit reached")
	}
	defer p.releaseSemaphore()

	// Translate each gateway BatchRequest into an Anthropic batch request item.
	items := make([]anthropicBatchRequestItem, 0, len(requests))
	for i, req := range requests {
		ar, err := translateRequest(&req.Body)
		if err != nil {
			return "", models.ErrInternal(fmt.Sprintf("anthropic: translating batch request %d: %v", i, err))
		}
		ar.Stream = false

		items = append(items, anthropicBatchRequestItem{
			CustomID: req.CustomID,
			Params:   ar,
		})
	}

	batchReq := anthropicBatchRequest{
		Requests: items,
	}

	body, err := json.Marshal(batchReq)
	if err != nil {
		return "", models.ErrInternal(fmt.Sprintf("anthropic: marshaling batch request: %v", err))
	}

	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/messages/batches", bytes.NewReader(body))
	if err != nil {
		return "", models.ErrInternal(fmt.Sprintf("anthropic: creating batch request: %v", err))
	}

	p.setHeaders(httpReq)
	httpReq.Header.Set("anthropic-beta", "message-batches-2024-09-24")

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return "", models.ErrGatewayTimeout("anthropic: batch submission timed out")
		}
		return "", models.ErrUpstreamProvider(0, fmt.Sprintf("anthropic: batch submission failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return "", models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("anthropic: reading batch response: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return "", parseAnthropicError(resp.StatusCode, respBody)
	}

	var batchResp anthropicBatchResponse
	if err := json.Unmarshal(respBody, &batchResp); err != nil {
		return "", models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("anthropic: parsing batch response: %v", err))
	}

	return batchResp.ID, nil
}

// GetBatch retrieves the current status of an Anthropic message batch.
func (p *Provider) GetBatch(ctx context.Context, batchID string) (*models.ProviderBatchStatus, error) {
	httpReq, err := http.NewRequestWithContext(ctx, "GET", p.baseURL+"/v1/messages/batches/"+batchID, nil)
	if err != nil {
		return nil, models.ErrInternal(fmt.Sprintf("anthropic: creating get batch request: %v", err))
	}

	p.setHeaders(httpReq)
	httpReq.Header.Set("anthropic-beta", "message-batches-2024-09-24")

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return nil, models.ErrGatewayTimeout("anthropic: get batch timed out")
		}
		return nil, models.ErrUpstreamProvider(0, fmt.Sprintf("anthropic: get batch failed: %v", err))
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, 10*1024*1024))
	if err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("anthropic: reading batch status: %v", err))
	}

	if resp.StatusCode != http.StatusOK {
		return nil, parseAnthropicError(resp.StatusCode, respBody)
	}

	var batchResp anthropicBatchResponse
	if err := json.Unmarshal(respBody, &batchResp); err != nil {
		return nil, models.ErrUpstreamProvider(resp.StatusCode, fmt.Sprintf("anthropic: parsing batch status: %v", err))
	}

	total := batchResp.RequestCounts.Processing +
		batchResp.RequestCounts.Succeeded +
		batchResp.RequestCounts.Errored +
		batchResp.RequestCounts.Canceled +
		batchResp.RequestCounts.Expired

	// Parse ISO 8601 timestamps to Unix seconds.
	createdAt := parseISO8601Unix(batchResp.CreatedAt)
	var completedAt *int64
	if batchResp.EndedAt != nil {
		ts := parseISO8601Unix(*batchResp.EndedAt)
		completedAt = &ts
	}

	return &models.ProviderBatchStatus{
		ID:          batchResp.ID,
		Status:      mapAnthropicBatchStatus(batchResp.ProcessingStatus),
		Total:       total,
		Completed:   batchResp.RequestCounts.Succeeded,
		Failed:      batchResp.RequestCounts.Errored,
		CreatedAt:   createdAt,
		CompletedAt: completedAt,
	}, nil
}

// CancelBatch cancels an Anthropic message batch.
func (p *Provider) CancelBatch(ctx context.Context, batchID string) error {
	httpReq, err := http.NewRequestWithContext(ctx, "POST", p.baseURL+"/v1/messages/batches/"+batchID+"/cancel", nil)
	if err != nil {
		return models.ErrInternal(fmt.Sprintf("anthropic: creating cancel batch request: %v", err))
	}

	p.setHeaders(httpReq)
	httpReq.Header.Set("anthropic-beta", "message-batches-2024-09-24")

	resp, err := p.httpClient.Do(httpReq)
	if err != nil {
		if ctx.Err() != nil {
			return models.ErrGatewayTimeout("anthropic: cancel batch timed out")
		}
		return models.ErrUpstreamProvider(0, fmt.Sprintf("anthropic: cancel batch failed: %v", err))
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		return parseAnthropicError(resp.StatusCode, respBody)
	}

	return nil
}

// mapAnthropicBatchStatus maps Anthropic's processing_status to unified batch status.
func mapAnthropicBatchStatus(status string) string {
	switch status {
	case "in_progress":
		return models.BatchStatusProcessing
	case "ended":
		return models.BatchStatusCompleted
	case "canceling":
		return models.BatchStatusProcessing
	default:
		return models.BatchStatusQueued
	}
}

// parseISO8601Unix parses an ISO 8601 timestamp string to Unix seconds.
// Returns 0 if parsing fails.
func parseISO8601Unix(s string) int64 {
	if s == "" {
		return 0
	}
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		// Try without fractional seconds.
		t, err = time.Parse(time.RFC3339, s)
		if err != nil {
			return 0
		}
	}
	return t.Unix()
}
