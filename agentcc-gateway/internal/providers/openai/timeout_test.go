package openai

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// newSlowProvider returns a provider whose client timeout is shorter than the
// upstream takes to answer, so the provider ceiling is what ends the call.
func newSlowProvider(t *testing.T, upstreamDelay, clientTimeout time.Duration) *Provider {
	t.Helper()
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(upstreamDelay)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(models.ChatCompletionResponse{ID: "late"})
	}))
	t.Cleanup(ts.Close)

	p, err := New("test-openai", config.ProviderConfig{
		BaseURL:        ts.URL,
		APIKey:         "test-key",
		DefaultTimeout: clientTimeout,
	})
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	return p
}

func apiError(t *testing.T, err error) *models.APIError {
	t.Helper()
	if err == nil {
		t.Fatal("call succeeded, want a timeout error")
	}
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error %v is not an *models.APIError", err)
	}
	return apiErr
}

// The provider's own client timeout never touches the request context, so a
// call it ends has to be read off the error. Reported as an upstream failure
// it becomes a 502, which the OpenAI and Anthropic SDKs both retry — on a
// request that already spent its whole budget.
func TestChatCompletion_ProviderCeilingIsAGatewayTimeout(t *testing.T) {
	p := newSlowProvider(t, 300*time.Millisecond, 50*time.Millisecond)

	// A request deadline far longer than the provider ceiling: ctx is still
	// live when the call is cut off, which is the whole point.
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	_, err := p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "gpt-4o"})

	apiErr := apiError(t, err)
	if apiErr.Status != http.StatusGatewayTimeout {
		t.Errorf("status = %d (%s / %s), want %d",
			apiErr.Status, apiErr.Type, apiErr.Code, http.StatusGatewayTimeout)
	}
	if apiErr.Type != models.ErrTypeGatewayTimeout {
		t.Errorf("type = %q, want %q", apiErr.Type, models.ErrTypeGatewayTimeout)
	}
}

// The same classification has to hold for a streamed call: its first error
// arrives on the error channel instead of the return value.
func TestStreamChatCompletion_ProviderCeilingIsAGatewayTimeout(t *testing.T) {
	p := newSlowProvider(t, 300*time.Millisecond, 50*time.Millisecond)

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	_, errs := p.StreamChatCompletion(ctx, &models.ChatCompletionRequest{Model: "gpt-4o", Stream: true})

	select {
	case err := <-errs:
		if apiErr := apiError(t, err); apiErr.Status != http.StatusGatewayTimeout {
			t.Errorf("status = %d (%s), want %d", apiErr.Status, apiErr.Code, http.StatusGatewayTimeout)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("no error arrived on the stream error channel")
	}
}

// A transport failure that is not a timeout must stay an upstream error, or
// the fix above would relabel every unreachable provider as a timeout.
func TestChatCompletion_UnreachableProviderStaysAnUpstreamError(t *testing.T) {
	// A server that is closed at once: connecting fails immediately.
	ts := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := ts.URL
	ts.Close()

	p, err := New("test-openai", config.ProviderConfig{BaseURL: url, APIKey: "test-key"})
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}

	_, err = p.ChatCompletion(context.Background(), &models.ChatCompletionRequest{Model: "gpt-4o"})

	if apiErr := apiError(t, err); apiErr.Status != http.StatusBadGateway {
		t.Errorf("status = %d (%s), want %d", apiErr.Status, apiErr.Code, http.StatusBadGateway)
	}
}

// A request whose own deadline expires keeps answering 504, as it did before.
func TestChatCompletion_RequestDeadlineIsStillAGatewayTimeout(t *testing.T) {
	p := newSlowProvider(t, 300*time.Millisecond, 30*time.Second)

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	_, err := p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "gpt-4o"})

	if apiErr := apiError(t, err); apiErr.Status != http.StatusGatewayTimeout {
		t.Errorf("status = %d (%s), want %d", apiErr.Status, apiErr.Code, http.StatusGatewayTimeout)
	}
}

// stalledProvider answers with headers and then withholds the body, so the
// call fails while reading rather than while waiting for headers.
func stalledProvider(t *testing.T, clientTimeout time.Duration, stream bool) *Provider {
	t.Helper()
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if stream {
			w.Header().Set("Content-Type", "text/event-stream")
		} else {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Content-Length", "4096") // promise a body that never comes
		}
		w.WriteHeader(http.StatusOK)
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
		time.Sleep(2 * time.Second) // outlast the client timeout
	}))
	t.Cleanup(ts.Close)

	p, err := New("test-openai", config.ProviderConfig{
		BaseURL:        ts.URL,
		APIKey:         "test-key",
		DefaultTimeout: clientTimeout,
	})
	if err != nil {
		t.Fatalf("New() error: %v", err)
	}
	return p
}

// A body that stalls after the headers arrive is still a timeout. The
// classification at the request boundary never sees it, because that call
// returned successfully — so this used to answer 502 and be retried.
func TestChatCompletion_StalledBodyIsAGatewayTimeout(t *testing.T) {
	p := stalledProvider(t, 100*time.Millisecond, false)

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	_, err := p.ChatCompletion(ctx, &models.ChatCompletionRequest{Model: "gpt-4o"})

	apiErr := apiError(t, err)
	if apiErr.Status != http.StatusGatewayTimeout {
		t.Errorf("status = %d (%s / %s), want %d",
			apiErr.Status, apiErr.Type, apiErr.Code, http.StatusGatewayTimeout)
	}
}

// The same for a stream whose first chunk never arrives.
func TestStreamChatCompletion_StalledFirstChunkIsAGatewayTimeout(t *testing.T) {
	p := stalledProvider(t, 100*time.Millisecond, true)

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	chunks, errs := p.StreamChatCompletion(ctx, &models.ChatCompletionRequest{Model: "gpt-4o", Stream: true})

	for {
		select {
		case _, ok := <-chunks:
			if !ok {
				chunks = nil
			}
		case err := <-errs:
			if apiErr := apiError(t, err); apiErr.Status != http.StatusGatewayTimeout {
				t.Errorf("status = %d (%s), want %d", apiErr.Status, apiErr.Code, http.StatusGatewayTimeout)
			}
			return
		case <-time.After(10 * time.Second):
			t.Fatal("no error arrived on the stream error channel")
		}
	}
}
