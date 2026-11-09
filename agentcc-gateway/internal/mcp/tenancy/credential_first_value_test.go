package tenancy

import (
	"context"
	"errors"
	"net/http"
	"reflect"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// A matching poisoned cache entry proves the FIRST-value refusal is before a
// cache hit, not merely before constructing an unauthenticated client.
type firstValueCacheProbe struct {
	*fakeClient
	healthReads atomic.Int32
}

func (c *firstValueCacheProbe) Healthy() bool {
	c.healthReads.Add(1)
	return true
}

func TestPersonCredentialEmptyFirstValueNeverTouchesCache(t *testing.T) {
	for _, headerName := range []string{"Authorization", "X-Api-Key"} {
		for _, cached := range []bool{false, true} {
			name := headerName + "/cold"
			if cached {
				name = headerName + "/matching-cache-entry"
			}
			t.Run(name, func(t *testing.T) {
				const person = "first-value-person"
				header := http.Header{headerName: {"", "synthetic-later-value"}}
				if headerName == "Authorization" {
					header[headerName][1] = "Bearer synthetic-later-value"
				}
				override := mcp.AuthOverride{Header: header}
				if !reflect.DeepEqual(authFromOverride(override), mcp.AuthConfig{}) {
					t.Error("an empty first value must not yield any credential field")
				}
				r := newRig(t, Options{})
				u := r.factory.upstream(urlA, "search")
				dials := &countingDialer{}
				r.factory.dialer = dials
				r.push("org-a", 1, `{"enabled":true,"servers":{"mine":{"url":"`+urlA+`","credential":{"mode":"per_user"}}}}`)
				_, pool := r.resolve("org-a")
				entry := pool.(*Pool).current()["mine"]
				probe := &firstValueCacheProbe{fakeClient: &fakeClient{up: u}}
				var saved *userClient
				if cached {
					ctx, cancel := context.WithCancel(entry.ctx)
					saved = &userClient{client: probe, authFP: fingerprint([]any{header, 0}), ctx: ctx, cancel: cancel}
					entry.mu.Lock()
					entry.users[person] = saved
					entry.mu.Unlock()
				}

				client, err := pool.Client("mine", person, override)
				if client != nil {
					t.Error("empty first value returned a client")
				}
				var denial *mcp.Denial
				if !errors.As(err, &denial) || denial.Code != "auth_required" || denial.Type != "auth_required" || !denial.InBand() {
					t.Error("empty first value must return the in-band credential refusal")
				} else if !reflect.DeepEqual(denial.Metadata, map[string]any{"server_id": "mine", "reason": "no_token"}) {
					t.Error("empty first value must have the exact no_token metadata")
				}
				entry.mu.Lock()
				unchanged := entry.users[person] == saved && len(entry.userBuilds) == 0
				entry.mu.Unlock()
				if !unchanged || probe.healthReads.Load() != 0 || probe.closeCall.Load() != 0 {
					t.Error("empty first value accessed, replaced or closed a cached credential")
				}
				if dials.n.Load() != 0 || r.factory.totalBuilds() != 0 || len(u.called()) != 0 {
					t.Error("empty first value performed upstream work")
				}
			})
		}
	}
}
