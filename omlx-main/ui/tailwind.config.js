/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Minimal neutral palette. Status colors reused across pages.
        healthy: '#166534',
        stale: '#854d0e',
        missing: '#991b1b',
        partial: '#1d4ed8',
      },
    },
  },
  plugins: [],
};
