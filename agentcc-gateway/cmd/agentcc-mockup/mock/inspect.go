package mock

import (
	"encoding/json"
	"io"
	"net/http"
	"strconv"
)

// logWebhookPath is where the gateway's log flusher posts when its
// control_plane.url is the mock; it stores like POST /__logs.
const logWebhookPath = "/agentcc/webhook/logs/"

// inspectionRoutes serve the scenario's view of the mock: what it was sent,
// what it charged, the logs it was delivered, and its behaviour controls.
func (s *Server) inspectionRoutes() {
	s.mux.HandleFunc("GET /__health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	})
	s.mux.HandleFunc("GET /__received", func(w http.ResponseWriter, r *http.Request) {
		last, ok := lastParam(w, r)
		if !ok {
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"data": s.state.ReceivedRequests(last, r.URL.Query().Get("path"))})
	})
	s.mux.HandleFunc("GET /__charges", func(w http.ResponseWriter, _ *http.Request) {
		total, count := s.state.TotalChargeMicros()
		writeJSON(w, http.StatusOK, map[string]any{"total_micros": total, "count": count})
	})
	storeLogs := func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if err := s.state.AppendLogs(body); err != nil {
			writeOpenAIError(w, http.StatusBadRequest, "invalid_json", err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	}
	s.mux.HandleFunc("POST /__logs", storeLogs)
	s.mux.HandleFunc("POST "+logWebhookPath, storeLogs)
	s.mux.HandleFunc("GET /__logs", func(w http.ResponseWriter, r *http.Request) {
		last, ok := lastParam(w, r)
		if !ok {
			return
		}
		logs, batches := s.state.Logs(last)
		writeJSON(w, http.StatusOK, map[string]any{"logs": logs, "batches": batches})
	})
	s.mux.HandleFunc("POST /__reset", func(w http.ResponseWriter, _ *http.Request) {
		s.state.Reset()
		writeJSON(w, http.StatusOK, map[string]any{"reset": true})
	})
	s.mux.HandleFunc("GET /__behavior", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"armed": s.state.Armed()})
	})
	s.mux.HandleFunc("POST /__behavior", s.handleArm)
	s.mux.HandleFunc("DELETE /__behavior", func(w http.ResponseWriter, _ *http.Request) {
		s.state.Arm(nil)
		writeJSON(w, http.StatusOK, map[string]any{"armed": nil})
	})
}

// handleArm arms {"behavior": "<x-mock-behavior value>", "model": "<optional>"}.
func (s *Server) handleArm(w http.ResponseWriter, r *http.Request) {
	var b ArmedBehavior
	if err := json.NewDecoder(r.Body).Decode(&b); err != nil || len(splitDirectives(b.Behavior)) == 0 {
		writeOpenAIError(w, http.StatusBadRequest, "invalid_body", `body must be {"behavior": "<directives>", "model": "<optional>"}`)
		return
	}
	s.state.Arm(&b)
	writeJSON(w, http.StatusOK, map[string]any{"armed": b})
}

// lastParam reads ?last=N (absent: 0, meaning all).
func lastParam(w http.ResponseWriter, r *http.Request) (int, bool) {
	v := r.URL.Query().Get("last")
	if v == "" {
		return 0, true
	}
	n, err := strconv.Atoi(v)
	if err != nil || n < 0 {
		writeOpenAIError(w, http.StatusBadRequest, "invalid_last", "last must be a non-negative integer")
		return 0, false
	}
	return n, true
}
