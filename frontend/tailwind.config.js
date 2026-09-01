/** @type {import('tailwindcss').Config} */
//
// Industrial control-panel palette. NOT the Tailwind default -- this is
// projected next to a dozen default-styled chat boxes.
//
// Colour carries meaning and nothing else:
//   iso    green   verified, isolated, ok        -- the sovereignty claim
//   work   amber   in progress, loading, waiting
//   fault  red     failed, timed out, breached
//   accent cyan    interactive / selected / the one accent colour
// Everything else is `steel`, a cool graphite ramp.
//
// Typography is two SYSTEM stacks, no webfonts: a font file would be one more
// asset to vendor, and a missing one is a demo-day surprise for no benefit.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        steel: {
          950: '#0a0d10', 900: '#0f1418', 850: '#141a20', 800: '#1a2229',
          750: '#212b34', 700: '#2a3540', 600: '#3d4b58', 500: '#5a6b7a',
          400: '#8095a5', 300: '#a8bac7', 200: '#cbd7e0', 100: '#e6edf2',
        },
        iso:    { DEFAULT: '#2ee59d', dim: '#1a9c6b', deep: '#0d3d2a' },
        work:   { DEFAULT: '#f2b134', dim: '#b8821f', deep: '#3d2f0d' },
        fault:  { DEFAULT: '#ff5c5c', dim: '#c03636', deep: '#3d1414' },
        accent: { DEFAULT: '#38bdf8', dim: '#1d7fa8', deep: '#0c2f3d' },
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'SF Mono', 'JetBrains Mono',
               'Menlo', 'Consolas', 'monospace'],
        sans: ['system-ui', '-apple-system', 'Segoe UI', 'Roboto',
               'Helvetica Neue', 'sans-serif'],
      },
      fontSize: {
        micro: ['10px', { lineHeight: '14px', letterSpacing: '0.08em' }],
        tiny: ['11px', { lineHeight: '16px' }],
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
