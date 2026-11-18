package tenant

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"sort"
	"strings"
	"time"
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

// KeyPolicyFingerprint is the compatibility wrapper for
// KeyPolicyCacheFingerprint, using a 30-second default timeout. Callers needing
// runtime settings or cache admission MUST use KeyPolicyCacheFingerprint and
// honor its cacheable result; this wrapper cannot report serialization failures.
func (g *GuardrailConfig) KeyPolicyFingerprint(keyID string) string {
	fingerprint, _ := g.KeyPolicyCacheFingerprint(keyID, 30*time.Second)
	return fingerprint
}

// KeyPolicyCacheFingerprint identifies the key, its selected policies, and the
// org checks and effective execution settings that govern those policies. The
// caller must supply the engine's default timeout. The SHA256 digest keeps
// configuration secrets out of cache keys, and includes key identity because a
// check may inspect metadata.auth_key_id even when two keys share policies.
//
// With no selected policies it returns "", true. If configuration cannot be
// serialized, it returns a nonempty sentinel and false: callers MUST bypass
// cache reads and writes, not treat the sentinel as a reusable cache key or an
// unscoped request.
func (g *GuardrailConfig) KeyPolicyCacheFingerprint(keyID string, defaultTimeout time.Duration) (fingerprint string, cacheable bool) {
	matched := g.KeyPoliciesFor(keyID)
	if len(matched) == 0 {
		return "", true
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
		mode := "enforce"
		// Match execution exactly: whitespace around "monitor" enforces.
		if strings.EqualFold(pol.Mode, "monitor") {
			mode = "monitor"
		}
		entries = append(entries, entry{ID: pol.ID, Name: pol.Name, Mode: mode, Priority: pol.Priority, Checks: pol.Checks})
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].ID < entries[j].ID })
	timeout := defaultTimeout
	if g.TimeoutMs > 0 {
		timeout = time.Duration(g.TimeoutMs) * time.Millisecond
	}
	pipelineMode := "parallel"
	if strings.EqualFold(strings.TrimSpace(g.PipelineMode), "sequential") {
		pipelineMode = "sequential"
	}
	// encoding/json sorts map keys, so the encoding is deterministic.
	b, err := json.Marshal(struct {
		KeyID        string                     `json:"key_id"`
		Policies     []entry                    `json:"policies"`
		Checks       map[string]*GuardrailCheck `json:"checks"`
		FailOpen     bool                       `json:"fail_open"`
		Timeout      time.Duration              `json:"timeout"`
		PipelineMode string                     `json:"pipeline_mode"`
	}{
		KeyID: keyID, Policies: entries, Checks: g.Checks,
		FailOpen: g.FailOpen, Timeout: timeout, PipelineMode: pipelineMode,
	})
	if err != nil {
		return "uncacheable-key-policy", false
	}
	digest := sha256.Sum256(b)
	return hex.EncodeToString(digest[:]), true
}
