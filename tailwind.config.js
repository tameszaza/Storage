/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './static/js/**/*.js',
  ],
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      boxShadow: {
        panel: '0 18px 55px rgba(15, 23, 42, 0.10)',
        lift: '0 18px 45px rgba(15, 23, 42, 0.14)',
      },
    },
  },
  plugins: [],
};
