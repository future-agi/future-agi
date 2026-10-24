package catalog

import (
	"compress/gzip"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// The snapshot directory (catalog.snapshot_dir) holds:
//
//	snapshots/<id>.json.gz  the full content of each kept catalog version
//	history.json            the history ring
//	blocks.json             the runtime blocks (PUT /-/catalog/blocks)
const (
	snapshotsSubdir = "snapshots"
	historyFile     = "history.json"
	blocksFile      = "blocks.json"
	snapshotExt     = ".json.gz"
)

type persister struct{ dir string }

func newPersister(dir string) (*persister, error) {
	if err := os.MkdirAll(filepath.Join(dir, snapshotsSubdir), 0o750); err != nil {
		return nil, fmt.Errorf("catalog.snapshot_dir %q: %w", dir, err)
	}
	return &persister{dir: dir}, nil
}

// catalogJSON is a catalog version as it is persisted and served by
// GET /-/catalog/snapshots/{id}.
type catalogJSON struct {
	ID        string         `json:"id"`
	Providers []snapshotJSON `json:"providers"`
}

type snapshotJSON struct {
	ProviderID    string    `json:"provider"`
	Source        string    `json:"source"`
	CatalogSource string    `json:"catalog_source,omitempty"`
	FetchedAt     time.Time `json:"fetched_at"`
	ZDR           []string  `json:"zdr,omitempty"`
	Models        []*Model  `json:"models"`
}

// MarshalJSON writes the catalog version with every provider's models.
func (c *Catalog) MarshalJSON() ([]byte, error) {
	out := catalogJSON{ID: c.id, Providers: []snapshotJSON{}}
	for _, s := range c.Snapshots() {
		zdr := make([]string, 0, len(s.ZDR))
		for k := range s.ZDR {
			zdr = append(zdr, k)
		}
		sort.Strings(zdr)
		out.Providers = append(out.Providers, snapshotJSON{ProviderID: s.ProviderID, Source: s.Source,
			CatalogSource: s.CatalogSource, FetchedAt: s.FetchedAt, ZDR: zdr, Models: s.Sorted()})
	}
	return json.Marshal(out)
}

// UnmarshalJSON reads what MarshalJSON wrote.
func (c *Catalog) UnmarshalJSON(b []byte) error {
	var in catalogJSON
	if err := json.Unmarshal(b, &in); err != nil {
		return err
	}
	c.id = in.ID
	c.byProvider = make(map[string]*Snapshot, len(in.Providers))
	for _, p := range in.Providers {
		snap := &Snapshot{ProviderID: p.ProviderID, Source: p.Source, CatalogSource: p.CatalogSource,
			FetchedAt: p.FetchedAt, Models: make(map[string]*Model, len(p.Models))}
		if p.ZDR != nil {
			snap.ZDR = make(map[string]bool, len(p.ZDR))
			for _, k := range p.ZDR {
				snap.ZDR[k] = true
			}
		}
		for _, m := range p.Models {
			snap.Models[m.ID] = m
		}
		c.byProvider[p.ProviderID] = snap
	}
	return nil
}

func (p *persister) snapshotPath(id string) string {
	return filepath.Join(p.dir, snapshotsSubdir, id+snapshotExt)
}

func (p *persister) saveSnapshot(c *Catalog) error {
	b, err := json.Marshal(c)
	if err != nil {
		return err
	}
	return writeAtomic(p.snapshotPath(c.id), func(f *os.File) error {
		zw := gzip.NewWriter(f)
		if _, err := zw.Write(b); err != nil {
			return err
		}
		return zw.Close()
	})
}

func (p *persister) loadSnapshot(id string) (*Catalog, error) {
	f, err := os.Open(p.snapshotPath(id))
	if err != nil {
		return nil, err
	}
	defer f.Close()
	zr, err := gzip.NewReader(f)
	if err != nil {
		return nil, err
	}
	defer zr.Close()
	c := &Catalog{}
	if err := json.NewDecoder(zr).Decode(c); err != nil {
		return nil, err
	}
	return c, nil
}

// keptOnDisk lists the persisted versions, oldest first.
func (p *persister) keptOnDisk() []string {
	entries, err := os.ReadDir(filepath.Join(p.dir, snapshotsSubdir))
	if err != nil {
		return nil
	}
	type file struct {
		id  string
		mod time.Time
	}
	var files []file
	for _, e := range entries {
		id := strings.TrimSuffix(e.Name(), snapshotExt)
		if e.IsDir() || id == e.Name() || !snapshotIDPattern.MatchString(id) {
			continue
		}
		if info, err := e.Info(); err == nil {
			files = append(files, file{id, info.ModTime()})
		}
	}
	sort.SliceStable(files, func(i, j int) bool { return files[i].mod.Before(files[j].mod) })
	out := make([]string, len(files))
	for i, f := range files {
		out[i] = f.id
	}
	return out
}

// prune deletes every persisted version not in keep.
func (p *persister) prune(keep []string) error {
	var errs []error
	for _, id := range p.keptOnDisk() {
		if !contains(keep, id) {
			if err := os.Remove(p.snapshotPath(id)); err != nil && !errors.Is(err, fs.ErrNotExist) {
				errs = append(errs, err)
			}
		}
	}
	return errors.Join(errs...)
}

func (p *persister) saveHistory(entries []HistoryEntry) error {
	return writeJSONFile(filepath.Join(p.dir, historyFile), struct {
		Entries []HistoryEntry `json:"entries"`
	}{entries})
}

func (p *persister) loadHistory() ([]HistoryEntry, error) {
	var v struct {
		Entries []HistoryEntry `json:"entries"`
	}
	err := readJSONFile(filepath.Join(p.dir, historyFile), &v)
	return v.Entries, err
}

type blockJSON struct {
	Model  string `json:"model"`
	Reason string `json:"reason"`
}

func (p *persister) saveBlocks(blocks map[string]string) error {
	list := make([]blockJSON, 0, len(blocks))
	for m, r := range blocks {
		list = append(list, blockJSON{m, r})
	}
	sort.Slice(list, func(i, j int) bool { return list[i].Model < list[j].Model })
	return writeJSONFile(filepath.Join(p.dir, blocksFile), struct {
		Blocks []blockJSON `json:"blocks"`
	}{list})
}

func (p *persister) loadBlocks() (map[string]string, error) {
	var v struct {
		Blocks []blockJSON `json:"blocks"`
	}
	if err := readJSONFile(filepath.Join(p.dir, blocksFile), &v); err != nil {
		return nil, err
	}
	out := make(map[string]string, len(v.Blocks))
	for _, b := range v.Blocks {
		out[b.Model] = b.Reason
	}
	return out, nil
}

// loadPersistedLocked reads the snapshot directory back: runtime blocks,
// history, the kept versions, and, for each configured provider, the newest
// kept version's snapshot when the store has none for it or it is fresher
// than the one it has (a restart while the upstream is down keeps the last
// live prices, not the bundled ones). A missing file is an empty one; an
// unreadable one is logged.
func (s *Store) loadPersistedLocked() {
	p := s.persist
	if blocks, err := p.loadBlocks(); err != nil {
		logPersistError("blocks (read)", err)
	} else if blocks != nil {
		s.runtimeBlocks = blocks
	}
	if hist, err := p.loadHistory(); err != nil {
		logPersistError("history (read)", err)
	} else {
		if n := len(hist) - s.opts.HistorySize; n > 0 {
			hist = hist[n:]
		}
		s.history = hist
	}
	// The kept versions are the ones on disk. Versions put before the
	// directory was configured (the bundled seed) were never served; the
	// one that stays current is kept by Configure.
	onDisk := p.keptOnDisk()
	if len(onDisk) > 0 {
		s.restoreNewestLocked(onDisk[len(onDisk)-1])
	}
	if n := len(onDisk) - s.opts.KeepSnapshots; n > 0 {
		onDisk = onDisk[n:]
	}
	s.keptIDs, s.keptMem = onDisk, map[string]*Catalog{}
	logPersistError("snapshot pruning", p.prune(s.keptIDs))
}

// restoreNewestLocked takes from the previous run's newest version (id) the
// snapshot of each configured provider the store has none for, or an older
// one.
func (s *Store) restoreNewestLocked(id string) {
	newest, err := s.persist.loadSnapshot(id)
	if err != nil {
		logPersistError("newest snapshot (read)", err)
		return
	}
	for id, snap := range newest.byProvider {
		cur, ok := s.base[id]
		if ok && snap.FetchedAt.After(cur.FetchedAt) || !ok && contains(s.opts.Providers, id) {
			restored := *snap
			restored.Source = OriginRestored
			s.base[id] = &restored
		}
	}
}

// writeAtomic writes path through a temporary file renamed into place, so a
// crash never leaves a half-written file.
func writeAtomic(path string, write func(*os.File) error) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), ".tmp-"+filepath.Base(path)+"-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name()) // no-op once renamed
	if err := write(tmp); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

func writeJSONFile(path string, v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return writeAtomic(path, func(f *os.File) error {
		_, err := f.Write(b)
		return err
	})
}

// readJSONFile reads path into v; a missing file leaves v untouched.
func readJSONFile(path string, v any) error {
	b, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	return json.Unmarshal(b, v)
}
