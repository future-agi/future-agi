// Package contracttest replays recorded (or hand-authored) upstream provider
// responses through a provider adapter's real code path and pins what comes
// out, so a change in a provider's response shape fails a test instead of
// silently dropping content in production.
//
// Fixtures are keyed by exact upstream model ID, because providers change
// response shapes per model release, not per API. See README.md for the
// layout, the meta.json schema, how to add a model version, how to update
// goldens, and live mode.
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
	GoldenFile              = "golden.json"
	UpstreamGoldenFile      = "upstream_request.golden.json"
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
	// Request is request.json, decoded as the gateway would decode it. Each
	// Case owns its own copy.
	Request *models.ChatCompletionRequest
	// Body is the raw upstream response body, byte for byte.
	Body []byte
	// IsStream is true when the body is response.sse or response.eventstream.
	IsStream bool
	// ContentType is what the replay server answers with, chosen by the
	// response file's extension.
	ContentType string
	// Live is true when the case came from LiveEnv rather than the repo.
	Live bool

	// requestDir holds the request.json in use: Dir, or in live mode the
	// repo's case directory when the recording did not bring its own.
	requestDir string
}

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
	Notes      string  `json:"notes"`
	// AllowedUnknownFields lists upstream JSON paths the adapter's structs do
	// not represent, each with the reason that is acceptable.
	AllowedUnknownFields []AllowedField `json:"allowed_unknown_fields"`
	// AllowedUnknownShapes lists unknown-shape reports (common.UnknownShape)
	// the adapter is known to make on these fixtures.
	AllowedUnknownShapes []AllowedShape `json:"allowed_unknown_shapes"`

	dir   string
	cases int
	live  bool

	mu            sync.Mutex
	usedFields    map[string]bool
	usedShapes    map[string]bool
	auditedFields map[string]bool // case names that ran a field audit
	auditedShapes map[string]bool // case names that ran a shape audit
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
		metas = append(metas, meta)

		caseDirs, err := os.ReadDir(modelDir)
		if err != nil {
			t.Fatalf("contracttest: reading %s: %v", modelDir, err)
		}
		for _, cd := range caseDirs {
			if !cd.IsDir() {
				continue
			}
			c := loadCase(t, meta, model, cd.Name(), filepath.Join(modelDir, cd.Name()), fallback, live)
			cases = append(cases, c)
			meta.cases++
		}
		if meta.cases == 0 {
			t.Fatalf("contracttest: %s has a meta.json but no cases", modelDir)
		}
	}
	if len(cases) == 0 {
		t.Fatalf("contracttest: no cases for %s under %s", provider, root)
	}

	t.Cleanup(func() {
		for _, m := range metas {
			m.checkStale(t)
		}
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

	meta := &Meta{dir: modelDir, live: live}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(meta); err != nil {
		t.Fatalf("contracttest: %s: %v", path, err)
	}
	if err := meta.validate(provider, model); err != nil {
		t.Fatalf("contracttest: %s: %v", path, err)
	}
	return meta
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
	paths := map[string]bool{}
	for _, f := range m.AllowedUnknownFields {
		if !strings.Contains(f.Path, "$") {
			return fmt.Errorf("allowed_unknown_fields path %q is not a JSON path", f.Path)
		}
		if strings.TrimSpace(f.Reason) == "" {
			return fmt.Errorf("allowed_unknown_fields %q has no reason", f.Path)
		}
		if paths[f.Path] {
			return fmt.Errorf("allowed_unknown_fields %q is listed twice", f.Path)
		}
		paths[f.Path] = true
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

// responseFiles are the response file names a case may hold, exactly one.
var responseFiles = []struct {
	name        string
	stream      bool
	contentType string
}{
	{ResponseJSONFile, false, "application/json"},
	{ResponseSSEFile, true, "text/event-stream"},
	{ResponseEventStreamFile, true, "application/vnd.amazon.eventstream"},
}

func loadCase(t testing.TB, meta *Meta, model, name, dir, fallback string, live bool) Case {
	t.Helper()
	c := Case{Name: name, Model: model, Dir: dir, Meta: meta, Live: live}

	var found []string
	for _, rf := range responseFiles {
		body, err := os.ReadFile(filepath.Join(dir, rf.name))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			t.Fatalf("contracttest: reading %s/%s: %v", dir, rf.name, err)
		}
		found = append(found, rf.name)
		c.Body, c.IsStream, c.ContentType = body, rf.stream, rf.contentType
	}
	switch {
	case len(found) == 0:
		t.Fatalf("contracttest: %s has no response file (want one of %s, %s, %s)",
			dir, ResponseJSONFile, ResponseSSEFile, ResponseEventStreamFile)
	case len(found) > 1:
		t.Fatalf("contracttest: %s has more than one response file: %s", dir, strings.Join(found, ", "))
	case found[0] == ResponseJSONFile && !json.Valid(c.Body):
		t.Fatalf("contracttest: %s/%s is not valid JSON", dir, ResponseJSONFile)
	}

	c.requestDir = dir
	reqPath := filepath.Join(dir, RequestFile)
	raw, err := os.ReadFile(reqPath)
	if os.IsNotExist(err) && live {
		// The recorder replays the repo's request; it need not copy it.
		c.requestDir = filepath.Join(fallback, ModelDir(model), name)
		reqPath = filepath.Join(c.requestDir, RequestFile)
		raw, err = os.ReadFile(reqPath)
	}
	if err != nil {
		t.Fatalf("contracttest: reading %s: %v", reqPath, err)
	}
	var req models.ChatCompletionRequest
	if err := json.Unmarshal(raw, &req); err != nil {
		t.Fatalf("contracttest: %s: %v", reqPath, err)
	}
	if req.Stream != c.IsStream {
		t.Fatalf("contracttest: %s: request has stream=%v, but the case's response file says stream=%v",
			reqPath, req.Stream, c.IsStream)
	}
	c.Request = &req
	return c
}

// noteFields records that c ran a field audit and which allowlist entries it
// used.
func (m *Meta) noteFields(caseName string, used []string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.auditedFields == nil {
		m.auditedFields = map[string]bool{}
		m.usedFields = map[string]bool{}
	}
	m.auditedFields[caseName] = true
	for _, p := range used {
		m.usedFields[p] = true
	}
}

func (m *Meta) noteShapes(caseName string, used []string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.auditedShapes == nil {
		m.auditedShapes = map[string]bool{}
		m.usedShapes = map[string]bool{}
	}
	m.auditedShapes[caseName] = true
	for _, k := range used {
		m.usedShapes[k] = true
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

	if len(m.auditedFields) == m.cases {
		var stale []string
		for _, f := range m.AllowedUnknownFields {
			if !m.usedFields[f.Path] {
				stale = append(stale, f.Path)
			}
		}
		if len(stale) > 0 {
			sort.Strings(stale)
			t.Errorf("contracttest: %s/%s allowed_unknown_fields entries no case produced (remove them):\n  %s",
				m.dir, MetaFile, strings.Join(stale, "\n  "))
		}
	}
	if len(m.auditedShapes) == m.cases {
		var stale []string
		for _, s := range m.AllowedUnknownShapes {
			if !m.usedShapes[s.key()] {
				stale = append(stale, s.key())
			}
		}
		if len(stale) > 0 {
			sort.Strings(stale)
			t.Errorf("contracttest: %s/%s allowed_unknown_shapes entries no case produced (remove them):\n  %s",
				m.dir, MetaFile, strings.Join(stale, "\n  "))
		}
	}
}
