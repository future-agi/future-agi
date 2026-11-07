package contracttest

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
)

// Strict is a provider's strict-mode declaration: how its adapter decodes
// upstream bodies, and what the provider documents beyond what the Go
// structs can say. Declare one per provider, in its contract test:
//
//	var contractStrict = &contracttest.Strict{
//		Docs:    contractDocs,     // body -> documents + the Go type each decodes into
//		Enums:   ContractEnums,    // documented value sets (exported: the spec-diff check reads them)
//		Decoded: []contracttest.Decoded{...}, // raw regions the adapter decodes later
//		Free:    []string{"$.content[].input"}, // arbitrary by design (tool input)
//	}
//
// then call contractStrict.Check(t, c) once per case, in place of
// AssertKnownPaths, and contractStrict.AssertMutationsDetected to prove the
// checks fire. A Strict is safe for concurrent use once its fields are set.
//
// Path syntax everywhere is UnknownFields': "$.a.b", "$.list[].x",
// "$.map.*.x", prefixed with the document's root for a stream event
// ("message_delta:$.delta.stop_reason"). In Enums, Decoded, Watched and Free,
// a "*:" prefix matches every event root: "*:$.type".
type Strict struct {
	// Docs splits a case's upstream body into the JSON documents the adapter
	// decodes and names the Go type each is decoded into. For a stream case
	// it returns one Doc per event, in order, as Codec (default SSE) splits
	// the body; otherwise one Doc for the whole body, or none for a body that
	// is not JSON by design. Root an error body at ErrorRoot.
	Docs func(c Case) []Doc
	// Enums are the documented value sets of enum-like fields. Every value
	// found at a path, anywhere in the body, modeled or not, must be in the
	// set. Null is not a value.
	Enums []EnumSpec
	// Decoded are opaque regions (json.RawMessage, any) the adapter decodes
	// later with a type of its own; the walk follows that type.
	Decoded []Decoded
	// Watched are opaque regions whose keys are known although no Go type
	// models them; a new key there is an unknown field.
	Watched []Watched
	// Free are regions that are arbitrary by design (tool-call arguments,
	// tool input): neither reported as opaque nor mutated nor shape-diffed.
	Free []string
	// Ignore are unknown fields that carry nothing by protocol for every
	// model (path -> reason), e.g. a stop_reason that is always null at
	// message_start. They are filtered from Report.Unknown. Model-specific
	// gaps belong in meta.json instead.
	Ignore map[string]string
	// Codec splits and joins stream bodies for the mutation self-test; nil
	// means SSE.
	Codec *Codec
	// MaxMutations caps the mutations per case, sampled evenly; 0 means 400.
	MaxMutations int

	once sync.Once
	idx  *strictIndex
}

// ErrorRoot is the path root for an upstream error body. Rooting HTTP error
// bodies and mid-stream error events at the same root gives them one set of
// paths, allowlist entries and enums ("error:$.error.type").
const ErrorRoot = "error:$"

// Doc is one JSON document of an upstream body.
type Doc struct {
	// Root prefixes its paths: "$" for a whole body, "<event>:$" for a
	// stream event, ErrorRoot for an error body.
	Root string
	// Data is the document's JSON.
	Data []byte
	// Type is what the adapter decodes Data into. nil means the adapter does
	// not decode it: the whole document is opaque, unless declared. A Doc
	// whose Data is not JSON is skipped when Type is nil (a "[DONE]"
	// sentinel) and reported as unknown at Root otherwise.
	Type reflect.Type
}

// EnumSpec is one documented value set.
type EnumSpec struct {
	// Spec names where the values come from: the provider's spec schema
	// (e.g. "StopReason") or doc page. The spec-diff check compares Values
	// with it.
	Spec string
	// Paths are where the field appears.
	Paths []string
	// Values are the documented values.
	Values []string
}

// Decoded is an opaque region the adapter decodes later into its own type:
// Type, or for a tagged union, the type the value of key Tag selects. An
// occurrence whose tag has no type stays opaque and is reported so.
type Decoded struct {
	Path  string
	Type  reflect.Type
	Tag   string
	Types map[string]reflect.Type
}

// Watched is an opaque region whose keys are known: an object at Path whose
// keys must be among Keys. Values stay unwatched.
type Watched struct {
	Path string
	Keys []string
}

type enumIdx struct {
	spec   *EnumSpec
	values map[string]bool
}

func (e *enumIdx) name() string {
	if e.spec.Spec != "" {
		return e.spec.Spec
	}
	return "the enum"
}

// strictIndex is a Strict's declarations by path.
type strictIndex struct {
	err      error
	enums    map[string][]*enumIdx
	decoded  map[string]*Decoded
	watched  map[string]map[string]bool
	free     map[string]bool
	freeWild map[string]bool
	ignore   map[string]string
}

func (s *Strict) index() *strictIndex {
	s.once.Do(func() { s.idx = buildIndex(s) })
	return s.idx
}

func buildIndex(s *Strict) *strictIndex {
	x := &strictIndex{
		enums:    map[string][]*enumIdx{},
		decoded:  map[string]*Decoded{},
		watched:  map[string]map[string]bool{},
		free:     map[string]bool{},
		freeWild: map[string]bool{},
		ignore:   s.Ignore,
	}
	fail := func(format string, a ...any) {
		if x.err == nil {
			x.err = fmt.Errorf(format, a...)
		}
	}
	for i := range s.Enums {
		e := &s.Enums[i]
		if len(e.Paths) == 0 || len(e.Values) == 0 {
			fail("Enums[%d] (%s) needs paths and values", i, e.Spec)
		}
		ix := &enumIdx{spec: e, values: map[string]bool{}}
		for _, v := range e.Values {
			ix.values[v] = true
		}
		for _, p := range e.Paths {
			if !strings.Contains(p, "$") {
				fail("Enums[%d] path %q is not a JSON path", i, p)
			}
			x.enums[p] = append(x.enums[p], ix)
		}
	}
	seen := map[string]string{}
	declare := func(kind, p string) {
		if !strings.Contains(p, "$") {
			fail("%s path %q is not a JSON path", kind, p)
		}
		if prev, dup := seen[p]; dup {
			fail("%s path %q is already declared %s", kind, p, prev)
		}
		seen[p] = kind
	}
	for i := range s.Decoded {
		d := &s.Decoded[i]
		declare("Decoded", d.Path)
		if (d.Type == nil) == (d.Tag == "") || (d.Tag != "") != (len(d.Types) > 0) {
			fail("Decoded %q needs Type, or Tag and Types", d.Path)
		}
		x.decoded[d.Path] = d
	}
	for _, wt := range s.Watched {
		declare("Watched", wt.Path)
		if len(wt.Keys) == 0 {
			fail("Watched %q has no keys", wt.Path)
		}
		keys := map[string]bool{}
		for _, k := range wt.Keys {
			keys[k] = true
		}
		x.watched[wt.Path] = keys
	}
	for _, p := range s.Free {
		declare("Free", p)
		if strings.HasPrefix(p, "*:") {
			x.freeWild[p] = true
		} else {
			x.free[p] = true
		}
	}
	return x
}

// wild is path's "*:" spelling when it has an event root, else "".
func wild(path string) string {
	if i := strings.Index(path, ":$"); i > 0 {
		return "*" + path[i:]
	}
	return ""
}

func (x *strictIndex) match(set map[string]bool, path string) bool {
	w := wild(path)
	return w != "" && set[w]
}

func (x *strictIndex) enumsAt(path string) []*enumIdx {
	out := x.enums[path]
	if w := wild(path); w != "" {
		out = append(out[:len(out):len(out)], x.enums[w]...)
	}
	return out
}

func (x *strictIndex) decodedAt(path string) *Decoded {
	if d := x.decoded[path]; d != nil {
		return d
	}
	if w := wild(path); w != "" {
		return x.decoded[w]
	}
	return nil
}

func (x *strictIndex) watchedAt(path string) map[string]bool {
	if k := x.watched[path]; k != nil {
		return k
	}
	if w := wild(path); w != "" {
		return x.watched[w]
	}
	return nil
}

func (x *strictIndex) declaredAt(path string) bool {
	return x.decodedAt(path) != nil || x.watchedAt(path) != nil || x.free[path] || x.match(x.freeWild, path)
}

// Report walks c's upstream body (s.Docs) and returns what the strict checks
// look at, without asserting anything.
func (s *Strict) Report(c Case) (Report, error) {
	x := s.index()
	if x.err != nil {
		return Report{}, fmt.Errorf("contracttest: Strict: %v", x.err)
	}
	w := s.newWalker()
	stream := c.IsStream && !c.IsError()
	for i, d := range s.Docs(c) {
		idx := -1
		if stream {
			idx = i
		}
		w.walkDoc(d, idx)
	}
	return w.report(), nil
}

// Check runs the strict checks on case c and returns the report. Call it
// exactly once per case, instead of AssertKnownPaths (it calls that). It
// fails t for:
//
//   - unknown fields not in meta.json's allowed_unknown_fields;
//   - type mismatches not in allowed_type_mismatches;
//   - json.Unmarshal errors no mismatch explains;
//   - values outside an EnumSpec;
//   - shape drift: for a live case, against the committed fixture of the
//     same case; for a model with cloned_from, against the same case of
//     that model. Paths that disappear or change type fail unless listed in
//     optional_fields; new paths the structs model are logged (new paths
//     they do not are unknown fields already).
//
// Opaque regions are logged and collected into the provider's
// OpaqueGoldenFile once every case has run.
func (s *Strict) Check(t testing.TB, c Case) Report {
	t.Helper()
	rep, err := s.Report(c)
	if err != nil {
		t.Fatalf("%v", err)
	}

	AssertKnownPaths(t, c, rep.Unknown)

	allowed := map[string]bool{}
	for _, f := range c.Meta.AllowedTypeMismatches {
		allowed[f.Path] = true
	}
	var used, bad []string
	for _, m := range rep.Mismatches {
		if allowed[m.Path] {
			used = append(used, m.Path)
		} else {
			bad = append(bad, m.String())
		}
	}
	c.Meta.note(auditTypes, c.Name, used)
	if len(bad) > 0 {
		t.Errorf("upstream response has values whose JSON type the adapter's structs do not accept "+
			"(json.Unmarshal fails on them, and an adapter that swallows the error loses them):\n  %s\n"+
			"Fix the struct, or allowlist each path in %s/%s under allowed_type_mismatches with a reason.",
			strings.Join(bad, "\n  "), c.Meta.dir, MetaFile)
	}
	if len(rep.DecodeErrors) > 0 {
		t.Errorf("json.Unmarshal into the adapter's type fails, and the type walk found no mismatch to explain it:\n  %s",
			strings.Join(rep.DecodeErrors, "\n  "))
	}
	if len(rep.EnumViolations) > 0 {
		t.Errorf("upstream response has values outside the provider's documented sets:\n  %s\n"+
			"If the provider now documents the value, add it to the EnumSpec (and check the adapter handles it).",
			strings.Join(rep.EnumViolations, "\n  "))
	}

	if c.mutant {
		return rep
	}
	if len(rep.Opaque) > 0 {
		t.Logf("opaque regions (not watched by the adapter's structs): %s", strings.Join(rep.Opaque, ", "))
	}
	if c.Meta.suite != nil {
		c.Meta.suite.noteStrict(s, c.ID(), rep)
	}
	if c.Live {
		s.diffAgainst(t, c, rep, c.Model, "the committed fixture of this case")
	}
	if from := c.Meta.ClonedFrom; from != "" {
		s.diffAgainst(t, c, rep, from, fmt.Sprintf("the same case of %s, which this model was cloned from", from))
	}
	return rep
}

// diffAgainst diffs rep's shape with the repo's case c.Name of model.
func (s *Strict) diffAgainst(t testing.TB, c Case, rep Report, model, what string) {
	t.Helper()
	dir := filepath.Join(DefaultRoot, ModelDir(model), c.Name)
	if _, err := os.Stat(dir); err != nil {
		t.Logf("contracttest: no %s at %s; shape diff skipped", what, dir)
		return
	}
	modelDir := filepath.Join(DefaultRoot, ModelDir(model))
	raw, err := os.ReadFile(filepath.Join(modelDir, MetaFile))
	var meta *Meta
	if err == nil {
		meta, err = decodeMeta(raw, c.Meta.Provider, model)
	}
	if err != nil {
		t.Errorf("contracttest: reading the meta.json of %s for the shape diff: %v", what, err)
		return
	}
	meta.dir = modelDir
	base, err := readCase(meta, model, c.Name, dir, "", false)
	if err != nil {
		t.Errorf("contracttest: reading %s for the shape diff: %v", what, err)
		return
	}
	if base.IsStream != c.IsStream || base.IsError() != c.IsError() || base.Endpoint != c.Endpoint {
		t.Errorf("contracttest: %s is a different kind of case (stream %v, status %d, endpoint %s); shapes cannot be compared",
			what, base.IsStream, base.Status, base.Endpoint)
		return
	}
	baseRep, err := s.Report(base)
	if err != nil {
		t.Fatalf("%v", err)
	}
	if meta.Source == SourceSynthetic {
		what += " (synthetic: if the new capture is right, promote it)"
	}
	assertShapeStable(t, c, baseRep.Shape, rep, what)
}

// ShapeDelta is how one shape differs from another.
type ShapeDelta struct {
	// Added are paths only the new shape has.
	Added []string
	// Removed are paths only the base shape has.
	Removed []string
	// TypeChanged are paths both have with different kinds. Null is ignored
	// next to another kind (a nullable field that is sometimes set), but a
	// path that became null-only, or stopped being null-only, has changed.
	TypeChanged []string
}

// Empty reports whether the shapes are the same.
func (d ShapeDelta) Empty() bool {
	return len(d.Added) == 0 && len(d.Removed) == 0 && len(d.TypeChanged) == 0
}

// ShapeDiff compares two shapes path by path.
func ShapeDiff(base, cur Shape) ShapeDelta {
	var d ShapeDelta
	for p, bk := range base {
		ck, ok := cur[p]
		if !ok {
			d.Removed = append(d.Removed, p)
			continue
		}
		if nonNull(bk) != nonNull(ck) {
			d.TypeChanged = append(d.TypeChanged, p)
		}
	}
	for p := range cur {
		if _, ok := base[p]; !ok {
			d.Added = append(d.Added, p)
		}
	}
	sort.Strings(d.Added)
	sort.Strings(d.Removed)
	sort.Strings(d.TypeChanged)
	return d
}

// nonNull is a kind set without null, unless null is all there is.
func nonNull(k Kinds) Kinds {
	if k&^KindNull != 0 {
		return k &^ KindNull
	}
	return k
}

// assertShapeStable diffs cur's shape against base (a shape of the same case
// from another capture, described by what) and fails t for every removed or
// retyped path that c's meta.json does not list under optional_fields. Added
// paths the adapter's structs model are logged; added paths they do not
// model are cur's unknown fields, which AssertKnownPaths reports.
func assertShapeStable(t testing.TB, c Case, base Shape, cur Report, what string) {
	t.Helper()
	d := ShapeDiff(base, cur.Shape)
	optional := func(p string) bool {
		for _, o := range c.Meta.OptionalFields {
			if p == o.Path || strings.HasPrefix(p, o.Path+".") || strings.HasPrefix(p, o.Path+"[") {
				return true
			}
		}
		return false
	}
	var bad []string
	for _, p := range d.Removed {
		if !optional(p) {
			bad = append(bad, fmt.Sprintf("%s: removed (was %s)", p, base[p]))
		}
	}
	for _, p := range d.TypeChanged {
		if !optional(p) {
			bad = append(bad, fmt.Sprintf("%s: %s, was %s", p, cur.Shape[p], base[p]))
		}
	}
	bad = collapse(bad)
	if len(bad) > 0 {
		t.Errorf("response shape differs from %s:\n  %s\n"+
			"If the difference is expected (a field the model sends only sometimes), list the path in %s/%s "+
			"under optional_fields with a reason.", what, strings.Join(bad, "\n  "), c.Meta.dir, MetaFile)
	}
	var added []string
	for _, p := range d.Added {
		if !underAny(p, cur.Unknown) {
			added = append(added, p)
		}
	}
	if len(added) > 0 {
		t.Logf("response has paths %s did not, all modeled by the adapter's structs:\n  %s", what, strings.Join(collapse(added), "\n  "))
	}
}

// underAny reports whether p is one of roots or below one.
func underAny(p string, roots []string) bool {
	for _, r := range roots {
		if p == r || strings.HasPrefix(p, r+".") || strings.HasPrefix(p, r+"[") {
			return true
		}
	}
	return false
}

// collapse drops entries ("<path>: <detail>") for paths below another
// listed path: a removed object implies its removed children.
func collapse(lines []string) []string {
	pathOf := func(l string) string { p, _, _ := strings.Cut(l, ": "); return p }
	sort.Slice(lines, func(i, j int) bool { return pathOf(lines[i]) < pathOf(lines[j]) })
	var out, roots []string
	for _, l := range lines {
		p := pathOf(l)
		if underAny(p, roots) {
			continue
		}
		roots = append(roots, p)
		out = append(out, l)
	}
	return out
}
