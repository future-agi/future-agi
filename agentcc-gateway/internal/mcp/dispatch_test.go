package mcp

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestMethodTablePreRegistersBothEras checks that every 2025 and 2026 method
// has an entry, that the ones with no handler answer exactly as an unknown
// method does, and that the notifications keep their 202.
func TestMethodTablePreRegistersBothEras(t *testing.T) {
	for _, m := range []string{
		MethodInitialize, MethodPing, MethodInitialized, MethodCancelled, MethodProgress, MethodRootsListChanged,
		MethodToolsList, MethodToolsCall, MethodResourcesList, MethodResourcesRead, MethodResourcesTemplatesList,
		MethodResourcesSubscribe, MethodResourcesUnsubscribe, MethodPromptsList, MethodPromptsGet,
		MethodCompletionComplete, MethodLoggingSetLevel, MethodTasksGet, MethodTasksResult, MethodTasksList,
		MethodTasksCancel, MethodServerDiscover, MethodSubscriptionsListen,
	} {
		if _, ok := methodTable[m]; !ok {
			t.Errorf("method %s is not registered", m)
		}
	}

	s := newTestServer(t)
	defer s.Close()
	for _, m := range []string{
		MethodServerDiscover, MethodSubscriptionsListen, MethodCompletionComplete, MethodLoggingSetLevel,
		MethodResourcesTemplatesList, MethodResourcesSubscribe, MethodTasksGet, MethodProgress, "no/such/method",
	} {
		w := postMCP(t, s, &Message{JSONRPC: "2.0", ID: json.RawMessage(`1`), Method: m}, nil)
		resp := decodeResponse(t, w)
		if w.Code != http.StatusOK || resp.Error == nil || resp.Error.Code != ErrCodeMethodNotFound || resp.Error.Message != "unknown method: "+m {
			t.Errorf("%s answered %d %+v, want -32601 unknown method", m, w.Code, resp.Error)
		}
	}
	for _, m := range []string{MethodInitialized, MethodCancelled} {
		if w := postMCP(t, s, &Message{JSONRPC: "2.0", Method: m}, nil); w.Code != http.StatusAccepted || w.Body.Len() != 0 {
			t.Errorf("%s answered %d %q, want an empty 202", m, w.Code, w.Body.String())
		}
	}
}

func TestHandleDeleteAnswers405(t *testing.T) {
	s := newTestServer(t)
	defer s.Close()
	w := httptest.NewRecorder()
	s.HandleDelete(w, httptest.NewRequest(http.MethodDelete, "/mcp", nil))
	if w.Code != http.StatusMethodNotAllowed ||
		w.Body.String() != `{"error":{"message":"Method not allowed for this endpoint","type":"invalid_request_error","param":null,"code":"method_not_allowed"}}`+"\n" {
		t.Fatalf("DELETE answered %d %s", w.Code, w.Body.String())
	}
}

func TestRPCDenialCarriesStatusHeaderAndData(t *testing.T) {
	w := httptest.NewRecorder()
	writeRPCDenial(w, json.RawMessage(`3`), &Denial{
		Code: "invalid_api_key", Message: "invalid API key", RPCCode: -32001, HTTPStatus: http.StatusUnauthorized,
		Header: http.Header{"Www-Authenticate": {`Bearer realm="agentcc-mcp"`}},
		Data:   json.RawMessage(`{"code":"invalid_api_key"}`),
	})
	if w.Code != http.StatusUnauthorized || w.Header().Get("WWW-Authenticate") != `Bearer realm="agentcc-mcp"` ||
		w.Body.String() != `{"jsonrpc":"2.0","id":3,"error":{"code":-32001,"message":"invalid API key","data":{"code":"invalid_api_key"}}}`+"\n" {
		t.Fatalf("protocol denial = %d %v %s", w.Code, w.Header(), w.Body.String())
	}
}
