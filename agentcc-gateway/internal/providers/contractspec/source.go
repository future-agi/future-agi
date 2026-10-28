package contractspec

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
)

// Spec formats.
const (
	FormatOpenAPI   = "openapi"
	FormatDiscovery = "discovery"
	FormatSmithy    = "smithy"
)

// Source is where one provider's official spec lives and which roots the
// gateway parses from it.
type Source struct {
	Provider string
	Format   string
	// Repo ("owner/name") and Path locate a spec in a GitHub repository.
	// The latest commit of Branch (default HEAD) is resolved with
	// git ls-remote, and the file is fetched raw at that commit, so the
	// provenance records an immutable URL.
	Repo, Branch, Path string
	// URL is fetched as is when Repo is empty (Google discovery documents,
	// which have no history; the provenance records their revision and
	// sha256).
	URL string
	// Decode turns the fetched bytes into the spec (see DecodeGzip,
	// DecodeEmbeddedMock).
	Decode string
	// License notes the spec's terms, which is why only subsets are
	// vendored.
	License string
	Roots   []RootDef
}

// RootDef selects one root of a source and says how to flatten it.
type RootDef struct {
	// Name is the subset's name within the provider ("messages").
	Name string
	// Description says what the gateway does with the root; it is copied
	// into the subset file.
	Description string

	// OpenAPI: Op ("POST /v1/messages") with Status (default "200"; or
	// "errors", the union of every 4xx, 5xx and default response) and
	// Content (default "application/json"); or Schema, a JSON pointer.
	Op, Status, Content string
	// Schema is a JSON pointer (OpenAPI) or a schema ID (discovery).
	Schema string

	// Method is a discovery method path ("models.generateContent").
	Method string

	// Operation is a Smithy operation shape ID; its output body is the
	// root. With Errors, the root is the operation's error shapes instead.
	Operation string
	Errors    bool

	// Events splits a union root (a discriminated stream event union, or a
	// Smithy event stream) into one "<event>:$" root per event.
	Events bool
	// Prefix is put in front of every path: "chunk" for a stream of
	// undifferentiated chunks, matching the contract tests' "chunk:$...".
	Prefix string
	// Elide maps a schema (a JSON pointer) that another root already
	// flattens to that root ("openai/responses $"). Where the walk meets it,
	// the path is recorded with same_as and not expanded, so a stream whose
	// events carry the whole response does not repeat it per event.
	Elide map[string]string
	// Prune maps paths to why nothing below them is listed: their own
	// kinds are kept, their subtrees are not. For request parameters a
	// response echoes back (Responses' tools, instructions), whose growth
	// the gateway never reads, so it is not drift. OpenAPI only.
	Prune map[string]string
	// Rebase drops an envelope: with "$.data", "$.data.choices" is stored
	// as "$.choices" and paths outside "$.data" are dropped.
	Rebase string
}

func (r RootDef) status() string {
	if r.Status == "" {
		return "200"
	}
	return r.Status
}

// RawURL is the URL the spec is fetched from at commit (ignored for URL
// sources).
func (s Source) RawURL(commit string) string {
	if s.Repo == "" {
		return s.URL
	}
	return fmt.Sprintf("https://raw.githubusercontent.com/%s/%s/%s", s.Repo, commit, s.Path)
}

// Extract parses a decoded spec and flattens every root of src; any root
// that cannot be extracted is an error. meta holds the spec's own version
// fields for the provenance record.
func Extract(src Source, spec []byte) (subsets []*Subset, meta map[string]string, err error) {
	subsets, meta, failed, err := ExtractEach(src, spec)
	if err != nil {
		return nil, nil, err
	}
	for _, r := range src.Roots {
		if ferr := failed[r.Name]; ferr != nil {
			return nil, nil, fmt.Errorf("%s/%s: %w", src.Provider, r.Name, ferr)
		}
	}
	return subsets, meta, nil
}

// ExtractEach is Extract that keeps going past a root it cannot extract (an
// operation the spec no longer has, say), returning those roots' errors by
// name. The drift check reports them as drift; err is for a spec that does
// not parse at all.
func ExtractEach(src Source, spec []byte) (subsets []*Subset, meta map[string]string, failed map[string]error, err error) {
	doc, err := ParseDocument(spec)
	if err != nil {
		return nil, nil, nil, err
	}
	switch src.Format {
	case FormatOpenAPI:
		meta = openAPIMeta(doc)
	case FormatDiscovery:
		meta = discoveryMeta(doc)
	case FormatSmithy:
		meta = smithyMeta(doc)
	default:
		return nil, nil, nil, fmt.Errorf("%s: unknown format %q", src.Provider, src.Format)
	}
	failed = map[string]error{}
	for _, r := range src.Roots {
		s, err := extractRoot(src, doc, r)
		switch {
		case err != nil:
			failed[r.Name] = err
		case len(s.Paths) == 0:
			failed[r.Name] = fmt.Errorf("the root flattened to no paths")
		default:
			subsets = append(subsets, s)
		}
	}
	return subsets, meta, failed, nil
}

func extractRoot(src Source, doc map[string]any, r RootDef) (*Subset, error) {
	b := newBuilder(src.Provider, r.Name, src.Format, "")
	b.s.Description = r.Description
	if r.Prefix != "" {
		b.prefix = r.Prefix
	}
	b.rebase = r.Rebase
	switch src.Format {
	case FormatOpenAPI:
		schemas, sel, err := openAPIRootSchemas(doc, r)
		if err != nil {
			return nil, err
		}
		b.s.Select = sel
		w := &openAPIWalker{doc: doc, b: b, elide: r.Elide, prune: r.Prune}
		for _, s := range schemas {
			w.walkRoot(s, r.Events)
		}
	case FormatDiscovery:
		id, sel, err := discoveryRootSchema(doc, r)
		if err != nil {
			return nil, err
		}
		b.s.Select = sel
		schemas, _ := doc["schemas"].(map[string]any)
		w := &discoveryWalker{schemas: schemas, b: b}
		w.walk(map[string]any{"$ref": id}, "$", nil, 0)
	case FormatSmithy:
		shapes, _ := doc["shapes"].(map[string]any)
		w := &smithyWalker{shapes: shapes, b: b}
		var sel string
		var err error
		if r.Errors {
			sel, err = w.walkErrors(r.Operation)
		} else {
			sel, err = w.walkOperation(r.Operation, r.Events)
		}
		if err != nil {
			return nil, err
		}
		b.s.Select = sel
	}
	if r.Rebase != "" {
		b.s.Select += " (rebased at " + r.Rebase + ")"
	}
	return b.finish(), nil
}

// Provenance records where a provider's committed subsets came from. It is
// written next to them as source.json.
type Provenance struct {
	Provider string `json:"provider"`
	Format   string `json:"format"`
	// URL is what was fetched: a raw.githubusercontent.com URL at Commit,
	// or the discovery URL.
	URL    string `json:"url"`
	Repo   string `json:"repo,omitempty"`
	Path   string `json:"path,omitempty"`
	Commit string `json:"commit,omitempty"`
	Decode string `json:"decode,omitempty"`
	// SpecVersion holds the spec's own version fields (openapi,
	// info.version; discovery revision; Smithy service version). They are
	// recorded, not trusted: OpenAI's info.version never changes and a
	// discovery revision changes daily.
	SpecVersion map[string]string `json:"spec_version,omitempty"`
	// SHA256 is the hash of the fetched bytes; DecodedSHA256 of the spec
	// after Decode, when that differs.
	SHA256        string   `json:"sha256"`
	DecodedSHA256 string   `json:"decoded_sha256,omitempty"`
	FetchedAt     string   `json:"fetched_at"`
	License       string   `json:"license,omitempty"`
	Roots         []string `json:"roots"`
}

// SHA256Hex is the hex sha256 of b.
func SHA256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// ShortRef abbreviates a commit SHA for messages.
func ShortRef(ref string) string {
	if len(ref) > 12 && strings.Trim(ref, "0123456789abcdef") == "" {
		return ref[:12]
	}
	return ref
}
