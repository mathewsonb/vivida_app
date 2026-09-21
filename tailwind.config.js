/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        parchment: {
          50: '#FDFBF7',
          100: '#F7F4EC',
          200: '#EAE4D5',
          800: '#3D3A34',
          900: '#23211E',
        },
        terracotta: {
          DEFAULT: '#C85A32',
          hover: '#B04B27',
          light: '#E29578',
        },
        sage: {
          DEFAULT: '#3A5A40',
          light: '#588157',
        },
      },
      fontFamily: {
        serif: ['Playfair Display', 'Georgia', 'serif'],
        sans: ['Plus Jakarta Sans', 'Inter', 'sans-serif'],
      },
    },
  },
  plugins: [],
};