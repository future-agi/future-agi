package external

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestCrowdStrike_ValidToken(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth2/token" {
			json.NewEncoder(w).Encode(map[string]interface{}{"access_token": "cs-token", "expires_in": 1799})
			return
		}
		if r.Header.Get("Authorization") != "Bearer cs-token" {
			t.Errorf("auth header = %q", r.Header.Get("Authorization"))
		}
		json.NewEncoder(w).Encode(map[string]interface{}{"result": map[string]interface{}{"blocked": false}})
	}))
	defer srv.Close()

	g := New("test-crowdstrike", map[string]interface{}{
		"provider": "crowdstrike", "client_id": "id", "client_secret": "secret", "base_url": srv.URL,
	})

	result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
	if !result.Pass {
		t.Fatalf("expected pass: %s", result.Message)
	}
}

func TestCrowdStrike_MissingAccessToken(t *testing.T) {
	for name, tokenBody := range map[string]map[string]interface{}{
		"missing": {"expires_in": 1799},
		"empty":   {"access_token": "", "expires_in": 1799},
	} {
		t.Run(name, func(t *testing.T) {
			guardCalls := 0
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/oauth2/token" {
					json.NewEncoder(w).Encode(tokenBody)
					return
				}
				guardCalls++
				json.NewEncoder(w).Encode(map[string]interface{}{"result": map[string]interface{}{"blocked": false}})
			}))
			defer srv.Close()

			g := New("test-crowdstrike", map[string]interface{}{
				"provider": "crowdstrike", "client_id": "id", "client_secret": "secret", "base_url": srv.URL,
			})

			result := g.Check(context.Background(), makeInput([]models.Message{makeMsg("user", "hi")}))
			if guardCalls != 0 {
				t.Errorf("guard endpoint called %d times with an empty bearer", guardCalls)
			}
			if result.Pass {
				t.Fatal("expected fail when the token reply has no access_token")
			}
			if !contains(result.Message, "crowdstrike auth failed") {
				t.Errorf("message = %q", result.Message)
			}
		})
	}
}
