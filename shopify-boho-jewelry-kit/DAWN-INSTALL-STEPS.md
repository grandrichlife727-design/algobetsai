# Dawn Install Steps (Fast Launch)

1. Shopify Admin -> Online Store -> Themes -> Add theme (Dawn).
2. Edit code in Dawn:
- Upload `assets/boho-theme.css`
- Upload all files in `sections/`
- Upload `snippets/boho-fonts.liquid`

3. In `layout/theme.liquid`, before `</head>` add:

```liquid
{% render 'boho-fonts' %}
{{ 'boho-theme.css' | asset_url | stylesheet_tag }}
```

4. Theme Customizer -> Home page:
- Add section `Boho Hero`
- Add section `Boho Categories`
- Add section `Boho Social Proof`

5. Upload media files from `media/` in Content -> Files.
6. Products -> Import -> upload `products/sol-and-sage-products.csv`.
7. Replace placeholder product image URLs with supplier images.
