package tenant

import (
	"encoding/hex"
	"encoding/json"
	"hash/fnv"
	"sort"
	"strings"
)

// KeyPoliciesFor returns the key policies that apply to requests made with
// keyID (a gateway key ID, as in auth_key_id), in no particular order.
func (g *GuardrailConfig) KeyPoliciesFor(keyID string) []*KeyGuardrailPolicy {
	if g == nil || keyID == "" {
		return nil
	}
	var matched []*KeyGuardrailPolicy
	for _, pol := range g.KeyPolicies {
		if pol == nil {
			continue
		}
		for _, id := range pol.KeyIDs {
			if id == keyID {
				matched = append(matched, pol)
				break
			}
		}
	}
	return matched
}

// KeyPolicyFingerprint identifies the key and what the key policies that apply
// to it check: their IDs, names, modes, priorities and checks. It is "" when
// none apply. Any change to a policy that applies to the key (a check, an
// action, Monitor to Enforce, a rename that changes the order policies run in)
// changes it. It includes the key ID, so two keys never share a fingerprint
// even under the same policies: a check may read the request's key (an
// expression on metadata.auth_key_id, for example).
func (g *GuardrailConfig) KeyPolicyFingerprint(keyID string) string {
	matched := g.KeyPoliciesFor(keyID)
	if len(matched) == 0 {
		return ""
	}
	type entry struct {
		ID       string                     `json:"id"`
		Name     string                     `json:"name"`
		Mode     string                     `json:"mode"`
		Priority int                        `json:"priority"`
		Checks   map[string]*GuardrailCheck `json:"checks"`
	}
	entries := make([]entry, 0, len(matched))
	for _, pol := range matched {
		entries = append(entries, entry{ID: pol.ID, Name: pol.Name, Mode: strings.ToLower(strings.TrimSpace(pol.Mode)), Priority: pol.Priority, Checks: pol.Checks})
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].ID < entries[j].ID })
	// encoding/json sorts map keys, so the encoding is deterministic.
	b, err := json.Marshal(entries)
	if err != nil {
		// A config that cannot be encoded still must not share entries with
		// keys that have no policies.
		b = []byte("unencodable")
		for _, e := range entries {
			b = append(b, '|')
			b = append(b, e.ID...)
		}
	}
	h := fnv.New128a()
	h.Write([]byte(keyID))
	h.Write([]byte{0})
	h.Write(b)
	return hex.EncodeToString(h.Sum(nil))
}
