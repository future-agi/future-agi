package mock

import (
	"net/http"
	"strings"
)

// catalogRoutes serves the fixtures' catalog in OpenRouter's formats, and
// the account's credits.
func (s *Server) catalogRoutes() {
	models := func(w http.ResponseWriter, _ *http.Request) { writeRawJSON(w, http.StatusOK, s.fix.catalog) }
	s.mux.HandleFunc("GET /v1/models", models)
	s.mux.HandleFunc("GET /api/v1/models", models)
	s.mux.HandleFunc("GET /api/v1/models/{author}/{slug}/endpoints", s.handleModelEndpoints)
	s.mux.HandleFunc("GET /api/v1/endpoints/zdr", func(w http.ResponseWriter, _ *http.Request) {
		if s.fix.zdr == nil {
			writeJSON(w, http.StatusOK, map[string]any{"data": []any{}})
			return
		}
		writeRawJSON(w, http.StatusOK, s.fix.zdr)
	})
	s.mux.HandleFunc("GET /api/v1/credits", func(w http.ResponseWriter, _ *http.Request) {
		usage, _ := s.state.TotalChargeMicros()
		writeJSON(w, http.StatusOK, map[string]any{"data": map[string]any{
			"total_credits": float64(s.state.CreditMicros()) / 1e6,
			"total_usage":   float64(usage) / 1e6,
		}})
	})
}

// handleModelEndpoints serves endpoints/<author>/<slug>.json when the
// fixtures have it, else one endpoint built from the catalog entry.
func (s *Server) handleModelEndpoints(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("author") + "/" + r.PathValue("slug")
	if b, ok := s.fix.endpoints[id]; ok {
		writeRawJSON(w, http.StatusOK, b)
		return
	}
	m, ok := s.fix.models[id]
	if !ok {
		writeOpenAIError(w, http.StatusNotFound, "model_not_found", "no model "+id+" in the mock catalog")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"data": synthesizedEndpoints(id, m)})
}

func synthesizedEndpoints(id string, m map[string]any) map[string]any {
	author := strings.SplitN(id, "/", 2)[0]
	providerName := strings.ToUpper(author[:1]) + author[1:]
	var maxCompletion any
	if top, ok := m["top_provider"].(map[string]any); ok {
		maxCompletion = top["max_completion_tokens"]
	}
	endpoint := map[string]any{
		"name":                  providerName + " | " + id,
		"model_name":            m["name"],
		"context_length":        m["context_length"],
		"pricing":               m["pricing"],
		"provider_name":         providerName,
		"tag":                   author,
		"quantization":          nil,
		"max_completion_tokens": maxCompletion,
		"max_prompt_tokens":     nil,
		"supported_parameters":  m["supported_parameters"],
		"status":                0,
		"uptime_last_30m":       100,
	}
	return map[string]any{
		"id": id, "name": m["name"], "created": m["created"], "description": m["description"],
		"architecture": m["architecture"], "endpoints": []any{endpoint},
	}
}
