package cache

import (
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"encoding/json"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// DiskBackend stores cached responses as files on the local filesystem.
// Uses 2-level directory sharding and atomic writes.
type DiskBackend struct {
	dir             string
	maxSizeBytes    int64
	currentSize     atomic.Int64
	compress        bool
	cleanupInterval time.Duration
	done            chan struct{}

	mu    sync.RWMutex
	index map[string]*diskEntry
}

type diskEntry struct {
	path      string
	size      int64
	expiresAt time.Time
	lastUsed  time.Time
}

// NewDiskBackend creates a disk-based cache backend.
func NewDiskBackend(dir string, maxSizeBytes int64, compress bool, cleanupInterval time.Duration) (*DiskBackend, error) {
	if maxSizeBytes <= 0 {
		maxSizeBytes = 1 << 30 // 1GB default
	}
	if cleanupInterval <= 0 {
		cleanupInterval = 5 * time.Minute
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}

	d := &DiskBackend{
		dir:             dir,
		maxSizeBytes:    maxSizeBytes,
		compress:        compress,
		cleanupInterval: cleanupInterval,
		done:            make(chan struct{}),
		index:           make(map[string]*diskEntry),
	}

	// Rebuild index from existing files.
	d.rebuildIndex()

	// Start background cleanup.
	go d.cleanupLoop()

	return d, nil
}

// Get retrieves a cached response from disk.
func (d *DiskBackend) Get(key string) (*models.ChatCompletionResponse, bool) {
	d.mu.RLock()
	e, ok := d.index[key]
	d.mu.RUnlock()
	if !ok {
		return nil, false
	}

	if time.Now().After(e.expiresAt) {
		d.Delete(key)
		return nil, false
	}

	data, err := os.ReadFile(e.path)
	if err != nil {
		slog.Warn("disk cache read error", "key", key, "error", err)
		d.Delete(key)
		return nil, false
	}

	// Skip 16-byte header (8 bytes expiresAt + 8 reserved).
	if len(data) < 16 {
		d.Delete(key)
		return nil, false
	}
	payload := data[16:]

	// Decompress if needed.
	if d.compress {
		gr, err := gzip.NewReader(bytes.NewReader(payload))
		if err != nil {
			slog.Warn("disk cache decompress error", "key", key, "error", err)
			return nil, false
		}
		payload, err = io.ReadAll(gr)
		gr.Close()
		if err != nil {
			slog.Warn("disk cache decompress read error", "key", key, "error", err)
			return nil, false
		}
	}

	var resp models.ChatCompletionResponse
	if err := json.Unmarshal(payload, &resp); err != nil {
		slog.Warn("disk cache unmarshal error", "key", key, "error", err)
		return nil, false
	}

	// Update lastUsed.
	d.mu.Lock()
	if ee, ok := d.index[key]; ok {
		ee.lastUsed = time.Now()
	}
	d.mu.Unlock()

	return &resp, true
}

// Set stores a response on disk with atomic write.
func (d *DiskBackend) Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	jsonData, err := json.Marshal(resp)
	if err != nil {
		slog.Warn("disk cache marshal error", "key", key, "error", err)
		return
	}

	var payload []byte
	if d.compress {
		var buf bytes.Buffer
		gw := gzip.NewWriter(&buf)
		gw.Write(jsonData)
		gw.Close()
		payload = buf.Bytes()
	} else {
		payload = jsonData
	}

	expiresAt := time.Now().Add(ttl)

	// Build file: 16-byte header + payload.
	header := make([]byte, 16)
	binary.BigEndian.PutUint64(header[0:8], uint64(expiresAt.Unix()))
	// Bytes 8-16: reserved (zero).

	fileData := append(header, payload...)
	fileSize := int64(len(fileData))

	// Evict if needed.
	for d.currentSize.Load()+fileSize > d.maxSizeBytes {
		if !d.evictOne() {
			break
		}
	}

	// Determine file path with 2-level sharding.
	path := d.keyPath(key)
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		slog.Warn("disk cache mkdir error", "path", dir, "error", err)
		return
	}

	// Atomic write: temp file → rename.
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, fileData, 0o644); err != nil {
		slog.Warn("disk cache write error", "path", tmp, "error", err)
		return
	}
	if err := os.Rename(tmp, path); err != nil {
		slog.Warn("disk cache rename error", "path", path, "error", err)
		os.Remove(tmp)
		return
	}

	// Update index and size atomically under the same lock.
	d.mu.Lock()
	if old, ok := d.index[key]; ok {
		d.currentSize.Add(-old.size)
	}
	d.index[key] = &diskEntry{
		path:      path,
		size:      fileSize,
		expiresAt: expiresAt,
		lastUsed:  time.Now(),
	}
	d.currentSize.Add(fileSize)
	d.mu.Unlock()
}

// Delete removes a cache entry from disk and index.
func (d *DiskBackend) Delete(key string) {
	d.mu.Lock()
	e, ok := d.index[key]
	if ok {
		delete(d.index, key)
	}
	d.mu.Unlock()

	if ok {
		d.currentSize.Add(-e.size)
		os.Remove(e.path)
	}
}

// keyPath returns the file path for a cache key with 2-level sharding.
func (d *DiskBackend) keyPath(key string) string {
	if len(key) < 4 {
		return filepath.Join(d.dir, "00", "00", key+".bin")
	}
	ext := ".bin"
	return filepath.Join(d.dir, key[0:2], key[2:4], key+ext)
}

// evictOne removes the least recently used entry. Returns false if index is empty.
func (d *DiskBackend) evictOne() bool {
	d.mu.Lock()
	defer d.mu.Unlock()

	if len(d.index) == 0 {
		return false
	}

	var oldestKey string
	var oldestTime time.Time
	first := true
	for k, e := range d.index {
		if first || e.lastUsed.Before(oldestTime) {
			oldestKey = k
			oldestTime = e.lastUsed
			first = false
		}
	}

	e := d.index[oldestKey]
	delete(d.index, oldestKey)
	d.currentSize.Add(-e.size)
	os.Remove(e.path)
	return true
}

// rebuildIndex scans the disk directory and rebuilds the in-memory index.
func (d *DiskBackend) rebuildIndex() {
	now := time.Now()
	var totalSize int64

	filepath.Walk(d.dir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return nil
		}
		if filepath.Ext(path) != ".bin" {
			return nil
		}

		// Read header to get expiresAt.
		f, err := os.Open(path)
		if err != nil {
			return nil
		}
		header := make([]byte, 16)
		n, err := f.Read(header)
		f.Close()
		if err != nil || n < 16 {
			os.Remove(path)
			return nil
		}

		expiresAt := time.Unix(int64(binary.BigEndian.Uint64(header[0:8])), 0)
		if now.After(expiresAt) {
			os.Remove(path)
			return nil
		}

		// Extract key from filename.
		base := filepath.Base(path)
		key := base[:len(base)-len(filepath.Ext(base))]

		size := info.Size()
		d.index[key] = &diskEntry{
			path:      path,
			size:      size,
			expiresAt: expiresAt,
			lastUsed:  info.ModTime(),
		}
		totalSize += size
		return nil
	})

	d.currentSize.Store(totalSize)
	if len(d.index) > 0 {
		slog.Info("disk cache index rebuilt", "entries", len(d.index), "total_size", totalSize)
	}
}

// cleanupLoop periodically removes expired entries.
func (d *DiskBackend) cleanupLoop() {
	ticker := time.NewTicker(d.cleanupInterval)
	defer ticker.Stop()
	for {
		select {
		case <-d.done:
			return
		case <-ticker.C:
			d.cleanupExpired()
		}
	}
}

func (d *DiskBackend) cleanupExpired() {
	now := time.Now()
	var expired []string

	d.mu.RLock()
	for k, e := range d.index {
		if now.After(e.expiresAt) {
			expired = append(expired, k)
		}
	}
	d.mu.RUnlock()

	for _, k := range expired {
		d.Delete(k)
	}

	if len(expired) > 0 {
		slog.Debug("disk cache cleanup", "expired", len(expired))
	}
}

// Close stops the background cleanup goroutine.
func (d *DiskBackend) Close() {
	close(d.done)
}

// sortedByLRU returns index entries sorted by lastUsed ascending (oldest first).
func (d *DiskBackend) sortedByLRU() []string {
	d.mu.RLock()
	keys := make([]string, 0, len(d.index))
	for k := range d.index {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		return d.index[keys[i]].lastUsed.Before(d.index[keys[j]].lastUsed)
	})
	d.mu.RUnlock()
	return keys
}
