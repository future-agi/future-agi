package credentials

import (
	"net/url"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// connectLinkSecret is a test value of the secret the gateway shares with
// Django (mcp.django.internal_token_secret).
const connectLinkSecret = "m25-test-django-shared-secret-not-a-real-one"

// A connect link opens where Django opens it: its binding is sealed under
// the key Django's start page derives from the secret the gateway shares
// with it (the same derivation, mcp.NewStateCodec over that secret), never
// under the gateway's own state keys, and it names the person, the
// organization and the server it was made for.
func TestConnectLinkOpensWhereDjangoOpensIt(t *testing.T) {
	links, err := ConnectCodec(connectLinkSecret)
	if err != nil {
		t.Fatal(err)
	}
	clock := newTestClock()
	r, err := NewResolver(ResolverOptions{Tokens: tokenClientFor(t, newFakeDjango(t)), Codec: testCodec(t),
		ConnectCodec: links, DjangoBaseURL: testConnectHost, Now: clock.now})
	if err != nil {
		t.Fatal(err)
	}
	k := Key{OrgID: "org-a", UserID: "u2", ServerID: "issues"}
	link := r.connectURL(k, "")
	u, err := url.Parse(link)
	if err != nil || u.Scheme+"://"+u.Host+u.Path != testConnectHost+ConnectStartPath("issues") {
		t.Fatalf("connect link %q, want the server's start page on the Django address", link)
	}
	sealed := u.Query().Get(ConnectBindingParam)

	django, err := mcp.NewStateCodec([]string{connectLinkSecret}, false)
	if err != nil {
		t.Fatal(err)
	}
	b, err := OpenConnectBinding(django, sealed)
	if err != nil {
		t.Fatalf("the binding does not open under the key Django derives from the shared secret: %v", err)
	}
	if b != (ConnectBinding{V: 1, OrgID: "org-a", ServerID: "issues", UserID: "u2"}) {
		t.Fatalf("the binding names %+v, want org-a, issues and u2", b)
	}
	if _, err := OpenConnectBinding(testCodec(t), sealed); err == nil {
		t.Fatal("the binding also opens under the gateway's own state key")
	}
}

// Without a secret shared with Django there is no key to seal a link Django
// can open, so none is made.
func TestConnectCodecNeedsTheSharedSecret(t *testing.T) {
	if _, err := ConnectCodec(""); err == nil {
		t.Fatal("a connect codec was made with no shared secret")
	}
	if _, err := ConnectCodec("too-short-for-a-key"); err == nil {
		t.Fatal("a connect codec was made from a secret shorter than a state key")
	}
}
