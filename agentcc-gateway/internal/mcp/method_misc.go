package mcp

import (
	"net/http"
)

func (s *Server) handlePing(w http.ResponseWriter, msg *Message) {
	writeJSONRPCResult(w, msg.ID, struct{}{})
}

// handleNotification serves the client notifications: initialized and
// cancelled are accepted with 202; the others get the unknown-method answer.
func (s *Server) handleNotification(w http.ResponseWriter, r *http.Request, msg *Message) {
	switch msg.Method {
	case MethodInitialized, MethodCancelled:
		// Notification — acknowledge but no response.
		w.WriteHeader(http.StatusAccepted)
	default:
		writeMethodNotFound(w, msg)
	}
}

// handleCompletion serves completion/complete with the unknown-method answer.
func (s *Server) handleCompletion(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}

// handleSetLevel serves logging/setLevel with the unknown-method answer.
func (s *Server) handleSetLevel(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}

// handleTasks serves the tasks/* methods with the unknown-method answer;
// task-shaped results pass through untouched.
func (s *Server) handleTasks(w http.ResponseWriter, r *http.Request, msg *Message) {
	writeMethodNotFound(w, msg)
}
