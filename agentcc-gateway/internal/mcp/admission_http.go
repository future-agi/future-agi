package mcp

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/mcp/protocol"
)

func (s *Server) handleAdmittedPost(w http.ResponseWriter, r *http.Request, msg *Message) {
	set := s.protocolSettings()
	era := protocol.Classify(msg.Method, msg.Params, r.Header)
	if perr := era.Validate(set.Versions); perr != nil {
		writeProtocolError(w, msg.ID, perr)
		return
	}
	if !protocol.AcceptOK(r.Header, era.Stateless) {
		writeHTTPRefusal(w, http.StatusNotAcceptable, "not_acceptable", "Accept must list supported MCP response types")
		return
	}
	// No legacy resolver shortcut for unqualified stateless/schema integration.
	if era.Stateless {
		writeAdmissionRefusal(w, errAdmissionUnavailable)
		return
	}
	h, a, revision, err := s.admissionRequest(r, msg.Method, msg.Params)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	var sess *admissionSession
	if msg.Method != "initialize" {
		sess, err = s.resumeAdmissionSession(r.Context(), h, r, a, revision)
		if err != nil {
			writeAdmissionRefusal(w, err)
			return
		}
	}
	op, err := s.beginAdmissionOperation(r.Context(), h, a, revision, LeaseCall, sess)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	defer op.close()
	r = r.WithContext(op.watch.ctx)
	aw, err := newAdmissionWriter(w, r, op.watch)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	defer aw.close()
	if msg.IsNotification() {
		if msg.Method != "notifications/initialized" {
			writeAdmissionRefusal(w, errAdmissionDenied)
			return
		}
		_ = aw.reply(func(b http.ResponseWriter) { b.WriteHeader(http.StatusAccepted) })
		return
	}
	if msg.Method == "initialize" {
		s.initializeAdmitted(aw, r, msg, h, a, revision)
		return
	}
	if msg.Method == "ping" {
		_ = aw.reply(func(b http.ResponseWriter) { writeJSONRPCResult(b, msg.ID, struct{}{}) })
		return
	}
	// Separate metadata entry point: it must not read resource/prompt/tool
	// payloads. The runtime is still owed/qualified separately, never legacy.
	if a.Action == ActionListResourceMetadata || a.Action == ActionListPromptMetadata {
		h.Runtime.ListMetadata(aw, r, msg, a)
	} else {
		h.Runtime.DispatchContent(aw, r, msg, a)
	}
}
func (s *Server) initializeAdmitted(w *admissionWriter, r *http.Request, msg *Message, h *HostedAdmission, a LiveAuthorizationRequest, revision MembershipRevision) {
	var params initializeParams
	if json.Unmarshal(msg.Params, &params) != nil {
		_ = w.reply(func(b http.ResponseWriter) {
			writeJSONRPCError(b, msg.ID, ErrCodeInvalidParams, "invalid initialize params")
		})
		return
	}
	var caps ClientCapabilities
	if len(params.Capabilities) > 0 && json.Unmarshal(params.Capabilities, &caps) != nil {
		_ = w.reply(func(b http.ResponseWriter) {
			writeJSONRPCError(b, msg.ID, ErrCodeInvalidParams, "invalid initialize params")
		})
		return
	}
	version, ok := s.sessions.negotiateVersion(params.ProtocolVersion)
	if !ok {
		_ = w.reply(func(b http.ResponseWriter) {
			writeRPCDenial(b, msg.ID, &Denial{RPCCode: errCodeUnsupportedVersion, HTTPStatus: http.StatusBadRequest, Message: "legacy sessions are not served on this gateway"})
		})
		return
	}
	token, sess, err := s.createAdmissionSession(r.Context(), h, a, revision, virtualServerOf(r), version)
	if err != nil {
		_ = w.reply(func(b http.ResponseWriter) { writeAdmissionRefusal(b, err) })
		return
	}
	// Session ID and initialization body remain buffered until same-unit RELEASE.
	err = w.reply(func(b http.ResponseWriter) {
		b.Header().Set("MCP-Session-Id", token)
		writeJSONRPCResult(b, msg.ID, InitializeResult{ProtocolVersion: version, ServerInfo: s.serverInfo})
	})
	if err != nil || w.failed() {
		_ = s.endAdmissionSession(r.Context(), sess)
	}
}
func (s *Server) handleAdmittedDelete(w http.ResponseWriter, r *http.Request) {
	h, a, revision, err := s.admissionRequest(r, "session/delete", nil)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	sess, err := s.resumeAdmissionSession(r.Context(), h, r, a, revision)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	// Independent operation watch: ending the session cancels its children but
	// does not substitute for fresh continuation authorization of this response.
	op, err := s.beginAdmissionOperation(r.Context(), h, a, revision, LeaseCall, nil)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	defer op.close()
	aw, err := newAdmissionWriter(w, r.WithContext(op.watch.ctx), op.watch)
	if err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	defer aw.close()
	if err := s.endAdmissionSession(r.Context(), sess); err != nil {
		writeAdmissionRefusal(w, err)
		return
	}
	_ = aw.reply(func(b http.ResponseWriter) { b.WriteHeader(http.StatusNoContent) })
}
func admissionReadContext(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, admissionLookupTimeout)
}
