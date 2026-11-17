package credentials

import "github.com/futureagi/agentcc-gateway/internal/mcp"

// Caller is who a call on a per_user server acts for: the user, within the
// org that user called from.
type Caller struct {
	OrgID  string
	UserID string
}

// CallerOf returns the user a call acts for on a per_user server (decision
// 30): an OAuth principal's subject, or the user of a personal key, which
// the identity step sets on a key principal only for a key its owner marked
// personal. A shared org key, a runtime or callback token and the admin
// playground carry no user, so the call is never run as a key's creator. ok
// is false when there is no usable user identity.
func CallerOf(p mcp.Principal) (Caller, bool) {
	switch p.Kind {
	case mcp.PrincipalOAuth, mcp.PrincipalKey:
	default:
		return Caller{}, false
	}
	if !orgIDPattern.MatchString(p.OrgID) || !userIDPattern.MatchString(p.UserID) {
		return Caller{}, false
	}
	return Caller{OrgID: p.OrgID, UserID: p.UserID}, true
}

// KeyFor is the cache key of caller's token on serverID.
func KeyFor(c Caller, serverID string) Key {
	return Key{OrgID: c.OrgID, UserID: c.UserID, ServerID: serverID}
}
