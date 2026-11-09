package restmcp

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/mcp/protocol"
)

// Methods the runtime serves.
const (
	methodInitialize = "initialize"
	methodPing       = "ping"
	methodDiscover   = "server/discover"
	methodToolsList  = "tools/list"
	methodToolsCall  = "tools/call"
)

// callLog is what one request's log line says. It never holds a credential,
// an argument, a vendor URL or vendor text.
type callLog struct {
	tool         string
	outcome      string
	vendorStatus int
	attempts     int
}

// dispatch answers one JSON-RPC request and logs it.
func (s *Server) dispatch(w http.ResponseWriter, r *http.Request, app *App, msg *message, era protocol.Era,
	cred *credential, start time.Time) {
	lg := callLog{outcome: outcomeOK}
	status := http.StatusOK
	switch msg.Method {
	case methodInitialize:
		s.initialize(w, msg)
	case methodPing:
		s.writeResult(w, era, msg, json.RawMessage(`{}`))
	case methodDiscover:
		if !era.Stateless {
			lg.outcome = "method_not_found"
			writeError(w, http.StatusOK, msg.ID, codeMethodNotFound, "server/discover is a "+protocol.Version2026+" method", nil)
			break
		}
		s.discover(w, era, msg)
	case methodToolsList:
		lg.outcome = s.listTools(w, app, era, msg)
	case methodToolsCall:
		lg = s.callTool(r.Context(), w, app, era, msg, cred)
	default:
		lg.outcome = "method_not_found"
		writeError(w, http.StatusOK, msg.ID, codeMethodNotFound, "unknown method: "+truncate(msg.Method, 64), nil)
	}
	attrs := []any{"app", app.ID, "method", loggedMethod(msg.Method), "era", era.Served(), "status", status,
		"outcome", lg.outcome, "duration_ms", time.Since(start).Milliseconds()}
	if lg.tool != "" {
		attrs = append(attrs, "tool", lg.tool)
	}
	if lg.vendorStatus != 0 {
		attrs = append(attrs, "vendor_status", lg.vendorStatus)
	}
	if lg.attempts > 1 {
		attrs = append(attrs, "attempts", lg.attempts)
	}
	s.log.Info("connectors request", attrs...)
}

// loggedMethod is the method as the log line names it: a served method by
// name, anything else as "other", so text a client chose never reaches the
// log through the method field.
func loggedMethod(m string) string {
	switch m {
	case methodInitialize, methodPing, methodDiscover, methodToolsList, methodToolsCall:
		return m
	}
	return "other"
}

// writeResult writes a result; on the stateless era it carries the 2026
// envelope: resultType, serverInfo in _meta, and on a list or discover the
// caching fields (public: the runtime's answers to these do not depend on
// the caller's credential).
func (s *Server) writeResult(w http.ResponseWriter, era protocol.Era, msg *message, result json.RawMessage) {
	if era.Stateless {
		env := protocol.Envelope{ServerInfo: s.info, Cacheable: protocol.Cacheable(msg.Method), TTL: listTTL,
			CacheScope: protocol.CacheScopePublic}
		result = env.Decorate(result)
	}
	writeResult(w, msg.ID, result)
}

// capabilities: tools only. listChanged is not declared: the tool list is
// fixed for the life of the process.
var capabilities = json.RawMessage(`{"tools":{}}`)

// initialize negotiates a legacy revision: the client's when the runtime
// serves it, else the newest it serves. It opens no session (no
// Mcp-Session-Id): every request carries its own credential, so any replica
// answers any request.
func (s *Server) initialize(w http.ResponseWriter, msg *message) {
	var p struct {
		ProtocolVersion string `json:"protocolVersion"`
	}
	_ = json.Unmarshal(msg.Params, &p)
	version := legacyVersions[0]
	if contains(legacyVersions, p.ProtocolVersion) {
		version = p.ProtocolVersion
	}
	result, _ := json.Marshal(map[string]any{
		"protocolVersion": version,
		"capabilities":    capabilities,
		"serverInfo":      json.RawMessage(s.info),
	})
	writeResult(w, msg.ID, result)
}

// discover answers server/discover (2026-07-28).
func (s *Server) discover(w http.ResponseWriter, era protocol.Era, msg *message) {
	result, _ := json.Marshal(map[string]any{
		"supportedVersions": ServedVersions,
		"capabilities":      capabilities,
	})
	s.writeResult(w, era, msg, result)
}

// listTools serves one page of the app's tools. The cursor is the offset of
// the next page bound to this tool list, so a cursor from another app or an
// older build is refused rather than read as a position in this one.
func (s *Server) listTools(w http.ResponseWriter, app *App, era protocol.Era, msg *message) string {
	var p struct {
		Cursor string `json:"cursor"`
	}
	_ = json.Unmarshal(msg.Params, &p)
	offset := 0
	if p.Cursor != "" {
		var ok bool
		if offset, ok = app.openCursor(p.Cursor); !ok {
			writeError(w, http.StatusOK, msg.ID, codeInvalidParams, "invalid cursor", nil)
			return "invalid_cursor"
		}
	}
	end, more := protocol.Page(len(app.listed), offset, s.pageSize)
	tools := app.listed[offset:end]
	if tools == nil {
		tools = []json.RawMessage{}
	}
	page := map[string]any{"tools": tools}
	if more {
		page["nextCursor"] = app.cursor(end)
	}
	result, err := json.Marshal(page)
	if err != nil {
		writeError(w, http.StatusOK, msg.ID, codeInternal, "the tool list could not be encoded", nil)
		return "internal_error"
	}
	s.writeResult(w, era, msg, result)
	return outcomeOK
}

// cursor encodes the offset of a page of this app's list.
func (a *App) cursor(offset int) string {
	return base64.RawURLEncoding.EncodeToString([]byte("v1:" + a.hash + ":" + strconv.Itoa(offset)))
}

// openCursor reads a cursor of this app's list.
func (a *App) openCursor(c string) (int, bool) {
	raw, err := base64.RawURLEncoding.DecodeString(c)
	if err != nil {
		return 0, false
	}
	parts := strings.Split(string(raw), ":")
	if len(parts) != 3 || parts[0] != "v1" || parts[1] != a.hash {
		return 0, false
	}
	n, err := strconv.Atoi(parts[2])
	if err != nil || n <= 0 || n >= len(a.listed) {
		return 0, false
	}
	return n, true
}

// callTool runs one tool: place the arguments, call the vendor, shape the
// answer. An unknown tool or malformed params is a JSON-RPC error; arguments
// the tool cannot send, a refused or failed vendor call and a vendor error
// are isError results the model can read.
func (s *Server) callTool(ctx context.Context, w http.ResponseWriter, app *App, era protocol.Era, msg *message,
	cred *credential) callLog {
	var p struct {
		Name      string                     `json:"name"`
		Arguments map[string]json.RawMessage `json:"arguments"`
	}
	if err := json.Unmarshal(msg.Params, &p); err != nil || p.Name == "" {
		writeError(w, http.StatusOK, msg.ID, codeInvalidParams, "tools/call needs params.name and an arguments object", nil)
		return callLog{outcome: "invalid_params"}
	}
	t := app.Tool(p.Name)
	if t == nil {
		writeError(w, http.StatusOK, msg.ID, codeInvalidParams, "unknown tool: "+truncate(p.Name, 128), nil)
		return callLog{outcome: "unknown_tool"}
	}
	lg := callLog{tool: t.Name, outcome: outcomeOK}
	var res *toolResult
	req, err := t.buildRequest(p.Arguments)
	if err != nil {
		lg.outcome = outcomeArguments
		res = errorResult(fmt.Sprintf("%s was not called: %s", t.Name, err.Error()))
	} else {
		v := s.vendor.call(ctx, t, req, cred)
		lg.attempts, lg.vendorStatus = v.attempts, v.status
		switch {
		case v.outcome != "":
			// No usable answer (refused, unreachable, too large): still
			// attributed to the vendor and host the call was for.
			lg.outcome = v.outcome
			res = errorResult(cred.scrub(v.failure))
			res.Meta = map[string]json.RawMessage{}
			res.Meta[MetaVendor], _ = json.Marshal(attribution{Name: app.VendorName, Host: t.host, Status: v.status})
		default:
			res = app.vendorResult(t, v.status, v.header, v.body, cred)
			if res.IsError {
				lg.outcome = outcomeVendorError
			}
		}
	}
	result, encErr := json.Marshal(res)
	if encErr != nil {
		writeError(w, http.StatusOK, msg.ID, codeInternal, "the result could not be encoded", nil)
		lg.outcome = "internal_error"
		return lg
	}
	s.writeResult(w, era, msg, result)
	return lg
}
