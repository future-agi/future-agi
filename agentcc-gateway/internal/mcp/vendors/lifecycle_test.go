package vendors_test

import (
	"context"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/cmd/agentcc-mcpmock/mcpmock"
	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
)

func TestW4bConnectBinding(t *testing.T) {
	for _, p := range []struct{ platform, app string }{{"google", "gmail"}, {"microsoft", "microsoft-outlook"}, {"arcade", "customer-gateway"}} {
		t.Run(p.platform, func(t *testing.T) {
			f := newFixture(t, p.platform, p.app, nil)
			before := time.Now()
			a := f.call(t, userCaller("org-b", "u1"))
			denied(t, a, mcp.CodeAuthRequired)
			meta := metadata(t, a)
			equal(t, meta["reason"], mcp.AuthReasonNoToken)
			equal(t, meta["server_id"], "service")
			link, ok := meta["connect_url"].(string)
			if !ok {
				t.Fatal("missing connect URL")
			}
			u, err := url.Parse(link)
			if err != nil {
				t.Fatal(err)
			}
			equal(t, u.Scheme+"://"+u.Host+u.Path, f.cp.url+credentials.ConnectStartPath("service"))
			sealed := u.Query().Get(credentials.ConnectBindingParam)
			b, err := credentials.OpenConnectBinding(f.codec, sealed)
			if err != nil {
				t.Fatal(err)
			}
			equal(t, b, credentials.ConnectBinding{V: 1, OrgID: "org-b", ServerID: "service", UserID: "u1"})
			equal(t, credentials.ConnectBindingTTL, 10*time.Minute)
			assertBindingTTL(t, sealed, before)
			for _, legacy := range []string{"app", "end_user", "authorization_url"} {
				if _, ok := meta[legacy]; ok {
					t.Fatal("legacy vendor-consent payload returned")
				}
			}
			if strings.Contains(link, userCaller("org-b", "u1")) || strings.Contains(link, channelSecret) {
				t.Fatal("connect link leaked a credential")
			}
			equal(t, len(f.fingerprints()), 0)
		})
	}
}

func TestW4bAuthRequiredStates(t *testing.T) {
	rows := []struct{ state, reason string }{
		{credentials.RefusalNoCredential, mcp.AuthReasonNoToken}, {credentials.RefusalExpired, mcp.AuthReasonExpired},
		{credentials.RefusalRevoked, mcp.AuthReasonNeedsReauth}, {credentials.RefusalNeedsReauth, mcp.AuthReasonNeedsReauth}, {"expired-success", mcp.AuthReasonExpired},
	}
	for _, p := range []struct{ platform, app string }{{"google", "gmail"}, {"microsoft", "microsoft-outlook"}, {"arcade", "customer-gateway"}} {
		for _, row := range rows {
			t.Run(p.platform+"/"+row.state, func(t *testing.T) {
				f := newFixture(t, p.platform, p.app, nil)
				upstream := f.give(t, "org-a", "u1")
				tr := tokenRow{refusal: row.state}
				if row.state == "expired-success" {
					tr = tokenRow{value: upstream, expired: true}
				}
				f.cp.set(key("org-a", "u1"), tr)
				a := f.call(t, userCaller("org-a", "u1"))
				denied(t, a, mcp.CodeAuthRequired)
				equal(t, metadata(t, a)["reason"], row.reason)
				equal(t, metadata(t, a)["server_id"], "service")
				if metadata(t, a)["connect_url"] == nil {
					t.Fatal("missing connect URL")
				}
				if strings.Contains(string(a.Raw), upstream) || strings.Contains(string(a.Raw), channelSecret) {
					t.Fatal("denial leaked a credential")
				}
				equal(t, len(f.fingerprints()), 0)
			})
		}
	}
}

func TestW4bRevokePurge(t *testing.T) {
	for _, p := range []struct{ platform, app string }{{"google", "gmail"}, {"microsoft", "microsoft-outlook"}, {"arcade", "customer-gateway"}} {
		t.Run(p.platform, func(t *testing.T) {
			f := newFixture(t, p.platform, p.app, nil)
			f.give(t, "org-a", "u1")
			u2 := f.give(t, "org-a", "u2")
			connected(t, f.call(t, userCaller("org-a", "u1")))
			connected(t, f.call(t, userCaller("org-a", "u2")))
			purger, ok := f.vendor.(mcp.CredentialPurger)
			if !ok {
				t.Fatal("selected adapter does not delegate CredentialPurger")
			}
			f.cp.revoke(key("org-a", "u1"))
			n, err := purger.PurgeCredentials(context.Background(), "org-a", "u1", "service")
			if err != nil {
				t.Fatal(err)
			}
			equal(t, n, 1)
			if _, ok, err := f.cache.Get(context.Background(), key("org-a", "u1")); err != nil || ok {
				t.Fatal("revoked user's cached token survived purge")
			}
			a := f.call(t, userCaller("org-a", "u1"))
			denied(t, a, mcp.CodeAuthRequired)
			equal(t, metadata(t, a)["reason"], mcp.AuthReasonNeedsReauth)
			equal(t, len(f.fingerprints()), 2)
			connected(t, f.call(t, userCaller("org-a", "u2")))
			equal(t, f.fingerprints()[2], bearerHash(u2))
			// A subsequent token-channel outage must not resurrect the purged token,
			// even though remote cleanup is recorded as failed by the control plane.
			f.cp.set(key("org-a", "u1"), tokenRow{channelError: true})
			denied(t, f.call(t, userCaller("org-a", "u1")), mcp.CodePolicyUnavailable)
			equal(t, len(f.fingerprints()), 3)
			f.cp.mu.Lock()
			outcome := f.cp.cleanup[key("org-a", "u1")]
			f.cp.mu.Unlock()
			equal(t, outcome, "failed_upstream_cleanup")
			f.cp.set(key("org-a", "u1"), tokenRow{refusal: credentials.RefusalRevoked})
			denied(t, f.call(t, userCaller("org-a", "u1")), mcp.CodeAuthRequired)
			connected(t, f.call(t, userCaller("org-a", "u2")))
			equal(t, len(f.fingerprints()), 4)
		})
	}
}

func TestW4bFailureNoFallback(t *testing.T) {
	for _, p := range []struct{ platform, app string }{{"google", "gmail"}, {"microsoft", "microsoft-outlook"}, {"arcade", "customer-gateway"}} {
		for _, failure := range []string{"token-channel", "upstream"} {
			t.Run(p.platform+"/"+failure, func(t *testing.T) {
				logs := captureLogs(t)
				f := newFixture(t, p.platform, p.app, nil)
				own := f.give(t, "org-a", "u1")
				f.give(t, "org-a", "u2")
				f.give(t, "org-b", "u1")
				f.cp.set(credentials.SharedKey("org-a", "service"), tokenRow{value: "synthetic-shared-not-a-fallback"})
				if failure == "token-channel" {
					f.cp.set(key("org-a", "u1"), tokenRow{channelError: true})
				} else {
					f.mock.State().SetBehavior(mcpmock.Behavior{FailNext: 1})
				}
				a := f.call(t, userCaller("org-a", "u1"))
				if !a.IsError {
					t.Fatal("failed call succeeded via fallback")
				}
				equal(t, f.cp.fetched(), []credentials.Key{key("org-a", "u1")})
				if failure == "token-channel" {
					denied(t, a, mcp.CodePolicyUnavailable)
					equal(t, len(f.fingerprints()), 0)
				} else {
					equal(t, f.fingerprints(), []string{bearerHash(own)})
				}
				f.assertBYOKBudgets(t, failure == "upstream")
				f.seen.mu.Lock()
				defer f.seen.mu.Unlock()
				if len(f.seen.records) != 1 {
					t.Fatal("missing failed-call record")
				}
				equal(t, f.seen.records[0].BillingMode, "byok")
				equal(t, f.seen.records[0].BilledCostMicros, int64(0))
				equal(t, f.seen.records[0].FallbackFrom, "")
				for _, value := range []string{own, userCaller("org-a", "u1"), channelSecret} {
					if strings.Contains(logs.text(), value) || strings.Contains(string(a.Raw), value) {
						t.Fatal("failure leaked a credential in a log or denial")
					}
				}
			})
		}
	}
}

func TestW4bBYOKZeroCharge(t *testing.T) {
	for _, p := range []struct{ platform, app string }{{"google", "gmail"}, {"microsoft", "microsoft-outlook"}, {"arcade", "customer-gateway"}} {
		t.Run(p.platform, func(t *testing.T) {
			f := newFixture(t, p.platform, p.app, nil)
			f.give(t, "org-a", "u1")
			connected(t, f.call(t, userCaller("org-a", "u1")))
			f.assertBYOKBudgets(t, true)
			f.seen.mu.Lock()
			defer f.seen.mu.Unlock()
			equal(t, len(f.seen.records), 1)
			equal(t, len(f.seen.billing), 1)
			r, b := f.seen.records[0], f.seen.billing[0]
			equal(t, r.BillingMode, "byok")
			equal(t, r.BilledCostMicros, int64(0))
			equal(t, r.CredentialSource, "user")
			equal(t, b.ReserveMicros, int64(0))
			equal(t, b.BilledMicros, int64(0))
			equal(t, b.Settled, false)
		})
	}
}
