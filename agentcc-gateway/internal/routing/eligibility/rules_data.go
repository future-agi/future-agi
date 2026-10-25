package eligibility

import (
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Data-collection policies of an effective data policy.
const (
	dataCollectionAllow = "allow"
	dataCollectionDeny  = "deny"
)

// EffectivePolicy is the data policy a request runs under: the org's
// routing.data_policy, tightened and never loosened by the caller's
// provider.zdr and provider.data_collection. A managed attempt also denies
// data collection unless the org set managed_models.allow_data_collection.
func EffectivePolicy(org *tenant.OrgConfig, prefs *models.ProviderPrefs, managed bool) models.EffectiveDataPolicy {
	var eff models.EffectiveDataPolicy
	collection := dataCollectionAllow
	if org != nil {
		dp := org.DataPolicy()
		eff.ZDR = dp.ZDR
		if dp.DataCollection == dataCollectionDeny {
			collection = dataCollectionDeny
		}
		if managed && !org.ManagedPolicy().AllowDataCollection {
			collection = dataCollectionDeny
		}
	} else if managed {
		collection = dataCollectionDeny
	}
	if prefs != nil {
		if prefs.ZDR != nil && *prefs.ZDR {
			eff.ZDR = true
		}
		if prefs.DataCollection == dataCollectionDeny {
			collection = dataCollectionDeny
		}
	}
	eff.DataCollection = collection
	return eff
}

// checkDataPolicy is rule 5, the effective data policy:
//   - ZDR needs a ZDR endpoint (managed) or an attested provider (BYOK).
//   - data_collection=deny needs an endpoint that collects no data. An
//     endpoint whose policy is unknown passes only on a managed aggregator
//     attempt, which pins data_collection=deny in the provider object it
//     sends, so the aggregator enforces it; a BYOK provider needs its ZDR
//     attestation.
//   - allowed_regions needs an endpoint region or the provider's data region
//     inside them; managed traffic through an aggregator is never
//     region-pinned (region_unpinnable_on_aggregator).
//   - a preview model is refused in an env the org blocks previews in.
//
// Unknown fails closed for ZDR and regions. On a BYOK aggregator the
// caller's own provider object is forwarded, so only the org's part of the
// policy needs the attestation.
func checkDataPolicy(c *checker) (string, string) {
	in := &c.in
	need := c.eff
	var dp tenant.DataPolicyConfig
	if in.Org != nil {
		dp = in.Org.DataPolicy()
	}
	if !in.Managed && in.Aggregator {
		need = EffectivePolicy(in.Org, nil, false)
	}
	if need.ZDR {
		if code, reason := c.requireZDR(); code != "" {
			return code, reason
		}
	}
	if need.DataCollection == dataCollectionDeny {
		if code, reason := c.requireNoCollection(); code != "" {
			return code, reason
		}
	}
	if len(dp.AllowedRegions) > 0 {
		if code, reason := c.requireRegion(dp.AllowedRegions); code != "" {
			return code, reason
		}
	}
	if in.Entry != nil && in.Entry.Preview && in.Env != "" && equalFoldAny(dp.BlockPreviewInEnvs, in.Env) {
		return CodePreviewBlocked, "preview model " + in.Model + " is blocked in env " + in.Env
	}
	return "", ""
}

func (c *checker) requireZDR() (string, string) {
	const reason = "the data policy requires zero data retention, and no eligible endpoint is known to retain nothing"
	switch {
	case c.hasEndpoints:
		if !c.narrow(func(e *catalog.Endpoint) bool { return e.ZDR != nil && *e.ZDR }) {
			return CodeDataPolicyZDR, reason
		}
	case c.in.Managed:
		return CodeDataPolicyZDR, reason // no endpoint data: unknown fails closed
	case !c.in.ZDRAttested:
		return CodeDataPolicyZDR, "the data policy requires zero data retention, and provider " + c.in.ProviderID + " is not attested ZDR"
	}
	return "", ""
}

func (c *checker) requireNoCollection() (string, string) {
	const reason = "the data policy denies data collection, and no eligible endpoint is known to collect none"
	switch {
	case c.hasEndpoints:
		if !c.narrow(func(e *catalog.Endpoint) bool {
			switch e.DataCollection {
			case catalog.DataCollectionDeny:
				return true
			case catalog.DataCollectionAllow:
				return false
			}
			return e.Source == catalog.SourceOpenRouter // unknown: the pinned policy is enforced upstream
		}) {
			return CodeDataPolicyCollection, reason
		}
	case c.in.Managed:
		if !c.in.Aggregator {
			return CodeDataPolicyCollection, reason
		}
	case !c.in.ZDRAttested:
		return CodeDataPolicyCollection, "the data policy denies data collection, and provider " + c.in.ProviderID + " is not attested"
	}
	return "", ""
}

func (c *checker) requireRegion(allowed []string) (string, string) {
	in := &c.in
	switch {
	case c.hasEndpoints:
		pinnable := c.narrow(func(e *catalog.Endpoint) bool { return e.Source != catalog.SourceOpenRouter })
		if !pinnable {
			return CodeRegionUnpinnable, "managed traffic through an aggregator is not pinned to a region"
		}
		if !c.narrow(func(e *catalog.Endpoint) bool { return regionsAllowed(e.Regions, allowed) }) {
			return CodeRegionNotAllowed, "no eligible endpoint is in an allowed region"
		}
	case in.Managed && in.Aggregator:
		return CodeRegionUnpinnable, "managed traffic through an aggregator is not pinned to a region"
	case in.Managed:
		return CodeRegionNotAllowed, "the endpoint's region is unknown"
	case in.DataRegion == "" || !regionAllowed(in.DataRegion, allowed):
		return CodeRegionNotAllowed, "provider " + in.ProviderID + "'s data region is not in an allowed region"
	}
	return "", ""
}

// regionsAllowed reports whether any of regions is allowed; none known is
// not.
func regionsAllowed(regions, allowed []string) bool {
	for _, r := range regions {
		if regionAllowed(r, allowed) {
			return true
		}
	}
	return false
}

// regionAllowed reports whether region is one of allowed or inside one:
// "eu" allows "eu" and "eu-west-1".
func regionAllowed(region string, allowed []string) bool {
	for _, a := range allowed {
		a = strings.TrimSpace(a)
		if a == "" {
			continue
		}
		if strings.EqualFold(region, a) ||
			(len(region) > len(a) && region[len(a)] == '-' && strings.EqualFold(region[:len(a)], a)) {
			return true
		}
	}
	return false
}
