package restmcp

import (
	"bytes"
	"encoding/json"
	"net/http"
)

// JSON-RPC error codes the runtime answers with.
const (
	codeParse          = -32700
	codeInvalidRequest = -32600
	codeMethodNotFound = -32601
	codeInvalidParams  = -32602
	codeInternal       = -32603
)

// message is one JSON-RPC 2.0 message.
type message struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method,omitempty"`
	Params  json.RawMessage `json:"params,omitempty"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

type rpcError struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data,omitempty"`
}

func (m *message) isNotification() bool { return m.Method != "" && len(m.ID) == 0 }
func (m *message) isResponse() bool     { return m.Method == "" && len(m.ID) > 0 }

// writeJSON writes v as the response body, with no HTML escaping: vendor
// text goes out as the vendor sent it.
func writeJSON(w http.ResponseWriter, status int, v any) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		status = http.StatusInternalServerError
		buf.Reset()
		buf.WriteString(`{"jsonrpc":"2.0","id":null,"error":{"code":-32603,"message":"the answer could not be encoded"}}`)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(buf.Bytes())
}

func idOrNull(id json.RawMessage) json.RawMessage {
	if len(id) == 0 {
		return json.RawMessage("null")
	}
	return id
}

func writeResult(w http.ResponseWriter, id, result json.RawMessage) {
	writeJSON(w, http.StatusOK, message{JSONRPC: "2.0", ID: idOrNull(id), Result: result})
}

func writeError(w http.ResponseWriter, status int, id json.RawMessage, code int, msg string, data json.RawMessage) {
	writeJSON(w, status, message{JSONRPC: "2.0", ID: idOrNull(id), Error: &rpcError{Code: code, Message: msg, Data: data}})
}
