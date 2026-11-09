package replay

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-cassette/cassette"
)

// maxBody bounds one request body read.
const maxBody = 64 << 20

// ControlPrefix holds the player's own endpoints, never a recorded path.
const ControlPrefix = "/__cassette/"

// MissHeader marks a reply that is a cassette miss, not a recorded one.
const MissHeader = "X-Agentcc-Cassette"

// codeMiss is the JSON-RPC error of an MCP request nothing recorded.
const codeMiss = -32603

// Handler serves the player: MCP at mcpPath for an MCP cassette, every
// other path for an HTTP one, and the control endpoints under ControlPrefix.
func (p *Player) Handler(mcpPath string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasPrefix(r.URL.Path, ControlPrefix):
			p.serveControl(w, r)
		case p.kind == cassette.KindHTTP:
			p.serveHTTP(w, r)
		case r.URL.Path == mcpPath:
			p.serveMCP(w, r)
		default:
			p.miss(fmt.Sprintf("%s %s (not the MCP endpoint %s)", r.Method, r.URL.Path, mcpPath))
			writeJSON(w, http.StatusNotImplemented, map[string]any{"error": map[string]string{
				"type": "cassette_miss", "message": "agentcc-cassette: no MCP endpoint at this path"}}, true)
		}
	})
}

type rpcMessage struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method,omitempty"`
	Params  json.RawMessage `json:"params,omitempty"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   json.RawMessage `json:"error,omitempty"`
}

func (p *Player) serveMCP(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodPost:
	case http.MethodDelete:
		w.WriteHeader(http.StatusNoContent) // a session ends; replay keeps none
		return
	default: // GET: replay has no server-initiated stream
		w.Header().Set("Allow", "POST, DELETE")
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	data, err := io.ReadAll(io.LimitReader(r.Body, maxBody))
	var msg rpcMessage
	if err == nil {
		err = json.Unmarshal(data, &msg)
	}
	if err != nil || msg.JSONRPC != "2.0" {
		writeJSON(w, http.StatusBadRequest, rpcReply(nil, nil, rpcError(-32700, "want one JSON-RPC 2.0 message")), false)
		return
	}
	if msg.Method == "" || len(msg.ID) == 0 { // a notification, or the client's answer to a request
		w.WriteHeader(http.StatusAccepted)
		return
	}
	switch msg.Method {
	case "ping", "logging/setLevel":
		writeJSON(w, http.StatusOK, rpcReply(msg.ID, json.RawMessage(`{}`), nil), false)
		return
	case "subscriptions/listen":
		writeJSON(w, http.StatusOK, rpcReply(msg.ID, nil, rpcError(-32601, "replay serves no notification stream")), false)
		return
	}
	key, err := p.keys.MCPKey(msg.Method, msg.Params)
	a, ok := p.next(key)
	switch {
	case err == nil && ok:
	case msg.Method == "server/discover": // not recorded: an older era, so the client falls back to initialize
		writeJSON(w, http.StatusOK, rpcReply(msg.ID, nil, rpcError(-32601, "method not found: server/discover")), false)
		return
	default:
		what := p.keys.DescribeMCP(msg.Method, msg.Params)
		p.miss(what)
		w.Header().Set(MissHeader, "miss")
		writeJSON(w, http.StatusOK, rpcReply(msg.ID, nil, rpcError(codeMiss, "agentcc-cassette: no recorded reply for "+what)), false)
		return
	}
	if msg.Method == "initialize" {
		w.Header().Set("Mcp-Session-Id", "replay-"+strconv.FormatInt(p.sessions.Add(1), 10))
	}
	status := http.StatusOK
	if a.status != 0 {
		status = a.status
	}
	writeJSON(w, status, rpcReply(msg.ID, a.result, a.rpcErr), false)
}

func (p *Player) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBody))
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	ct := r.Header.Get("Content-Type")
	a, ok := p.next(p.keys.HTTPKey(r.Method, r.URL.EscapedPath(), r.URL.RawQuery, ct, body))
	if !ok {
		what := p.keys.DescribeHTTP(r.Method, r.URL.EscapedPath(), r.URL.RawQuery, ct, body)
		p.miss(what)
		writeJSON(w, http.StatusNotImplemented, map[string]any{"error": map[string]string{
			"type": "cassette_miss", "message": "agentcc-cassette: no recorded response for " + what}}, true)
		return
	}
	out, err := a.resp.Body.Bytes()
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		return
	}
	for name, values := range a.resp.Headers {
		if hopByHop[http.CanonicalHeaderKey(name)] {
			continue
		}
		for _, v := range values {
			w.Header().Add(name, v)
		}
	}
	w.WriteHeader(a.resp.Status)
	_, _ = w.Write(out)
}

// hopByHop are not replayed; Content-Length is recomputed.
var hopByHop = map[string]bool{"Connection": true, "Keep-Alive": true, "Proxy-Connection": true,
	"Te": true, "Trailer": true, "Transfer-Encoding": true, "Upgrade": true, "Content-Length": true}

func (p *Player) serveControl(w http.ResponseWriter, r *http.Request) {
	switch strings.TrimPrefix(r.URL.Path, ControlPrefix) {
	case "health":
		writeJSON(w, http.StatusOK, map[string]any{"kind": p.kind, "exchanges": p.exchanges, "misses": len(p.Misses())}, false)
	case "misses":
		writeJSON(w, http.StatusOK, map[string]any{"misses": p.Misses()}, false)
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

func rpcError(code int, message string) json.RawMessage {
	data, _ := json.Marshal(map[string]any{"code": code, "message": message})
	return data
}

func rpcReply(id, result, rpcErr json.RawMessage) rpcMessage {
	if len(id) == 0 {
		id = json.RawMessage("null")
	}
	return rpcMessage{JSONRPC: "2.0", ID: id, Result: result, Error: rpcErr}
}

// writeJSON writes v without HTML escaping: recorded vendor text goes out
// exactly as it was recorded.
func writeJSON(w http.ResponseWriter, status int, v any, miss bool) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	w.Header().Set("Content-Type", "application/json")
	if miss {
		w.Header().Set(MissHeader, "miss")
	}
	w.WriteHeader(status)
	_, _ = w.Write(buf.Bytes())
}
