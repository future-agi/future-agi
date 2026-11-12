package openrouter

import (
	"context"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/netguard"
	video "github.com/futureagi/agentcc-gateway/internal/providers/video"
	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
)

type rebindRoundTrip func(*http.Request) (*http.Response, error)

func (f rebindRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// The authenticated content endpoint redirects to a storage host that resolved
// to a public address earlier but resolves to loopback when the download client
// dials it. The guarded dialer checks the address actually dialled, so the
// redirect hop is refused at connect time and never carries the API key.
func TestFetch_ContentRedirectDNSRebindingRefusedAtDial(t *testing.T) {
	a, err := New(Config{Provider: config.VideoProviderConfig{APIKey: "test-api-key", BaseURL: "https://openrouter.test/api/v1"}})
	if err != nil {
		t.Fatal(err)
	}
	resolver, rebound := videotest.RebindingResolver(net.ParseIP("203.0.113.9"), net.ParseIP("127.0.0.1"))
	ips, err := resolver.LookupIP(context.Background(), "ip4", "storage.test")
	if err != nil || len(ips) != 1 || !ips[0].Equal(net.ParseIP("203.0.113.9")) {
		t.Fatal(ips, err)
	}
	rebound.Store(true)
	guarded := a.download.Transport.(*http.Transport)
	guarded.DialContext = netguard.DialContext(net.Dialer{Resolver: resolver, Timeout: time.Second}, false)
	hops := 0
	a.download.Transport = rebindRoundTrip(func(r *http.Request) (*http.Response, error) {
		if r.URL.Host == "openrouter.test" {
			return &http.Response{StatusCode: http.StatusFound, Header: http.Header{"Location": []string{"https://storage.test/object.mp4"}}, Body: io.NopCloser(strings.NewReader("")), Request: r}, nil
		}
		hops++
		if r.URL.Host != "storage.test" || r.Header.Get("Authorization") != "" {
			t.Errorf("unexpected hop %s auth=%q", r.URL, r.Header.Get("Authorization"))
		}
		return guarded.RoundTrip(r)
	})
	_, _, e := a.Fetch(context.Background(), video.ProviderRef{ProviderJobID: "gen-vid-1"}, 0)
	if e == nil || e.Error() != "provider destination refused" || hops != 1 {
		t.Fatalf("rebinding redirect not refused at dial: hops=%d err=%v", hops, e)
	}
}
