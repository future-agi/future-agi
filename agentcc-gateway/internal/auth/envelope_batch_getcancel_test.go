package auth

import (
	"testing"
	"time"
)

// FA reads one delegated batch with GET /-/batches/{id} and cancels it with
// POST /-/batches/{id}/cancel (733 add.16(a)). Both bind the exact route, the
// body digest, the batch scope and the forwarded grant, like the listing.
const batchItem733 = "/-/batches/batch-0123456789ab"

func fe1BatchRouteFixture(method, route string) (EnvelopeClaims, string, time.Time) {
	c, grant, now := fe1BatchListFixture()
	c.Method, c.Route = method, route
	return c, grant, now
}

func TestEnvelope733BatchGetCancelAcceptsSignedRoutes(t *testing.T) {
	for _, tc := range []struct{ method, route string }{
		{"GET", batchItem733},
		{"POST", batchItem733 + "/cancel"},
	} {
		c, grant, now := fe1BatchRouteFixture(tc.method, tc.route)
		token := fe1Raw(c.fields(), fe1TestSecret)
		signed, err := SignEnvelope(fe1TestSecret, c)
		if err != nil || signed != token {
			t.Fatalf("%s %s request envelope is not canonical: %v", tc.method, tc.route, err)
		}
		v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-time.Minute))
		_, g, err := v.VerifyDelegated(token, grant, tc.method, tc.route, nil, now)
		if err != nil {
			t.Fatalf("%s %s envelope rejected: %v", tc.method, tc.route, err)
		}
		if g.Kind != "batch" || g.Org != fe1TestOrg || "grant:"+g.ID != c.Via {
			t.Fatalf("%s %s bound grant differs: %+v", tc.method, tc.route, g)
		}
		if _, _, err = v.VerifyDelegated(token, grant, tc.method, tc.route, nil, now); err == nil {
			t.Fatalf("%s %s envelope replayed", tc.method, tc.route)
		}
	}
}

func TestEnvelope733BatchGetCancelGrammarStaysNarrow(t *testing.T) {
	c, grant, now := fe1BatchListFixture()
	at := func(method, route string) EnvelopeClaims {
		x := c
		x.Method, x.Route = method, route
		return x
	}
	refuse := func(name string, claims EnvelopeClaims) {
		t.Helper()
		v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-time.Minute))
		if _, _, err := v.VerifyDelegated(fe1Raw(claims.fields(), fe1TestSecret), grant, claims.Method, claims.Route, nil, now); err == nil {
			t.Errorf("verified %s: %s %s", name, claims.Method, claims.Route)
		}
		if _, err := SignEnvelope(fe1TestSecret, claims); err == nil {
			t.Errorf("signed %s: %s %s", name, claims.Method, claims.Route)
		}
	}
	// Each route takes only its own method.
	refuse("POST on a single batch", at("POST", batchItem733))
	refuse("GET on cancel", at("GET", batchItem733+"/cancel"))
	for _, method := range []string{"PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "get", "post"} {
		refuse(method+" on a single batch", at(method, batchItem733))
		refuse(method+" on cancel", at(method, batchItem733+"/cancel"))
	}
	// Only the ids the gateway makes ("batch-" and 12 lowercase hex digits),
	// and only these two routes under them.
	for _, route := range []string{
		"/-/batches/batch-0123456789AB",
		"/-/batches/batch-0123456789a",
		"/-/batches/batch-0123456789abc",
		"/-/batches/0123456789ab",
		"/-/batches/batch_0123456789ab",
		"/-/batches/nope",
		"/-/batches/batch-0123456789ab/",
		"/-/batches/batch-0123456789ab.json",
		"/-/batches/batch-0123456789ab/results",
		"/-/batches/batch-0123456789ab/cancel/",
		"/-/batches/batch-0123456789ab/cancel/x",
		"/-/batches/batch-0123456789ab/../batch-ba9876543210",
		"/-/batches//batch-0123456789ab",
		"/-/batches/batch-0123456789ab/batch-ba9876543210",
	} {
		refuse("GET "+route, at("GET", route))
		refuse("POST "+route, at("POST", route))
	}
	// The batch scope only.
	for _, scope := range []string{"playground", "tryit", "guardrail-test"} {
		get := at("GET", batchItem733)
		get.Scope = scope
		refuse(scope+" scope on a single batch", get)
		cancel := at("POST", batchItem733+"/cancel")
		cancel.Scope = scope
		refuse(scope+" scope on cancel", cancel)
	}
	// GET stays refused on the delegated chat route.
	chat := at("GET", "/-/delegated/v1/chat/completions")
	chat.Scope = "playground"
	refuse("GET on the delegated chat route", chat)
	// The eval signer never reaches a batch route, by either method.
	for _, route := range []string{batchItem733, batchItem733 + "/cancel"} {
		for _, method := range []string{"GET", "POST"} {
			eval := at(method, route)
			eval.Iss, eval.Aud, eval.Scope = "gateway", "fa-eval", "eval"
			if _, err := SignEnvelope(fe1TestSecret, eval); err == nil {
				t.Errorf("signed an eval envelope for %s %s", method, route)
			}
		}
	}
	// An envelope binds its own batch, its own route and its empty body.
	cross := func(name string, claims EnvelopeClaims, method, route string, body []byte) {
		t.Helper()
		v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-time.Minute))
		if _, _, err := v.VerifyDelegated(fe1Raw(claims.fields(), fe1TestSecret), grant, method, route, body, now); err == nil {
			t.Errorf("%s: verified as %s %s", name, method, route)
		}
	}
	get := at("GET", batchItem733)
	cancel := at("POST", batchItem733+"/cancel")
	cross("GET envelope on another batch", get, "GET", "/-/batches/batch-ba9876543210", nil)
	cross("GET envelope on the cancel route", get, "POST", batchItem733+"/cancel", nil)
	cross("GET envelope with a body it did not sign", get, "GET", batchItem733, []byte(`{"x":1}`))
	cross("cancel envelope on another batch", cancel, "POST", "/-/batches/batch-ba9876543210/cancel", nil)
	cross("cancel envelope as a GET", cancel, "GET", batchItem733, nil)
	cross("listing envelope on a single batch", at("GET", "/-/batches"), "GET", batchItem733, nil)
}
