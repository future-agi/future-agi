package contractwatch

import (
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
)

// RecorderArgs are the cmd/contract-recorder arguments that record a plan
// entry: -provider Provider, then -from From -as As (a new model) or -model
// Model (a re-record), and -case with Cases joined by commas.
type RecorderArgs struct {
	Provider string   `json:"provider"`
	From     string   `json:"from,omitempty"`
	As       string   `json:"as,omitempty"`
	Model    string   `json:"model,omitempty"`
	Cases    []string `json:"cases"`
}

// PlanEntry is one NEW model and how to bring it under contract. The plan
// file (-plan-out) is a JSON array of these.
type PlanEntry struct {
	Provider      string `json:"provider"`
	NewModel      string `json:"new_model"`
	TemplateModel string `json:"template_model"`
	Reason        string `json:"reason"`
	// Warnings are mismatches the template could not avoid.
	Warnings []string `json:"warnings,omitempty"`
	// AliasOf is set when the new ID looks like TemplateModel under another
	// snapshot date or Bedrock geography.
	AliasOf string `json:"alias_of,omitempty"`
	// Record is true for the entries this run should record: those with a
	// template, up to the -max-record cap, spread across providers.
	Record   bool          `json:"record"`
	Recorder *RecorderArgs `json:"recorder,omitempty"`
	// InModelDB is whether the gateway's bundled pricing data
	// (internal/modeldb/litellm.json) knows the model; without it, requests
	// to it are not costed.
	InModelDB bool   `json:"in_modeldb"`
	Metadata  *Model `json:"metadata,omitempty"`
}

// NewestEntry is one covered model -newest re-records.
type NewestEntry struct {
	Provider string       `json:"provider"`
	Model    string       `json:"model"`
	Family   string       `json:"family"`
	Reason   string       `json:"reason"`
	Record   bool         `json:"record"`
	Recorder RecorderArgs `json:"recorder"`
}

// PlanOptions bound what a run records.
type PlanOptions struct {
	ProvidersDir string
	// MaxRecord caps the entries marked Record; 0 means no cap.
	MaxRecord int
	// MaxCases caps the cases per entry; 0 means every case.
	MaxCases int
	// Rotate shifts -newest's window, so a cap below the number of families
	// still reaches every family over successive runs (the workflow passes
	// the day number).
	Rotate int
}

// casePriority is the order cases are kept in under -max-cases: the plain
// text and tool paths first, streaming next, then thinking, then the rest by
// name. They are the paths every model takes; the rest are model-specific.
var casePriority = []string{
	"text_basic", "stream_text", "tool_call", "tool_use", "stream_tool_call", "stream_tool_use",
	"thinking", "reasoning", "stream_thinking", "stream_reasoning",
}

// PickCases orders cases by casePriority, then plain cases by name, then
// secondary ones (error statuses, other endpoints) by name, and keeps the
// first max (all when max is 0).
func PickCases(cases []CaseInfo, max int) []string {
	rank := func(c CaseInfo) int {
		for i, p := range casePriority {
			if c.Name == p && !c.Secondary {
				return i
			}
		}
		if c.Secondary {
			return len(casePriority) + 1
		}
		return len(casePriority)
	}
	sorted := append([]CaseInfo(nil), cases...)
	sort.SliceStable(sorted, func(i, j int) bool {
		ri, rj := rank(sorted[i]), rank(sorted[j])
		if ri != rj {
			return ri < rj
		}
		return sorted[i].Name < sorted[j].Name
	})
	if max > 0 && len(sorted) > max {
		sorted = sorted[:max]
	}
	out := make([]string, len(sorted))
	for i, c := range sorted {
		out[i] = c.Name
	}
	return out
}

// BuildPlan picks a template for every NEW model in results and lays out the
// recorder arguments. Entries are grouped by provider, newest first within
// one; Record is spread round-robin across providers up to opts.MaxRecord, so
// one provider's backlog cannot use the whole budget.
func BuildPlan(reg *Registry, results []SectionResult, opts PlanOptions) []PlanEntry {
	db := modeldb.New(modeldb.BundledModels, nil)
	var perSection [][]PlanEntry
	for _, r := range results {
		s := reg.Providers[r.Name]
		if s == nil || len(r.New) == 0 {
			continue
		}
		var cands []Candidate
		for _, id := range s.Covered() {
			kinds := FixtureKinds(opts.ProvidersDir, s.FixtureProvider(r.Name), s.FixtureKey(id))
			cands = append(cands, Candidate{ID: id, Meta: r.Meta[id], Kinds: kinds})
		}
		var entries []PlanEntry
		for i := range r.New {
			m := r.New[i]
			e := PlanEntry{Provider: r.Name, NewModel: m.ID, Metadata: &m, InModelDB: inModelDB(db, r.Name, m.ID)}
			ch, ok := SelectTemplate(r.Name, m.ID, &m, cands)
			if !ok {
				e.Reason = "no covered " + r.Name + " " + ModelKind(m.ID, &m) + " model to clone cases from: write this model's fixtures by hand (contracttest/README.md, \"Adding a model version\")"
				entries = append(entries, e)
				continue
			}
			e.TemplateModel, e.Reason, e.Warnings, e.AliasOf = ch.Template, ch.Reason, ch.Warnings, ch.AliasOf
			prov := s.FixtureProvider(r.Name)
			cases, err := TemplateCases(opts.ProvidersDir, prov, s.FixtureKey(ch.Template))
			if err != nil || len(cases) == 0 {
				e.Warnings = append(e.Warnings, "the template has no cases on disk (run model-watch -offline)")
			}
			e.Recorder = &RecorderArgs{
				Provider: prov,
				From:     s.FixtureKey(ch.Template),
				As:       s.FixtureKey(m.ID),
				Cases:    PickCases(cases, opts.MaxCases),
			}
			entries = append(entries, e)
		}
		// Successors of a covered family go first (the diff already put them
		// newest first): within the cap, gemini-4.1-argon is recorded before
		// a first gemma.
		sort.SliceStable(entries, func(i, j int) bool {
			return successor(r.Name, entries[i]) && !successor(r.Name, entries[j])
		})
		perSection = append(perSection, entries)
	}
	var out []PlanEntry
	order := roundRobin(len(perSection), func(i int) int { return len(perSection[i]) })
	marked := 0
	for _, ix := range order {
		e := &perSection[ix[0]][ix[1]]
		if e.Recorder != nil && len(e.Recorder.Cases) > 0 && (opts.MaxRecord <= 0 || marked < opts.MaxRecord) {
			e.Record = true
			marked++
		}
	}
	for _, es := range perSection {
		out = append(out, es...)
	}
	return out
}

// successor reports whether e's new model is of its template's family.
func successor(section string, e PlanEntry) bool {
	if e.TemplateModel == "" {
		return false
	}
	n, t := ParseModelID(section, e.NewModel), ParseModelID(section, e.TemplateModel)
	return n.Family == t.Family && n.Vendor == t.Vendor
}

// roundRobin returns (group, index) pairs taking the first of every group,
// then the second, and so on.
func roundRobin(groups int, size func(int) int) [][2]int {
	var out [][2]int
	for k := 0; ; k++ {
		added := false
		for g := 0; g < groups; g++ {
			if k < size(g) {
				out = append(out, [2]int{g, k})
				added = true
			}
		}
		if !added {
			return out
		}
	}
}

// litellmPrefix is the provider prefix litellm.json files a section's models
// under, when it uses one.
var litellmPrefix = map[string]string{"gemini": "gemini/", "groq": "groq/", "perplexity": "perplexity/", "bedrock": "bedrock/"}

func inModelDB(db *modeldb.ModelDB, section, id string) bool {
	if p := litellmPrefix[section]; p != "" {
		if _, ok := db.Get(p + id); ok {
			return true
		}
	}
	_, ok := db.Get(id)
	return ok
}

// BuildNewest picks, for each section in sections, the newest covered model
// of every family (FamilyKey) and lays out a re-record of it. Sections are
// interleaved round-robin; with opts.MaxRecord set, a window of that many
// entries starting at opts.Rotate*MaxRecord (wrapping) is marked Record.
func BuildNewest(reg *Registry, sections []string, opts PlanOptions) []NewestEntry {
	var perSection [][]NewestEntry
	for _, name := range sections {
		s := reg.Providers[name]
		if s == nil {
			continue
		}
		families := map[string][]string{}
		for _, id := range s.Covered() {
			k := FamilyKey(name, id)
			families[k] = append(families[k], id)
		}
		keys := make([]string, 0, len(families))
		for k := range families {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		var entries []NewestEntry
		for _, k := range keys {
			ids := families[k]
			newest := ids[0]
			for _, id := range ids[1:] {
				if Newer(name, id, newest) {
					newest = id
				}
			}
			prov := s.FixtureProvider(name)
			cases, _ := TemplateCases(opts.ProvidersDir, prov, s.FixtureKey(newest))
			reason := "newest covered model of its family"
			if len(ids) > 1 {
				reason += " (of " + strings.Join(ids, ", ") + ")"
			}
			entries = append(entries, NewestEntry{
				Provider: name,
				Model:    newest,
				Family:   familyLabel(name, newest),
				Reason:   reason,
				Recorder: RecorderArgs{Provider: prov, Model: s.FixtureKey(newest), Cases: PickCases(cases, opts.MaxCases)},
			})
		}
		perSection = append(perSection, entries)
	}
	var out []NewestEntry
	for _, ix := range roundRobin(len(perSection), func(i int) int { return len(perSection[i]) }) {
		out = append(out, perSection[ix[0]][ix[1]])
	}
	n := len(out)
	switch {
	case n == 0:
	case opts.MaxRecord <= 0 || opts.MaxRecord >= n:
		for i := range out {
			out[i].Record = len(out[i].Recorder.Cases) > 0
		}
	default:
		start := ((opts.Rotate%n)*opts.MaxRecord%n + n) % n
		for k := 0; k < opts.MaxRecord; k++ {
			i := (start + k) % n
			out[i].Record = len(out[i].Recorder.Cases) > 0
		}
	}
	return out
}

// familyLabel is a readable family name: "gemini flash-lite", "bedrock
// converse anthropic claude sonnet".
func familyLabel(section, id string) string {
	m := ParseModelID(section, id)
	parts := []string{}
	for _, p := range []string{m.API, m.Vendor, m.Family, strings.Join(m.Tiers, "-")} {
		if p != "" {
			parts = append(parts, p)
		}
	}
	if m.Thinking == "yes" {
		parts = append(parts, "(reasoning)")
	}
	return strings.Join(parts, " ")
}
