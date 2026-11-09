package futureagi

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
)

// evalPrincipal is local to one Check, never retained by a shared rule or put
// in check metadata (external checks and logs can serialize that metadata).
type evalPrincipal struct {
	org, user, workspace, via, grant string
	grantExp                         int64
}

func requestPrincipal(ctx context.Context, input *guardrails.CheckInput) (evalPrincipal, error) {
	if grant, c, ok := auth.DelegatedGrant(ctx); ok {
		if grant == "" || c.Mode != "grant" || c.Aud != "fa-eval-grant" || time.Now().Unix() > c.Exp {
			return evalPrincipal{}, fmt.Errorf("gateway_service requires a live delegated principal")
		}
		return evalPrincipal{c.Org, c.User, c.Workspace, "grant:" + c.ID, grant, c.Exp}, nil
	}
	// Metadata contains labels written by the pipeline auth plugin. Bind the
	// identity to KeyAuth's unexported context value too: metadata or network
	// headers alone (including on auth-disabled paths) authorize no signature.
	id, ok := middleware.KeyIdentity(ctx)
	if !ok || input == nil || id.KeyID == "" || id.OrgID == "" || input.Metadata["auth_key_id"] != id.KeyID || input.Metadata["key_org_id"] != id.OrgID || id.UserID == "" {
		return evalPrincipal{}, fmt.Errorf("gateway_service requires an authenticated person principal")
	}
	workspace := input.Metadata["key_workspace_id"]
	if workspace == "" {
		workspace = "-"
	}
	return evalPrincipal{org: id.OrgID, user: id.UserID, workspace: workspace, via: "key:" + id.KeyID}, nil
}

// signAttempt is called inside doRequest, not outside the retry loop. A nonce
// consumed by FA remains consumed even if its evaluation subsequently fails.
func (g *FutureAGIGuardrail) signAttempt(req *http.Request, body []byte, p evalPrincipal) error {
	if !g.operatorBackend || req.URL.String() != g.baseURL+"/sdk/api/v1/eval/" {
		return fmt.Errorf("gateway_service refuses a destination other than the operator FI_BASE_URL")
	}
	now := time.Now().Unix()
	if p.grant != "" && now > p.grantExp {
		return fmt.Errorf("gateway_service requires a live delegated principal")
	}
	id, err := auth.NewEnvelopeID()
	if err != nil {
		return fmt.Errorf("gateway_service could not create an envelope nonce")
	}
	digest := "-"
	if p.grant != "" {
		digest = auth.EnvelopeDigest([]byte(p.grant))
	}
	c := auth.EnvelopeClaims{
		Mode: "request", Kid: auth.EnvelopeKid, Iss: "gateway", Aud: "fa-eval",
		Method: req.Method, Route: req.URL.Path, Iat: now, Exp: now + 60, ID: id,
		Kind: "-", Org: p.org, Workspace: p.workspace, User: p.user, Via: p.via,
		Sub: "-", Scope: "eval", Body: auth.EnvelopeDigest(body), Grant: digest,
	}
	token, err := auth.SignEnvelope(g.serviceSecret, c)
	if err != nil {
		return fmt.Errorf("gateway_service could not sign the person principal")
	}
	req.Header.Set(auth.EnvelopeHeader, token)
	if p.grant != "" {
		req.Header.Set(auth.GrantHeader, p.grant)
	}
	return nil
}
