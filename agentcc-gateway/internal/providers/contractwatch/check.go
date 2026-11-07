package contractwatch

import (
	"context"
	"fmt"
	"net/http"
	"regexp"
	"strings"
	"time"
)

// StaticMaxAge is how old a static list's "checked" date may get before the
// report asks for the provider's docs to be checked again.
const StaticMaxAge = 90 * 24 * time.Hour

// CheckOptions configure Check.
type CheckOptions struct {
	// Env reads credentials (os.Getenv in the CLI).
	Env func(string) string
	// Client makes every upstream call. Required: Check never falls back to
	// http.DefaultClient, so tests control all I/O.
	Client *http.Client
	// Sections limits the run to these registry sections; empty means all.
	Sections []string
	// Timeout bounds each section's listing.
	Timeout time.Duration
	// Now is the clock for the static-list age check.
	Now func() time.Time
	// Listers overrides Specs' lister for a section (tests).
	Listers map[string]Lister
	Plan    PlanOptions
}

// Report is everything one -check run found.
type Report struct {
	Sections     []SectionResult `json:"providers"`
	Plan         []PlanEntry     `json:"plan"`
	Stale        []Stale         `json:"stale,omitempty"`
	Placeholders []string        `json:"placeholders,omitempty"`
	// Notes are reminders that need a person (an old static list).
	Notes []string `json:"notes,omitempty"`
}

// NewCount is how many NEW models the report holds.
func (r *Report) NewCount() int { return len(r.Plan) }

// Failed reports whether the run needs attention: a NEW model, a listing
// that failed, or stale coverage. RETIRED and deprecations are warnings.
func (r *Report) Failed() bool {
	if len(r.Plan) > 0 || len(r.Stale) > 0 {
		return true
	}
	for _, s := range r.Sections {
		if s.State == StateError || len(s.Invalid) > 0 {
			return true
		}
	}
	return false
}

var regionPattern = regexp.MustCompile(`^[a-z]{2}(-[a-z]+)+-\d+$`)

// Check lists every selected section the environment has credentials for,
// diffs each listing against the registry, checks coverage offline, and
// plans a template for each NEW model.
func Check(ctx context.Context, reg *Registry, opts CheckOptions) *Report {
	rep := &Report{}
	now := time.Now
	if opts.Now != nil {
		now = opts.Now
	}
	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = time.Minute
	}
	for _, name := range reg.Names() {
		if len(opts.Sections) > 0 && !contains(opts.Sections, name) {
			continue
		}
		s := reg.Providers[name]
		spec := Specs[name]
		if s.List == ListNone {
			rep.Sections = append(rep.Sections, SectionResult{Name: name, State: StateNone, Detail: s.Note})
			continue
		}
		if missing := spec.MissingEnv(opts.Env); len(missing) > 0 {
			detail := strings.Join(missing, ", ") + " not set"
			if spec.Help != "" {
				detail += " (" + spec.Help + ")"
			}
			rep.Sections = append(rep.Sections, SectionResult{Name: name, State: StateSkipped, Detail: detail})
			continue
		}
		var lister Lister
		switch {
		case opts.Listers[name] != nil:
			lister = opts.Listers[name]
		case s.List == ListStatic:
			lister = StaticLister{Models: s.Static.Models}
			if checked, err := time.Parse("2006-01-02", s.Static.Checked); err == nil && now().Sub(checked) > StaticMaxAge {
				rep.Notes = append(rep.Notes, fmt.Sprintf("%s: the static model list was last checked on %s; compare it with %s and update static.checked",
					name, s.Static.Checked, s.Static.Source))
			}
		default:
			if name == "bedrock" && !regionPattern.MatchString(BedrockRegion(opts.Env)) {
				rep.Sections = append(rep.Sections, SectionResult{Name: name, State: StateError,
					Detail: fmt.Sprintf("region %q does not look like an AWS region", BedrockRegion(opts.Env))})
				continue
			}
			lister = spec.NewLister(opts.Env, opts.Client)
		}
		lctx, cancel := context.WithTimeout(ctx, timeout)
		models, err := lister.List(lctx)
		cancel()
		if err != nil {
			rep.Sections = append(rep.Sections, SectionResult{Name: name, State: StateError, Detail: err.Error()})
			continue
		}
		res := DiffSection(name, s, models)
		if s.List == ListStatic {
			res.Detail = "static list checked " + s.Static.Checked
		}
		rep.Sections = append(rep.Sections, res)
	}
	rep.Stale = CheckCoverage(reg, opts.Plan.ProvidersDir)
	rep.Placeholders = reg.Placeholders()
	rep.Plan = BuildPlan(reg, rep.Sections, opts.Plan)
	return rep
}

// CredentialedSections are the selected sections whose credentials env
// sets, sorted; -newest re-records only those.
func CredentialedSections(reg *Registry, env func(string) string, only []string) (have []string, skipped map[string]string) {
	skipped = map[string]string{}
	for _, name := range reg.Names() {
		if len(only) > 0 && !contains(only, name) {
			continue
		}
		if missing := Specs[name].MissingEnv(env); len(missing) > 0 {
			skipped[name] = strings.Join(missing, ", ") + " not set"
			continue
		}
		have = append(have, name)
	}
	return have, skipped
}
