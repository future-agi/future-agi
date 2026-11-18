package server

import (
	"net/http"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	ipaclplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ipacl"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	inspectionAuthStandard   = "authorization-then-x-api-key"
	inspectionAuthMiddleware = "middleware-first-valid-key"
	inspectionAuthGenAI      = "query-key-then-google-key-then-authorization"
	inspectionAuthSpeech     = "speech-sdk-key"
	inspectionAuthRealtime   = "bearer-then-query-api-key"
	inspectionAuthMCP        = "case-insensitive-bearer"
	inspectionAuthStored     = "bearer-else-x-api-key"
	inspectionAuthCatalog    = "catalog-middleware-first-valid-key"
	inspectionAuthPublic     = "public"
	inspectionAuthAdmin      = "separate-admin-auth"
)

func defaultInspectionAuth(pattern string) string {
	switch pattern {
	case "/healthz", "/readyz", "/livez", "/.well-known/agent.json":
		return inspectionAuthPublic
	}
	if strings.HasPrefix(pattern, "/-/") {
		return inspectionAuthAdmin
	}
	return inspectionAuthStandard
}

// RouteInspection is derived from an actual registration. Unknown is the safe
// default. Auth describes the handler's credential precedence, or explicitly
// the middleware-only contract when there is no second gate. Discriminator names operations
// sharing one HTTP template; their capability is resolved before dispatch.
type RouteInspection struct {
	Method        string                      `json:"method"`
	Pattern       string                      `json:"template"`
	Auth          string                      `json:"auth"`
	Discriminator string                      `json:"discriminator,omitempty"`
	Contract      models.InspectionContract   `json:"contract"`
	Variants      []models.InspectionContract `json:"operations,omitempty"`
}

// InspectionInventory includes the HTTP registrations and the internal
// executors actually installed by server initialization (including feature
// flags). Internal rows are registered beside their executor, not inferred from
// endpoint labels or a manually maintained external route list.
func (s *Server) InspectionInventory() []RouteInspection {
	rows := s.router.InspectionInventory()
	return append(rows, s.internalInspections...)
}

func (s *Server) registerInternalInspection(operation, reason string) models.InspectionContract {
	contract := models.InspectionContract{Operation: operation, Reason: reason}
	s.internalInspections = append(s.internalInspections, RouteInspection{
		Method: "INTERNAL", Pattern: operation, Auth: "pipeline-auth-required", Contract: contract,
	})
	return contract
}

func textInspection(operation string) models.InspectionContract {
	return models.InspectionContract{Operation: operation, Input: models.InspectionSupported, Output: models.InspectionSupported}
}

// inspectionAuthorization mirrors the actual handler. In particular an invalid
// higher-priority credential must not fall through to an unrelated selected key.
func inspectionAuthorization(r *http.Request, mode string) string {
	switch mode {
	case inspectionAuthGenAI:
		if key := r.URL.Query().Get("key"); key != "" {
			return "Bearer " + key
		}
		if key := r.Header.Get("x-goog-api-key"); key != "" {
			return "Bearer " + key
		}
		return r.Header.Get("Authorization")
	case inspectionAuthSpeech:
		if key := speechClientKey(r); key != "" {
			return "Bearer " + key
		}
		return ""
	case inspectionAuthRealtime:
		if header := r.Header.Get("Authorization"); strings.HasPrefix(header, "Bearer ") && len(header) > 7 {
			return header
		}
		if key := r.URL.Query().Get("api_key"); key != "" {
			return "Bearer " + key
		}
		return ""
	case inspectionAuthMCP:
		header := r.Header.Get("Authorization")
		if len(header) > 7 && strings.EqualFold(header[:7], "Bearer ") {
			return "Bearer " + strings.TrimSpace(header[7:])
		}
		return ""
	case inspectionAuthStored:
		if header := r.Header.Get("Authorization"); strings.HasPrefix(header, "Bearer ") {
			return header
		}
		if key := r.Header.Get("x-api-key"); key != "" {
			return "Bearer " + key
		}
		return ""
	default:
		if header := r.Header.Get("Authorization"); header != "" {
			return header
		}
		if key := r.Header.Get("x-api-key"); key != "" {
			return "Bearer " + key
		}
		return ""
	}
}

// Middleware-only routes accept every KeyAuth credential form and fall through
// invalid candidates exactly as KeyAuth does. Other routes authenticate again
// in their handler, so keep their case-sensitive schemes and precedence intact.
func inspectionCredential(r *http.Request, mode string, keys *auth.KeyStore) (*auth.APIKey, string) {
	if mode == inspectionAuthMiddleware || mode == inspectionAuthCatalog {
		key, raw := middleware.AuthenticateRequestAPIKey(keys, r)
		return key, "Bearer " + raw
	}
	authorization := inspectionAuthorization(r, mode)
	raw, bearer := strings.CutPrefix(authorization, "Bearer ")
	if !bearer {
		return nil, authorization
	}
	return keys.Authenticate(raw), authorization
}

func (registration RouteInspection) contractFor(r *http.Request) models.InspectionContract {
	contract := registration.Contract
	if registration.Discriminator == "genai-action" {
		modelAction := r.URL.Query().Get("model_action") // router-owned path parameter
		_, action, _ := strings.Cut(modelAction, ":")
		contract.Operation += "#" + action
		contract.Reason = "native action does not have complete input and output inspection"
		for _, variant := range registration.Variants {
			if variant.Operation == action {
				contract.Input, contract.Output, contract.Reason = variant.Input, variant.Output, variant.Reason
				break
			}
		}
	}
	// Queued execution cannot inherit an HTTP receipt. Refuse selected blocking
	// submission until a separately authenticated executor can inspect it.
	if r.Header.Get("x-agentcc-async") == "true" && registration.Pattern == "/v1/chat/completions" {
		contract.Operation += "#async"
		contract.Input, contract.Output = models.InspectionUnknown, models.InspectionUnknown
		contract.Reason = "queued execution has no complete inspection contract"
	}
	return contract
}

// configureInspection installs a declaration-only preflight. It does not run
// billing, cache or org/static checks; the handler retains its normal access
// checks. Identity comes only from the actual authenticated credential, never
// caller headers describing org/key IDs or stored creator metadata.
func (rt *Router) configureInspection(engine *pipeline.Engine, keys *auth.KeyStore, ipcfg config.IPACLConfig, tenants *tenant.Store) {
	rt.dispatch = func(w http.ResponseWriter, r *http.Request, registration RouteInspection, handler http.HandlerFunc) {
		state := models.NewInspectionState(registration.contractFor(r))
		r = r.WithContext(models.WithInspection(r.Context(), state))

		// These are the existing public/separately authenticated surfaces, not
		// content-capability exemptions. No API-key principal exists on them.
		if keys != nil && registration.Auth != inspectionAuthPublic && registration.Auth != inspectionAuthAdmin && !middleware.IsLicenseAuthorized(r.Context()) {
			key, authorization := inspectionCredential(r, registration.Auth, keys)
			if key == nil {
				models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
				return
			}
			rc := models.AcquireRequestContext()
			rc.RequestID = models.GetRequestID(r.Context())
			rc.EndpointType = "inspection_admission"
			rc.Inspection = state
			rc.Metadata["inspection_operation"] = state.Contract().Operation
			rc.Metadata["authorization"] = authorization
			rc.Metadata["client_ip"] = trustedClientIP(r, ipcfg.TrustedProxies)
			orgID := key.Metadata["org_id"]
			rc.Metadata["org_id"], rc.Metadata[tenant.MetadataKeyOrgID], rc.Metadata["auth_key_id"] = orgID, orgID, key.ID
			if orgID != "" {
				state.BindPrincipal(orgID, key.ID)
			}
			var err *models.APIError
			if engine != nil {
				err = engine.AdmitSelectedOperation(r.Context(), rc)
			}
			// Preflight computes only declarations. Apply access gates before
			// returning its refusal, but do not move unselected/Monitor access
			// failures out of the handler's existing native/RPC error envelope.
			if state.Required(models.InspectionInput) || state.Required(models.InspectionOutput) {
				access := authplugin.New(keys, true).ProcessRequest(r.Context(), rc)
				if access.Error == nil {
					access = ipaclplugin.New(ipcfg, tenants).ProcessRequest(r.Context(), rc)
				}
				if access.Error != nil {
					err = access.Error
				}
			}
			if err != nil {
				// This request never reaches the handler's pipeline. Finalize it
				// through the same post seam as a pre-plugin refusal so audit,
				// logging and metrics retain the selected-policy attribution.
				// No request/body/usage was populated: accounting remains zero.
				// Successful preflight must NOT finalize here; the handler owns it.
				rc.Flags.ShortCircuited = true
				rc.AddError(err)
				if engine != nil {
					engine.RunPostPlugins(r.Context(), rc)
				}
				rc.Release()
				models.WriteError(w, err)
				return
			}
			rc.Release()
		}

		guard, guarded := newInspectionWriter(w, state)
		handler(guarded, r)
		// An empty handler still implicitly sends 200 when net/http returns.
		// Check it here rather than letting absence of Write bypass the gate.
		guard.finish()
	}
}
