package pipeline

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestSanitizeReflectionError(t *testing.T) {
	private := "withheld answer and private feedback"
	for _, tc := range []struct {
		status   int
		typeName string
		code     string
	}{
		{400, models.ErrTypeInvalidRequest, "provider_400"},
		{401, models.ErrTypeAuthentication, "invalid_api_key"},
		{403, models.ErrTypePermission, "permission_denied"},
		{404, models.ErrTypeNotFound, "provider_404"},
		{408, models.ErrTypeTimeout, "request_timeout"},
		{422, models.ErrTypeUpstream, "provider_422"},
		{429, models.ErrTypeRateLimit, "rate_limit_exceeded"},
		{500, models.ErrTypeServer, "internal_error"},
		{502, models.ErrTypeUpstream, "provider_502"},
		{503, models.ErrTypeServer, "all_providers_unavailable"},
		{504, models.ErrTypeGatewayTimeout, "gateway_timeout"},
	} {
		t.Run(fmt.Sprint(tc.status), func(t *testing.T) {
			param := private
			upstream := &models.APIError{Status: tc.status, Type: private, Code: private, Message: private, Param: &param}
			for _, err := range []error{upstream, fmt.Errorf("%s: %w", private, upstream)} {
				safe := sanitizeReflectionError(err)
				var apiErr *models.APIError
				if !errors.As(safe, &apiErr) || apiErr.Message != "Provider request failed during response repair" || apiErr.Param != nil || strings.Contains(safe.Error(), private) {
					t.Fatalf("unsafe repair error: %v", safe)
				}
				if apiErr.Status != tc.status || apiErr.Type != tc.typeName || apiErr.Code != tc.code {
					t.Fatalf("incorrect canonical error: %+v", apiErr)
				}
			}
			if upstream.Message != private || upstream.Type != private || upstream.Code != private || upstream.Param == nil {
				t.Fatal("sanitization mutated the original error")
			}
		})
	}
	safe := sanitizeReflectionError(errors.New(private))
	var apiErr *models.APIError
	if !errors.As(safe, &apiErr) || apiErr.Status != 502 || apiErr.Type != models.ErrTypeUpstream || apiErr.Code != "provider_502" || strings.Contains(safe.Error(), private) {
		t.Fatalf("unsafe untyped repair error: %v", safe)
	}
	admission := &models.AdmissionError{Err: models.ErrTooManyRequests("local budget exhausted")}
	for _, err := range []error{admission, context.Canceled, context.DeadlineExceeded} {
		if sanitizeReflectionError(err) != err {
			t.Fatalf("safe local error changed: %v", err)
		}
	}
}

type attemptModelRecorder struct {
	requested, resolved, admitted string
}

func (p *attemptModelRecorder) Name() string              { return "budget" }
func (p *attemptModelRecorder) Priority() int             { return 40 }
func (p *attemptModelRecorder) IsAttemptAccounting() bool { return true }
func (p *attemptModelRecorder) IsRetryAdmission() bool    { return true }
func (p *attemptModelRecorder) ProcessRequest(_ context.Context, rc *models.RequestContext) PluginResult {
	p.admitted = rc.Model
	return ResultContinue()
}
func (p *attemptModelRecorder) ProcessResponse(_ context.Context, rc *models.RequestContext) PluginResult {
	p.requested, p.resolved = rc.Model, rc.ResolvedModel
	return ResultContinue()
}

func TestEndProviderAttemptPreservesRequestedAndServingModels(t *testing.T) {
	for _, requested := range []string{"deployment-x", ""} {
		t.Run(requested, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model = "caller-alias"
			rc.Request = &models.ChatCompletionRequest{Model: requested}
			rc.ResolvedModel = "serving-snapshot"
			rc.Attempts = &models.AttemptLedger{Open: true}
			recorder := &attemptModelRecorder{}
			NewEngine(recorder).EndProviderAttempt(context.Background(), rc)
			if recorder.requested != "caller-alias" || recorder.resolved != "serving-snapshot" || rc.Model != "caller-alias" || rc.ResolvedModel != "serving-snapshot" {
				t.Fatalf("settlement models=%+v context models=%s/%s", recorder, rc.Model, rc.ResolvedModel)
			}
			if len(rc.Attempts.Attempts) != 1 || rc.Attempts.Attempts[0].Model != "serving-snapshot" {
				t.Fatalf("incorrect serving-model ledger: %+v", rc.Attempts)
			}
		})
	}
}

func TestEndProviderAttemptPricesModellessResponseByUpstreamModel(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "chat-alias"
	rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o"}
	rc.Attempts = &models.AttemptLedger{Open: true}
	recorder := &attemptModelRecorder{}
	NewEngine(recorder).EndProviderAttempt(context.Background(), rc)
	if recorder.requested != "chat-alias" || recorder.resolved != "gpt-4o" {
		t.Fatalf("modelless response settled with models=%+v", recorder)
	}
	if rc.Model != "chat-alias" || rc.ResolvedModel != "" {
		t.Fatalf("settlement leaked models into the context: %s/%s", rc.Model, rc.ResolvedModel)
	}
	if len(rc.Attempts.Attempts) != 1 || rc.Attempts.Attempts[0].Model != "gpt-4o" {
		t.Fatalf("incorrect serving-model ledger: %+v", rc.Attempts)
	}
}

func TestBeginProviderAttemptPreservesAdmissionModel(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model = "gpt-4o"
	rc.Request = &models.ChatCompletionRequest{Model: "deployment-x"}
	rc.Attempts = &models.AttemptLedger{Started: 1}
	recorder := &attemptModelRecorder{}
	if err := NewEngine(recorder).BeginProviderAttempt(context.Background(), rc); err != nil {
		t.Fatal(err)
	}
	if recorder.admitted != "gpt-4o" || rc.Model != "gpt-4o" || rc.Request.Model != "deployment-x" || rc.Attempts.Started != 2 {
		t.Fatalf("incorrect retry admission: recorder=%+v model=%s request=%s ledger=%+v", recorder, rc.Model, rc.Request.Model, rc.Attempts)
	}
}
