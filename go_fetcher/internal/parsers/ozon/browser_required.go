package ozon

import (
	"context"
	"fmt"
	"go_fetcher/internal/models"
	"strings"
)

func (p *Parser) requireBrowserClient() (*BrowserClient, error) {
	if p == nil || p.browserClient == nil || !p.browserClient.Enabled() {
		return nil, fmt.Errorf("Ozon browser fetcher is required but not configured")
	}
	return p.browserClient, nil
}

func (p *Parser) parseProductBrowserRequired(
	ctx context.Context,
	productInput string,
) ([]models.Product, error) {
	productInput = strings.TrimSpace(productInput)
	if productInput == "" {
		return nil, fmt.Errorf("productInput is empty")
	}

	client, err := p.requireBrowserClient()
	if err != nil {
		return nil, err
	}

	product, err := client.ParseProduct(ctx, productInput)
	if err != nil {
		return nil, fmt.Errorf("parse Ozon product through required VPN browser gateway: %w", err)
	}
	return []models.Product{product}, nil
}

func (p *Parser) searchProductsBrowserRequired(
	ctx context.Context,
	query string,
	limit int,
) ([]models.Product, error) {
	client, err := p.requireBrowserClient()
	if err != nil {
		return nil, err
	}

	products, err := client.SearchProducts(ctx, query, limit)
	if err != nil {
		return nil, fmt.Errorf("search Ozon products through required VPN browser gateway: %w", err)
	}
	return products, nil
}

func (p *Parser) categoryProductsBrowserRequired(
	ctx context.Context,
	categoryInput string,
	limit int,
) ([]models.Product, error) {
	client, err := p.requireBrowserClient()
	if err != nil {
		return nil, err
	}

	products, err := client.CategoryProducts(ctx, categoryInput, limit)
	if err != nil {
		return nil, fmt.Errorf("parse Ozon category through required VPN browser gateway: %w", err)
	}
	return products, nil
}
