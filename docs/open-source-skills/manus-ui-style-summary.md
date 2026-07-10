# Manus UI Style — Open-source Skill Reference

Source: https://github.com/cicia-night/manus-ui-style

## Brand tokens used
- Black: `#34322D`
- Light gray page bg: `#F8F8F7`
- Card/section bg: `#EBEBEB`
- White: `#FFFFFF`
- Secondary text: `#5E5E5B`
- Tertiary text: `#858481`

## Typography
- UI / body: DM Sans
- Headlines: Libre Baskerville

## Motion
- Micro-interactions: 150–200 ms ease
- Panel/modal enter: 200–350 ms ease-out
- Prefer one transform channel at a time (slide, fade, or scale)
- Reduced motion respect via `prefers-reduced-motion`

## Layout
- Nav height 64px, sticky, backdrop blur, bottom border
- Content max-width 1032px, section vertical padding 100px desktop / 60px mobile
- Card radius 20px for feature splits; pill radius for tabs/CTAs

## Applied in this project
- `web/src/index.css` now loads DM Sans + Libre Baskerville and defines the Manus light tokens.
- `web/src/App.tsx` color values were rewritten to use the brand palette.
