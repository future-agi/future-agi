package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/connectorcatalog/restmcp"
)

func TestK2OffHidesRuntimeAndHealthDiscovery(t *testing.T) {
	t.Setenv("AGENTCC_MCP_K2_APPS_ENABLED", "false")
	apps, _, err := loadApps(cn4Catalog)
	if err != nil {
		t.Fatal(err)
	}
	srv, err := restmcp.New(apps, restmcp.Options{})
	if err != nil {
		t.Fatal(err)
	}
	h := handler(srv, apps)
	for _, path := range []string{"/gmail/mcp", "/google-calendar/mcp", "/microsoft-outlook/mcp", "/microsoft-teams/mcp", "/arcade/mcp"} {
		for _, method := range []string{"GET", "POST", "OPTIONS"} {
			w := httptest.NewRecorder()
			h.ServeHTTP(w, httptest.NewRequest(method, path, nil))
			if w.Code != http.StatusNotFound {
				t.Fatalf("%s %s = %d", method, path, w.Code)
			}
		}
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", "/healthz", nil))
	var health struct {
		Apps []string `json:"apps"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &health); err != nil {
		t.Fatal(err)
	}
	for _, app := range health.Apps {
		if app == "gmail" || app == "google-calendar" {
			t.Fatalf("OFF app in health discovery: %s", app)
		}
	}
}
