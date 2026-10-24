package openai

// An upstream error keeps the provider's own status
// beside the 502 the client gets, and the client's own timeout is answered
// as the gateway timeout, so on_timeout acts on it.

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func chatRequest() *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{Model: "gpt-4o", Messages: []models.Message{{Role: "user", Content: []byte(`"hi"`)}}}
}

func TestUpstreamStatusMatchOpenAIKeepsTheUpstreamStatus(t *testing.T) {
	for _, tc := range []struct {
		name, body string
	}{
		{"parsed error body", `{"error":{"message":"overloaded","type":"server_error","code":"unavailable"}}`},
		{"unparsed error body", `<html>503 Service Unavailable</html>`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
				w.WriteHeader(http.StatusServiceUnavailable)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer ts.Close()
			_, err := newTestProvider(t, ts.URL).ChatCompletion(context.Background(), chatRequest())
			var apiErr *models.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("error %v is not an APIError", err)
			}
			if apiErr.Status != http.StatusBadGateway || apiErr.UpstreamStatus != http.StatusServiceUnavailable {
				t.Errorf("status %d upstream %d, want the client's 502 and the upstream's 503", apiErr.Status, apiErr.UpstreamStatus)
			}
		})
	}
}

func TestRetryOnTimeoutOpenAIClientTimeoutIsAGatewayTimeout(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-time.After(400 * time.Millisecond):
		case <-r.Context().Done():
		}
	}))
	defer ts.Close()
	p, err := New("test-openai", config.ProviderConfig{BaseURL: ts.URL, APIKey: "test-key", DefaultTimeout: 50 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	_, err = p.ChatCompletion(context.Background(), chatRequest())
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Type != models.ErrTypeGatewayTimeout || apiErr.Status != http.StatusGatewayTimeout {
		t.Errorf("the client's own timeout answered %v, want the gateway timeout (504 gateway_timeout)", err)
	}
}
