# apps/assets

Static assets for the dev harness (`apps/dev_ui.py`).

- The browser-tab icon. Name it **`favicon.png`** (or `.ico`/`.svg`) to be explicit, or
  just drop any image here — `dev_ui.py`'s `_favicon_path()` prefers a `favicon.*` file and
  otherwise falls back to the first image in this folder. If none is present the app still
  launches (no icon). Dev-only asset.
