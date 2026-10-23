package cache

import (
	"crypto/sha256"
	"encoding/hex"
)

// scopePayloadKey is the payload/metadata/property name the vector-store
// backends store the scope under and filter on.
const scopePayloadKey = "cache_scope"

// NoOrgScope is the cache partition for requests whose key carries no org:
// single-tenant deployments, auth turned off, keys minted without an org_id.
// They share one partition with each other and none with any org.
const NoOrgScope = "noorg"

// OrgScope returns the cache partition token for an org. Every cache layer
// keys or filters on this token so a lookup can only ever see entries written
// under the same org; orgID "" maps to NoOrgScope.
//
// orgID must come from the authenticated key (rc.Metadata["key_org_id"], set
// by the auth plugin and Handlers.peekKeyOrgID), never from caller input.
//
// The token is "o" followed by 32 hex digits of SHA-256(orgID): fixed-length,
// lowercase alphanumeric, and never equal to NoOrgScope. Alphanumeric matters
// for vector stores that tokenize text filters (Weaviate's default "word"
// tokenization splits on punctuation): an alphanumeric token is a single word,
// so an Equal filter matches the whole token. Fixed length keeps the token
// prefix-free inside a hashed cache key.
func OrgScope(orgID string) string {
	if orgID == "" {
		return NoOrgScope
	}
	sum := sha256.Sum256([]byte(orgID))
	return "o" + hex.EncodeToString(sum[:16])
}
