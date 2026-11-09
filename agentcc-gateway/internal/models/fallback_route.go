package models

import "slices"

// FallbackRoute is the identity a chat request's model fallback plan is
// resolved for: requested, the name the request is routed under; hop, the
// model its first attempt is routed with; inherit, the name a configured chain
// is inherited under when requested has no entry; and, when the request sent
// its own models[], that list less requested and hop.
//
// Routing fixes a request's route once, when it has chosen the first attempt
// and before admission judges it (RequestContext.FixFallbackRoute), and every
// admission and dispatch path then resolves the plan from that route. A
// provider failover or a fallback hop rewrites the request's model after
// that, never its route. The fields are unexported, so no other package can
// change a route once it is built: NewFallbackRoute copies the declared list
// in and Declared copies it out.
type FallbackRoute struct {
	routed    bool
	requested string
	hop       string
	inherit   string
	declares  bool
	declared  []string
}

// NewFallbackRoute is a route for a request named requested whose first
// attempt is routed with hop, and whose configured chains are inherited under
// inherit. declares reports whether the request sent its own models[];
// declared is that list less the request's own identities, possibly empty.
func NewFallbackRoute(requested, hop, inherit string, declares bool, declared []string) FallbackRoute {
	r := FallbackRoute{routed: true, requested: requested, hop: hop, inherit: inherit, declares: declares}
	if declares {
		r.declared = make([]string, len(declared))
		copy(r.declared, declared)
	}
	return r
}

// Routed reports whether r is a route at all: the zero route is a request's
// before routing has chosen its first attempt (auto pool admission).
func (r FallbackRoute) Routed() bool { return r.routed }

// Requested is the name the request is routed under.
func (r FallbackRoute) Requested() string { return r.requested }

// Hop is the model the request's first attempt is routed with.
func (r FallbackRoute) Hop() string { return r.hop }

// Inherit is the name a configured chain is inherited under when the
// requested name has no entry of its own.
func (r FallbackRoute) Inherit() string { return r.inherit }

// Declares reports whether the request sent its own models[].
func (r FallbackRoute) Declares() bool { return r.declares }

// Declared is a copy of the request's models[] less its own identities: its
// whole plan when Declares, possibly empty.
func (r FallbackRoute) Declared() []string { return slices.Clone(r.declared) }

// FixFallbackRoute makes r the route rc's model fallback plan is resolved for,
// and reports whether it did. Only the first route given takes effect: a
// request's route is fixed once, before its first attempt, and is never
// replaced. The zero route is never taken.
func (rc *RequestContext) FixFallbackRoute(r FallbackRoute) bool {
	if rc.fallbackRoute.routed || !r.routed {
		return false
	}
	rc.fallbackRoute = r
	return true
}

// FallbackRoute is the route rc's model fallback plan is resolved for: the
// one fixed by FixFallbackRoute, else the zero route.
func (rc *RequestContext) FallbackRoute() FallbackRoute { return rc.fallbackRoute }
