package ext

import (
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"strings"
	"testing"
)

// keyData is GET /api/v1/key's data for bearer.
func (m *extMock) keyData(t *testing.T, bearer string) map[string]any {
	t.Helper()
	status, b := m.do(t, "GET", "/api/v1/key", "", "Authorization", "Bearer "+bearer)
	if status != http.StatusOK {
		t.Fatalf("GET /api/v1/key as %s: status %d body %s", bearer, status, b)
	}
	return decode(t, b)["data"].(map[string]any)
}

func (m *extMock) chargesMicros(t *testing.T) int64 {
	t.Helper()
	_, b := m.do(t, "GET", "/__charges", "")
	return int64(decode(t, b)["total_micros"].(float64))
}

// The usage contract: a bearer's /api/v1/key usage is exactly its
// share of /__charges, and the shares add up to /__charges.
func TestKeyUsageIsTheBearersShareOfTheCharges(t *testing.T) {
	m := newExtMock(t)
	m.chat(t, "bearer-a", chatBody("test/model-a", false))                                // 3,000
	m.chat(t, "bearer-a", chatBody("test/model-a", true))                                 // 3,000, streamed
	m.chat(t, "bearer-b", chatBody("test/model-b", false))                                // 1,200
	m.chat(t, "bearer-a", chatBody("test/model-a", false), "x-mock-behavior", "fail:503") // refused: not charged
	// x-api-key is read as the mock reads it when it charges (Anthropic clients).
	m.do(t, "POST", "/v1/messages", chatBody("test/model-b", false), "x-api-key", "bearer-c") // 1,200

	a, b, c, idle := m.keyData(t, "bearer-a"), m.keyData(t, "bearer-b"), m.keyData(t, "bearer-c"), m.keyData(t, "bearer-idle")
	if a["usage"] != 0.006 || b["usage"] != 0.0012 || c["usage"] != 0.0012 || idle["usage"] != 0.0 {
		t.Fatalf("usage a=%v b=%v c=%v idle=%v; want 0.006, 0.0012, 0.0012, 0", a["usage"], b["usage"], c["usage"], idle["usage"])
	}
	_, viaHeader := m.do(t, "GET", "/api/v1/key", "", "x-api-key", "bearer-c")
	if decode(t, viaHeader)["data"].(map[string]any)["usage"] != 0.0012 {
		t.Fatalf("x-api-key usage = %s", viaHeader)
	}
	sum := usdToMicros(a["usage"].(float64)) + usdToMicros(b["usage"].(float64)) + usdToMicros(c["usage"].(float64))
	if total := m.chargesMicros(t); sum != total || total != 8400 {
		t.Fatalf("usage sums to %d micros, /__charges is %d; want both 8400", sum, total)
	}
	for _, d := range []map[string]any{a, idle} {
		if d["limit"] != nil || d["limit_remaining"] != nil || d["is_free_tier"] != false {
			t.Errorf("an unprovisioned key has no limit: %v", d)
		}
	}
	if label := a["label"].(string); strings.Contains(label, "bearer-a") || !strings.HasSuffix(label, "...") {
		t.Errorf("the label must not echo the key: %q", label)
	}
	if status, b := m.do(t, "GET", "/api/v1/key", ""); status != http.StatusUnauthorized || errorCode(t, b) != 401.0 {
		t.Errorf("no bearer: status %d body %s", status, b)
	}
}

// Provisioning: POST /api/v1/keys, then GET, PATCH and DELETE by hash, with
// usage read from the key's own charges, and keys kept per bearer.
func TestProvisioningCRUD(t *testing.T) {
	m := newExtMock(t)
	owner := []string{"Authorization", "Bearer provisioner"}
	status, b := m.do(t, "POST", "/api/v1/keys", `{"name":"org-1","limit":0.01,"limit_reset":"monthly","include_byok_in_limit":true}`, owner...)
	if status != http.StatusCreated {
		t.Fatalf("create: status %d body %s", status, b)
	}
	created := decode(t, b)
	secret, _ := created["key"].(string)
	data := created["data"].(map[string]any)
	sum := sha256.Sum256([]byte(secret))
	hash := hex.EncodeToString(sum[:])
	if !strings.HasPrefix(secret, "sk-or-v1-") || data["hash"] != hash || data["name"] != "org-1" || data["disabled"] != false ||
		data["limit"] != 0.01 || data["limit_remaining"] != 0.01 || data["usage"] != 0.0 || data["limit_reset"] != "monthly" ||
		data["include_byok_in_limit"] != true || strings.Contains(data["label"].(string), secret) {
		t.Fatalf("created = %s", b)
	}

	// The key's calls are its usage; the provisioner's own usage is untouched.
	m.chat(t, secret, chatBody("test/model-a", false)) // 3,000
	_, b = m.do(t, "GET", "/api/v1/keys/"+hash, "", owner...)
	if got := decode(t, b)["data"].(map[string]any); got["usage"] != 0.003 || got["limit_remaining"] != 0.007 {
		t.Fatalf("after one call: %s", b)
	}
	if self := m.keyData(t, secret); self["usage"] != 0.003 || self["limit"] != 0.01 || self["limit_remaining"] != 0.007 || self["label"] != data["label"] {
		t.Fatalf("GET /api/v1/key as the key: %v", self)
	}
	if m.keyData(t, "provisioner")["usage"] != 0.0 {
		t.Fatal("the provisioner was charged for its key's call")
	}

	// PATCH changes only the fields sent; null clears the limit.
	status, b = m.do(t, "PATCH", "/api/v1/keys/"+hash, `{"name":"org-1b","disabled":true,"limit":null}`, owner...)
	if got := decode(t, b)["data"].(map[string]any); status != http.StatusOK || got["name"] != "org-1b" || got["disabled"] != true ||
		got["limit"] != nil || got["limit_remaining"] != nil || got["limit_reset"] != "monthly" || got["usage"] != 0.003 {
		t.Fatalf("patch: status %d body %s", status, b)
	}
	for _, bad := range []string{`{"name":""}`, `{"limit":-1}`, `{"limit":"lots"}`, `{"limit_reset":"hourly"}`, `{"disabled":"yes"}`, `[]`, ``} {
		if status, b := m.do(t, "PATCH", "/api/v1/keys/"+hash, bad, owner...); status != http.StatusBadRequest {
			t.Errorf("patch %q: status %d body %s", bad, status, b)
		}
	}
	if _, b = m.do(t, "GET", "/api/v1/keys/"+hash, "", owner...); decode(t, b)["data"].(map[string]any)["name"] != "org-1b" {
		t.Fatalf("a refused patch changed the key: %s", b)
	}

	// Keys belong to the bearer that made them.
	other := []string{"Authorization", "Bearer someone-else"}
	for _, method := range []string{"GET", "PATCH", "DELETE"} {
		if status, _ := m.do(t, method, "/api/v1/keys/"+hash, `{"name":"x"}`, other...); status != http.StatusNotFound {
			t.Errorf("%s by another bearer: status %d, want 404", method, status)
		}
	}
	if _, b = m.do(t, "GET", "/api/v1/keys", "", other...); len(decode(t, b)["data"].([]any)) != 0 {
		t.Errorf("another bearer lists %s", b)
	}
	_, b = m.do(t, "GET", "/api/v1/keys", "", owner...)
	if list := decode(t, b)["data"].([]any); len(list) != 1 || list[0].(map[string]any)["hash"] != hash {
		t.Fatalf("list = %s", b)
	}

	if status, b = m.do(t, "DELETE", "/api/v1/keys/"+hash, "", owner...); status != http.StatusOK || decode(t, b)["deleted"] != true {
		t.Fatalf("delete: status %d body %s", status, b)
	}
	if status, _ = m.do(t, "GET", "/api/v1/keys/"+hash, "", owner...); status != http.StatusNotFound {
		t.Fatalf("a deleted key is still served: %d", status)
	}
}

func TestProvisioningValidatesAndResets(t *testing.T) {
	m := newExtMock(t)
	owner := []string{"Authorization", "Bearer provisioner"}
	for _, bad := range []string{`{}`, `{"name":""}`, `{"name":"k","limit":-0.5}`, `not json`} {
		if status, b := m.do(t, "POST", "/api/v1/keys", bad, owner...); status != http.StatusBadRequest {
			t.Errorf("create %q: status %d body %s", bad, status, b)
		}
	}
	if status, _ := m.do(t, "POST", "/api/v1/keys", `{"name":"k"}`); status != http.StatusUnauthorized {
		t.Errorf("create without a bearer: status %d", status)
	}
	status, b := m.do(t, "POST", "/api/v1/keys", `{"name":"k"}`, owner...)
	if status != http.StatusCreated || decode(t, b)["data"].(map[string]any)["limit"] != nil {
		t.Fatalf("create with no limit: status %d body %s", status, b)
	}
	m.do(t, "POST", "/__reset", "")
	if _, b = m.do(t, "GET", "/api/v1/keys", "", owner...); len(decode(t, b)["data"].([]any)) != 0 {
		t.Fatalf("/__reset kept the keys: %s", b)
	}
}
