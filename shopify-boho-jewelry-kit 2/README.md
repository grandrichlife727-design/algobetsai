# Sol & Sage Jewelry - Shopify Starter Kit

This is a boho-style Shopify starter pack for a women's dropshipping jewelry brand.

## Brand
- Name: **Sol & Sage Jewelry**
- Positioning: Boho-luxe, earthy, giftable, stackable
- Audience: Women 18-40, festival + everyday style

## What's included
- `branding/logo.svg` and `branding/logo-mark.svg`
- `assets/boho-theme.css` custom boho styling
- `sections/` custom homepage sections (hero, categories, social proof)
- `templates/index.json` homepage structure
- `products/sol-and-sage-products.csv` Shopify import CSV with 12 products
- `products/product-strategy.md` product rationale

## Quick Shopify setup
1. In Shopify Admin: Online Store -> Themes -> Customize (using Dawn recommended).
2. Add `assets/boho-theme.css` to your theme assets.
3. Include `{{ 'boho-theme.css' | asset_url | stylesheet_tag }}` in `layout/theme.liquid` before `</head>`.
4. Add section files from `sections/` into your theme.
5. Replace homepage template with `templates/index.json` structure manually in Theme Customizer.
6. Products -> Import -> upload `products/sol-and-sage-products.csv`.
7. Replace placeholder image URLs with your supplier or custom photos.

## Notes
- Prices are set for healthy gross margin and gift-friendly AOV.
- Product copy is optimized for fast launch.
- Update shipping, return policy, and legal pages before going live.
