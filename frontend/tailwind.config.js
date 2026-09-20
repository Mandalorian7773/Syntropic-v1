/** @type {import('tailwindcss').Config} */
//
// Every colour is a CSS variable (RGB channels) defined twice in index.css:
// once for the light theme on :root, once for `.dark`. Class names therefore
// stay the same in both themes and opacity modifiers (bg-iso-deep/40) work.
//
// Colour carries meaning and nothing else:
//   iso    green   verified, isolated, ok
//   work   amber   in progress, loading, waiting
//   fault  red     failed, timed out, breached
//   accent green   interactive / selected / the one accent colour
//   brand  green   solid primary buttons (white text on it in both themes)
// Everything else is `steel`, a neutral ramp: 950 is the page, 900 a card,
// 800 a border, 100 the strongest text. The ramp flips between themes.
//
// Typography is SYSTEM stacks, no webfonts: a font file would be one more
// asset to vendor, and a missing one is a demo-day surprise for no benefit.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const ramp = (name, keys) =>
  Object.fromEntries(keys.map((k) => [k, v(`${name}-${k}`)]));

export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        steel: ramp('steel', [950, 900, 850, 800, 750, 700, 600, 500, 400, 300, 200, 100]),
        iso:    { DEFAULT: v('iso'), dim: v('iso-dim'), deep: v('iso-deep') },
        work:   { DEFAULT: v('work'), dim: v('work-dim'), deep: v('work-deep') },
        fault:  { DEFAULT: v('fault'), dim: v('fault-dim'), deep: v('fault-deep') },
        accent: { DEFAULT: v('accent'), dim: v('accent-dim'), deep: v('accent-deep') },
        brand:  { DEFAULT: v('brand'), hover: v('brand-hover') },
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'SF Mono', 'JetBrains Mono',
               'Menlo', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto',
               'Helvetica Neue', 'sans-serif'],
      },
      fontSize: {
        micro: ['11px', { lineHeight: '15px', letterSpacing: '0.02em' }],
        tiny: ['12.5px', { lineHeight: '18px' }],
      },
      boxShadow: {
        card: '0 1px 2px rgb(0 0 0 / 0.04), 0 4px 16px -6px rgb(0 0 0 / 0.08)',
        pop: '0 8px 30px -8px rgb(0 0 0 / 0.18)',
      },
      animation: {
        // Loading states only -- the brief rules out decorative motion.
        'pulse-slow': 'pulse 2.2s cubic-bezier(0.4,0,0.6,1) infinite',
        sweep: 'sweep 1.4s linear infinite',
      },
      keyframes: {
        sweep: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(400%)' },
        },
      },
    },
  },
  plugins: [],
};
