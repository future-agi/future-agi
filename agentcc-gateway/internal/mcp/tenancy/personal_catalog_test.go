package tenancy

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

type personalCatalogClient struct {
	*fakeClient
	account string
	lists   [3]atomic.Int32
}

func (c *personalCatalogClient) ListTools(ctx context.Context) ([]json.RawMessage, error) {
	c.lists[0].Add(1)
	return c.fakeClient.ListTools(ctx)
}
func (c *personalCatalogClient) ListResources(context.Context) ([]json.RawMessage, error) {
	c.lists[1].Add(1)
	return []json.RawMessage{json.RawMessage(fmt.Sprintf(`{"uri":%q,"name":%q,"description":%q}`, "file:///"+c.account, c.account, c.account+" resource"))}, nil
}
func (c *personalCatalogClient) ListPrompts(context.Context) ([]json.RawMessage, error) {
	c.lists[2].Add(1)
	return []json.RawMessage{json.RawMessage(fmt.Sprintf(`{"name":%q,"description":%q}`, c.account, c.account+" prompt"))}, nil
}

func TestPersonalCatalogStaysWithCredentialLifetime(t *testing.T) {
	r := newRig(t, Options{})
	r.push("org-a", 1, `{"enabled":true,"servers":{"mine":{"url":"`+urlA+`","tools_cache_ttl_seconds":300,"credential":{"mode":"per_user"}}}}`)
	built := map[string]*personalCatalogClient{}
	d := *r.deps.Load()
	d.ClientFactory = func(cfg mcp.ClientConfig) (mcp.UpstreamClient, error) {
		c := &personalCatalogClient{fakeClient: &fakeClient{up: &fakeUpstream{tools: []string{"common"}}}, account: cfg.Auth.Token}
		built[c.account] = c
		return c, nil
	}
	r.deps.Store(&d)
	_, pool := r.resolve("org-a")
	life, revoke := context.WithCancel(context.Background())
	defer revoke()
	auth := func(account string) mcp.AuthOverride {
		return mcp.AuthOverride{Header: http.Header{"Authorization": {"Bearer " + account}}, Lifetime: life}
	}
	a, err := pool.Client("mine", "member-a", auth("a"))
	if err != nil {
		t.Fatal(err)
	}
	b, err := pool.Client("mine", "member-b", auth("b"))
	if err != nil {
		t.Fatal(err)
	}
	check := func(c mcp.UpstreamClient, account string) {
		t.Helper()
		for range 2 {
			if _, err := c.ListTools(context.Background()); err != nil {
				t.Fatal(err)
			}
			raw, err := c.ListResources(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			resources := decodeAll[mcp.Resource](raw)
			if len(resources) != 1 || resources[0].URI != "file:///"+account {
				t.Fatalf("wrong personal resources for %s: %+v", account, resources)
			}
			// A response consumer must not mutate the next cached response.
			raw[0][0] = '!'
			raw, err = c.ListPrompts(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			prompts := decodeAll[mcp.Prompt](raw)
			if len(prompts) != 1 || prompts[0].Name != account {
				t.Fatalf("wrong personal prompts for %s: %+v", account, prompts)
			}
		}
		for i := range built[account].lists {
			if n := built[account].lists[i].Load(); n != 1 {
				t.Errorf("%s discovery method %d called %d times, want one cached listing", account, i, n)
			}
		}
	}
	check(a, "a")
	check(b, "b")
	// Organization-wide readers (including admin and virtual-server sources)
	// must never receive a catalog learned through a personal credential.
	ps, _ := pool.Server("mine")
	members := mcp.PoolMembers(pool, "__")
	if len(ps.Tools)+len(ps.Resources)+len(ps.Prompts) != 0 || len(members.MemberResources("mine"))+len(members.MemberPrompts("mine")) != 0 {
		t.Error("personal discovery was published to the organization-wide catalog")
	}
	_, err = pool.Client("mine", "unconnected", mcp.AuthOverride{})
	requireCredentialRefusal(t, err)
	fresh, err := pool.Client("mine", "member-b", auth("b_new"))
	if err != nil {
		t.Fatal(err)
	}
	check(fresh, "b_new")
	_, err = b.ListResources(context.Background())
	requireCredentialRefusal(t, err)
	check(a, "a")
	// A grant version change also drops discovery, even if its bearer and
	// lifetime context did not change.
	previous := built["b_new"]
	versioned := auth("b_new")
	versioned.Version = 2
	latest, err := pool.Client("mine", "member-b", versioned)
	if err != nil {
		t.Fatal(err)
	}
	if built["b_new"] == previous {
		t.Fatal("a new grant version retained the old personal client")
	}
	check(latest, "b_new")
	_, err = fresh.ListPrompts(context.Background())
	requireCredentialRefusal(t, err)
	revoke()
	_, err = a.ListResources(context.Background())
	requireCredentialRefusal(t, err)
	_, err = latest.ListPrompts(context.Background())
	requireCredentialRefusal(t, err)
}
