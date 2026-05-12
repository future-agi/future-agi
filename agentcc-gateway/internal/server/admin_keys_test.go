package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
)

// helper: create empty keystore with admin token
func newTestHandlers(adminToken string) *KeyHandlers {
	cfg := config.AuthConfig{Keys: []config.AuthKeyConfig{}}
	ks := auth.NewKeyStore(cfg)
	return NewKeyHandlers(ks, adminToken)
}

// helper: create keystore pre-loaded with keys
func newTestHandlersWithKeys(adminToken string, keys []config.AuthKeyConfig) *KeyHandlers {
	cfg := config.AuthConfig{Keys: keys}
	ks := auth.NewKeyStore(cfg)
	return NewKeyHandlers(ks, adminToken)
}

// helper: set admin auth header
func setAdminAuth(req *http.Request, token string) {
	req.Header.Set("Authorization", "Bearer "+token)
}

// --- ListKeys Tests ---

func TestListKeys_Empty(t *testing.T) {
	h := newTestHandlers("admin-secret")

	req := httptest.NewRequest(http.MethodGet, "/-/keys", nil)
	setAdminAuth(req, "admin-secret")
	rec := httptest.NewRecorder()

	h.ListKeys(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}

	var body map[string]interface{}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if body["object"] != "list" {
		t.Errorf("expected object=list, got %v", body["object"])
	}

	data, ok := body["data"].([]interface{})
	if !ok {
		t.Fatalf("expected data to be an array, got %T", body["data"])
	}
	if len(data) != 0 {
		t.Errorf("expected empty data array, got %d items", len(data))
	}
}

func TestListKeys_WithKeys(t *testing.T) {
	h := newTestHandlersWithKeys("admin-secret", []config.AuthKeyConfig{
		{Name: "key-a", Key: "sk-agentcc-aaaa", Owner: "alice"},
		{Name: "key-b", Key: "sk-agentcc-bbbb", Owner: "bob"},
	})

	req := httptest.NewRequest(http.MethodGet, "/-/keys", nil)
	setAdminAuth(req, "admin-secret")
	rec := httptest.NewRecorder()

	h.ListKeys(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d", rec.Code)
	}

	var body map[string]interface{}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	data, ok := body["data"].([]interface{})
	if !ok {
		t.Fatalf("expected data to be an array, got %T", body["data"])
	}
	if len(data) != 2 {
		t.Errorf("expected 2 keys, got %d", len(data))
	}

	// Verify each item has expected fields
	for i, item := range data {
		keyObj, ok := item.(map[string]interface{})
		if !ok {
			t.Fatalf("data[%d] is not an object", i)
		}
		if _, exists := keyObj["id"]; !exists {
			t.Errorf("data[%d] missing 'id' field", i)
		}
		if _, exists := keyObj["name"]; !exists {
			t.Errorf("data[%d] missing 'name' field", i)
		}
		if keyObj["status"] != "active" {
			t.Errorf("data[%d] expected status=active, got %v", i, keyObj["status"])
		}
	}
}

func TestListKeys_RequiresAdminToken(t *testing.T) {
	h := newTestHandlers("admin-secret")

	// No auth header
	req := httptest.NewRequest(http.MethodGet, "/-/keys", nil)
	rec := httptest.NewRecorder()

	h.ListKeys(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected status 401, got %d", rec.Code)
	}

	var body map[string]interface{}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if body["error"] != "unauthorized - admin token required" {
		t.Errorf("unexpected error message: %v", body["error"])
	}

	// Wrong token
	req2 := httptest.NewRequest(http.MethodGet, "/-/keys", nil)
	setAdminAuth(req2, "wrong-token")
	rec2 := httptest.NewRecorder()

	h.ListKeys(rec2, req2)

	if rec2.Code != http.StatusUnauthorized {
		t.Fatalf("expected status 401 with wrong token, got %d", rec2.Code)
	}
}

func TestListKeys_NoAdminToken(t *testing.T) {
	// When adminToken is empty, requests are rejected with 403.
	h := newTestHandlers("")

	req := httptest.NewRequest(http.MethodGet, "/-/keys", nil)
	// No auth header set
	rec := httptest.NewRecorder()

	h.ListKeys(rec, req)

	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected status 403 (no admin token configured), got %d", rec.Code)
	}
}

// --- CreateKey Tests ---

func TestCreateKey_Success(t *testing.T) {
	h := newTestHandlers("admin-secret")

	payload := `{"name":"test-key","owner":"tester","models":["gpt-4"],"providers":["openai"],"metadata":{"env":"test"}}`
	req := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(payload))
	req.Header.Set("Content-Type", "application/json")
	setAdminAuth(req, "admin-secret")
	rec := httptest.NewRecorder()

	h.CreateKey(rec, req)

	if rec.Code != http.StatusCreated {
		t.Fatalf("expected status 201, got %d; body: %s", rec.Code, rec.Body.String())
	}

	var body map[string]interface{}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	// Check key field starts with sk-agentcc-
	rawKey, ok := body["key"].(string)
	if !ok || rawKey == "" {
		t.Fatalf("expected 'key' field in response, got %v", body["key"])
	}
	if !strings.HasPrefix(rawKey, "sk-agentcc-") {
		t.Errorf("expected key to start with 'sk-agentcc-', got %q", rawKey)
	}

	// Check other fields
	if body["name"] != "test-key" {
		t.Errorf("expected name=test-key, got %v", body["name"])
	}
	if body["owner"] != "tester" {
		t.Errorf("expected owner=tester, got %v", body["owner"])
	}
	if body["status"] != "active" {
		t.Errorf("expected status=active, got %v", body["status"])
	}
	if _, exists := body["id"]; !exists {
		t.Error("expected 'id' field in response")
	}
	if _, exists := body["key_prefix"]; !exists {
		t.Error("expected 'key_prefix' field in response")
	}
	if _, exists := body["created_at"]; !exists {
		t.Error("expected 'created_at' field in response")
	}

	// Check models array
	models, ok := body["models"].([]interface{})
	if !ok {
		t.Fatalf("expected models to be an array, got %T", body["models"])
	}
	if len(models) != 1 || models[0] != "gpt-4" {
		t.Errorf("expected models=[gpt-4], got %v", models)
	}

	// Check providers array
	providers, ok := body["providers"].([]interface{})
	if !ok {
		t.Fatalf("expected providers to be an array, got %T", body["providers"])
	}
	if len(providers) != 1 || providers[0] != "openai" {
		t.Errorf("expected providers=[openai], got %v", providers)
	}
}

func TestCreateKey_MissingName(t *testing.T) {
	h := newTestHandlers("admin-secret")

	payload := `{"owner":"tester"}`
	req := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(payload))
	req.Header.Set("Content-Type", "application/json")
	setAdminAuth(req, "admin-secret")
	rec := httptest.NewRecorder()

	h.CreateKey(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d; body: %s", rec.Code, rec.Body.String())
	}

	var body map[string]interface{}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	errObj, ok := body["error"].(map[string]interface{})
	if !ok {
		t.Fatalf("expected error object in response, got %T: %v", body["error"], body["error"])
	}
	if errObj["code"] != "missing_name" {
		t.Errorf("expected error code=missing_name, got %v", errObj["code"])
	}
}

func TestCreateKey_RequiresAdmin(t *testing.T) {
	h := newTestHandlers("admin-secret")

	payload := `{"name":"test-key","owner":"tester"}`
	req := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(payload))
	req.Header.Set("Content-Type", "application/json")
	// No admin auth
	rec := httptest.NewRecorder()

	h.CreateKey(rec, req)

	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected status 401, got %d", rec.Code)
	}
}

// --- GetKey Tests ---

func TestGetKey_Success(t *testing.T) {
	h := newTestHandlers("admin-secret")

	// First create a key
	payload := `{"name":"get-test","owner":"tester"}`
	createReq := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(payload))
	createReq.Header.Set("Content-Type", "application/json")
	setAdminAuth(createReq, "admin-secret")
	createRec := httptest.NewRecorder()
	h.CreateKey(createRec, createReq)

	if createRec.Code != http.StatusCreated {
		t.Fatalf("create failed: %d %s", createRec.Code, createRec.Body.String())
	}

	var created map[string]interface{}
	json.Unmarshal(createRec.Body.Bytes(), &created)
	keyID := created["id"].(string)

	// Now GET it
	getReq := httptest.NewRequest(http.MethodGet, "/-/keys/"+keyID, nil)
	getReq.URL.RawQuery = "key_id=" + keyID
	setAdminAuth(getReq, "admin-secret")
	getRec := httptest.NewRecorder()

	h.GetKey(getRec, getReq)

	if getRec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d; body: %s", getRec.Code, getRec.Body.String())
	}

	var body map[string]interface{}
	if err := json.Unmarshal(getRec.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if body["id"] != keyID {
		t.Errorf("expected id=%s, got %v", keyID, body["id"])
	}
	if body["name"] != "get-test" {
		t.Errorf("expected name=get-test, got %v", body["name"])
	}
	if body["status"] != "active" {
		t.Errorf("expected status=active, got %v", body["status"])
	}
	// GetKey should NOT expose the raw key (it encodes the APIKey struct, KeyHash is json:"-")
	if _, exists := body["key"]; exists {
		t.Error("GetKey response should not contain raw 'key' field")
	}
}

func TestGetKey_NotFound(t *testing.T) {
	h := newTestHandlers("admin-secret")

	req := httptest.NewRequest(http.MethodGet, "/-/keys/nonexistent", nil)
	req.URL.RawQuery = "key_id=nonexistent"
	setAdminAuth(req, "admin-secret")
	rec := httptest.NewRecorder()

	h.GetKey(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d; body: %s", rec.Code, rec.Body.String())
	}

	var body map[string]interface{}
	json.Unmarshal(rec.Body.Bytes(), &body)

	errObj, ok := body["error"].(map[string]interface{})
	if !ok {
		t.Fatalf("expected error object, got %T", body["error"])
	}
	if errObj["code"] != "key_not_found" {
		t.Errorf("expected code=key_not_found, got %v", errObj["code"])
	}
}

func TestGetKey_MissingKeyID(t *testing.T) {
	h := newTestHandlers("admin-secret")

	req := httptest.NewRequest(http.MethodGet, "/-/keys/", nil)
	// No key_id query param
	setAdminAuth(req, "admin-secret")
	rec := httptest.NewRecorder()

	h.GetKey(rec, req)

	if rec.Code != http.StatusBadRequest {
		t.Fatalf("expected status 400, got %d; body: %s", rec.Code, rec.Body.String())
	}

	var body map[string]interface{}
	json.Unmarshal(rec.Body.Bytes(), &body)

	errObj, ok := body["error"].(map[string]interface{})
	if !ok {
		t.Fatalf("expected error object, got %T", body["error"])
	}
	if errObj["code"] != "missing_key_id" {
		t.Errorf("expected code=missing_key_id, got %v", errObj["code"])
	}
}

// --- RevokeKey Tests ---

func TestRevokeKey_Success(t *testing.T) {
	h := newTestHandlers("admin-secret")

	// Create a key first
	payload := `{"name":"revoke-me","owner":"tester"}`
	createReq := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(payload))
	createReq.Header.Set("Content-Type", "application/json")
	setAdminAuth(createReq, "admin-secret")
	createRec := httptest.NewRecorder()
	h.CreateKey(createRec, createReq)

	var created map[string]interface{}
	json.Unmarshal(createRec.Body.Bytes(), &created)
	keyID := created["id"].(string)

	// Revoke it
	revokeReq := httptest.NewRequest(http.MethodDelete, "/-/keys/"+keyID, nil)
	revokeReq.URL.RawQuery = "key_id=" + keyID
	setAdminAuth(revokeReq, "admin-secret")
	revokeRec := httptest.NewRecorder()

	h.RevokeKey(revokeRec, revokeReq)

	if revokeRec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d; body: %s", revokeRec.Code, revokeRec.Body.String())
	}

	var body map[string]interface{}
	if err := json.Unmarshal(revokeRec.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}
	if body["status"] != "revoked" {
		t.Errorf("expected status=revoked, got %v", body["status"])
	}

	// Verify key is now revoked via GetKey
	getReq := httptest.NewRequest(http.MethodGet, "/-/keys/"+keyID, nil)
	getReq.URL.RawQuery = "key_id=" + keyID
	setAdminAuth(getReq, "admin-secret")
	getRec := httptest.NewRecorder()
	h.GetKey(getRec, getReq)

	var getBody map[string]interface{}
	json.Unmarshal(getRec.Body.Bytes(), &getBody)
	if getBody["status"] != "revoked" {
		t.Errorf("expected key status=revoked after revocation, got %v", getBody["status"])
	}
}

func TestRevokeKey_NotFound(t *testing.T) {
	h := newTestHandlers("admin-secret")

	req := httptest.NewRequest(http.MethodDelete, "/-/keys/nonexistent", nil)
	req.URL.RawQuery = "key_id=nonexistent"
	setAdminAuth(req, "admin-secret")
	rec := httptest.NewRecorder()

	h.RevokeKey(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d; body: %s", rec.Code, rec.Body.String())
	}
}

// --- UpdateKey Tests ---

func TestUpdateKey_Success(t *testing.T) {
	h := newTestHandlers("admin-secret")

	// Create a key first
	payload := `{"name":"original-name","owner":"original-owner"}`
	createReq := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(payload))
	createReq.Header.Set("Content-Type", "application/json")
	setAdminAuth(createReq, "admin-secret")
	createRec := httptest.NewRecorder()
	h.CreateKey(createRec, createReq)

	var created map[string]interface{}
	json.Unmarshal(createRec.Body.Bytes(), &created)
	keyID := created["id"].(string)

	// Update the name and add models
	updatePayload := `{"name":"updated-name","models":["gpt-4","claude-3"],"metadata":{"team":"backend"}}`
	updateReq := httptest.NewRequest(http.MethodPut, "/-/keys/"+keyID, strings.NewReader(updatePayload))
	updateReq.URL.RawQuery = "key_id=" + keyID
	updateReq.Header.Set("Content-Type", "application/json")
	setAdminAuth(updateReq, "admin-secret")
	updateRec := httptest.NewRecorder()

	h.UpdateKey(updateRec, updateReq)

	if updateRec.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d; body: %s", updateRec.Code, updateRec.Body.String())
	}

	var body map[string]interface{}
	if err := json.Unmarshal(updateRec.Body.Bytes(), &body); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if body["name"] != "updated-name" {
		t.Errorf("expected name=updated-name, got %v", body["name"])
	}
	// Owner should remain unchanged since we didn't send it
	if body["owner"] != "original-owner" {
		t.Errorf("expected owner=original-owner (unchanged), got %v", body["owner"])
	}

	// Check models were updated
	models, ok := body["allowed_models"].([]interface{})
	if !ok {
		t.Fatalf("expected allowed_models to be array, got %T", body["allowed_models"])
	}
	if len(models) != 2 {
		t.Errorf("expected 2 models, got %d", len(models))
	}

	// Verify via GetKey
	getReq := httptest.NewRequest(http.MethodGet, "/-/keys/"+keyID, nil)
	getReq.URL.RawQuery = "key_id=" + keyID
	setAdminAuth(getReq, "admin-secret")
	getRec := httptest.NewRecorder()
	h.GetKey(getRec, getReq)

	var getBody map[string]interface{}
	json.Unmarshal(getRec.Body.Bytes(), &getBody)
	if getBody["name"] != "updated-name" {
		t.Errorf("GetKey after update: expected name=updated-name, got %v", getBody["name"])
	}

	// Check metadata
	meta, ok := getBody["metadata"].(map[string]interface{})
	if !ok {
		t.Fatalf("expected metadata to be a map, got %T", getBody["metadata"])
	}
	if meta["team"] != "backend" {
		t.Errorf("expected metadata.team=backend, got %v", meta["team"])
	}
}

func TestUpdateKey_NotFound(t *testing.T) {
	h := newTestHandlers("admin-secret")

	payload := `{"name":"updated"}`
	req := httptest.NewRequest(http.MethodPut, "/-/keys/nonexistent", strings.NewReader(payload))
	req.URL.RawQuery = "key_id=nonexistent"
	req.Header.Set("Content-Type", "application/json")
	setAdminAuth(req, "admin-secret")
	rec := httptest.NewRecorder()

	h.UpdateKey(rec, req)

	if rec.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d; body: %s", rec.Code, rec.Body.String())
	}
}

// --- CreateAndAuthenticate Test ---

func TestCreateAndAuthenticate(t *testing.T) {
	cfg := config.AuthConfig{Keys: []config.AuthKeyConfig{}}
	ks := auth.NewKeyStore(cfg)
	h := NewKeyHandlers(ks, "admin-secret")

	// Create a key via the handler
	payload := `{"name":"auth-test","owner":"tester","models":["gpt-4"]}`
	createReq := httptest.NewRequest(http.MethodPost, "/-/keys", strings.NewReader(payload))
	createReq.Header.Set("Content-Type", "application/json")
	setAdminAuth(createReq, "admin-secret")
	createRec := httptest.NewRecorder()

	h.CreateKey(createRec, createReq)

	if createRec.Code != http.StatusCreated {
		t.Fatalf("create failed: %d %s", createRec.Code, createRec.Body.String())
	}

	var created map[string]interface{}
	json.Unmarshal(createRec.Body.Bytes(), &created)

	rawKey := created["key"].(string)
	keyID := created["id"].(string)

	// The raw key should start with sk-agentcc-
	if !strings.HasPrefix(rawKey, "sk-agentcc-") {
		t.Fatalf("expected raw key to start with sk-agentcc-, got %q", rawKey)
	}

	// Authenticate using the raw key against the keystore directly
	authenticatedKey := ks.Authenticate(rawKey)
	if authenticatedKey == nil {
		t.Fatal("expected Authenticate to return the key, got nil")
	}
	if authenticatedKey.ID != keyID {
		t.Errorf("expected authenticated key ID=%s, got %s", keyID, authenticatedKey.ID)
	}
	if authenticatedKey.Name != "auth-test" {
		t.Errorf("expected name=auth-test, got %s", authenticatedKey.Name)
	}
	if !authenticatedKey.IsActive() {
		t.Error("expected key to be active")
	}
	if !authenticatedKey.CanAccessModel("gpt-4") {
		t.Error("expected key to have access to gpt-4")
	}
	if authenticatedKey.CanAccessModel("claude-3") {
		t.Error("expected key to NOT have access to claude-3")
	}

	// A wrong key should not authenticate
	wrongKey := ks.Authenticate("sk-agentcc-wrong")
	if wrongKey != nil {
		t.Error("expected wrong key to return nil")
	}
}
