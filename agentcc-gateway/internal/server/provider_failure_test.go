package server

import (
	"errors"
	"net/http"
	"reflect"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

func TestProviderFailureByCredentialOwner(t *testing.T) {
	providerAnswer := func(status int) *models.APIError {
		return &models.APIError{Status: status, Type: "invalid_request_error", Code: "invalid_api_key",
			Message: "Incorrect API key provided", ProviderStatus: status, UpstreamStatus: status}
	}
	cases := []struct {
		owner, name, want string
	}{
		{credentialOrg, "my-openai", `Your provider "my-openai" rejected the API key saved for it (HTTP 401). Update that key on the Gateway Providers page; your gateway API key is valid. The provider said: Incorrect API key provided`},
		{credentialManaged, "fagi-managed", `Provider "fagi-managed" rejected Future AGI's credential for it (HTTP 401). This is not a problem with your API key; retry later, and contact Future AGI support if it persists. The provider said: Incorrect API key provided`},
		{credentialGateway, "openai", `Provider "openai" rejected the credential this gateway is configured with for it (HTTP 401). This is not a problem with your API key; the gateway's operator must update that provider's key. The provider said: Incorrect API key provided`},
		{credentialOrg, "", `Your provider rejected the API key saved for it (HTTP 401). Update that key on the Gateway Providers page; your gateway API key is valid. The provider said: Incorrect API key provided`},
	}
	for _, c := range cases {
		got := attributeProviderFailure(providerAnswer(http.StatusUnauthorized), c.name, c.owner)
		if got.Status != http.StatusBadGateway || got.Code != codeProviderAuthFailed || got.Type != models.ErrTypeUpstream || got.Message != c.want {
			t.Errorf("%s/%s: %d %s %s %q", c.owner, c.name, got.Status, got.Type, got.Code, got.Message)
		}
		wantMeta := map[string]any{"provider": c.name, "provider_status": 401, "provider_message": "Incorrect API key provided",
			"provider_type": "invalid_request_error", "provider_code": "invalid_api_key"}
		if !reflect.DeepEqual(got.Metadata, wantMeta) {
			t.Errorf("%s: metadata %v, want %v", c.owner, got.Metadata, wantMeta)
		}
	}
}

func TestProviderFailureKeepsWellFormedCodesAndOtherErrors(t *testing.T) {
	// A provider's own well-formed code is kept: clients match on it.
	e := &models.APIError{Status: 400, Type: "invalid_request_error", Code: "context_length_exceeded",
		Message: "too long", ProviderStatus: 400, UpstreamStatus: 400}
	got := attributeProviderFailure(e, "openai", credentialOrg)
	if got.Status != 400 || got.Code != "context_length_exceeded" || got.Type != "invalid_request_error" ||
		got.Message != `Provider "openai" returned HTTP 400: too long` {
		t.Errorf("400: %+v", got)
	}
	// The bare "provider_" is replaced by provider_<status>.
	e = &models.APIError{Status: 502, Type: "", Code: "provider_", Message: "no route", ProviderStatus: 404}
	if got := attributeProviderFailure(e, "p", credentialGateway); got.Code != "provider_404" || got.Type != models.ErrTypeUpstream {
		t.Errorf("bare provider_: %+v", got)
	}
	// A provider that never answered.
	e = models.ErrUpstreamProvider(0, "dial tcp: connection refused")
	if got := attributeProviderFailure(e, "p", credentialOrg); got.Code != "provider_0" || got.Message != `Provider "p" could not be reached: dial tcp: connection refused` {
		t.Errorf("unreachable: %+v", got)
	}
	// A raw-body 401 the adapter could not parse is an auth failure too.
	e = models.ErrUpstreamProvider(401, "provider error (HTTP 401): nope")
	if got := attributeProviderFailure(e, "p", credentialOrg); got.Status != http.StatusBadGateway || got.Code != codeProviderAuthFailed {
		t.Errorf("raw 401: %+v", got)
	}
	// The gateway's own errors are unchanged, and the input is never modified.
	own := models.ErrForbidden("nope")
	if got := providerFailure(&models.RequestContext{Provider: "p", Metadata: map[string]string{}}, own); got != own {
		t.Errorf("gateway error rewritten: %+v", got)
	}
	answer := &models.APIError{Status: 401, Message: "bad key", ProviderStatus: 401}
	wrapped := routing.SkipHop(answer, "deadline")
	got = providerFailure(&models.RequestContext{Provider: "p", Metadata: map[string]string{}}, wrapped).(*models.APIError)
	if got.Code != codeProviderAuthFailed || answer.Status != 401 || answer.Provider != "" {
		t.Errorf("wrapped: %+v; input now %+v", got, answer)
	}
	if providerFailure(nil, nil) != nil {
		t.Error("nil error rewritten")
	}
	plain := errors.New("x")
	if providerFailure(nil, plain) != plain {
		t.Error("plain error rewritten")
	}
}

func TestStampProviderKeepsTheFirstProvider(t *testing.T) {
	e := &models.APIError{Status: 500, ProviderStatus: 500}
	stampProvider(e, "primary")
	stampProvider(e, "fallback")
	if e.Provider != "primary" {
		t.Fatalf("provider %q, want the first stamp", e.Provider)
	}
	own := models.ErrForbidden("nope")
	stampProvider(own, "p")
	if own.Provider != "" {
		t.Fatal("a gateway error was stamped")
	}
}

func TestModelSuggestionHelpers(t *testing.T) {
	if d := editDistance("gpt-4o-minii", "gpt-4o-mini", 4); d != 1 {
		t.Errorf("distance %d", d)
	}
	if d := editDistance("gpt4o", "gpt-4o", 2); d != 1 {
		t.Errorf("distance %d", d)
	}
	if d := editDistance("abc", "abcdefgh", 2); d <= 2 {
		t.Errorf("distance %d over the limit", d)
	}
	for n, want := range map[int]string{
		1: `model "x" was not found: no provider your organization can use serves it. Did you mean "a"? GET /v1/models lists the models this key can call.`,
		2: `model "x" was not found: no provider your organization can use serves it. Did you mean "a" or "b"? GET /v1/models lists the models this key can call.`,
		3: `model "x" was not found: no provider your organization can use serves it. Did you mean "a", "b" or "c"? GET /v1/models lists the models this key can call.`,
	} {
		e := errModelNotFound("x", []string{"a", "b", "c"}[:n])
		if e.Status != http.StatusNotFound || e.Code != "model_not_found" || e.Message != want {
			t.Errorf("%d suggestions: %d %s %q", n, e.Status, e.Code, e.Message)
		}
	}
}
