package server

import (
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestOptBR4DGatewayFieldsSurviveProjection(t *testing.T) {
	rc := &models.RequestContext{Provider: "fixture", CredentialSource: "platform", RequestID: r4dRequestID}
	for _, status := range []int{0, 400, 401, 403, 429, 502} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			original := models.ErrUpstreamProvider(status, r4dPlatformCanary)
			original.RetryAfterSec = 7
			got := providerFailure(rc, original).(*models.APIError)
			wantCode := fmt.Sprintf("provider_%d", status)
			if status == 401 || status == 403 {
				wantCode = codeProviderAuthFailed
			}
			if got.Status != 502 || got.Type != models.ErrTypeUpstream || got.Code != wantCode || got.RetryAfterSec != 7 {
				t.Error("projection changed gateway-assigned status/type/code/retry header")
			}
			if strings.Contains(got.Message, r4dPlatformCanary) || original.Message != r4dPlatformCanary {
				t.Error("publication leaked or mutated the original diagnostic")
			}
		})
	}
	own := models.ErrBadRequest("gateway_validation_code", "gateway validation message")
	if got := providerFailure(rc, own); got != own {
		t.Error("well-formed gateway error changed")
	}
	original := &models.APIError{Status: 400, ProviderStatus: 400, Message: r4dPlatformCanary, Code: r4dPlatformCanary, Type: r4dPlatformCanary}
	got := providerFailure(rc, original).(*models.APIError)
	if got.Code == original.Code || got.Type == original.Type {
		t.Error("provider fields were incorrectly treated as gateway-assigned")
	}
	native := nativeProviderFailure(rc, 404, []byte(`{"error":{"message":"`+r4dPlatformCanary+`","code":"`+r4dPlatformCanary+`","type":"`+r4dPlatformCanary+`"}}`))
	if native.Status != 404 || native.Code != "provider_404" || strings.Contains(native.Message, r4dPlatformCanary) {
		t.Error("native gateway status code or projection regressed")
	}
}

func TestOptBR4DRedactorUsesAuthenticatedOrgFirst(t *testing.T) {
	store := tenant.NewStore()
	for _, org := range []string{"authenticated", "resolved"} {
		store.Set(org, &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{Enabled: true, Mode: "patterns", Patterns: []*tenant.RedactPatternConfig{{Name: org, Pattern: "private"}}}})
	}
	h := &Handlers{tenantStore: store}
	for _, authenticated := range []bool{false, true} {
		rc := models.AcquireRequestContext()
		rc.Metadata["org_id"] = "resolved"
		want := "resolved"
		if authenticated {
			rc.Metadata[tenant.MetadataKeyOrgID] = "authenticated"
			want = "authenticated"
		}
		h.bindErrorRedactor(rc)
		if rc.ErrorTextRedactor == nil || rc.ErrorTextRedactor("private") != "[REDACTED:"+want+"]" {
			t.Error("wrong org error redactor bound")
		}
		rc.Release()
	}
}

func TestOptBR4DRealtimeLogUsesOrgOrGlobalRedactor(t *testing.T) {
	for _, org := range []bool{false, true} {
		t.Run(fmt.Sprint(org), func(t *testing.T) {
			store := tenant.NewStore()
			h := &Handlers{tenantStore: store}
			h.configureErrorRedactor(config.PrivacyConfig{Enabled: true, Mode: "patterns", Patterns: []config.RedactPatternConfig{{Name: "global", Pattern: "upstream_unavailable"}}})
			want := "global"
			if org {
				store.Set("fixture", &tenant.OrgConfig{Privacy: &tenant.PrivacyConfig{Enabled: true, Mode: "patterns", Patterns: []*tenant.RedactPatternConfig{{Name: "org", Pattern: "upstream_unavailable"}}}})
				want = "org"
			}
			var logs r4dLogBuffer
			rh := &RealtimeHandler{logger: slog.New(slog.NewJSONHandler(&logs, nil)), bindErrorRedactor: h.bindErrorRedactor}
			client := rh.providerSetupFailure(r4dRequestID, "fixture", "provider", "dial", errors.New(r4dPlatformCanary))
			if strings.Contains(logs.text(), r4dPlatformCanary) || !strings.Contains(logs.text(), "[REDACTED:"+want+"]") {
				t.Error("realtime log did not use provider projection and configured redactor")
			}
			if client.Code != "realtime_provider_dial_failed" || !strings.Contains(client.Message, r4dRequestID) {
				t.Error("configured log redactor changed value-free client error shape")
			}
		})
	}
}
