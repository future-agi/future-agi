package server

import (
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// providerTextMarker stands for anything a provider writes into its error:
// it must reach no answer to a provider's 401 or 403.
const providerTextMarker = "provider-text-marker-5b81"

// providerAuthAnswer is the answer written to the client for a provider's
// error with status and message, on a call made with the credential
// owner's rc names.
func providerAuthAnswer(t *testing.T, setOwner func(*models.RequestContext), status int, message string) (*models.APIError, string) {
	t.Helper()
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Provider = "openai"
	setOwner(rc)
	err := providerFailure(rc, &models.APIError{Status: status, Type: "authentication_error", Code: "invalid_api_key",
		Message: message, ProviderStatus: status, UpstreamStatus: status})
	var apiErr *models.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("providerFailure returned %T %v", err, err)
	}
	w := httptest.NewRecorder()
	models.WriteError(w, apiErr)
	return apiErr, w.Body.String()
}

var credentialOwners = []struct {
	name     string
	setOwner func(*models.RequestContext)
	sentence string // the whole message, %d the provider's status
}{
	{"org", func(rc *models.RequestContext) { rc.CredentialSource = "org" },
		`Your provider "openai" rejected the API key saved for it (HTTP %d). Update that key on the Gateway Providers page; your gateway API key is valid.`},
	{"managed", func(rc *models.RequestContext) { rc.Metadata["billing_mode"] = billingManaged },
		`Provider "openai" rejected Future AGI's credential for it (HTTP %d). This is not a problem with your API key; retry later, and contact Future AGI support if it persists.`},
	{"platform", func(rc *models.RequestContext) {},
		`Provider "openai" rejected the credential this gateway is configured with for it (HTTP %d). This is not a problem with your API key; the gateway's operator must update that provider's key.`},
}

// A provider's 401 or 403 is answered 502 provider_auth_failed with the
// gateway's own sentence for whoever owns the credential, and nothing from
// the provider's message: not in the message, not under
// metadata.provider_message, nowhere in the body. metadata.provider_type and
// provider_code keep the provider's error type and code.
func TestProviderAuthFailedCarriesNoProviderText(t *testing.T) {
	for _, owner := range credentialOwners {
		for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden} {
			t.Run(fmt.Sprintf("%s/%d", owner.name, status), func(t *testing.T) {
				got, body := providerAuthAnswer(t, owner.setOwner, status, "Incorrect API key for account "+providerTextMarker)
				if got.Status != http.StatusBadGateway || got.Code != codeProviderAuthFailed {
					t.Fatalf("answered %d %s, want 502 %s", got.Status, got.Code, codeProviderAuthFailed)
				}
				if want := fmt.Sprintf(owner.sentence, status); got.Message != want {
					t.Errorf("message %q, want %q", got.Message, want)
				}
				if meta, _ := got.Metadata.(map[string]any); meta == nil || meta["provider"] != "openai" {
					t.Errorf("metadata %v does not name the provider", got.Metadata)
				} else if _, ok := meta["provider_message"]; ok {
					t.Errorf("metadata carries provider_message: %v", meta)
				}
				if strings.Contains(body, providerTextMarker) {
					t.Errorf("the answer carries the provider's text: %s", body)
				}
			})
		}
	}
}

// Every other provider status keeps the provider's own message, in the
// message and under metadata.provider_message, whoever owns the credential.
func TestOtherProviderFailuresKeepTheProvidersText(t *testing.T) {
	for _, owner := range credentialOwners {
		for _, status := range []int{http.StatusBadRequest, http.StatusTooManyRequests, http.StatusInternalServerError} {
			t.Run(fmt.Sprintf("%s/%d", owner.name, status), func(t *testing.T) {
				got, _ := providerAuthAnswer(t, owner.setOwner, status, "quota for account "+providerTextMarker)
				meta, _ := got.Metadata.(map[string]any)
				if !strings.Contains(got.Message, providerTextMarker) || meta == nil || !strings.Contains(fmt.Sprint(meta["provider_message"]), providerTextMarker) {
					t.Errorf("HTTP %d lost the provider's text: message %q metadata %v", status, got.Message, got.Metadata)
				}
			})
		}
	}
}
