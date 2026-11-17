package server

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/routebinding"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestAdminPublicationRefusalNoAcknowledgement(t *testing.T) {
	for _, kind := range []string{"set", "delete", "bulk"} {
		t.Run(kind, func(t *testing.T) {
			s := tenant.NewStore()
			g, err := routebinding.NewGate()
			if err != nil {
				t.Fatal("gate setup failed")
			}
			if s.BindNativeLifecycle(g) != nil {
				t.Fatal("native ownership unavailable")
			}
			if g.Close() != nil {
				t.Fatal("gate close failed")
			}
			callbacks := 0
			h := NewOrgConfigHandlers(s, "synthetic", nil)
			h.SetOnConfigChange(func(string) { callbacks++ })
			body := "{}"
			if kind == "bulk" {
				body = `{"org":{}}`
			}
			r := httptest.NewRequest(http.MethodPut, "/-/orgs/org/config?org_id=org", strings.NewReader(body))
			r.Header.Set("Authorization", "Bearer synthetic")
			w := httptest.NewRecorder()
			switch kind {
			case "set":
				h.SetOrgConfig(w, r)
			case "delete":
				h.DeleteOrgConfig(w, r)
			case "bulk":
				h.BulkLoadOrgConfigs(w, r)
			}
			if w.Code != http.StatusServiceUnavailable || callbacks != 0 || s.Count() != 0 || !strings.Contains(w.Body.String(), "tenant_config_publication_refused") {
				t.Fatal("failed publication acknowledged or successful side effect ran")
			}
		})
	}
}

func TestAdminPublicationErrorEnvelope(t *testing.T) {
	for _, tc := range []struct {
		name    string
		err     error
		status  int
		kind    string
		code    string
		message string
	}{
		{"unsupported", tenant.ErrConfigGraph, http.StatusBadRequest, "invalid_request_error", "tenant_config_unsupported", "This configuration isn't supported by the gateway."},
		{"preparation", tenant.ErrConfigPreparation, http.StatusInternalServerError, "server_error", "tenant_config_preparation_failed", "The gateway couldn't prepare the configuration."},
		{"lifecycle", tenant.ErrNativeLifecycle, http.StatusServiceUnavailable, "server_error", "tenant_config_publication_refused", "The gateway couldn't complete the configuration request."},
		{"unknown", errors.New("private synthetic failure"), http.StatusServiceUnavailable, "server_error", "tenant_config_publication_refused", "The gateway couldn't complete the configuration request."},
		{"nil", nil, http.StatusServiceUnavailable, "server_error", "tenant_config_publication_refused", "The gateway couldn't complete the configuration request."},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			writeOrgPublicationError(w, tc.err)
			if w.Code != tc.status || w.Header().Get("Retry-After") != "" {
				t.Fatal("publication error status or retry contract changed")
			}
			var body struct {
				Error struct {
					Type    string `json:"type"`
					Code    string `json:"code"`
					Message string `json:"message"`
				} `json:"error"`
			}
			if json.Unmarshal(w.Body.Bytes(), &body) != nil {
				t.Fatal("publication error envelope is not JSON")
			}
			if body.Error.Type != tc.kind || body.Error.Code != tc.code || body.Error.Message != tc.message {
				t.Fatal("publication error envelope differs from the approved contract")
			}
			if strings.Contains(w.Body.String(), "private synthetic failure") {
				t.Fatal("publication error exposed its underlying cause")
			}
		})
	}
}
