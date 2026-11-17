package logging

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A request log says whose words its error message is (error_source):
// "provider" when the request's last error is a provider's own error answer,
// which an adapter parsed (APIError.ProviderStatus or ProviderRejected);
// "client" when the caller ended the request; "gateway" for every other
// error, the gateway's account of a provider that answered badly or not at
// all included. A request without an error carries none. The source is kept
// when the content mode drops the message itself.

func errorSourceRequest(t *testing.T, err error, metadata map[string]string) *models.RequestContext {
	t.Helper()
	rc := models.AcquireRequestContext()
	t.Cleanup(rc.Release)
	rc.RequestID = "req_error_source"
	rc.StartTime = time.Date(2026, 11, 12, 1, 0, 0, 0, time.UTC)
	rc.Model = "gpt-4o-mini"
	rc.Provider = "openai"
	for k, v := range metadata {
		rc.Metadata[k] = v
	}
	if err != nil {
		rc.AddError(err)
	}
	return rc
}

func TestRequestLogSaysWhoseWordsItsErrorIs(t *testing.T) {
	cases := []struct {
		name     string
		err      error
		metadata map[string]string
		want     string
	}{
		{
			name: "a provider's own error answer",
			err: &models.APIError{Status: 400, Type: models.ErrTypeInvalidRequest, Code: "invalid_request_error",
				Message: "max_tokens is too large", ProviderStatus: 400, Provider: "openai"},
			want: "provider",
		},
		{
			name: "a provider error an adapter read from its envelope",
			err: &models.APIError{Status: 502, Type: models.ErrTypeUpstream, Code: "provider_500",
				Message: "upstream overloaded", ProviderRejected: true},
			want: "provider",
		},
		{name: "a gateway refusal", err: models.ErrForbidden("this key may not use this model"), want: "gateway"},
		{
			name: "the gateway's account of a provider that never answered",
			err:  models.ErrUpstreamProvider(0, "the provider did not answer"),
			want: "gateway",
		},
		{name: "an error that is not an API error", err: errors.New("something broke"), want: "gateway"},
		{name: "the client cancelled before an answer", err: context.Canceled, want: "client"},
		{
			name:     "the client went away during a call (call_end)",
			err:      &models.APIError{Status: 502, Type: models.ErrTypeUpstream, Code: "provider_0", Message: "reading provider response: context canceled"},
			metadata: map[string]string{"call_end": "client_cancelled"},
			want:     "client",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rc := errorSourceRequest(t, tc.err, tc.metadata)
			rec := buildRecord(rc, config.RequestLoggingConfig{Enabled: true})
			rec.LatencyMs = 3
			entry := sentEntry(t, rec, rc, LogEntryOptions{IncludeBodies: true, ContentMode: models.ContentModeFull})
			if entry["error_source"] != tc.want {
				t.Errorf("log entry error_source = %v, want %q", entry["error_source"], tc.want)
			}
			if errs := schematest.Validate(t, "log-entry-v2.schema.json", entry); len(errs) > 0 {
				t.Errorf("the entry fails log-entry-v2.schema.json: %v", errs)
			}
		})
	}
}

func TestRequestLogWithoutAnErrorHasNoErrorSource(t *testing.T) {
	rc := errorSourceRequest(t, nil, nil)
	rec := buildRecord(rc, config.RequestLoggingConfig{Enabled: true})
	entry := sentEntry(t, rec, rc, LogEntryOptions{IncludeBodies: true, ContentMode: models.ContentModeFull})
	if v, ok := entry["error_source"]; ok {
		t.Errorf("log entry error_source = %v, want the key left out", v)
	}
}

func TestErrorSourceIsKeptWhenTheContentModeDropsTheMessage(t *testing.T) {
	rc := errorSourceRequest(t, &models.APIError{Status: 400, Type: models.ErrTypeInvalidRequest,
		Code: "invalid_request_error", Message: "quoted request content", ProviderStatus: 400}, nil)
	rec := buildRecord(rc, config.RequestLoggingConfig{Enabled: true})
	entry := sentEntry(t, rec, rc, LogEntryOptions{ContentMode: models.ContentModeNone})
	if _, ok := entry["error_message"]; ok {
		t.Errorf("content mode none kept error_message %v", entry["error_message"])
	}
	if entry["error_source"] != "provider" {
		t.Errorf("log entry error_source = %v, want provider", entry["error_source"])
	}
}
