package byteplus

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

// BytePlus output URLs pass media.ValidateURL by name only. An output host, or a
// host it redirects to, that resolves to loopback when the download client dials
// must be refused at connect time even though it resolved publicly before.
func TestFetch_OutputDNSRebindingRefusedAtDial(t *testing.T) {
	for _, tc := range []struct{ name, output string }{{"direct output host", "https://output.test/video.mp4"}, {"redirect hop", "https://cdn.test/video.mp4"}} {
		t.Run(tc.name, func(t *testing.T) {
			a, err := New(Config{Provider: config.VideoProviderConfig{APIKey: "test-api-key", BaseURL: "https://ark.test/api/v3", ExecutionExpiresAfter: 7200}, CorrelationSecret: "test-correlation-secret"})
			if err != nil {
				t.Fatal(err)
			}
			resolver, rebound := videotest.RebindingResolver(net.ParseIP("203.0.113.9"), net.ParseIP("127.0.0.1"))
			ips, err := resolver.LookupIP(context.Background(), "ip4", "output.test")
			if err != nil || len(ips) != 1 || !ips[0].Equal(net.ParseIP("203.0.113.9")) {
				t.Fatal(ips, err)
			}
			rebound.Store(true)
			guarded := a.download.Transport.(*http.Transport)
			guarded.DialContext = netguard.DialContext(net.Dialer{Resolver: resolver, Timeout: time.Second}, false)
			dialled := 0
			a.download.Transport = rebindRoundTrip(func(r *http.Request) (*http.Response, error) {
				if r.URL.Host == "cdn.test" {
					return &http.Response{StatusCode: http.StatusFound, Header: http.Header{"Location": []string{"https://output.test/video.mp4"}}, Body: io.NopCloser(strings.NewReader("")), Request: r}, nil
				}
				dialled++
				return guarded.RoundTrip(r)
			})
			ref := video.ProviderRef{ProviderJobID: "cgt-test", Outputs: []video.OutputRef{{URL: tc.output}}}
			_, _, e := a.Fetch(context.Background(), ref, 0)
			if e == nil || e.Error() != "provider destination refused" || dialled != 1 {
				t.Fatalf("rebinding output not refused at dial: dialled=%d err=%v", dialled, e)
			}
		})
	}
}
