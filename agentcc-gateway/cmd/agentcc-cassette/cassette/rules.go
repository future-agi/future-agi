package cassette

import "fmt"

// Rules are the credential names a recording adds to the built-in ones
// (names.go): query or form parameters (--secret-param) and JSON member
// names (--secret-key). A cassette carries them, so replay blinds exactly
// the locations the scrubber scrubbed. Names compare as normName does:
// opaqueHandle, opaque_handle and OPAQUE-HANDLE are one name.
type Rules struct {
	SecretParams []string `json:"secret_params,omitempty"`
	SecretKeys   []string `json:"secret_keys,omitempty"`
}

// Validate refuses a name with no letter or digit: it could match nothing.
func (r *Rules) Validate() error {
	if r == nil {
		return nil
	}
	for _, list := range []struct {
		what  string
		names []string
	}{{"secret parameter", r.SecretParams}, {"secret member name", r.SecretKeys}} {
		for _, n := range list.names {
			if normName(n) == "" {
				return fmt.Errorf("%s %q has no letter or digit", list.what, n)
			}
		}
	}
	return nil
}

// mergeRules is the union of rs, each name in the spelling first met; nil
// when no name is left.
func mergeRules(rs ...*Rules) *Rules {
	out := &Rules{}
	seenParam, seenKey := map[string]bool{}, map[string]bool{}
	add := func(list *[]string, seen map[string]bool, names []string) {
		for _, n := range names {
			if k := normName(n); k != "" && !seen[k] {
				seen[k] = true
				*list = append(*list, n)
			}
		}
	}
	for _, r := range rs {
		if r != nil {
			add(&out.SecretParams, seenParam, r.SecretParams)
			add(&out.SecretKeys, seenKey, r.SecretKeys)
		}
	}
	if len(out.SecretParams) == 0 && len(out.SecretKeys) == 0 {
		return nil
	}
	return out
}

// nameRules decides which names hold credentials: the built-in rules plus a
// recording's additions. A nil *nameRules is the built-in rules alone.
type nameRules struct {
	params, keys map[string]bool // added names, normalized
}

func compileRules(rs ...*Rules) *nameRules {
	m := mergeRules(rs...)
	if m == nil {
		return nil
	}
	n := &nameRules{params: map[string]bool{}, keys: map[string]bool{}}
	for _, p := range m.SecretParams {
		n.params[normName(p)] = true
	}
	for _, k := range m.SecretKeys {
		n.keys[normName(k)] = true
	}
	return n
}

// secretKey reports a JSON member name whose value is a credential.
func (n *nameRules) secretKey(name string) bool {
	k := normName(name)
	if n != nil && n.keys[k] {
		return true
	}
	return builtinSecret(k)
}

// secretMember is secretKey, plus code and state in an object that also
// holds an OAuth companion member (grant_type, redirect_uri,
// code_verifier): a JSON token request.
func (n *nameRules) secretMember(name string, oauthObject bool) bool {
	return n.secretKey(name) || oauthObject && oauthParams[normName(name)]
}

// secretParam reports a query or form parameter whose value is a
// credential; oauth says the parameters include an OAuth companion (or sit
// in a Location URL or a fragment), which makes code and state secret too.
func (n *nameRules) secretParam(name string, oauth bool) bool {
	k := normName(name)
	if n != nil && n.params[k] {
		return true
	}
	return builtinSecretParam(k, oauth)
}
