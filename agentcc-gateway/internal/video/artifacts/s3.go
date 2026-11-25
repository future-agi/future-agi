package artifacts

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"
)

type S3Config struct {
	Bucket, Prefix, Region, AccessKey, SecretKey, SSE string
	Endpoint                                          string
	MaxBytes                                          int64
	Timeout                                           time.Duration
	TempDir                                           string
}
type S3 struct {
	cfg    S3Config
	client *http.Client
	base   *url.URL
}

var _ Store = (*S3)(nil)

func NewS3(c S3Config) (*S3, error) {
	if c.Bucket == "" || c.Region == "" || c.AccessKey == "" || c.SecretKey == "" || c.MaxBytes <= 0 {
		return nil, fmt.Errorf("incomplete artifact S3 configuration")
	}
	if !strings.HasSuffix(c.Prefix, "/") || !validKey(strings.TrimSuffix(c.Prefix, "/")) {
		return nil, ErrKey
	}
	if c.Endpoint == "" {
		c.Endpoint = fmt.Sprintf("https://%s.s3.%s.amazonaws.com", c.Bucket, c.Region)
	}
	u, e := url.Parse(c.Endpoint)
	if e != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "https" && u.Scheme != "http") {
		return nil, fmt.Errorf("invalid artifact S3 endpoint")
	}
	if c.Timeout <= 0 {
		c.Timeout = 10 * time.Minute
	}
	return &S3{cfg: c, base: u, client: &http.Client{Timeout: c.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}
func (s *S3) check(key string) error {
	if !validKey(key) || !strings.HasPrefix(key, s.cfg.Prefix) || len(key) <= len(s.cfg.Prefix) {
		return ErrKey
	}
	return nil
}
func (s *S3) request(ctx context.Context, method, key string, body io.Reader) (*http.Request, error) {
	if e := s.check(key); e != nil {
		return nil, e
	}
	u := *s.base
	u.Path = strings.TrimSuffix(u.Path, "/") + "/" + key
	u.RawPath = ""
	return http.NewRequestWithContext(ctx, method, u.String(), body)
}
func (s *S3) do(req *http.Request) (*http.Response, error) {
	s.sign(req)
	r, e := s.client.Do(req)
	if e != nil {
		return nil, fmt.Errorf("artifact S3 transport failed")
	}
	if r.StatusCode >= 200 && r.StatusCode < 300 {
		return r, nil
	}
	r.Body.Close()
	switch r.StatusCode {
	case 404:
		return nil, ErrNotFound
	case 416:
		return nil, ErrRange
	}
	return nil, fmt.Errorf("artifact S3 HTTP %d", r.StatusCode)
}

// Stage a stream on disk, never in memory, before PUT. This gives S3 an exact
// Content-Length and prevents committing a partial stream on a late validation
// error. The network upload itself streams the file with UNSIGNED-PAYLOAD.
func (s *S3) Put(ctx context.Context, key string, r io.Reader, m Meta) (Meta, error) {
	if e := s.check(key); e != nil {
		return Meta{}, e
	}
	if e := ctx.Err(); e != nil {
		return Meta{}, e
	}
	if m.Bytes > s.cfg.MaxBytes {
		return Meta{}, ErrTooLarge
	}
	f, e := os.CreateTemp(s.cfg.TempDir, "agentcc-video-upload-*")
	if e != nil {
		return Meta{}, e
	}
	defer os.Remove(f.Name())
	defer f.Close()
	br := newBounded(ctx, r, s.cfg.MaxBytes)
	if _, e = io.Copy(f, br); e != nil {
		return Meta{}, e
	}
	m, e = br.metadata(m)
	if e != nil {
		return Meta{}, e
	}
	if _, e = f.Seek(0, io.SeekStart); e != nil {
		return Meta{}, e
	}
	req, e := s.request(ctx, "PUT", key, f)
	if e != nil {
		return Meta{}, e
	}
	req.ContentLength = m.Bytes
	req.Header.Set("Content-Type", m.ContentType)
	req.Header.Set("X-Amz-Acl", "private")
	req.Header.Set("X-Amz-Meta-Sha256", m.Digest)
	if !m.ExpiresAt.IsZero() {
		req.Header.Set("X-Amz-Meta-Agentcc-Expires-At", strconv.FormatInt(m.ExpiresAt.Unix(), 10))
	}
	if s.cfg.SSE != "" {
		req.Header.Set("X-Amz-Server-Side-Encryption", s.cfg.SSE)
	}
	req.Header.Set("X-Amz-Content-Sha256", "UNSIGNED-PAYLOAD")
	resp, e := s.do(req)
	if e != nil {
		return Meta{}, e
	}
	resp.Body.Close()
	return m, nil
}
func responseMeta(r *http.Response) (Meta, error) {
	m := Meta{ContentType: r.Header.Get("Content-Type"), Bytes: r.ContentLength, Digest: r.Header.Get("X-Amz-Meta-Sha256")}
	if m.Bytes < 0 {
		return Meta{}, ErrSizeMismatch
	}
	if v := r.Header.Get("X-Amz-Meta-Agentcc-Expires-At"); v != "" {
		n, e := strconv.ParseInt(v, 10, 64)
		if e != nil {
			return Meta{}, e
		}
		m.ExpiresAt = time.Unix(n, 0).UTC()
	}
	return m, nil
}
func (s *S3) Stat(ctx context.Context, key string) (Meta, error) {
	req, e := s.request(ctx, "HEAD", key, nil)
	if e != nil {
		return Meta{}, e
	}
	r, e := s.do(req)
	if e != nil {
		return Meta{}, e
	}
	defer r.Body.Close()
	return responseMeta(r)
}
func (s *S3) Open(ctx context.Context, key, rng string) (Object, error) {
	req, e := s.request(ctx, "GET", key, nil)
	if e != nil {
		return Object{}, e
	}
	if rng != "" {
		if _, _, _, e = parseRange(rng, 1<<62); e != nil {
			return Object{}, e
		}
		req.Header.Set("Range", rng)
	}
	r, e := s.do(req)
	if e != nil {
		return Object{}, e
	}
	fail := func(e error) (Object, error) { r.Body.Close(); return Object{}, e }
	m, e := responseMeta(r)
	if e != nil {
		return fail(e)
	}
	cr := r.Header.Get("Content-Range")
	if rng != "" {
		if r.StatusCode != 206 {
			return fail(ErrRange)
		}
		var a, b, total int64
		if _, e = fmt.Sscanf(cr, "bytes %d-%d/%d", &a, &b, &total); e != nil {
			return fail(ErrRange)
		}
		want, n, expected, e := parseRange(rng, total)
		if e != nil || a != want || b-a+1 != n || r.ContentLength != n || cr != expected {
			return fail(ErrRange)
		}
		m.Bytes = total
	} else if r.StatusCode != 200 {
		return fail(ErrRange)
	}
	return Object{Body: r.Body, Meta: m, Length: r.ContentLength, ContentRange: cr}, nil
}
func (s *S3) Delete(ctx context.Context, key string) error {
	req, e := s.request(ctx, "DELETE", key, nil)
	if e != nil {
		return e
	}
	r, e := s.do(req)
	if e == ErrNotFound {
		return nil
	}
	if e != nil {
		return e
	}
	return r.Body.Close()
}

// Adapted from cache/backend_s3.go signRequest: isolated here to avoid changing
// existing cache signatures. Adds streaming payloads and signs ACL/SSE/Range.
func (s *S3) sign(req *http.Request) {
	now := time.Now().UTC()
	date := now.Format("20060102")
	req.Header.Set("X-Amz-Date", now.Format("20060102T150405Z"))
	payload := req.Header.Get("X-Amz-Content-Sha256")
	if payload == "" {
		h := sha256.Sum256(nil)
		payload = hex.EncodeToString(h[:])
		req.Header.Set("X-Amz-Content-Sha256", payload)
	}
	headers := []string{"host"}
	for k := range req.Header {
		lower := strings.ToLower(k)
		if strings.HasPrefix(lower, "x-amz-") || lower == "content-type" || lower == "range" {
			headers = append(headers, lower)
		}
	}
	sort.Strings(headers)
	var canonical strings.Builder
	for _, h := range headers {
		v := req.Header.Get(h)
		if h == "host" {
			v = req.URL.Host
		}
		canonical.WriteString(h + ":" + strings.Join(strings.Fields(v), " ") + "\n")
	}
	scope := date + "/" + s.cfg.Region + "/s3/aws4_request"
	request := strings.Join([]string{req.Method, req.URL.EscapedPath(), req.URL.Query().Encode(), canonical.String(), strings.Join(headers, ";"), payload}, "\n")
	hash := sha256.Sum256([]byte(request))
	toSign := "AWS4-HMAC-SHA256\n" + req.Header.Get("X-Amz-Date") + "\n" + scope + "\n" + hex.EncodeToString(hash[:])
	key := mac([]byte("AWS4"+s.cfg.SecretKey), date)
	key = mac(key, s.cfg.Region)
	key = mac(key, "s3")
	key = mac(key, "aws4_request")
	req.Header.Set("Authorization", fmt.Sprintf("AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s", s.cfg.AccessKey, scope, strings.Join(headers, ";"), hex.EncodeToString(mac(key, toSign))))
}
func mac(key []byte, s string) []byte {
	h := hmac.New(sha256.New, key)
	h.Write([]byte(s))
	return h.Sum(nil)
}
