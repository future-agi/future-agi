package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/restmcp"
)

// This test uses only pre-existing symbols, so a base failure is behavioral
// (404 instead of 401), not a missing-package/symbol compilation failure.
func TestRuntimeSupportRouteAuthentication(t *testing.T) {
	t.Setenv("AGENTCC_ADMIN_TOKEN", "unit-only-0123456789abcdef0123456789abcdef")
	apps, _, err := loadApps(cn4Catalog)
	if err != nil {
		t.Fatal(err)
	}
	srv, err := restmcp.New(apps, restmcp.Options{})
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest(http.MethodPost, "/.well-known/connector-runtime-support", strings.NewReader("{}"))
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	handler(srv, apps).ServeHTTP(w, r)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("support route status %d, want 401", w.Code)
	}
}
