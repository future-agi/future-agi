package auth

import (
	"context"
	"errors"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

type contextKey struct{}

// FromContext retrieves the ResolveResult stored by the interceptor/middleware.
func FromContext(ctx context.Context) *ResolveResult {
	v, _ := ctx.Value(contextKey{}).(*ResolveResult)
	return v
}

type cacheKeyCtxKey struct{}

type credentialsContextKey struct{}
type requestCredentials struct{ apiKey, secretKey string }

// withRequestCredentials retains only the credentials this request actually
// authenticated, not credentials supplied by an in-process resolved context.
func withRequestCredentials(ctx context.Context, apiKey, secretKey string) context.Context {
	return context.WithValue(ctx, credentialsContextKey{}, requestCredentials{apiKey, secretKey})
}

// Reauthenticate restarts an in-flight request against the current key result.
// It does not revive the old entry or infer validity from matching tenant IDs.
// Contexts constructed only with WithResolvedContext cannot reauthenticate.
func (a *Authenticator) Reauthenticate(ctx context.Context) (context.Context, error) {
	creds, ok := ctx.Value(credentialsContextKey{}).(requestCredentials)
	if a == nil || !ok || CacheKey(creds.apiKey, creds.secretKey) != CacheKeyFromContext(ctx) {
		return nil, &ProjectAuthChangedError{}
	}
	result, err := a.Authenticate(ctx, creds.apiKey, creds.secretKey)
	if err != nil {
		return nil, err
	}
	if result == nil || result.OrgID == "" || result.WorkspaceID == "" {
		return nil, ErrUnauthenticated
	}
	return WithResolvedContext(ctx, result, CacheKey(creds.apiKey, creds.secretKey)), nil
}

// WithResolvedContext carries an already authenticated result across an
// in-process boundary. Callers must verify the key and project/workspace
// membership first; this helper does not authenticate untrusted request data.
func WithResolvedContext(ctx context.Context, result *ResolveResult, cacheKey string) context.Context {
	ctx = context.WithValue(ctx, contextKey{}, result)
	return context.WithValue(ctx, cacheKeyCtxKey{}, cacheKey)
}

// CacheKeyFromContext retrieves the hashed cache key from context.
func CacheKeyFromContext(ctx context.Context) string {
	v, _ := ctx.Value(cacheKeyCtxKey{}).(string)
	return v
}

// GRPCInterceptor returns a gRPC unary server interceptor that validates
// X-Api-Key and X-Secret-Key from request metadata.
func (a *Authenticator) GRPCInterceptor() grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		ctx, err := a.AuthenticateGRPC(ctx)
		if err != nil {
			return nil, err
		}
		return handler(ctx, req)
	}
}

// AuthenticateGRPC validates the X-Api-Key and X-Secret-Key metadata of a
// call and returns ctx carrying the result (FromContext). The error is a
// gRPC status: UNAUTHENTICATED for missing or invalid credentials, UNAVAILABLE
// when they cannot be checked. With auth disabled (a nil Authenticator) it
// returns ctx unchanged.
func (a *Authenticator) AuthenticateGRPC(ctx context.Context) (context.Context, error) {
	if a == nil {
		return ctx, nil
	}

	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return nil, status.Error(codes.Unauthenticated, "missing metadata")
	}

	apiKey := firstMD(md, "x-api-key")
	secretKey := firstMD(md, "x-secret-key")
	if apiKey == "" || secretKey == "" {
		return nil, status.Error(codes.Unauthenticated, "missing X-Api-Key or X-Secret-Key")
	}

	result, err := a.Authenticate(ctx, apiKey, secretKey)
	if err != nil {
		if errors.Is(err, ErrUnauthenticated) {
			a.log.Warn("grpc auth failed", "err", err)
			return nil, status.Error(codes.Unauthenticated, "authentication failed")
		}
		a.log.Error("grpc auth infrastructure error", "err", err)
		return nil, status.Error(codes.Unavailable, "service temporarily unavailable")
	}

	ctx = withRequestCredentials(ctx, apiKey, secretKey)
	return WithResolvedContext(ctx, result, CacheKey(apiKey, secretKey)), nil
}

// HasGRPCCredentials reports whether call metadata carries both credentials
// AuthenticateGRPC needs. It does no lookup, so it can run before a call is
// accepted (a gRPC tap handle).
func HasGRPCCredentials(md metadata.MD) bool {
	return firstMD(md, "x-api-key") != "" && firstMD(md, "x-secret-key") != ""
}

// KnownGRPCCredentials reports whether call metadata carries a key pair that
// is in the cache as valid (fresh or within the warm window). It reads only
// the cache, so it can run before a call is accepted; a pair it does not
// know may still be valid. With auth disabled every call is known.
func (a *Authenticator) KnownGRPCCredentials(md metadata.MD) bool {
	if a == nil {
		return true
	}
	apiKey, secretKey := firstMD(md, "x-api-key"), firstMD(md, "x-secret-key")
	if apiKey == "" || secretKey == "" {
		return false
	}
	_, status := a.cache.peek(CacheKey(apiKey, secretKey))
	return status != "miss"
}

func firstMD(md metadata.MD, key string) string {
	vals := md.Get(key)
	if len(vals) == 0 {
		return ""
	}
	return vals[0]
}
