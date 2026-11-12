package manifest

import (
	"encoding/json"
	"reflect"
	"slices"
	"strings"
)

func diffProvider(ch *Change, o, n *Provider) {
	for _, f := range []struct{ name, was, is string }{
		{"kind", o.Kind, n.Kind}, {"auth.kind", o.Auth.Kind, n.Auth.Kind}, {"transport.kind", o.Transport.Kind, n.Transport.Kind},
	} {
		if f.was != f.is {
			ch.need(BumpMajor, "%s %s -> %s", f.name, f.was, f.is)
		}
	}
	oScopes, nScopes := scopesOf(o), scopesOf(n)
	for _, s := range nScopes {
		if !slices.Contains(oScopes, s) {
			ch.need(BumpMajor, "scope %s added: users consent again", s)
		}
	}
	for _, s := range oScopes {
		if !slices.Contains(nScopes, s) {
			ch.need(BumpMinor, "scope %s removed", s)
		}
	}
	for _, m := range o.AccountModes {
		if !slices.Contains(n.AccountModes, m) {
			ch.need(BumpMajor, "account mode %s removed", m)
		}
	}
	for _, m := range n.AccountModes {
		if !slices.Contains(o.AccountModes, m) {
			ch.need(BumpMinor, "account mode %s added", m)
		}
	}
	diffCredentialParts(ch, o.Auth.CredentialParts, n.Auth.CredentialParts)
	diffVariables(ch, "", o.Transport.URLVariables, n.Transport.URLVariables)
	if o.Auth.OAuth2 != nil && n.Auth.OAuth2 != nil {
		diffVariables(ch, "OAuth ", o.Auth.OAuth2.URLVariables, n.Auth.OAuth2.URLVariables)
		if o.Auth.OAuth2.AuthorizeURL != n.Auth.OAuth2.AuthorizeURL || o.Auth.OAuth2.TokenURL != n.Auth.OAuth2.TokenURL {
			ch.need(BumpMinor, "an OAuth endpoint changed")
		}
	}
	if o.Transport.URL != n.Transport.URL {
		ch.need(BumpMinor, "transport.url changed")
	}
	if o.Tools.DefaultPosture != n.Tools.DefaultPosture {
		ch.need(BumpMinor, "tools.defaultPosture %s -> %s", o.Tools.DefaultPosture, n.Tools.DefaultPosture)
	}
	diffTools(ch, o.AllTools(), n.AllTools())
	diffProviderOmega(ch, o, n)
}

// diffProviderOmega measures what the provider's own change does to what EE
// and OSS receive. With both records' apps known (LoadDir sets them), the
// new app is emitted once as it is and once with the old provider in place
// of the new, so a change elsewhere in the app is not counted here. Without
// them, the provider's x-omega keys are compared: an ossId change is taken
// as a rename, and a public client no longer proven as a withdrawal.
func diffProviderOmega(ch *Change, o, n *Provider) {
	if o.app == nil || n.app == nil {
		flags := func(p *Provider) (ee, oss, ossID string) {
			if p.XOmega == nil {
				return "", "", ""
			}
			return p.XOmega.EE, p.XOmega.OSS, p.XOmega.OSSID
		}
		oEE, oOSS, oID := flags(o)
		nEE, nOSS, nID := flags(n)
		diffIncludeFlags(ch, "the provider", oEE, nEE, oOSS, nOSS)
		if oID != nID {
			ch.need(BumpMajor, "x-omega.ossId %q -> %q: OSS renames the server and its tools", oID, nID)
		}
		diffPublicClient(ch, o, n)
		return
	}
	was := *n.app
	was.Providers = slices.Clone(n.app.Providers)
	for i, p := range was.Providers {
		if p.ID == o.ID {
			was.Providers[i] = o
		}
	}
	diffEmitted(ch, &was, n.app)
}

// diffEmitted compares what EE and OSS receive from two versions of an app.
// EE taking another provider breaks its connections, and an OSS server
// withdrawn or renamed breaks its users' tool names (<server>__<tool>), so
// either is major; EE or a server offered is minor. OSS's servers are the
// ones its registry lists (OSSListed), so a record change that makes
// OSSLeftOutReason leave a server out withdraws it.
func diffEmitted(ch *Change, was, is *App) {
	wp, wok := EligibleForEE(was)
	ip, iok := EligibleForEE(is)
	switch {
	case wok && !iok:
		ch.need(BumpMajor, "EE no longer receives the app (it took provider %s)", wp.ID)
	case !wok && iok:
		ch.need(BumpMinor, "EE receives the app, through provider %s", ip.ID)
	case wok && iok && wp.ID != ip.ID:
		ch.need(BumpMajor, "EE takes provider %s instead of %s", ip.ID, wp.ID)
	}
	servers := func(a *App) map[string]string { // provider id -> OSS server id, as OSS lists them
		m := map[string]string{}
		for _, s := range OSSListed(a) {
			m[s.Provider.ID] = s.ID
		}
		return m
	}
	wm, im := servers(was), servers(is)
	for _, pid := range sortedNames(wm) {
		switch id, ok := im[pid]; {
		case !ok:
			why := ""
			if p := is.Provider(pid); p != nil && OSSLeftOutReason(p) != "" {
				why = ": " + OSSLeftOutReason(p)
			}
			ch.need(BumpMajor, "OSS no longer receives server %s (provider %s)%s", wm[pid], pid, why)
		case id != wm[pid]:
			ch.need(BumpMajor, "OSS server %s (provider %s) is renamed %s, and so are its tools", wm[pid], pid, id)
		}
	}
	for _, pid := range sortedNames(im) {
		if _, ok := wm[pid]; !ok {
			ch.need(BumpMinor, "OSS receives server %s (provider %s)", im[pid], pid)
		}
	}
}

func scopesOf(p *Provider) []string {
	if p.Auth.OAuth2 == nil {
		return nil
	}
	return p.Auth.OAuth2.Scopes
}

// diffCredentialParts: a part the customer must now supply (a secret, or a
// setting with no constant value) is major; a dropped part is minor.
func diffCredentialParts(ch *Change, o, n []CredentialPart) {
	key := func(p CredentialPart) string { return p.In + " " + strings.ToLower(p.Name) }
	has := func(list []CredentialPart, k string) bool {
		return slices.ContainsFunc(list, func(p CredentialPart) bool { return key(p) == k })
	}
	for _, p := range n {
		if !has(o, key(p)) {
			if p.Secret || p.Value == "" {
				ch.need(BumpMajor, "credential part %s %s added", p.In, p.Name)
			} else {
				ch.need(BumpMinor, "constant credential part %s %s added", p.In, p.Name)
			}
		}
	}
	for _, p := range o {
		if !has(n, key(p)) {
			ch.need(BumpMinor, "credential part %s %s removed", p.In, p.Name)
		}
	}
}

// diffVariables: a URL variable the customer must now fill (no default) is
// major, and so is a rename, which removes one and adds another.
func diffVariables(ch *Change, kind string, o, n map[string]URLVariable) {
	for _, name := range sortedNames(n) {
		if _, ok := o[name]; !ok {
			if n[name].Default == "" {
				ch.need(BumpMajor, "%sURL variable %s added", kind, name)
			} else {
				ch.need(BumpMinor, "%sURL variable %s added, with a default", kind, name)
			}
		}
	}
	for _, name := range sortedNames(o) {
		if _, ok := n[name]; !ok {
			ch.need(BumpMinor, "%sURL variable %s removed", kind, name)
		}
	}
}

func diffTools(ch *Change, o, n []Tool) {
	byName := func(tools []Tool) map[string]Tool {
		m := map[string]Tool{}
		for _, t := range tools {
			m[t.Name] = t
		}
		return m
	}
	om, nm := byName(o), byName(n)
	for _, name := range sortedNames(om) {
		nt, ok := nm[name]
		if !ok {
			ch.need(BumpMajor, "tool %s removed", name)
			continue
		}
		ot := om[name]
		diffSchema(ch, name, "input", ot.InputSchema, nt.InputSchema)
		diffSchema(ch, name, "output", ot.OutputSchema, nt.OutputSchema)
		if ot.Annotations != nt.Annotations {
			ch.need(BumpMinor, "tool %s: annotations changed", name)
		}
		if ot.Posture != nt.Posture {
			ch.need(BumpMinor, "tool %s: posture %q -> %q", name, ot.Posture, nt.Posture)
		}
		if !reflect.DeepEqual(ot.Operation, nt.Operation) {
			ch.need(BumpMinor, "tool %s: operation changed", name)
		}
	}
	for _, name := range sortedNames(nm) {
		if _, ok := om[name]; !ok {
			ch.need(BumpMinor, "tool %s added", name)
		}
	}
}

// schemaKeys is the top level of a tool's input or output schema.
type schemaKeys struct {
	Properties map[string]map[string]any `json:"properties"`
	Required   []string                  `json:"required"`
}

func readSchemaKeys(doc json.RawMessage) schemaKeys {
	var s schemaKeys
	if len(doc) > 0 {
		// The document is a JSON object (checked at load). A keyword or
		// property of another type, such as a boolean subschema, is
		// skipped: json.Unmarshal decodes everything else and reports only
		// the first mismatch, which the diff does not need.
		_ = json.Unmarshal(doc, &s)
	}
	return s
}

// diffSchema compares the top-level keys of a tool's input or output
// schema: a removed key is major; an added required input with no default
// is major; any other addition is minor.
func diffSchema(ch *Change, tool, side string, o, n json.RawMessage) {
	os, ns := readSchemaKeys(o), readSchemaKeys(n)
	for _, key := range sortedNames(os.Properties) {
		if _, ok := ns.Properties[key]; !ok {
			ch.need(BumpMajor, "tool %s: %s %s removed", tool, side, key)
		}
	}
	for _, key := range sortedNames(ns.Properties) {
		_, existed := os.Properties[key]
		required := side == "input" && slices.Contains(ns.Required, key) && !slices.Contains(os.Required, key)
		_, hasDefault := ns.Properties[key]["default"]
		switch {
		case required && !hasDefault && existed:
			ch.need(BumpMajor, "tool %s: input %s became required", tool, key)
		case required && !hasDefault:
			ch.need(BumpMajor, "tool %s: required input %s added", tool, key)
		case !existed && required:
			ch.need(BumpMinor, "tool %s: required input %s added, with a default", tool, key)
		case !existed && side == "input":
			ch.need(BumpMinor, "tool %s: optional input %s added", tool, key)
		case !existed:
			ch.need(BumpMinor, "tool %s: output %s added", tool, key)
		}
	}
}
