package providers

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func asAPIError(err error, target **models.APIError) bool { return errors.As(err, target) }

func timeAfter() <-chan time.Time { return time.After(10 * time.Second) }

// sentinel is shaped like a credential and appears nowhere else, so finding it
// in any output means it came from the configured provider.
const sentinel = "SENTINEL-c7f3a1e9-DO-NOT-LEAK"

// deadURL returns the address of a server that is not listening, so a call to
// it fails at the transport level. That is the case that leaks: Go wraps the
// failure in a *url.Error carrying the whole URL it tried, and a provider that
// formats that error into its message publishes whatever the URL held.
func deadURL(t *testing.T) string {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	url := server.URL
	server.Close()
	return url
}

// configFor is the least configuration each format needs to be built.
func configFor(format, baseURL string) config.ProviderConfig {
	cfg := config.ProviderConfig{
		APIFormat: format,
		BaseURL:   baseURL,
		APIKey:    sentinel,
		Models:    []string{"test-model"},
	}
	if format == "bedrock" {
		cfg.AWSRegion = "us-east-1"
		cfg.AWSAccessKeyID = sentinel
		cfg.AWSSecretAccessKey = sentinel
	}
	return cfg
}

// A provider's credential must never reach the text of an error. That text is
// returned to the caller in the response body and stored on the request log
// row, so a credential in it is a credential published to everyone who can
// call the gateway or read its logs.
//
// This is the general form of a defect found in the gemini provider, which
// passed its key as a ?key= query parameter: every transport failure, and
// every client timeout, wrote the key into both places.
func TestNoProviderLeaksItsCredentialIntoAnError(t *testing.T) {
	for _, format := range supportedAPIFormats(t) {
		t.Run(format, func(t *testing.T) {
			provider, err := createProvider("leak-test", configFor(format, deadURL(t)))
			if err != nil {
				// Even a construction failure is text that reaches a log.
				assertNoSentinel(t, "construction error", err.Error())
				t.Skipf("%s cannot be built with a minimal config: %v", format, err)
			}

			req := &models.ChatCompletionRequest{
				Model:    "test-model",
				Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hi"`)}},
			}

			if _, err := provider.ChatCompletion(context.Background(), req); err != nil {
				assertNoSentinel(t, "ChatCompletion error", err.Error())
				var apiErr *models.APIError
				if asAPIError(err, &apiErr) {
					assertNoSentinel(t, "ChatCompletion error message", apiErr.Message)
				}
			}

			chunks, errs := provider.StreamChatCompletion(context.Background(), req)
			drainForError(t, chunks, errs)
		})
	}
}

// drainForError reads a stream until it ends or fails, checking whatever it
// reports.
func drainForError(t *testing.T, chunks <-chan models.StreamChunk, errs <-chan error) {
	t.Helper()
	if chunks == nil && errs == nil {
		return
	}
	for {
		select {
		case _, ok := <-chunks:
			if !ok {
				chunks = nil
				if errs == nil {
					return
				}
			}
		case err, ok := <-errs:
			if !ok {
				return
			}
			if err != nil {
				assertNoSentinel(t, "stream error", err.Error())
			}
			return
		case <-timeAfter():
			return
		}
	}
}

func assertNoSentinel(t *testing.T, what, text string) {
	t.Helper()
	if strings.Contains(text, sentinel) {
		t.Errorf("the configured credential reached the %s:\n%s", what, text)
	}
}

// supportedAPIFormats reads the formats createProvider accepts out of its own
// rejection message, so a provider added to that switch is covered here
// without anyone remembering to add it.
func supportedAPIFormats(t *testing.T) []string {
	t.Helper()
	_, err := createProvider("x", config.ProviderConfig{APIFormat: "no-such-format"})
	if err == nil {
		t.Fatal("createProvider accepted an unknown api_format")
	}
	_, list, found := strings.Cut(err.Error(), "supported: ")
	if !found {
		t.Fatalf("createProvider's rejection no longer lists the formats it accepts, so this "+
			"test can no longer discover them. Message was: %s", err.Error())
	}
	var formats []string
	for _, name := range strings.Split(strings.TrimRight(list, ")"), ",") {
		if name = strings.TrimSpace(name); name != "" {
			formats = append(formats, name)
		}
	}
	if len(formats) < 6 {
		t.Fatalf("only %d formats discovered, which cannot be right: %q", len(formats), list)
	}
	return formats
}
