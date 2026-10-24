package azure

// An upstream error keeps the provider's own status
// beside the 502 the client gets, and the client's own timeout is answered
// as the gateway timeout.

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

func TestUpstreamStatusMatchAzureKeepsTheUpstreamStatus(t *testing.T) {
	for _, body := range []string{
		`{"error":{"message":"overloaded","type":"server_error","code":"unavailable"}}`,
		`<html>503 Service Unavailable</html>`,
	} {
		ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = w.Write([]byte(body))
		}))
		_, err := newTestProvider(t, ts.URL).ChatCompletion(context.Background(), chatRequest())
		ts.Close()
		var apiErr *models.APIError
		if !errors.As(err, &apiErr) || apiErr.Status != http.StatusBadGateway || apiErr.UpstreamStatus != http.StatusServiceUnavailable {
			t.Errorf("body %s: error %#v, want the client's 502 and the upstream's 503", body, err)
		}
	}
}

func TestRetryOnTimeoutAzureClientTimeoutIsAGatewayTimeout(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-time.After(400 * time.Millisecond):
		case <-r.Context().Done():
		}
	}))
	defer ts.Close()
	p, err := New("test-azure", config.ProviderConfig{BaseURL: ts.URL, APIKey: "test-key", DefaultTimeout: 50 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	_, err = p.ChatCompletion(context.Background(), chatRequest())
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Type != models.ErrTypeGatewayTimeout {
		t.Errorf("the client's own timeout answered %v, want the gateway timeout", err)
	}
}
