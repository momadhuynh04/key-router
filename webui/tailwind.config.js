/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: {
          50: '#fafafa',
          600: '#3a3a3f',
          700: '#2a2a2e',
          800: '#232326',
          850: '#1a1a1d',
          900: '#141415',
          950: '#0a0a0b',
        },
        border: '#262629',
        text: {
          primary: '#fafafa',
          secondary: '#a1a1aa',
          muted: '#71717a',
        },
        accent: {
          DEFAULT: '#f59e0b',
          muted: '#d97706',
          bg: 'rgba(245,158,11,0.10)',
          border: 'rgba(245,158,11,0.30)',
        },
        brand: {
          DEFAULT: '#38bdf8',
          muted: '#0ea5e9',
          bg: 'rgba(56,189,248,0.10)',
          border: 'rgba(56,189,248,0.30)',
        },
        success: '#22c55e',
        danger: '#ef4444',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'Helvetica', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
