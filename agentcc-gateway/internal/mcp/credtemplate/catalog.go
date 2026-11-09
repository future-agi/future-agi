package credtemplate

import (
	"regexp"
	"strings"
)

// Declared is one credential part a catalog provider declares
// (connector-manifest/v1 auth.credentialParts, AM-C12 a): a secret part,
// which the push carries, or a non-secret part, whose constant Value the
// catalog carries and the push never does (AM-C20 item 1).
type Declared struct {
	In     In
	Name   string
	Secret bool
	Value  string // non-secret parts only
	Prefix string // header parts only
}

// Catalog is where the gateway reads the parts a catalog provider declares.
// catalogparts.FromManifest adapts a connector-manifest/v1 catalog.
type Catalog interface {
	// CredentialParts returns the provider's version and declared parts;
	// ok is false when the catalog has no such provider.
	CredentialParts(app, provider string) (version string, parts []Declared, ok bool)
}

// Ref is a server's catalog_ref, <app-id>/<provider-id>@<version> (AM-C9).
type Ref struct {
	App, Provider, Version string
}

// refPattern is the contract's catalog_ref pattern (connector-manifest/v1
// appId, providerId and semver).
var refPattern = regexp.MustCompile(`^([a-z][a-z0-9-]{1,39})/([a-z][a-z0-9-]{1,23})@((0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*))$`)

// ParseRef parses a catalog_ref.
func ParseRef(s string) (Ref, error) {
	m := refPattern.FindStringSubmatch(s)
	if m == nil {
		return Ref{}, refuse("catalog_ref", "is not <app-id>/<provider-id>@<major>.<minor>.<patch>")
	}
	return Ref{App: m[1], Provider: m[2], Version: m[3]}, nil
}

// ForCatalogSecret compiles the pushed secret of a server added from the
// catalog (its catalog_ref; "" is ForSecret) with the parts the provider
// declares, and refuses, never sends in part or elsewhere:
//   - no catalog loaded, a catalog_ref that does not parse, a provider the
//     catalog does not have, or a catalog_ref of another major version than
//     the provider loaded;
//   - a pushed part the provider does not declare as a secret part (a
//     constant part is never pushed), or one whose prefix differs from the
//     declared one (the push's type decides the prefix: "Bearer " in
//     Authorization);
//   - a declared secret part the push does not carry, which would send the
//     credential in part;
//   - a declared non-secret part with no value, which nothing supplies.
//
// Each declared constant part is added from the catalog, by the same rules
// as a pushed one, in declared order. A provider that declares no parts
// leaves the pushed parts as they are.
func ForCatalogSecret(rawURL string, sec Secret, cat Catalog, catalogRef string) (*Template, error) {
	if catalogRef == "" {
		return ForSecret(rawURL, sec)
	}
	pushed, err := Parts(sec)
	if err != nil {
		return nil, err
	}
	pushedNames := pushedFields(sec, len(pushed))
	if cat == nil {
		return nil, refuse("catalog_ref", "names a catalog provider, but no connector catalog is loaded, so the parts it declares cannot be applied")
	}
	ref, err := ParseRef(catalogRef)
	if err != nil {
		return nil, err
	}
	version, declared, ok := cat.CredentialParts(ref.App, ref.Provider)
	if !ok {
		return nil, refuse("catalog_ref", "names %s/%s, which the loaded connector catalog does not have", ref.App, ref.Provider)
	}
	if major(version) != major(ref.Version) {
		return nil, refuse("catalog_ref", "is version %s, and the loaded catalog has %s, another major version", ref.Version, version)
	}
	if len(declared) == 0 {
		return compile(rawURL, pushed, pushedNames)
	}
	parts, names, err := resolve(pushed, pushedNames, declared, ref)
	if err != nil {
		return nil, err
	}
	return compile(rawURL, parts, names)
}

// resolve matches the pushed parts to the declared ones and adds the
// declared constants, in declared order.
func resolve(pushed []Part, pushedNames []fieldNames, declared []Declared, ref Ref) ([]Part, []fieldNames, error) {
	matched := make([]bool, len(pushed))
	var parts []Part
	var names []fieldNames
	for _, d := range declared {
		dp := Part{In: d.In, Name: d.Name, Value: d.Value, Prefix: d.Prefix, Constant: true}
		i := indexOf(pushed, dp.key())
		switch {
		case d.Secret && i < 0:
			return nil, nil, refuse("parts", "does not carry the secret part %s %s that %s/%s declares, so the credential would be sent in part",
				d.In, d.Name, ref.App, ref.Provider)
		case d.Secret:
			if pushed[i].Prefix != d.Prefix {
				return nil, nil, refuse(pushedNames[i].of("name"), "is sent with the prefix %q, and %s/%s declares %q",
					pushed[i].Prefix, ref.App, ref.Provider, d.Prefix)
			}
			matched[i] = true
			parts, names = append(parts, pushed[i]), append(names, pushedNames[i])
		case i >= 0:
			return nil, nil, refuse(pushedNames[i].of("name"), "is a constant part that %s/%s supplies, which is never pushed", ref.App, ref.Provider)
		case d.Value == "":
			return nil, nil, refuse("catalog_ref", "%s/%s declares the non-secret part %s %s with no value, which nothing supplies",
				ref.App, ref.Provider, d.In, d.Name)
		default:
			if f, reason := checkPart(dp); f != "" {
				return nil, nil, refuse("catalog_ref", "%s/%s declares the constant part %s %s, whose %s %s", ref.App, ref.Provider, d.In, d.Name, f, reason)
			}
			parts = append(parts, dp)
			names = append(names, fieldNames{fixed: "catalog_ref"})
		}
	}
	for i, ok := range matched {
		if !ok {
			return nil, nil, refuse(pushedNames[i].of("name"), "is a part %s/%s does not declare", ref.App, ref.Provider)
		}
	}
	return parts, names, nil
}

func indexOf(parts []Part, key string) int {
	for i, p := range parts {
		if p.key() == key {
			return i
		}
	}
	return -1
}

func major(version string) string {
	m, _, _ := strings.Cut(version, ".")
	return m
}
