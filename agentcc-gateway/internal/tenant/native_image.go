package tenant

import "github.com/futureagi/agentcc-gateway/internal/routebinding"

// storedConfig is the single org entry for both ordinary and bound stores.
// In bound mode image is immutable; legacy is a separate replacement identity.
// A present nil config is distinct from both an unseen org and a tombstone.
type storedConfig struct {
	present  bool
	ordinary *OrgConfig
	image    *nativeImage
	stamp    routebinding.Stamp
	legacy   *legacyIdentity
}

func (c *storedConfig) data() *OrgConfig {
	if c == nil || !c.present {
		return nil
	}
	if c.image != nil {
		return c.image.stable
	}
	return c.ordinary
}

type nativeImage struct{ stable, candidate *OrgConfig }

func (i *nativeImage) variant(name string) (*OrgConfig, bool) {
	if i == nil {
		return nil, false
	}
	switch name {
	case PolicyVariantStable:
		return i.stable, true
	case PolicyVariantCandidate:
		return i.candidate, i.candidate != nil
	default:
		return nil, false
	}
}
func prepareNativeImage(src *OrgConfig) (*nativeImage, error) {
	stable, err := cloneConfigData(src)
	if err != nil {
		return nil, err
	}
	image := &nativeImage{stable: stable}
	if stable != nil && stable.Routing != nil && stable.Routing.Rollout != nil {
		// Private precomputation only: never enter the legacy Candidate memoizer.
		image.candidate, err = cloneConfigData(stable.buildCandidate(stable.Routing.Rollout))
		if err != nil {
			return nil, err
		}
	}
	return image, nil
}

type nativeDraft struct {
	image *nativeImage
	hash  [32]byte
}

// Detach the WHOLE batch before invoking any serializer, including outer maps.
// Serializer side effects elsewhere in arbitrary Go are not a sandbox promise.
func prepareNativeBatch(input map[string]*OrgConfig, hash bool) (map[string]nativeDraft, error) {
	out := make(map[string]nativeDraft, len(input))
	for org, c := range input {
		image, err := prepareNativeImage(c)
		if err != nil {
			return nil, err
		}
		out[org] = nativeDraft{image: image}
	}
	if hash {
		for org, draft := range out {
			sum, err := legacyHashForOwnedDraft(draft.image.stable)
			if err != nil {
				return nil, err
			}
			draft.hash = sum
			out[org] = draft
		}
	}
	return out, nil
}

func (s *Store) configDataLocked(org string) *OrgConfig { return s.configs[org].data() }
func (s *Store) configCopyLocked(org string) *OrgConfig {
	entry := s.configs[org]
	if entry == nil || !entry.present {
		return nil
	}
	if entry.image == nil {
		return entry.ordinary
	}
	return legacyData(entry.image, entry.legacy, PolicyVariantStable)
}
