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
		if a == nil {
			return handler(ctx, req)
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

		ctx = WithResolvedContext(ctx, result, CacheKey(apiKey, secretKey))
		return handler(ctx, req)
	}
}

func firstMD(md metadata.MD, key string) string {
	vals := md.Get(key)
	if len(vals) == 0 {
		return ""
	}
	return vals[0]
}
