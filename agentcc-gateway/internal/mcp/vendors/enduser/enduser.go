// Package enduser resolves partner identities without a dependency on the MCP
// runtime. The runtime supplies only authenticated principal fields, never a key creator.
package enduser

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"regexp"
	"strings"
)

type Principal struct{ Kind, OrgID, UserID string }
type Policy struct{ AllowAsserted bool }
type Identity struct{ Source, Subject, Hash string }

var (
	ErrRequired   = errors.New("end_user_identity_required")
	ErrConflict   = errors.New("end_user_conflict")
	assertedShape = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:@|+=-]{0,127}$`)
)

func Resolve(p Principal, asserted string, policy Policy) (Identity, error) {
	if p.OrgID == "" || (p.Kind != "oauth" && p.Kind != "key") {
		return Identity{}, ErrRequired
	}
	source, subject := "member", p.UserID
	if subject != "" {
		if asserted != "" && asserted != subject {
			return Identity{}, ErrConflict
		}
	} else {
		if p.Kind != "key" || !policy.AllowAsserted || !assertedShape.MatchString(asserted) || strings.HasPrefix(asserted, "fau_") {
			return Identity{}, ErrRequired
		}
		source, subject = "asserted", asserted
	}
	sum := sha256.Sum256([]byte("v1|" + p.OrgID + "|" + source + "|" + subject))
	return Identity{Source: source, Subject: subject, Hash: hex.EncodeToString(sum[:8])}, nil
}
