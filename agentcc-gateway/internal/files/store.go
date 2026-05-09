package files

import (
	"fmt"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// StoredFile holds file metadata and content.
type StoredFile struct {
	Meta    models.FileObject
	OrgID   string
	Content []byte
}

// Store is a thread-safe in-memory file store.
type Store struct {
	mu    sync.RWMutex
	files map[string]*StoredFile
	seq   int64
}

// NewStore creates a new file store.
func NewStore() *Store {
	return &Store{
		files: make(map[string]*StoredFile),
	}
}

// Upload stores a file and returns its metadata.
func (s *Store) Upload(orgID, filename, purpose string, content []byte) *models.FileObject {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.seq++
	id := fmt.Sprintf("file-%d-%d", time.Now().Unix(), s.seq)

	meta := models.FileObject{
		ID:        id,
		Object:    "file",
		Bytes:     len(content),
		CreatedAt: time.Now().Unix(),
		Filename:  filename,
		Purpose:   purpose,
		Status:    "uploaded",
	}

	s.files[id] = &StoredFile{
		Meta:    meta,
		OrgID:   orgID,
		Content: content,
	}

	return &meta
}

// Get retrieves a file by ID with org isolation.
func (s *Store) Get(id, orgID string) *StoredFile {
	s.mu.RLock()
	defer s.mu.RUnlock()

	f := s.files[id]
	if f == nil {
		return nil
	}
	if f.OrgID != "" && orgID != "" && f.OrgID != orgID {
		return nil
	}
	return f
}

// List returns all files for an org.
func (s *Store) List(orgID, purpose string) []models.FileObject {
	s.mu.RLock()
	defer s.mu.RUnlock()

	var result []models.FileObject
	for _, f := range s.files {
		// Filter by org.
		if f.OrgID != "" && orgID != "" && f.OrgID != orgID {
			continue
		}
		// Filter by purpose if specified.
		if purpose != "" && f.Meta.Purpose != purpose {
			continue
		}
		result = append(result, f.Meta)
	}
	return result
}

// Delete removes a file with org isolation. Returns true if deleted.
func (s *Store) Delete(id, orgID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()

	f := s.files[id]
	if f == nil {
		return false
	}
	if f.OrgID != "" && orgID != "" && f.OrgID != orgID {
		return false
	}
	delete(s.files, id)
	return true
}

// Count returns the number of files in the store.
func (s *Store) Count() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.files)
}
