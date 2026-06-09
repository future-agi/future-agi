package tenant

import (
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
)

// MetadataKeyOrgID is the metadata key where the auth plugin stores the org ID.
// Set by the auth plugin from apiKey.Metadata["org_id"] → rc.Metadata["key_org_id"].
const MetadataKeyOrgID = "key_org_id"

// ResolveOrgConfig extracts the org ID from request metadata and returns the
// org's config from the store. Returns nil if no org ID is present or no
// config is found (caller should fall back to base config).
func (s *Store) ResolveOrgConfig(metadata map[string]string) (string, *OrgConfig) {
	orgID := metadata[MetadataKeyOrgID]
	if orgID == "" {
		return "", nil
	}
	return orgID, s.Get(orgID)
}

// GuardrailPolicy converts the org's guardrail config into a policy.Policy
// that the guardrail engine understands. Returns nil if no guardrail
// overrides are configured for the org.
func (cfg *OrgConfig) GuardrailPolicy() *policy.Policy {
	if cfg == nil || cfg.Guardrails == nil || len(cfg.Guardrails.Checks) == 0 {
		return nil
	}

	overrides := make(map[string]policy.Override, len(cfg.Guardrails.Checks))
	for name, check := range cfg.Guardrails.Checks {
		if check == nil {
			continue
		}
		ov := policy.Override{
			Disabled: !check.Enabled,
		}
		if check.Action != "" {
			ov.Action = check.Action
		}
		if check.ConfidenceThreshold > 0 {
			t := check.ConfidenceThreshold
			ov.Threshold = &t
		}
		overrides[name] = ov
	}

	if len(overrides) == 0 {
		return nil
	}

	return &policy.Policy{
		Overrides: overrides,
	}
}
