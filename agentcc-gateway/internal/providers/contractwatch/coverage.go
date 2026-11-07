package contractwatch

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// ContractsRoot is where a provider package keeps its fixtures, relative to
// the package directory: contracttest.DefaultRoot. (This package does not
// import contracttest, which is test support that registers -update on the
// global flag set; coverage_test.go pins the two together.)
const ContractsRoot = "testdata/contracts"

// metaFile is contracttest.MetaFile.
const metaFile = "meta.json"

// requestFile is contracttest.RequestFile.
const requestFile = "request.json"

// ModelDir is contracttest.ModelDir: model with every byte outside RFC 3986's
// unreserved set percent-encoded, uppercase hex. coverage_test.go checks the
// two agree.
func ModelDir(model string) string {
	var b strings.Builder
	for i := 0; i < len(model); i++ {
		c := model[i]
		if 'a' <= c && c <= 'z' || 'A' <= c && c <= 'Z' || '0' <= c && c <= '9' ||
			c == '-' || c == '.' || c == '_' || c == '~' {
			b.WriteByte(c)
		} else {
			fmt.Fprintf(&b, "%%%02X", c)
		}
	}
	return b.String()
}

// modelFromDir decodes a model directory name, accepting only ModelDir's
// spelling, as contracttest.Discover does.
func modelFromDir(dir string) (string, error) {
	model, err := url.PathUnescape(dir)
	if err != nil {
		return "", fmt.Errorf("model directory %q is not valid percent-encoding: %v", dir, err)
	}
	if want := ModelDir(model); want != dir {
		return "", fmt.Errorf("model directory %q: the fixtures for model %q must be in %q", dir, model, want)
	}
	return model, nil
}

// Fixture is one model directory under some provider's testdata/contracts.
type Fixture struct {
	Provider string // adapter directory name, e.g. "openai"
	Key      string // decoded model key, e.g. "groq@openai/gpt-oss-120b"
	Dir      string
}

// FindFixtures lists every model directory under
// <providersDir>/*/testdata/contracts, sorted by provider and key.
func FindFixtures(providersDir string) ([]Fixture, []error) {
	entries, err := os.ReadDir(providersDir)
	if err != nil {
		return nil, []error{err}
	}
	var out []Fixture
	var errs []error
	for _, pe := range entries {
		if !pe.IsDir() {
			continue
		}
		root := filepath.Join(providersDir, pe.Name(), filepath.FromSlash(ContractsRoot))
		models, err := os.ReadDir(root)
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			errs = append(errs, err)
			continue
		}
		for _, me := range models {
			if !me.IsDir() {
				continue
			}
			key, err := modelFromDir(me.Name())
			if err != nil {
				errs = append(errs, fmt.Errorf("%s: %v", root, err))
				continue
			}
			out = append(out, Fixture{Provider: pe.Name(), Key: key, Dir: filepath.Join(root, me.Name())})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Provider != out[j].Provider {
			return out[i].Provider < out[j].Provider
		}
		return out[i].Key < out[j].Key
	})
	return out, errs
}

// StaleKind classifies a coverage problem.
type StaleKind string

const (
	// StaleNoFixtures: a covered entry whose model directory is missing.
	StaleNoFixtures StaleKind = "covered_without_fixtures"
	// StaleNoEntry: a model directory no covered entry claims.
	StaleNoEntry StaleKind = "fixtures_without_entry"
	// StaleBadDir: a model directory whose name does not decode.
	StaleBadDir StaleKind = "bad_model_dir"
)

// Stale is one disagreement between the registry and the fixtures.
type Stale struct {
	Kind    StaleKind `json:"kind"`
	Section string    `json:"section,omitempty"`
	Model   string    `json:"model,omitempty"`
	Dir     string    `json:"dir"`
	Detail  string    `json:"detail"`
}

func (s Stale) String() string { return fmt.Sprintf("%s: %s", s.Kind, s.Detail) }

// CheckCoverage compares the registry's covered entries with the model
// directories under providersDir. Every covered entry needs its model
// directory (with a meta.json); every model directory needs exactly one
// covered entry. It needs no network.
func CheckCoverage(reg *Registry, providersDir string) []Stale {
	var out []Stale
	fixtures, errs := FindFixtures(providersDir)
	for _, err := range errs {
		out = append(out, Stale{Kind: StaleBadDir, Dir: providersDir, Detail: err.Error()})
	}
	type ref struct{ provider, key string }
	claimed := map[ref]string{} // fixture -> section/id claiming it
	for _, name := range reg.Names() {
		s := reg.Providers[name]
		for _, id := range s.Covered() {
			prov, key := s.FixtureProvider(name), s.FixtureKey(id)
			dir := filepath.Join(providersDir, prov, filepath.FromSlash(ContractsRoot), ModelDir(key))
			r := ref{prov, key}
			if other, dup := claimed[r]; dup {
				out = append(out, Stale{Kind: StaleNoEntry, Section: name, Model: id, Dir: dir,
					Detail: fmt.Sprintf("%s/%s and %s both claim %s", name, id, other, dir)})
				continue
			}
			claimed[r] = name + "/" + id
			if _, err := os.Stat(filepath.Join(dir, metaFile)); err != nil {
				out = append(out, Stale{Kind: StaleNoFixtures, Section: name, Model: id, Dir: dir,
					Detail: fmt.Sprintf("%s/%s is covered, but %s has no %s; record it (contract-recorder -from/-as) or change its status", name, id, dir, metaFile)})
			}
		}
	}
	for _, f := range fixtures {
		if _, ok := claimed[ref{f.Provider, f.Key}]; !ok {
			out = append(out, Stale{Kind: StaleNoEntry, Model: f.Key, Dir: f.Dir,
				Detail: fmt.Sprintf("%s has fixtures for %q, but no registry section lists it as covered; add it to %s", f.Dir, f.Key, RegistryFile)})
		}
	}
	return out
}

// caseFile is contracttest.CaseFile, the optional per-case spec.
const caseFile = "case.json"

// CaseInfo is one case directory of a covered model.
type CaseInfo struct {
	Name string
	// Secondary is true for a case that is not a plain 2xx chat case (an
	// error status or another endpoint in its case.json): it is cloned
	// after every plain one.
	Secondary bool
}

// TemplateCases lists the recordable case directories (those with a
// request.json, and no case.json saying "recordable": false) of a covered
// model, sorted by name. case.json is read leniently, only for the fields
// that matter here; contracttest validates it.
func TemplateCases(providersDir, fixtureProvider, key string) ([]CaseInfo, error) {
	dir := filepath.Join(providersDir, fixtureProvider, filepath.FromSlash(ContractsRoot), ModelDir(key))
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var cases []CaseInfo
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		if _, err := os.Stat(filepath.Join(dir, e.Name(), requestFile)); err != nil {
			continue
		}
		var spec struct {
			Endpoint   string `json:"endpoint"`
			Status     int    `json:"status"`
			Recordable *bool  `json:"recordable"`
		}
		if raw, err := os.ReadFile(filepath.Join(dir, e.Name(), caseFile)); err == nil {
			if err := json.Unmarshal(raw, &spec); err != nil {
				return nil, fmt.Errorf("%s: %v", filepath.Join(dir, e.Name(), caseFile), err)
			}
		}
		if spec.Recordable != nil && !*spec.Recordable {
			continue
		}
		cases = append(cases, CaseInfo{
			Name:      e.Name(),
			Secondary: spec.Status >= 300 || (spec.Endpoint != "" && spec.Endpoint != "chat"),
		})
	}
	sort.Slice(cases, func(i, j int) bool { return cases[i].Name < cases[j].Name })
	return cases, nil
}
