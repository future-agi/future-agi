package contractwatch

import (
	"fmt"
	"net"
	"net/http"
	"os"
	"testing"
)

// TestMain makes any attempt to reach a non-loopback host fail the run.
// Every lister in this package takes its *http.Client from the caller, and
// the tests hand them httptest clients; this guard covers anything that
// would fall back to the default transport or dial on its own.
func TestMain(m *testing.M) {
	guard := &guardTransport{}
	http.DefaultTransport = guard
	http.DefaultClient = &http.Client{Transport: guard}
	os.Exit(m.Run())
}

// guardTransport refuses every request that is not to a loopback address.
type guardTransport struct{}

func (guardTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	if ip := net.ParseIP(r.URL.Hostname()); ip != nil && ip.IsLoopback() {
		return (&http.Transport{}).RoundTrip(r)
	}
	return nil, fmt.Errorf("contractwatch tests: network access to %s refused", r.URL.Host)
}

// refusingClient fails every request and records where it was going, for
// tests that build the real listers (real hosts) and must not reach them.
type refusingClient struct{ hosts []string }

func (c *refusingClient) client() *http.Client {
	return &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		c.hosts = append(c.hosts, r.URL.Host)
		return nil, fmt.Errorf("refused: %s", r.URL.Host)
	})}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestNetworkGuard(t *testing.T) {
	_, err := http.Get("https://api.openai.com/v1/models")
	if err == nil {
		t.Fatal("the default client reached a real host")
	}
}
