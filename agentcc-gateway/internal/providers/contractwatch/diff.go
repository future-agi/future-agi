package contractwatch

import (
	"fmt"
	"sort"
	"strings"
)

// SectionState is how a section's listing went.
type SectionState string

const (
	StateListed  SectionState = "listed"  // the API (or the static list) answered
	StateSkipped SectionState = "skipped" // credentials missing, or not selected
	StateError   SectionState = "error"   // the listing failed
	StateNone    SectionState = "unlisted"
)

// Finding is one model the diff has something to say about.
type Finding struct {
	Section string `json:"provider"`
	Model   string `json:"model"`
	Status  Status `json:"status,omitempty"`
	Detail  string `json:"detail"`
}

// SectionResult is one section's diff.
type SectionResult struct {
	Name   string       `json:"provider"`
	State  SectionState `json:"state"`
	Detail string       `json:"detail,omitempty"`
	Listed int          `json:"listed"`
	// New are listed models with no entry, no matching rule and no reason
	// in their own metadata to be ignored, newest first.
	New []Model `json:"new,omitempty"`
	// Counts of listed models by how they were classified.
	Covered int `json:"covered"`
	Aliases int `json:"aliases"`
	// GeoAliases are Bedrock profiles that differ from an entry only in
	// geography: the same model on the same API, so they take its status.
	GeoAliases  int `json:"geo_aliases"`
	Ignored     int `json:"ignored"`
	RuleIgnored int `json:"rule_ignored"`
	AutoIgnored int `json:"auto_ignored"`
	// Retired are covered or alias entries upstream no longer lists: their
	// fixtures can no longer be re-recorded.
	Retired []Finding `json:"retired,omitempty"`
	// Deprecating are covered or alias entries upstream lists as deprecated
	// or with a shutdown date.
	Deprecating []Finding `json:"deprecating,omitempty"`
	// Gone are ignored entries upstream no longer lists (safe to prune).
	Gone []Finding `json:"gone,omitempty"`
	// UnusedRules matched nothing upstream listed.
	UnusedRules []string `json:"unused_rules,omitempty"`
	// Invalid are listed IDs with characters model-watch refuses to pass on.
	Invalid []string `json:"invalid,omitempty"`
	// Meta is every listed model's metadata, by ID, for the selector.
	Meta map[string]*Model `json:"-"`
}

// DiffSection classifies every listed model against the section: an explicit
// entry wins, then an ignore rule, then the model's own metadata (AutoIgnore);
// anything left is NEW.
func DiffSection(name string, s *Section, listed []Model) SectionResult {
	r := SectionResult{Name: name, State: StateListed, Meta: map[string]*Model{}}
	usedRules := map[string]bool{}
	seen := map[string]bool{}
	for i := range listed {
		m := listed[i]
		if !ValidID(m.ID) {
			r.Invalid = append(r.Invalid, fmt.Sprintf("%q", m.ID))
			continue
		}
		if seen[m.ID] {
			continue // a provider listing an ID twice is not news
		}
		seen[m.ID] = true
		r.Listed++
		r.Meta[m.ID] = &listed[i]
		if e, ok := s.Entry(m.ID); ok {
			switch e.Status {
			case Covered:
				r.Covered++
			case Alias:
				r.Aliases++
			case Ignored:
				r.Ignored++
			}
			if e.Status != Ignored {
				if d := deprecation(m); d != "" {
					r.Deprecating = append(r.Deprecating, Finding{Section: name, Model: m.ID, Status: e.Status, Detail: d})
				}
			}
			continue
		}
		if rule, ok := s.MatchRule(m.ID); ok {
			r.RuleIgnored++
			usedRules[rule.Match] = true
			continue
		}
		if m.AutoIgnore != "" {
			r.AutoIgnored++
			continue
		}
		if e, ok := geoEntry(name, s, m.ID); ok {
			r.GeoAliases++
			if e.Status != Ignored {
				if d := deprecation(m); d != "" {
					r.Deprecating = append(r.Deprecating, Finding{Section: name, Model: m.ID, Status: Alias, Detail: d})
				}
			}
			continue
		}
		r.New = append(r.New, m)
	}
	r.New = foldGeoVariants(name, r.New)
	for _, e := range s.Models {
		if seen[e.ID] {
			continue
		}
		switch e.Status {
		case Covered, Alias:
			r.Retired = append(r.Retired, Finding{Section: name, Model: e.ID, Status: e.Status,
				Detail: "no longer listed upstream; its fixtures cannot be re-recorded. Check the provider's deprecation notice, then retire the fixtures or keep them as a frozen record"})
		case Ignored:
			r.Gone = append(r.Gone, Finding{Section: name, Model: e.ID, Status: e.Status, Detail: "no longer listed; the entry can be removed"})
		}
	}
	for _, rule := range s.IgnoreRules {
		if !usedRules[rule.Match] {
			r.UnusedRules = append(r.UnusedRules, rule.Match)
		}
	}
	SortNewest(name, r.New)
	return r
}

// GeoKey is a Bedrock inference profile's identity without its geography:
// the API the adapter routes it to and the ID after the geo prefix
// ("converse|anthropic.claude-sonnet-4-6" for us. and global. alike). It is
// "" for an ID with no geo prefix, and outside bedrock.
func GeoKey(section, id string) string {
	if section != "bedrock" {
		return ""
	}
	m := ParseModelID(section, id)
	if m.Geo == "" {
		return ""
	}
	return m.API + "|" + strings.TrimPrefix(strings.ToLower(id), m.Geo+".")
}

// geoEntry finds an explicit entry for another geography of id.
func geoEntry(section string, s *Section, id string) (Entry, bool) {
	k := GeoKey(section, id)
	if k == "" {
		return Entry{}, false
	}
	for _, e := range s.Models {
		if e.ID != id && GeoKey(section, e.ID) == k {
			return e, true
		}
	}
	return Entry{}, false
}

// geoRank orders the geography kept when variants fold: global routing
// first, then us, then the rest by name.
func geoRank(section, id string) string {
	switch ParseModelID(section, id).Geo {
	case "global":
		return "0"
	case "us":
		return "1"
	}
	return "2" + id
}

// foldGeoVariants keeps one NEW entry per Bedrock model and API, listing the
// other geographies under Variants, so one model is one issue and one
// recording.
func foldGeoVariants(section string, ms []Model) []Model {
	var out []Model
	at := map[string]int{}
	for _, m := range ms {
		k := GeoKey(section, m.ID)
		if k == "" {
			out = append(out, m)
			continue
		}
		i, seen := at[k]
		if !seen {
			at[k] = len(out)
			out = append(out, m)
			continue
		}
		keep, other := out[i], m
		if geoRank(section, m.ID) < geoRank(section, keep.ID) {
			keep, other = m, keep
			keep.Variants = append(keep.Variants, other.Variants...)
			other.Variants = nil
		}
		keep.Variants = append(keep.Variants, other.ID)
		sort.Strings(keep.Variants)
		out[i] = keep
	}
	return out
}

// deprecation says whether upstream flags a model as on its way out.
func deprecation(m Model) string {
	switch {
	case m.Lifecycle == "deprecated" || m.Lifecycle == "LEGACY":
		if m.Retires != "" {
			return fmt.Sprintf("upstream lifecycle %s, retires %s", m.Lifecycle, m.Retires)
		}
		return "upstream lifecycle " + m.Lifecycle
	case m.Retires != "":
		return "upstream shutdown date " + m.Retires
	}
	return ""
}

// SortNewest orders models newest first: by the provider's created date when
// it gives one, then by the version and date in the ID, then by ID.
func SortNewest(section string, ms []Model) {
	sort.SliceStable(ms, func(i, j int) bool {
		a, b := ms[i], ms[j]
		switch {
		case a.Created != nil && b.Created != nil && !a.Created.Equal(*b.Created):
			return a.Created.After(*b.Created)
		case (a.Created != nil) != (b.Created != nil):
			return a.Created != nil
		}
		if a.ID == b.ID {
			return false
		}
		ma, mb := ParseModelID(section, a.ID), ParseModelID(section, b.ID)
		va, _ := ma.versionValue()
		vb, _ := mb.versionValue()
		if va != vb {
			return va > vb
		}
		if da, db := ma.dateValue(), mb.dateValue(); da != db {
			return da > db
		}
		return a.ID < b.ID
	})
}
