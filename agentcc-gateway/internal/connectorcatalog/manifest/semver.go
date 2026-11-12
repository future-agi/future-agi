package manifest

import (
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strconv"
	"strings"
)

// Bump is a semver increment, smallest first.
type Bump int

const (
	BumpNone Bump = iota
	BumpPatch
	BumpMinor
	BumpMajor
)

func (b Bump) String() string { return [...]string{"no", "patch", "minor", "major"}[b] }

// Change is the bump a new version of a record requires, and why.
type Change struct {
	Bump    Bump
	Reasons []string
}

func (c *Change) need(b Bump, format string, args ...any) {
	c.Bump = max(c.Bump, b)
	c.Reasons = append(c.Reasons, fmt.Sprintf(format, args...))
}

// Record is an app, provider or platform record.
type Record interface {
	recordID() string
	recordVersion() string
}

func (a *App) recordID() string           { return a.ID }
func (a *App) recordVersion() string      { return a.Version }
func (p *Provider) recordID() string      { return p.App + "/" + p.ID }
func (p *Provider) recordVersion() string { return p.Version }
func (p *Platform) recordID() string      { return "platform/" + p.ID }
func (p *Platform) recordVersion() string { return p.Version }

// SemverDiff returns the bump the change from old to new requires
// (scaling.md §7, CONNECTORS-PLAN §2.5). Major: a tool removed, a required
// input added without a default, an input or output key removed or renamed,
// auth.kind changed, a requested scope added (users consent again), an
// account mode removed, a credential part or URL variable the customer must
// now supply, EE's id or env prefix changed, or what EE or OSS receives
// withdrawn or renamed (EE takes another provider; an OSS server id, which
// names its tools, changes). Minor: a tool, optional input, output key or
// account mode added, a scope removed, an endpoint, posture or annotation
// changed, or EE or OSS offered more. Patch: any other change.
func SemverDiff(old, new Record) (Change, error) {
	var ch Change
	if old.recordID() != new.recordID() {
		return ch, fmt.Errorf("cannot diff %s against %s", old.recordID(), new.recordID())
	}
	switch o := old.(type) {
	case *App:
		n, ok := new.(*App)
		if !ok {
			return ch, fmt.Errorf("cannot diff app %s against another kind of record", o.ID)
		}
		diffApp(&ch, o, n)
	case *Provider:
		n, ok := new.(*Provider)
		if !ok {
			return ch, fmt.Errorf("cannot diff provider %s against another kind of record", o.recordID())
		}
		diffProvider(&ch, o, n)
	case *Platform:
		n, ok := new.(*Platform)
		if !ok {
			return ch, fmt.Errorf("cannot diff platform %s against another kind of record", o.ID)
		}
		for _, m := range o.Connect.Modes {
			if !slices.Contains(n.Connect.Modes, m) {
				ch.need(BumpMajor, "connect mode %s removed", m)
			}
		}
		if o.Connect.URLPattern != n.Connect.URLPattern {
			ch.need(BumpMinor, "connect.urlPattern changed")
		}
	}
	if ch.Bump == BumpNone && !sameContent(old, new) {
		ch.need(BumpPatch, "patch: a change with no interface change")
	}
	return ch, nil
}

// CheckVersionBump refuses a new version that does not move by the bump
// SemverDiff requires, or that moves backwards.
func CheckVersionBump(old, new Record) error {
	ch, err := SemverDiff(old, new)
	if err != nil {
		return err
	}
	ov, err1 := parseSemver(old.recordVersion())
	nv, err2 := parseSemver(new.recordVersion())
	if err := errors.Join(err1, err2); err != nil {
		return err
	}
	moved := fmt.Sprintf("(%s -> %s)", old.recordVersion(), new.recordVersion())
	if slices.Compare(nv[:], ov[:]) < 0 {
		return fmt.Errorf("%s: version went backwards %s", new.recordID(), moved)
	}
	ok := true
	switch ch.Bump {
	case BumpMajor:
		ok = nv[0] > ov[0]
	case BumpMinor:
		ok = nv[0] > ov[0] || nv[0] == ov[0] && nv[1] > ov[1]
	case BumpPatch:
		ok = slices.Compare(nv[:], ov[:]) > 0
	}
	if !ok {
		return fmt.Errorf("%s: needs a %s version bump %s: %s", new.recordID(), ch.Bump, moved, strings.Join(ch.Reasons, "; "))
	}
	return nil
}

func parseSemver(v string) ([3]int, error) {
	var out [3]int
	parts := strings.Split(v, ".")
	if len(parts) != 3 {
		return out, fmt.Errorf("version %q is not MAJOR.MINOR.PATCH", v)
	}
	for i, s := range parts {
		n, err := strconv.Atoi(s)
		if err != nil || n < 0 {
			return out, fmt.Errorf("version %q is not MAJOR.MINOR.PATCH", v)
		}
		out[i] = n
	}
	return out, nil
}

// sameContent compares two records apart from their versions, generated
// tools included.
func sameContent(old, new Record) bool {
	encode := func(r Record) string {
		var v any
		switch x := r.(type) {
		case *App:
			c := *x
			c.Version = ""
			v = c
		case *Provider:
			c := *x
			c.Version = ""
			v = struct {
				P     Provider
				Tools []Tool
			}{c, x.AllTools()}
		case *Platform:
			c := *x
			c.Version = ""
			v = c
		}
		data, _ := json.Marshal(v)
		return string(data)
	}
	return encode(old) == encode(new)
}

func diffApp(ch *Change, o, n *App) {
	eeID := func(a *App) string {
		if a.XOmega != nil && a.XOmega.EEID != "" {
			return a.XOmega.EEID
		}
		return a.ID
	}
	envPrefix := func(a *App) string {
		if a.XOmega != nil && a.XOmega.EnvPrefix != "" {
			return a.XOmega.EnvPrefix
		}
		return eeEnvPrefix(eeID(a))
	}
	flags := func(a *App) (ee, oss string) {
		if a.XOmega == nil {
			return "", ""
		}
		return a.XOmega.EE, a.XOmega.OSS
	}
	if eeID(o) != eeID(n) {
		ch.need(BumpMajor, "x-omega.eeId %s -> %s", eeID(o), eeID(n))
	}
	if envPrefix(o) != envPrefix(n) {
		ch.need(BumpMajor, "x-omega.envPrefix %s -> %s", envPrefix(o), envPrefix(n))
	}
	oEE, oOSS := flags(o)
	nEE, nOSS := flags(n)
	diffIncludeFlags(ch, "the app", oEE, nEE, oOSS, nOSS)
	if o.DefaultProvider != n.DefaultProvider {
		ch.need(BumpMinor, "defaultProvider %s -> %s", o.DefaultProvider, n.DefaultProvider)
		// The default leads the emission order, so it can move EE to another
		// provider and rename the OSS servers that derive their ids.
		was := *n
		was.DefaultProvider = o.DefaultProvider
		diffEmitted(ch, &was, n)
	}
	if o.EECategory != n.EECategory || o.Group != n.Group {
		ch.need(BumpMinor, "eeCategory or group changed")
	}
}

// diffIncludeFlags: a record excluded from EE or OSS is withdrawn from it
// (major); one no longer excluded is offered to it (minor).
func diffIncludeFlags(ch *Change, what, oEE, nEE, oOSS, nOSS string) {
	for _, f := range []struct{ name, was, is string }{{"ee", oEE, nEE}, {"oss", oOSS, nOSS}} {
		switch was, is := excluded(f.was), excluded(f.is); {
		case !was && is:
			ch.need(BumpMajor, "x-omega.%s: %s is withdrawn from %s", f.name, what, strings.ToUpper(f.name))
		case was && !is:
			ch.need(BumpMinor, "x-omega.%s: %s is offered to %s", f.name, what, strings.ToUpper(f.name))
		}
	}
}
