package server

import (
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/routing/eligibility"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// managedOffer applies serving's org policy to an exact listing, including
// endpoint/data restrictions. The access view may ask about opting in without
// mutating stored policy. Request-specific constraints still run at dispatch.
func managedOffer(l catalog.Listing, org *tenant.OrgConfig, now time.Time, optIn bool, handlers ...*Handlers) (eligibility.Verdict, bool) {
	if !l.Model.ManagedBillable(l.Source) || !l.Model.Servable(now) {
		return eligibility.Verdict{}, false
	}
	if optIn {
		var copyOrg tenant.OrgConfig
		if org != nil {
			copyOrg = *org
		}
		var m tenant.ManagedModelsConfig
		if copyOrg.ManagedModels != nil {
			m = *copyOrg.ManagedModels
		}
		m.Enabled = true
		copyOrg.ManagedModels = &m
		org = &copyOrg
	}
	in := eligibility.Input{Model: l.Model.ID, ProviderID: l.ProviderID, Org: org, Managed: true,
		Aggregator: l.Source == catalog.SourceOpenRouter, Entry: l.Model, Now: now}
	if len(handlers) > 0 && handlers[0] != nil {
		h := handlers[0]
		in.Shutdowns = h.deprecations
		in.Host = h.deprecationHost(org, l.ProviderID)
	}
	v := eligibility.Check(&in)
	return v, v.OK
}

// offerEndpoints is exactly the default endpoint set serving can pin, not a
// cheaper endpoint outside that set. A listing with no endpoint table is its
// own supported card.
func offerEndpoints(l catalog.Listing, v *eligibility.Verdict) []catalog.Endpoint {
	set := v.EndpointSet(eligibility.MaxEndpointSet)
	if len(l.Model.Endpoints) == 0 {
		return []catalog.Endpoint{{Source: l.Source, Prices: l.Model.Prices}}
	}
	out := make([]catalog.Endpoint, 0, len(set))
	for _, e := range set {
		out = append(out, *e)
	}
	return out
}

// accessOffer selects a callable ID for a canonical group. Prefer its exact
// base listing; a base-less free offering must advertise its :free ID, not an
// uncallable synthetic base. Prices and source IDs describe that same offer.
func accessOffer(cm *catalog.CanonicalModel, org *tenant.OrgConfig, now time.Time, handlers ...*Handlers) (string, []catalog.Listing, []catalog.Endpoint) {
	var allowed []catalog.Listing
	verdicts := map[string]eligibility.Verdict{}
	id := ""
	for _, l := range cm.Listings {
		v, ok := managedOffer(l, org, now, true, handlers...)
		if !ok {
			continue
		}
		allowed = append(allowed, l)
		verdicts[l.ProviderID+"\x00"+l.Model.ID] = v
		if id == "" || l.Model.ID == cm.ID || (id != cm.ID && l.Model.ID < id) {
			id = l.Model.ID
		}
	}
	var chosen []catalog.Listing
	var endpoints []catalog.Endpoint
	for _, l := range allowed {
		if l.Model.ID != id && !(id == cm.ID && !catalog.HasVariant(l.Model.ID) && catalog.ManagedCanonical(l.ProviderID, l.Model.ID) == id) {
			continue
		}
		chosen = append(chosen, l)
		v := verdicts[l.ProviderID+"\x00"+l.Model.ID]
		endpoints = append(endpoints, offerEndpoints(l, &v)...)
	}
	return id, chosen, endpoints
}
