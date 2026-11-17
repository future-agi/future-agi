package genaifmt

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

type googleErrorInfo struct {
	Type   string `json:"@type"`
	Reason string `json:"reason"`
	Domain string `json:"domain"`
}

type googleErrorBody struct {
	Error struct {
		Code    int               `json:"code"`
		Status  string            `json:"status"`
		Details []googleErrorInfo `json:"details"`
	} `json:"error"`
}

// A gateway error leaving in Google's format names its stable gateway code in
// a google.rpc.ErrorInfo detail, as a provider-attributed error already does:
// error.code stays the numeric HTTP status and error.status the RPC status.
// An error with no gateway code carries no detail.
func TestWriteAPIError_ReasonInErrorInfo(t *testing.T) {
	for _, tc := range []struct {
		name   string
		err    *models.APIError
		status string
		reason string
	}{
		{"permission", &models.APIError{Status: http.StatusForbidden, Type: models.ErrTypePermission, Code: "permission_denied", Message: "denied"}, "PERMISSION_DENIED", "permission_denied"},
		{"rate limit", &models.APIError{Status: http.StatusTooManyRequests, Type: models.ErrTypeRateLimit, Code: "budget_exceeded", Message: "over"}, "RESOURCE_EXHAUSTED", "budget_exceeded"},
		{"no code", &models.APIError{Status: http.StatusBadRequest, Type: models.ErrTypeInvalidRequest, Message: "bad"}, "INVALID_ARGUMENT", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			WriteAPIError(w, tc.err)
			var got googleErrorBody
			if err := json.Unmarshal(w.Body.Bytes(), &got); err != nil {
				t.Fatalf("body %s: %v", w.Body.String(), err)
			}
			if w.Code != tc.err.Status || got.Error.Code != tc.err.Status || got.Error.Status != tc.status {
				t.Fatalf("HTTP %d, code %d, status %q; want %d, %d, %q: %s", w.Code, got.Error.Code, got.Error.Status, tc.err.Status, tc.err.Status, tc.status, w.Body.String())
			}
			if tc.reason == "" {
				if len(got.Error.Details) != 0 {
					t.Fatalf("details %+v on an error with no gateway code", got.Error.Details)
				}
				return
			}
			want := googleErrorInfo{Type: "type.googleapis.com/google.rpc.ErrorInfo", Reason: tc.reason, Domain: "agentcc-gateway"}
			if len(got.Error.Details) != 1 || got.Error.Details[0] != want {
				t.Fatalf("details %+v, want [%+v]", got.Error.Details, want)
			}
		})
	}
}

// A GenAI stream error event carries the same ErrorInfo detail and a
// credential-redacted message.
func TestStreamErrorEvent_ReasonAndRedaction(t *testing.T) {
	frame := string(StreamErrorEvent(&models.APIError{Status: http.StatusForbidden, Code: "content_blocked",
		Message: `blocked at "https://u:pw-0002@up.example/x?key=fixture-value-0002"`}))
	if !strings.HasPrefix(frame, "data: ") || !strings.HasSuffix(frame, "\n\n") {
		t.Fatalf("frame %q is not one SSE data frame", frame)
	}
	if strings.Contains(frame, "pw-0002") || strings.Contains(frame, "fixture-value-0002") {
		t.Fatalf("frame carries a credential: %s", frame)
	}
	var got googleErrorBody
	if err := json.Unmarshal([]byte(strings.TrimSuffix(strings.TrimPrefix(frame, "data: "), "\n\n")), &got); err != nil {
		t.Fatalf("frame %s: %v", frame, err)
	}
	want := googleErrorInfo{Type: "type.googleapis.com/google.rpc.ErrorInfo", Reason: "content_blocked", Domain: "agentcc-gateway"}
	if got.Error.Code != 403 || got.Error.Status != "PERMISSION_DENIED" || len(got.Error.Details) != 1 || got.Error.Details[0] != want {
		t.Fatalf("frame %s, want 403 PERMISSION_DENIED with details [%+v]", frame, want)
	}
	if frame := string(StreamErrorEvent(&models.APIError{Status: http.StatusBadRequest, Message: "bad"})); strings.Contains(frame, "details") {
		t.Fatalf("details on an error with no gateway code: %s", frame)
	}
}
