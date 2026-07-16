/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#050507',
        surface: '#0d0f14',
        panel: '#151821',
        card: '#1d202a',
        border: '#2d313d',
        primary: {
          DEFAULT: '#f4c15d',
          light: '#ffe2a1',
          dark: '#b7791f',
        },
        muted: '#a6a1a0',
        text: '#fff7ea',
        cinema: {
          ink: '#050507',
          wine: '#241016',
          ember: '#c25a2e',
          gold: '#f4c15d',
          blue: '#162236',
        },
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(244,193,93,0.16), 0 24px 80px rgba(0,0,0,0.48)',
        poster: '0 28px 80px rgba(0,0,0,0.62)',
      },
    },
  },
  plugins: [],
}
