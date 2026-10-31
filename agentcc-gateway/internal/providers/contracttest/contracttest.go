// Package contracttest replays recorded (or hand-authored) upstream provider
// responses through a provider adapter's real code path and pins what comes
// out, so a change in a provider's response shape fails a test instead of
// silently dropping content in production.
//
// Fixtures are keyed by exact upstream model ID, because providers change
// response shapes per model release, not per API. See README.md for the
// layout, the meta.json and case.json schemas, error cases, strict mode
// (Strict: type, enum, opaque-region and shape checks), the mutation
// self-test, how to add a model version, how to update goldens, and live
// mode.
//
// The package is test support: import it only from _test.go files. The one
// exception is cmd/contract-recorder, which uses ModelDir, Meta and the file
// name constants so its layout cannot drift from Discover's.
package contracttest

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/common"
)

// update rewrites golden files instead of comparing against them:
//
//	go test ./internal/providers/<provider>/ -run Contract -update
var update = flag.Bool("update", false, "rewrite contract-test golden files")

const (
	// DefaultRoot is where a provider package keeps its fixtures, relative to
	// the package directory (the working directory of its tests).
	DefaultRoot = "testdata/contracts"

	// LiveEnv, when set, points at a directory laid out as
	// <provider>/<model>/<case>/ holding freshly recorded responses. Discover
	// then reads cases from there instead of DefaultRoot. See README.md.
	LiveEnv = "AGENTCC_CONTRACT_FIXTURES_DIR"
)

// Fixture file names. MetaFile sits in the model directory, the rest in a
// case directory.
const (
	MetaFile         = "meta.json"
	RequestFile      = "request.json"
	ResponseJSONFile = "response.json"
	ResponseSSEFile  = "response.sse"
	// ResponseEventStreamFile is a binary AWS event-stream body (Bedrock's
	// streaming framing), replayed as application/vnd.amazon.eventstream.
	ResponseEventStreamFile = "response.eventstream"
	// ResponseBinFile is an opaque binary body, such as speech audio,
	// replayed as application/octet-stream unless case.json says otherwise.
	ResponseBinFile = "response.bin"
	// ResponseTextFile is a non-JSON text body, such as an HTML 502 page from
	// an upstream proxy, replayed as text/plain unless case.json says
	// otherwise.
	ResponseTextFile   = "response.txt"
	GoldenFile         = "golden.json"
	UpstreamGoldenFile = "upstream_request.golden.json"
	// OpaqueGoldenFile, in the contracts root, lists every opaque region
	// (json.RawMessage, any, a custom unmarshaler) the strict checks met
	// across the provider's fixtures: what the adapter's structs do not
	// watch. Written by -update; see Strict.
	OpaqueGoldenFile = "opaque.golden.json"
)

// Values of Meta.Source.
const (
	SourceSynthetic = "synthetic"
	SourceRecorded  = "recorded"
)

// Case is one fixture: a canonical request, the raw upstream body to answer
// it with, and where its goldens live.
type Case struct {
	// Name is the case directory name, e.g. "stream_thinking".
	Name string
	// Model is the exact upstream model ID, decoded from the model directory
	// name (see ModelDir).
	Model string
	// Dir holds the case's fixture files and goldens.
	Dir string
	// Meta is shared by every case of the same model.
	Meta *Meta
	// Request is request.json, decoded as the gateway would decode it, for a
	// chat or chat_via_responses case; nil for any other endpoint, whose test
	// decodes RawRequest into its own request type. Each Case owns its own
	// copy.
	Request *models.ChatCompletionRequest
	// RawRequest is request.json byte for byte, whatever the endpoint.
	RawRequest []byte
	// Body is the raw upstream response body, byte for byte.
	Body []byte
	// IsStream is true when the request asks for a stream: request.json has
	// "stream": true, or the response file is response.sse or
	// response.eventstream. A 2xx stream case has a stream response file; an
	// error case's body is the error the upstream answered with instead.
	IsStream bool
	// ContentType is what the replay server answers with: case.json's
	// content_type, or the one the response file's extension implies.
	ContentType string
	// Live is true when the case came from LiveEnv rather than the repo.
	Live bool

	// Endpoint is the provider method the case drives (case.json; default
	// EndpointChat).
	Endpoint string
	// Status is the HTTP status replayed (case.json; default 200).
	Status int
	// Headers are extra response headers replayed (case.json).
	Headers map[string]string
	// Recordable is false for a case no real request can induce (case.json).
	Recordable bool
	// Notes is case.json's notes.
	Notes string
	// SanityGaps are the sanity failures case.json expects (see ExpectGaps).
	SanityGaps []SanityGap

	// requestDir holds the request.json in use: Dir, or in live mode the
	// repo's case directory when the recording did not bring its own.
	requestDir string
	// mutant marks a copy made by the mutation self-test: its checks must not
	// count toward allowlist staleness, the opaque golden or shape diffs.
	mutant bool
}

// IsError reports whether the case replays an error status (400-599).
func (c Case) IsError() bool { return c.Status >= 400 }

// ID is "<model>/<case>", the natural subtest name.
func (c Case) ID() string { return c.Model + "/" + c.Name }

// GoldenPath is the canonical-output golden for this case.
func (c Case) GoldenPath() string { return filepath.Join(c.Dir, GoldenFile) }

// UpstreamGoldenPath is the outbound-request golden for this case. The
// outbound request depends on request.json alone, so its golden sits beside
// the request.json in use — in live mode, usually the repo's — and live runs
// still pin it.
func (c Case) UpstreamGoldenPath() string { return filepath.Join(c.requestDir, UpstreamGoldenFile) }

// Meta is a model directory's meta.json.
type Meta struct {
	Provider string `json:"provider"`
	Model    string `json:"model"`
	// Source is "synthetic" (authored by hand from provider docs) or
	// "recorded" (captured from the real API).
	Source string `json:"source"`
	// RecordedAt is the capture date, YYYY-MM-DD, for recorded fixtures.
	RecordedAt *string `json:"recorded_at"`
	// ClonedFrom is the model this one's cases were cloned from (the
	// recorder's -from). Strict.Check then diffs each case's shape against the
	// same case of that model, so a new model's first capture is compared
	// with a reviewed one instead of being accepted as it is.
	ClonedFrom string `json:"cloned_from,omitempty"`
	Notes      string `json:"notes"`
	// AllowedUnknownFields lists upstream JSON paths the adapter's structs do
	// not represent, each with the reason that is acceptable.
	AllowedUnknownFields []AllowedField `json:"allowed_unknown_fields"`
	// AllowedUnknownShapes lists unknown-shape reports (common.UnknownShape)
	// the adapter is known to make on these fixtures.
	AllowedUnknownShapes []AllowedShape `json:"allowed_unknown_shapes"`
	// AllowedTypeMismatches lists paths whose JSON type differs from the
	// adapter's struct (a decode would fail or drop them), each with a reason.
	// Same rules as AllowedUnknownFields.
	AllowedTypeMismatches []AllowedField `json:"allowed_type_mismatches,omitempty"`
	// OptionalFields lists paths allowed to appear, disappear or change JSON
	// type between two captures of the same case (live vs committed, or a
	// cloned model vs its template), each with a reason. An entry covers the
	// path and everything below it.
	OptionalFields []AllowedField `json:"optional_fields,omitempty"`

	dir   string
	cases int
	live  bool
	suite *suite
	// mutant metas belong to mutation self-test copies and record nothing.
	mutant bool

	mu      sync.Mutex
	audited map[auditKind]map[string]bool // case names that ran each audit
	used    map[auditKind]map[string]bool // allowlist keys each audit used
}

// auditKind names an allowlist a case's audit consults.
type auditKind string

const (
	auditFields auditKind = "allowed_unknown_fields"
	auditShapes auditKind = "allowed_unknown_shapes"
	auditTypes  auditKind = "allowed_type_mismatches"
)

// mutantCopy is a Meta with m's allowlists that records nothing, for the
// mutation self-test.
func (m *Meta) mutantCopy() *Meta {
	return &Meta{
		Provider: m.Provider, Model: m.Model, Source: m.Source, RecordedAt: m.RecordedAt,
		ClonedFrom: m.ClonedFrom, Notes: m.Notes,
		AllowedUnknownFields: m.AllowedUnknownFields, AllowedUnknownShapes: m.AllowedUnknownShapes,
		AllowedTypeMismatches: m.AllowedTypeMismatches, OptionalFields: m.OptionalFields,
		dir: m.dir, cases: m.cases, live: m.live, mutant: true,
	}
}

// suite is what one Discover call shares across its models: what the strict
// checks and the mutation self-test met, for OpaqueGoldenFile and the
// summary logs.
type suite struct {
	root  string
	live  bool
	cases int

	mu     sync.Mutex
	strict map[string]bool // case IDs that ran Strict.Check
	opaque map[string]bool

	// enums are the EnumSpec paths each Strict declares and the ones a
	// case reached, for a log of declarations nothing exercised.
	enums map[*Strict]map[string]bool

	// The mutation self-test's totals, for one summary line.
	mutCases, mutRun, mutDetected int
	mutOpaque                     map[string]bool
}

// noteStrict records what one case's Strict.Check met.
func (s *suite) noteStrict(st *Strict, caseID string, rep Report) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.strict == nil {
		s.strict, s.opaque, s.enums = map[string]bool{}, map[string]bool{}, map[*Strict]map[string]bool{}
	}
	s.strict[caseID] = true
	for _, p := range rep.Opaque {
		s.opaque[p] = true
	}
	if s.enums[st] == nil {
		s.enums[st] = map[string]bool{}
	}
	for _, p := range rep.enumHits {
		s.enums[st][p] = true
	}
}

// finish logs the mutation self-test's totals and, once every case ran
// Strict.Check, the EnumSpec paths no case reached, and compares the opaque
// regions every case met with OpaqueGoldenFile. A -run filter that selects
// some cases skips the last two, and so does live mode, whose responses need
// not reach every region a fixture pinned.
func (s *suite) finish(t testing.TB) {
	t.Helper()
	s.mu.Lock()
	ran, paths := len(s.strict), make([]string, 0, len(s.opaque))
	for p := range s.opaque {
		paths = append(paths, p)
	}
	if s.mutCases > 0 {
		t.Logf("contracttest: mutation self-test: %d cases, %d of %d mutations detected, %d distinct opaque regions not mutated",
			s.mutCases, s.mutDetected, s.mutRun, len(s.mutOpaque))
	}
	s.mu.Unlock()
	var unreached []string
	for st, hits := range s.enums {
		for _, e := range st.Enums {
			for _, p := range e.Paths {
				if !hits[p] {
					unreached = append(unreached, p)
				}
			}
		}
	}
	if s.live || ran == 0 || ran != s.cases {
		return
	}
	if len(unreached) > 0 {
		sort.Strings(unreached)
		t.Logf("contracttest: EnumSpec paths no fixture has a value at (fine for rare fields; a mistyped path disables its check):\n  %s",
			strings.Join(unreached, "\n  "))
	}
	sort.Strings(paths)
	AssertGolden(t, filepath.Join(s.root, OpaqueGoldenFile), paths)
}

// AllowedField is one allowlisted unknown JSON path.
type AllowedField struct {
	// Path uses the syntax UnknownFields returns: "$.content[].citations",
	// or "<event>:$.delta.foo" for a streamed event's payload.
	Path   string `json:"path"`
	Reason string `json:"reason"`
}

// AllowedShape is one allowlisted unknown-shape report.
type AllowedShape struct {
	Kind   string `json:"kind"`
	Value  string `json:"value"`
	Reason string `json:"reason"`
}

func (s AllowedShape) key() string { return s.Kind + ":" + s.Value }

// Discover loads every case for provider: from DefaultRoot, or in live mode
// from $AGENTCC_CONTRACT_FIXTURES_DIR/<provider>. It fails the test on any
// malformed fixture, and when the provider has no cases at all. Once every
// subtest of t has finished, it fails t for allowlist entries no case used
// (see AssertKnownPaths), so an allowlist cannot outlive the gap it excused.
func Discover(t testing.TB, provider string) []Case {
	t.Helper()
	if dir := os.Getenv(LiveEnv); dir != "" {
		return discover(t, provider, filepath.Join(dir, provider), DefaultRoot, true)
	}
	return discover(t, provider, DefaultRoot, "", false)
}

// discover reads cases from root. In live mode, fallback is the repo root
// consulted for a meta.json or request.json the recording did not write.
func discover(t testing.TB, provider, root, fallback string, live bool) []Case {
	t.Helper()
	modelDirs, err := os.ReadDir(root)
	if err != nil {
		t.Fatalf("contracttest: reading fixtures for %s: %v", provider, err)
	}

	var cases []Case
	var metas []*Meta
	st := &suite{root: root, live: live}
	for _, md := range modelDirs {
		if !md.IsDir() {
			continue
		}
		modelDir := filepath.Join(root, md.Name())
		model, err := modelFromDir(md.Name())
		if err != nil {
			t.Fatalf("contracttest: %s: %v", modelDir, err)
		}
		meta := loadMeta(t, provider, model, modelDir, fallback, live)
		meta.suite = st
		metas = append(metas, meta)

		caseDirs, err := os.ReadDir(modelDir)
		if err != nil {
			t.Fatalf("contracttest: reading %s: %v", modelDir, err)
		}
		found := 0
		for _, cd := range caseDirs {
			if !cd.IsDir() {
				continue
			}
			found++
			c, err := readCase(meta, model, cd.Name(), filepath.Join(modelDir, cd.Name()), fallback, live)
			if err != nil {
				t.Fatalf("contracttest: %v", err)
			}
			if live && !c.Recordable {
				// Nothing real can induce this case, so a recording of it
				// answers a different question; the committed fixture keeps
				// testing it.
				t.Logf("contracttest: live mode: skipping %s, which case.json marks not recordable", c.ID())
				continue
			}
			cases = append(cases, c)
			meta.cases++
		}
		if found == 0 {
			t.Fatalf("contracttest: %s has a meta.json but no cases", modelDir)
		}
	}
	if len(cases) == 0 {
		t.Fatalf("contracttest: no cases for %s under %s", provider, root)
	}
	st.cases = len(cases)

	t.Cleanup(func() {
		for _, m := range metas {
			m.checkStale(t)
		}
		st.finish(t)
	})
	return cases
}

// ModelDir is the name of the directory that holds model's fixtures: the
// model ID with every byte outside RFC 3986's unreserved set (A-Z a-z 0-9
// - . _ ~) percent-encoded. Most IDs are their own directory name. Bedrock's
// "us.amazon.nova-2-lite-v1:0" is filed under "us.amazon.nova-2-lite-v1%3A0",
// because Windows cannot check out a path containing ':' and Go module zips
// reject one. The encoding is a filesystem detail: meta.json's model and
// Case.Model carry the real ID. (url.PathEscape is not used: it leaves ':'.)
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

// modelFromDir decodes a model directory name to the model ID. It rejects a
// name that does not decode, and one that is not ModelDir's spelling of the
// ID it decodes to (a literal ':', or "%3a" for "%3A"), so each model has
// exactly one directory name.
func modelFromDir(dir string) (string, error) {
	model, err := url.PathUnescape(dir)
	if err != nil {
		return "", fmt.Errorf("model directory name is not valid percent-encoding: %v", err)
	}
	if want := ModelDir(model); want != dir {
		return "", fmt.Errorf("the fixtures for model %q must be in a directory named %q (see ModelDir)", model, want)
	}
	return model, nil
}

func loadMeta(t testing.TB, provider, model, modelDir, fallback string, live bool) *Meta {
	t.Helper()
	path := filepath.Join(modelDir, MetaFile)
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) && live {
		// A recording need not carry its own meta.json: the repo's copy for
		// the same model holds the allowlist. A model the repo has never seen
		// starts from an empty one.
		path = filepath.Join(fallback, ModelDir(model), MetaFile)
		raw, err = os.ReadFile(path)
		if os.IsNotExist(err) {
			return &Meta{Provider: provider, Model: model, Source: SourceRecorded, dir: modelDir, live: true}
		}
	}
	if err != nil {
		t.Fatalf("contracttest: reading %s: %v", path, err)
	}

	meta, err := decodeMeta(raw, provider, model)
	if err != nil {
		t.Fatalf("contracttest: %s: %v", path, err)
	}
	meta.dir, meta.live = modelDir, live
	return meta
}

// decodeMeta decodes and validates a meta.json strictly.
func decodeMeta(raw []byte, provider, model string) (*Meta, error) {
	meta := &Meta{}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(meta); err != nil {
		return nil, err
	}
	if err := meta.validate(provider, model); err != nil {
		return nil, err
	}
	return meta, nil
}

var shapeKinds = map[common.ShapeKind]bool{
	common.ShapeUnknownEvent:         true,
	common.ShapeUnknownBlock:         true,
	common.ShapeUnknownDelta:         true,
	common.ShapeUnmappedFinishReason: true,
	common.ShapeMalformed:            true,
}

var recordedAtRe = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

func (m *Meta) validate(provider, model string) error {
	if m.Provider != provider {
		return fmt.Errorf("provider = %q, want %q", m.Provider, provider)
	}
	if m.Model != model {
		return fmt.Errorf("model = %q, want %q, the model ID the directory name %q encodes", m.Model, model, ModelDir(model))
	}
	switch m.Source {
	case SourceSynthetic:
		if m.RecordedAt != nil {
			return fmt.Errorf("recorded_at must be null for a synthetic fixture")
		}
	case SourceRecorded:
		if m.RecordedAt == nil || !recordedAtRe.MatchString(*m.RecordedAt) {
			return fmt.Errorf("recorded_at must be YYYY-MM-DD for a recorded fixture")
		}
	default:
		return fmt.Errorf("source = %q, want %q or %q", m.Source, SourceSynthetic, SourceRecorded)
	}
	if err := validatePaths("allowed_unknown_fields", m.AllowedUnknownFields); err != nil {
		return err
	}
	if err := validatePaths("allowed_type_mismatches", m.AllowedTypeMismatches); err != nil {
		return err
	}
	if err := validatePaths("optional_fields", m.OptionalFields); err != nil {
		return err
	}
	if m.ClonedFrom == m.Model {
		return fmt.Errorf("cloned_from is the model itself")
	}
	shapes := map[string]bool{}
	for _, s := range m.AllowedUnknownShapes {
		if !shapeKinds[common.ShapeKind(s.Kind)] {
			return fmt.Errorf("allowed_unknown_shapes kind %q is not a common.ShapeKind", s.Kind)
		}
		if strings.TrimSpace(s.Reason) == "" {
			return fmt.Errorf("allowed_unknown_shapes %q has no reason", s.key())
		}
		if shapes[s.key()] {
			return fmt.Errorf("allowed_unknown_shapes %q is listed twice", s.key())
		}
		shapes[s.key()] = true
	}
	return nil
}

// validatePaths checks a list of path entries: JSON paths, each with a
// reason, none twice.
func validatePaths(list string, entries []AllowedField) error {
	paths := map[string]bool{}
	for _, f := range entries {
		if !strings.Contains(f.Path, "$") {
			return fmt.Errorf("%s path %q is not a JSON path", list, f.Path)
		}
		if strings.TrimSpace(f.Reason) == "" {
			return fmt.Errorf("%s %q has no reason", list, f.Path)
		}
		if paths[f.Path] {
			return fmt.Errorf("%s %q is listed twice", list, f.Path)
		}
		paths[f.Path] = true
	}
	return nil
}

// responseFiles are the response file names a case may hold, exactly one.
var responseFiles = []struct {
	name        string
	stream      bool
	contentType string
}{
	{ResponseJSONFile, false, "application/json"},
	{ResponseSSEFile, true, "text/event-stream"},
	{ResponseEventStreamFile, true, "application/vnd.amazon.eventstream"},
	{ResponseBinFile, false, "application/octet-stream"},
	{ResponseTextFile, false, "text/plain; charset=utf-8"},
}

func responseFileNames() string {
	names := make([]string, len(responseFiles))
	for i, rf := range responseFiles {
		names[i] = rf.name
	}
	return strings.Join(names, ", ")
}

// readCase loads one case directory. In live mode, a request.json or
// case.json the recording did not write comes from the repo's same-named
// case under fallback.
func readCase(meta *Meta, model, name, dir, fallback string, live bool) (Case, error) {
	c := Case{Name: name, Model: model, Dir: dir, Meta: meta, Live: live}
	repoDir := ""
	if live {
		repoDir = filepath.Join(fallback, ModelDir(model), name)
	}

	var found []string
	respStream := false
	for _, rf := range responseFiles {
		body, err := os.ReadFile(filepath.Join(dir, rf.name))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return c, fmt.Errorf("reading %s/%s: %v", dir, rf.name, err)
		}
		found = append(found, rf.name)
		c.Body, respStream, c.ContentType = body, rf.stream, rf.contentType
	}
	switch {
	case len(found) == 0:
		return c, fmt.Errorf("%s has no response file (want one of %s)", dir, responseFileNames())
	case len(found) > 1:
		return c, fmt.Errorf("%s has more than one response file: %s", dir, strings.Join(found, ", "))
	case found[0] == ResponseJSONFile && !json.Valid(c.Body):
		return c, fmt.Errorf("%s/%s is not valid JSON", dir, ResponseJSONFile)
	}

	specDir := dir
	if _, err := os.Stat(filepath.Join(dir, CaseFile)); os.IsNotExist(err) && live {
		specDir = repoDir
	}
	spec, err := ReadCaseSpec(specDir)
	if err != nil {
		return c, err
	}
	c.Endpoint, c.Status = spec.Endpoint, spec.Status
	if c.Endpoint == "" {
		c.Endpoint = EndpointChat
	}
	if c.Status == 0 {
		c.Status = 200
	}
	if spec.ContentType != "" {
		c.ContentType = spec.ContentType
	}
	c.Headers, c.Recordable, c.Notes, c.SanityGaps = spec.Headers, spec.CanRecord(), spec.Notes, spec.SanityGaps
	if c.IsError() && respStream {
		return c, fmt.Errorf("%s: a %s with error status %d; an error case's body is the error the upstream answered with "+
			"(%s or %s), and a mid-stream error is a 200 stream", dir, found[0], c.Status, ResponseJSONFile, ResponseTextFile)
	}

	c.requestDir = dir
	reqPath := filepath.Join(dir, RequestFile)
	raw, err := os.ReadFile(reqPath)
	if os.IsNotExist(err) && live {
		// The recorder replays the repo's request; it need not copy it.
		c.requestDir = repoDir
		reqPath = filepath.Join(c.requestDir, RequestFile)
		raw, err = os.ReadFile(reqPath)
	}
	if err != nil {
		return c, fmt.Errorf("reading %s: %v", reqPath, err)
	}
	c.RawRequest = raw
	var head map[string]json.RawMessage
	if err := json.Unmarshal(raw, &head); err != nil {
		return c, fmt.Errorf("%s: not a JSON object: %v", reqPath, err)
	}
	reqStream, hasStream := false, false
	if s, ok := head["stream"]; ok {
		if err := json.Unmarshal(s, &reqStream); err != nil {
			return c, fmt.Errorf(`%s: "stream" must be a boolean`, reqPath)
		}
		hasStream = true
	}
	if chatEndpoints[c.Endpoint] {
		var req models.ChatCompletionRequest
		if err := json.Unmarshal(raw, &req); err != nil {
			return c, fmt.Errorf("%s: %v", reqPath, err)
		}
		c.Request = &req
		hasStream = true // a chat request without "stream" asks for none
	}
	if hasStream && !c.IsError() && reqStream != respStream {
		return c, fmt.Errorf("%s: request has stream=%v, but the case's response file says stream=%v",
			reqPath, reqStream, respStream)
	}
	c.IsStream = reqStream || respStream
	return c, nil
}

// note records that caseName ran the audit of kind and which of its
// allowlist entries it used. A mutant meta records nothing.
func (m *Meta) note(kind auditKind, caseName string, used []string) {
	if m.mutant {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.audited == nil {
		m.audited = map[auditKind]map[string]bool{}
		m.used = map[auditKind]map[string]bool{}
	}
	if m.audited[kind] == nil {
		m.audited[kind], m.used[kind] = map[string]bool{}, map[string]bool{}
	}
	m.audited[kind][caseName] = true
	for _, k := range used {
		m.used[kind][k] = true
	}
}

// checkStale fails t for allowlist entries no case of the model used. It only
// judges an allowlist when every case of the model ran the matching audit: a
// -run filter that selects one case must not condemn entries other cases
// need. Live mode skips it, since a live response need not reproduce every
// shape a fixture pinned.
func (m *Meta) checkStale(t testing.TB) {
	t.Helper()
	if m.live {
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()

	lists := []struct {
		kind auditKind
		keys []string
	}{
		{auditFields, fieldKeys(m.AllowedUnknownFields)},
		{auditShapes, shapeKeys(m.AllowedUnknownShapes)},
		{auditTypes, fieldKeys(m.AllowedTypeMismatches)},
	}
	for _, l := range lists {
		if len(m.audited[l.kind]) != m.cases {
			continue
		}
		var stale []string
		for _, k := range l.keys {
			if !m.used[l.kind][k] {
				stale = append(stale, k)
			}
		}
		if len(stale) > 0 {
			sort.Strings(stale)
			t.Errorf("contracttest: %s/%s %s entries no case produced (remove them):\n  %s",
				m.dir, MetaFile, l.kind, strings.Join(stale, "\n  "))
		}
	}
}

func fieldKeys(fs []AllowedField) []string {
	keys := make([]string, len(fs))
	for i, f := range fs {
		keys[i] = f.Path
	}
	return keys
}

func shapeKeys(ss []AllowedShape) []string {
	keys := make([]string, len(ss))
	for i, s := range ss {
		keys[i] = s.key()
	}
	return keys
}
