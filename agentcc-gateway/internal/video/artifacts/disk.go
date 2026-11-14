package artifacts

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path"
	"strings"
)

// One fixed-size metadata header and the payload share an atomically renamed
// file, avoiding split metadata/data commits. Root prevents symlink escapes.
const diskHeaderSize = 4096

type Disk struct {
	root *os.Root
	max  int64
}

var _ Store = (*Disk)(nil)

func NewDisk(dir string, max int64) (*Disk, error) {
	if max <= 0 {
		return nil, ErrTooLarge
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, err
	}
	r, err := os.OpenRoot(dir)
	if err != nil {
		return nil, err
	}
	return &Disk{root: r, max: max}, nil
}
func (d *Disk) Close() error { return d.root.Close() }
func (d *Disk) Put(ctx context.Context, key string, r io.Reader, m Meta) (Meta, error) {
	if !validKey(key) {
		return Meta{}, ErrKey
	}
	if err := ctx.Err(); err != nil {
		return Meta{}, err
	}
	if m.Bytes > d.max {
		return Meta{}, ErrTooLarge
	}
	if err := d.mkdirAll(path.Dir(key)); err != nil {
		return Meta{}, err
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return Meta{}, err
	}
	tmp := path.Join(path.Dir(key), ".upload-"+hex.EncodeToString(nonce[:]))
	f, err := d.root.OpenFile(tmp, os.O_RDWR|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return Meta{}, err
	}
	defer f.Close()
	defer d.root.Remove(tmp)
	if _, err = f.Write(make([]byte, diskHeaderSize)); err != nil {
		return Meta{}, err
	}
	br := newBounded(ctx, r, d.max)
	if _, err = io.Copy(f, br); err != nil {
		return Meta{}, err
	}
	m, err = br.metadata(m)
	if err != nil {
		return Meta{}, err
	}
	header, err := json.Marshal(m)
	if err != nil {
		return Meta{}, err
	}
	if len(header) >= diskHeaderSize {
		return Meta{}, ErrTooLarge
	}
	if _, err = f.WriteAt(header, 0); err != nil {
		return Meta{}, err
	}
	if err = f.Sync(); err != nil {
		return Meta{}, err
	}
	if err = f.Close(); err != nil {
		return Meta{}, err
	}
	if err = ctx.Err(); err != nil {
		return Meta{}, err
	}
	if err = d.root.Rename(tmp, key); err != nil {
		return Meta{}, err
	}
	dir, err := d.root.Open(path.Dir(key))
	if err != nil {
		return Meta{}, err
	}
	defer dir.Close()
	if err = dir.Sync(); err != nil {
		return Meta{}, err
	}
	return m, nil
}
func (d *Disk) open(ctx context.Context, key string) (*os.File, Meta, error) {
	if !validKey(key) {
		return nil, Meta{}, ErrKey
	}
	if err := ctx.Err(); err != nil {
		return nil, Meta{}, err
	}
	f, err := d.root.Open(key)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			err = ErrNotFound
		}
		return nil, Meta{}, err
	}
	fail := func(err error) (*os.File, Meta, error) { f.Close(); return nil, Meta{}, err }
	var header [diskHeaderSize]byte
	if _, err = io.ReadFull(f, header[:]); err != nil {
		return fail(err)
	}
	var m Meta
	if err = json.Unmarshal(bytes.TrimRight(header[:], "\x00"), &m); err != nil {
		return fail(err)
	}
	st, err := f.Stat()
	if err != nil {
		return fail(err)
	}
	if !st.Mode().IsRegular() || m.Bytes < 0 || st.Size()-diskHeaderSize != m.Bytes {
		return fail(ErrSizeMismatch)
	}
	return f, m, nil
}
func (d *Disk) Stat(ctx context.Context, key string) (Meta, error) {
	f, m, e := d.open(ctx, key)
	if e == nil {
		e = f.Close()
	}
	return m, e
}
func (d *Disk) Open(ctx context.Context, key, rng string) (Object, error) {
	f, m, e := d.open(ctx, key)
	if e != nil {
		return Object{}, e
	}
	start, n, cr, e := parseRange(rng, m.Bytes)
	if e != nil {
		f.Close()
		return Object{}, e
	}
	return Object{Body: &sectionCloser{Reader: &contextReader{ctx: ctx, r: io.NewSectionReader(f, diskHeaderSize+start, n)}, Closer: f}, Meta: m, Length: n, ContentRange: cr}, nil
}
func (d *Disk) Delete(ctx context.Context, key string) error {
	if !validKey(key) {
		return ErrKey
	}
	if e := ctx.Err(); e != nil {
		return e
	}
	e := d.root.Remove(key)
	if errors.Is(e, os.ErrNotExist) {
		return nil
	}
	return e
}

type contextReader struct {
	ctx context.Context
	r   io.Reader
}

func (r *contextReader) Read(p []byte) (int, error) {
	if e := r.ctx.Err(); e != nil {
		return 0, e
	}
	return r.r.Read(p)
}

// Root.MkdirAll can return EEXIST when another replica creates an intermediate
// directory. Check each component on EEXIST, retaining Root's containment.
func (d *Disk) mkdirAll(dir string) error {
	if dir == "." {
		return nil
	}
	current := ""
	for _, part := range strings.Split(dir, "/") {
		current = path.Join(current, part)
		err := d.root.Mkdir(current, 0700)
		if errors.Is(err, os.ErrExist) {
			st, statErr := d.root.Stat(current)
			if statErr != nil {
				return statErr
			}
			if !st.IsDir() {
				return err
			}
		} else if err != nil {
			return err
		}
	}
	return nil
}
