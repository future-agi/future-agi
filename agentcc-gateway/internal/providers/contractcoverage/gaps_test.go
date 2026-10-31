package contractcoverage

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
)

const (
	gapsDoc       = "../KNOWN_GAPS.md"
	summariesFile = "gap_summaries.json"
)

// gapPrefixes are the known gap ID prefixes, in the document's order: each
// provider owns its prefixes (internal/providers/contracttest/README.md).
var gapPrefixes = []struct{ Prefix, Provider, Topic string }{
	{"T", "Anthropic", "thinking"},
	{"C", "Anthropic", "citations"},
	{"S", "Anthropic", "stop reasons"},
	{"U", "Anthropic", "usage and billing"},
	{"X", "Anthropic", "errors, models and the rest"},
	{"R", "Anthropic", "request side"},
	{"O", "OpenAI", ""},
	{"A", "Azure OpenAI", ""},
	{"G", "Gemini", ""},
	{"GR", "Gemini", "request side"},
	{"V", "Vertex AI", ""},
	{"B", "Amazon Bedrock", ""},
	{"K", "Cohere", ""},
	{"GQ", "Groq", ""},
	{"P", "Perplexity", ""},
	{"MI", "Mistral", ""},
	{"OR", "OpenRouter", ""},
	{"TG", "Together", ""},
	{"CB", "Cerebras", ""},
	{"FW", "Fireworks", ""},
	{"XA", "xAI", ""},
	{"RP", "Replicate", ""},
	{"DI", "DeepInfra", ""},
	{"HF", "Hugging Face", ""},
	{"AN", "Anyscale", ""},
	{"DG", "Deepgram", "speech relay"},
	{"CA", "Cartesia", "speech relay"},
}

var (
	// gapDef is a definition: "known gap <ID>: <reason>" ("known gap T1/T2:"
	// defines both).
	gapDef = regexp.MustCompile(`(?i:known gap) ([A-Z]{1,3}[0-9]+(?:/[A-Z]{1,3}[0-9]+)*)\s*:\s*`)
	// gapNoID is the ID-less "known gap: <reason>", which every definition
	// must replace with "known gap <ID>: <reason>".
	gapNoID = regexp.MustCompile(`(?i)\bknown gap\s*:`)
	// gapRef is a reference to an ID ("gap B1", "gaps K1", "known gap B14)").
	gapRef = regexp.MustCompile(`\bgaps? ([A-Z]{1,3}[0-9]+)\b`)
	// gapID is a well-formed ID: a prefix and a number without a leading
	// zero.
	gapID = regexp.MustCompile(`^([A-Z]{1,3})([1-9][0-9]*)$`)
	// sentenceEnd ends a reason's first sentence.
	sentenceEnd = regexp.MustCompile(`[a-z0-9)"'\]]\.\s+[A-Z]`)
)

// gapOccurrence is one definition of a gap ID.
type gapOccurrence struct {
	ID       string
	Reason   string // the first sentence: the loss itself
	Provider string // the adapter directory, or "server"
	Where    string // model, model/case, "spec <subset>", or a test file
}

// gapSources scans every file that can define a gap: the JSON under each
// adapter's testdata (meta.json, case.json, spec_allowlist*.json, speech
// sessions; not the upstream bodies or goldens) and the provider test files'
// string constants (internal/providers/<adapter>/*_test.go, the providers
// package's own *_test.go, whose preset-health table files the presets'
// defects, and internal/server/contract_*_test.go; not the harness packages,
// whose tests use placeholder gaps).
func gapSources(t *testing.T) (defs []gapOccurrence, refs map[string][]string, problems []string) {
	t.Helper()
	refs = map[string][]string{}
	scan := func(s, provider, where, file string) {
		for _, m := range gapNoID.FindAllStringIndex(s, -1) {
			problems = append(problems, fmt.Sprintf("%s: %q: a known gap needs an ID (\"known gap <ID>: <reason>\")", file, excerpt(s, m[0])))
		}
		ms := gapDef.FindAllStringSubmatchIndex(s, -1)
		for i, m := range ms {
			end := len(s)
			if i+1 < len(ms) {
				end = ms[i+1][0]
			}
			reason := firstSentence(s[m[1]:end])
			for _, id := range strings.Split(s[m[2]:m[3]], "/") {
				defs = append(defs, gapOccurrence{ID: id, Reason: reason, Provider: provider, Where: where})
			}
		}
		for _, m := range gapRef.FindAllStringSubmatch(s, -1) {
			refs[m[1]] = append(refs[m[1]], file)
		}
	}

	// JSON under testdata.
	skip := map[string]bool{"response.json": true, "request.json": true, "golden.json": true, "upstream_request.golden.json": true, "opaque.golden.json": true}
	adapters, err := os.ReadDir(providersDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, a := range adapters {
		if !a.IsDir() || harnessPackages[a.Name()] {
			continue
		}
		root := filepath.Join(providersDir, a.Name(), "testdata")
		err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
			if os.IsNotExist(err) && path == root {
				return filepath.SkipDir
			}
			if err != nil || d.IsDir() || !strings.HasSuffix(path, ".json") || skip[d.Name()] {
				return err
			}
			raw, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			var v any
			if err := json.Unmarshal(raw, &v); err != nil {
				return fmt.Errorf("%s: %v", path, err)
			}
			where := jsonWhere(root, path)
			walkJSON(v, where, func(s, where string) { scan(s, a.Name(), where, path) })
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
		tests, _ := filepath.Glob(filepath.Join(providersDir, a.Name(), "*_test.go"))
		for _, f := range tests {
			for _, s := range goStrings(t, f) {
				scan(s, a.Name(), filepath.Base(f), f)
			}
		}
	}
	root, _ := filepath.Glob(filepath.Join(providersDir, "*_test.go"))
	for _, f := range root {
		for _, s := range goStrings(t, f) {
			scan(s, "providers", filepath.Base(f), f)
		}
	}
	server, _ := filepath.Glob(filepath.Join(providersDir, "..", "server", "contract_*_test.go"))
	for _, f := range server {
		for _, s := range goStrings(t, f) {
			scan(s, "server", filepath.Base(f), f)
		}
	}
	return defs, refs, problems
}

// harnessPackages are internal/providers packages whose tests exercise the
// gap machinery itself with placeholder gaps.
var harnessPackages = map[string]bool{"contracttest": true, "contractspec": true, "contractwatch": true, "contractcoverage": true}

// jsonWhere labels a testdata file: the model for meta.json, model/case for
// case.json, vendor/session for a speech session, else the file name (spec
// allowlist entries are labelled by their subset in walkJSON).
func jsonWhere(root, path string) string {
	rel, _ := filepath.Rel(root, path)
	parts := strings.Split(filepath.ToSlash(rel), "/")
	switch {
	case len(parts) == 3 && parts[0] == "contracts" && parts[2] == "meta.json":
		return unescape(parts[1])
	case len(parts) == 4 && parts[0] == "contracts":
		return unescape(parts[1]) + "/" + parts[2]
	case len(parts) == 4 && parts[0] == "sessions":
		return parts[1] + "/" + parts[2]
	}
	return filepath.ToSlash(rel)
}

func unescape(dir string) string {
	if s, err := url.PathUnescape(dir); err == nil {
		return s
	}
	return dir
}

// walkJSON calls fn with every string in v; an object with a "subset" (a
// spec allowlist entry) labels the strings inside it "spec <subset>".
func walkJSON(v any, where string, fn func(s, where string)) {
	switch x := v.(type) {
	case string:
		fn(x, where)
	case []any:
		for _, e := range x {
			walkJSON(e, where, fn)
		}
	case map[string]any:
		if sub, ok := x["subset"].(string); ok {
			where = "spec " + sub
		}
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			walkJSON(x[k], where, fn)
		}
	}
}

// goStrings returns a Go file's string constants, adjacent literals joined
// by + merged into one.
func goStrings(t *testing.T, file string) []string {
	t.Helper()
	f, err := parser.ParseFile(token.NewFileSet(), file, nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	ast.Inspect(f, func(n ast.Node) bool {
		e, ok := n.(ast.Expr)
		if !ok {
			return true
		}
		if s, ok := constString(e); ok {
			out = append(out, s)
			return false
		}
		return true
	})
	return out
}

func constString(e ast.Expr) (string, bool) {
	switch x := e.(type) {
	case *ast.BasicLit:
		if x.Kind != token.STRING {
			return "", false
		}
		s, err := strconv.Unquote(x.Value)
		return s, err == nil
	case *ast.BinaryExpr:
		if x.Op != token.ADD {
			return "", false
		}
		l, ok1 := constString(x.X)
		r, ok2 := constString(x.Y)
		return l + r, ok1 && ok2
	case *ast.ParenExpr:
		return constString(x.X)
	}
	return "", false
}

// firstSentence is a reason's first sentence, which states the loss; what
// follows is context particular to where it is written.
func firstSentence(s string) string {
	s = strings.Join(strings.Fields(s), " ")
	if loc := sentenceEnd.FindStringIndex(s); loc != nil {
		s = s[:loc[0]+2]
	}
	s = strings.TrimRight(s, " ;,")
	return strings.TrimSuffix(s, ".")
}

func excerpt(s string, at int) string {
	end := at + 60
	if end > len(s) {
		end = len(s)
	}
	return s[at:end]
}

// gapSummaries is gap_summaries.json: a reviewed one-line summary for every
// ID whose definitions word the loss differently.
type gapSummaries struct {
	About     string            `json:"about"`
	Summaries map[string]string `json:"summaries"`
}

func loadSummaries(t *testing.T) gapSummaries {
	t.Helper()
	raw, err := os.ReadFile(summariesFile)
	if err != nil {
		t.Fatal(err)
	}
	var s gapSummaries
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&s); err != nil {
		t.Fatalf("%s: %v", summariesFile, err)
	}
	return s
}

// gapInfo is one ID's definitions.
type gapInfo struct {
	ID      string
	Reasons map[string]int // first sentence -> occurrences
	Occ     []gapOccurrence
}

// gapIndex groups the definitions by ID.
func gapIndex(defs []gapOccurrence) map[string]*gapInfo {
	idx := map[string]*gapInfo{}
	for _, d := range defs {
		g := idx[d.ID]
		if g == nil {
			g = &gapInfo{ID: d.ID, Reasons: map[string]int{}}
			idx[d.ID] = g
		}
		g.Reasons[d.Reason]++
		g.Occ = append(g.Occ, d)
	}
	return idx
}

// prefixOf splits a well-formed ID; ok is false for a malformed one or an
// unknown prefix.
func prefixOf(id string) (prefix string, n int, ok bool) {
	m := gapID.FindStringSubmatch(id)
	if m == nil {
		return "", 0, false
	}
	n, _ = strconv.Atoi(m[2])
	for _, p := range gapPrefixes {
		if p.Prefix == m[1] {
			return m[1], n, true
		}
	}
	return m[1], n, false
}

// TestKnownGaps collects every gap definition, validates the IDs, requires a
// reviewed summary for every ID whose definitions word the loss
// differently, and compares ../KNOWN_GAPS.md (-update rewrites it).
//
// The summaries are the semi-automatic collision check: two different losses
// filed under one ID show up as one ID with differing reasons, and a person
// decides whether they are one loss (write the summary) or two (renumber
// one). Every differing ID is logged with its variants.
func TestKnownGaps(t *testing.T) {
	defs, refs, problems := gapSources(t)
	for _, p := range problems {
		t.Error(p)
	}
	idx := gapIndex(defs)
	for id := range idx {
		if _, _, ok := prefixOf(id); !ok {
			t.Errorf("gap ID %q is malformed or has an unknown prefix (want one of the prefixes in gapPrefixes and a number without a leading zero), defined in %s", id, idx[id].Occ[0].Provider+": "+idx[id].Occ[0].Where)
		}
		for r := range idx[id].Reasons {
			if strings.TrimSpace(r) == "" {
				t.Errorf("gap %s is defined with an empty reason in %s", id, idx[id].Occ[0].Provider+": "+idx[id].Occ[0].Where)
			}
		}
	}
	var refIDs []string
	for id := range refs {
		refIDs = append(refIDs, id)
	}
	sort.Strings(refIDs)
	for _, id := range refIDs {
		if idx[id] == nil {
			t.Errorf("gap %s is referenced (%s) but never defined as \"known gap %s: <reason>\"", id, strings.Join(uniq(refs[id]), ", "), id)
		}
	}

	sums := loadSummaries(t)
	for _, id := range sortedIDs(idx) {
		g := idx[id]
		if len(g.Reasons) < 2 {
			continue
		}
		var variants []string
		for r, n := range g.Reasons {
			variants = append(variants, fmt.Sprintf("  [%d] %s", n, r))
		}
		sort.Strings(variants)
		t.Logf("gap %s: %d wordings:\n%s", id, len(g.Reasons), strings.Join(variants, "\n"))
		if strings.TrimSpace(sums.Summaries[id]) == "" {
			t.Errorf("gap %s is defined with %d different wordings: if they are one loss, add a one-line summary to %s; if not, renumber one (the variants are logged above)", id, len(g.Reasons), summariesFile)
		}
	}
	var sumIDs []string
	for id := range sums.Summaries {
		sumIDs = append(sumIDs, id)
	}
	sort.Strings(sumIDs)
	for _, id := range sumIDs {
		if g := idx[id]; g == nil || len(g.Reasons) < 2 {
			t.Errorf("%s: stale summary for %s: it has no differing wordings any more; drop it", summariesFile, id)
		}
	}
	compareDoc(t, gapsDoc, renderGaps(idx, sums))
}

func uniq(ss []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range ss {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	sort.Strings(out)
	return out
}

// sortedIDs orders IDs by prefix (gapPrefixes order), then number.
func sortedIDs(idx map[string]*gapInfo) []string {
	order := map[string]int{}
	for i, p := range gapPrefixes {
		order[p.Prefix] = i
	}
	ids := make([]string, 0, len(idx))
	for id := range idx {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool {
		pi, ni, _ := prefixOf(ids[i])
		pj, nj, _ := prefixOf(ids[j])
		if order[pi] != order[pj] {
			return order[pi] < order[pj]
		}
		if ni != nj {
			return ni < nj
		}
		return ids[i] < ids[j]
	})
	return ids
}

// renderGaps writes KNOWN_GAPS.md.
func renderGaps(idx map[string]*gapInfo, sums gapSummaries) []byte {
	var b bytes.Buffer
	w := func(format string, args ...any) { fmt.Fprintf(&b, format+"\n", args...) }
	ids := sortedIDs(idx)
	total := 0
	perPrefix := map[string]int{}
	for _, id := range ids {
		total += len(idx[id].Occ)
		p, _, _ := prefixOf(id)
		perPrefix[p]++
	}

	w("# Known gaps")
	w("")
	b.WriteString(generatedNote)
	w("")
	w("Every response field, event, finish reason, error mapping or billing input a provider adapter loses, as")
	w("the contract tests allowlist it: each `known gap <ID>: <reason>` in the fixtures' `meta.json` and `case.json`,")
	w("the speech sessions, the `spec_allowlist*.json` files of the official-spec checks, and the provider tests' gap")
	w("tables. A gap is allowlisted, not fixed: the suites fail when it changes or goes away, and a fix deletes its")
	w("entries. One line per ID: the loss (the reason's first sentence, or the reviewed summary in")
	w("`internal/providers/contractcoverage/gap_summaries.json` where definitions word it differently) and where it")
	w("is defined (provider, then model, model/case, `spec <subset>` or test file, with a count when more than one).")
	w("Coverage by provider and endpoint is in [CONTRACT_COVERAGE.md](CONTRACT_COVERAGE.md).")
	w("")
	w("## Totals")
	w("")
	w("%d gap IDs, defined %d times.", len(ids), total)
	w("")
	w("| prefix | provider | topic | IDs |")
	w("|---|---|---|--:|")
	for _, p := range gapPrefixes {
		if perPrefix[p.Prefix] == 0 {
			continue
		}
		topic := p.Topic
		if topic == "" {
			topic = "all"
		}
		w("| `%s` | %s | %s | %d |", p.Prefix, p.Provider, topic, perPrefix[p.Prefix])
	}
	w("")

	section := ""
	for _, id := range ids {
		pfx, _, _ := prefixOf(id)
		provider := "Unknown prefix"
		for _, p := range gapPrefixes {
			if p.Prefix == pfx {
				provider = p.Provider
			}
		}
		if provider != section {
			section = provider
			w("## %s", provider)
			w("")
			w("| ID | loss | defined in |")
			w("|---|---|---|")
		}
		g := idx[id]
		loss := sums.Summaries[id]
		if len(g.Reasons) < 2 || loss == "" {
			for r := range g.Reasons {
				loss = r
			}
		}
		w("| %s | %s | %s |", id, oneLine(loss), oneLine(whereList(g.Occ)))
		if next := nextSection(ids, id); next != section {
			w("")
		}
	}
	return b.Bytes()
}

// nextSection is the provider of the ID after id, or "".
func nextSection(ids []string, id string) string {
	for i, x := range ids {
		if x == id && i+1 < len(ids) {
			p, _, _ := prefixOf(ids[i+1])
			for _, gp := range gapPrefixes {
				if gp.Prefix == p {
					return gp.Provider
				}
			}
		}
	}
	return ""
}

// whereList groups occurrences by provider: "openai: gpt-6-sol ×2, spec
// openai/chat; azure: gpt-4.1-2025-04-14".
func whereList(occ []gapOccurrence) string {
	byProvider := map[string]map[string]int{}
	var provs []string
	for _, o := range occ {
		if byProvider[o.Provider] == nil {
			byProvider[o.Provider] = map[string]int{}
			provs = append(provs, o.Provider)
		}
		byProvider[o.Provider][o.Where]++
	}
	sort.Strings(provs)
	var parts []string
	for _, p := range provs {
		var wh []string
		for k := range byProvider[p] {
			wh = append(wh, k)
		}
		sort.Strings(wh)
		for i, k := range wh {
			if n := byProvider[p][k]; n > 1 {
				wh[i] = fmt.Sprintf("%s ×%d", k, n)
			}
		}
		parts = append(parts, p+": "+strings.Join(wh, ", "))
	}
	return strings.Join(parts, "; ")
}
