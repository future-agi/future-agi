package capability

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"strings"
)

// Fingerprint uses normalized numbers, deterministic key ordering, and media
// content digests. A URL requires ingress verification; signed URLs never enter
// the canonical fingerprint. Canonical requests for workers retain sources in
// Resolved.Request separately, so hashing does not discard their input.
func Fingerprint(res Resolved) ([]byte, string, error) {
	b, err := json.Marshal(res)
	if err != nil {
		return nil, "", err
	}
	var object map[string]any
	if err = json.Unmarshal(b, &object); err != nil {
		return nil, "", err
	}
	req := object["request"].(map[string]any)
	if len(res.Request.Inputs) > 0 {
		inputs := make([]any, 0, len(res.Request.Inputs))
		for _, in := range res.Request.Inputs {
			digest := strings.ToLower(in.Digest)
			if in.Source.Data != "" {
				data, err := base64.StdEncoding.DecodeString(in.Source.Data)
				if err != nil {
					return nil, "", err
				}
				sum := sha256.Sum256(data)
				actual := hex.EncodeToString(sum[:])
				if digest != "" && digest != actual {
					return nil, "", invalid("invalid_parameter", "inputs", "media digest mismatch")
				}
				digest = actual
			}
			raw, err := hex.DecodeString(digest)
			if err != nil || len(raw) != 32 {
				return nil, "", invalid("invalid_parameter", "inputs", "verified sha256 media digest required")
			}
			inputs = append(inputs, map[string]any{"role": in.Role, "media_type": in.MediaType, "sha256": digest})
		}
		req["inputs"] = inputs
	}
	canonical, err := json.Marshal(object)
	if err != nil {
		return nil, "", err
	}
	sum := sha256.Sum256(canonical)
	return canonical, hex.EncodeToString(sum[:]), nil
}
