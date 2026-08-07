package middleware

import (
	"bytes"
	"context"
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

type licenseAuthContextKey struct{}
type licenseClaimsContextKey struct{}

type licenseClaims struct {
	Type                 string   `json:"typ"`
	Issuer               string   `json:"iss"`
	Audience             string   `json:"aud"`
	IssuedAt             int64    `json:"iat"`
	NotBefore            int64    `json:"nbf"`
	ExpiresAt            int64    `json:"exp"`
	JTI                  string   `json:"jti"`
	LicenseID            string   `json:"license_id"`
	CustomerID           string   `json:"customer_id"`
	InstanceID           string   `json:"instance_id"`
	AuthorizationVersion int      `json:"authorization_version"`
	Scope                string   `json:"scope"`
	Services             []string `json:"services"`
	Models               []string `json:"models"`
}

type jwtHeader struct {
	Algorithm string `json:"alg"`
	KeyID     string `json:"kid"`
}

type modelRequest struct {
	Model string `json:"model"`
}

func LicenseAuth(cfg config.LicenseAuthConfig, store *redisstate.LicenseStore) func(http.Handler) http.Handler {
	cfg = withLicenseAuthDefaults(cfg)
	keys := buildLicensePublicKeyMap(cfg)

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if !cfg.Enabled || !strings.HasPrefix(r.URL.Path, "/v1/") {
				next.ServeHTTP(w, r)
				return
			}
			if len(keys) == 0 {
				models.WriteError(w, models.ErrServiceUnavailable("license verification unavailable"))
				return
			}

			rawToken := extractBearerToken(r)
			if rawToken == "" {
				if isManagedEndpoint(r) {
					models.WriteError(w, models.ErrUnauthorized("Missing managed service token"))
					return
				}
				next.ServeHTTP(w, r)
				return
			}

			// Only attempt license verification if the token looks like a JWT.
			// Internal API keys (e.g. fi-internal-...) should pass through to
			// the pipeline auth plugin.
			if !looksLikeJWT(rawToken) {
				next.ServeHTTP(w, r)
				return
			}

			claims, err := verifyLicenseToken(rawToken, keys, cfg)
			if err != nil {
				models.WriteError(w, models.ErrUnauthorized("Invalid managed service token"))
				return
			}

			if err := authorizeManagedRequest(r, claims); err != nil {
				models.WriteError(w, models.ErrForbidden(err.Error()))
				return
			}

			if err := authorizeRuntimeState(claims, cfg, store); err != nil {
				models.WriteError(w, err)
				return
			}

			ctx := context.WithValue(r.Context(), licenseAuthContextKey{}, true)
			ctx = context.WithValue(ctx, licenseClaimsContextKey{}, claims)

			meter := &usageMeterResponseWriter{ResponseWriter: w}
			next.ServeHTTP(meter, r.WithContext(ctx))

			if store != nil && cfg.MonthlyUsageLimit > 0 && meter.status >= 200 && meter.status < 300 {
				if err := store.IncrMonthlyUsage(claims.LicenseID); err != nil {
					slog.Warn("license_usage_charge_failed",
						"license_id", claims.LicenseID,
						"err", err.Error(),
					)
				}
			}
		})
	}
}

func isManagedEndpoint(r *http.Request) bool {
	return r.Method == http.MethodPost && r.URL.Path == "/v1/chat/completions"
}

func authorizeRuntimeState(claims *licenseClaims, cfg config.LicenseAuthConfig, store *redisstate.LicenseStore) *models.APIError {
	if !cfg.RuntimeStateRequired && cfg.RateLimitRPM <= 0 && cfg.MonthlyUsageLimit <= 0 {
		return nil
	}
	if store == nil {
		return models.ErrServiceUnavailable("license runtime state unavailable")
	}

	if cfg.RuntimeStateRequired {
		grantActive, err := store.GrantActive(claims.LicenseID, claims.AuthorizationVersion)
		if err != nil {
			return models.ErrServiceUnavailable("license runtime state unavailable")
		}
		if !grantActive {
			return models.ErrForbidden("license grant is not active")
		}

		sessionActive, err := store.SessionActive(claims.JTI)
		if err != nil {
			return models.ErrServiceUnavailable("license runtime state unavailable")
		}
		if !sessionActive {
			return models.ErrForbidden("license session is not active")
		}

		instanceActive, err := store.InstanceActive(claims.LicenseID, claims.InstanceID)
		if err != nil {
			return models.ErrServiceUnavailable("license runtime state unavailable")
		}
		if !instanceActive {
			return models.ErrForbidden("license instance is not active")
		}
	}

	if allowed, err := store.AllowRate(claims.LicenseID, cfg.RateLimitRPM); err != nil {
		return models.ErrServiceUnavailable("license rate state unavailable")
	} else if !allowed {
		return models.ErrTooManyRequests("license rate limit exceeded")
	}

	if allowed, err := store.PeekMonthlyUsage(claims.LicenseID, cfg.MonthlyUsageLimit); err != nil {
		return models.ErrServiceUnavailable("license usage state unavailable")
	} else if !allowed {
		return models.ErrForbidden("license usage limit exceeded")
	}

	return nil
}

type usageMeterResponseWriter struct {
	http.ResponseWriter
	status  int
	written bool
}

func (w *usageMeterResponseWriter) WriteHeader(status int) {
	if !w.written {
		w.status = status
		w.written = true
	}
	w.ResponseWriter.WriteHeader(status)
}

func (w *usageMeterResponseWriter) Write(b []byte) (int, error) {
	if !w.written {
		w.status = http.StatusOK
		w.written = true
	}
	return w.ResponseWriter.Write(b)
}

func (w *usageMeterResponseWriter) Flush() {
	if flusher, ok := w.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func IsLicenseAuthorized(ctx context.Context) bool {
	value, _ := ctx.Value(licenseAuthContextKey{}).(bool)
	return value
}

type LicenseClaims struct {
	LicenseID  string
	CustomerID string
	InstanceID string
}

func GetLicenseClaims(ctx context.Context) *LicenseClaims {
	claims, _ := ctx.Value(licenseClaimsContextKey{}).(*licenseClaims)
	if claims == nil {
		return nil
	}
	return &LicenseClaims{
		LicenseID:  claims.LicenseID,
		CustomerID: claims.CustomerID,
		InstanceID: claims.InstanceID,
	}
}

func withLicenseAuthDefaults(cfg config.LicenseAuthConfig) config.LicenseAuthConfig {
	if cfg.Issuer == "" {
		cfg.Issuer = "https://licenses.futureagi.com"
	}
	if cfg.Audience == "" {
		cfg.Audience = "futureagi-agentcc-gateway"
	}
	if cfg.TokenType == "" {
		cfg.TokenType = "futureagi-managed-service-token"
	}
	if cfg.ClockSkewSeconds == 0 {
		cfg.ClockSkewSeconds = 300
	}
	return cfg
}

func buildLicensePublicKeyMap(cfg config.LicenseAuthConfig) map[string]*rsa.PublicKey {
	keys := make(map[string]*rsa.PublicKey)
	if cfg.PublicKey != "" {
		if key, err := parseRSAPublicKey(cfg.PublicKey); err == nil {
			keys["default"] = key
		}
	}
	for _, entry := range cfg.PublicKeys {
		if entry.KID == "" || entry.PublicKey == "" {
			continue
		}
		if key, err := parseRSAPublicKey(entry.PublicKey); err == nil {
			keys[entry.KID] = key
		}
	}
	return keys
}

func verifyLicenseToken(raw string, keys map[string]*rsa.PublicKey, cfg config.LicenseAuthConfig) (*licenseClaims, error) {
	parts := strings.Split(raw, ".")
	if len(parts) != 3 {
		return nil, errors.New("invalid token")
	}

	headerBytes, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return nil, err
	}
	var header jwtHeader
	if err := json.Unmarshal(headerBytes, &header); err != nil {
		return nil, err
	}
	if header.Algorithm != "RS256" {
		return nil, errors.New("unsupported license token algorithm")
	}
	keyID := header.KeyID
	if keyID == "" {
		keyID = "default"
	}
	key := keys[keyID]
	if key == nil {
		return nil, errors.New("unknown license token kid")
	}

	signature, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return nil, err
	}
	signed := []byte(parts[0] + "." + parts[1])
	digest := sha256.Sum256(signed)
	if err := rsa.VerifyPKCS1v15(key, crypto.SHA256, digest[:], signature); err != nil {
		return nil, err
	}

	payloadBytes, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil, err
	}
	var claims licenseClaims
	if err := json.Unmarshal(payloadBytes, &claims); err != nil {
		return nil, err
	}

	now := time.Now().Unix()
	skew := int64(cfg.ClockSkewSeconds)
	if skew < 0 {
		return nil, errors.New("invalid license token clock skew")
	}
	if claims.Type != cfg.TokenType {
		return nil, errors.New("invalid license token type")
	}
	if claims.Issuer != cfg.Issuer {
		return nil, errors.New("invalid license token issuer")
	}
	if claims.Audience != cfg.Audience {
		return nil, errors.New("invalid license token audience")
	}
	if claims.IssuedAt <= 0 || claims.IssuedAt > now+skew {
		return nil, errors.New("invalid license token issued-at time")
	}
	if claims.NotBefore <= 0 || claims.NotBefore > now+skew {
		return nil, errors.New("license token not yet valid")
	}
	if claims.ExpiresAt <= now-skew || claims.ExpiresAt <= claims.NotBefore {
		return nil, errors.New("license token expired")
	}
	if claims.LicenseID == "" || claims.InstanceID == "" || claims.JTI == "" {
		return nil, errors.New("license token identity claims are required")
	}
	if claims.AuthorizationVersion <= 0 {
		return nil, errors.New("license token authorization version is required")
	}
	if claims.Scope != "enterprise" {
		return nil, errors.New("invalid license token scope")
	}
	return &claims, nil
}

func authorizeManagedRequest(r *http.Request, claims *licenseClaims) error {
	if !isManagedEndpoint(r) {
		return errors.New("managed service token is not valid for this endpoint")
	}

	model, err := readRequestModel(r)
	if err != nil {
		return err
	}
	if !contains(claims.Models, model) {
		return fmt.Errorf("model %q not included in token scope", model)
	}

	service := serviceForModel(model)
	if service == "" {
		return fmt.Errorf("model %q is not a managed service model", model)
	}
	if !contains(claims.Services, service) {
		return fmt.Errorf("service %q not included in token scope", service)
	}
	return nil
}

func readRequestModel(r *http.Request) (string, error) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		return "", err
	}
	r.Body = io.NopCloser(bytes.NewReader(body))

	var req modelRequest
	if err := json.Unmarshal(body, &req); err != nil {
		return "", err
	}
	if req.Model == "" {
		return "", errors.New("model is required")
	}
	return req.Model, nil
}

func serviceForModel(model string) string {
	switch {
	case strings.HasPrefix(model, "turing_"):
		return "turing"
	case model == "falcon_ai":
		return "falcon"
	case strings.HasPrefix(model, "protect"):
		return "protect"
	default:
		return ""
	}
}

func parseRSAPublicKey(raw string) (*rsa.PublicKey, error) {
	block, _ := pem.Decode([]byte(raw))
	if block == nil {
		return nil, errors.New("invalid PEM public key")
	}
	parsed, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		pkcs1, pkcs1Err := x509.ParsePKCS1PublicKey(block.Bytes)
		if pkcs1Err != nil {
			return nil, err
		}
		return pkcs1, nil
	}
	key, ok := parsed.(*rsa.PublicKey)
	if !ok {
		return nil, errors.New("public key is not RSA")
	}
	return key, nil
}

func extractBearerToken(r *http.Request) string {
	authHeader := r.Header.Get("Authorization")
	if !strings.HasPrefix(authHeader, "Bearer ") {
		return ""
	}
	return strings.TrimPrefix(authHeader, "Bearer ")
}

func contains(values []string, needle string) bool {
	for _, value := range values {
		if value == needle {
			return true
		}
	}
	return false
}

func looksLikeJWT(token string) bool {
	return strings.Count(token, ".") == 2
}
