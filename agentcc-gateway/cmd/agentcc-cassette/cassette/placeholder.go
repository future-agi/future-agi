package cassette

import (
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"
)

// class is what a scrubbed value was.
type class int

const (
	classToken class = iota
	classLiteral
	classEmail
	classID
)

var classNames = [...]string{"token", "literal", "email", "id"}

// Minimum lengths for replacing a value wherever it appears. A shorter
// credential is still replaced at the header, parameter or key it was found
// in; it is only not hunted down in free text, where a three-letter cookie
// value would match half the cassette.
const (
	minGlobalToken = 8
	minID          = 6
)

// placeholders maps each original value to a stable placeholder: the same
// value gets the same placeholder everywhere in a cassette, numbered in the
// order values were first met, so a recorded flow stays connected (an id a
// list returned is the id the next call asked for).
type placeholders struct {
	class   map[string]class
	order   []string
	ph      map[string]string
	used    map[string]bool
	n       [len(classNames)]int
	sub     *regexp.Regexp    // every substitutable variant, longest first
	subOf   map[string]string // variant -> its replacement
	bounded map[string]bool   // variants replaced only where they stand alone
	literal map[string]bool   // variants of a literal: in a member name, replaced only where they stand alone
}

func newPlaceholders() *placeholders {
	return &placeholders{class: map[string]class{}, ph: map[string]string{}, used: map[string]bool{}}
}

// add records v as a value of class c. A value met as more than one class
// keeps the most sensitive (token, then literal, email and id).
func (p *placeholders) add(c class, v string) {
	if v == "" || isPlaceholder(v) {
		return
	}
	old, seen := p.class[v]
	if !seen {
		p.order = append(p.order, v)
	}
	if !seen || c < old {
		p.class[v] = c
	}
}

// assign hands out the placeholders, in first-met order, and builds the
// substitution table.
func (p *placeholders) assign() {
	for _, v := range p.order {
		c := p.class[v]
		var ph string
		for ph == "" || p.used[ph] || p.isOriginal(ph) {
			p.n[c]++
			ph = makePlaceholder(c, v, p.n[c])
		}
		p.ph[v], p.used[ph] = ph, true
	}
	p.subOf, p.bounded, p.literal = map[string]string{}, map[string]bool{}, map[string]bool{}
	var variants []string
	for _, v := range p.order {
		c := p.class[v]
		if c == classToken && len(v) < minGlobalToken {
			continue
		}
		for _, enc := range variantEncodings {
			from, to := enc(v), enc(p.ph[v])
			if _, dup := p.subOf[from]; dup {
				continue
			}
			p.subOf[from] = to
			// an id, and any value of digits only (a numeric password), is
			// replaced only where it stands alone: inside a longer number
			// or word it is another value
			p.bounded[from] = c == classID || digitsRE.MatchString(v)
			p.literal[from] = c == classLiteral
			variants = append(variants, from)
		}
	}
	sort.SliceStable(variants, func(i, j int) bool { return len(variants[i]) > len(variants[j]) })
	if len(variants) == 0 {
		return
	}
	quoted := make([]string, len(variants))
	for i, v := range variants {
		quoted[i] = regexp.QuoteMeta(v)
	}
	p.sub = regexp.MustCompile(strings.Join(quoted, "|"))
}

// variantEncodings are the forms a value is replaced in: as itself, query-
// and path-escaped, and with only its @ escaped (as many clients write an
// email in a URL).
var variantEncodings = []func(string) string{
	func(s string) string { return s },
	url.QueryEscape,
	url.PathEscape,
	func(s string) string { return strings.ReplaceAll(s, "@", "%40") },
}

func (p *placeholders) isOriginal(v string) bool { _, ok := p.class[v]; return ok }

// global reports a collected value that is replaced wherever it appears:
// all but a credential shorter than minGlobalToken, which is replaced only
// where a rule found it.
func (p *placeholders) global(v string) bool {
	c, ok := p.class[v]
	return ok && !(c == classToken && len(v) < minGlobalToken)
}

func (p *placeholders) className(v string) string { return classNames[p.class[v]] }

// get is v's placeholder. A value the collect pass never met cannot have
// one; it becomes a fixed marker rather than survive.
func (p *placeholders) get(v string) string {
	if ph, ok := p.ph[v]; ok {
		return ph
	}
	if isPlaceholder(v) {
		return v
	}
	return "scrubbed-unmatched"
}

// substitute replaces every collected value in s by its placeholder. An id
// or a digits-only value is replaced only where it stands alone (no letter
// or digit touching it), and so is a literal in a member name (inName):
// latest and contest keep their names when the literal is "test".
func (p *placeholders) substitute(s string, inName bool) string {
	return p.replaceMatches(s, inName, func(from, to string) string { return to })
}

// survivors lists the classes of collected values still present in s, as
// substitute would find them.
func (p *placeholders) survivors(s string, inName bool) []string {
	var found []string
	p.replaceMatches(s, inName, func(from, _ string) string {
		found = append(found, classNames[p.class[p.originalOf(from)]])
		return from
	})
	return found
}

func (p *placeholders) originalOf(variant string) string {
	for _, v := range p.order {
		for _, enc := range variantEncodings {
			if enc(v) == variant {
				return v
			}
		}
	}
	return variant
}

// replaceMatches finds every collected value in s, except inside a
// placeholder already there: a literal such as "token" must neither
// rewrite scrubbed-token-1 nor be reported as surviving in it.
func (p *placeholders) replaceMatches(s string, inName bool, on func(from, to string) string) string {
	if p.sub == nil || s == "" {
		return s
	}
	locs := p.sub.FindAllStringIndex(s, -1)
	if locs == nil {
		return s
	}
	inside := placeholderRE.FindAllStringIndex(s, -1)
	var b strings.Builder
	last := 0
	for _, loc := range locs {
		from := s[loc[0]:loc[1]]
		alone := p.bounded[from] || inName && p.literal[from]
		if alone && !standsAlone(s, loc[0], loc[1]) || within(loc, inside) {
			continue
		}
		b.WriteString(s[last:loc[0]])
		b.WriteString(on(from, p.subOf[from]))
		last = loc[1]
	}
	b.WriteString(s[last:])
	return b.String()
}

// placeholderRE matches the placeholders this scrubber writes (digit
// placeholders cannot be told from data and are not listed).
var placeholderRE = regexp.MustCompile(`scrubbed-(?:token|literal|id)-[0-9]+|scrubbed-unmatched|user-[0-9]+@example\.com|` +
	placeholderUUIDPrefix + `[0-9]{12}`)

// within reports whether loc lies inside one of spans.
func within(loc []int, spans [][]int) bool {
	for _, sp := range spans {
		if loc[0] >= sp[0] && loc[1] <= sp[1] {
			return true
		}
	}
	return false
}

func standsAlone(s string, start, end int) bool {
	return (start == 0 || !isAlnum(s[start-1])) && (end == len(s) || !isAlnum(s[end]))
}

// counts is the number of placeholders per class, for the cassette header.
func (p *placeholders) counts() map[string]int {
	out := map[string]int{}
	for _, v := range p.order {
		out[classNames[p.class[v]]]++
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

var (
	placeholderEmailRE = regexp.MustCompile(`^user-[0-9]+@example\.com$`)
	digitsRE           = regexp.MustCompile(`^[0-9]+$`)
	uuidRE             = regexp.MustCompile(`[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}`)
)

const placeholderUUIDPrefix = "00000000-0000-4000-8000-"

// makePlaceholder is the n-th placeholder of class c for value v. An id keeps
// its shape, so a schema that wants an integer or a uuid still validates.
func makePlaceholder(c class, v string, n int) string {
	switch c {
	case classToken:
		return fmt.Sprintf("scrubbed-token-%d", n)
	case classLiteral:
		return fmt.Sprintf("scrubbed-literal-%d", n)
	case classEmail:
		return fmt.Sprintf("user-%d@example.com", n)
	}
	switch {
	case digitsRE.MatchString(v):
		d := fmt.Sprintf("%d", n)
		if len(d) < len(v) {
			d = "9" + strings.Repeat("0", len(v)-1-len(d)) + d
		}
		return d
	case len(v) == 36 && uuidRE.MatchString(v):
		return fmt.Sprintf("%s%012d", placeholderUUIDPrefix, n)
	}
	return fmt.Sprintf("scrubbed-id-%d", n)
}

// isPlaceholder reports a value this scrubber wrote (digit placeholders
// cannot be told from data, and need not be).
func isPlaceholder(v string) bool {
	return strings.HasPrefix(v, "scrubbed-") || placeholderEmailRE.MatchString(v) ||
		strings.HasPrefix(v, placeholderUUIDPrefix)
}
