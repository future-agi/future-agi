package capmock

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPartnerLifecycleFixtures(t *testing.T) {
	mock := New("")
	server := httptest.NewServer(mock.Handler())
	defer server.Close()
	mock.SetBase(server.URL)
	request := func(method, path string, body any, headers map[string]string) (int, map[string]any) {
		t.Helper()
		raw, _ := json.Marshal(body)
		req, _ := http.NewRequest(method, server.URL+path, bytes.NewReader(raw))
		req.Header.Set("Content-Type", "application/json")
		for name, value := range headers {
			req.Header.Set(name, value)
		}
		res, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		result := map[string]any{}
		_ = json.NewDecoder(res.Body).Decode(&result)
		return res.StatusCode, result
	}
	code, minted := request("POST", "/pipedream/v1/oauth/token", map[string]string{"client_id": "fixture-client", "client_secret": "fixture-secret-not-real"}, nil)
	if code != 200 {
		t.Fatal("token route missing")
	}
	bearer, _ := minted["access_token"].(string)
	headers := map[string]string{"Authorization": "Bearer " + bearer, "x-pd-project-id": "proj_fixture", "x-pd-environment": "development", "x-pd-external-user-id": "fau_fixture", "x-pd-app-slug": "gmail"}
	rpc := map[string]any{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": map[string]any{"name": "gmail-send-message"}}
	code, _ = request("POST", "/pipedream/v3", rpc, headers)
	if code != 200 {
		t.Fatal("not-connected must be vendor JSON-RPC, not an auth transport error")
	}
	headers["Authorization"] = "Bearer never-minted"
	code, _ = request("POST", "/pipedream/v3", rpc, headers)
	if code != 401 {
		t.Fatal("unminted bearer accepted")
	}
	project := map[string]string{"x-api-key": "fixture-project-not-real"}
	code, a := request("POST", "/composio/api/v3.1/tool_router/session", map[string]string{"user_id": "fau_one"}, project)
	if code != 201 {
		t.Fatal("session create missing")
	}
	_, b := request("POST", "/composio/api/v3.1/tool_router/session", map[string]string{"user_id": "fau_two"}, project)
	if a["session_id"] == b["session_id"] {
		t.Fatal("end users shared a session")
	}
	sid := a["session_id"].(string)
	code, _ = request("DELETE", "/composio/api/v3.1/tool_router/session/"+sid, nil, project)
	if code != 200 {
		t.Fatal("session delete failed")
	}
	code, _ = request("POST", "/composio/mcp/"+sid, rpc, project)
	if code != 404 {
		t.Fatal("deleted session still executable")
	}
}
