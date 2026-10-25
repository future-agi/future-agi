package ratelimit

import (
	"context"
	"strconv"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The auth plugin sets rc.UserID to the key's owner when the request names
// no user (budgets fall back to it). For rate limits a request
// with no user id is not limited per user, so the owner fill-in is not a
// user; a body that names the owner as its user is.
func TestPerUserRPMKeyOwnerIsNotAUser(t *testing.T) {
	p := newLimitsPlugin()
	ctx := context.Background()
	for i := 0; i < 3; i++ {
		rc := orgLimitRC("org-a", "k1", "m-chat")
		rc.Metadata["org_rate_limit_per_user_rpm"] = "1"
		rc.Metadata["auth_key_owner"] = "alice"
		rc.UserID = "alice" // the auth plugin's fill-in
		wantAllowed(t, "no user named, owner fill-in #"+strconv.Itoa(i+1), p.ProcessRequest(ctx, rc))
	}
	mk := func() *models.RequestContext {
		rc := orgLimitRC("org-a", "k1", "m-chat")
		rc.Metadata["org_rate_limit_per_user_rpm"] = "1"
		rc.Metadata["auth_key_owner"] = "alice"
		rc.UserID = "alice"
		rc.Request.User = "alice" // the body names the owner as its user
		return rc
	}
	wantAllowed(t, "body user alice #1", p.ProcessRequest(ctx, mk()))
	wantRefused(t, "body user alice #2", p.ProcessRequest(ctx, mk()), "per_user_rpm")
}
