package browsergateway

import "context"

const (
	RequestModeHeader      = "X-Flashsale-Request-Mode"
	RequestModeInteractive = "interactive"
)

type requestModeContextKey struct{}

var requestModeKey requestModeContextKey

func WithInteractiveRequest(ctx context.Context) context.Context {
	return context.WithValue(ctx, requestModeKey, true)
}

func IsInteractiveRequest(ctx context.Context) bool {
	value, _ := ctx.Value(requestModeKey).(bool)
	return value
}
