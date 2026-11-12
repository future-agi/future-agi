package server

// The org's model_map is read for customer keys. orgProviderFor
// consults the map first (a mapped name goes to the org provider it names;
// an alias of a provider that lists one model is sent as that model), then
// matches provider model lists in sorted provider-id order. Ported from
// TestConsoleAModelMapCustomerKey (at the base: alpha 17 / beta 3 for a model
// mapped to beta; alias-x 403 with 0 upstream calls).

import (
	"net/http"
	"testing"
)

func r7ModelMapGateway(t *testing.T, orgJSON string) (*Server, *r7Upstream, *r7Upstream, *r7Upstream) {
	t.Helper()
	ua := r7StartUpstream(t, nil)
	ub := r7StartUpstream(t, nil)
	ug := r7StartUpstream(t, nil) // the operator's only
	srv := r7Gateway(t, map[string]r7Op{
		"alpha": {ua, []string{"shared-model"}},
		"beta":  {ub, []string{"shared-model"}},
		"gamma": {ug, []string{"shared-model"}},
	}, nil)
	r7Push(t, srv, r7OrgA, orgJSON)
	return srv, ua, ub, ug
}

const r7MapProviders = `"providers":{` +
	`"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["shared-model"],"enabled":true},` +
	`"beta":{"api_key":"org-beta-key","api_format":"openai","models":["shared-model"],"enabled":true}}`

// A model mapped to beta always goes to beta.
func TestR7OrgModelMapPinsTheMappedOrgProvider(t *testing.T) {
	srv, ua, ub, _ := r7ModelMapGateway(t, `{`+r7MapProviders+`,"model_map":{"shared-model":"beta"}}`)
	for i := 0; i < 20; i++ {
		if code, body, _ := r7Chat(t, srv, r7CustA, "shared-model", i%5 == 0); code != http.StatusOK {
			t.Fatalf("request %d -> %d %s", i, code, body)
		}
	}
	if a, b := ua.count(), ub.count(); a != 0 || b != 20 {
		t.Fatalf("model_map {shared-model: beta}, 20 requests: alpha %d, beta %d; want 0 / 20", a, b)
	}
	r7AssertOrgCredentialOnly(t, ua, ub)
}

// An alias mapped to an org provider that lists one model is sent to it as
// that model, with the org's key.
func TestR7OrgModelMapAliasRoutesToTheOrgProvidersModel(t *testing.T) {
	srv, ua, ub, ug := r7ModelMapGateway(t, `{`+r7MapProviders+`,"model_map":{"alias-x":"beta"}}`)
	code, body, _ := r7Chat(t, srv, r7CustA, "alias-x", false)
	if code != http.StatusOK {
		t.Fatalf("customer key, model_map {alias-x: beta} -> %d %s, want 200", code, body)
	}
	calls := ub.snapshot()
	if len(calls) != 1 || calls[0].Model != "shared-model" || calls[0].Auth != "Bearer org-beta-key" {
		t.Fatalf("beta calls %v, want one call for shared-model with the org's key", calls)
	}
	if ua.count() != 0 || ug.count() != 0 {
		t.Fatalf("alpha %v gamma %v, want no calls", ua.snapshot(), ug.snapshot())
	}
}

// An alias mapped to an org provider that lists several models is sent as
// named (the provider is chosen, the name is not guessed).
func TestR7OrgModelMapAliasOfAMultiModelProviderKeepsItsName(t *testing.T) {
	srv, _, ub, _ := r7ModelMapGateway(t, `{"providers":{"beta":{"api_key":"org-beta-key","api_format":"openai","models":["shared-model","other-model"],"enabled":true}},"model_map":{"alias-z":"beta"}}`)
	if code, body, _ := r7Chat(t, srv, r7CustA, "alias-z", false); code != http.StatusOK {
		t.Fatalf("-> %d %s, want 200", code, body)
	}
	if calls := ub.snapshot(); len(calls) != 1 || calls[0].Model != "alias-z" {
		t.Fatalf("beta calls %v, want alias-z as named", calls)
	}
}

// Without a map entry, the org providers that list a model are matched in
// sorted provider-id order: always alpha, never map iteration order.
func TestR7OrgModelMapUnmappedUsesSortedProviderOrder(t *testing.T) {
	srv, ua, ub, _ := r7ModelMapGateway(t, `{`+r7MapProviders+`}`)
	for i := 0; i < 20; i++ {
		if code, body, _ := r7Chat(t, srv, r7CustA, "shared-model", false); code != http.StatusOK {
			t.Fatalf("request %d -> %d %s", i, code, body)
		}
	}
	if a, b := ua.count(), ub.count(); a != 20 || b != 0 {
		t.Fatalf("no map, no strategy, 20 requests: alpha %d, beta %d; want 20 / 0 (sorted order)", a, b)
	}
}

// A map entry naming a provider the org does not have (the operator's gamma)
// never sends a customer key to it.
func TestR7OrgModelMapNeverAnOperatorProvider(t *testing.T) {
	srv, ua, ub, ug := r7ModelMapGateway(t, `{`+r7MapProviders+`,"model_map":{"alias-y":"gamma","shared-model":"gamma"}}`)
	if code, body, _ := r7Chat(t, srv, r7CustA, "alias-y", false); code != http.StatusForbidden {
		t.Fatalf("alias-y -> gamma (not the org's) -> %d %s, want 403", code, body)
	}
	if code, body, _ := r7Chat(t, srv, r7CustA, "shared-model", false); code != http.StatusOK {
		t.Fatalf("shared-model mapped to gamma -> %d %s, want 200 from the org's own provider", code, body)
	}
	if ug.count() != 0 {
		t.Fatalf("gamma (operator only) was called by a customer key: %v", ug.snapshot())
	}
	r7AssertOrgCredentialOnly(t, ua, ub)
}

// The model map applies to every key of the org: an internal key's alias goes
// to the org's own instance of the provider it names, with the org's key and
// as that provider's one model (at the base: the operator's beta, its key,
// alias-x as named).
func TestR7OrgModelMapAppliesToTheInternalKeyToo(t *testing.T) {
	srv, ua, ub, ug := r7ModelMapGateway(t, `{`+r7MapProviders+`,"model_map":{"alias-x":"beta"}}`)
	if code, body, _ := r7Chat(t, srv, r7IntlA, "alias-x", false); code != http.StatusOK {
		t.Fatalf("internal key, model_map {alias-x: beta} -> %d %s, want 200", code, body)
	}
	if calls := ub.snapshot(); len(calls) != 1 || calls[0].Model != "shared-model" || calls[0].Auth != "Bearer org-beta-key" {
		t.Fatalf("beta calls %v, want one call for shared-model with the org's key", calls)
	}
	if ua.count() != 0 || ug.count() != 0 {
		t.Fatalf("alpha %v gamma %v, want no calls", ua.snapshot(), ug.snapshot())
	}
}
