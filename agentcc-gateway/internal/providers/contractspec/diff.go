package contractspec

import (
	"fmt"
	"io"
	"sort"
	"strings"
)

// ChangeKind names one kind of difference between two subsets.
type ChangeKind string

// Change kinds. The ones that can carry content the gateway does not parse
// fail the drift check; the rest are reported for information.
const (
	ChangeNewRoot          ChangeKind = "new root"
	ChangeRemovedRoot      ChangeKind = "removed root"
	ChangeNewField         ChangeKind = "new field"
	ChangeRemovedField     ChangeKind = "removed field"
	ChangeKindAdded        ChangeKind = "kind changed"
	ChangeKindRemoved      ChangeKind = "kind narrowed"
	ChangeBecameNullable   ChangeKind = "became nullable"
	ChangeNoLongerNullable ChangeKind = "no longer nullable"
	ChangeNewEnumValue     ChangeKind = "new enum value"
	ChangeRemovedEnumValue ChangeKind = "removed enum value"
	ChangeOpenEnumGrew     ChangeKind = "open enum grew"
	ChangeEnumOpened       ChangeKind = "enum became open"
	ChangeEnumClosed       ChangeKind = "enum became closed"
	ChangeNewEvent         ChangeKind = "new stream event"
	ChangeRemovedEvent     ChangeKind = "removed stream event"
	ChangeNewException     ChangeKind = "new error event"
	ChangeBecameOpaque     ChangeKind = "became opaque"
	ChangeNoLongerOpaque   ChangeKind = "now structured"
	ChangeDeprecated       ChangeKind = "deprecated"
	ChangeUndeprecated     ChangeKind = "no longer deprecated"
	ChangeSelection        ChangeKind = "selection changed"
	ChangeUnextractable    ChangeKind = "root not extractable"
)

// failing are the kinds that fail the drift check: each can put a field, a
// value or an event in a response that the gateway's committed contract,
// and so its tests, do not know about.
var failing = map[ChangeKind]bool{
	ChangeNewRoot: true, ChangeRemovedRoot: true, ChangeNewField: true, ChangeKindAdded: true,
	ChangeBecameNullable: true, ChangeNewEnumValue: true, ChangeNewEvent: true, ChangeNewException: true,
	ChangeUnextractable: true,
}

// Change is one difference.
type Change struct {
	Kind ChangeKind
	Path string
	// Detail is the kinds of a new field, the values added or removed, or
	// the old and new kinds.
	Detail string
	// Below counts the paths under a new or removed field, which are not
	// reported on their own.
	Below int
}

// Fail reports whether the change fails the drift check.
func (c Change) Fail() bool { return failing[c.Kind] }

// Diff is the difference between a committed subset and a fresh one.
type Diff struct {
	ID      string
	Changes []Change
}

// Failing counts the changes that fail the drift check.
func (d Diff) Failing() int {
	n := 0
	for _, c := range d.Changes {
		if c.Fail() {
			n++
		}
	}
	return n
}

// DiffProvider compares a provider's committed subsets with fresh ones, by
// root.
func DiffProvider(committed, fresh []*Subset) []Diff {
	byRoot := func(ss []*Subset) map[string]*Subset {
		m := map[string]*Subset{}
		for _, s := range ss {
			m[s.ID()] = s
		}
		return m
	}
	o, n := byRoot(committed), byRoot(fresh)
	ids := map[string]bool{}
	for id := range o {
		ids[id] = true
	}
	for id := range n {
		ids[id] = true
	}
	var out []Diff
	for _, id := range sortedKeys(ids) {
		switch {
		case o[id] == nil:
			out = append(out, Diff{ID: id, Changes: []Change{{Kind: ChangeNewRoot, Path: "$", Detail: fmt.Sprintf("%d paths, not committed", len(n[id].Paths))}}})
		case n[id] == nil:
			out = append(out, Diff{ID: id, Changes: []Change{{Kind: ChangeRemovedRoot, Path: "$", Detail: "committed, but no longer extracted"}}})
		default:
			out = append(out, DiffSubsets(o[id], n[id]))
		}
	}
	return out
}

// DiffSubsets compares a committed subset (old) with a fresh one (new). A
// new or removed field is reported once, at its outermost path; the paths
// below it are counted, not listed.
func DiffSubsets(old, new *Subset) Diff {
	d := Diff{ID: new.ID()}
	add := func(c Change) { d.Changes = append(d.Changes, c) }

	if old.Select != new.Select {
		add(Change{Kind: ChangeSelection, Path: "$", Detail: old.Select + " -> " + new.Select})
	}
	// A new (or removed) event's paths are counted on its event line.
	inEvent := map[string]bool{}
	countBelow := func(s *Subset, e string) int {
		n := 0
		for p := range s.Paths {
			if ev, _ := splitEvent(p); ev == e {
				inEvent[p] = true
				n++
			}
		}
		return n
	}
	newExc := toSet(new.ExceptionEvents)
	evAdd, evRem := setDiff(old.Events, new.Events)
	for _, e := range evAdd {
		k := ChangeNewEvent
		if newExc[e] {
			k = ChangeNewException
		}
		add(Change{Kind: k, Path: e + ":$", Below: countBelow(new, e)})
	}
	for _, e := range evRem {
		add(Change{Kind: ChangeRemovedEvent, Path: e + ":$", Below: countBelow(old, e)})
	}

	paths := map[string]bool{}
	for p := range old.Paths {
		paths[p] = true
	}
	for p := range new.Paths {
		paths[p] = true
	}
	var added, removed []string
	for _, p := range sortedKeys(paths) {
		if inEvent[p] {
			continue
		}
		op, np := old.Paths[p], new.Paths[p]
		switch {
		case op == nil:
			added = append(added, p)
		case np == nil:
			removed = append(removed, p)
		default:
			diffPath(p, op, np, add)
		}
	}
	reportOutermost(added, ChangeNewField, new, add)
	reportOutermost(removed, ChangeRemovedField, old, add)

	sort.SliceStable(d.Changes, func(i, j int) bool {
		a, b := d.Changes[i], d.Changes[j]
		if a.Fail() != b.Fail() {
			return a.Fail()
		}
		return a.Path < b.Path
	})
	return d
}

// reportOutermost reports each path no ancestor of which is in paths,
// counting the rest under it. An added event's paths are covered by the
// new-event line and are only counted.
func reportOutermost(paths []string, kind ChangeKind, s *Subset, add func(Change)) {
	in := toSet(paths)
	var tops []Change
	for _, p := range paths {
		if outermost(p, in) != "" {
			continue // an ancestor is new (or gone) too
		}
		tops = append(tops, Change{Kind: kind, Path: p, Detail: describe(s.Paths[p])})
	}
	for i := range tops {
		for _, p := range paths {
			if p != tops[i].Path && under(p, tops[i].Path) {
				tops[i].Below++
			}
		}
		add(tops[i])
	}
}

func diffPath(p string, op, np *PathInfo, add func(Change)) {
	ea, er := setDiff(op.Enum, np.Enum)
	if len(ea) > 0 {
		k := ChangeNewEnumValue
		if np.OpenEnum && op.OpenEnum {
			k = ChangeOpenEnumGrew
		}
		add(Change{Kind: k, Path: p, Detail: "+ " + strings.Join(ea, ", ")})
	}
	if len(er) > 0 {
		add(Change{Kind: ChangeRemovedEnumValue, Path: p, Detail: "- " + strings.Join(er, ", ")})
	}
	switch {
	case !op.OpenEnum && np.OpenEnum && len(np.Enum) > 0:
		add(Change{Kind: ChangeEnumOpened, Path: p})
	case op.OpenEnum && !np.OpenEnum && len(np.Enum) > 0:
		add(Change{Kind: ChangeEnumClosed, Path: p})
	}
	ka, kr := setDiff(op.Kinds, np.Kinds)
	switch {
	case len(ka) > 0:
		add(Change{Kind: ChangeKindAdded, Path: p, Detail: kinds(op) + " -> " + kinds(np)})
	case len(kr) > 0:
		add(Change{Kind: ChangeKindRemoved, Path: p, Detail: kinds(op) + " -> " + kinds(np)})
	}
	switch {
	case !op.Nullable && np.Nullable:
		add(Change{Kind: ChangeBecameNullable, Path: p})
	case op.Nullable && !np.Nullable:
		add(Change{Kind: ChangeNoLongerNullable, Path: p})
	}
	switch {
	case !op.Opaque && np.Opaque:
		add(Change{Kind: ChangeBecameOpaque, Path: p})
	case op.Opaque && !np.Opaque:
		add(Change{Kind: ChangeNoLongerOpaque, Path: p})
	}
	switch {
	case !op.Deprecated && np.Deprecated:
		add(Change{Kind: ChangeDeprecated, Path: p})
	case op.Deprecated && !np.Deprecated:
		add(Change{Kind: ChangeUndeprecated, Path: p})
	}
}

func kinds(p *PathInfo) string {
	if len(p.Kinds) == 0 {
		return "(none)"
	}
	return strings.Join(p.Kinds, "|")
}

// describe renders a path's kinds, nullability and (short) enum.
func describe(p *PathInfo) string {
	if p == nil {
		return ""
	}
	s := kinds(p)
	if p.Nullable {
		s += "|null"
	}
	if p.Opaque {
		s += " opaque"
	}
	if n := len(p.Enum); n > 0 {
		if n <= 12 {
			s += " enum=" + strings.Join(p.Enum, ",")
		} else {
			s += fmt.Sprintf(" enum=(%d values)", n)
		}
		if p.OpenEnum {
			s += " (open)"
		}
	}
	return s
}

// Format writes a review-friendly report: one line per change, failing
// ones first, then a count.
func (d Diff) Format(w io.Writer) {
	if len(d.Changes) == 0 {
		_, _ = fmt.Fprintf(w, "%s: no changes\n", d.ID)
		return
	}
	_, _ = fmt.Fprintf(w, "%s:\n", d.ID)
	width := 0
	for _, c := range d.Changes {
		if len(c.Path) > width && len(c.Path) < 72 {
			width = len(c.Path)
		}
	}
	for _, c := range d.Changes {
		tag := "info"
		if c.Fail() {
			tag = "FAIL"
		}
		line := fmt.Sprintf("  %s  %-20s %-*s", tag, c.Kind, width, c.Path)
		if c.Detail != "" {
			line += "  " + c.Detail
		}
		if c.Below > 0 {
			line += fmt.Sprintf("  (+%d paths below)", c.Below)
		}
		_, _ = fmt.Fprintln(w, strings.TrimRight(line, " "))
	}
	_, _ = fmt.Fprintf(w, "  => %d failing change(s), %d informational\n", d.Failing(), len(d.Changes)-d.Failing())
}

func toSet(list []string) map[string]bool {
	m := make(map[string]bool, len(list))
	for _, v := range list {
		m[v] = true
	}
	return m
}

// setDiff returns b's values missing from a (added) and a's missing from b
// (removed), in order.
func setDiff(a, b []string) (added, removed []string) {
	as, bs := toSet(a), toSet(b)
	for _, v := range b {
		if !as[v] {
			added = append(added, v)
		}
	}
	for _, v := range a {
		if !bs[v] {
			removed = append(removed, v)
		}
	}
	return added, removed
}
