package contractspec

import (
	"bytes"
	"io/fs"
	"path"
	"reflect"
	"sort"
	"strings"
	"testing"
)

// The committed subsets are what every provider's spec test reads, so they
// must parse, be in canonical form (what cmd/contract-specs -update writes),
// and match the definitions and their provenance.
func TestCommittedSubsets(t *testing.T) {
	fsys := Committed()
	for _, src := range Sources() {
		t.Run(src.Provider, func(t *testing.T) {
			subsets, err := LoadFS(fsys, src.Provider)
			if err != nil {
				t.Fatal(err)
			}
			var roots, defined []string
			for _, s := range subsets {
				roots = append(roots, s.Root)
				raw, _ := fs.ReadFile(fsys, path.Join(src.Provider, s.Root+".json"))
				canon, err := s.Marshal()
				if err != nil {
					t.Fatal(err)
				}
				if !bytes.Equal(raw, canon) {
					t.Errorf("%s is not in canonical form; regenerate it with cmd/contract-specs -update", s.ID())
				}
				if s.Format != src.Format {
					t.Errorf("%s format %q, definition says %q", s.ID(), s.Format, src.Format)
				}
			}
			for _, r := range src.Roots {
				defined = append(defined, r.Name)
			}
			sort.Strings(defined)
			if !reflect.DeepEqual(roots, defined) {
				t.Errorf("committed roots %v, defined roots %v: regenerate with cmd/contract-specs -fetch -update -provider %s", roots, defined, src.Provider)
			}
			prov, err := LoadProvenance(fsys, src.Provider)
			if err != nil {
				t.Fatalf("provenance: %v", err)
			}
			if !reflect.DeepEqual(prov.Roots, defined) {
				t.Errorf("source.json roots %v, defined roots %v", prov.Roots, defined)
			}
			if prov.SHA256 == "" || prov.FetchedAt == "" || prov.URL == "" {
				t.Errorf("source.json lacks url, sha256 or fetched_at: %+v", prov)
			}
			if src.Repo != "" {
				if prov.Repo != src.Repo || len(prov.Commit) != 40 || !strings.Contains(prov.URL, prov.Commit) {
					t.Errorf("source.json must pin %s at a commit SHA in its URL: %+v", src.Repo, prov)
				}
			} else if prov.SpecVersion["revision"] == "" {
				t.Errorf("source.json for a discovery source must record its revision: %+v", prov)
			}
		})
	}
}

func TestCommittedManifest(t *testing.T) {
	m, err := LoadManifest(Committed())
	if err != nil {
		t.Fatal(err)
	}
	for _, u := range UnfetchableProviders() {
		e := m.Entry(u.Provider)
		if e == nil {
			t.Errorf("%s is not in %s", u.Provider, ManifestFile)
			continue
		}
		if e.Reason == "" {
			t.Errorf("%s: no reason", u.Provider)
		}
		if u.Signal != nil && (e.Observed == nil || e.Observed.Value == "") {
			t.Errorf("%s: change signal never observed; run cmd/contract-specs -fetch -update -provider %s", u.Provider, u.Provider)
		}
		if !reflect.DeepEqual(e.Signal, u.Signal) {
			t.Errorf("%s: manifest signal %+v, definition %+v", u.Provider, e.Signal, u.Signal)
		}
	}
	if len(m.Providers) != len(UnfetchableProviders()) {
		t.Errorf("%s lists %d providers, the definitions %d", ManifestFile, len(m.Providers), len(UnfetchableProviders()))
	}
}

func TestEnums(t *testing.T) {
	got, err := Enums("anthropic/messages", "$.stop_reason")
	if err != nil {
		t.Fatal(err)
	}
	for _, v := range []string{"end_turn", "max_tokens", "tool_use"} {
		found := false
		for _, g := range got {
			found = found || g == v
		}
		if !found {
			t.Errorf("anthropic stop reasons %v lack %q", got, v)
		}
	}
	if _, err := Enums("anthropic", "$.no.such.path"); err == nil {
		t.Error("a path no subset has was accepted")
	}
}
