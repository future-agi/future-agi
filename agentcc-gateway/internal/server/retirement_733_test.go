package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/batch"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

func syncRetirement733(t *testing.T, ks *auth.KeyStore, hash string) {
	t.Helper()
	app := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": true, "result": []interface{}{},
			"retired":          []map[string]string{{"id": "retired-person", "key_hash": hash}},
			"config_conflicts": []string{hash},
			"snapshot":         map[string]interface{}{"complete": true, "issued_ever": 1},
		})
	}))
	defer app.Close()
	if err := auth.SyncKeysFromControlPlane(context.Background(), app.URL, "", ks); err != nil {
		t.Fatal(err)
	}
}

func Test733RetiredHashRefusedAcrossRoutesEmptySyncAndRestart(t *testing.T) {
	// The same config and persistent negative response are reused on restart.
	const raw = "sk-retirement-fixture-not-a-deployment-credential"
	hash := auth.HashKey(raw)
	for boot := 0; boot < 2; boot++ {
		srv := newMCPTestServer(t, true, func(cfg *config.Config) {
			cfg.ControlPlane.URL = "http://127.0.0.1:9"
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "deployment", Key: raw, KeyType: "internal"}}
		})
		t.Cleanup(srv.batchProcessor.Stop)
		before := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(`{}`))
		req.Header.Set("Authorization", "Bearer "+raw)
		srv.httpServer.Handler.ServeHTTP(before, req)
		if before.Code != 503 || !strings.Contains(before.Body.String(), "key_list_not_loaded") {
			t.Fatalf("before first list: %d %s", before.Code, before.Body.String())
		}
		for _, phase := range []string{"retirement_sync", "empty_sync"} {
			syncRetirement733(t, srv.keyStore, hash)
			for _, path := range []string{"/v1/chat/completions", "/mcp", "/v1/realtime?model=test", "/-/batches"} {
				request := httptest.NewRequest(http.MethodPost, path, strings.NewReader(`{}`))
				if strings.HasPrefix(path, "/v1/realtime") {
					request.Method = http.MethodGet
				}
				request.Header.Set("Authorization", "Bearer "+raw)
				if path == "/-/batches" {
					request.Header.Set("Authorization", "Bearer "+srv.cfg.Admin.Token)
					request.Header.Set(batch.KeyHeader, raw)
				}
				response := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(response, request)
				if response.Code != 401 {
					t.Errorf("boot=%d phase=%s path=%s: %d %s", boot, phase, path, response.Code, response.Body.String())
				}
			}
			// Direct server.go MCP adapter and realtime handler cannot bypass the gate.
			if _, ok := (&mcpKeyAuth{keyStore: srv.keyStore}).AuthenticateKey(raw); ok {
				t.Error("MCP adapter admitted retired hash")
			}
			realtime := NewRealtimeHandler(nil, nil, srv.keyStore, realtimeHandlerConfig{})
			request := httptest.NewRequest(http.MethodGet, "/v1/realtime?model=test", nil)
			request.Header.Set("Authorization", "Bearer "+raw)
			response := httptest.NewRecorder()
			realtime.HandleRealtime(response, request)
			if response.Code != 401 {
				t.Fatalf("direct realtime: %d", response.Code)
			}
		}
	}
}

func Test733AdminRefusesUnownedCreateAndConfigRevoke(t *testing.T) {
	ks := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Key: "operator-test-seed", KeyType: "internal"}}}, true)
	handlers := NewKeyHandlers(ks, "admin-test-only")
	for _, body := range []string{`{"name":"unowned"}`, `{"name":"unowned","metadata":{"org_id":"org"}}`} {
		r := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(body))
		setAdminAuth(r, "admin-test-only")
		w := httptest.NewRecorder()
		handlers.CreateKey(w, r)
		if w.Code != 400 {
			t.Fatalf("unowned create: %d", w.Code)
		}
	}
	key := ks.List()[0]
	r := httptest.NewRequest(http.MethodDelete, "/-/keys?key_id="+key.ID, nil)
	setAdminAuth(r, "admin-test-only")
	w := httptest.NewRecorder()
	handlers.RevokeKey(w, r)
	if w.Code != 409 || !strings.Contains(w.Body.String(), "config_key_not_revocable") {
		t.Fatalf("config revoke: %d %s", w.Code, w.Body.String())
	}
}
