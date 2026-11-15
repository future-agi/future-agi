package server

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestInspectionR3LegacyStreamErrorSanitized(t *testing.T) {
	for _, tc := range []struct{ code, want string }{
		{"streaming_not_supported", "streaming_not_supported"},
		{"raw-upstream-code", "request_failed"},
	} {
		t.Run(tc.code, func(t *testing.T) {
			state := models.NewInspectionState(textInspection("test.legacy-completion"))
			state.BindPrincipal("org", "key")
			state.Require(models.InspectionInput)
			dst := httptest.NewRecorder()
			guard, w := newInspectionWriter(dst, state)
			secret := "uninspected-upstream-content"
			w.Header().Set("Location", secret)
			models.WriteError(w, &models.APIError{Status: 501, Code: tc.code, Type: secret, Message: secret, Param: &secret, RetryAfter: secret})
			_, _ = w.Write([]byte(secret))
			guard.finish()
			var body models.ErrorResponse
			if err := json.Unmarshal(dst.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if dst.Code != 501 || body.Error.Code != tc.want || body.Error.Type != models.ErrTypeServer || body.Error.Param != nil {
				t.Fatalf("status=%d body=%s", dst.Code, dst.Body.String())
			}
			if strings.Contains(dst.Body.String(), secret) || dst.Header().Get("Location") != "" || dst.Header().Get("Retry-After") != "" {
				t.Fatalf("unsanitized body/headers: %s %v", dst.Body.String(), dst.Header())
			}
		})
	}
}
