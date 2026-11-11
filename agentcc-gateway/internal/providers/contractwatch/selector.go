package contractwatch

import (
	"fmt"
	"sort"
	"strings"
)

// Candidate is a covered model the selector may clone cases from, with the
// metadata the provider listed for it (nil when it was not listed).
type Candidate struct {
	ID   string
	Meta *Model
	// Kinds are the kinds its fixtures exercise (FixtureKinds); empty means
	// the kind its ID and metadata name (ModelKind).
	Kinds []string
}

// Choice is the selector's answer for one new model.
type Choice struct {
	Template string
	Reason   string
	// AliasOf is set when the new ID looks like the same model as Template
	// under another snapshot date or Bedrock geography, so an alias entry may
	// be all it needs.
	AliasOf string
	// Warnings are mismatches the choice could not avoid (a different
	// Bedrock API, thinking support).
	Warnings []string
}

// score is one candidate's rank, compared field by field: an earlier field
// always outweighs every later one.
type score struct {
	sameAPI, sameVendor, sameFamily, thinkingOK, tierOverlap, sameIDThinking int
	versionDist                                                              int // lower is better
	tierExact, sameSize, notNewer                                            int
	version                                                                  int // higher is better, as a tie-break
}

func (a score) better(b score) bool {
	for _, p := range [][2]int{
		{a.sameAPI, b.sameAPI}, {a.sameVendor, b.sameVendor}, {a.sameFamily, b.sameFamily},
		{a.thinkingOK, b.thinkingOK}, {a.tierOverlap, b.tierOverlap}, {a.sameIDThinking, b.sameIDThinking},
		{-a.versionDist, -b.versionDist}, {a.tierExact, b.tierExact}, {a.sameSize, b.sameSize},
		{a.notNewer, b.notNewer}, {a.version, b.version},
	} {
		if p[0] != p[1] {
			return p[0] > p[1]
		}
	}
	return false
}

// noVersion is the distance between IDs whose versions cannot be compared.
const noVersion = 1 << 20

// SelectTemplate picks the covered model whose cases a new model should be
// recorded with (contract-recorder -from <template> -as <new>). The
// heuristics, in order of weight:
//
//  1. Bedrock only: the same API. The adapter sends inference profiles to
//     Converse and base IDs to InvokeModel; their fixtures and allowlists are
//     not interchangeable.
//  2. The same vendor (Bedrock vendor, or the vendor/ prefix of a Groq ID).
//  3. The same family: gemini, gpt, claude, command-a, nova, gpt-oss.
//  4. Thinking support, when the provider's API states it for both models
//     (Gemini "thinking", Anthropic capabilities.thinking): a thinking
//     model's cases (thinking blocks, budgets) are not cloned onto a model
//     without it, or the other way round, while a compatible one exists.
//  5. A shared tier word (flash, pro, argon, sol, opus, lite...): flash-lite
//     and flash share "flash"; argon and flash share nothing.
//  6. The same reasoning hint in the ID (command-a-reasoning vs command-a).
//  7. The nearest version (3.8 → 3.9 beats 3.1 → 3.9), else the nearest
//     snapshot date.
//  8. Then an exact tier match, the same parameter size, a template no newer
//     than the new model, the newer template, and finally the ID.
//
// Before any of these, a candidate must be of the new model's kind
// (ModelKind: chat, embeddings, images, speech, transcription, rerank, ocr,
// completions): a template's cases drive one endpoint class.
//
// ok is false when no candidate is of the new model's kind.
func SelectTemplate(section, newID string, newMeta *Model, candidates []Candidate) (Choice, bool) {
	kind := ModelKind(newID, newMeta)
	var cands []Candidate
	for _, c := range candidates {
		if contains(c.kindsOf(), kind) {
			cands = append(cands, c)
		}
	}
	if len(cands) == 0 {
		return Choice{}, false
	}
	n := ParseModelID(section, newID)
	nThink := thinkingOf(n, newMeta)

	var best *Candidate
	var bestScore score
	var bestID ModelID
	sort.Slice(cands, func(i, j int) bool { return cands[i].ID < cands[j].ID })
	for i := range cands {
		c := &cands[i]
		t := ParseModelID(section, c.ID)
		sc := scoreOf(n, t, nThink, thinkingOf(t, c.Meta))
		if best == nil {
			best, bestScore, bestID = c, sc, t
			continue
		}
		if sc.better(bestScore) {
			best, bestScore, bestID = c, sc, t
		}
	}
	ch := Choice{Template: best.ID}
	ch.Reason, ch.Warnings = explain(section, n, bestID, bestScore, nThink, thinkingOf(bestID, best.Meta))
	if sameModel(n, bestID) {
		ch.AliasOf = best.ID
	}
	return ch, true
}

// thinkingOf is "yes"/"no" from the provider's metadata when it says, else
// the ID's own hint, tagged so the selector knows which is authoritative.
type thinking struct {
	value  string // "yes", "no", ""
	stated bool   // from the provider's API
}

func thinkingOf(id ModelID, meta *Model) thinking {
	if meta != nil && meta.Thinking != nil {
		if *meta.Thinking {
			return thinking{"yes", true}
		}
		return thinking{"no", true}
	}
	return thinking{id.Thinking, false}
}

func scoreOf(n, t ModelID, nThink, tThink thinking) score {
	var s score
	s.sameAPI = b2i(n.API == t.API)
	s.sameVendor = b2i(n.Vendor == t.Vendor)
	s.sameFamily = b2i(n.Family == t.Family)
	s.thinkingOK = b2i(!nThink.stated || !tThink.stated || nThink.value == tThink.value)
	s.tierOverlap = b2i(len(n.Tiers) == 0 && len(t.Tiers) == 0 || overlap(n.Tiers, t.Tiers))
	s.sameIDThinking = b2i(n.Thinking == t.Thinking)
	nv, nok := n.versionValue()
	tv, tok := t.versionValue()
	switch {
	case nok && tok:
		s.versionDist = abs(nv - tv)
		s.notNewer = b2i(tv <= nv)
	case !nok && !tok && n.dateValue() > 0 && t.dateValue() > 0:
		s.versionDist = monthsApart(n.dateValue(), t.dateValue())
		s.notNewer = b2i(t.dateValue() <= n.dateValue())
	default:
		s.versionDist = noVersion
	}
	s.version = tv
	s.tierExact = b2i(strings.Join(n.Tiers, ",") == strings.Join(t.Tiers, ","))
	s.sameSize = b2i(strings.Join(n.Sizes, ",") == strings.Join(t.Sizes, ","))
	return s
}

// sameModel reports whether two IDs differ only in snapshot date or Bedrock
// geography: gpt-6-sol and gpt-6-sol-2026-11-02, or us. and global. profiles
// of one model on the same API.
func sameModel(a, b ModelID) bool {
	return a.API == b.API && a.Vendor == b.Vendor && a.Family == b.Family &&
		a.VersionString() == b.VersionString() && a.Thinking == b.Thinking &&
		strings.Join(a.Tiers, ",") == strings.Join(b.Tiers, ",") &&
		strings.Join(a.Sizes, ",") == strings.Join(b.Sizes, ",")
}

func explain(section string, n, t ModelID, s score, nThink, tThink thinking) (string, []string) {
	var parts, warns []string
	if section == "bedrock" {
		if s.sameAPI == 1 {
			parts = append(parts, "same Bedrock API ("+t.API+")")
		} else {
			warns = append(warns, fmt.Sprintf("no covered %s model: the template is %s, so its allowlists may not fit", n.API, t.API))
		}
	}
	if n.Vendor != "" && s.sameVendor == 1 {
		parts = append(parts, "vendor "+t.Vendor)
	}
	if s.sameFamily == 1 {
		parts = append(parts, "family "+familyName(t))
	} else {
		parts = append(parts, fmt.Sprintf("no covered %s model; nearest other family %s", familyName(n), familyName(t)))
	}
	switch {
	case nThink.stated && tThink.stated && s.thinkingOK == 1:
		parts = append(parts, "thinking "+nThink.value+" for both (per the API)")
	case s.thinkingOK == 0:
		warns = append(warns, fmt.Sprintf("the API says thinking %s for the new model and %s for the template; no covered model matches", nThink.value, tThink.value))
	}
	switch {
	case s.tierExact == 1 && len(t.Tiers) > 0:
		parts = append(parts, "same tier "+strings.Join(t.Tiers, "-"))
	case s.tierOverlap == 1 && len(t.Tiers) > 0:
		parts = append(parts, fmt.Sprintf("shares tier %s (%s vs %s)", strings.Join(intersect(n.Tiers, t.Tiers), "-"),
			strings.Join(n.Tiers, "-"), strings.Join(t.Tiers, "-")))
	case len(n.Tiers) > 0:
		parts = append(parts, "no covered model shares tier "+strings.Join(n.Tiers, "-"))
	}
	switch {
	case s.versionDist == 0:
		if v := t.VersionString(); v != "" {
			parts = append(parts, "same version "+v)
		}
	case s.versionDist < noVersion && len(n.Version) > 0:
		parts = append(parts, fmt.Sprintf("nearest version %s → %s", t.VersionString(), n.VersionString()))
	case s.versionDist < noVersion:
		parts = append(parts, fmt.Sprintf("nearest snapshot %s → %s", t.Date, n.Date))
	}
	if sameModel(n, t) {
		parts = append(parts, "differs only in snapshot date or geography: consider an alias entry instead")
	}
	return strings.Join(parts, "; "), warns
}

func familyName(m ModelID) string {
	if m.Family == "" {
		return "(unnamed)"
	}
	return m.Family
}

func overlap(a, b []string) bool { return len(intersect(a, b)) > 0 }

func intersect(a, b []string) []string {
	var out []string
	for _, x := range a {
		if contains(b, x) {
			out = append(out, x)
		}
	}
	return out
}

func monthsApart(a, b int) int {
	ma := (a/10000)*12 + (a/100)%100
	mb := (b/10000)*12 + (b/100)%100
	return abs(ma - mb)
}

func b2i(b bool) int {
	if b {
		return 1
	}
	return 0
}

func abs(x int) int {
	if x < 0 {
		return -x
	}
	return x
}

// FamilyKey groups covered models for -newest: models sharing it are the
// same line, and only the newest of them is re-recorded daily.
func FamilyKey(section, id string) string {
	m := ParseModelID(section, id)
	return strings.Join([]string{m.API, m.Vendor, m.Family, strings.Join(m.Tiers, "+"), m.Thinking}, "|")
}

// Newer reports whether a is a newer release than b within a family: by
// version, then snapshot date, then ID.
func Newer(section, a, b string) bool {
	ma, mb := ParseModelID(section, a), ParseModelID(section, b)
	va, _ := ma.versionValue()
	vb, _ := mb.versionValue()
	if va != vb {
		return va > vb
	}
	if da, db := ma.dateValue(), mb.dateValue(); da != db {
		return da > db
	}
	return a > b
}
