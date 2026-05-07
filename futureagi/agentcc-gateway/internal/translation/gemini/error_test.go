package gemini_test

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestErrorFromCanonical_AllTypes(t *testing.T) {
	cases := []struct {
		apiErr           *models.APIError
		wantStatusCode   int
		wantGeminiStatus string
	}{
		{
			apiErr:           &models.APIError{Status: http.StatusBadRequest, Type: models.ErrTypeInvalidRequest, Message: "bad field"},
			wantStatusCode:   http.StatusBadRequest,
			wantGeminiStatus: "INVALID_ARGUMENT",
		},
		{
			apiErr:           &models.APIError{Status: http.StatusUnauthorized, Type: models.ErrTypeAuthentication, Message: "bad key"},
			wantStatusCode:   http.StatusUnauthorized,
			wantGeminiStatus: "UNAUTHENTICATED",
		},
		{
			apiErr:           &models.APIError{Status: http.StatusForbidden, Type: models.ErrTypePermission, Message: "denied"},
			wantStatusCode:   http.StatusForbidden,
			wantGeminiStatus: "PERMISSION_DENIED",
		},
		{
			apiErr:           &models.APIError{Status: http.StatusNotFound, Type: models.ErrTypeNotFound, Message: "model not found"},
			wantStatusCode:   http.StatusNotFound,
			wantGeminiStatus: "NOT_FOUND",
		},
		{
			apiErr:           &models.APIError{Status: http.StatusTooManyRequests, Type: models.ErrTypeRateLimit, Message: "rate limit"},
			wantStatusCode:   http.StatusTooManyRequests,
			wantGeminiStatus: "RESOURCE_EXHAUSTED",
		},
		{
			apiErr:           &models.APIError{Status: http.StatusInternalServerError, Type: models.ErrTypeServer, Message: "internal"},
			wantStatusCode:   http.StatusInternalServerError,
			wantGeminiStatus: "INTERNAL",
		},
		{
			apiErr:           &models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeUpstream, Message: "upstream"},
			wantStatusCode:   http.StatusBadGateway,
			wantGeminiStatus: "INTERNAL",
		},
		{
			// Unknown type falls through to UNAVAILABLE
			apiErr:           &models.APIError{Status: http.StatusServiceUnavailable, Type: "overloaded", Message: "overloaded"},
			wantStatusCode:   http.StatusServiceUnavailable,
			wantGeminiStatus: "UNAVAILABLE",
		},
	}

	tr := translator()
	for _, c := range cases {
		statusCode, body, contentType := tr.ErrorFromCanonical(c.apiErr)

		if statusCode != c.wantStatusCode {
			t.Errorf("type=%q: status code got %d, want %d", c.apiErr.Type, statusCode, c.wantStatusCode)
		}
		if contentType != "application/json" {
			t.Errorf("type=%q: content type got %q, want application/json", c.apiErr.Type, contentType)
		}

		var decoded struct {
			Error struct {
				Code    int    `json:"code"`
				Message string `json:"message"`
				Status  string `json:"status"`
			} `json:"error"`
		}
		if err := json.Unmarshal(body, &decoded); err != nil {
			t.Fatalf("type=%q: decode error: %v\nbody: %s", c.apiErr.Type, err, body)
		}
		if decoded.Error.Status != c.wantGeminiStatus {
			t.Errorf("type=%q: status got %q, want %q", c.apiErr.Type, decoded.Error.Status, c.wantGeminiStatus)
		}
		if decoded.Error.Code != c.wantStatusCode {
			t.Errorf("type=%q: error code got %d, want %d", c.apiErr.Type, decoded.Error.Code, c.wantStatusCode)
		}
		if decoded.Error.Message != c.apiErr.Message {
			t.Errorf("type=%q: message got %q, want %q", c.apiErr.Type, decoded.Error.Message, c.apiErr.Message)
		}
	}
}

func TestErrorFromCanonical_ZeroStatusFallback(t *testing.T) {
	apiErr := &models.APIError{Status: 0, Type: models.ErrTypeServer, Message: "whoops"}
	tr := translator()
	statusCode, body, _ := tr.ErrorFromCanonical(apiErr)

	if statusCode != http.StatusInternalServerError {
		t.Errorf("expected 500 fallback for Status=0, got %d", statusCode)
	}
	var decoded struct {
		Error struct {
			Code int `json:"code"`
		} `json:"error"`
	}
	if err := json.Unmarshal(body, &decoded); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if decoded.Error.Code != http.StatusInternalServerError {
		t.Errorf("error.code got %d, want 500", decoded.Error.Code)
	}
}
