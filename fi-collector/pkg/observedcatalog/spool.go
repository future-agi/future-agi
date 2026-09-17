package observedcatalog

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"

	"golang.org/x/sys/unix"
)

type SpoolConfig struct {
	Directory string
	MaxFiles  int
	MaxBytes  int64
}

// Writer transfers ownership synchronously to a local durable spool. It does
// not close the preceding canonical-commit crash gap; bounded backfill repairs
// that gap. The directory must survive restart and have a single owner.
type Writer struct {
	cfg      SpoolConfig
	limits   Limits
	mu       sync.Mutex
	replayMu sync.Mutex
	files    map[string]int64
	bytes    int64
	lock     *os.File
	closed   bool
	syncDir  func(string) error
}

func NewWriter(cfg SpoolConfig, limits Limits) (*Writer, error) {
	if cfg.MaxFiles == 0 {
		cfg.MaxFiles = 10000
	}
	if cfg.MaxBytes == 0 {
		cfg.MaxBytes = 512 << 20
	}
	if cfg.Directory == "" || !filepath.IsAbs(cfg.Directory) || cfg.MaxFiles <= 0 || cfg.MaxFiles > 1000000 || cfg.MaxBytes <= 0 {
		return nil, errors.New("observedcatalog: invalid spool configuration")
	}
	if err := makeSpoolDirectory(cfg.Directory); err != nil {
		return nil, errors.New("observedcatalog: cannot create spool")
	}
	info, err := os.Lstat(cfg.Directory)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("observedcatalog: spool must be a real directory")
	}
	fd, err := unix.Open(filepath.Join(cfg.Directory, "observed.lock"), unix.O_CREAT|unix.O_RDWR|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, errors.New("observedcatalog: cannot open spool lock")
	}
	lock := os.NewFile(uintptr(fd), "observed.lock")
	if err := unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB); err != nil {
		lock.Close()
		return nil, errors.New("observedcatalog: spool is already owned")
	}
	w := &Writer{cfg: cfg, limits: limits, files: map[string]int64{}, lock: lock, syncDir: syncDirectory}
	ok := false
	defer func() {
		if !ok {
			w.Close()
		}
	}()
	entries, err := os.ReadDir(cfg.Directory)
	if err != nil {
		return nil, errors.New("observedcatalog: cannot read spool")
	}
	orphaned := 0
	for _, entry := range entries {
		// A crash before rename never acknowledged ownership. Discard only
		// our regular temporary files while holding the exclusive owner lock;
		// otherwise repeated interrupted writes could bypass capacity limits.
		if strings.HasPrefix(entry.Name(), ".observed-tmp-") {
			info, err := entry.Info()
			if err != nil || !info.Mode().IsRegular() {
				return nil, errors.New("observedcatalog: invalid spool temporary entry")
			}
			if err := os.Remove(filepath.Join(cfg.Directory, entry.Name())); err != nil {
				return nil, errors.New("observedcatalog: cannot remove interrupted spool temporary")
			}
			orphaned++
			continue
		}
		if !strings.HasPrefix(entry.Name(), "observed-") || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		info, err := entry.Info()
		if err != nil || !info.Mode().IsRegular() || info.Size() > MaxRecordBytes || info.Size() <= 0 {
			return nil, errors.New("observedcatalog: invalid spool entry")
		}
		w.files[entry.Name()] = info.Size()
		w.bytes += info.Size()
		if len(w.files) > cfg.MaxFiles || w.bytes > cfg.MaxBytes {
			return nil, errors.New("observedcatalog: existing spool exceeds configured capacity")
		}
	}
	if err := w.syncDir(cfg.Directory); err != nil {
		return nil, err
	}
	if orphaned > 0 {
		slog.Warn("observed catalog interrupted pre-handoff temporaries removed; bounded backfill repair required", "files", orphaned)
	}
	ok = true
	return w, nil
}

// Enqueue persists every transport chunk. An error may follow partial durable
// progress: those chunks remain replayable and retries may duplicate them.
func (w *Writer) Enqueue(ctx context.Context, batch Batch) error {
	chunks, err := Chunk(batch)
	if err != nil {
		return err
	}
	for _, chunk := range chunks {
		if err := ctx.Err(); err != nil {
			return err
		}
		raw, err := Encode(chunk)
		if err != nil {
			return err
		}
		if err := w.save(raw); err != nil {
			return err
		}
	}
	return nil
}

func (w *Writer) save(raw []byte) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.closed {
		return errors.New("observedcatalog: spool closed")
	}
	digest := sha256.Sum256(raw)
	name := "observed-" + hex.EncodeToString(digest[:]) + ".json"
	if _, exists := w.files[name]; exists {
		prior, err := os.ReadFile(filepath.Join(w.cfg.Directory, name))
		if err != nil || string(prior) != string(raw) {
			return errors.New("observedcatalog: spool identity conflict")
		}
		return w.syncDir(w.cfg.Directory)
	}
	if len(w.files) >= w.cfg.MaxFiles || int64(len(raw)) > w.cfg.MaxBytes-w.bytes {
		return errors.New("observedcatalog: spool capacity exceeded; backfill repair required")
	}
	f, err := os.CreateTemp(w.cfg.Directory, ".observed-tmp-")
	if err != nil {
		return errors.New("observedcatalog: cannot create spool record")
	}
	tmp := f.Name()
	defer func() { f.Close(); os.Remove(tmp) }()
	if _, err = f.Write(raw); err != nil {
		return errors.New("observedcatalog: spool write failed")
	}
	if err = f.Sync(); err != nil {
		return errors.New("observedcatalog: spool fsync failed")
	}
	if err = f.Close(); err != nil {
		return errors.New("observedcatalog: spool close failed")
	}
	if err = os.Rename(tmp, filepath.Join(w.cfg.Directory, name)); err != nil {
		return errors.New("observedcatalog: spool rename failed")
	}
	// Count a visible file even if the following directory fsync is uncertain.
	w.files[name] = int64(len(raw))
	w.bytes += int64(len(raw))
	return w.syncDir(w.cfg.Directory)
}

func (w *Writer) EnqueueCanonicalSpans(spans []ScopedSpan) error {
	var failures int
	var last error
	// Coalesce a bounded number of source spans, then chunk by actual bytes.
	// A large span is flushed independently; no total byte truncation is used.
	batch := Batch{}
	flush := func() {
		if !batch.Empty() {
			if err := w.Enqueue(context.Background(), Merge(batch)); err != nil {
				failures++
				last = err
			}
			batch = Batch{}
		}
	}
	for _, span := range spans {
		built, report, err := Extract(span, w.limits)
		if err != nil {
			failures++
			last = err
			continue
		}
		if !report.Complete {
			failures++
			last = fmt.Errorf("observedcatalog: incomplete extraction: %s", strings.Join(report.GapReasons, ","))
		}
		// Only coalesce small rows. Larger batches go directly through chunking.
		batch.Keys = append(batch.Keys, built.Keys...)
		batch.Values = append(batch.Values, built.Values...)
		if len(batch.Keys)+len(batch.Values) >= MaxRecordRows/4 {
			flush()
		}
	}
	flush()
	if failures > 0 {
		return fmt.Errorf("observedcatalog: %d catalog gaps; bounded backfill repair required: %w", failures, last)
	}
	return nil
}

// Replay retains each file until Kafka acknowledges all its observations.
func (w *Writer) Replay(ctx context.Context, publisher Publisher) (int, error) {
	w.replayMu.Lock()
	defer w.replayMu.Unlock()
	w.mu.Lock()
	names := make([]string, 0, len(w.files))
	for name := range w.files {
		names = append(names, name)
	}
	closed := w.closed
	w.mu.Unlock()
	if closed || publisher == nil {
		return 0, errors.New("observedcatalog: replay unavailable")
	}
	sort.Strings(names)
	delivered := 0
	for _, name := range names {
		if err := ctx.Err(); err != nil {
			return delivered, err
		}
		path := filepath.Join(w.cfg.Directory, name)
		info, err := os.Lstat(path)
		if err != nil || !info.Mode().IsRegular() || info.Size() > MaxRecordBytes {
			return delivered, errors.New("observedcatalog: invalid spool record")
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return delivered, errors.New("observedcatalog: cannot read spool record")
		}
		digest := sha256.Sum256(raw)
		if name != "observed-"+hex.EncodeToString(digest[:])+".json" {
			return delivered, errors.New("observedcatalog: corrupt spool digest")
		}
		batch, err := Decode(raw)
		if err != nil {
			return delivered, err
		}
		if err = publisher.Publish(ctx, batch); err != nil {
			return delivered, err
		}
		w.mu.Lock()
		err = os.Remove(path)
		if err == nil {
			w.bytes -= w.files[name]
			delete(w.files, name)
			err = w.syncDir(w.cfg.Directory)
		}
		w.mu.Unlock()
		if err != nil {
			return delivered, errors.New("observedcatalog: spool removal not confirmed")
		}
		delivered++
	}
	return delivered, nil
}

func (w *Writer) Close() error {
	w.replayMu.Lock()
	defer w.replayMu.Unlock()
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.closed {
		return nil
	}
	w.closed = true
	if w.lock == nil {
		return nil
	}
	err := unix.Flock(int(w.lock.Fd()), unix.LOCK_UN)
	return errors.Join(err, w.lock.Close())
}

func syncDirectory(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return errors.New("observedcatalog: open spool directory failed")
	}
	defer f.Close()
	if err = f.Sync(); err != nil {
		return errors.New("observedcatalog: directory fsync failed")
	}
	return nil
}

// Persist each newly created directory's parent entry, not only the eventual
// record rename. Existing directories need no creation acknowledgement.
func makeSpoolDirectory(path string) error {
	info, err := os.Lstat(path)
	if err == nil {
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return errors.New("observedcatalog: spool ancestor must be a real directory")
		}
		return nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	parent := filepath.Dir(path)
	if err := makeSpoolDirectory(parent); err != nil {
		return err
	}
	if err := os.Mkdir(path, 0700); err != nil && !errors.Is(err, os.ErrExist) {
		return err
	}
	return syncDirectory(parent)
}
