package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestSetOrgConfigGuardrailCheckStage(t *testing.T) {
	tests := []struct {
		name   string
		field  string
		status int
	}{
		{"stage is part of the contract", `"stage":"both"`, http.StatusOK},
		{"unknown check fields are still rejected", `"phase":"both"`, http.StatusBadRequest},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			store := tenant.NewStore()
			h := NewOrgConfigHandlers(store, "admin-token", nil)
			body := `{"guardrails":{"checks":{"vendor-check":{"enabled":true,"action":"block",` + tt.field +
				`,"config":{"provider":"bedrock_guardrails"}}}}}`
			req := httptest.NewRequest(http.MethodPut, "/-/orgs/org-1/config?org_id=org-1", strings.NewReader(body))
			req.Header.Set("Authorization", "Bearer admin-token")
			rec := httptest.NewRecorder()

			h.SetOrgConfig(rec, req)

			if rec.Code != tt.status {
				t.Fatalf("status = %d, want %d: %s", rec.Code, tt.status, rec.Body)
			}
			if tt.status != http.StatusOK {
				return
			}
			cfg := store.Get("org-1")
			if cfg == nil || cfg.Guardrails == nil || cfg.Guardrails.Checks["vendor-check"] == nil {
				t.Fatalf("stored config = %#v", cfg)
			}
			if got := cfg.Guardrails.Checks["vendor-check"].Stage; got != "both" {
				t.Errorf("stored stage = %q, want %q", got, "both")
			}
		})
	}
}
