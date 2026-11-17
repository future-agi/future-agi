package server

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// capAdmissionGuard is inside successful KeyAuth and outside route dispatch.
// Only an exact canonical chat POST is a supported execution candidate. Every
// other authenticated route is refused by default, including future routes.
// Discovery exceptions are exact non-execution method/path pairs, not prefixes.
func capAdmissionGuard(store *tenant.Store, maxBody int64, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id, authenticated := middleware.KeyIdentity(r.Context())
		if !authenticated || store == nil {
			next.ServeHTTP(w, r)
			return
		}
		cfg := store.Get(id.OrgID)
		if !capbudget.CapsEnabled(cfg) {
			next.ServeHTTP(w, r)
			return
		}
		if capPublicDiscovery(r.Method, r.URL.Path) {
			next.ServeHTTP(w, r)
			return
		}
		deadline, _ := r.Context().Deadline()
		admission, refusal := capbudget.ResolveAdmission(id, cfg, deadline)
		if refusal != nil {
			writeCapRefusal(w, r, refusal)
			return
		}
		if admission == nil {
			next.ServeHTTP(w, r.WithContext(capbudget.WithAdmission(r.Context(), nil)))
			return
		}
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" || r.URL.RawPath != "" {
			writeCapRefusal(w, r, admission.Refusal(capbudget.CapUnenforceable("capped execution supports only canonical synchronous chat"), "", ""))
			return
		}
		// Match ChatCompletion's size/error behavior without allowing unbounded
		// buffering. The handler sees exactly the original bytes on every pass.
		body, err := io.ReadAll(io.LimitReader(r.Body, maxBody+1))
		if err != nil {
			models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read request body"))
			return
		}
		r.Body.Close()
		r.Body = io.NopCloser(bytes.NewReader(body))
		if int64(len(body)) <= maxBody {
			var req models.ChatCompletionRequest
			if json.Unmarshal(body, &req) == nil {
				if req.Stream || r.Header.Get("x-agentcc-async") != "" || r.Header.Get("Prefer") == "respond-async" || string(req.Extra["background"]) == "true" {
					writeCapRefusal(w, r, admission.Refusal(capbudget.CapUnenforceable("streaming and deferred chat are unsupported under admission caps"), "", ""))
					return
				}
			}
		}
		next.ServeHTTP(w, r.WithContext(capbudget.WithAdmission(r.Context(), admission)))
	})
}

// Admission runs before the route handler, so it must choose that route's
// error dialect itself, including refusals before a stream starts.
func writeCapRefusal(w http.ResponseWriter, r *http.Request, err *models.APIError) {
	if r.URL.Path == "/v1/messages" || r.URL.Path == "/v1/messages/count_tokens" {
		writeAnthropicErrorFromError(w, err)
		return
	}
	models.WriteError(w, err)
}

func capPublicDiscovery(method, path string) bool {
	if method != http.MethodGet {
		return false
	}
	switch path {
	case "/v1/models", "/v1/.well-known/agent.json", "/v1/agents":
		return true
	}
	return false
}

// bindCapChat never manufactures permission for direct handler calls: they
// need the same trusted central admission state as externally routed requests.
func bindCapChat(r *http.Request, rc *models.RequestContext, cfg *tenant.OrgConfig) (refusal *models.APIError) {
	st, checked := capbudget.AdmissionFromContext(r.Context())
	defer func() { refusal = st.Refusal(refusal, "", "") }()
	if checked {
		capbudget.BindAdmission(rc, st)
	}
	if st == nil {
		if !checked && capbudget.CapsEnabled(cfg) {
			return capbudget.CapUnenforceable("strict chat is missing authenticated central admission")
		}
		return nil
	}
	if rc.Agent.AgentID != "" && rc.Agent.AgentID != st.Agent() {
		return capbudget.CapUnenforceable("caller agent identity conflicts with the administrative key binding")
	}
	if rc.Metadata["org_id"] != st.Org() {
		return capbudget.CapUnenforceable("authenticated org conflicts with admission")
	}
	return nil
}
