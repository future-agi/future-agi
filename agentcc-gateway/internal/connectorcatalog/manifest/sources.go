package manifest

import (
	"fmt"
	"strings"
)

// The owner's rule: every fact has a URL fetched on a date. §2.5 names the
// facts L0 checks: each URL, scope list, rate limit and lead time. A rate
// limit, a review lead time, a vendor status label and a client approval
// carry their source inline, so the schema requires it there. Links that
// are themselves sources (docsUrl, icons, helpUrl, termsUrl, upstream.spec)
// need none, and neither does a rest provider's URL, which is our own.

func appFacts(a *App) []string {
	facts := []string{"vendor.domains"}
	if len(a.Vendor.GitHubOrgs) > 0 {
		facts = append(facts, "vendor.githubOrgs")
	}
	return facts
}

func providerFacts(p *Provider) []string {
	var facts []string
	if p.Transport.URL != "" && p.Kind != KindREST {
		facts = append(facts, "transport.url")
	}
	if o := p.Auth.OAuth2; o != nil {
		facts = append(facts, "auth.oauth2.authorizeUrl", "auth.oauth2.tokenUrl")
		if len(o.Scopes) > 0 {
			facts = append(facts, "auth.oauth2.scopes")
		}
		if o.Revocation != nil {
			facts = append(facts, "auth.oauth2.revocation.url")
		}
	}
	if v := p.Auth.Verify; v != nil && v.HTTP != nil {
		facts = append(facts, "auth.verify.http.url")
	}
	return facts
}

// checkSources requires a source for every required fact, and refuses a
// source that names a field the record does not have, so a typo cannot
// leave a fact unsourced.
func checkSources(raw map[string]any, sources []Source, required []string) []error {
	var errs []error
	cited := map[string]bool{}
	for i, s := range sources {
		if !hasPath(raw, s.Fact) {
			errs = append(errs, fmt.Errorf("/sources/%d: sources names %s, which this record does not have", i, s.Fact))
		}
		cited[s.Fact] = true
	}
	for _, fact := range required {
		if !cited[fact] {
			errs = append(errs, fmt.Errorf("%s needs a source: add {fact: %s, url, fetchedAt} to sources", fact, fact))
		}
	}
	return errs
}

// hasPath reports whether the dotted path names a field of doc.
func hasPath(doc map[string]any, path string) bool {
	var cur any = doc
	for _, key := range strings.Split(path, ".") {
		m, ok := cur.(map[string]any)
		if !ok {
			return false
		}
		if cur, ok = m[key]; !ok {
			return false
		}
	}
	return true
}
