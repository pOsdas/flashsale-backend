package wildberries

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"go_fetcher/internal/cookies"
	"go_fetcher/internal/models"
)

const (
	defaultCurrency = "RUB"

	detailURL  = "https://www.wildberries.ru/__internal/u-card/cards/v4/detail"
	catalogURL = "https://www.wildberries.ru/__internal/search/exactmatch/ru/common/v18/search"

	defaultPageSize                = 100
	defaultWBBrowserFetcherTimeout = 35 * time.Second

	wbRequestProfile        = "go_http_client_browser_headers"
	wbBlockedRecommendation = "pause requests and refresh marketplace session cookies"
)

type Parser struct {
	client         *http.Client
	browserClient  *BrowserClient
	logger         *slog.Logger
	cookie         string
	cookieProvider cookies.Provider
	requestDelay   time.Duration
	maxRetries     int
	retryBaseDelay time.Duration
	blockedUntil   time.Time
	blockReason    string
	blockMu        sync.RWMutex
}

type ParserConfig struct {
	Cookie                string
	CookieProvider        cookies.Provider
	Timeout               time.Duration
	RequestDelay          time.Duration
	MaxRetries            int
	RetryBaseDelay        time.Duration
	BrowserFetcherURL     string
	BrowserFetcherEnabled bool
	BrowserFetcherTimeout time.Duration
}

type parserRequestError struct {
	err           error
	requestURL    string
	cookiePresent bool
	cookieSource  string
}

func (e *parserRequestError) Error() string {
	return e.err.Error()
}

func (e *parserRequestError) Unwrap() error {
	return e.err
}

func (e *parserRequestError) ParserDetails() map[string]interface{} {
	details := map[string]interface{}{
		"cookie_present":  e.cookiePresent,
		"cookie_source":   e.cookieSource,
		"request_host":    requestHostFromURL(e.requestURL),
		"request_profile": wbRequestProfile,
	}

	if isRateLimitedOrBlockedError(e.err) {
		details["error_type"] = "rate_limited_or_blocked"
		details["recommendation"] = wbBlockedRecommendation
	}

	return details
}

func NewParser(cfg ParserConfig, logger *slog.Logger) *Parser {
	if logger == nil {
		logger = slog.Default()
	}

	if cfg.Timeout <= 0 {
		cfg.Timeout = 15 * time.Second
	}

	if cfg.RequestDelay < 0 {
		cfg.RequestDelay = 0
	}

	if cfg.MaxRetries < 0 {
		cfg.MaxRetries = 0
	}

	if cfg.RetryBaseDelay <= 0 {
		cfg.RetryBaseDelay = 1 * time.Second
	}

	if cfg.BrowserFetcherTimeout <= 0 {
		cfg.BrowserFetcherTimeout = defaultWBBrowserFetcherTimeout
	}

	return &Parser{
		client: &http.Client{
			Timeout: cfg.Timeout,
		},
		browserClient:  NewBrowserClient(cfg.BrowserFetcherEnabled, cfg.BrowserFetcherURL, cfg.BrowserFetcherTimeout),
		logger:         logger,
		cookie:         cfg.Cookie,
		cookieProvider: cfg.CookieProvider,
		requestDelay:   cfg.RequestDelay,
		maxRetries:     cfg.MaxRetries,
		retryBaseDelay: cfg.RetryBaseDelay,
	}
}

func (p *Parser) ParseProduct(ctx context.Context, nmID string) ([]models.Product, error) {
	nmID = strings.TrimSpace(nmID)
	if nmID == "" {
		return nil, fmt.Errorf("nmID is empty")
	}

	requestURL := buildDetailURL(nmID)

	var response wbProductsResponse

	if err := p.doJSONRequest(ctx, requestURL, &response); err != nil {
		return nil, fmt.Errorf("parse WB product %s: %w", nmID, p.withRequestDetails(err, requestURL))
	}

	products := normalizeWBProducts(response.productList())
	products = deduplicateProductsBySKU(products)

	p.logger.Info(
		"wildberries product parsed",
		slog.String("nm_id", nmID),
		slog.Int("products_found", len(products)),
	)

	return products, nil
}

func (p *Parser) SearchProducts(ctx context.Context, query string, limit int) ([]models.Product, error) {
	query = strings.TrimSpace(query)

	if query == "" {
		return nil, fmt.Errorf("search query is empty")
	}

	if limit <= 0 {
		return nil, fmt.Errorf("limit must be greater than zero")
	}

	p.logger.Info(
		"wildberries search import started",
		slog.String("query", query),
		slog.Int("limit", limit),
		slog.Bool("cookie_present", p.hasCookie()),
		slog.String("cookie_source", p.cookieSource()),
	)

	products, err := p.fetchCatalogProducts(
		ctx,
		"search",
		query,
		limit,
		func(page int) string {
			return buildSearchURL(query, page)
		},
	)
	if err != nil {
		return nil, fmt.Errorf("search WB products by query %q: %w", query, p.withRequestDetails(err, buildSearchURL(query, 1)))
	}

	p.logger.Info(
		"wildberries search import parsed",
		slog.String("query", query),
		slog.Int("products_found", len(products)),
	)

	return products, nil
}

func (p *Parser) CategoryProducts(ctx context.Context, categoryName string, limit int) ([]models.Product, error) {
	categoryName = strings.TrimSpace(categoryName)

	if categoryName == "" {
		return nil, fmt.Errorf("category name is empty")
	}

	if limit <= 0 {
		return nil, fmt.Errorf("limit must be greater than zero")
	}

	p.logger.Info(
		"wildberries category import started",
		slog.String("category", categoryName),
		slog.Int("limit", limit),
		slog.Bool("cookie_present", p.hasCookie()),
		slog.String("cookie_source", p.cookieSource()),
	)

	products, err := p.fetchCatalogProducts(
		ctx,
		"category",
		categoryName,
		limit,
		func(page int) string {
			return buildCategoryURL(categoryName, page)
		},
	)
	if err != nil {
		return nil, fmt.Errorf("parse WB category %q: %w", categoryName, p.withRequestDetails(err, buildCategoryURL(categoryName, 1)))
	}

	p.logger.Info(
		"wildberries category import parsed",
		slog.String("category", categoryName),
		slog.Int("products_found", len(products)),
	)

	return products, nil
}

func (p *Parser) currentCookie() string {
	if p == nil {
		return ""
	}

	if p.cookieProvider != nil {
		return strings.TrimSpace(p.cookieProvider.GetCookie())
	}

	return strings.TrimSpace(p.cookie)
}

func (p *Parser) forceReloadCookie() {
	if p == nil {
		return
	}

	if p.cookieProvider != nil {
		p.cookieProvider.ForceReload()
	}
}

func (p *Parser) hasCookie() bool {
	return strings.TrimSpace(p.currentCookie()) != ""
}

func (p *Parser) cookieSource() string {
	if p == nil {
		return ""
	}

	if p.cookieProvider != nil {
		return p.cookieProvider.Source()
	}

	if strings.TrimSpace(p.cookie) != "" {
		return "env"
	}

	return "empty"
}

func (p *Parser) withRequestDetails(err error, requestURL string) error {
	if err == nil {
		return nil
	}

	return &parserRequestError{
		err:           err,
		requestURL:    requestURL,
		cookiePresent: p.hasCookie(),
		cookieSource:  p.cookieSource(),
	}
}

func requestHostFromURL(requestURL string) string {
	parsedURL, err := url.Parse(requestURL)
	if err != nil {
		return ""
	}

	return parsedURL.Host
}

func isRateLimitedOrBlockedError(err error) bool {
	if err == nil {
		return false
	}

	errorText := strings.ToLower(err.Error())

	return strings.Contains(errorText, "status code: 429") ||
		strings.Contains(errorText, "status code 429") ||
		strings.Contains(errorText, "status code: 498") ||
		strings.Contains(errorText, "status code 498") ||
		strings.Contains(errorText, "status code: 403") ||
		strings.Contains(errorText, "status code 403") ||
		strings.Contains(errorText, "temporarily blocked") ||
		strings.Contains(errorText, "antibot") ||
		strings.Contains(errorText, "__wbaas/challenges/antibot")
}

func (p *Parser) isTemporarilyBlocked() (bool, time.Time, string) {
	if p == nil {
		return false, time.Time{}, ""
	}

	p.blockMu.RLock()
	defer p.blockMu.RUnlock()

	if p.blockedUntil.IsZero() {
		return false, time.Time{}, ""
	}

	if time.Now().Before(p.blockedUntil) {
		return true, p.blockedUntil, p.blockReason
	}

	return false, time.Time{}, ""
}

func (p *Parser) blockTemporarily(reason string, duration time.Duration) {
	if p == nil {
		return
	}

	if duration <= 0 {
		duration = 5 * time.Minute
	}

	until := time.Now().Add(duration)

	p.blockMu.Lock()
	p.blockedUntil = until
	p.blockReason = reason
	p.blockMu.Unlock()

	p.logger.Warn(
		"wildberries parser temporarily blocked",
		slog.String("reason", reason),
		slog.Time("blocked_until", until),
	)
}
