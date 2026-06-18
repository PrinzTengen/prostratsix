'use strict';

////////////////////////////////
// Setup
////////////////////////////////

const { src, dest, parallel, series, watch } = require('gulp');

// Build tooling
const sass = require('gulp-sass')(require('sass'));
const postcss = require('gulp-postcss');
const autoprefixer = require('autoprefixer');
const cssnano = require('cssnano');
const rename = require('gulp-rename');
const plumber = require('gulp-plumber');
const sourcemaps = require('gulp-sourcemaps');
const browserSync = require('browser-sync').create();

// Relative paths function
function pathsConfig() {
  const app = 'prostratsix';

  return {
    // SCSS source lives outside the served static dir so raw partials are
    // never shipped. Only the compiled bundle lands in static/.
    sass: 'assets/scss',
    css: `${app}/static/css`,
    js: `${app}/static/js`,
    templates: `${app}/templates`,
    // Bootstrap (and other vendor) sources are resolved from node_modules.
    nodeModules: 'node_modules',
  };
}

const paths = pathsConfig();
const isProduction = process.env.NODE_ENV === 'production';

////////////////////////////////
// Tasks
////////////////////////////////

// Compile the SCSS theme (incl. Bootstrap) into a single project.css bundle.
function styles() {
  const processors = [autoprefixer()];
  if (isProduction) {
    processors.push(cssnano());
  }

  return src(`${paths.sass}/main.scss`)
    .pipe(plumber()) // keep the watcher alive on syntax errors
    .pipe(sourcemaps.init())
    .pipe(
      sass({
        // Lets @import "bootstrap/scss/..." resolve from node_modules.
        includePaths: [paths.nodeModules],
        // Bootstrap 5 still ships legacy @import partials; silence the noise.
        quietDeps: true,
        silenceDeprecations: [
          'import',
          'global-builtin',
          'color-functions',
          'legacy-js-api',
          'if-function',
        ],
      }).on('error', sass.logError),
    )
    .pipe(postcss(processors))
    .pipe(rename('project.css'))
    .pipe(sourcemaps.write('.'))
    .pipe(dest(paths.css))
    .pipe(browserSync.stream());
}

// Copy the bundled Bootstrap JS (Popper included) into the static dir so the
// site is fully self-hosted with no CDN dependency.
function vendorScripts() {
  return src(`${paths.nodeModules}/bootstrap/dist/js/bootstrap.bundle.min.js`, {
    encoding: false,
  })
    .pipe(rename('vendors.js'))
    .pipe(dest(paths.js));
}

// Live-reload dev server. Proxies the running Django app.
function serve() {
  browserSync.init({
    proxy: process.env.DJANGO_HOST || 'localhost:8000',
    open: false,
    notify: false,
  });

  watch(`${paths.sass}/**/*.scss`, styles);
  watch(`${paths.js}/project.js`).on('change', browserSync.reload);
  watch(`${paths.templates}/**/*.html`).on('change', browserSync.reload);
}

const build = series(parallel(styles, vendorScripts));
const dev = series(build, serve);

exports.styles = styles;
exports.vendorScripts = vendorScripts;
exports.build = build;
exports.serve = serve;
exports.default = dev;
