package manifest

import (
	"errors"
	"fmt"
)

// maxServerTools is the most tools one server lists: EE keeps the first 128
// (EE:attachments.ts:19, context.ts:112) and its broker refuses more.
const maxServerTools = 128

// maxNamespacedTool is the gateway's cap on <server>__<tool>
// (MCP-PLAN.md:1177); a catalog app id is the server id.
const maxNamespacedTool = 64

// checkCatalog runs L0's rules that span records, then EE's and OSS's.
func checkCatalog(cat *Catalog) []error {
	var errs []error
	fail := func(path, format string, args ...any) {
		errs = append(errs, fmt.Errorf("%s: %s", path, fmt.Sprintf(format, args...)))
	}
	for _, pl := range cat.Platforms {
		if cat.App(pl.ID) != nil {
			fail(pl.path, "platform id %s is also an app id; apps and platforms share one id space", pl.ID)
		}
	}
	groups := map[string]*clientGroup{}
	for _, a := range cat.Apps {
		if len(a.Providers) == 0 {
			fail(a.path, "has no providers")
		} else if a.Provider(a.DefaultProvider) == nil {
			fail(a.path, "defaultProvider %s is not one of its providers", a.DefaultProvider)
		}
		if r := a.Lifecycle.ReplacedBy; r != "" && cat.App(r) == nil {
			fail(a.path, "lifecycle.replacedBy %s is not an app", r)
		}
		for _, p := range a.Providers {
			errs = append(errs, checkProviderInCatalog(cat, a, p, groups)...)
		}
	}
	errs = append(errs, checkEE(cat)...)
	errs = append(errs, checkOSS(cat)...)
	return errs
}

// clientGroup is one OAuth client that oauth2 providers share. CN3 resolves
// a provider's client, the org's or the platform's, by its clientGroup, else
// by its app id (connector_oauth_clients.py resolve; CONNECTORS-PLAN §2.9), so
// every app id is also a group. The client's secret goes to the token
// endpoint and, on disconnect, to the revocation endpoint, both properties
// of the authorization server the group shares, so every member names the
// same ones, and declares a revocation endpoint only if all do: otherwise
// one member could add a revocation endpoint the rest never name.
type clientGroup struct {
	first *Provider // the first member, which every other one is compared with
}

// oauthClientGroup is an oauth2 provider's client group: its clientGroup,
// else its app id.
func (p *Provider) oauthClientGroup() string {
	if g := p.Auth.OAuth2.ClientGroup; g != "" {
		return g
	}
	return p.App
}

// revocationEndpoint names where and how a revocation request sends the
// client's credentials, or "none".
func revocationEndpoint(r *Revocation) string {
	if r == nil {
		return "none"
	}
	return r.URL + " (clientAuth " + r.ClientAuth + ")"
}

// checkClientGroup pins the token and revocation endpoints across p's
// client group.
func checkClientGroup(p *Provider, groups map[string]*clientGroup) []error {
	name := p.oauthClientGroup()
	g := groups[name]
	if g == nil {
		groups[name] = &clientGroup{first: p}
		return nil
	}
	var errs []error
	const why = "client group %s is one OAuth client (a provider without clientGroup is in its app's group)"
	o, fo := p.Auth.OAuth2, g.first.Auth.OAuth2
	if o.TokenURL != fo.TokenURL {
		errs = append(errs, fmt.Errorf("%s: "+why+": tokenUrl %s differs from %s's %s",
			p.path, name, o.TokenURL, g.first.path, fo.TokenURL))
	}
	if r, fr := revocationEndpoint(o.Revocation), revocationEndpoint(fo.Revocation); r != fr {
		errs = append(errs, fmt.Errorf("%s: "+why+": revocation %s differs from %s's %s",
			p.path, name, r, g.first.path, fr))
	}
	return errs
}

func checkProviderInCatalog(cat *Catalog, a *App, p *Provider, groups map[string]*clientGroup) []error {
	var errs []error
	fail := func(path, format string, args ...any) {
		errs = append(errs, fmt.Errorf("%s: %s", path, fmt.Sprintf(format, args...)))
	}
	if r := p.Lifecycle.ReplacedBy; r != "" && a.Provider(r) == nil {
		fail(p.path, "lifecycle.replacedBy %s is not a provider of %s", r, a.ID)
	}
	if pa := p.Auth.Partner; pa != nil && cat.Platform(pa.Platform) == nil {
		fail(p.path, "auth.partner.platform %s is not a platform", pa.Platform)
	}
	if p.Auth.OAuth2 != nil {
		errs = append(errs, checkClientGroup(p, groups)...)
	}
	errs = append(errs, prefixed(p.path, checkPublicClient(a, p))...)
	if p.Auth.ClientApproval.Pending() && p.Quality.Tier != "listed" {
		fail(p.path, "needs vendor approval (%s): tier must stay listed until clientApproval.approvedAt is set", p.Auth.ClientApproval.Program)
	}

	tools := p.AllTools()
	if len(tools) > maxServerTools {
		fail(p.path, "lists %d tools; a server lists at most %d", len(tools), maxServerTools)
	}
	if p.Tools.Source == ToolsOperations && len(tools) == 0 {
		fail(p.path, "an operations source lists its tools in tools.items or tools.generated")
	}
	seen := map[string]bool{}
	for i, t := range tools {
		path := p.toolFile(i)
		if seen[t.Name] {
			fail(path, "tool %s is listed twice", t.Name)
		}
		seen[t.Name] = true
		if n := len(a.ID) + 2 + len(t.Name); n > maxNamespacedTool {
			fail(path, "tool %s__%s is %d characters; <server>__<tool> must fit in %d", a.ID, t.Name, n, maxNamespacedTool)
		}
	}
	if v := p.Auth.Verify; v != nil && v.Tool != "" && len(tools) > 0 && !seen[v.Tool] {
		fail(p.path, "auth.verify.tool %s is not one of its tools", v.Tool)
	}
	return errs
}

// toolFile names the file the i-th tool of AllTools comes from.
func (p *Provider) toolFile(i int) string {
	if i < len(p.Tools.Items) {
		return p.path
	}
	return p.generatedPath
}

// CheckPublic applies the open-data rule to a catalog that will be
// published (omega-coordination.md, the 05:40 agreement, item 1c): it holds
// no community package. L0 already refuses secrets, client ids and prices
// in every catalog; an operator's own catalog may carry community entries.
func CheckPublic(c *Catalog) error {
	var errs []error
	for _, a := range c.Apps {
		for _, p := range a.Providers {
			if p.Publisher == "community" {
				errs = append(errs, fmt.Errorf("%s: publisher community: the public catalog holds no community packages", p.path))
			}
		}
	}
	return errors.Join(errs...)
}
