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
	"slices"
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/providers/contracttest"
)

// PatchFile, in a case directory, is a JSON merge patch (RFC 7396) the
// recorder applies to the request body the adapter sends upstream, for
// provider knobs the canonical request cannot carry yet (e.g. Anthropic
// "thinking", gap R1), or to induce a recordable error case's status.
// Contract tests ignore it; it only shapes recordings.
const PatchFile = "upstream_patch.json"

// caseRef is one case directory in the repo.
type caseRef struct {
	Provider string
	Model    string // real model ID (the directory name decoded)
	Name     string
	Dir      string
	Request  []byte // request.json as committed
	Patch    []byte // upstream_patch.json, nil when absent
	// Spec is case.json; the zero CaseSpec when the case has none (HasSpec
	// false): a 200 chat case.
	Spec    contracttest.CaseSpec
	HasSpec bool
	// SpecRaw is case.json as committed, nil when absent.
	SpecRaw []byte
	// Endpoint is Spec.Endpoint, default chat.
	Endpoint string
	// RespFile is the response file the case holds, "" for a case that has
	// none yet (and for a clone's job); a recording keeps its kind.
	RespFile string
	// Stream is whether the request asks for a stream (see streamOf).
	Stream bool
	// Input is a multipart endpoint's upload (contracttest.InputFile).
	Input *contracttest.InputFile
	// Bad says why the case cannot be recorded as it is (a multipart case
	// without its audio, a malformed passthrough request): the case fails
	// when its turn comes, and the rest of the plan still runs.
	Bad error
}

// wantStatus is the status case.json expects: 200 unless it says otherwise.
func (c caseRef) wantStatus() int {
	if c.Spec.Status == 0 {
		return 200
	}
	return c.Spec.Status
}

// isError reports whether the case is an error case (status 400-599).
func (c caseRef) isError() bool { return c.Spec.Status >= 400 }

// modelDirRef is one model directory in the repo.
type modelDirRef struct {
	Provider string
	Model    string
	Dir      string
	Cases    []caseRef
}

// recordable counts the cases a recording can replace: all but those
// case.json marks "recordable": false.
func (m modelDirRef) recordable() int {
	n := 0
	for _, c := range m.Cases {
		if c.Spec.CanRecord() {
			n++
		}
	}
	return n
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
func loadModels(providersDir, provider string) ([]modelDirRef, error) {
	root := contractsRoot(providersDir, provider)
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	var out []modelDirRef
	for _, me := range entries {
		if !me.IsDir() {
			continue
		}
		model, err := modelFromDir(me.Name())
		if err != nil {
			return nil, fmt.Errorf("%s: %v", root, err)
		}
		m := modelDirRef{Provider: provider, Model: model, Dir: filepath.Join(root, me.Name())}
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

// loadCase reads one case directory: request.json, and whichever of
// upstream_patch.json, case.json, a response file and an upload it has. It
// validates them as contracttest does, so the recorder refuses the fixtures
// Discover would. What only recording needs (the upload's bytes, a
// well-formed passthrough request) is checked per case, into Bad.
func loadCase(provider, model, name, dir string) (caseRef, error) {
	c := caseRef{Provider: provider, Model: model, Name: name, Dir: dir}
	reqPath := filepath.Join(dir, contracttest.RequestFile)
	raw, err := os.ReadFile(reqPath)
	if err != nil {
		return c, err
	}
	var head map[string]json.RawMessage
	if err := json.Unmarshal(raw, &head); err != nil {
		return c, fmt.Errorf("%s: not a JSON object: %v", reqPath, err)
	}
	reqStream := false
	if s, ok := head["stream"]; ok {
		if err := json.Unmarshal(s, &reqStream); err != nil {
			return c, fmt.Errorf(`%s: "stream" must be a boolean`, reqPath)
		}
	}
	c.Request = raw

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

	if c.Spec, err = contracttest.ReadCaseSpec(dir); err != nil {
		return c, err
	}
	if c.SpecRaw, err = os.ReadFile(filepath.Join(dir, contracttest.CaseFile)); err == nil {
		c.HasSpec = true
	} else if !errors.Is(err, fs.ErrNotExist) {
		return c, err
	}
	c.Endpoint = c.Spec.Endpoint
	if c.Endpoint == "" {
		c.Endpoint = contracttest.EndpointChat
	}

	var found []string
	for _, f := range contracttest.ResponseFiles() {
		if _, err := os.Stat(filepath.Join(dir, f)); err == nil {
			found = append(found, f)
		}
	}
	switch {
	case len(found) > 1:
		return c, fmt.Errorf("%s has more than one response file: %s", dir, strings.Join(found, ", "))
	case len(found) == 1:
		c.RespFile = found[0]
	}
	if c.isError() && contracttest.IsStreamFile(c.RespFile) {
		return c, fmt.Errorf("%s: an error case (status %d) with a %s; its body is the error, %s or %s",
			dir, c.Spec.Status, c.RespFile, contracttest.ResponseJSONFile, contracttest.ResponseTextFile)
	}

	c.Input, c.Bad = contracttest.ReadInputFile(dir, c.Endpoint, raw)
	if contracttest.PassthroughEndpoint(c.Endpoint) {
		pr, err := contracttest.DecodePassthrough(raw)
		if err != nil && c.Bad == nil {
			c.Bad = err
		}
		reqStream = pr.Stream()
	}
	c.Stream = streamOf(c.Endpoint, reqStream, c.RespFile, name)
	return c, nil
}

// streamOf decides whether a case's request asks for a stream: its "stream"
// key, for every endpoint whose body carries one (for assistants, the
// passthrough body's). A native GenAI request has none (streaming is another
// URL), so for genai_generate it is the case's response file, or for a case
// not recorded yet its name: "stream" as a prefix, the README's convention.
func streamOf(endpoint string, reqStream bool, respFile, name string) bool {
	switch {
	case reqStream:
		return true
	case endpoint != contracttest.EndpointGenAIGenerate:
		return false
	case respFile != "":
		return contracttest.IsStreamFile(respFile)
	default:
		return strings.HasPrefix(name, "stream")
	}
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
	// Endpoints keeps only cases of these endpoints (none: every endpoint);
	// Exclude drops these.
	Endpoints, Exclude []string
	// MaxCases caps the cases recorded per model and endpoint, MaxModels the
	// models per provider and endpoint; 0 is no cap. Rotate shifts where the
	// MaxModels window starts in the sorted model list.
	MaxCases, MaxModels, Rotate int
	From                        string // clone this model's cases ...
	As                          string // ... into this new model ID
	Out                         string // "" records in place
}

func (s selection) cloning() bool { return s.From != "" }

// wants reports whether c passes the -case, -endpoint and -exclude-endpoint
// filters.
func (s selection) wants(c caseRef) (bool, error) {
	ok, err := matchAny(s.Cases, c.Name)
	if err != nil || !ok {
		return false, err
	}
	if len(s.Endpoints) > 0 && !slices.Contains(s.Endpoints, c.Endpoint) {
		return false, nil
	}
	return !slices.Contains(s.Exclude, c.Endpoint), nil
}

// job is one case to record and where its files go.
type job struct {
	caseRef
	// Target is the fixture model key recorded: the case's own, or -as.
	Target string
	// Upstream names where Target is recorded (see upstreamFor): the
	// provider itself, or an OpenAI-compatible preset; UpModel is the model
	// ID that upstream knows.
	Upstream, UpModel string
	// APIVersion is the api-version the adapter is configured with for this
	// case (azure; see apiVersionOf), "" for the adapter's default.
	APIVersion string
	// OutDir receives the response file (and, when cloning, request.json
	// and the case's other inputs).
	OutDir string
	// Body is the request to send: request.json, with "model" set to the
	// upstream's model ID for Target when cloning.
	Body []byte
	// WriteRequest is true when OutDir needs its own request.json: a cloned
	// model exists nowhere else. case.json, the upload and
	// upstream_patch.json go with it.
	WriteRequest bool
	// Live is true for an -out recording, which live mode reads with the
	// repo's case as fallback.
	Live bool
}

// call is what the job sends through its endpoint's capability method.
func (j job) call() call {
	return call{Endpoint: j.Endpoint, Body: j.Body, Model: j.UpModel, Stream: j.Stream, Input: j.Input}
}

// ID names the job in output: "provider model/case".
func (j job) ID() string { return fmt.Sprintf("%s %s/%s", j.Provider, j.Target, j.Name) }

// metaWrite is a meta.json the recorder writes once its jobs have run.
type metaWrite struct {
	Path string // meta.json to write
	Src  string // meta.json to start from
	// Model is the model ID written into it.
	Model string
	// ClonedFrom is the -from model when the meta is new.
	ClonedFrom string
	// Jobs are indexes into plan.Jobs feeding this meta.
	Jobs []int
	// Cases is how many recordable cases the model directory has. An
	// in-place update marks the model recorded only when all of them were.
	Cases int
	// Synthetic counts the model's cases case.json marks not recordable,
	// which stay synthetic whatever is recorded.
	Synthetic int
}

type plan struct {
	Provider string
	Jobs     []job
	Metas    []metaWrite
	// Copies are a clone's not-recordable cases, copied as they are (still
	// synthetic) once the clone has recorded something, so the new model
	// keeps their coverage.
	Copies []job
	// NotRecordable are selected cases case.json marks "recordable": false,
	// which are not sent.
	NotRecordable []caseRef
	// Capped are selected cases left out by -max-cases or -max-models.
	Capped []caseRef
}

// planProvider selects provider's cases and lays out where each recording
// goes.
func planProvider(providersDir, provider string, sel selection) (*plan, error) {
	modelDirs, err := loadModels(providersDir, provider)
	if err != nil {
		return nil, err
	}
	p := &plan{Provider: provider}
	if sel.cloning() {
		return p, p.addClone(providersDir, modelDirs, sel)
	}
	var cands []caseRef
	for _, m := range modelDirs {
		ok, err := matchAny(sel.Models, m.Model)
		if err != nil {
			return nil, err
		}
		if !ok {
			continue
		}
		for _, c := range m.Cases {
			ok, err := sel.wants(c)
			if err != nil {
				return nil, err
			}
			switch {
			case !ok:
			case !c.Spec.CanRecord():
				p.NotRecordable = append(p.NotRecordable, c)
			default:
				cands = append(cands, c)
			}
		}
	}
	keep := capCases(cands, sel.MaxModels, sel.MaxCases, sel.Rotate)

	metas := map[string]int{} // model -> index in p.Metas
	for i, c := range cands {
		if !keep[i] {
			p.Capped = append(p.Capped, c)
			continue
		}
		k, ok := metas[c.Model]
		// With -out the repo's meta.json is the allowlist live mode falls
		// back to; only an in-place recording changes it.
		if !ok && sel.Out == "" {
			m := modelOf(modelDirs, c.Model)
			k = len(p.Metas)
			metas[c.Model] = k
			p.Metas = append(p.Metas, metaWrite{
				Path:      filepath.Join(m.Dir, contracttest.MetaFile),
				Src:       filepath.Join(m.Dir, contracttest.MetaFile),
				Model:     m.Model,
				Cases:     m.recordable(),
				Synthetic: len(m.Cases) - m.recordable(),
			})
		}
		if sel.Out == "" {
			p.Metas[k].Jobs = append(p.Metas[k].Jobs, len(p.Jobs))
		}
		up, upModel := upstreamFor(provider, c.Model)
		p.Jobs = append(p.Jobs, job{
			caseRef:    c,
			Target:     c.Model,
			Upstream:   up,
			UpModel:    upModel,
			APIVersion: apiVersionOf(provider, c.Dir),
			OutDir:     caseDir(sel.Out, providersDir, provider, c.Model, c.Name),
			Body:       c.Request,
			Live:       sel.Out != "",
		})
	}
	return p, nil
}

func modelOf(ms []modelDirRef, model string) modelDirRef {
	for _, m := range ms {
		if m.Model == model {
			return m
		}
	}
	return modelDirRef{}
}

func (p *plan) addClone(providersDir string, modelDirs []modelDirRef, sel selection) error {
	var src *modelDirRef
	for i := range modelDirs {
		if modelDirs[i].Model == sel.From {
			src = &modelDirs[i]
		}
	}
	if src == nil {
		return fmt.Errorf("-from %q: no model directory %s under %s",
			sel.From, contracttest.ModelDir(sel.From), contractsRoot(providersDir, p.Provider))
	}
	for _, m := range modelDirs {
		if m.Model == sel.As {
			return fmt.Errorf("-as %q: %s already exists; record it in place with -model instead", sel.As, m.Dir)
		}
	}
	mw := metaWrite{
		Path:       filepath.Join(modelDir(sel.Out, providersDir, p.Provider, sel.As), contracttest.MetaFile),
		Src:        filepath.Join(src.Dir, contracttest.MetaFile),
		Model:      sel.As,
		ClonedFrom: sel.From,
	}
	up, upModel := upstreamFor(p.Provider, sel.As)
	newJob := func(c caseRef) (job, error) {
		body, err := setModel(c.Request, upModel, modelInBody(c.Endpoint))
		if err != nil {
			return job{}, fmt.Errorf("%s: %v", filepath.Join(c.Dir, contracttest.RequestFile), err)
		}
		return job{
			caseRef:      c,
			Target:       sel.As,
			Upstream:     up,
			UpModel:      upModel,
			APIVersion:   apiVersionOf(p.Provider, c.Dir),
			OutDir:       caseDir(sel.Out, providersDir, p.Provider, sel.As, c.Name),
			Body:         body,
			WriteRequest: true,
			Live:         sel.Out != "",
		}, nil
	}

	var cands []caseRef
	for _, c := range src.Cases {
		ok, err := sel.wants(c)
		if err != nil {
			return err
		}
		switch {
		case !ok:
		case !c.Spec.CanRecord() && sel.Out != "":
			// Live mode ignores a not-recordable case, so a copy of the
			// synthetic body would only be noise there.
			p.NotRecordable = append(p.NotRecordable, c)
		case !c.Spec.CanRecord():
			// A copy keeps the source's response file.
			j, err := newJob(c)
			if err != nil {
				return err
			}
			p.Copies = append(p.Copies, j)
		default:
			cands = append(cands, c)
		}
	}
	keep := capCases(cands, 0, sel.MaxCases, 0)
	for i, c := range cands {
		if !keep[i] {
			p.Capped = append(p.Capped, c)
			continue
		}
		j, err := newJob(c)
		if err != nil {
			return err
		}
		// The new case directory has no response file yet: the recording's
		// Content-Type picks it, not the source's.
		j.RespFile = ""
		mw.Jobs = append(mw.Jobs, len(p.Jobs))
		p.Jobs = append(p.Jobs, j)
	}
	mw.Cases, mw.Synthetic = len(mw.Jobs), len(p.Copies)
	if len(mw.Jobs) > 0 {
		p.Metas = append(p.Metas, mw)
	}
	return nil
}

// modelInBody reports whether an endpoint's request.json must carry the
// model at its top level. The native GenAI calls take it from the URL,
// list_models takes none, and an Assistants or Vector Stores request.json is
// a PassthroughRequest (a model in its body, an assistant's, is not
// changed by a clone).
func modelInBody(endpoint string) bool {
	switch endpoint {
	case contracttest.EndpointGenAIGenerate, contracttest.EndpointGenAICountTokens, contracttest.EndpointGenAIEmbed,
		contracttest.EndpointListModels, contracttest.EndpointAssistants, contracttest.EndpointVectorStores:
		return false
	}
	return true
}

// casePriority orders a model's cases under -max-cases: the plain text and
// tool paths first, streaming next, then thinking, then the other 2xx cases
// by name, then error cases. It matches the order cmd/model-watch picks a
// template's cases in.
var casePriority = []string{
	"text_basic", "stream_text", "tool_call", "tool_use", "stream_tool_call", "stream_tool_use",
	"thinking", "reasoning", "stream_thinking", "stream_reasoning",
}

func caseRank(c caseRef) int {
	if c.isError() {
		return len(casePriority) + 1
	}
	for i, p := range casePriority {
		if c.Name == p {
			return i
		}
	}
	return len(casePriority)
}

// capCases applies -max-models and -max-cases to cands (in model order) and
// reports which to keep. Caps count per endpoint, so capping chat cannot
// starve embeddings: maxModels keeps that many models of each endpoint per
// provider, starting rotate places into the sorted model list (so successive
// runs with a growing rotate cover every model), and maxCases keeps that
// many cases of each endpoint per model, in casePriority order. 0 is no cap.
func capCases(cands []caseRef, maxModels, maxCases, rotate int) []bool {
	keep := make([]bool, len(cands))
	for i := range keep {
		keep[i] = true
	}
	if maxModels > 0 {
		models := map[string][]string{} // endpoint -> models, in order
		for _, c := range cands {
			if l := models[c.Endpoint]; len(l) == 0 || l[len(l)-1] != c.Model {
				models[c.Endpoint] = append(l, c.Model)
			}
		}
		allowed := map[string]bool{} // endpoint + "\x00" + model
		for ep, l := range models {
			start := rotate % len(l)
			for k := 0; k < maxModels && k < len(l); k++ {
				allowed[ep+"\x00"+l[(start+k)%len(l)]] = true
			}
		}
		for i, c := range cands {
			keep[i] = allowed[c.Endpoint+"\x00"+c.Model]
		}
	}
	if maxCases > 0 {
		groups := map[string][]int{} // model + endpoint -> indexes
		var order []string
		for i, c := range cands {
			if !keep[i] {
				continue
			}
			k := c.Model + "\x00" + c.Endpoint
			if _, ok := groups[k]; !ok {
				order = append(order, k)
			}
			groups[k] = append(groups[k], i)
		}
		for _, k := range order {
			idx := groups[k]
			sort.SliceStable(idx, func(a, b int) bool {
				ca, cb := cands[idx[a]], cands[idx[b]]
				if ra, rb := caseRank(ca), caseRank(cb); ra != rb {
					return ra < rb
				}
				return ca.Name < cb.Name
			})
			for n, i := range idx {
				keep[i] = n < maxCases
			}
		}
	}
	return keep
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
