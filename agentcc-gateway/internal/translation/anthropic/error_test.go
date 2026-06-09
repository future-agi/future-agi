package anthropic_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ─── Error translation golden tests ──────────────────────────────────────────

func TestErrorFromCanonical(t *testing.T) {
	tests := []struct {
		name            string
		apiErr          *models.APIError
		wantStatus      int
		wantType        string // Anthropic error type
		wantMsgContains string
	}{
		{
			name:       "invalid_request",
			apiErr:     &models.APIError{Status: 400, Type: models.ErrTypeInvalidRequest, Message: "bad param"},
			wantStatus: 400,
			wantType:   "invalid_request_error",
		},
		{
			name:       "authentication",
			apiErr:     &models.APIError{Status: 401, Type: models.ErrTypeAuthentication, Message: "bad key"},
			wantStatus: 401,
			wantType:   "authentication_error",
		},
		{
			name:       "permission",
			apiErr:     &models.APIError{Status: 403, Type: models.ErrTypePermission, Message: "forbidden"},
			wantStatus: 403,
			wantType:   "permission_error",
		},
		{
			name:       "not_found",
			apiErr:     &models.APIError{Status: 404, Type: models.ErrTypeNotFound, Message: "not found"},
			wantStatus: 404,
			wantType:   "not_found_error",
		},
		{
			name:       "rate_limit",
			apiErr:     &models.APIError{Status: 429, Type: models.ErrTypeRateLimit, Message: "too many requests"},
			wantStatus: 429,
			wantType:   "rate_limit_error",
		},
		{
			name:       "server_error",
			apiErr:     &models.APIError{Status: 500, Type: models.ErrTypeServer, Message: "internal error"},
			wantStatus: 500,
			wantType:   "api_error",
		},
		{
			name:       "overloaded",
			apiErr:     &models.APIError{Status: 503, Type: "overloaded_error", Message: "overloaded"},
			wantStatus: 503,
			wantType:   "overloaded_error",
		},
		{
			name:       "api_error",
			apiErr:     &models.APIError{Status: 500, Type: "api_error", Message: "api error"},
			wantStatus: 500,
			wantType:   "api_error",
		},
		{
			name:       "unknown_type_defaults_to_api_error",
			apiErr:     &models.APIError{Status: 500, Type: "totally_unknown", Message: "mystery"},
			wantStatus: 500,
			wantType:   "api_error",
		},
		{
			name:       "zero_status_uses_default",
			apiErr:     &models.APIError{Status: 0, Type: models.ErrTypeInvalidRequest, Message: "bad"},
			wantStatus: http.StatusBadRequest,
			wantType:   "invalid_request_error",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			status, body, ct := tr.ErrorFromCanonical(tc.apiErr)

			if status != tc.wantStatus {
				t.Errorf("status: want %d, got %d", tc.wantStatus, status)
			}
			if ct != "application/json" {
				t.Errorf("content-type: want application/json, got %q", ct)
			}

			// Parse body.
			var resp map[string]interface{}
			if err := json.Unmarshal(body, &resp); err != nil {
				t.Fatalf("cannot unmarshal error body: %v\nbody: %s", err, body)
			}

			// Top-level type must be "error".
			if resp["type"] != "error" {
				t.Errorf("want type=error, got %v", resp["type"])
			}

			// Nested error.
			errDetail, ok := resp["error"].(map[string]interface{})
			if !ok {
				t.Fatalf("want error object, got %T: %v", resp["error"], resp["error"])
			}
			if errDetail["type"] != tc.wantType {
				t.Errorf("want error.type=%q, got %q", tc.wantType, errDetail["type"])
			}
			if errDetail["message"] != tc.apiErr.Message {
				t.Errorf("want error.message=%q, got %q", tc.apiErr.Message, errDetail["message"])
			}

			if tc.wantMsgContains != "" {
				msg, _ := errDetail["message"].(string)
				if msg == "" || !containsStr(msg, tc.wantMsgContains) {
					t.Errorf("expected message to contain %q, got %q", tc.wantMsgContains, msg)
				}
			}
		})
	}
}

// TestErrorFromCanonical_BodyShape verifies the exact wire shape.
func TestErrorFromCanonical_BodyShape(t *testing.T) {
	apiErr := &models.APIError{
		Status:  400,
		Type:    models.ErrTypeInvalidRequest,
		Message: "max_tokens is required",
	}
	_, body, _ := tr.ErrorFromCanonical(apiErr)

	// Must decode cleanly.
	var resp struct {
		Type  string `json:"type"`
		Error struct {
			Type    string `json:"type"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(body, &resp); err != nil {
		t.Fatalf("cannot unmarshal: %v\nbody: %s", err, body)
	}
	if resp.Type != "error" {
		t.Errorf("want type=error, got %q", resp.Type)
	}
	if resp.Error.Type != "invalid_request_error" {
		t.Errorf("want error.type=invalid_request_error, got %q", resp.Error.Type)
	}
	if resp.Error.Message != "max_tokens is required" {
		t.Errorf("want error.message=%q, got %q", "max_tokens is required", resp.Error.Message)
	}
}

func containsStr(s, substr string) bool {
	return strings.Contains(s, substr)
}
