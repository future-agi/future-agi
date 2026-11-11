package contractspec

import (
	"bytes"
	"encoding"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"reflect"
	"sort"
	"strings"
)

// Spec coverage: every path in a provider's committed subsets must be
// accounted for by the adapter that parses it, and every enum value and
// stream event it lists must be handled, so "the adapter covers the
// provider's API" is a number a test checks rather than a belief.
//
// A spec path is, in order:
//
//   - mapped: the adapter's Go type decodes it (found by reflection, the way
//     encoding/json would decode, plus Bindings for payloads the adapter
//     decodes in a second step);
//   - opaque: it sits in a json.RawMessage, any or custom-unmarshaled field
//     that carries it as a blob. A blob over spec structure must be listed
//     in the allowlist's "opaque" section with what happens to it;
//   - passthrough: an unknown key directly under a type whose UnmarshalJSON
//     captures unknown keys (models.ChatCompletionResponse's Extra) and
//     forwards them, subtree and all;
//   - allowlisted: unmapped, and its outermost unmapped path is in the
//     allowlist's "unmapped" section with a reason.
//
// Anything else fails, and so does an allowlist entry nothing matches.

// Binding says which Go types decode the JSON at Path, a subset path. A root
// binding is "$" (or "<event>:$", "chunk:$"); a nested one rebinds a
// json.RawMessage field the adapter decodes later into its own type. Several
// types mean the adapter tries each (a union decoded by trial), and their
// fields are united.
type Binding struct {
	Path  string
	Types []reflect.Type
}

// Bind is a Binding literal.
func Bind(path string, types ...reflect.Type) Binding { return Binding{Path: path, Types: types} }

// EnumCheck requires every value the spec lists at Path to be handled.
type EnumCheck struct {
	Path string
	// By names what decides (the adapter's lookup function), for messages.
	By string
	// Handled reports whether the adapter handles v. Call the adapter's real
	// lookup/map function, so the check follows the code.
	Handled func(v string) bool
}

// Root is one subset's coverage declaration.
type Root struct {
	// Subset is the subset ID, "anthropic/messages".
	Subset string
	// EachEvent binds every event root ("<event>:$") of an event-split
	// subset to these types (the stream envelope).
	EachEvent []reflect.Type
	Bindings  []Binding
	// Passthrough are types whose UnmarshalJSON captures unknown keys and
	// forwards them (an Extra map). Reflection cannot tell such a type from
	// one that drops what it does not know, so they are declared.
	Passthrough []reflect.Type
	Enums       []EnumCheck
	// Events, when set, must handle every stream event the subset lists.
	Events func(event string) bool
}

// Coverage is one provider's spec coverage declaration.
type Coverage struct {
	// Provider names the subsets every root of which must be accounted for
	// here: by a Root, or in Elsewhere.
	Provider string
	// Allowlist is the allowlist file, relative to the test's package
	// directory. Default: testdata/spec_allowlist.json.
	Allowlist string
	Roots     []Root
	// Elsewhere maps subset IDs of Provider that are not checked here to
	// where they are (or why not), e.g. a list-models root that another
	// package parses.
	Elsewhere map[string]string
	// Subsets is the subsets tree; nil means the committed one.
	Subsets fs.FS
}

// TB is the part of testing.TB Check uses.
type TB interface {
	Helper()
	Errorf(format string, args ...any)
	Logf(format string, args ...any)
	Fatalf(format string, args ...any)
}

// Allowlist is a provider's spec allowlist file.
type Allowlist struct {
	Provider string `json:"provider"`
	Note     string `json:"note,omitempty"`
	// Unmapped are outermost spec paths no Go type maps.
	Unmapped []AllowedPath `json:"unmapped"`
	// Opaque are Go blobs (json.RawMessage, any) over spec structure.
	Opaque []AllowedPath `json:"opaque"`
	// Enums are spec enum values the adapter does not handle.
	Enums []AllowedValue `json:"enums"`
	// Events are spec stream events the adapter does not handle.
	Events []AllowedEvent `json:"events"`
	// Kinds are mapped paths whose spec kinds the Go type cannot decode.
	Kinds []AllowedPath `json:"kinds,omitempty"`
}

// AllowedPath is one unmapped path or opaque blob, with why.
type AllowedPath struct {
	Subset string `json:"subset"`
	Path   string `json:"path"`
	Reason string `json:"reason"`
}

// AllowedValue is one unhandled enum value, with why.
type AllowedValue struct {
	Subset string `json:"subset"`
	Path   string `json:"path"`
	Value  string `json:"value"`
	Reason string `json:"reason"`
}

// AllowedEvent is one unhandled stream event, with why.
type AllowedEvent struct {
	Subset string `json:"subset"`
	Event  string `json:"event"`
	Reason string `json:"reason"`
}

// DraftEnv, set to 1, makes Check (re)write the allowlist with every
// current finding: existing reasons are kept, new entries get a TODO reason
// (which still fails) for a human to replace.
const DraftEnv = "CONTRACTSPEC_DRAFT"

// RootReport is what Check found for one subset.
type RootReport struct {
	Subset string
	Total  int
	// Mapped, Opaque, Passthrough and Allowlisted count spec paths.
	Mapped, Opaque, Passthrough, Allowlisted int
	// Implied counts paths only unhandled enum variants or stream events
	// carry: their allowlist entries account for them.
	Implied int
	// Unmapped are the outermost unmapped paths; Unaccounted those of them
	// the allowlist does not list.
	Unmapped, Unaccounted []string
	// Blobs are Go opaque paths over spec structure; Undeclared those the
	// allowlist does not list.
	Blobs, Undeclared []string
	EnumPaths         int // enum paths checked
	EnumValues        int // values checked
	EnumAllowlisted   int
	EnumUnhandled     []string // "path=value" neither handled nor allowlisted
	Events            int
	EventsAllowlisted int
	EventsUnhandled   []string
	EnumPathsInSpec   int // every enum path in the subset, for scale
	// Mismatched are mapped paths whose spec kinds the Go type cannot
	// decode (an integer code into a string field fails the whole decode);
	// MismatchUnaccounted those the allowlist does not list.
	Mismatched, MismatchUnaccounted []string
	mismatchWhy                     []string

	missingEnums []AllowedValue // EnumUnhandled, for a draft
}

// Accounted is the number of spec paths the adapter accounts for.
func (r RootReport) Accounted() int {
	return r.Mapped + r.Opaque + r.Passthrough + r.Allowlisted + r.Implied
}

// Summary is the one-line coverage figure the test logs.
func (r RootReport) Summary() string {
	s := fmt.Sprintf("%s: %d/%d paths mapped", r.Subset, r.Mapped+r.Passthrough, r.Total)
	if r.Passthrough > 0 {
		s += fmt.Sprintf(" (%d forwarded via Extra)", r.Passthrough)
	}
	s += fmt.Sprintf(", %d in declared blobs, %d allowlisted (%d entries)", r.Opaque, r.Allowlisted, len(r.Unmapped)-len(r.Unaccounted))
	if r.Implied > 0 {
		s += fmt.Sprintf(", %d on unhandled variants/events", r.Implied)
	}
	s += fmt.Sprintf("; %d/%d accounted", r.Accounted(), r.Total)
	if len(r.Unaccounted) > 0 {
		s += fmt.Sprintf(" (%d UNACCOUNTED roots)", len(r.Unaccounted))
	}
	if r.EnumPaths > 0 {
		s += fmt.Sprintf("; enums: %d values at %d paths checked, %d allowlisted", r.EnumValues, r.EnumPaths, r.EnumAllowlisted)
	}
	if r.Events > 0 {
		s += fmt.Sprintf("; events: %d/%d handled, %d allowlisted", r.Events-r.EventsAllowlisted-len(r.EventsUnhandled), r.Events, r.EventsAllowlisted)
	}
	return s
}

// Check verifies c against the committed subsets and the allowlist, logs a
// coverage line per subset, and fails t for every unaccounted path,
// undeclared blob, unhandled enum value or event, and stale allowlist entry.
func Check(t TB, c Coverage) []RootReport {
	t.Helper()
	fsys := c.Subsets
	if fsys == nil {
		fsys = Committed()
	}
	subsets := map[string]*Subset{}
	all, err := LoadFS(fsys, c.Provider)
	if err != nil {
		t.Fatalf("loading %s subsets: %v", c.Provider, err)
	}
	if len(all) == 0 {
		t.Fatalf("no committed subsets for provider %q (internal/providers/contractspec/subsets)", c.Provider)
	}
	for _, s := range all {
		subsets[s.ID()] = s
	}
	for _, r := range c.Roots {
		if subsets[r.Subset] == nil {
			s, err := LoadSubsetFS(fsys, r.Subset)
			if err != nil {
				t.Fatalf("root %s: %v", r.Subset, err)
			}
			subsets[r.Subset] = s
		}
	}
	file := c.Allowlist
	if file == "" {
		file = "testdata/spec_allowlist.json"
	}
	al, err := readAllowlist(file)
	if err != nil {
		t.Fatalf("%v", err)
	}
	if al.Provider != "" && al.Provider != c.Provider {
		t.Errorf("%s is for provider %q, not %q", file, al.Provider, c.Provider)
	}

	checked := map[string]bool{}
	for _, r := range c.Roots {
		if checked[r.Subset] {
			t.Errorf("subset %s is declared twice", r.Subset)
		}
		checked[r.Subset] = true
	}
	for _, s := range all {
		if !checked[s.ID()] && c.Elsewhere[s.ID()] == "" {
			t.Errorf("subset %s has no coverage declaration: add a Root for it (bind the Go type that decodes it), or say in Elsewhere where it is parsed", s.ID())
		}
	}
	for id := range c.Elsewhere {
		if checked[id] {
			t.Errorf("subset %s is both checked and in Elsewhere", id)
		} else if subsets[id] == nil {
			t.Errorf("Elsewhere names %s, which is not a committed subset", id)
		}
	}

	u := newUsage(al)
	var reports []RootReport
	for _, r := range c.Roots {
		rep := checkRoot(subsets[r.Subset], r, u)
		reports = append(reports, rep)
		t.Logf("%s", rep.Summary())
		if len(rep.Unaccounted) > 0 {
			t.Errorf("%s: spec paths the adapter's types do not map (content would be dropped):\n  %s\nMap them in the adapter's structs (or add a Binding for a payload decoded in a second step), or allowlist each in %s under \"unmapped\" with a reason (\"known gap <ID>: ...\" or \"no canonical field: ...\").",
				r.Subset, describePaths(subsets[r.Subset], rep.Unaccounted), file)
		}
		if len(rep.Undeclared) > 0 {
			t.Errorf("%s: Go blobs (json.RawMessage / any) hold spec structure the types do not show:\n  %s\nBind the type the adapter decodes them into, or list each in %s under \"opaque\" saying what happens to the blob.",
				r.Subset, strings.Join(rep.Undeclared, "\n  "), file)
		}
		if len(rep.MismatchUnaccounted) > 0 {
			t.Errorf("%s: spec kinds the adapter's types cannot decode (json.Unmarshal fails on the whole body):\n  %s\nWiden the Go type (json.RawMessage, json.Number, a custom unmarshaler), or allowlist each in %s under \"kinds\" with a reason.",
				r.Subset, strings.Join(rep.MismatchUnaccounted, "\n  "), file)
		}
		if len(rep.EnumUnhandled) > 0 {
			t.Errorf("%s: spec enum values the adapter does not handle:\n  %s\nHandle them, or allowlist each in %s under \"enums\" with a reason.",
				r.Subset, strings.Join(rep.EnumUnhandled, "\n  "), file)
		}
		if len(rep.EventsUnhandled) > 0 {
			t.Errorf("%s: spec stream events the adapter does not handle: %s\nHandle them, or allowlist each in %s under \"events\" with a reason.",
				r.Subset, strings.Join(rep.EventsUnhandled, ", "), file)
		}
	}
	for _, stale := range u.stale(checked) {
		t.Errorf("%s: stale allowlist entry (nothing matches it any more; delete it): %s", file, stale)
	}
	for _, todo := range u.todos() {
		t.Errorf("%s: entry without a real reason: %s", file, todo)
	}
	if os.Getenv(DraftEnv) == "1" {
		if err := writeDraft(file, c.Provider, al, reports, u); err != nil {
			t.Errorf("writing draft allowlist: %v", err)
		} else {
			t.Logf("%s=1: wrote %s; replace every TODO reason", DraftEnv, file)
		}
	}
	return reports
}

func describePaths(s *Subset, paths []string) string {
	lines := make([]string, len(paths))
	for i, p := range paths {
		lines[i] = p
		if info := s.Paths[p]; info != nil {
			lines[i] += "  [" + describe(info) + "]"
		}
	}
	return strings.Join(lines, "\n  ")
}

func readAllowlist(file string) (*Allowlist, error) {
	raw, err := os.ReadFile(file)
	if os.IsNotExist(err) && os.Getenv(DraftEnv) == "1" {
		return &Allowlist{}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("spec allowlist: %w (run with %s=1 to write a draft)", err, DraftEnv)
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var al Allowlist
	if err := dec.Decode(&al); err != nil {
		return nil, fmt.Errorf("%s: %w", file, err)
	}
	return &al, nil
}

// usage tracks which allowlist entries matched.
type usage struct {
	al                                    *Allowlist
	unmapped, opaque, enums, evnts, kinds map[string]int // key -> index
	used                                  map[string]bool
}

func pathKey(subset, path string) string     { return subset + "\x00" + path }
func valueKey(subset, path, v string) string { return subset + "\x00" + path + "\x00" + v }
func entryLabel(subset, path, extra string) string {
	if extra != "" {
		return fmt.Sprintf("%s %s = %s", subset, path, extra)
	}
	return subset + " " + path
}

func newUsage(al *Allowlist) *usage {
	u := &usage{al: al, unmapped: map[string]int{}, opaque: map[string]int{}, enums: map[string]int{}, evnts: map[string]int{}, kinds: map[string]int{}, used: map[string]bool{}}
	for i, e := range al.Kinds {
		u.kinds["k"+pathKey(e.Subset, e.Path)] = i
	}
	for i, e := range al.Unmapped {
		u.unmapped["u"+pathKey(e.Subset, e.Path)] = i
	}
	for i, e := range al.Opaque {
		u.opaque["o"+pathKey(e.Subset, e.Path)] = i
	}
	for i, e := range al.Enums {
		u.enums["e"+valueKey(e.Subset, e.Path, e.Value)] = i
	}
	for i, e := range al.Events {
		u.evnts["v"+pathKey(e.Subset, e.Event)] = i
	}
	return u
}

func (u *usage) has(m map[string]int, key string) bool {
	if _, ok := m[key]; ok {
		u.used[key] = true
		return true
	}
	return false
}

// stale lists entries nothing matched. Entries for subsets this Coverage did
// not check are stale too: they cannot be audited here.
func (u *usage) stale(checked map[string]bool) []string {
	var out []string
	for _, e := range u.al.Unmapped {
		if !u.used["u"+pathKey(e.Subset, e.Path)] {
			out = append(out, "unmapped "+entryLabel(e.Subset, e.Path, ""))
		}
	}
	for _, e := range u.al.Opaque {
		if !u.used["o"+pathKey(e.Subset, e.Path)] {
			out = append(out, "opaque "+entryLabel(e.Subset, e.Path, ""))
		}
	}
	for _, e := range u.al.Enums {
		if !u.used["e"+valueKey(e.Subset, e.Path, e.Value)] {
			out = append(out, "enum "+entryLabel(e.Subset, e.Path, e.Value))
		}
	}
	for _, e := range u.al.Events {
		if !u.used["v"+pathKey(e.Subset, e.Event)] {
			out = append(out, "event "+entryLabel(e.Subset, e.Event, ""))
		}
	}
	for _, e := range u.al.Kinds {
		if !u.used["k"+pathKey(e.Subset, e.Path)] {
			out = append(out, "kinds "+entryLabel(e.Subset, e.Path, ""))
		}
	}
	return out
}

func badReason(r string) bool {
	r = strings.TrimSpace(r)
	return r == "" || strings.HasPrefix(r, "TODO")
}

func (u *usage) todos() []string {
	var out []string
	for _, e := range u.al.Unmapped {
		if badReason(e.Reason) {
			out = append(out, "unmapped "+entryLabel(e.Subset, e.Path, ""))
		}
	}
	for _, e := range u.al.Opaque {
		if badReason(e.Reason) {
			out = append(out, "opaque "+entryLabel(e.Subset, e.Path, ""))
		}
	}
	for _, e := range u.al.Enums {
		if badReason(e.Reason) {
			out = append(out, "enum "+entryLabel(e.Subset, e.Path, e.Value))
		}
	}
	for _, e := range u.al.Events {
		if badReason(e.Reason) {
			out = append(out, "event "+entryLabel(e.Subset, e.Event, ""))
		}
	}
	for _, e := range u.al.Kinds {
		if badReason(e.Reason) {
			out = append(out, "kinds "+entryLabel(e.Subset, e.Path, ""))
		}
	}
	return out
}

// goKind is what the Go side does at a path.
type goKind int

const (
	goMapped      goKind = iota + 1 // a typed field decodes it
	goOpaque                        // json.RawMessage, any, or a custom unmarshaler: a blob
	goPassthrough                   // a struct whose UnmarshalJSON forwards unknown keys
)

// goPaths is every path the bound Go types decode, keyed by lower-cased path
// (encoding/json matches keys case-insensitively): what the Go side does
// there, and for a typed node the JSON kinds its type accepts.
type goPaths struct {
	kind   map[string]goKind
	accept map[string]map[string]bool
	types  map[string]string
}

func newGoPaths() goPaths {
	return goPaths{kind: map[string]goKind{}, accept: map[string]map[string]bool{}, types: map[string]string{}}
}

var jsonNumberType = reflect.TypeFor[json.Number]()

// accepts is the set of spec kinds a Go type decodes without error.
func accepts(typ reflect.Type, quoted bool) []string {
	if quoted { // ",string": a scalar inside a JSON string
		return []string{"string", "string(int64)", "integer", "number", "boolean"}
	}
	switch typ.Kind() {
	case reflect.Struct, reflect.Map:
		return []string{"object", "map"}
	case reflect.Slice, reflect.Array:
		if typ.Elem().Kind() == reflect.Uint8 {
			return []string{"string", "blob"}
		}
		return []string{"array"}
	case reflect.String:
		if typ == jsonNumberType {
			return []string{"integer", "number", "string", "string(int64)"}
		}
		return []string{"string", "string(int64)", "blob"}
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64,
		reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return []string{"integer"}
	case reflect.Float32, reflect.Float64:
		return []string{"integer", "number"}
	case reflect.Bool:
		return []string{"boolean"}
	}
	return nil
}

func (g goPaths) setAccept(key string, typ reflect.Type, quoted bool) {
	set := g.accept[key]
	if set == nil {
		set = map[string]bool{}
		g.accept[key] = set
	}
	for _, k := range accepts(typ, quoted) {
		set[k] = true
	}
	if g.types[key] == "" {
		g.types[key] = typ.String()
	} else if !strings.Contains(g.types[key], typ.String()) {
		g.types[key] += " | " + typ.String()
	}
}

var (
	rawMessageType  = reflect.TypeFor[json.RawMessage]()
	jsonUnmarshaler = reflect.TypeFor[json.Unmarshaler]()
	textUnmarshaler = reflect.TypeFor[encoding.TextUnmarshaler]()
)

func implements(t, iface reflect.Type) bool {
	return t.Implements(iface) || reflect.PointerTo(t).Implements(iface)
}

// add walks typ at path, recording what it decodes.
func (g goPaths) add(typ reflect.Type, path string, pass map[reflect.Type]bool, depth int, seen map[reflect.Type]int) {
	for typ.Kind() == reflect.Pointer {
		typ = typ.Elem()
	}
	key := strings.ToLower(path)
	if depth > maxDepth || seen[typ] > 2 { // recursive types: three levels are plenty
		return
	}
	if !pass[typ] && (typ == rawMessageType || typ.Kind() == reflect.Interface ||
		implements(typ, jsonUnmarshaler) || implements(typ, textUnmarshaler)) {
		if g.kind[key] != goMapped {
			g.kind[key] = goOpaque
		}
		return
	}
	if pass[typ] {
		g.kind[key] = goPassthrough
	} else if g.kind[key] == 0 || g.kind[key] == goOpaque {
		g.kind[key] = goMapped
	}
	g.setAccept(key, typ, false)
	seen[typ]++
	defer func() { seen[typ]-- }()
	switch typ.Kind() {
	case reflect.Struct:
		g.addFields(typ, path, pass, depth, seen, map[string]bool{})
	case reflect.Slice, reflect.Array:
		if typ.Elem().Kind() == reflect.Uint8 {
			return // []byte: base64 text
		}
		g.add(typ.Elem(), path+"[]", pass, depth+1, seen)
	case reflect.Map:
		g.add(typ.Elem(), path+".*", pass, depth+1, seen)
	}
}

func (g goPaths) addFields(typ reflect.Type, path string, pass map[reflect.Type]bool, depth int, seen map[reflect.Type]int, taken map[string]bool) {
	var embedded []reflect.Type
	for i := 0; i < typ.NumField(); i++ {
		f := typ.Field(i)
		tag := f.Tag.Get("json")
		if tag == "-" {
			continue
		}
		name, opts, _ := strings.Cut(tag, ",")
		if f.Anonymous && name == "" {
			et := f.Type
			for et.Kind() == reflect.Pointer {
				et = et.Elem()
			}
			if et.Kind() == reflect.Struct {
				embedded = append(embedded, et)
				continue
			}
		}
		if !f.IsExported() {
			continue
		}
		if name == "" {
			name = f.Name
		}
		lname := strings.ToLower(name)
		if taken[lname] {
			continue
		}
		taken[lname] = true
		child := path + "." + name
		if hasOpt(opts, "string") {
			ckey := strings.ToLower(child)
			g.kind[ckey] = goMapped // a scalar sent as a JSON string
			g.setAccept(ckey, f.Type, true)
			continue
		}
		g.add(f.Type, child, pass, depth+1, seen)
	}
	for _, et := range embedded { // promoted fields; the outer level shadows them
		g.addFields(et, path, pass, depth, seen, taken)
	}
}

func hasOpt(opts, want string) bool {
	for _, o := range strings.Split(opts, ",") {
		if o == want {
			return true
		}
	}
	return false
}

// buildGoPaths applies a root's bindings, shallowest first, so a nested
// binding refines the blob its parent left.
func buildGoPaths(s *Subset, r Root) goPaths {
	pass := map[reflect.Type]bool{}
	for _, t := range r.Passthrough {
		for t.Kind() == reflect.Pointer {
			t = t.Elem()
		}
		pass[t] = true
	}
	var binds []Binding
	if len(r.EachEvent) > 0 {
		for _, ev := range s.Events {
			binds = append(binds, Binding{Path: ev + ":$", Types: r.EachEvent})
		}
	}
	binds = append(binds, r.Bindings...)
	sort.SliceStable(binds, func(i, j int) bool { return depthOf(binds[i].Path) < depthOf(binds[j].Path) })
	g := newGoPaths()
	for _, b := range binds {
		key := strings.ToLower(b.Path)
		if g.kind[key] == goOpaque {
			delete(g.kind, key) // rebound: the blob is decoded after all
		}
		for _, t := range b.Types {
			g.add(t, b.Path, pass, 0, map[reflect.Type]int{})
		}
	}
	return g
}

func depthOf(p string) int {
	_, rest := splitEvent(p)
	return strings.Count(rest, ".") + strings.Count(rest, "[]")
}

// hasStructureBelow reports whether the spec says anything below p: child
// paths, or an object or array kind that is not free-form.
func hasStructureBelow(s *Subset, p string) bool {
	info := s.Paths[p]
	if info != nil && info.SameAs != "" {
		return true
	}
	for q := range s.Paths {
		if q != p && under(q, p) {
			return true
		}
	}
	return false
}

func checkRoot(s *Subset, r Root, u *usage) RootReport {
	rep := RootReport{Subset: s.ID()}
	for p, info := range s.Paths {
		if len(info.Enum) > 0 {
			rep.EnumPathsInSpec++
		}
		if !isPseudo(p) {
			rep.Total++
		}
	}

	// Enums and events first: a field only an unhandled variant or event
	// carries is covered by that value's entry, not listed on its own.
	unhandled := checkEnums(s, r, u, &rep)
	skipped := checkEvents(s, r, u, &rep)

	g := buildGoPaths(s, r)
	unmappedRoot := map[string]bool{}
	impliedRoot := map[string]bool{}
	blobs := map[string]bool{}
	for _, p := range s.SortedPaths() {
		if isPseudo(p) {
			continue // a Smithy union's member list, not a JSON key
		}
		if ev, _ := splitEvent(p); ev != "" && skipped[ev] {
			rep.Implied++
			continue
		}
		if k, ok := g.kind[strings.ToLower(p)]; ok {
			if k == goOpaque && hasStructureBelow(s, p) {
				blobs[p] = true
				rep.Opaque++
			} else {
				rep.Mapped++ // a passthrough struct itself is mapped
			}
			if k != goOpaque {
				if bad := kindMismatch(s.Paths[p], g.accept[strings.ToLower(p)]); bad != "" {
					rep.Mismatched = append(rep.Mismatched, p)
					rep.mismatchWhy = append(rep.mismatchWhy, fmt.Sprintf("%s: the spec allows %s, which %s does not decode", p, bad, g.types[strings.ToLower(p)]))
				}
			}
			continue
		}
		// The nearest ancestor Go knows decides.
		cls, child := "unmapped", p
		for anc := p; ; {
			par, ok := parentPath(anc)
			if !ok {
				break // no Go ancestor at all: an unbound root
			}
			if unmappedRoot[par] || impliedRoot[par] {
				cls = "under"
				break
			}
			if k, ok := g.kind[strings.ToLower(par)]; ok {
				switch k {
				case goOpaque:
					cls = "opaque"
					blobs[par] = true
				case goPassthrough:
					cls = "passthrough"
				}
				child = anc
				break
			}
			anc, child = par, par
		}
		switch {
		case cls == "opaque":
			rep.Opaque++
		case cls == "passthrough":
			rep.Passthrough++
		case cls == "under":
			if p2 := outermost(p, impliedRoot); p2 != "" {
				rep.Implied++
			} else {
				rep.Allowlisted++
			}
		case variantImplied(s, child, unhandled):
			impliedRoot[child] = true
			rep.Implied++
		default:
			unmappedRoot[child] = true
			rep.Unmapped = append(rep.Unmapped, child)
			rep.Allowlisted++
		}
	}
	rep.Unmapped = sortedUniq(rep.Unmapped)
	for _, p := range rep.Unmapped {
		if !u.has(u.unmapped, "u"+pathKey(s.ID(), p)) {
			rep.Unaccounted = append(rep.Unaccounted, p)
		}
	}
	// Paths under an unaccounted root are not allowlisted.
	for _, p := range rep.Unaccounted {
		for q := range s.Paths {
			if under(q, p) && !isPseudo(q) {
				rep.Allowlisted--
			}
		}
	}
	for p := range blobs {
		rep.Blobs = append(rep.Blobs, p)
	}
	sort.Strings(rep.Blobs)
	for _, p := range rep.Blobs {
		if !u.has(u.opaque, "o"+pathKey(s.ID(), p)) {
			rep.Undeclared = append(rep.Undeclared, p)
		}
	}
	for i, p := range rep.Mismatched {
		if !u.has(u.kinds, "k"+pathKey(s.ID(), p)) {
			rep.MismatchUnaccounted = append(rep.MismatchUnaccounted, rep.mismatchWhy[i])
		}
	}
	return rep
}

// kindMismatch returns the spec kinds at a path that the Go type's accepted
// set lacks, or "". A timestamp is skipped (its JSON form depends on the
// protocol), and so is a path whose kinds the spec leaves unstated.
func kindMismatch(info *PathInfo, accept map[string]bool) string {
	if info == nil || accept == nil {
		return ""
	}
	var bad []string
	for _, k := range info.Kinds {
		if k != "timestamp" && !accept[k] {
			bad = append(bad, k)
		}
	}
	return strings.Join(bad, "|")
}

// isPseudo reports whether p is a Smithy union's member-list pseudo-path.
func isPseudo(p string) bool { return strings.HasSuffix(p, "."+unionMemberKey) }

// outermost returns the ancestor of p that is in roots, or "".
func outermost(p string, roots map[string]bool) string {
	for anc := p; ; {
		par, ok := parentPath(anc)
		if !ok {
			return ""
		}
		if roots[par] {
			return par
		}
		anc = par
	}
}

// variantImplied reports whether every union arm that carries p is an
// unhandled value of an enum the root checks: the field arrives only on
// variants the adapter skips, so their entries account for it.
func variantImplied(s *Subset, p string, unhandled map[string]map[string]bool) bool {
	info := s.Paths[p]
	if info == nil || len(info.Variants) == 0 {
		return false
	}
	key, _, ok := strings.Cut(info.Variants[0], "=")
	if !ok {
		return false
	}
	for anc := p; ; {
		par, ok := parentPath(anc)
		if !ok {
			return false
		}
		if vals, ok := unhandled[par+"."+key]; ok {
			for _, label := range info.Variants {
				k, v, _ := strings.Cut(label, "=")
				if k != key || !vals[v] {
					return false
				}
			}
			return true
		}
		anc = par
	}
}

// checkEnums checks r's enum paths and returns, per path, the values the
// adapter does not handle.
func checkEnums(s *Subset, r Root, u *usage, rep *RootReport) map[string]map[string]bool {
	unhandled := map[string]map[string]bool{}
	for _, e := range r.Enums {
		rep.EnumPaths++
		info := s.Paths[e.Path]
		if info == nil || len(info.Enum) == 0 {
			rep.EnumUnhandled = append(rep.EnumUnhandled, e.Path+": the subset lists no enum here (fix the EnumCheck path)")
			continue
		}
		unhandled[e.Path] = map[string]bool{}
		for _, v := range info.Enum {
			rep.EnumValues++
			if e.Handled(v) {
				// A handled value that is also allowlisted leaves its entry
				// unused, so the entry goes stale.
				continue
			}
			unhandled[e.Path][v] = true
			if u.has(u.enums, "e"+valueKey(s.ID(), e.Path, v)) {
				rep.EnumAllowlisted++
				continue
			}
			by := ""
			if e.By != "" {
				by = " (" + e.By + ")"
			}
			rep.EnumUnhandled = append(rep.EnumUnhandled, fmt.Sprintf("%s = %q%s", e.Path, v, by))
			rep.missingEnums = append(rep.missingEnums, AllowedValue{Subset: s.ID(), Path: e.Path, Value: v})
		}
	}
	return unhandled
}

// checkEvents checks the subset's stream events and returns the ones the
// adapter does not handle.
func checkEvents(s *Subset, r Root, u *usage, rep *RootReport) map[string]bool {
	skipped := map[string]bool{}
	if r.Events == nil {
		return skipped
	}
	for _, ev := range s.Events {
		rep.Events++
		if r.Events(ev) {
			continue
		}
		skipped[ev] = true
		if u.has(u.evnts, "v"+pathKey(s.ID(), ev)) {
			rep.EventsAllowlisted++
		} else {
			rep.EventsUnhandled = append(rep.EventsUnhandled, ev)
		}
	}
	return skipped
}

// writeDraft rewrites the allowlist with every current finding. Existing
// entries keep their reasons; stale ones are dropped.
func writeDraft(file, provider string, old *Allowlist, reports []RootReport, u *usage) error {
	reason := func(m map[string]int, key string, get func(int) string, def string) string {
		if i, ok := m[key]; ok && !badReason(get(i)) {
			return get(i)
		}
		return def
	}
	al := &Allowlist{Provider: provider, Note: old.Note}
	if al.Note == "" {
		al.Note = "Spec paths, blobs, enum values and events the adapter does not map, with why. Checked by TestSpecCoverage (internal/providers/contractspec)."
	}
	for _, rep := range reports {
		for _, p := range rep.Unmapped {
			k := "u" + pathKey(rep.Subset, p)
			al.Unmapped = append(al.Unmapped, AllowedPath{Subset: rep.Subset, Path: p,
				Reason: reason(u.unmapped, k, func(i int) string { return old.Unmapped[i].Reason }, "TODO: map it, or say why it is dropped (known gap <ID>: ... / no canonical field: ...)")})
		}
		for _, p := range rep.Blobs {
			k := "o" + pathKey(rep.Subset, p)
			al.Opaque = append(al.Opaque, AllowedPath{Subset: rep.Subset, Path: p,
				Reason: reason(u.opaque, k, func(i int) string { return old.Opaque[i].Reason }, "TODO: what the adapter does with this blob")})
		}
		for _, ev := range rep.EventsUnhandled {
			al.Events = append(al.Events, AllowedEvent{Subset: rep.Subset, Event: ev, Reason: "TODO: handle it, or say why it is skipped"})
		}
		for _, p := range rep.Mismatched {
			k := "k" + pathKey(rep.Subset, p)
			al.Kinds = append(al.Kinds, AllowedPath{Subset: rep.Subset, Path: p,
				Reason: reason(u.kinds, k, func(i int) string { return old.Kinds[i].Reason }, "TODO: widen the Go type, or say why the other kinds never arrive")})
		}
		for _, e := range rep.missingEnums {
			e.Reason = "TODO: handle it, or say why it falls back"
			al.Enums = append(al.Enums, e)
		}
	}
	// Keep allowlisted enum values and events that are still in use.
	for _, e := range old.Enums {
		if u.used["e"+valueKey(e.Subset, e.Path, e.Value)] {
			al.Enums = append(al.Enums, e)
		}
	}
	for _, e := range old.Events {
		if u.used["v"+pathKey(e.Subset, e.Event)] {
			al.Events = append(al.Events, e)
		}
	}
	sortAllowlist(al)
	raw, err := marshalIndent(al)
	if err != nil {
		return err
	}
	return os.WriteFile(file, raw, 0o644)
}

func sortAllowlist(al *Allowlist) {
	sort.SliceStable(al.Unmapped, func(i, j int) bool {
		return pathKey(al.Unmapped[i].Subset, al.Unmapped[i].Path) < pathKey(al.Unmapped[j].Subset, al.Unmapped[j].Path)
	})
	sort.SliceStable(al.Opaque, func(i, j int) bool {
		return pathKey(al.Opaque[i].Subset, al.Opaque[i].Path) < pathKey(al.Opaque[j].Subset, al.Opaque[j].Path)
	})
	sort.SliceStable(al.Enums, func(i, j int) bool {
		a, b := al.Enums[i], al.Enums[j]
		return valueKey(a.Subset, a.Path, a.Value) < valueKey(b.Subset, b.Path, b.Value)
	})
	sort.SliceStable(al.Kinds, func(i, j int) bool {
		return pathKey(al.Kinds[i].Subset, al.Kinds[i].Path) < pathKey(al.Kinds[j].Subset, al.Kinds[j].Path)
	})
	sort.SliceStable(al.Events, func(i, j int) bool {
		return pathKey(al.Events[i].Subset, al.Events[i].Event) < pathKey(al.Events[j].Subset, al.Events[j].Event)
	})
	if al.Unmapped == nil {
		al.Unmapped = []AllowedPath{}
	}
	if al.Opaque == nil {
		al.Opaque = []AllowedPath{}
	}
	if al.Enums == nil {
		al.Enums = []AllowedValue{}
	}
	if al.Events == nil {
		al.Events = []AllowedEvent{}
	}
}

// Set is a Handled function for a fixed set of values, for enums the
// adapter dispatches on with a switch.
func Set(values ...string) func(string) bool {
	m := toSet(values)
	return func(v string) bool { return m[v] }
}
