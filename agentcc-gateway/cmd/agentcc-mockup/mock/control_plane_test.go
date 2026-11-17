package mock

import (
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"strings"
	"testing"
)

func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

// keyList fetches the key list as a control-plane gateway does.
func keyList(t *testing.T, m *testMock, headers ...string) (int, map[string]any) {
	t.Helper()
	resp, b := m.do(t, "GET", keyListPath, "", append([]string{"Authorization", "Bearer scn-admin"}, headers...)...)
	if resp.StatusCode != http.StatusOK {
		return resp.StatusCode, nil
	}
	return resp.StatusCode, decode(t, b)
}

func TestControlPlaneListsRegisteredPersonKeysIn733Shape(t *testing.T) {
	m := newTestMock(t, "")

	// No key yet: a 733 answer that is not authoritative (issued_ever 0),
	// with every list present and empty.
	_, v := keyList(t, m)
	if v["status"] != true || len(v["result"].([]any)) != 0 || len(v["retired"].([]any)) != 0 ||
		v["config_conflicts"] == nil || len(v["config_conflicts"].([]any)) != 0 {
		t.Fatalf("empty list = %v", v)
	}
	if snap := v["snapshot"].(map[string]any); snap["complete"] != true || snap["issued_ever"] != 0.0 {
		t.Fatalf("empty snapshot = %v", snap)
	}

	resp, b := m.do(t, "POST", "/__control-plane/keys", `{"keys": [
	  {"id": "scn-key-acme", "key": "scn-test-key-acme", "name": "acme-app", "owner": "acme",
	   "metadata": {"org_id": "org-acme", "user_id": "scn-user-acme"}},
	  {"id": "key_b", "key_hash": "`+strings.ToUpper(sha256Hex("raw-b"))+`", "models": ["mock/ok-1"],
	   "metadata": {"org_id": "org-b", "user_id": "scn-user-b", "log_content": "none"}}]}`)
	if resp.StatusCode != http.StatusOK || decode(t, b)["active"] != 2.0 {
		t.Fatalf("register: %d %s", resp.StatusCode, b)
	}
	_, v = keyList(t, m)
	result := v["result"].([]any)
	if len(result) != 2 {
		t.Fatalf("result = %v", result)
	}
	first, second := result[0].(map[string]any), result[1].(map[string]any)
	if first["id"] != "scn-key-acme" || first["key_hash"] != sha256Hex("scn-test-key-acme") || first["name"] != "acme-app" ||
		first["owner"] != "acme" || first["metadata"].(map[string]any)["user_id"] != "scn-user-acme" {
		t.Fatalf("first key = %v", first)
	}
	if _, raw := first["key"]; raw {
		t.Fatalf("the list carries a raw key: %v", first)
	}
	if second["key_hash"] != sha256Hex("raw-b") || second["models"].([]any)[0] != "mock/ok-1" ||
		second["metadata"].(map[string]any)["log_content"] != "none" {
		t.Fatalf("second key = %v", second)
	}
	if snap := v["snapshot"].(map[string]any); snap["complete"] != true || snap["issued_ever"] != 2.0 {
		t.Fatalf("snapshot = %v", snap)
	}

	// Retiring a key moves it to retired; issued_ever still counts it.
	if resp, b := m.do(t, "DELETE", "/__control-plane/keys/key_b", ""); resp.StatusCode != http.StatusOK {
		t.Fatalf("retire: %d %s", resp.StatusCode, b)
	}
	if resp, _ := m.do(t, "DELETE", "/__control-plane/keys/key_b", ""); resp.StatusCode != http.StatusNotFound {
		t.Fatalf("retiring a retired key gives %d", resp.StatusCode)
	}
	_, v = keyList(t, m)
	if len(v["result"].([]any)) != 1 || v["snapshot"].(map[string]any)["issued_ever"] != 2.0 {
		t.Fatalf("after retire: %v", v)
	}
	retired := v["retired"].([]any)
	if len(retired) != 1 || retired[0].(map[string]any)["id"] != "key_b" || retired[0].(map[string]any)["key_hash"] != sha256Hex("raw-b") {
		t.Fatalf("retired = %v", retired)
	}
}

func TestControlPlaneAnswersConfigConflicts(t *testing.T) {
	m := newTestMock(t, "")
	m.do(t, "POST", "/__control-plane/keys", `{"keys": [
	  {"id": "k1", "key": "raw-1", "metadata": {"org_id": "o", "user_id": "u"}},
	  {"id": "k2", "key": "raw-2", "metadata": {"org_id": "o", "user_id": "u"}}]}`)
	m.do(t, "DELETE", "/__control-plane/keys/k2", "")

	seed := sha256Hex("deployment-seed")
	_, v := keyList(t, m, configKeyHashesHeader, seed+","+sha256Hex("raw-1")+", "+strings.ToUpper(sha256Hex("raw-2")))
	got := v["config_conflicts"].([]any)
	if len(got) != 2 || got[0] != sha256Hex("raw-1") || got[1] != sha256Hex("raw-2") {
		t.Fatalf("config_conflicts = %v, want the active and the retired key's hashes only", got)
	}
	_, v = keyList(t, m, configKeyHashesHeader, seed)
	if got := v["config_conflicts"].([]any); len(got) != 0 {
		t.Fatalf("a seed no key holds conflicts: %v", got)
	}

	if code, _ := keyList(t, m, configKeyHashesHeader, "not-hex"); code != http.StatusBadRequest {
		t.Fatalf("a malformed hash gives %d", code)
	}
	many := make([]string, 33)
	for i := range many {
		many[i] = seed
	}
	if code, _ := keyList(t, m, configKeyHashesHeader, strings.Join(many, ",")); code != http.StatusBadRequest {
		t.Fatalf("33 hashes give %d", code)
	}
	if resp, _ := m.do(t, "GET", keyListPath, ""); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("a list without a bearer gives %d", resp.StatusCode)
	}
}

func TestControlPlaneRefusesKeysThatAreNotAPersons(t *testing.T) {
	m := newTestMock(t, "")
	for name, body := range map[string]string{
		"no user_id": `{"keys": [{"id": "k", "key": "raw", "metadata": {"org_id": "o"}}]}`,
		"no org_id":  `{"keys": [{"id": "k", "key": "raw", "metadata": {"user_id": "u"}}]}`,
		"no id":      `{"keys": [{"key": "raw", "metadata": {"org_id": "o", "user_id": "u"}}]}`,
		"bad hash":   `{"keys": [{"id": "k", "key_hash": "abc", "metadata": {"org_id": "o", "user_id": "u"}}]}`,
		"no keys":    `{"keys": []}`,
	} {
		if resp, b := m.do(t, "POST", "/__control-plane/keys", body); resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("%s: %d %s", name, resp.StatusCode, b)
		}
	}
	if _, v := keyList(t, m); len(v["result"].([]any)) != 0 {
		t.Fatalf("a refused registration was listed: %v", v)
	}
}

// The bulk lists are not upstream calls, the org config list is not served
// (an answer would replace the org configs a scenario pushed), and
// POST /__reset keeps the registered keys.
func TestControlPlaneSyncIsNotAnUpstreamCall(t *testing.T) {
	m := newTestMock(t, "")
	m.do(t, "POST", "/__control-plane/keys", `{"keys": [{"id": "k", "key": "raw", "metadata": {"org_id": "o", "user_id": "u"}}]}`)
	keyList(t, m)
	if resp, _ := m.do(t, "GET", orgConfigListPath, "", "Authorization", "Bearer scn-admin"); resp.StatusCode != http.StatusNotFound {
		t.Fatalf("the org config list answered %d", resp.StatusCode)
	}
	m.chat(t, "test/model-a", "", false)
	_, b := m.do(t, "GET", "/__received", "")
	if all := decode(t, b)["data"].([]any); len(all) != 1 {
		t.Fatalf("want the chat only, got %d: %s", len(all), b)
	}

	m.do(t, "POST", "/__reset", "")
	_, b = m.do(t, "GET", "/__control-plane/keys", "")
	v := decode(t, b)
	if len(v["keys"].([]any)) != 1 || v["list_fetches"] != 1.0 {
		t.Fatalf("after reset: %s", b)
	}
}
