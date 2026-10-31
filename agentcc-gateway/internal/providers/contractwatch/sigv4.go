package contractwatch

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"
)

// awsCredentials are the AWS keys the Bedrock lister signs with: the same
// AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
// cmd/contract-recorder uses.
type awsCredentials struct {
	AccessKeyID     string
	SecretAccessKey string
	SessionToken    string
}

// signV4 signs a bodiless request with AWS Signature Version 4, signing the
// host and x-amz-date headers (and x-amz-security-token with a session
// token).
//
// The gateway's signer (internal/providers/bedrock/auth.go) is unexported and
// always signs a content-type header, which a GET does not send; the
// control-plane calls here are plain GETs with a query string, so this is the
// small subset of the spec they need. sigv4_test.go checks it against AWS's
// published test vectors.
func signV4(req *http.Request, creds awsCredentials, region, service string, now time.Time) {
	now = now.UTC()
	amzDate := now.Format("20060102T150405Z")
	day := now.Format("20060102")

	req.Header.Set("X-Amz-Date", amzDate)
	if creds.SessionToken != "" {
		req.Header.Set("X-Amz-Security-Token", creds.SessionToken)
	}
	host := req.Host
	if host == "" {
		host = req.URL.Host
	}
	headers := map[string]string{"host": host, "x-amz-date": amzDate}
	if creds.SessionToken != "" {
		headers["x-amz-security-token"] = creds.SessionToken
	}
	names := make([]string, 0, len(headers))
	for k := range headers {
		names = append(names, k)
	}
	sort.Strings(names)
	var canonHeaders strings.Builder
	for _, k := range names {
		canonHeaders.WriteString(k + ":" + strings.TrimSpace(headers[k]) + "\n")
	}
	signed := strings.Join(names, ";")

	canonical := strings.Join([]string{
		req.Method,
		canonicalPath(req.URL.EscapedPath()),
		canonicalQuery(req),
		canonHeaders.String(),
		signed,
		sha256Hex(nil), // no body
	}, "\n")
	scope := day + "/" + region + "/" + service + "/aws4_request"
	toSign := "AWS4-HMAC-SHA256\n" + amzDate + "\n" + scope + "\n" + sha256Hex([]byte(canonical))

	key := []byte("AWS4" + creds.SecretAccessKey)
	for _, part := range []string{day, region, service, "aws4_request"} {
		key = hmacSHA256(key, part)
	}
	sig := hex.EncodeToString(hmacSHA256(key, toSign))
	req.Header.Set("Authorization", fmt.Sprintf("AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s",
		creds.AccessKeyID, scope, signed, sig))
}

// canonicalPath URI-encodes each segment of an already escaped path.
func canonicalPath(escaped string) string {
	if escaped == "" {
		return "/"
	}
	segs := strings.Split(escaped, "/")
	for i, s := range segs {
		segs[i] = awsURIEncode(unescapeOrSelf(s))
	}
	return strings.Join(segs, "/")
}

// canonicalQuery is the query string with keys and values URI-encoded and
// sorted by key, then value.
func canonicalQuery(req *http.Request) string {
	q := req.URL.Query()
	var pairs []string
	for k, vs := range q {
		for _, v := range vs {
			pairs = append(pairs, awsURIEncode(k)+"="+awsURIEncode(v))
		}
	}
	sort.Strings(pairs)
	return strings.Join(pairs, "&")
}

// awsURIEncode is SigV4's UriEncode: every byte but A-Za-z0-9-._~ as %XX.
func awsURIEncode(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		c := s[i]
		if 'a' <= c && c <= 'z' || 'A' <= c && c <= 'Z' || '0' <= c && c <= '9' || strings.IndexByte("-._~", c) >= 0 {
			b.WriteByte(c)
		} else {
			fmt.Fprintf(&b, "%%%02X", c)
		}
	}
	return b.String()
}

func unescapeOrSelf(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '%' && i+2 < len(s) && isHex(s[i+1]) && isHex(s[i+2]) {
			b.WriteByte(unhex(s[i+1])<<4 | unhex(s[i+2]))
			i += 2
			continue
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

func isHex(c byte) bool {
	return '0' <= c && c <= '9' || 'a' <= c && c <= 'f' || 'A' <= c && c <= 'F'
}

func unhex(c byte) byte {
	switch {
	case '0' <= c && c <= '9':
		return c - '0'
	case 'a' <= c && c <= 'f':
		return c - 'a' + 10
	default:
		return c - 'A' + 10
	}
}

func sha256Hex(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func hmacSHA256(key []byte, s string) []byte {
	m := hmac.New(sha256.New, key)
	m.Write([]byte(s))
	return m.Sum(nil)
}
