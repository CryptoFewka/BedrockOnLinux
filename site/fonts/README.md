# Web fonts

Self-hosted so the page loads nothing from a third party. Each file is the
`latin` subset of the family's variable font, taken from the Google Fonts CDN
and served here unchanged.

| File | Family | Upstream | Licence |
| --- | --- | --- | --- |
| `space-grotesk.woff2` | Space Grotesk (v22, wght axis) | https://fonts.google.com/specimen/Space+Grotesk | OFL-1.1, `OFL-spacegrotesk.txt` |
| `ibm-plex-sans.woff2` | IBM Plex Sans (v23, wght axis) | https://fonts.google.com/specimen/IBM+Plex+Sans | OFL-1.1, `OFL-ibmplexsans.txt` |
| `jetbrains-mono.woff2` | JetBrains Mono (v24, wght axis) | https://fonts.google.com/specimen/JetBrains+Mono | OFL-1.1, `OFL-jetbrainsmono.txt` |

One file per family covers every weight the site uses: these are variable
fonts, so `style.css` declares several `@font-face` blocks pointing at the same
file, exactly as the Google Fonts CSS does.

To refresh a subset, request the family from `fonts.googleapis.com/css2` with a
current browser User-Agent, take the `/* latin */` block's woff2 URL and replace
the file in place.
