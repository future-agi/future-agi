package artifacts

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/providers/video/videotest"
	"github.com/futureagi/agentcc-gateway/internal/video/media"
)

func disk(t *testing.T, cap int64) *Disk {
	t.Helper()
	d, e := NewDisk(t.TempDir(), cap)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { d.Close() })
	return d
}
func TestDisk_StreamingRangeStatDelete(t *testing.T) {
	ctx := context.Background()
	d := disk(t, 20)
	m := Meta{ContentType: "video/mp4", ExpiresAt: time.Now().Add(time.Hour).UTC().Truncate(time.Second)}
	got, e := d.Put(ctx, "video/org/job/0.mp4", strings.NewReader("0123456789"), m)
	if e != nil || got.Bytes != 10 || len(got.Digest) != 64 {
		t.Fatal(got, e)
	}
	stat, e := d.Stat(ctx, "video/org/job/0.mp4")
	if e != nil || stat != got {
		t.Fatal(stat, e)
	}
	for _, tc := range []struct{ header, want, cr string }{{"", "0123456789", ""}, {"bytes=2-5", "2345", "bytes 2-5/10"}, {"bytes=7-", "789", "bytes 7-9/10"}, {"bytes=-3", "789", "bytes 7-9/10"}, {"bytes=0-99", "0123456789", "bytes 0-9/10"}} {
		t.Run(tc.header, func(t *testing.T) {
			r, e := d.Open(ctx, "video/org/job/0.mp4", tc.header)
			if e != nil {
				t.Fatal(e)
			}
			b, e := io.ReadAll(r.Body)
			r.Body.Close()
			if e != nil || string(b) != tc.want || r.ContentRange != tc.cr || r.Length != int64(len(b)) {
				t.Fatal(string(b), r, e)
			}
		})
	}
	for _, h := range []string{"bytes=10-", "bytes=5-2", "bytes=-0", "bytes=0-1,3-4", "other=0-1"} {
		if _, e := d.Open(ctx, "video/org/job/0.mp4", h); !errors.Is(e, ErrRange) {
			t.Fatal(h, e)
		}
	}
	if e = d.Delete(ctx, "video/org/job/0.mp4"); e != nil {
		t.Fatal(e)
	}
	if _, e = d.Stat(ctx, "video/org/job/0.mp4"); !errors.Is(e, ErrNotFound) {
		t.Fatal(e)
	}
	if e = d.Delete(ctx, "video/org/job/0.mp4"); e != nil {
		t.Fatal(e)
	}
}

type brokenReader struct{}

func (brokenReader) Read([]byte) (int, error) { return 0, io.ErrUnexpectedEOF }
func TestDisk_FailedPutAtomic(t *testing.T) {
	ctx := context.Background()
	d := disk(t, 4)
	for _, tc := range []struct {
		name string
		r    io.Reader
		want error
	}{{"large", strings.NewReader("12345"), ErrTooLarge}, {"broken", brokenReader{}, io.ErrUnexpectedEOF}} {
		t.Run(tc.name, func(t *testing.T) {
			if _, e := d.Put(ctx, "video/o/j/0", tc.r, Meta{}); !errors.Is(e, tc.want) {
				t.Fatal(e)
			}
			if _, e := d.Stat(ctx, "video/o/j/0"); !errors.Is(e, ErrNotFound) {
				t.Fatal(e)
			}
		})
	}
	if _, e := d.Put(ctx, "video/o/j/0", strings.NewReader("1234"), Meta{}); e != nil {
		t.Fatal(e)
	}
	d.Put(ctx, "video/o/j/0", strings.NewReader("12345"), Meta{})
	r, e := d.Open(ctx, "video/o/j/0", "")
	if e != nil {
		t.Fatal(e)
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(r.Body)
	if string(b) != "1234" {
		t.Fatal("overwrite on failed put")
	}
}
func TestDisk_PathAndSymlinkDenial(t *testing.T) {
	root := t.TempDir()
	d, e := NewDisk(root, 100)
	if e != nil {
		t.Fatal(e)
	}
	defer d.Close()
	for _, key := range []string{"../escape", "/abs", "a/../b", "a//b", "a/%2e%2e/b", "a\\b", "."} {
		if _, e := d.Put(context.Background(), key, strings.NewReader("x"), Meta{}); !errors.Is(e, ErrKey) {
			t.Fatal(key, e)
		}
	}
	outside := t.TempDir()
	if e := os.Symlink(outside, filepath.Join(root, "link")); e != nil {
		t.Fatal(e)
	}
	if _, e = d.Put(context.Background(), "link/escape", strings.NewReader("x"), Meta{}); e == nil {
		t.Fatal("symlink escape")
	}
	entries, _ := os.ReadDir(outside)
	if len(entries) != 0 {
		t.Fatal("wrote outside root")
	}
}
func TestDisk_ConcurrentAtomicReplace(t *testing.T) {
	d := disk(t, 4096)
	ctx := context.Background()
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			data := bytes.Repeat([]byte{byte(i)}, 2048)
			if _, err := d.Put(ctx, "video/o/j/0", bytes.NewReader(data), Meta{}); err != nil {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()
	r, err := d.Open(ctx, "video/o/j/0", "")
	if err != nil {
		t.Fatal(err)
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(r.Body)
	if len(b) != 2048 || !bytes.Equal(b, bytes.Repeat(b[:1], 2048)) {
		t.Fatal("partial write")
	}
}
func TestMedia_DiskSink(t *testing.T) {
	ctx := context.Background()
	d := disk(t, 1024)
	f := media.NewFetcher(func(ctx context.Context, k string, r io.Reader) error {
		_, err := d.Put(ctx, k, r, Meta{ContentType: "video/mp4"})
		return err
	})
	for _, valid := range []bool{true, false} {
		b := videotest.MP4()
		key := "video/o/good/0"
		if !valid {
			b = b[:20]
			key = "video/o/bad/0"
		}
		_, err := f.Verify(ctx, bytes.NewReader(b), "video/mp4", key, media.Limits{MaxBytes: 1024})
		if valid && err != nil || !valid && err == nil {
			t.Fatal(err)
		}
		_, err = d.Stat(ctx, key)
		if !valid && !errors.Is(err, ErrNotFound) {
			t.Fatal("invalid committed", err)
		}
	}
}
func TestDisk_ContextAndSizeMismatch(t *testing.T) {
	d := disk(t, 100)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, e := d.Put(ctx, "video/o/j/0", strings.NewReader("x"), Meta{}); !errors.Is(e, context.Canceled) {
		t.Fatal(e)
	}
	if _, e := d.Put(context.Background(), "video/o/j/0", strings.NewReader("x"), Meta{Bytes: 2}); !errors.Is(e, ErrSizeMismatch) {
		t.Fatal(e)
	}
}
