package vendors_test

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
)

func TestW4bCallerIdentity(t *testing.T) {
	for _, p := range []struct{ platform, app string }{{"google", "gmail"}, {"microsoft", "microsoft-outlook"}, {"arcade", "customer-gateway"}} {
		for _, kind := range []mcp.PrincipalKind{mcp.PrincipalOAuth, mcp.PrincipalKey, mcp.PrincipalRuntime, mcp.PrincipalCallback, mcp.PrincipalAdmin, "missing", "shared"} {
			t.Run(p.platform+"/"+string(kind), func(t *testing.T) {
				f := newFixture(t, p.platform, p.app, nil)
				tok := f.give(t, "org-a", "u1")
				f.give(t, "org-a", "u2")
				bearer := caller("org-a", "u1", kind)
				if kind == "missing" {
					bearer = "w4b-no-user"
				}
				if kind == "shared" {
					bearer = sharedCaller
				}
				a := f.call(t, bearer)
				if kind == mcp.PrincipalKey || kind == mcp.PrincipalOAuth {
					connected(t, a)
					equal(t, f.fingerprints(), []string{bearerHash(tok)})
					equal(t, f.cp.fetched(), []credentials.Key{key("org-a", "u1")})
				} else {
					denied(t, a, mcp.CodeAuthRequired)
					equal(t, metadata(t, a)["reason"], mcp.AuthReasonNoUserIdentity)
					equal(t, metadata(t, a)["server_id"], "service")
					if _, ok := metadata(t, a)["connect_url"]; ok {
						t.Fatal("no identity must not create a personal connect link")
					}
					equal(t, len(f.cp.fetched()), 0)
					equal(t, len(f.fingerprints()), 0)
				}
			})
		}
	}
}

func TestW4bUserOrgIsolation(t *testing.T) {
	for _, p := range []struct{ platform, app string }{{"google", "gmail"}, {"microsoft", "microsoft-outlook"}, {"arcade", "customer-gateway"}} {
		t.Run(p.platform, func(t *testing.T) {
			f := newFixture(t, p.platform, p.app, nil)
			values := map[credentials.Key]string{}
			for _, org := range []string{"org-a", "org-b"} {
				for _, user := range []string{"u1", "u2"} {
					values[key(org, user)] = f.give(t, org, user)
				}
			}
			order := []credentials.Key{key("org-a", "u1"), key("org-b", "u1"), key("org-a", "u2"), key("org-b", "u2"), key("org-a", "u1"), key("org-b", "u1")}
			var hashes []string
			for _, k := range order {
				connected(t, f.call(t, userCaller(k.OrgID, k.UserID)))
				hashes = append(hashes, bearerHash(values[k]))
			}
			equal(t, f.fingerprints(), hashes)
			// The vendor flow revalidates every personal grant with the authority, even when
			// its sealed token is cached; the rechecks must keep the same identity.
			equal(t, f.cp.fetched(), order)
			for k, value := range values {
				tok, ok, err := f.cache.Get(context.Background(), k)
				if err != nil || !ok || tok.AccessToken != value {
					t.Fatal("cache does not preserve org/user binding")
				}
			}
		})
	}
}

func TestW4bCallerAuthorizationNeverForwarded(t *testing.T) {
	for _, p := range []struct{ platform, app string }{{"google", "gmail"}, {"microsoft", "microsoft-outlook"}, {"arcade", "customer-gateway"}} {
		t.Run(p.platform, func(t *testing.T) {
			f := newFixture(t, p.platform, p.app, nil)
			up1 := f.give(t, "org-a", "u1")
			up2 := f.give(t, "org-a", "u2")
			// Re-resolve the SAME call, mutate only the first copy, and confirm the
			// second copy/cache remain intact. The first gets restored for dispatch.
			f.bridge.inspect = func(cc *mcp.CallContext, a *mcp.AuthOverride, d *mcp.Denial) {
				if d != nil || a == nil {
					t.Fatal("connected adapter did not return an override")
				}
				other, den := f.vendor.Resolve(cc, p.platform)
				if den != nil || other == nil {
					t.Fatal("repeat resolution failed")
				}
				want := other.Header.Get("Authorization")
				a.Header.Set("Authorization", "Bearer w4b-mutated-copy")
				if other.Header.Get("Authorization") != want {
					t.Fatal("per-call headers alias")
				}
				a.Header.Set("Authorization", want)
				for _, formatted := range []string{fmt.Sprint(a), fmt.Sprintf("%#v", a)} {
					if strings.Contains(formatted, up1) || strings.Contains(formatted, up2) || strings.Contains(formatted, "w4b-gateway-") {
						t.Fatal("formatted override leaked a credential")
					}
				}
			}
			connected(t, f.call(t, userCaller("org-a", "u1")))
			connected(t, f.call(t, userCaller("org-a", "u2")))
			connected(t, f.call(t, userCaller("org-a", "u1")))
			equal(t, f.fingerprints(), []string{bearerHash(up1), bearerHash(up2), bearerHash(up1)})
			for _, h := range f.fingerprints() {
				if h == bearerHash(userCaller("org-a", "u1")) || h == bearerHash(sharedCaller) {
					t.Fatal("caller Authorization forwarded")
				}
			}
		})
	}
}
