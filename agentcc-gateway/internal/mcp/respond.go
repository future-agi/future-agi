package mcp

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
)

func writeJSONRPCResult(w http.ResponseWriter, id json.RawMessage, result interface{}) {
	resp, err := NewResponse(id, result)
	if err != nil {
		writeJSONRPCError(w, id, ErrCodeInternal, "failed to marshal result")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func writeJSONRPCError(w http.ResponseWriter, id json.RawMessage, code int, message string) {
	resp := NewErrorResponse(id, code, message)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

// --- Chain answers ---

// writeToolCall answers a tools/call: its denial, or its result with the
// deprecation note a deprecated tool carries and the call's response meta.
func (s *Server) writeToolCall(w http.ResponseWriter, cc *CallContext) {
	if d := cc.Decision; d != nil {
		writeToolCallDenial(w, cc, d)
		return
	}
	result := cc.legacy.toolResult
	if result == nil {
		// An upstream hook that answers raw JSON only is passed through.
		if len(cc.Result) == 0 {
			writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, "tool call produced no result")
			return
		}
		writeResultWithMeta(w, cc.msg.ID, cc.Result, cc.ResponseMeta)
		return
	}

	// Add deprecation warning if tool is deprecated.
	if vi, ok := s.registry.GetToolVersion(cc.Tool.ExposedName); ok && vi.Deprecated {
		warning := fmt.Sprintf("DEPRECATED: tool %q is deprecated", cc.Tool.ExposedName)
		if vi.DeprecationMessage != "" {
			warning = fmt.Sprintf("DEPRECATED: %s", vi.DeprecationMessage)
		}
		if vi.ReplacedBy != "" {
			warning += fmt.Sprintf(". Use %q instead", vi.ReplacedBy)
		}
		result.Content = append(result.Content, ContentPart{
			Type: "text",
			Text: warning,
		})
	}

	slog.Info("mcp tool call",
		"tool", cc.Tool.ExposedName,
		"server", cc.Upstream.ServerID,
		"duration", cc.Timings.UpstreamEnded.Sub(cc.Timings.UpstreamStarted),
		"is_error", result.IsError,
	)

	writeResultWithMeta(w, cc.msg.ID, result, cc.ResponseMeta)
}

// writeToolCallDenial answers a refused tools/call: in band as an isError
// result, or as the protocol-level error the denial names.
func writeToolCallDenial(w http.ResponseWriter, cc *CallContext, d *Denial) {
	if !d.InBand() {
		writeRPCDenial(w, cc.msg.ID, d)
		return
	}
	res, err := inBandDenialResult(d, cc.ResponseMeta)
	if err != nil {
		writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, "failed to marshal denial")
		return
	}
	writeJSONRPCResult(w, cc.msg.ID, res)
}

// writeDenialRPC answers a refused list or read: always a JSON-RPC error. A
// denial without a JSON-RPC code is -32600, with its error object as data.
func writeDenialRPC(w http.ResponseWriter, cc *CallContext) {
	d := *cc.Decision
	if d.RPCCode == 0 {
		obj, err := json.Marshal(d.Object())
		if err != nil {
			writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, "failed to marshal denial")
			return
		}
		d.RPCCode, d.Message, d.Data = ErrCodeInvalidRequest, d.Error(), obj
	}
	writeRPCDenial(w, cc.msg.ID, &d)
}

// writeRPCDenial writes a protocol-level denial: a plain JSON-RPC error
// when it sets no status, header or data.
func writeRPCDenial(w http.ResponseWriter, id json.RawMessage, d *Denial) {
	if d.HTTPStatus == 0 && len(d.Header) == 0 && len(d.Data) == 0 {
		writeJSONRPCError(w, id, d.RPCCode, d.Message)
		return
	}
	for k, vs := range d.Header {
		for _, v := range vs {
			w.Header().Add(k, v)
		}
	}
	resp := NewErrorResponse(id, d.RPCCode, d.Message)
	resp.Error.Data = d.Data
	w.Header().Set("Content-Type", "application/json")
	if d.HTTPStatus != 0 {
		w.WriteHeader(d.HTTPStatus)
	}
	json.NewEncoder(w).Encode(resp)
}

// writeResultWithMeta writes result with meta merged into its _meta object.
// With no meta it is exactly writeJSONRPCResult.
func writeResultWithMeta(w http.ResponseWriter, id json.RawMessage, result any, meta map[string]json.RawMessage) {
	if len(meta) == 0 {
		writeJSONRPCResult(w, id, result)
		return
	}
	merged, err := mergeMeta(result, meta)
	if err != nil {
		writeJSONRPCError(w, id, ErrCodeInternal, "failed to marshal result")
		return
	}
	writeJSONRPCResult(w, id, merged)
}

// mergeMeta returns result as a JSON object whose _meta holds its own entries
// and meta's; meta wins on a shared key.
func mergeMeta(result any, meta map[string]json.RawMessage) (json.RawMessage, error) {
	raw, ok := result.(json.RawMessage)
	if !ok {
		var err error
		if raw, err = json.Marshal(result); err != nil {
			return nil, err
		}
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(raw, &obj); err != nil {
		return nil, err
	}
	if obj == nil {
		obj = map[string]json.RawMessage{}
	}
	existing := map[string]json.RawMessage{}
	if m, ok := obj["_meta"]; ok {
		if err := json.Unmarshal(m, &existing); err != nil || existing == nil {
			existing = map[string]json.RawMessage{}
		}
	}
	for k, v := range meta {
		existing[k] = v
	}
	m, err := json.Marshal(existing)
	if err != nil {
		return nil, err
	}
	obj["_meta"] = m
	return json.Marshal(obj)
}

func (s *Server) writeToolsList(w http.ResponseWriter, cc *CallContext) {
	writeResultWithMeta(w, cc.msg.ID, ListToolsResult{Tools: cc.ToolList}, cc.ResponseMeta)
}

func (s *Server) writeResourcesList(w http.ResponseWriter, cc *CallContext) {
	writeResultWithMeta(w, cc.msg.ID, ListResourcesResult{Resources: cc.ResourceList}, cc.ResponseMeta)
}

func (s *Server) writePromptsList(w http.ResponseWriter, cc *CallContext) {
	writeResultWithMeta(w, cc.msg.ID, ListPromptsResult{Prompts: cc.PromptList}, cc.ResponseMeta)
}

func (s *Server) writeReadResource(w http.ResponseWriter, cc *CallContext) {
	if r := cc.legacy.readResult; r != nil {
		writeResultWithMeta(w, cc.msg.ID, r, cc.ResponseMeta)
		return
	}
	writeRawResult(w, cc)
}

func (s *Server) writeGetPrompt(w http.ResponseWriter, cc *CallContext) {
	if r := cc.legacy.promptResult; r != nil {
		writeResultWithMeta(w, cc.msg.ID, r, cc.ResponseMeta)
		return
	}
	writeRawResult(w, cc)
}

// writeRawResult passes a raw upstream result through.
func writeRawResult(w http.ResponseWriter, cc *CallContext) {
	if len(cc.Result) == 0 {
		writeJSONRPCError(w, cc.msg.ID, ErrCodeInternal, cc.Method+" produced no result")
		return
	}
	writeResultWithMeta(w, cc.msg.ID, cc.Result, cc.ResponseMeta)
}
