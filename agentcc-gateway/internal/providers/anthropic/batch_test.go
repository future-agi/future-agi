package anthropic

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ---------------------------------------------------------------------------
// SubmitBatch tests
// ---------------------------------------------------------------------------

func TestSubmitBatch_Success(t *testing.T) {
	var receivedBody []byte
	var receivedAPIKey string
	var receivedBeta string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedAPIKey = r.Header.Get("x-api-key")
		receivedBeta = r.Header.Get("anthropic-beta")

		if r.Method != http.MethodPost {
			t.Errorf("method = %q, want POST", r.Method)
		}
		if r.URL.Path != "/v1/messages/batches" {
			t.Errorf("path = %q, want /v1/messages/batches", r.URL.Path)
		}

		var err error
		receivedBody, err = io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed to read request body: %v", err)
		}

		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{
			"id": "msgbatch_abc123",
			"type": "message_batch",
			"processing_status": "in_progress",
			"request_counts": {
				"processing": 2,
				"succeeded": 0,
				"errored": 0,
				"canceled": 0,
				"expired": 0
			},
			"created_at": "2024-09-24T18:37:24.100435+00:00",
			"expires_at": "2024-09-25T18:37:24.100435+00:00"
		}`)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	batchID, err := p.SubmitBatch(context.Background(), []models.BatchRequest{
		{
			CustomID: "req-1",
			Body: models.ChatCompletionRequest{
				Model: "claude-3-sonnet-20240229",
				Messages: []models.Message{
					{Role: "user", Content: json.RawMessage(`"Hello!"`)},
				},
			},
		},
		{
			CustomID: "req-2",
			Body: models.ChatCompletionRequest{
				Model: "claude-3-sonnet-20240229",
				Messages: []models.Message{
					{Role: "user", Content: json.RawMessage(`"World!"`)},
				},
			},
		},
	})
	if err != nil {
		t.Fatalf("SubmitBatch error: %v", err)
	}

	if batchID != "msgbatch_abc123" {
		t.Errorf("batchID = %q, want %q", batchID, "msgbatch_abc123")
	}

	// Verify headers.
	if receivedAPIKey != "test-api-key" {
		t.Errorf("x-api-key = %q, want %q", receivedAPIKey, "test-api-key")
	}
	if receivedBeta != "message-batches-2024-09-24" {
		t.Errorf("anthropic-beta = %q, want %q", receivedBeta, "message-batches-2024-09-24")
	}

	// Verify request body structure.
	var batchReq anthropicBatchRequest
	if err := json.Unmarshal(receivedBody, &batchReq); err != nil {
		t.Fatalf("failed to unmarshal request body: %v", err)
	}
	if len(batchReq.Requests) != 2 {
		t.Fatalf("requests length = %d, want 2", len(batchReq.Requests))
	}
	if batchReq.Requests[0].CustomID != "req-1" {
		t.Errorf("requests[0].custom_id = %q, want %q", batchReq.Requests[0].CustomID, "req-1")
	}
	if batchReq.Requests[1].CustomID != "req-2" {
		t.Errorf("requests[1].custom_id = %q, want %q", batchReq.Requests[1].CustomID, "req-2")
	}
	// Verify the params were translated to Anthropic format.
	if batchReq.Requests[0].Params.Model != "claude-3-sonnet-20240229" {
		t.Errorf("requests[0].params.model = %q, want %q", batchReq.Requests[0].Params.Model, "claude-3-sonnet-20240229")
	}
	if batchReq.Requests[0].Params.Stream {
		t.Error("requests[0].params.stream should be false")
	}
}

func TestSubmitBatch_ProviderTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Block until context is cancelled.
		<-r.Context().Done()
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	// Use a very short context timeout.
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately.

	_, err := p.SubmitBatch(ctx, []models.BatchRequest{
		{
			CustomID: "req-1",
			Body: models.ChatCompletionRequest{
				Model: "claude-3-sonnet-20240229",
				Messages: []models.Message{
					{Role: "user", Content: json.RawMessage(`"Hello"`)},
				},
			},
		},
	})
	if err == nil {
		t.Fatal("expected error for cancelled context, got nil")
	}
}

func TestSubmitBatch_ProviderError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		fmt.Fprint(w, `{
			"type": "error",
			"error": {
				"type": "invalid_request_error",
				"message": "Too many requests in batch."
			}
		}`)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	_, err := p.SubmitBatch(context.Background(), []models.BatchRequest{
		{
			CustomID: "req-1",
			Body: models.ChatCompletionRequest{
				Model: "claude-3-sonnet-20240229",
				Messages: []models.Message{
					{Role: "user", Content: json.RawMessage(`"Hello"`)},
				},
			},
		},
	})
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T: %v", err, err)
	}
	if apiErr.Status != http.StatusBadRequest {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusBadRequest)
	}
}

// ---------------------------------------------------------------------------
// GetBatch tests
// ---------------------------------------------------------------------------

func TestGetBatch_InProgress(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method = %q, want GET", r.Method)
		}
		if r.URL.Path != "/v1/messages/batches/msgbatch_abc123" {
			t.Errorf("path = %q, want /v1/messages/batches/msgbatch_abc123", r.URL.Path)
		}

		// Verify beta header is set.
		if beta := r.Header.Get("anthropic-beta"); beta != "message-batches-2024-09-24" {
			t.Errorf("anthropic-beta = %q, want %q", beta, "message-batches-2024-09-24")
		}

		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{
			"id": "msgbatch_abc123",
			"type": "message_batch",
			"processing_status": "in_progress",
			"request_counts": {
				"processing": 5,
				"succeeded": 3,
				"errored": 1,
				"canceled": 0,
				"expired": 0
			},
			"created_at": "2024-09-24T18:37:24.100435+00:00"
		}`)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	status, err := p.GetBatch(context.Background(), "msgbatch_abc123")
	if err != nil {
		t.Fatalf("GetBatch error: %v", err)
	}

	if status.ID != "msgbatch_abc123" {
		t.Errorf("ID = %q, want %q", status.ID, "msgbatch_abc123")
	}
	if status.Status != models.BatchStatusProcessing {
		t.Errorf("Status = %q, want %q", status.Status, models.BatchStatusProcessing)
	}
	if status.Total != 9 { // 5 + 3 + 1 + 0 + 0
		t.Errorf("Total = %d, want 9", status.Total)
	}
	if status.Completed != 3 {
		t.Errorf("Completed = %d, want 3", status.Completed)
	}
	if status.Failed != 1 {
		t.Errorf("Failed = %d, want 1", status.Failed)
	}
	if status.CreatedAt == 0 {
		t.Error("CreatedAt should be non-zero (parsed from ISO 8601)")
	}
}

func TestGetBatch_Ended(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{
			"id": "msgbatch_done",
			"type": "message_batch",
			"processing_status": "ended",
			"request_counts": {
				"processing": 0,
				"succeeded": 10,
				"errored": 0,
				"canceled": 0,
				"expired": 0
			},
			"created_at": "2024-09-24T18:37:24.100435+00:00",
			"ended_at": "2024-09-24T19:00:00.000000+00:00",
			"results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_done/results"
		}`)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	status, err := p.GetBatch(context.Background(), "msgbatch_done")
	if err != nil {
		t.Fatalf("GetBatch error: %v", err)
	}

	if status.Status != models.BatchStatusCompleted {
		t.Errorf("Status = %q, want %q", status.Status, models.BatchStatusCompleted)
	}
	if status.Total != 10 {
		t.Errorf("Total = %d, want 10", status.Total)
	}
	if status.Completed != 10 {
		t.Errorf("Completed = %d, want 10", status.Completed)
	}
	if status.CompletedAt == nil {
		t.Error("CompletedAt should not be nil for ended batch")
	}
}

func TestGetBatch_StatusMapping(t *testing.T) {
	tests := []struct {
		anthropicStatus string
		expectedStatus  string
	}{
		{"in_progress", models.BatchStatusProcessing},
		{"ended", models.BatchStatusCompleted},
		{"canceling", models.BatchStatusProcessing},
		{"unknown_status", models.BatchStatusQueued},
	}

	for _, tc := range tests {
		t.Run(tc.anthropicStatus, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprintf(w, `{
					"id": "msgbatch_test",
					"type": "message_batch",
					"processing_status": %q,
					"request_counts": {"processing":0,"succeeded":0,"errored":0,"canceled":0,"expired":0},
					"created_at": "2024-09-24T18:37:24.100435+00:00"
				}`, tc.anthropicStatus)
			}))
			defer server.Close()

			p := newTestProvider(t, server.URL)
			defer p.Close()

			status, err := p.GetBatch(context.Background(), "msgbatch_test")
			if err != nil {
				t.Fatalf("GetBatch error: %v", err)
			}
			if status.Status != tc.expectedStatus {
				t.Errorf("Status = %q, want %q", status.Status, tc.expectedStatus)
			}
		})
	}
}

func TestGetBatch_NotFound(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		fmt.Fprint(w, `{
			"type": "error",
			"error": {
				"type": "not_found_error",
				"message": "Batch not found."
			}
		}`)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	_, err := p.GetBatch(context.Background(), "nonexistent")
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*models.APIError)
	if !ok {
		t.Fatalf("expected *models.APIError, got %T", err)
	}
	if apiErr.Status != http.StatusNotFound {
		t.Errorf("status = %d, want %d", apiErr.Status, http.StatusNotFound)
	}
}

// ---------------------------------------------------------------------------
// CancelBatch tests
// ---------------------------------------------------------------------------

func TestCancelBatch_Success(t *testing.T) {
	var receivedMethod string
	var receivedPath string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedMethod = r.Method
		receivedPath = r.URL.Path

		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{
			"id": "msgbatch_abc123",
			"type": "message_batch",
			"processing_status": "canceling",
			"request_counts": {"processing":3,"succeeded":5,"errored":0,"canceled":0,"expired":0},
			"created_at": "2024-09-24T18:37:24.100435+00:00"
		}`)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	err := p.CancelBatch(context.Background(), "msgbatch_abc123")
	if err != nil {
		t.Fatalf("CancelBatch error: %v", err)
	}

	if receivedMethod != http.MethodPost {
		t.Errorf("method = %q, want POST", receivedMethod)
	}
	if receivedPath != "/v1/messages/batches/msgbatch_abc123/cancel" {
		t.Errorf("path = %q, want /v1/messages/batches/msgbatch_abc123/cancel", receivedPath)
	}
}

func TestCancelBatch_NotFound(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		fmt.Fprint(w, `{
			"type": "error",
			"error": {
				"type": "not_found_error",
				"message": "Batch not found."
			}
		}`)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	err := p.CancelBatch(context.Background(), "nonexistent")
	if err == nil {
		t.Fatal("expected error, got nil")
	}
}

// ---------------------------------------------------------------------------
// parseISO8601Unix tests
// ---------------------------------------------------------------------------

func TestParseISO8601Unix(t *testing.T) {
	tests := []struct {
		input    string
		wantZero bool
	}{
		{"2024-09-24T18:37:24.100435+00:00", false},
		{"2024-09-24T18:37:24+00:00", false},
		{"2024-09-24T18:37:24Z", false},
		{"", true},
		{"invalid", true},
	}

	for _, tc := range tests {
		t.Run(tc.input, func(t *testing.T) {
			result := parseISO8601Unix(tc.input)
			if tc.wantZero && result != 0 {
				t.Errorf("parseISO8601Unix(%q) = %d, want 0", tc.input, result)
			}
			if !tc.wantZero && result == 0 {
				t.Errorf("parseISO8601Unix(%q) = 0, want non-zero", tc.input)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// mapAnthropicBatchStatus tests
// ---------------------------------------------------------------------------

func TestMapAnthropicBatchStatus(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"in_progress", models.BatchStatusProcessing},
		{"ended", models.BatchStatusCompleted},
		{"canceling", models.BatchStatusProcessing},
		{"some_new_status", models.BatchStatusQueued},
	}

	for _, tc := range tests {
		t.Run(tc.input, func(t *testing.T) {
			result := mapAnthropicBatchStatus(tc.input)
			if result != tc.expected {
				t.Errorf("mapAnthropicBatchStatus(%q) = %q, want %q", tc.input, result, tc.expected)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// SubmitBatch: model name prefix stripping
// ---------------------------------------------------------------------------

func TestSubmitBatch_StripsModelPrefix(t *testing.T) {
	var receivedModel string

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var batchReq anthropicBatchRequest
		json.Unmarshal(body, &batchReq)
		if len(batchReq.Requests) > 0 {
			receivedModel = batchReq.Requests[0].Params.Model
		}

		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{
			"id": "msgbatch_prefix",
			"type": "message_batch",
			"processing_status": "in_progress",
			"request_counts": {"processing":1,"succeeded":0,"errored":0,"canceled":0,"expired":0},
			"created_at": "2024-09-24T18:37:24.100435+00:00"
		}`)
	}))
	defer server.Close()

	p := newTestProvider(t, server.URL)
	defer p.Close()

	_, err := p.SubmitBatch(context.Background(), []models.BatchRequest{
		{
			CustomID: "req-1",
			Body: models.ChatCompletionRequest{
				Model: "anthropic/claude-3-sonnet-20240229",
				Messages: []models.Message{
					{Role: "user", Content: json.RawMessage(`"Hello"`)},
				},
			},
		},
	})
	if err != nil {
		t.Fatalf("SubmitBatch error: %v", err)
	}

	// The provider prefix should be stripped by translateRequest.
	if receivedModel != "claude-3-sonnet-20240229" {
		t.Errorf("model = %q, want %q (prefix stripped)", receivedModel, "claude-3-sonnet-20240229")
	}
}

// ---------------------------------------------------------------------------
// Interface compliance: verify Provider implements BatchProvider
// ---------------------------------------------------------------------------

func TestBatchProviderInterface(t *testing.T) {
	var _ interface {
		SubmitBatch(ctx context.Context, requests []models.BatchRequest) (string, error)
		GetBatch(ctx context.Context, batchID string) (*models.ProviderBatchStatus, error)
		CancelBatch(ctx context.Context, batchID string) error
	} = (*Provider)(nil)
}
