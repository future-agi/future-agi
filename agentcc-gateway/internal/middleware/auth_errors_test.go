package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestErrUnauthorizedKeyReasons(t *testing.T) {
	cases := map[string]string{
		"":                                    ReasonMissingAPIKey,
		"   ":                                 ReasonMissingAPIKey,
		"sk-agentcc-0123":                     ReasonUnknownAPIKey,
		"0123456789abcdef0123456789abcdef":    ReasonFutureAGIAPIKey,
		"0123456789ABCDEF0123456789ABCDEF":    ReasonNotAGatewayKey,
		"0123456789abcdef0123456789abcde":     ReasonNotAGatewayKey,
		"sk-or-v1-not-a-real-key":             ReasonNotAGatewayKey,
		"sk-ant-api03-not-a-real-key":         ReasonNotAGatewayKey,
		"operator-configured-key-of-any-kind": ReasonNotAGatewayKey,
	}
	for key, want := range cases {
		apiErr := ErrUnauthorizedKey(key)
		meta, _ := apiErr.Metadata.(map[string]string)
		if apiErr.Status != http.StatusUnauthorized || apiErr.Code != "invalid_api_key" || apiErr.Type != models.ErrTypeAuthentication || meta["reason"] != want {
			t.Errorf("key %q: %d %s %s %v; want 401 invalid_api_key reason %s", key, apiErr.Status, apiErr.Type, apiErr.Code, apiErr.Metadata, want)
		}
	}
	// Unknown and revoked gateway keys share one answer: it never tells
	// whether a key of the gateway's format exists.
	if ErrUnauthorizedKey("sk-agentcc-aaaa").Message != ErrUnauthorizedKey("sk-agentcc-bbbb").Message {
		t.Fatal("two gateway keys got different answers")
	}
}

func TestGateErrorShapeOnMessages(t *testing.T) {
	for path, want := range map[string]string{
		"/v1/messages":              `{"type":"error","error":{"type":"authentication_error","message":"x"}}` + "\n",
		"/v1/messages/count_tokens": `{"type":"error","error":{"type":"authentication_error","message":"x"}}` + "\n",
		"/v1/chat/completions":      `{"error":{"message":"x","type":"authentication_error","param":null,"code":"invalid_api_key"}}` + "\n",
		"/v1/v1/messages":           `{"error":{"message":"x","type":"authentication_error","param":null,"code":"invalid_api_key"}}` + "\n",
	} {
		w := httptest.NewRecorder()
		writeGateError(w, httptest.NewRequest(http.MethodPost, path, nil), models.ErrUnauthorized("x"))
		if w.Code != http.StatusUnauthorized || w.Body.String() != want || w.Header().Get("WWW-Authenticate") != models.BearerChallenge {
			t.Errorf("%s: %d %q (challenge %q), want %q", path, w.Code, w.Body.String(), w.Header().Get("WWW-Authenticate"), want)
		}
	}
}
