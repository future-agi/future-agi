package gemini_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/translation/gemini"
)

// The translated Gemini error body names the gateway code in a
// google.rpc.ErrorInfo detail, as the native GenAI writer does; an error with
// no gateway code carries none.
func TestErrorFromCanonical_ReasonInErrorInfo(t *testing.T) {
	type info struct {
		Type   string `json:"@type"`
		Reason string `json:"reason"`
		Domain string `json:"domain"`
	}
	var body struct {
		Error struct {
			Code    int    `json:"code"`
			Status  string `json:"status"`
			Details []info `json:"details"`
		} `json:"error"`
	}
	tr := gemini.New()
	status, raw, _ := tr.ErrorFromCanonical(&models.APIError{Status: http.StatusForbidden, Type: models.ErrTypePermission, Code: "model_not_permitted", Message: "no"})
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	want := info{Type: "type.googleapis.com/google.rpc.ErrorInfo", Reason: "model_not_permitted", Domain: "agentcc-gateway"}
	if status != http.StatusForbidden || body.Error.Code != 403 || body.Error.Status != "PERMISSION_DENIED" || len(body.Error.Details) != 1 || body.Error.Details[0] != want {
		t.Fatalf("status %d body %s; want 403 PERMISSION_DENIED with details [%+v]", status, raw, want)
	}
	body.Error.Details = nil
	_, raw, _ = tr.ErrorFromCanonical(&models.APIError{Status: http.StatusBadRequest, Type: models.ErrTypeInvalidRequest, Message: "bad"})
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Error.Details) != 0 {
		t.Fatalf("details on an error with no gateway code: %s", raw)
	}
}

// The translated Gemini error body carries no credential from a URL in its
// message, as the native GenAI writer's does not.
func TestErrorFromCanonical_RedactsCredentials(t *testing.T) {
	_, raw, _ := gemini.New().ErrorFromCanonical(&models.APIError{Status: http.StatusBadGateway, Type: models.ErrTypeUpstream,
		Message: `Post "https://user:pw-0003@up.example/v1beta/models/m:generateContent?key=fixture-value-0003": refused`})
	if strings.Contains(string(raw), "pw-0003") || strings.Contains(string(raw), "fixture-value-0003") {
		t.Fatalf("body carries a credential: %s", raw)
	}
	if !strings.Contains(string(raw), "REDACTED") || !strings.Contains(string(raw), "refused") {
		t.Fatalf("body %s, want the credentials REDACTED and the rest kept", raw)
	}
}
