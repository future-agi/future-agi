package ratelimit

import (
	"context"
	"strconv"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The auth plugin sets rc.UserID to the key's owner when the request names
// no user (budgets fall back to it). Contract K4: for rate limits a request
// with no user id is not limited per user, so the owner fill-in is not a
// user; a body that names the owner as its user is.
func TestR7PerUserRPMKeyOwnerIsNotAUser(t *testing.T) {
	p := r7Plugin()
	ctx := context.Background()
	for i := 0; i < 3; i++ {
		rc := r7RC("org-a", "k1", "m-chat")
		rc.Metadata["org_rate_limit_per_user_rpm"] = "1"
		rc.Metadata["auth_key_owner"] = "alice"
		rc.UserID = "alice" // the auth plugin's fill-in
		r7Allowed(t, "no user named, owner fill-in #"+strconv.Itoa(i+1), p.ProcessRequest(ctx, rc))
	}
	mk := func() *models.RequestContext {
		rc := r7RC("org-a", "k1", "m-chat")
		rc.Metadata["org_rate_limit_per_user_rpm"] = "1"
		rc.Metadata["auth_key_owner"] = "alice"
		rc.UserID = "alice"
		rc.Request.User = "alice" // the body names the owner as its user
		return rc
	}
	r7Allowed(t, "body user alice #1", p.ProcessRequest(ctx, mk()))
	r7Refused(t, "body user alice #2", p.ProcessRequest(ctx, mk()), "per_user_rpm")
}
