package auth

import (
	"strings"
	"testing"
	"time"
)

// FA's delegated batch recovery lists its org's batches with a GET envelope
// (733 add.13(3)). GET is bound like POST: the route, an empty-body digest,
// the batch scope and the forwarded grant. Only GET /-/batches gains a method
// here; one batch's GET route (733 add.16(a)) is pinned beside it.
func fe1BatchListFixture() (EnvelopeClaims, string, time.Time) {
	c, g, _, _, now := fe1Fixture()
	g.Kind = "batch"
	g.Sub = "batch:33333333-3333-4333-8333-333333333333"
	grant := fe1Raw(g.fields(), "test-only-fa-secret-unavailable-to-gateway")
	c.Method = "GET"
	c.Route = "/-/batches"
	c.Scope = "batch"
	c.Body = EnvelopeDigest(nil)
	c.Grant = EnvelopeDigest([]byte(grant))
	return c, grant, now
}

func TestEnvelope733BatchListAcceptsSignedGET(t *testing.T) {
	c, grant, now := fe1BatchListFixture()
	token := fe1Raw(c.fields(), fe1TestSecret)
	signed, err := SignEnvelope(fe1TestSecret, c)
	if err != nil || signed != token {
		t.Fatal("GET /-/batches request envelope is not canonical:", err)
	}
	v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-time.Minute))
	_, g, err := v.VerifyDelegated(token, grant, "GET", "/-/batches", nil, now)
	if err != nil {
		t.Fatal("GET /-/batches envelope rejected:", err)
	}
	if g.Kind != "batch" || g.Org != fe1TestOrg || "grant:"+g.ID != c.Via {
		t.Fatalf("bound grant differs: %+v", g)
	}
	if _, _, err = v.VerifyDelegated(token, grant, "GET", "/-/batches", nil, now); err == nil {
		t.Fatal("GET listing envelope replayed")
	}
}

func TestEnvelope733BatchListMethodStaysNarrow(t *testing.T) {
	c, grant, now := fe1BatchListFixture()
	verify := func(claims EnvelopeClaims, method, route string, body []byte) error {
		v := NewEnvelopeVerifier(fe1TestSecret, now.Add(-time.Minute))
		_, _, err := v.VerifyDelegated(fe1Raw(claims.fields(), fe1TestSecret), grant, method, route, body, now)
		return err
	}
	for _, method := range []string{"PUT", "PATCH", "DELETE", "HEAD", "get"} {
		bad := c
		bad.Method = method
		if err := verify(bad, method, bad.Route, nil); err == nil {
			t.Errorf("accepted %s /-/batches", method)
		}
	}
	chat := c
	chat.Route = "/-/delegated/v1/chat/completions"
	if err := verify(chat, "GET", chat.Route, nil); err == nil {
		t.Error("accepted GET on the delegated chat route")
	}
	// GET also reads one batch (733 add.16(a)), never its cancel route.
	one := c
	one.Route = "/-/batches/batch-0123456789ab/cancel"
	if err := verify(one, "GET", one.Route, nil); err == nil {
		t.Error("accepted a GET envelope for a batch's cancel route")
	}
	playground := c
	playground.Scope = "playground"
	if err := verify(playground, "GET", playground.Route, nil); err == nil {
		t.Error("accepted a non-batch scope for the batch listing")
	}
	if err := verify(c, "POST", c.Route, nil); err == nil {
		t.Error("a GET envelope authenticated a POST")
	}
	if err := verify(c, "GET", c.Route, []byte(`{"requests":[]}`)); err == nil {
		t.Error("a GET envelope accepted a body it did not sign")
	}
	tampered := c
	tampered.Via = "grant:" + strings.Repeat("c", 32)
	if err := verify(tampered, "GET", c.Route, nil); err == nil {
		t.Error("accepted a GET envelope whose jti differs from the grant")
	}
	// The eval signer stays POST-only.
	eval := c
	eval.Iss, eval.Aud, eval.Route, eval.Scope = "gateway", "fa-eval", "/sdk/api/v1/eval/", "eval"
	if _, err := SignEnvelope(fe1TestSecret, eval); err == nil {
		t.Error("signed a GET eval envelope")
	}
}
