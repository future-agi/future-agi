package cache

import (
	"fmt"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// NewBackend creates a cache Backend from config.
// Returns a MemoryBackend wrapping a new Store if backend is "memory" or unset.
func NewBackend(cfg config.CacheConfig) (Backend, error) {
	switch cfg.Backend {
	case "", "memory":
		store := NewStore(cfg.MaxEntries)
		slog.Info("cache backend: memory", "max_entries", cfg.MaxEntries)
		return NewMemoryBackend(store), nil

	case "disk":
		d := cfg.Disk
		backend, err := NewDiskBackend(d.Directory, d.MaxSizeBytes, d.Compress, d.CleanupInterval)
		if err != nil {
			return nil, fmt.Errorf("disk cache: %w", err)
		}
		slog.Info("cache backend: disk", "directory", d.Directory, "max_size", d.MaxSizeBytes)
		return backend, nil

	case "s3":
		s := cfg.S3
		backend := NewS3Backend(s.Bucket, s.Prefix, s.Region, s.AccessKeyID, s.SecretAccessKey, s.Compress, s.Timeout)
		slog.Info("cache backend: s3", "bucket", s.Bucket, "region", s.Region)
		return backend, nil

	case "azure-blob":
		a := cfg.AzureBlob
		backend, err := NewAzBlobBackend(a.ConnectionString, a.Container, a.Prefix, a.Compress, a.Timeout)
		if err != nil {
			return nil, fmt.Errorf("azure blob cache: %w", err)
		}
		slog.Info("cache backend: azure-blob", "container", a.Container)
		return backend, nil

	case "gcs":
		g := cfg.GCS
		backend, err := NewGCSBackend(g.Bucket, g.Prefix, g.CredentialsFile, g.Compress, g.Timeout)
		if err != nil {
			return nil, fmt.Errorf("gcs cache: %w", err)
		}
		slog.Info("cache backend: gcs", "bucket", g.Bucket)
		return backend, nil

	case "redis":
		r := cfg.Redis
		addr := r.Address
		if addr == "" && len(r.Addresses) > 0 {
			addr = r.Addresses[0]
		}
		backend, err := NewRedisBackend(addr, r.Password, r.DB, r.KeyPrefix, r.Compress, r.PoolSize, r.Timeout)
		if err != nil {
			return nil, fmt.Errorf("redis cache: %w", err)
		}
		slog.Info("cache backend: redis", "address", addr, "mode", r.Mode)
		return backend, nil

	default:
		return nil, fmt.Errorf("unknown cache backend %q (supported: memory, disk, s3, azure-blob, gcs, redis)", cfg.Backend)
	}
}

// NewSemanticBackend creates a SemanticBackend from config.
// Returns a MemorySemanticBackend wrapping a new SemanticStore if backend is "memory" or unset.
func NewSemanticBackend(cfg config.SemanticCacheConfig) (SemanticBackend, error) {
	threshold := cfg.Threshold
	dims := cfg.Dimensions

	switch cfg.Backend {
	case "", "memory":
		store := NewSemanticStore(threshold, dims, cfg.MaxEntries)
		slog.Info("semantic cache backend: memory", "threshold", threshold, "dims", dims)
		return NewMemorySemanticBackend(store), nil

	case "qdrant":
		q := cfg.Qdrant
		backend, err := NewQdrantBackend(q.URL, q.Collection, q.APIKey, threshold, dims, q.Timeout)
		if err != nil {
			return nil, fmt.Errorf("qdrant semantic cache: %w", err)
		}
		slog.Info("semantic cache backend: qdrant", "url", q.URL, "collection", q.Collection)
		return backend, nil

	case "weaviate":
		w := cfg.Weaviate
		backend, err := NewWeaviateBackend(w.URL, w.Class, w.APIKey, threshold, dims, w.Timeout)
		if err != nil {
			return nil, fmt.Errorf("weaviate semantic cache: %w", err)
		}
		slog.Info("semantic cache backend: weaviate", "url", w.URL, "class", w.Class)
		return backend, nil

	case "pinecone":
		p := cfg.Pinecone
		backend, err := NewPineconeBackend(p.URL, p.APIKey, threshold, dims, p.Timeout)
		if err != nil {
			return nil, fmt.Errorf("pinecone semantic cache: %w", err)
		}
		slog.Info("semantic cache backend: pinecone", "url", p.URL)
		return backend, nil

	default:
		return nil, fmt.Errorf("unknown semantic cache backend %q (supported: memory, qdrant, weaviate, pinecone)", cfg.Backend)
	}
}
