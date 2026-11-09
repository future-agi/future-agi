package capability

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestOptBPublicationOwnerSurvivesLaterHop(t *testing.T) {
	for _, owner := range []models.CredentialType{models.CredentialBYOK, models.CredentialManaged} {
		e := &models.APIError{Status: 400, ProviderStatus: 400, Provider: "first", Message: "original diagnostic", Type: "vendor_type", Code: "vendor_code"}
		e.BindPublicationCredentialOwner(owner)
		// The final request state is deliberately the opposite of the original.
		source := "org"
		if owner == models.CredentialBYOK {
			source = "platform"
		}
		rc := &models.RequestContext{Provider: "last", CredentialSource: source}
		if got := PublicationOwner(rc, e); got != owner {
			t.Fatal("later request state replaced error ownership")
		}
		out := PublishProviderError(e, PublicationOwner(rc, e), 400, "req", nil)
		if owner == models.CredentialBYOK && out.Message != e.Message {
			t.Fatal("source-owned BYOK diagnostic changed")
		}
		if owner == models.CredentialManaged && (out.Message == e.Message || out.Code == e.Code || out.Type == e.Type) {
			t.Fatal("platform provider fields were retained")
		}
		if e.Message != "original diagnostic" || e.Code != "vendor_code" {
			t.Fatal("internal error mutated")
		}
	}
}

func TestOptBPublicationRedactsEveryTextLeaf(t *testing.T) {
	param := "private"
	e := &models.APIError{Status: 400, Message: "private", Code: "private", Type: "private", Param: &param, Metadata: map[string]any{"private": []any{map[string]string{"x": "private"}}, "number": json.Number("9007199254740993")}}
	out := RedactPublishedError(e, func(s string) string { return strings.ReplaceAll(s, "private", "MASK") })
	data, err := json.Marshal(models.ErrorDetail{Message: out.Message, Code: out.Code, Type: out.Type, Param: out.Param, Metadata: out.Metadata})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), "private") || !strings.Contains(string(data), "9007199254740993") {
		t.Fatal("text redaction or numeric preservation failed")
	}
	if param != "private" || e.Message != "private" {
		t.Fatal("input mutated")
	}
}
