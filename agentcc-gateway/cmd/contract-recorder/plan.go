package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// PatchFile, in a case directory, is a JSON merge patch (RFC 7396) the
// recorder applies to the request body the adapter sends upstream, for
// provider knobs the canonical request cannot carry yet (e.g. Anthropic
// "thinking", gap R1). Contract tests ignore it; it only shapes recordings.
const PatchFile = "upstream_patch.json"

// caseRef is one case directory in the repo.
type caseRef struct {
	Provider string
	Model    string // real model ID (the directory name decoded)
	Name     string
	Dir      string
	Stream   bool
	Request  []byte // request.json as committed
	Patch    []byte // upstream_patch.json, nil when absent
}

// modelRef is one model directory in the repo.
type modelRef struct {
	Provider string
	Model    string
	Dir      string
	Cases    []caseRef
}

// contractsRoot is where provider keeps its fixtures in the repo.
func contractsRoot(providersDir, provider string) string {
	return filepath.Join(providersDir, provider, filepath.FromSlash(contracttest.DefaultRoot))
}

// providersWithFixtures lists the provider directories that have contract
// fixtures, sorted.
func providersWithFixtures(providersDir string) ([]string, error) {
	entries, err := os.ReadDir(providersDir)
	if err != nil {
		return nil, err
	}
	var names []string
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		if st, err := os.Stat(contractsRoot(providersDir, e.Name())); err == nil && st.IsDir() {
			names = append(names, e.Name())
		}
	}
	return names, nil
}

// modelFromDir decodes a model directory name, accepting only the spelling
// contracttest.ModelDir gives, as contracttest.Discover does.
func modelFromDir(dir string) (string, error) {
	model, err := url.PathUnescape(dir)
	if err != nil {
		return "", fmt.Errorf("model directory %q is not valid percent-encoding: %v", dir, err)
	}
	if want := contracttest.ModelDir(model); want != dir {
		return "", fmt.Errorf("model directory %q: the fixtures for model %q must be in %q", dir, model, want)
	}
	return model, nil
}

// loadModels reads every model and case directory of provider.
func loadModels(providersDir, provider string) ([]modelRef, error) {
	root := contractsRoot(providersDir, provider)
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	var out []modelRef
	for _, me := range entries {
		if !me.IsDir() {
			continue
		}
		model, err := modelFromDir(me.Name())
		if err != nil {
			return nil, fmt.Errorf("%s: %v", root, err)
		}
		m := modelRef{Provider: provider, Model: model, Dir: filepath.Join(root, me.Name())}
		caseEntries, err := os.ReadDir(m.Dir)
		if err != nil {
			return nil, err
		}
		for _, ce := range caseEntries {
			if !ce.IsDir() {
				continue
			}
			c, err := loadCase(provider, model, ce.Name(), filepath.Join(m.Dir, ce.Name()))
			if err != nil {
				return nil, err
			}
			m.Cases = append(m.Cases, c)
		}
		out = append(out, m)
	}
	return out, nil
}

func loadCase(provider, model, name, dir string) (caseRef, error) {
	c := caseRef{Provider: provider, Model: model, Name: name, Dir: dir}
	reqPath := filepath.Join(dir, contracttest.RequestFile)
	raw, err := os.ReadFile(reqPath)
	if err != nil {
		return c, err
	}
	var head struct {
		Stream bool `json:"stream"`
	}
	if err := json.Unmarshal(raw, &head); err != nil {
		return c, fmt.Errorf("%s: %v", reqPath, err)
	}
	c.Stream, c.Request = head.Stream, raw

	patchPath := filepath.Join(dir, PatchFile)
	patch, err := os.ReadFile(patchPath)
	switch {
	case errors.Is(err, fs.ErrNotExist):
	case err != nil:
		return c, err
	default:
		var obj map[string]any
		if err := json.Unmarshal(patch, &obj); err != nil {
			return c, fmt.Errorf("%s: must be a JSON object (an RFC 7396 merge patch): %v", patchPath, err)
		}
		c.Patch = patch
	}
	return c, nil
}

// splitList splits a comma-separated flag value, dropping empty items.
func splitList(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// matchAny reports whether s matches one of the glob patterns (path.Match
// syntax); no patterns matches everything.
func matchAny(patterns []string, s string) (bool, error) {
	if len(patterns) == 0 {
		return true, nil
	}
	for _, p := range patterns {
		ok, err := path.Match(p, s)
		if err != nil {
			return false, fmt.Errorf("bad pattern %q: %v", p, err)
		}
		if ok {
			return true, nil
		}
	}
	return false, nil
}

// selection is what to record for one provider.
type selection struct {
	Models []string // globs over real model IDs
	Cases  []string // globs over case names
	From   string   // clone this model's cases ...
	As     string   // ... into this new model ID
	Out    string   // "" records in place
}

func (s selection) cloning() bool { return s.From != "" }

// job is one case to record and where its files go.
type job struct {
	caseRef
	// Target is the fixture model key recorded: the case's own, or -as.
	Target string
	// Upstream names where Target is recorded (see upstreamFor): the
	// provider itself, or an OpenAI-compatible preset.
	Upstream string
	// OutDir receives the response file (and, when cloning, request.json
	// and any upstream_patch.json).
	OutDir string
	// Body is the request to send: request.json, with "model" set to the
	// upstream's model ID for Target when cloning.
	Body []byte
	// WriteRequest is true when OutDir needs its own request.json: a cloned
	// model exists nowhere else.
	WriteRequest bool
}

// ResponseFile is the fixture file name the case's body is saved under.
func (j job) ResponseFile() string { return responseFile(j.Provider, j.Stream) }

// metaWrite is a meta.json the recorder writes once its jobs have run.
type metaWrite struct {
	Path string // meta.json to write
	Src  string // meta.json to start from
	// Model is the model ID written into it.
	Model string
	// ClonedFrom is the --from model when the meta is new.
	ClonedFrom string
	// Jobs are indexes into plan.Jobs feeding this meta.
	Jobs []int
	// Cases is how many cases the model directory has. An in-place update
	// marks the model recorded only when all of them were.
	Cases int
}

type plan struct {
	Provider string
	Jobs     []job
	Metas    []metaWrite
}

// planProvider selects provider's cases and lays out where each recording
// goes.
func planProvider(providersDir, provider string, sel selection) (*plan, error) {
	models, err := loadModels(providersDir, provider)
	if err != nil {
		return nil, err
	}
	p := &plan{Provider: provider}
	if sel.cloning() {
		return p, p.addClone(providersDir, models, sel)
	}
	for _, m := range models {
		ok, err := matchAny(sel.Models, m.Model)
		if err != nil {
			return nil, err
		}
		if !ok {
			continue
		}
		mw := metaWrite{
			Path:  filepath.Join(m.Dir, contracttest.MetaFile),
			Src:   filepath.Join(m.Dir, contracttest.MetaFile),
			Model: m.Model,
			Cases: len(m.Cases),
		}
		for _, c := range m.Cases {
			ok, err := matchAny(sel.Cases, c.Name)
			if err != nil {
				return nil, err
			}
			if !ok {
				continue
			}
			up, _ := upstreamFor(provider, c.Model)
			mw.Jobs = append(mw.Jobs, len(p.Jobs))
			p.Jobs = append(p.Jobs, job{
				caseRef:  c,
				Target:   c.Model,
				Upstream: up,
				OutDir:   caseDir(sel.Out, providersDir, provider, c.Model, c.Name),
				Body:     c.Request,
			})
		}
		// With --out the repo's meta.json is the allowlist live mode falls
		// back to; only an in-place recording changes it.
		if sel.Out == "" && len(mw.Jobs) > 0 {
			p.Metas = append(p.Metas, mw)
		}
	}
	return p, nil
}

func (p *plan) addClone(providersDir string, models []modelRef, sel selection) error {
	var src *modelRef
	for i := range models {
		if models[i].Model == sel.From {
			src = &models[i]
		}
	}
	if src == nil {
		return fmt.Errorf("--from %q: no model directory %s under %s",
			sel.From, contracttest.ModelDir(sel.From), contractsRoot(providersDir, p.Provider))
	}
	for _, m := range models {
		if m.Model == sel.As {
			return fmt.Errorf("--as %q: %s already exists; record it in place with --model instead", sel.As, m.Dir)
		}
	}
	mw := metaWrite{
		Path:       filepath.Join(modelDir(sel.Out, providersDir, p.Provider, sel.As), contracttest.MetaFile),
		Src:        filepath.Join(src.Dir, contracttest.MetaFile),
		Model:      sel.As,
		ClonedFrom: sel.From,
	}
	for _, c := range src.Cases {
		ok, err := matchAny(sel.Cases, c.Name)
		if err != nil {
			return err
		}
		if !ok {
			continue
		}
		up, upModel := upstreamFor(p.Provider, sel.As)
		body, err := setModel(c.Request, upModel)
		if err != nil {
			return fmt.Errorf("%s: %v", filepath.Join(c.Dir, contracttest.RequestFile), err)
		}
		mw.Jobs = append(mw.Jobs, len(p.Jobs))
		p.Jobs = append(p.Jobs, job{
			caseRef:      c,
			Target:       sel.As,
			Upstream:     up,
			OutDir:       caseDir(sel.Out, providersDir, p.Provider, sel.As, c.Name),
			Body:         body,
			WriteRequest: true,
		})
	}
	mw.Cases = len(mw.Jobs)
	if len(mw.Jobs) > 0 {
		p.Metas = append(p.Metas, mw)
	}
	return nil
}

// modelDir is where model's fixtures go: the repo's testdata/contracts, or
// <out>/<provider>/ — the layout AGENTCC_CONTRACT_FIXTURES_DIR expects. Model
// directories are always contracttest.ModelDir(model).
func modelDir(out, providersDir, provider, model string) string {
	if out == "" {
		return filepath.Join(contractsRoot(providersDir, provider), contracttest.ModelDir(model))
	}
	return filepath.Join(out, provider, contracttest.ModelDir(model))
}

func caseDir(out, providersDir, provider, model, name string) string {
	return filepath.Join(modelDir(out, providersDir, provider, model), name)
}

// responseFile names a recorded body: Bedrock streams are AWS event-stream,
// other streams SSE, everything else JSON.
func responseFile(provider string, stream bool) string {
	switch {
	case !stream:
		return contracttest.ResponseJSONFile
	case provider == "bedrock":
		return contracttest.ResponseEventStreamFile
	default:
		return contracttest.ResponseSSEFile
	}
}
