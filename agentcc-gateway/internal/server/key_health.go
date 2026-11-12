package server

import (
	"encoding/json"
	"net/http"
)

// keyHealthHandler reports refusal counts without credential values or hashes.
func (s *Server) keyHealthHandler(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	total, reasons := s.keyStore.ConfigKeysRefused()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status": "ok", "config_keys_refused": total,
		"config_keys_refused_by_reason": reasons,
		"key_list_loaded":               s.keyStore.KeyListLoaded(),
	})
}
