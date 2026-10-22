package bedrock

// The client's own timeout is answered as the gateway
// timeout, and an upstream error's status is kept (in its code, which the
// status lists read, and on the unparsed path as UpstreamStatus).

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
	return &models.ChatCompletionRequest{Model: "anthropic.claude-3-sonnet-20240229-v1:0", Messages: []models.Message{{Role: "user", Content: []byte(`"hi"`)}}}
}

func TestRetryOnTimeoutBedrockClientTimeoutIsAGatewayTimeout(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-time.After(400 * time.Millisecond):
		case <-r.Context().Done():
		}
	}))
	defer ts.Close()
	p, err := New("test-bedrock", withTestAWSCredentials(config.ProviderConfig{
		BaseURL: ts.URL, APIFormat: "bedrock", DefaultTimeout: 50 * time.Millisecond, MaxConcurrent: 2,
	}))
	if err != nil {
		t.Fatal(err)
	}
	_, err = p.ChatCompletion(context.Background(), chatRequest())
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Type != models.ErrTypeGatewayTimeout {
		t.Errorf("the client's own timeout answered %v, want the gateway timeout", err)
	}
}

func TestUpstreamStatusMatchBedrockKeepsTheUpstreamStatus(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte(`<html>503</html>`))
	}))
	defer ts.Close()
	_, err := newTestProvider(t, ts.URL).ChatCompletion(context.Background(), chatRequest())
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != http.StatusBadGateway || apiErr.UpstreamStatus != http.StatusServiceUnavailable {
		t.Errorf("error %#v, want the client's 502 and the upstream's 503", err)
	}
}
