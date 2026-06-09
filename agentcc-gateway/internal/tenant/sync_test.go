package tenant

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestSyncFromControlPlane_Success(t *testing.T) {
	// Mock Django bulk endpoint.
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/agentcc/org-configs/bulk/" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer test-token" {
			t.Errorf("unexpected auth: %s", r.Header.Get("Authorization"))
		}

		resp := map[string]interface{}{
			"status": true,
			"result": map[string]interface{}{
				"org-1": map[string]interface{}{
					"providers": map[string]interface{}{
						"openai": map[string]interface{}{
							"api_key": "sk-org1",
							"enabled": true,
						},
					},
				},
				"org-2": map[string]interface{}{
					"routing": map[string]interface{}{
						"strategy":      "least_latency",
						"default_model": "gpt-4o",
					},
				},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	}))
	defer ts.Close()

	store := NewStore()
	err := SyncFromControlPlane(context.Background(), ts.URL, "test-token", store)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if store.Count() != 2 {
		t.Fatalf("expected 2 orgs, got %d", store.Count())
	}

	cfg1 := store.Get("org-1")
	if cfg1 == nil {
		t.Fatal("org-1 config not found")
	}
	if cfg1.Providers == nil || cfg1.Providers["openai"] == nil {
		t.Fatal("org-1 should have openai provider")
	}
	if cfg1.Providers["openai"].APIKey != "sk-org1" {
		t.Errorf("org-1 openai key = %q, want sk-org1", cfg1.Providers["openai"].APIKey)
	}

	cfg2 := store.Get("org-2")
	if cfg2 == nil {
		t.Fatal("org-2 config not found")
	}
	if cfg2.Routing == nil {
		t.Fatal("org-2 should have routing config")
	}
	if cfg2.Routing.Strategy != "least_latency" {
		t.Errorf("org-2 strategy = %q, want least_latency", cfg2.Routing.Strategy)
	}
}

func TestSyncFromControlPlane_EmptyURL(t *testing.T) {
	store := NewStore()
	err := SyncFromControlPlane(context.Background(), "", "token", store)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if store.Count() != 0 {
		t.Errorf("expected empty store, got %d", store.Count())
	}
}

func TestSyncFromControlPlane_ServerDown(t *testing.T) {
	store := NewStore()
	// Use a URL that will fail to connect.
	err := SyncFromControlPlane(context.Background(), "http://127.0.0.1:1", "token", store)
	if err != nil {
		t.Fatalf("expected nil error (non-fatal), got: %v", err)
	}
	if store.Count() != 0 {
		t.Errorf("expected empty store, got %d", store.Count())
	}
}

func TestSyncFromControlPlane_Non200(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte(`{"error":"server error"}`))
	}))
	defer ts.Close()

	store := NewStore()
	err := SyncFromControlPlane(context.Background(), ts.URL, "token", store)
	if err != nil {
		t.Fatalf("expected nil error (non-fatal), got: %v", err)
	}
	if store.Count() != 0 {
		t.Errorf("expected empty store, got %d", store.Count())
	}
}

func TestSyncFromControlPlane_StatusFalse(t *testing.T) {
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": false,
			"result": nil,
		})
	}))
	defer ts.Close()

	store := NewStore()
	err := SyncFromControlPlane(context.Background(), ts.URL, "token", store)
	if err != nil {
		t.Fatalf("expected nil error, got: %v", err)
	}
	if store.Count() != 0 {
		t.Errorf("expected empty store, got %d", store.Count())
	}
}
