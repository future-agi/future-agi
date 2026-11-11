package contractspec

import (
	"strings"
)

// EnumDecl is a value set a contract test declares for the values it
// accepts at some paths of a fixture (contracttest.EnumSpec: Spec, Paths,
// Values). CheckDeclaredEnums compares it with the spec.
type EnumDecl struct {
	Name   string
	Paths  []string
	Values []string
}

// EnumRouting says which subset holds a declared path: Body for "$..."
// paths, Stream for "<event>:$..." paths (and "*:$.type", the event names),
// Error for "error:$..." paths (contracttest.ErrorRoot), which the subset
// holds as "$...". An empty subset skips those paths.
type EnumRouting struct {
	Body, Stream, Error string
}

// CheckDeclaredEnums fails for every value the spec lists at a declared path
// that the declaration lacks: a response carrying it would fail the contract
// test, so the declaration should know it first. Declared values the spec
// does not list (events it omits, like Anthropic's ping) and declared paths
// it does not have are logged, not failed. Open enums (model IDs) are not
// compared.
func CheckDeclaredEnums(t TB, routing EnumRouting, decls []EnumDecl) {
	t.Helper()
	r := &enumResolver{t: t, cache: map[string]*Subset{}}
	compared := 0
	for _, d := range decls {
		for _, p := range d.Paths {
			var spec []string
			var where string
			switch p {
			case "*:$.type":
				s := r.load(routing.Stream)
				if s == nil {
					continue
				}
				spec, where = s.Events, s.ID()+" events"
			default:
				id, sp := routing.Body, p
				if rest, ok := strings.CutPrefix(p, "error:$"); ok {
					id, sp = routing.Error, "$"+rest
				} else if ev, _ := splitEvent(p); ev != "" {
					id = routing.Stream
				}
				if id == "" {
					continue
				}
				vals, open, found := r.enum(id, sp)
				if !found {
					t.Logf("declared enum %s: %s has no enum at %s (not compared)", d.Name, id, sp)
					continue
				}
				if open {
					continue
				}
				spec, where = vals, id+" "+sp
			}
			compared++
			missing, extra := setDiff(d.Values, spec)
			if len(missing) > 0 {
				t.Errorf("declared enum %s at %s lacks values the spec lists at %s: %s\nAdd them to the declaration (and handle them in the adapter, or allowlist them).",
					d.Name, p, where, strings.Join(missing, ", "))
			}
			if len(extra) > 0 {
				t.Logf("declared enum %s at %s has values the spec does not list at %s: %s", d.Name, p, where, strings.Join(extra, ", "))
			}
		}
	}
	t.Logf("declared enums: %d paths compared with the spec", compared)
}

// enumResolver reads enum values from subsets, following same_as nodes into
// the subset that flattens them and "*:" wildcards across every event.
type enumResolver struct {
	t     TB
	cache map[string]*Subset
}

func (r *enumResolver) load(id string) *Subset {
	if id == "" {
		return nil
	}
	if s, ok := r.cache[id]; ok {
		return s
	}
	s, err := LoadSubset(id)
	if err != nil {
		r.t.Fatalf("%v", err)
	}
	r.cache[id] = s
	return s
}

// enum returns the values at path in subset id. A "*:" path unions every
// event that has it.
func (r *enumResolver) enum(id, path string) (vals []string, open, found bool) {
	s := r.load(id)
	if s == nil {
		return nil, false, false
	}
	if rest, ok := strings.CutPrefix(path, "*:"); ok {
		for _, ev := range s.Events {
			v, o, f := r.enum(id, ev+":"+rest)
			if f {
				vals, open, found = append(vals, v...), open || o, true
			}
		}
		return sortedUniq(vals), open, found
	}
	if info := s.Paths[path]; info != nil {
		return info.Enum, info.OpenEnum, len(info.Enum) > 0
	}
	// Below a same_as node, the values are in the subset that flattens it.
	for anc := path; ; {
		par, ok := parentPath(anc)
		if !ok {
			return nil, false, false
		}
		if info := s.Paths[par]; info != nil && info.SameAs != "" {
			other, base, ok := strings.Cut(info.SameAs, " ")
			if !ok {
				return nil, false, false
			}
			return r.enum(other, base+path[len(par):])
		}
		anc = par
	}
}
