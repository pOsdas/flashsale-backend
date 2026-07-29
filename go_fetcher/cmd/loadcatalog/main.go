package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/joho/godotenv"

	"go_fetcher/internal/config"
	"go_fetcher/internal/cookies"
	"go_fetcher/internal/parsers/ozon"
	"go_fetcher/internal/parsers/wildberries"
)

type catalogProduct struct {
	Marketplace string    `json:"marketplace"`
	ExternalID  string    `json:"external_id"`
	URL         string    `json:"url"`
	Title       string    `json:"title"`
	Category    string    `json:"category"`
	ValidatedAt time.Time `json:"validated_at"`
}

type catalog struct {
	GeneratedAt time.Time        `json:"generated_at"`
	Products    []catalogProduct `json:"products"`
	Errors      []string         `json:"errors,omitempty"`
}

func main() {
	_ = godotenv.Load()

	output := flag.String("output", "/load-results/integration-products.json", "output JSON path")
	perMarketplace := flag.Int("per-marketplace", 25, "validated products per marketplace")
	queriesRaw := flag.String("queries", "смартфон,пылесос,наушники,кофе,инструменты,детские товары,косметика,товары для дома", "comma separated search queries")
	validationTimeout := flag.Duration("validation-timeout", 45*time.Second, "timeout for one product validation")
	flag.Parse()

	if *perMarketplace < 1 {
		fatal("per-marketplace must be positive")
	}

	cfg, err := config.Load()
	if err != nil {
		fatal("load config: %v", err)
	}

	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))
	wb := wildberries.NewParser(wildberries.ParserConfig{
		CookieProvider:         cookies.NewFileCookieProvider("secrets/wb_cookie.txt", 30*time.Second),
		Timeout:                cfg.Timeout,
		RequestDelay:           cfg.WBRequestDelay,
		MaxRetries:             cfg.WBMaxRetries,
		RetryBaseDelay:         cfg.WBRetryBaseDelay,
		BrowserFetcherURL:      cfg.WBBrowserFetcherURL,
		BrowserFetcherEnabled:  cfg.WBBrowserFetcherEnabled,
		BrowserFetcherRequired: cfg.WBBrowserFetcherRequired,
		BrowserFetcherTimeout:  time.Duration(cfg.WBBrowserFetcherTimeoutSeconds) * time.Second,
	}, logger)
	oz := ozon.NewParser(ozon.ParserConfig{
		CookieProvider:         cookies.NewFileCookieProvider("secrets/ozon_cookie.txt", 30*time.Second),
		Timeout:                cfg.Timeout,
		HTTPParserTimeout:      time.Duration(cfg.OzonHTTPParserTimeoutSeconds) * time.Second,
		RequestDelay:           cfg.OzonRequestDelay,
		MaxRetries:             cfg.OzonMaxRetries,
		RetryBaseDelay:         cfg.OzonRetryBaseDelay,
		BrowserFetcherURL:      cfg.OzonBrowserFetcherURL,
		BrowserFetcherEnabled:  cfg.OzonBrowserFetcherEnabled,
		BrowserFetcherRequired: cfg.OzonBrowserFetcherRequired,
		BrowserFetcherTimeout:  time.Duration(cfg.OzonBrowserFetcherTimeoutSeconds) * time.Second,
	}, logger)

	queries := splitQueries(*queriesRaw)
	result := catalog{GeneratedAt: time.Now().UTC()}
	result.Products = append(result.Products, collectWB(wb, queries, *perMarketplace, *validationTimeout, &result.Errors)...)
	result.Products = append(result.Products, collectOzon(oz, queries, *perMarketplace, *validationTimeout, &result.Errors)...)

	if len(result.Products) < *perMarketplace*2 {
		result.Errors = append(result.Errors, fmt.Sprintf(
			"collected %d of requested %d products", len(result.Products), *perMarketplace*2,
		))
	}

	if err := os.MkdirAll(filepath.Dir(*output), 0o755); err != nil {
		fatal("create output directory: %v", err)
	}
	payload, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		fatal("encode catalog: %v", err)
	}
	if err := os.WriteFile(*output, payload, 0o644); err != nil {
		fatal("write catalog: %v", err)
	}

	fmt.Printf("integration catalog written: %s, products=%d, errors=%d\n", *output, len(result.Products), len(result.Errors))
	if len(result.Products) == 0 {
		os.Exit(2)
	}
}

func collectWB(parser *wildberries.Parser, queries []string, target int, timeout time.Duration, errorsOut *[]string) []catalogProduct {
	result := make([]catalogProduct, 0, target)
	seen := map[string]struct{}{}
	for _, query := range queries {
		if len(result) >= target {
			break
		}
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		candidates, err := parser.SearchProducts(ctx, query, target)
		cancel()
		if err != nil {
			*errorsOut = append(*errorsOut, fmt.Sprintf("WB search %q: %v", query, err))
			continue
		}
		for _, candidate := range candidates {
			if len(result) >= target {
				break
			}
			if candidate.SKU == "" {
				continue
			}
			if _, ok := seen[candidate.SKU]; ok {
				continue
			}
			ctx, cancel := context.WithTimeout(context.Background(), timeout)
			validated, err := parser.ParseProduct(ctx, candidate.SKU)
			cancel()
			if err != nil || len(validated) == 0 {
				*errorsOut = append(*errorsOut, fmt.Sprintf("WB validate %s: %v", candidate.SKU, err))
				continue
			}
			product := validated[0]
			result = append(result, catalogProduct{
				Marketplace: "wb",
				ExternalID:  product.SKU,
				URL:         "https://www.wildberries.ru/catalog/" + product.SKU + "/detail.aspx",
				Title:       product.Title,
				Category:    query,
				ValidatedAt: time.Now().UTC(),
			})
			seen[product.SKU] = struct{}{}
		}
	}
	return result
}

func collectOzon(parser *ozon.Parser, queries []string, target int, timeout time.Duration, errorsOut *[]string) []catalogProduct {
	result := make([]catalogProduct, 0, target)
	seen := map[string]struct{}{}
	for _, query := range queries {
		if len(result) >= target {
			break
		}
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
		candidates, err := parser.SearchProducts(ctx, query, target)
		cancel()
		if err != nil {
			*errorsOut = append(*errorsOut, fmt.Sprintf("Ozon search %q: %v", query, err))
			continue
		}
		for _, candidate := range candidates {
			if len(result) >= target {
				break
			}
			if candidate.SKU == "" || candidate.URL == "" {
				continue
			}
			if _, ok := seen[candidate.SKU]; ok {
				continue
			}
			ctx, cancel := context.WithTimeout(context.Background(), timeout)
			validated, err := parser.ParseProduct(ctx, candidate.URL)
			cancel()
			if err != nil || len(validated) == 0 {
				*errorsOut = append(*errorsOut, fmt.Sprintf("Ozon validate %s: %v", candidate.URL, err))
				continue
			}
			product := validated[0]
			url := product.URL
			if url == "" {
				url = candidate.URL
			}
			result = append(result, catalogProduct{
				Marketplace: "ozon",
				ExternalID:  product.SKU,
				URL:         url,
				Title:       product.Title,
				Category:    query,
				ValidatedAt: time.Now().UTC(),
			})
			seen[product.SKU] = struct{}{}
		}
	}
	return result
}

func splitQueries(value string) []string {
	parts := strings.Split(value, ",")
	result := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			result = append(result, part)
		}
	}
	if len(result) == 0 {
		return []string{"смартфон"}
	}
	return result
}

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}
