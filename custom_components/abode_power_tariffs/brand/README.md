# Brand assets

Abode family. Background `#2B6D8D → #19475D`, 56 px corner radius, roof
`#F2C14E`, white body, badge `#18465C`. Only the badge differs between the
Abode integrations; this one is a bolt.

| File | Size | Use |
|---|---|---|
| `icon.svg` | 256 × 256 | Source. Edit this, re-render the rest |
| `icon.png` | 256 × 256 | Home Assistant brands icon |
| `icon@2x.png` | 512 × 512 | High density |
| `logo.svg` | 704 × 256 | Source for the lockup |
| `logo.png` | 704 × 256 | Home Assistant brands logo |
| `logo@2x.png` | 1408 × 512 | High density |

Re-render after editing either source:

```bash
python3 -c "
import cairosvg
cairosvg.svg2png(url='icon.svg', write_to='icon.png', output_width=256, output_height=256)
cairosvg.svg2png(url='icon.svg', write_to='icon@2x.png', output_width=512, output_height=512)
cairosvg.svg2png(url='logo.svg', write_to='logo.png', output_width=704, output_height=256)
cairosvg.svg2png(url='logo.svg', write_to='logo@2x.png', output_width=1408, output_height=512)
"
```

The wordmark sits on the brand panel rather than on transparency, so it holds
its contrast on both the light and dark Home Assistant themes. It is set in
DejaVu Sans Bold, which is what was available; if the family has a settled
typeface, change it in `logo.svg` and re-render.

**HACS will still show no icon in its store listing.** HACS fetches brand images
from its own CDN, populated from the Home Assistant brands repository, and does
not fall back to a local `brand/` folder for a custom integration. HACS issue
#5171, open, not fixable from here. The icon does appear once the integration is
installed.
