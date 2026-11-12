package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

// A synthetic flat-unit proof is not permission to reinterpret the bundled
// vendor catalogue. Keep every family ID closed until its tariff/bounds and
// adapter are qualified, including the five expired image-priced variants.
func TestManagedDimensionalFamilyRemainsFailClosed(t *testing.T) {
	all, install := installBundledCatalogue(t)
	at := time.Date(2026, 10, 27, 12, 0, 0, 0, time.UTC)
	images, lyria, expired := 0, 0, 0
	for id, m := range all {
		image := m.Prices.Image != 0 && strings.Contains(id, "google/gemini")
		music := id == "google/lyria-3-pro-preview" || id == "google/lyria-3-clip-preview"
		if !image && !music {
			continue
		}
		if image {
			images++
		} else {
			lyria++
		}
		if !m.Servable(at) {
			expired++
		}
		if m.ManagedBillable(catalog.SourceOpenRouter) {
			t.Errorf("unqualified family ID became billable: %s", id)
		}
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/%t", id, surface, stream), func(t *testing.T) {
					r := runManagedOn(t, install, surface, id, stream, managedGatewayOptions{}, nil, false, nil)
					if r.status != http.StatusForbidden || r.calls != 0 || r.spent != 0 || r.held != 0 || len(r.recs) != 0 {
						t.Fatalf("unqualified listing dispatched or charged: status=%d calls=%d spent=%d held=%d records=%v", r.status, r.calls, r.spent, r.held, r.recs)
					}
				})
			}
		}
	}
	if images != 32 || lyria != 2 || expired != 5 {
		t.Fatalf("family changed: images=%d lyria=%d expired=%d", images, lyria, expired)
	}
}
