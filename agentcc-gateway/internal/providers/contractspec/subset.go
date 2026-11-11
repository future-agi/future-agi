package contractspec

import (
	"bytes"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// SchemaVersion is the version of the subset file format. Bump it when the
// meaning of a field changes, so a committed subset written by an older tool
// is rejected instead of misread.
const SchemaVersion = 1

// PathInfo is everything a subset records about one JSON path.
type PathInfo struct {
	// Kinds are the JSON kinds the spec allows here: object, array, map,
	// string, integer, number, boolean, any. Format-specific scalars keep
	// their spec name: "string(int64)" (discovery: a 64-bit integer sent as a
	// JSON string), "blob" and "timestamp" (Smithy).
	Kinds []string `json:"kinds,omitempty"`
	// Enum is every value the spec lists for this path (enum, const, or a
	// discriminator's values), sorted.
	Enum []string `json:"enum,omitempty"`
	// OpenEnum marks an enum that also accepts any other string (a union of
	// known values and a plain string, as for model IDs). Growth is
	// informational, not drift.
	OpenEnum bool `json:"open_enum,omitempty"`
	// Nullable is set when null is allowed.
	Nullable bool `json:"nullable,omitempty"`
	// Opaque marks a node the spec leaves free-form: any JSON, a free-form
	// object, a Smithy document. Nothing below it is listed.
	Opaque bool `json:"opaque,omitempty"`
	// Deprecated is set when the spec marks the field deprecated.
	Deprecated bool `json:"deprecated,omitempty"`
	// Recursive names the schema at which the walk stopped because it was
	// already on the path (a cycle), or "max-depth".
	Recursive string `json:"recursive,omitempty"`
	// SameAs marks a node whose schema is flattened in another root and is
	// not repeated here ("openai/responses $": a stream event that carries
	// the whole Response object). Its subtree is that root's.
	SameAs string `json:"same_as,omitempty"`
	// Pruned says why nothing below this path is listed: the definition
	// prunes it (a request parameter echoed back, which the gateway never
	// reads). Changes below a pruned path are not drift.
	Pruned string `json:"pruned,omitempty"`
	// Variants are the union arms (discriminator values, or Smithy union
	// members) that contribute this path, as "type=thinking". Empty when the
	// path is not inside a union.
	Variants []string `json:"variants,omitempty"`
}

// Subset is the flattened contract of one root: the paths, kinds, enum
// values and stream events of one response (or stream, or error body) the
// gateway parses, extracted from a provider's official spec.
//
// Paths use contracttest.UnknownFields' syntax: "$" is the root, ".key"
// steps into an object, "[]" into array elements and ".*" into map values.
// A stream whose events are split carries one root per event, written
// "<event>:$" ("message_delta:$.delta.stop_reason"); a stream of
// undifferentiated chunks is prefixed "chunk:".
type Subset struct {
	Schema   int    `json:"schema"`
	Provider string `json:"provider"`
	// Root is the subset's name within the provider, e.g. "messages".
	Root string `json:"root"`
	// Format is the spec format it was extracted from: openapi, discovery
	// or smithy.
	Format string `json:"format"`
	// Select says, in the source's terms, where the root came from (an
	// operation's response, a schema pointer, a shape ID).
	Select string `json:"select"`
	// Description says what the gateway does with this root.
	Description string `json:"description,omitempty"`
	// Events are the stream event types of an event-split root, sorted.
	Events []string `json:"events,omitempty"`
	// ExceptionEvents are events that carry an error (Smithy exception
	// members of an event stream, or the named error shapes of an
	// operation), sorted. They are also in Events.
	ExceptionEvents []string `json:"exception_events,omitempty"`
	// Paths maps every path to what the spec says about it.
	Paths map[string]*PathInfo `json:"paths"`
}

// ID is "<provider>/<root>", the name tests and allowlists use.
func (s *Subset) ID() string { return s.Provider + "/" + s.Root }

// SortedPaths returns the subset's paths in file order.
func (s *Subset) SortedPaths() []string {
	keys := make([]string, 0, len(s.Paths))
	for k := range s.Paths {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// Enum returns the enum values the subset records at path, or nil.
func (s *Subset) Enum(path string) []string {
	if p := s.Paths[path]; p != nil {
		return p.Enum
	}
	return nil
}

// normalize sorts every list so the encoding is byte-stable.
func (s *Subset) normalize() {
	for _, p := range s.Paths {
		p.Kinds = sortedUniq(p.Kinds)
		p.Enum = sortedUniq(p.Enum)
		p.Variants = sortedUniq(p.Variants)
	}
	s.Events = sortedUniq(s.Events)
	s.ExceptionEvents = sortedUniq(s.ExceptionEvents)
}

func sortedUniq(in []string) []string {
	if len(in) == 0 {
		return nil
	}
	seen := make(map[string]bool, len(in))
	out := make([]string, 0, len(in))
	for _, v := range in {
		if !seen[v] {
			seen[v] = true
			out = append(out, v)
		}
	}
	sort.Strings(out)
	return out
}

// Marshal encodes s canonically: header fields first, then one path per
// line, sorted, so a git diff of a subset reads as a list of changed paths.
// The output is valid JSON and ends with a newline.
func (s *Subset) Marshal() ([]byte, error) {
	s.normalize()
	var b bytes.Buffer
	field := func(name string, v any, last bool) error {
		raw, err := marshalCompact(v)
		if err != nil {
			return err
		}
		fmt.Fprintf(&b, "  %q: %s", name, raw)
		if !last {
			b.WriteByte(',')
		}
		b.WriteByte('\n')
		return nil
	}
	b.WriteString("{\n")
	type kv struct {
		k string
		v any
	}
	head := []kv{{"schema", s.Schema}, {"provider", s.Provider}, {"root", s.Root}, {"format", s.Format}, {"select", s.Select}}
	if s.Description != "" {
		head = append(head, kv{"description", s.Description})
	}
	if len(s.Events) > 0 {
		head = append(head, kv{"events", s.Events})
	}
	if len(s.ExceptionEvents) > 0 {
		head = append(head, kv{"exception_events", s.ExceptionEvents})
	}
	for _, h := range head {
		if err := field(h.k, h.v, false); err != nil {
			return nil, err
		}
	}
	b.WriteString("  \"paths\": {")
	keys := s.SortedPaths()
	for i, k := range keys {
		key, err := marshalCompact(k)
		if err != nil {
			return nil, err
		}
		val, err := marshalCompact(s.Paths[k])
		if err != nil {
			return nil, err
		}
		b.WriteString("\n    ")
		b.Write(key)
		b.WriteString(": ")
		b.Write(val)
		if i < len(keys)-1 {
			b.WriteByte(',')
		}
	}
	if len(keys) > 0 {
		b.WriteString("\n  ")
	}
	b.WriteString("}\n}\n")
	return b.Bytes(), nil
}

func marshalCompact(v any) ([]byte, error) {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimRight(b.Bytes(), "\n"), nil
}

// ParseSubset decodes a subset file. Unknown keys and a different schema
// version are errors, so a typo or a stale file cannot pass silently.
func ParseSubset(raw []byte) (*Subset, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var s Subset
	if err := dec.Decode(&s); err != nil {
		return nil, err
	}
	if s.Schema != SchemaVersion {
		return nil, fmt.Errorf("subset %s/%s: schema %d, this tool reads %d (regenerate it)", s.Provider, s.Root, s.Schema, SchemaVersion)
	}
	if s.Provider == "" || s.Root == "" {
		return nil, fmt.Errorf("subset without provider or root")
	}
	if s.Paths == nil {
		s.Paths = map[string]*PathInfo{}
	}
	for p, info := range s.Paths {
		if info == nil {
			return nil, fmt.Errorf("subset %s: path %s has no info", s.ID(), p)
		}
	}
	return &s, nil
}

// builder accumulates a subset while a walker runs.
type builder struct {
	s *Subset
	// prefix is put in front of every path a walker emits ("" or
	// "chunk:"); the walker itself always starts at "$".
	prefix string
	// rebase strips a leading path (an envelope such as "$.data") and
	// drops paths outside it.
	rebase string
}

func newBuilder(provider, root, format, sel string) *builder {
	return &builder{s: &Subset{
		Schema: SchemaVersion, Provider: provider, Root: root, Format: format, Select: sel,
		Paths: map[string]*PathInfo{},
	}}
}

// key maps a walker path to the stored path, applying rebase and prefix.
// ok is false for a path outside the rebase root.
func (b *builder) key(path string) (string, bool) {
	if b.rebase != "" {
		ev, rest := splitEvent(path)
		switch {
		case rest == b.rebase:
			rest = "$"
		case strings.HasPrefix(rest, b.rebase+".") || strings.HasPrefix(rest, b.rebase+"["):
			rest = "$" + rest[len(b.rebase):]
		default:
			return "", false
		}
		path = joinEvent(ev, rest)
	}
	if b.prefix != "" {
		if ev, _ := splitEvent(path); ev == "" {
			path = b.prefix + ":" + path
		}
	}
	return path, true
}

func (b *builder) at(path string) *PathInfo {
	k, ok := b.key(path)
	if !ok {
		return &PathInfo{} // outside the rebase root: recorded nowhere
	}
	p := b.s.Paths[k]
	if p == nil {
		p = &PathInfo{}
		b.s.Paths[k] = p
	}
	return p
}

func (b *builder) kind(path, k string)           { p := b.at(path); p.Kinds = append(p.Kinds, k) }
func (b *builder) enum(path string, v ...string) { p := b.at(path); p.Enum = append(p.Enum, v...) }
func (b *builder) variant(path, label string) {
	p := b.at(path)
	p.Variants = append(p.Variants, label)
}

func (b *builder) event(name string, exception bool) {
	b.s.Events = append(b.s.Events, name)
	if exception {
		b.s.ExceptionEvents = append(b.s.ExceptionEvents, name)
	}
}

func (b *builder) finish() *Subset {
	delete(b.s.Paths, "") // defensive: never emit an empty key
	b.s.normalize()
	return b.s
}

// splitEvent splits "message_delta:$.x" into ("message_delta", "$.x"); a
// path without an event prefix returns ("", path).
func splitEvent(path string) (event, rest string) {
	if i := strings.Index(path, ":$"); i > 0 {
		return path[:i], path[i+1:]
	}
	return "", path
}

func joinEvent(event, rest string) string {
	if event == "" {
		return rest
	}
	return event + ":" + rest
}

// parentPath returns the path one step up ("$.a.b" -> "$.a", "$.a[]" ->
// "$.a", "$.m.*" -> "$.m"); ok is false at a root.
func parentPath(p string) (string, bool) {
	ev, rest := splitEvent(p)
	if rest == "$" {
		return "", false
	}
	if strings.HasSuffix(rest, "[]") {
		return joinEvent(ev, strings.TrimSuffix(rest, "[]")), true
	}
	i := strings.LastIndex(rest, ".")
	if i <= 0 {
		return "", false
	}
	return joinEvent(ev, rest[:i]), true
}

// under reports whether p is anc or below it.
func under(p, anc string) bool {
	return p == anc || strings.HasPrefix(p, anc+".") || strings.HasPrefix(p, anc+"[")
}
