package models

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestWriteError(t *testing.T) {
	tests := []struct {
		name       string
		err        *APIError
		wantStatus int
		wantType   string
		wantCode   string
	}{
		{
			name:       "bad request",
			err:        ErrBadRequest("invalid_model", "model not found"),
			wantStatus: 400,
			wantType:   ErrTypeInvalidRequest,
			wantCode:   "invalid_model",
		},
		{
			name:       "unauthorized",
			err:        ErrUnauthorized("invalid API key"),
			wantStatus: 401,
			wantType:   ErrTypeAuthentication,
			wantCode:   "invalid_api_key",
		},
		{
			name:       "rate limit",
			err:        ErrTooManyRequests("rate limit exceeded"),
			wantStatus: 429,
			wantType:   ErrTypeRateLimit,
			wantCode:   "rate_limit_exceeded",
		},
		{
			name:       "internal",
			err:        ErrInternal("something broke"),
			wantStatus: 500,
			wantType:   ErrTypeServer,
			wantCode:   "internal_error",
		},
		{
			name:       "upstream",
			err:        ErrUpstreamProvider(500, "provider error"),
			wantStatus: 502,
			wantType:   ErrTypeUpstream,
			wantCode:   "provider_500",
		},
		{
			name:       "gateway timeout",
			err:        ErrGatewayTimeout("timed out"),
			wantStatus: 504,
			wantType:   ErrTypeGatewayTimeout,
			wantCode:   "gateway_timeout",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			WriteError(w, tt.err)

			if w.Code != tt.wantStatus {
				t.Errorf("status = %d, want %d", w.Code, tt.wantStatus)
			}

			var resp ErrorResponse
			if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
				t.Fatalf("failed to parse response: %v", err)
			}

			if resp.Error.Type != tt.wantType {
				t.Errorf("type = %q, want %q", resp.Error.Type, tt.wantType)
			}
			if resp.Error.Code != tt.wantCode {
				t.Errorf("code = %q, want %q", resp.Error.Code, tt.wantCode)
			}

			if ct := w.Header().Get("Content-Type"); ct != "application/json" {
				t.Errorf("content-type = %q, want application/json", ct)
			}
		})
	}
}

func TestAPIErrorImplementsError(t *testing.T) {
	err := ErrBadRequest("test", "test message")
	var e error = err
	if e.Error() == "" {
		t.Error("Error() should return non-empty string")
	}
}

func TestWriteErrorFromError(t *testing.T) {
	t.Run("api error", func(t *testing.T) {
		w := httptest.NewRecorder()
		WriteErrorFromError(w, ErrBadRequest("test", "test"))
		if w.Code != http.StatusBadRequest {
			t.Errorf("status = %d, want %d", w.Code, http.StatusBadRequest)
		}
	})

	t.Run("generic error", func(t *testing.T) {
		w := httptest.NewRecorder()
		WriteErrorFromError(w, json.Unmarshal([]byte("invalid"), nil))
		if w.Code != http.StatusInternalServerError {
			t.Errorf("status = %d, want %d", w.Code, http.StatusInternalServerError)
		}
	})
}
