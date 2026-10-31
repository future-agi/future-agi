package contractspec

import (
	"bytes"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
)

// The committed subsets live in subsets/<provider>/<root>.json, with the
// provenance of each provider's spec in subsets/<provider>/source.json and
// the providers without a fetchable spec in subsets/unfetchable.json. They
// are embedded, so a provider's spec test reads them with no path
// arithmetic and no network.
//
//go:embed subsets
var embedded embed.FS

// Dir is the subsets directory relative to the agentcc-gateway module root,
// where cmd/contract-specs writes.
const Dir = "internal/providers/contractspec/subsets"

const (
	// ProvenanceFile is the per-provider provenance record.
	ProvenanceFile = "source.json"
	// ManifestFile lists the providers without a fetchable spec.
	ManifestFile = "unfetchable.json"
)

// Committed is the embedded subsets tree, rooted at the subsets directory.
func Committed() fs.FS {
	sub, err := fs.Sub(embedded, "subsets")
	if err != nil {
		panic(err) // the embed pattern guarantees the directory
	}
	return sub
}

// Load returns provider's committed subsets, sorted by root.
func Load(provider string) ([]*Subset, error) { return LoadFS(Committed(), provider) }

// LoadSubset returns one committed subset by ID ("anthropic/messages").
func LoadSubset(id string) (*Subset, error) { return LoadSubsetFS(Committed(), id) }

// LoadSubsetFS reads one subset by ID from fsys.
func LoadSubsetFS(fsys fs.FS, id string) (*Subset, error) {
	provider, root, ok := strings.Cut(id, "/")
	if !ok {
		return nil, fmt.Errorf("subset ID %q: want <provider>/<root>", id)
	}
	raw, err := fs.ReadFile(fsys, path.Join(provider, root+".json"))
	if err != nil {
		return nil, fmt.Errorf("subset %s: %w", id, err)
	}
	return ParseSubset(raw)
}

// LoadFS reads provider's subsets from fsys (laid out like the subsets
// directory), sorted by root. A provider with no directory has none.
func LoadFS(fsys fs.FS, provider string) ([]*Subset, error) {
	entries, err := fs.ReadDir(fsys, provider)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var out []*Subset
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasSuffix(name, ".json") || name == ProvenanceFile {
			continue
		}
		raw, err := fs.ReadFile(fsys, path.Join(provider, name))
		if err != nil {
			return nil, err
		}
		s, err := ParseSubset(raw)
		if err != nil {
			return nil, fmt.Errorf("%s/%s: %w", provider, name, err)
		}
		if s.Provider != provider || s.Root+".json" != name {
			return nil, fmt.Errorf("%s/%s holds subset %s", provider, name, s.ID())
		}
		out = append(out, s)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Root < out[j].Root })
	return out, nil
}

// Enums returns the enum values the committed subsets record at path. id is
// a provider ("anthropic"), which unions every root that has the path, or
// one subset ("anthropic/messages"). It is how a contract test's declared
// enum sets (contracttest EnumSpec) can be checked against the spec.
func Enums(id, p string) ([]string, error) {
	var subsets []*Subset
	if strings.Contains(id, "/") {
		s, err := LoadSubset(id)
		if err != nil {
			return nil, err
		}
		subsets = []*Subset{s}
	} else {
		var err error
		if subsets, err = Load(id); err != nil {
			return nil, err
		}
	}
	var vals []string
	found := false
	for _, s := range subsets {
		if info := s.Paths[p]; info != nil {
			found = true
			vals = append(vals, info.Enum...)
		}
	}
	if !found {
		return nil, fmt.Errorf("no committed %s subset has path %s", id, p)
	}
	return sortedUniq(vals), nil
}

// LoadProvenance reads provider's source.json from fsys.
func LoadProvenance(fsys fs.FS, provider string) (*Provenance, error) {
	raw, err := fs.ReadFile(fsys, path.Join(provider, ProvenanceFile))
	if err != nil {
		return nil, err
	}
	var p Provenance
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&p); err != nil {
		return nil, fmt.Errorf("%s/%s: %w", provider, ProvenanceFile, err)
	}
	return &p, nil
}

// ManifestEntry is one provider in unfetchable.json: the definition plus
// the last observed change signal.
type ManifestEntry struct {
	Unfetchable
	Observed *Observed `json:"observed,omitempty"`
}

// Observed is a change signal's value at a commit.
type Observed struct {
	Commit    string `json:"commit"`
	Value     string `json:"value"`
	CheckedAt string `json:"checked_at"`
}

// Manifest is unfetchable.json.
type Manifest struct {
	Schema    int             `json:"schema"`
	Note      string          `json:"note"`
	Providers []ManifestEntry `json:"providers"`
}

// LoadManifest reads unfetchable.json from fsys.
func LoadManifest(fsys fs.FS) (*Manifest, error) {
	raw, err := fs.ReadFile(fsys, ManifestFile)
	if err != nil {
		return nil, err
	}
	var m Manifest
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&m); err != nil {
		return nil, fmt.Errorf("%s: %w", ManifestFile, err)
	}
	return &m, nil
}

// Entry returns the manifest's entry for provider.
func (m *Manifest) Entry(provider string) *ManifestEntry {
	for i := range m.Providers {
		if m.Providers[i].Provider == provider {
			return &m.Providers[i]
		}
	}
	return nil
}

// WriteProvider replaces provider's directory under dir with subsets and
// prov: root files neither written nor named in keepRoots (roots that failed
// to extract this time, left as they were) are removed, so a dropped root
// cannot linger.
func WriteProvider(dir string, prov *Provenance, subsets []*Subset, keepRoots ...string) error {
	pdir := filepath.Join(dir, prov.Provider)
	if err := os.MkdirAll(pdir, 0o755); err != nil {
		return err
	}
	keep := map[string]bool{ProvenanceFile: true}
	for _, r := range keepRoots {
		keep[r+".json"] = true
	}
	for _, s := range subsets {
		raw, err := s.Marshal()
		if err != nil {
			return err
		}
		name := s.Root + ".json"
		keep[name] = true
		if err := os.WriteFile(filepath.Join(pdir, name), raw, 0o644); err != nil {
			return err
		}
	}
	entries, err := os.ReadDir(pdir)
	if err != nil {
		return err
	}
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".json") && !keep[e.Name()] {
			if err := os.Remove(filepath.Join(pdir, e.Name())); err != nil {
				return err
			}
		}
	}
	raw, err := marshalIndent(prov)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(pdir, ProvenanceFile), raw, 0o644)
}

// WriteManifest writes unfetchable.json under dir.
func WriteManifest(dir string, m *Manifest) error {
	raw, err := marshalIndent(m)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, ManifestFile), raw, 0o644)
}

func marshalIndent(v any) ([]byte, error) {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return b.Bytes(), nil
}
