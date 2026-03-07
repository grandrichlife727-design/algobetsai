# CurlMuse One-Page Setup

## Product direction
- Product: Heatless Satin Curling Set
- Suggested pricing:
  - 1x = $29
  - 2x = $49
  - 3x = $69

## Stripe wiring
In `index.html`, replace:
- `REPLACE_SINGLE`
- `REPLACE_DOUBLE`
- `REPLACE_TRIPLE`

## Video block
- The page now includes an embedded demo video.
- To replace it with your own UGC, swap the iframe `src` in `index.html` under \"See CurlMuse in action\".

## Local preview
```bash
cd /Users/fortunefavors/Documents/GitHub/algobetsai/onepager-curlmuse
python3 -m http.server 8083
```
Open:
- http://127.0.0.1:8083/site/index.html
