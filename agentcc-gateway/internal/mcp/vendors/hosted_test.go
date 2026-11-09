package vendors_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credentials"
)

// K2 hosted runtime wiring (G14b): a call on a server added from FutureAGI's
// own Gmail or Outlook provider runs through the real agentcc-connectors
// runtime with the calling person's own token, and that token reaches the
// vendor API only from the operator's runtime. Each test is one property.

func pilotKey(org, user, server string) credentials.Key {
	return credentials.Key{OrgID: org, UserID: user, ServerID: server}
}

func gmailAt(url string) func(f *hostedFixture) map[string]any {
	return func(f *hostedFixture) map[string]any {
		if url == "" {
			url = f.runtime + "/gmail/mcp"
		}
		return map[string]any{"gmail": pilotEntry(url, "gmail", "1.0.0")}
	}
}

// TestK2HostedRuntimeExecution: Gmail and Outlook through the runtime, each
// with the caller's token as the only bearer the vendor API receives.
func TestK2HostedRuntimeExecution(t *testing.T) {
	for _, c := range []struct{ name, server, app, tool string }{
		{"gmail", "gmail", "gmail", "gmail__get_profile"},
		{"outlook", "outlook", "microsoft-outlook", "outlook__list_mail_folders"},
	} {
		t.Run(c.name, func(t *testing.T) {
			f := newHostedFixture(t, func(f *hostedFixture) map[string]any {
				return map[string]any{c.server: pilotEntry(f.runtime+"/"+c.app+"/mcp", c.app, "1.0.0")}
			})
			token := "k2-synthetic-" + c.name + "-access-u1"
			f.cp.set(pilotKey("org-a", "u1", c.server), tokenRow{value: token})

			connected(t, f.callTool(t, userCaller("org-a", "u1"), c.tool))

			equal(t, f.vendor.seen(), []string{bearerHash(token)})
			fetched := f.cp.fetched()
			if len(fetched) == 0 {
				t.Fatal("no token was fetched for the caller")
			}
			for _, k := range fetched {
				equal(t, k, pilotKey("org-a", "u1", c.server))
			}
		})
	}
}

// TestK2HostedEachPersonOwnToken: interleaved people in two orgs, the same
// user id in both, each reach the vendor with their own token only.
func TestK2HostedEachPersonOwnToken(t *testing.T) {
	f := newHostedFixture(t, gmailAt(""))
	people := [][2]string{{"org-a", "u1"}, {"org-a", "u2"}, {"org-b", "u1"}, {"org-a", "u1"}}
	var want []string
	for _, p := range people {
		token := "k2-synthetic-gmail-access-" + p[0] + "-" + p[1]
		f.cp.set(pilotKey(p[0], p[1], "gmail"), tokenRow{value: token})
		want = append(want, bearerHash(token))
	}
	for _, p := range people {
		connected(t, f.callTool(t, userCaller(p[0], p[1]), "gmail__get_profile"))
	}
	equal(t, f.vendor.seen(), want)
}

// TestK2HostedRefusesAPilotServerOffTheRuntime: a server that claims a K2
// pilot provider but is not at the operator's runtime never gets a person's
// token: refused before any token fetch or upstream call.
func TestK2HostedRefusesAPilotServerOffTheRuntime(t *testing.T) {
	f := newHostedFixture(t, func(f *hostedFixture) map[string]any {
		return map[string]any{"elsewhere": pilotEntry(f.plainURL, "gmail", "1.0.0")}
	})
	f.cp.set(pilotKey("org-a", "u1", "elsewhere"), tokenRow{value: "k2-synthetic-elsewhere-access"})

	denied(t, f.callTool(t, userCaller("org-a", "u1"), "elsewhere__echo"), mcp.CodePolicyUnavailable)

	equal(t, len(f.cp.fetched()), 0)
	equal(t, len(f.plain.State().ReceivedLast(0, "tools/call")), 0)
}

// TestK2HostedRefusesAPilotVersionTheCatalogDoesNotHold: a pilot reference
// at a version the operator's catalog does not hold is refused, never sent
// down the plain per-user path.
func TestK2HostedRefusesAPilotVersionTheCatalogDoesNotHold(t *testing.T) {
	f := newHostedFixture(t, func(f *hostedFixture) map[string]any {
		return map[string]any{"gmail": pilotEntry(f.runtime+"/gmail/mcp", "gmail", "9.9.9")}
	})
	f.cp.set(pilotKey("org-a", "u1", "gmail"), tokenRow{value: "k2-synthetic-gmail-access-u1"})

	denied(t, f.callTool(t, userCaller("org-a", "u1"), "gmail__get_profile"), mcp.CodePolicyUnavailable)

	equal(t, len(f.cp.fetched()), 0)
	equal(t, len(f.vendor.seen()), 0)
}

// TestK2HostedWithoutRegistrationAsksToConnect: an org without its own app
// registration has no connected account (the control plane refuses every
// sign-in for it), so a call answers W4's typed auth_required with the
// sealed connect link, and reaches no vendor.
func TestK2HostedWithoutRegistrationAsksToConnect(t *testing.T) {
	f := newHostedFixture(t, gmailAt(""))

	a := f.callTool(t, userCaller("org-a", "u1"), "gmail__get_profile")

	denied(t, a, mcp.CodeAuthRequired)
	meta := metadata(t, a)
	equal(t, meta["reason"], mcp.AuthReasonNoToken)
	equal(t, meta["server_id"], "gmail")
	if link, _ := meta["connect_url"].(string); link == "" {
		t.Fatal("auth_required carries no connect link")
	}
	equal(t, len(f.vendor.seen()), 0)
}

// TestK2HostedNeverActsForASharedKey: a shared gateway key, even its
// creator's, acquires no person's token on a K2 server.
func TestK2HostedNeverActsForASharedKey(t *testing.T) {
	f := newHostedFixture(t, gmailAt(""))
	f.cp.set(pilotKey("org-a", "u1", "gmail"), tokenRow{value: "k2-synthetic-gmail-access-u1"})

	a := f.callTool(t, sharedCaller, "gmail__get_profile")

	if !a.IsError || a.Denial.Code == "" {
		t.Fatal("a shared key's call on a K2 server was not refused")
	}
	equal(t, len(f.cp.fetched()), 0)
	equal(t, len(f.vendor.seen()), 0)
}

// TestK2HostedLeavesOtherServersUnchanged: a per_user server added by URL
// keeps M25's own path through the wiring.
func TestK2HostedLeavesOtherServersUnchanged(t *testing.T) {
	f := newHostedFixture(t, func(f *hostedFixture) map[string]any {
		return map[string]any{"plain": map[string]any{"url": f.plainURL, "credential": map[string]any{"mode": "per_user"}}}
	})
	token := f.plainToken(t, "org-a/u1")
	f.cp.set(pilotKey("org-a", "u1", "plain"), tokenRow{value: token})

	connected(t, f.callTool(t, userCaller("org-a", "u1"), "plain__echo"))

	var got []string
	for _, r := range f.plain.State().ReceivedLast(0, "tools/call") {
		got = append(got, r.Auth.SHA256)
	}
	equal(t, got, []string{bearerHash(token)})
}

// TestK2HostedRevokePurges: a purge through the wiring drops only that
// person's cached token; once revoked, their next call asks to connect again
// and reaches no vendor, while another person's keeps working.
func TestK2HostedRevokePurges(t *testing.T) {
	f := newHostedFixture(t, gmailAt(""))
	f.cp.set(pilotKey("org-a", "u1", "gmail"), tokenRow{value: "k2-synthetic-gmail-access-u1"})
	f.cp.set(pilotKey("org-a", "u2", "gmail"), tokenRow{value: "k2-synthetic-gmail-access-u2"})
	connected(t, f.callTool(t, userCaller("org-a", "u1"), "gmail__get_profile"))
	connected(t, f.callTool(t, userCaller("org-a", "u2"), "gmail__get_profile"))

	f.cp.revoke(pilotKey("org-a", "u1", "gmail"))
	purger, ok := f.step.(mcp.CredentialPurger)
	if !ok {
		t.Fatal("the credential step cannot purge")
	}
	if _, err := purger.PurgeCredentials(context.Background(), "org-a", "u1", "gmail"); err != nil {
		t.Fatal(err)
	}
	before := len(f.vendor.seen())
	a := f.callTool(t, userCaller("org-a", "u1"), "gmail__get_profile")
	denied(t, a, mcp.CodeAuthRequired)
	equal(t, metadata(t, a)["reason"], mcp.AuthReasonNeedsReauth)
	equal(t, len(f.vendor.seen()), before)
	connected(t, f.callTool(t, userCaller("org-a", "u2"), "gmail__get_profile"))
}

// plainToken mints a token the plain mock accepts, for its subject.
func (f *hostedFixture) plainToken(t *testing.T, subject string) string {
	t.Helper()
	raw, _ := json.Marshal(map[string]any{"sub": subject, "ttl_s": 3600})
	resp, err := http.Post(f.plainURL[:len(f.plainURL)-len("/mcp")]+"/__control/oauth/token", "application/json", bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var reply struct {
		Access string `json:"access_token"`
	}
	if resp.StatusCode != http.StatusOK || json.NewDecoder(resp.Body).Decode(&reply) != nil || reply.Access == "" {
		t.Fatal("synthetic OAuth mint failed")
	}
	return reply.Access
}
