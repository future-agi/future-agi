package server

import (
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// presentUsageCost makes a managed response's usage.cost the amount billed,
// not the upstream's charge, which is FutureAGI's cost and not the client's.
// The upstream's cost_details and is_byok are withheld with it.
func presentUsageCost(rc *models.RequestContext) {
	if rc.Response == nil || rc.Response.Usage == nil {
		return
	}
	cacheHit := rc.Flags.CacheHit && hasUpstreamCharge(rc.Response.Usage)
	if !cacheHit && rc.Metadata["billing_mode"] != billingManaged {
		return
	}
	// A copy: the response may be the cache's stored object, shared by every
	// request that hits it.
	resp := *rc.Response
	if cacheHit {
		// A cached answer costs nothing; the stored charge, its details and
		// the upstream that served it are the original request's.
		u := *resp.Usage
		withholdUpstreamCharge(&u)
		resp.Usage = &u
		resp.UpstreamProvider = ""
	} else {
		resp.Usage = billedUsage(rc, resp.Usage)
	}
	rc.Response = &resp
}

// billedUsage is u with a managed request's cost replaced by the amount
// billed. u itself is not modified: the post plugins read the original.
func billedUsage(rc *models.RequestContext, u *models.Usage) *models.Usage {
	if u == nil || rc.Metadata["billing_mode"] != billingManaged {
		return u
	}
	cp := *u
	withholdUpstreamCharge(&cp)
	if billed, err := parseCost(rc.Metadata["cost"]); err == nil {
		cp.Cost = &billed
	}
	return &cp
}

// clientStreamUsage is a stream chunk's usage as the client may see it: a
// managed stream's upstream charge is withheld (the billed cost is computed
// once the stream ends).
func clientStreamUsage(rc *models.RequestContext, u *models.Usage) *models.Usage {
	if u == nil || !hasUpstreamCharge(u) || rc.Metadata["billing_mode"] != billingManaged {
		return u
	}
	cp := *u
	withholdUpstreamCharge(&cp)
	return &cp
}

// hasUpstreamCharge reports whether u carries any part of the upstream's
// charge: cost, cost_details or is_byok.
func hasUpstreamCharge(u *models.Usage) bool {
	return u.Cost != nil || u.CostDetails != nil || u.IsByok != nil
}

// withholdUpstreamCharge clears the upstream's charge from a usage copy.
func withholdUpstreamCharge(u *models.Usage) {
	u.Cost = nil
	u.CostDetails = nil
	u.IsByok = nil
}

func parseCost(s string) (float64, error) { return strconv.ParseFloat(s, 64) }
