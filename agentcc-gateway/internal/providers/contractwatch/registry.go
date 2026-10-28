package contractwatch

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"
)

// RegistryFile is the registry's file name, in this package's directory.
const RegistryFile = "known_models.json"

// SchemaVersion is the registry format this package reads and writes.
const SchemaVersion = 1

// Status is what the registry says about one upstream model ID.
type Status string

const (
	// Covered models have contract fixtures: their model directory exists
	// under internal/providers/<fixtures.provider>/testdata/contracts/.
	Covered Status = "covered"
	// Alias models are a dated snapshot or an alias of a covered model
	// (AliasOf); the covered model's fixtures stand for them.
	Alias Status = "alias"
	// Ignored models are deliberately not under contract; Reason says why.
	Ignored Status = "ignored"
)

// List says how a provider's models are listed.
const (
	// ListAPI: the provider has a list-models API, and a Lister calls it.
	ListAPI = "api"
	// ListStatic: no list API; Section.Static holds a list a person checked
	// against the provider's docs on Static.Checked.
	ListStatic = "static"
	// ListNone: the provider cannot be listed (Azure serves deployments, not
	// models). Only the coverage check applies.
	ListNone = "none"
)

// PlaceholderReason starts the reason -update-registry gives the models it
// adds. The coverage test fails on it, so a triage edit cannot be committed
// without a real reason.
const PlaceholderReason = "TODO(triage)"

// Registry is known_models.json.
type Registry struct {
	SchemaVersion int `json:"schema_version"`
	// About is a short description for a reader of the file.
	About string `json:"about"`
	// Providers is keyed by section name: an adapter name (anthropic,
	// openai, ...) or an OpenAI-compatible preset whose fixtures live in the
	// openai suite (groq, perplexity, mistral, ...), or vertex, whose fixtures
	// live in the gemini suite.
	Providers map[string]*Section `json:"providers"`
}

// Section is one provider's models.
type Section struct {
	List string `json:"list"`
	// Note explains anything a reader needs: why a provider is unlisted,
	// where its static list comes from.
	Note string `json:"note,omitempty"`
	// Fixtures says where covered models' fixtures live. Default: the
	// section's own adapter directory, no prefix.
	Fixtures *FixtureRef `json:"fixtures,omitempty"`
	// Static is the hand-checked model list of a ListStatic section.
	Static *StaticList `json:"static,omitempty"`
	// IgnoreRules ignore every listed ID that matches, unless the ID has an
	// explicit entry (which must not also match a rule).
	IgnoreRules []Rule  `json:"ignore_rules,omitempty"`
	Models      []Entry `json:"models"`
}

// FixtureRef locates a section's fixtures: the model directory of covered
// model M is internal/providers/<Provider>/testdata/contracts/
// <contracttest.ModelDir(Prefix + M)>. The openai suite files the Groq
// preset's models as "groq@<model>", so the groq section has
// {"provider": "openai", "prefix": "groq@"}.
type FixtureRef struct {
	Provider string `json:"provider"`
	Prefix   string `json:"prefix,omitempty"`
}

// StaticList is a provider's model list, copied from its docs by a person.
type StaticList struct {
	// Checked is the date (YYYY-MM-DD) someone last compared Models with
	// Source.
	Checked string `json:"checked"`
	// Source is the documentation page Models was copied from.
	Source string   `json:"source"`
	Models []string `json:"models"`
}

// Rule ignores every ID matching Match: a glob where "*" matches any run of
// characters (including "/" and ":") and "?" any one character.
type Rule struct {
	Match  string `json:"match"`
	Reason string `json:"reason"`
}

// Entry is one model ID's status.
type Entry struct {
	ID     string `json:"id"`
	Status Status `json:"status"`
	// AliasOf is the covered model an alias stands for.
	AliasOf string `json:"alias_of,omitempty"`
	// Reason is mandatory for ignored entries.
	Reason string `json:"reason,omitempty"`
	// Note is free text.
	Note string `json:"note,omitempty"`
}

// FixtureProvider is the adapter directory that holds the section's fixtures.
func (s *Section) FixtureProvider(name string) string {
	if s.Fixtures != nil && s.Fixtures.Provider != "" {
		return s.Fixtures.Provider
	}
	return name
}

// FixtureKey is the model key the fixtures and the recorder use for id: the
// section's prefix plus id ("groq@openai/gpt-oss-120b").
func (s *Section) FixtureKey(id string) string {
	if s.Fixtures != nil {
		return s.Fixtures.Prefix + id
	}
	return id
}

// Entry returns id's explicit entry.
func (s *Section) Entry(id string) (Entry, bool) {
	for _, e := range s.Models {
		if e.ID == id {
			return e, true
		}
	}
	return Entry{}, false
}

// MatchRule returns the first ignore rule matching id.
func (s *Section) MatchRule(id string) (Rule, bool) {
	for _, r := range s.IgnoreRules {
		if globMatch(r.Match, id) {
			return r, true
		}
	}
	return Rule{}, false
}

// Covered returns the IDs of the section's covered entries, sorted.
func (s *Section) Covered() []string {
	var ids []string
	for _, e := range s.Models {
		if e.Status == Covered {
			ids = append(ids, e.ID)
		}
	}
	sort.Strings(ids)
	return ids
}

// Names returns the registry's section names, sorted.
func (r *Registry) Names() []string {
	names := make([]string, 0, len(r.Providers))
	for n := range r.Providers {
		names = append(names, n)
	}
	sort.Strings(names)
	return names
}

// LoadRegistry reads and validates a registry file.
func LoadRegistry(path string) (*Registry, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	r, err := ParseRegistry(raw)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return r, nil
}

// ParseRegistry decodes a registry strictly (an unknown key is an error, so
// a typo cannot silently drop a rule) and validates it.
func ParseRegistry(raw []byte) (*Registry, error) {
	r := &Registry{}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(r); err != nil {
		return nil, err
	}
	if dec.More() {
		return nil, errors.New("trailing data after the registry object")
	}
	if err := r.Validate(); err != nil {
		return nil, err
	}
	return r, nil
}

// idPattern is what a model ID may look like. Upstream IDs flow into file
// names, shell arguments and issue titles, so anything else is refused.
var idPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,199}$`)

// ValidID reports whether id is a model ID this package accepts.
func ValidID(id string) bool { return idPattern.MatchString(id) }

var datePattern = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

// Validate checks the registry's internal consistency: every section is one
// this package knows how to list, every model ID has exactly one status,
// ignored entries and rules have a reason, and aliases point at covered
// models. It does not look at the file system; see CheckCoverage.
func (r *Registry) Validate() error {
	var errs []string
	add := func(format string, args ...any) { errs = append(errs, fmt.Sprintf(format, args...)) }

	if r.SchemaVersion != SchemaVersion {
		add("schema_version is %d, want %d", r.SchemaVersion, SchemaVersion)
	}
	if len(r.Providers) == 0 {
		add("providers is empty")
	}
	for _, name := range r.Names() {
		s := r.Providers[name]
		where := "providers." + name
		if s == nil {
			add("%s: null section", where)
			continue
		}
		spec, known := Specs[name]
		if !known {
			add("%s: unknown provider (known: %s)", where, strings.Join(SpecNames(), ", "))
		}
		switch s.List {
		case ListAPI:
			if known && spec.NewLister == nil {
				add("%s: list is %q, but model-watch has no lister for it; use %q or %q", where, ListAPI, ListStatic, ListNone)
			}
			if s.Static != nil {
				add("%s: static is only for list %q", where, ListStatic)
			}
		case ListStatic:
			switch s.Static {
			case nil:
				add("%s: list %q needs a static block", where, ListStatic)
			default:
				if !datePattern.MatchString(s.Static.Checked) {
					add("%s.static.checked: want YYYY-MM-DD, got %q", where, s.Static.Checked)
				}
				if !strings.HasPrefix(s.Static.Source, "https://") {
					add("%s.static.source: want the https:// documentation page the list was copied from", where)
				}
				if len(s.Static.Models) == 0 {
					add("%s.static.models is empty", where)
				}
				seen := map[string]bool{}
				for _, id := range s.Static.Models {
					if !ValidID(id) {
						add("%s.static.models: invalid model ID %q", where, id)
					}
					if seen[id] {
						add("%s.static.models: %q listed twice", where, id)
					}
					seen[id] = true
				}
			}
			if strings.TrimSpace(s.Note) == "" {
				add("%s: a static list needs a note saying why the provider cannot be listed", where)
			}
		case ListNone:
			if strings.TrimSpace(s.Note) == "" {
				add("%s: list %q needs a note saying why the provider cannot be listed", where, ListNone)
			}
			if s.Static != nil {
				add("%s: static is only for list %q", where, ListStatic)
			}
			if len(s.IgnoreRules) > 0 {
				add("%s: ignore_rules mean nothing for a provider that is never listed", where)
			}
		default:
			add("%s: list is %q, want %q, %q or %q", where, s.List, ListAPI, ListStatic, ListNone)
		}
		if s.Fixtures != nil {
			if s.Fixtures.Provider == "" {
				add("%s.fixtures.provider is empty", where)
			}
			if s.Fixtures.Prefix != "" && !strings.HasSuffix(s.Fixtures.Prefix, "@") {
				add("%s.fixtures.prefix %q: the openai suite's preset prefix is \"<preset>@\"", where, s.Fixtures.Prefix)
			}
		}

		for i, rule := range s.IgnoreRules {
			at := fmt.Sprintf("%s.ignore_rules[%d]", where, i)
			if rule.Match == "" || strings.Trim(rule.Match, "*?") == "" {
				add("%s: match %q would ignore everything", at, rule.Match)
			}
			if strings.TrimSpace(rule.Reason) == "" {
				add("%s (%s): a rule needs a reason", at, rule.Match)
			}
		}

		byID := map[string]Entry{}
		for _, e := range s.Models {
			at := where + ".models[" + e.ID + "]"
			if !ValidID(e.ID) {
				add("%s: invalid model ID %q", where, e.ID)
			}
			if _, dup := byID[e.ID]; dup {
				add("%s: listed twice; a model ID has exactly one status", at)
			}
			byID[e.ID] = e
			if rule, ok := s.MatchRule(e.ID); ok {
				add("%s: also matches ignore rule %q; an ID has exactly one status, so narrow the rule or drop the entry", at, rule.Match)
			}
		}
		for _, e := range s.Models {
			at := where + ".models[" + e.ID + "]"
			switch e.Status {
			case Covered:
				if e.AliasOf != "" {
					add("%s: alias_of is only for status %q", at, Alias)
				}
				if s.List == ListStatic && s.Static != nil && !contains(s.Static.Models, e.ID) {
					add("%s: covered, but not in the static list", at)
				}
			case Alias:
				target, ok := byID[e.AliasOf]
				switch {
				case e.AliasOf == "":
					add("%s: an alias needs alias_of", at)
				case !ok:
					add("%s: alias_of %q has no entry", at, e.AliasOf)
				case target.Status != Covered:
					add("%s: alias_of %q is %s; an alias must point at a covered model", at, e.AliasOf, target.Status)
				}
			case Ignored:
				if strings.TrimSpace(e.Reason) == "" {
					add("%s: an ignored model needs a reason", at)
				}
				if e.AliasOf != "" {
					add("%s: alias_of is only for status %q", at, Alias)
				}
			default:
				add("%s: status %q, want %q, %q or %q", at, e.Status, Covered, Alias, Ignored)
			}
			if s.List == ListNone && e.Status != Covered {
				add("%s: a provider that is never listed only needs its covered models", at)
			}
		}
	}
	if len(errs) == 0 {
		return nil
	}
	sort.Strings(errs)
	return errors.New("invalid registry:\n  " + strings.Join(errs, "\n  "))
}

// Placeholders lists the entries that still carry -update-registry's
// placeholder reason, as "section/id".
func (r *Registry) Placeholders() []string {
	var out []string
	for _, name := range r.Names() {
		for _, e := range r.Providers[name].Models {
			if strings.HasPrefix(e.Reason, PlaceholderReason) {
				out = append(out, name+"/"+e.ID)
			}
		}
	}
	return out
}

// AddIgnored adds id to section as ignored with reason, keeping the models
// sorted. It is how -update-registry records NEW models for triage.
func (r *Registry) AddIgnored(section, id, reason string) error {
	s := r.Providers[section]
	if s == nil {
		return fmt.Errorf("no section %q", section)
	}
	if _, ok := s.Entry(id); ok {
		return fmt.Errorf("%s/%s already has an entry", section, id)
	}
	s.Models = append(s.Models, Entry{ID: id, Status: Ignored, Reason: reason})
	sortEntries(s.Models)
	return nil
}

func sortEntries(es []Entry) {
	sort.SliceStable(es, func(i, j int) bool { return es[i].ID < es[j].ID })
}

// Encode returns the registry in its canonical form: two-space indent, keys
// in a fixed order, models sorted by ID, one model or rule per line, no HTML
// escaping, a trailing newline. The committed file must be in this form (a
// test checks), so -update-registry's diff is only what it changed.
func (r *Registry) Encode() []byte {
	var b bytes.Buffer
	b.WriteString("{\n")
	fmt.Fprintf(&b, "  \"schema_version\": %d,\n", r.SchemaVersion)
	fmt.Fprintf(&b, "  \"about\": %s,\n", str(r.About))
	b.WriteString("  \"providers\": {")
	for i, name := range r.Names() {
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, "\n    %s: ", str(name))
		r.Providers[name].encode(&b)
	}
	b.WriteString("\n  }\n}\n")
	return b.Bytes()
}

func (s *Section) encode(b *bytes.Buffer) {
	var fields []string
	fields = append(fields, `"list": `+str(s.List))
	if s.Note != "" {
		fields = append(fields, `"note": `+str(s.Note))
	}
	if s.Fixtures != nil {
		fields = append(fields, `"fixtures": `+inline(kv{"provider", s.Fixtures.Provider}, kv{"prefix", s.Fixtures.Prefix}))
	}
	if s.Static != nil {
		var models []string
		for _, m := range s.Static.Models {
			models = append(models, "\n          "+str(m))
		}
		fields = append(fields, "\"static\": {\n"+
			"        \"checked\": "+str(s.Static.Checked)+",\n"+
			"        \"source\": "+str(s.Static.Source)+",\n"+
			"        \"models\": ["+strings.Join(models, ",")+"\n        ]\n      }")
	}
	if len(s.IgnoreRules) > 0 {
		var rules []string
		for _, r := range s.IgnoreRules {
			rules = append(rules, "\n        "+inline(kv{"match", r.Match}, kv{"reason", r.Reason}))
		}
		fields = append(fields, `"ignore_rules": [`+strings.Join(rules, ",")+"\n      ]")
	}
	models := append([]Entry(nil), s.Models...)
	sortEntries(models)
	var lines []string
	for _, e := range models {
		lines = append(lines, "\n        "+inline(kv{"id", e.ID}, kv{"status", string(e.Status)},
			kv{"alias_of", e.AliasOf}, kv{"reason", e.Reason}, kv{"note", e.Note}))
	}
	if len(lines) == 0 {
		fields = append(fields, `"models": []`)
	} else {
		fields = append(fields, `"models": [`+strings.Join(lines, ",")+"\n      ]")
	}
	b.WriteString("{\n      " + strings.Join(fields, ",\n      ") + "\n    }")
}

type kv struct{ k, v string }

// inline writes {"k": "v", ...} on one line, leaving out empty values.
func inline(pairs ...kv) string {
	var parts []string
	for _, p := range pairs {
		if p.v != "" {
			parts = append(parts, str(p.k)+": "+str(p.v))
		}
	}
	return "{" + strings.Join(parts, ", ") + "}"
}

// str is s as a JSON string, without HTML escaping.
func str(s string) string {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(s)
	return strings.TrimSuffix(b.String(), "\n")
}

// globMatch reports whether s matches pattern, where "*" matches any run of
// characters (including none) and "?" exactly one; every other character is
// literal. Unlike path.Match, "*" crosses "/" (Groq IDs are "vendor/model").
func globMatch(pattern, s string) bool {
	p, i := 0, 0
	star, mark := -1, 0
	for i < len(s) {
		switch {
		case p < len(pattern) && (pattern[p] == '?' || pattern[p] == s[i]):
			p++
			i++
		case p < len(pattern) && pattern[p] == '*':
			star, mark = p, i
			p++
		case star >= 0:
			p = star + 1
			mark++
			i = mark
		default:
			return false
		}
	}
	for p < len(pattern) && pattern[p] == '*' {
		p++
	}
	return p == len(pattern)
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}
