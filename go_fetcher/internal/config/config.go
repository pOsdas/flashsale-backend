package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

type Config struct {
	DjangoURL     string
	FetcherAPIKey string

	Timeout time.Duration

	WBRequestDelay                 time.Duration
	WBMaxRetries                   int
	WBRetryBaseDelay               time.Duration
	WBBrowserFetcherURL            string
	WBBrowserFetcherEnabled        bool
	WBBrowserFetcherTimeoutSeconds int

	OzonRequestDelay             time.Duration
	OzonMaxRetries               int
	OzonRetryBaseDelay           time.Duration
	OzonHTTPParserTimeoutSeconds int

	OzonBrowserFetcherURL            string
	OzonBrowserFetcherEnabled        bool
	OzonBrowserFetcherTimeoutSeconds int
}

func Load() (*Config, error) {
	cfg := &Config{
		DjangoURL:     os.Getenv("DJANGO_URL"),
		FetcherAPIKey: os.Getenv("FETCHER_API_KEY"),

		Timeout: 30 * time.Second,

		WBRequestDelay:                 getEnvDurationMS("WB_REQUEST_DELAY_MS", 700*time.Millisecond),
		WBMaxRetries:                   getEnvInt("WB_MAX_RETRIES", 3),
		WBRetryBaseDelay:               getEnvDurationMS("WB_RETRY_BASE_DELAY_MS", 1*time.Second),
		WBBrowserFetcherURL:            os.Getenv("WB_BROWSER_FETCHER_URL"),
		WBBrowserFetcherEnabled:        getEnvBool("WB_BROWSER_FETCHER_ENABLED", false),
		WBBrowserFetcherTimeoutSeconds: getEnvInt("WB_BROWSER_FETCHER_TIMEOUT_SECONDS", 35),

		OzonRequestDelay:             getEnvDurationMS("OZON_REQUEST_DELAY_MS", 700*time.Millisecond),
		OzonMaxRetries:               getEnvInt("OZON_MAX_RETRIES", 3),
		OzonRetryBaseDelay:           getEnvDurationMS("OZON_RETRY_BASE_DELAY_MS", 1*time.Second),
		OzonHTTPParserTimeoutSeconds: getEnvInt("OZON_HTTP_PARSER_TIMEOUT_SECONDS", 12),

		OzonBrowserFetcherURL:            os.Getenv("OZON_BROWSER_FETCHER_URL"),
		OzonBrowserFetcherEnabled:        getEnvBool("OZON_BROWSER_FETCHER_ENABLED", false),
		OzonBrowserFetcherTimeoutSeconds: getEnvInt("OZON_BROWSER_FETCHER_TIMEOUT_SECONDS", 35),
	}

	if cfg.DjangoURL == "" {
		return nil, fmt.Errorf("DJANGO_URL is required")
	}

	if cfg.FetcherAPIKey == "" {
		return nil, fmt.Errorf("FETCHER_API_KEY is required")
	}

	if cfg.WBMaxRetries < 0 {
		return nil, fmt.Errorf("WB_MAX_RETRIES must be greater than or equal to zero")
	}

	if cfg.WBBrowserFetcherTimeoutSeconds <= 0 {
		return nil, fmt.Errorf("WB_BROWSER_FETCHER_TIMEOUT_SECONDS must be greater than zero")
	}

	if cfg.OzonBrowserFetcherTimeoutSeconds <= 0 {
		return nil, fmt.Errorf("OZON_BROWSER_FETCHER_TIMEOUT_SECONDS must be greater than zero")
	}

	if cfg.OzonHTTPParserTimeoutSeconds <= 0 {
		return nil, fmt.Errorf("OZON_HTTP_PARSER_TIMEOUT_SECONDS must be greater than zero")
	}

	return cfg, nil
}

func getEnvInt(key string, defaultValue int) int {
	rawValue := os.Getenv(key)
	if rawValue == "" {
		return defaultValue
	}

	value, err := strconv.Atoi(rawValue)
	if err != nil {
		return defaultValue
	}

	return value
}

func getEnvDurationMS(key string, defaultValue time.Duration) time.Duration {
	rawValue := os.Getenv(key)
	if rawValue == "" {
		return defaultValue
	}

	value, err := strconv.Atoi(rawValue)
	if err != nil {
		return defaultValue
	}

	return time.Duration(value) * time.Millisecond
}

func getEnvBool(key string, defaultValue bool) bool {
	rawValue := os.Getenv(key)
	if rawValue == "" {
		return defaultValue
	}

	value, err := strconv.ParseBool(rawValue)
	if err != nil {
		return defaultValue
	}

	return value
}
