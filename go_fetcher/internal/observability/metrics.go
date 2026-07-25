package observability

import (
	"strings"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	HTTPRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "go_fetcher_http_requests_total",
			Help: "Total number of go_fetcher HTTP requests",
		},
		[]string{"route", "method", "status_class"},
	)

	HTTPRequestDurationSeconds = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "go_fetcher_http_request_duration_seconds",
			Help:    "go_fetcher HTTP request duration in seconds",
			Buckets: []float64{0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 60, 90},
		},
		[]string{"route", "method"},
	)

	HTTPRequestsInProgress = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "go_fetcher_http_requests_in_progress",
			Help: "Current number of go_fetcher HTTP requests being processed",
		},
		[]string{"route", "method"},
	)

	ProductFetchesTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "go_fetcher_product_fetches_total",
			Help: "Total number of product fetch requests handled by go_fetcher",
		},
		[]string{"marketplace", "result", "error_type"},
	)

	ProductFetchDurationSeconds = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "go_fetcher_product_fetch_duration_seconds",
			Help:    "Product fetch duration in seconds",
			Buckets: []float64{0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 60, 90},
		},
		[]string{"marketplace", "result"},
	)

	ProductsReturnedTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "go_fetcher_products_returned_total",
			Help: "Total number of products successfully returned by go_fetcher",
		},
		[]string{"marketplace"},
	)

	LastSuccessfulProductFetchTimestampSeconds = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "go_fetcher_last_successful_product_fetch_timestamp_seconds",
			Help: "Unix timestamp of the last successful product fetch",
		},
		[]string{"marketplace"},
	)

	ParserHealthChecksTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "go_fetcher_parser_health_checks_total",
			Help: "Total number of parser health checks",
		},
		[]string{"marketplace", "status", "error_type"},
	)

	ParserHealthCheckDurationSeconds = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "go_fetcher_parser_health_check_duration_seconds",
			Help:    "Parser health check duration in seconds",
			Buckets: []float64{0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 60},
		},
		[]string{"marketplace", "status"},
	)

	ParserHealthState = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "go_fetcher_parser_health_state",
			Help: "Current parser health state represented as a one-hot gauge",
		},
		[]string{"marketplace", "status"},
	)

	ParserOperationsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "go_fetcher_parser_operations_total",
			Help: "Total number of parser operations",
		},
		[]string{"marketplace", "mode", "parser", "result", "reason"},
	)

	ParserOperationDurationSeconds = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "go_fetcher_parser_operation_duration_seconds",
			Help:    "Parser operation duration in seconds",
			Buckets: []float64{0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 60},
		},
		[]string{"marketplace", "mode", "parser", "result"},
	)

	MarketplaceFailuresTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "go_fetcher_marketplace_failures_total",
			Help: "Total number of classified marketplace and parser failures",
		},
		[]string{"marketplace", "operation", "error_type"},
	)

	OzonBrowserFallbackAttemptsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "go_fetcher_ozon_browser_fallback_attempts_total",
			Help: "Total number of Ozon browser fallback attempts",
		},
		[]string{"mode", "reason"},
	)

	OzonBrowserFallbackResultsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "go_fetcher_ozon_browser_fallback_results_total",
			Help: "Total number of Ozon browser fallback results",
		},
		[]string{"mode", "result", "reason"},
	)

	OzonBrowserFallbackDurationSeconds = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "go_fetcher_ozon_browser_fallback_duration_seconds",
			Help:    "Ozon browser fallback duration in seconds",
			Buckets: []float64{0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 35, 45, 60},
		},
		[]string{"mode", "result"},
	)

	OzonBrowserFallbackInProgress = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "go_fetcher_ozon_browser_fallback_in_progress",
			Help: "Current number of Ozon browser fallback operations in progress",
		},
		[]string{"mode"},
	)

	OzonBrowserFallbackLastSuccessTimestampSeconds = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "go_fetcher_ozon_browser_fallback_last_success_timestamp_seconds",
			Help: "Unix timestamp of the latest successful Ozon browser fallback",
		},
		[]string{"mode"},
	)
)

func NormalizeMarketplace(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "wb", "wildberries":
		return "wb"
	case "ozon":
		return "ozon"
	default:
		return "unknown"
	}
}

func NormalizeMode(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "product":
		return "product"
	case "search":
		return "search"
	case "category":
		return "category"
	default:
		return "unknown"
	}
}

func NormalizeHealthStatus(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "ok", "healthy", "success":
		return "ok"
	case "warning", "degraded":
		return "warning"
	case "error", "unhealthy", "failed":
		return "error"
	default:
		return "unknown"
	}
}

func NormalizeReason(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))

	switch value {
	case "", "none":
		return "none"
	case "invalid_or_empty_products":
		return value
	case "http_parser_local_timeout":
		return value
	case "browser_fallback_local_timeout":
		return value
	case "parent_context_canceled":
		return value
	case "parent_deadline_exceeded":
		return value
	case "parent_context_done":
		return value
	case "context_canceled":
		return value
	case "http_status_403":
		return value
	case "http_status_429":
		return value
	case "antibot":
		return value
	case "blocked_by_antibot":
		return value
	case "rate_limited":
		return value
	case "parse_error":
		return value
	case "network_timeout":
		return value
	case "network_error":
		return value
	case "go_parser_error":
		return value
	case "browser_fallback_error":
		return value
	case "parser_error":
		return value
	case "validation_error":
		return value
	case "product_not_found":
		return value
	case "unknown_error":
		return value
	case "unknown":
		return value
	}

	if strings.HasPrefix(value, "http_status_") {
		return "http_status_other"
	}

	return "other"
}
