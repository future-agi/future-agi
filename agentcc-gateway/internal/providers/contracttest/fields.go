package contracttest

import (
	"reflect"
	"sort"
	"strings"
	"testing"
)

// AssertNoUnknownFields audits raw against typ for case c: it fails for every
// path UnknownFields reports that c's meta.json does not allowlist. Call it
// (or AssertKnownPaths) exactly once per case so Discover can check the
// allowlist for stale entries.
func AssertNoUnknownFields(t testing.TB, c Case, raw []byte, typ reflect.Type) {
	t.Helper()
	AssertKnownPaths(t, c, UnknownFields(raw, typ))
}

// AssertKnownPaths is AssertNoUnknownFields for paths the caller gathered
// itself — typically a stream, audited event by event with UnknownFieldsAt
// and collected into one list.
func AssertKnownPaths(t testing.TB, c Case, paths []string) {
	t.Helper()
	allowed := make(map[string]bool, len(c.Meta.AllowedUnknownFields))
	for _, f := range c.Meta.AllowedUnknownFields {
		allowed[f.Path] = true
	}
	seen := map[string]bool{}
	var used, unknown []string
	for _, p := range paths {
		if seen[p] {
			continue
		}
		seen[p] = true
		if allowed[p] {
			used = append(used, p)
		} else {
			unknown = append(unknown, p)
		}
	}
	c.Meta.note(auditFields, c.Name, used)
	if len(unknown) > 0 {
		sort.Strings(unknown)
		t.Errorf("upstream response has fields the adapter's structs do not represent (content would be dropped):\n  %s\n"+
			"Represent them, or allowlist each in %s/%s under allowed_unknown_fields with a reason.",
			strings.Join(unknown, "\n  "), c.Meta.dir, MetaFile)
	}
}
