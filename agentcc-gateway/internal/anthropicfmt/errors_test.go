package anthropicfmt

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// decodedError is an Anthropic error body as a client reads it.
type decodedError struct {
	Type  string `json:"type"`
	Error struct {
		Type    string  `json:"type"`
		Message string  `json:"message"`
		Code    *string `json:"code"`
	} `json:"error"`
}

func decodeError(t *testing.T, data []byte) decodedError {
	t.Helper()
	var body decodedError
	if err := json.Unmarshal(data, &body); err != nil {
		t.Fatalf("decoding error body: %v\n%s", err, data)
	}
	if body.Type != "error" {
		t.Errorf("type = %q, want error", body.Type)
	}
	return body
}

func codeOf(body decodedError) string {
	if body.Error.Code == nil {
		return "<absent>"
	}
	return *body.Error.Code
}

// streamEventData is the data line of a single SSE event, which must be an
// error event.
func streamEventData(t *testing.T, frame []byte) []byte {
	t.Helper()
	text := string(frame)
	if !strings.HasPrefix(text, "event: error\ndata: ") || !strings.HasSuffix(text, "\n\n") {
		t.Fatalf("not a whole error event: %q", text)
	}
	return []byte(strings.TrimSuffix(strings.TrimPrefix(text, "event: error\ndata: "), "\n\n"))
}

func TestWriteAPIErrorAnswersAGatewayTimeoutAsTimeoutError(t *testing.T) {
	rec := httptest.NewRecorder()
	WriteAPIError(rec, models.ErrGatewayTimeout("request timed out"))

	if rec.Code != http.StatusGatewayTimeout {
		t.Errorf("status = %d, want 504", rec.Code)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", ct)
	}
	body := decodeError(t, rec.Body.Bytes())
	if body.Error.Type != "timeout_error" {
		t.Errorf("error.type = %q, want timeout_error", body.Error.Type)
	}
	if code := codeOf(body); code != "gateway_timeout" {
		t.Errorf("error.code = %q, want gateway_timeout", code)
	}
	if body.Error.Message != "request timed out" {
		t.Errorf("error.message = %q", body.Error.Message)
	}
}

func TestStreamErrorEventCarriesTheTimeoutTypeAndCode(t *testing.T) {
	frame := StreamErrorEvent(models.ErrGatewayTimeout("request timed out before the stream finished"))

	body := decodeError(t, streamEventData(t, frame))
	if body.Error.Type != "timeout_error" {
		t.Errorf("error.type = %q, want timeout_error", body.Error.Type)
	}
	if code := codeOf(body); code != "gateway_timeout" {
		t.Errorf("error.code = %q, want gateway_timeout", code)
	}
}

// Every other gateway error keeps the type its status gave it before; the
// code it already carried now comes with it.
func TestOtherErrorsKeepTheirTypes(t *testing.T) {
	tests := []struct {
		apiErr   *models.APIError
		wantType string
	}{
		{models.ErrBadRequest("invalid_json", "bad"), "invalid_request_error"},
		{models.ErrUnauthorized("bad key"), "authentication_error"},
		{models.ErrForbidden("not for this key"), "permission_error"},
		{models.ErrNotFound("model_not_found", "no such model"), "not_found_error"},
		{models.ErrTooManyRequests("slow down"), "rate_limit_error"},
		{models.ErrInternal("boom"), "api_error"},
		{models.ErrUpstreamProvider(500, "upstream broke"), "api_error"},
		{models.ErrServiceUnavailable("none left"), "overloaded_error"},
		{&models.APIError{Status: 529, Type: "overloaded_error", Code: "overloaded", Message: "busy"}, "overloaded_error"},
		{models.ErrRequestTimeout("deadline passed before the call"), "api_error"},
		// A 504 that is not the gateway's own timeout keeps api_error.
		{&models.APIError{Status: http.StatusGatewayTimeout, Type: models.ErrTypeUpstream, Code: "provider_504", Message: "upstream 504"}, "api_error"},
		// An upstream that reported its own gateway_timeout, passed on as the
		// gateway's 502, is not this gateway's timeout.
		{&models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeGatewayTimeout, Code: "upstream_gateway_timeout", Message: "upstream timed out"}, "api_error"},
	}
	for _, tc := range tests {
		t.Run(tc.apiErr.Code, func(t *testing.T) {
			rec := httptest.NewRecorder()
			WriteAPIError(rec, tc.apiErr)
			if rec.Code != tc.apiErr.Status {
				t.Errorf("status = %d, want %d", rec.Code, tc.apiErr.Status)
			}
			for name, data := range map[string][]byte{
				"response":     rec.Body.Bytes(),
				"stream event": streamEventData(t, StreamErrorEvent(tc.apiErr)),
			} {
				body := decodeError(t, data)
				if body.Error.Type != tc.wantType {
					t.Errorf("%s: error.type = %q, want %q", name, body.Error.Type, tc.wantType)
				}
				if code := codeOf(body); code != tc.apiErr.Code {
					t.Errorf("%s: error.code = %q, want %q", name, code, tc.apiErr.Code)
				}
			}
		})
	}
}

// An error the gateway built without a code, and the literal WriteError
// callers, send no code field at all.
func TestErrorsWithoutACodeOmitIt(t *testing.T) {
	rec := httptest.NewRecorder()
	WriteError(rec, http.StatusBadRequest, "invalid_request_error", "model is required")
	if body := decodeError(t, rec.Body.Bytes()); body.Error.Code != nil {
		t.Errorf("WriteError: error.code = %q, want it absent", *body.Error.Code)
	}
	if strings.Contains(rec.Body.String(), `"code"`) {
		t.Errorf("WriteError body has a code field: %s", rec.Body.String())
	}

	frame := StreamErrorEvent(&models.APIError{Status: http.StatusBadGateway, Message: "no code"})
	if strings.Contains(string(frame), `"code"`) {
		t.Errorf("codeless stream event has a code field: %s", frame)
	}
}

func TestErrorMessagesAreStillRedacted(t *testing.T) {
	apiErr := &models.APIError{
		Status:  http.StatusGatewayTimeout,
		Type:    models.ErrTypeGatewayTimeout,
		Code:    "gateway_timeout",
		Message: "timed out calling https://api.example.test/v1?key=sk-ant-SECRETSECRETSECRET",
	}
	rec := httptest.NewRecorder()
	WriteAPIError(rec, apiErr)
	frame := StreamErrorEvent(apiErr)
	for name, out := range map[string]string{"response": rec.Body.String(), "stream event": string(frame)} {
		if strings.Contains(out, "SECRETSECRETSECRET") {
			t.Errorf("%s leaks the credential: %s", name, out)
		}
	}
}
