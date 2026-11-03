<p align="center">
  <img src="src/persephone/static/favicon.svg" alt="Persephone logo: a pomegranate with laurel leaves" width="120">
</p>

# persephone

Webapp for managing a collection of books: what you own (and who owns each
copy), what you have read, your ratings, favorites and notes.

- **Backend:** Python 3.10+, [FastAPI](https://fastapi.tiangolo.com/), SQLAlchemy 2 on SQLite.
- **Frontend:** plain HTML, CSS and JavaScript (no build step, no framework), served by FastAPI.
- **Tooling:** pytest (+ coverage) and ruff.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
make install     # pip install -e ".[dev]"
make seed        # optional: a dozen demo books
make serve       # http://127.0.0.1:8000
```

The library lives in `local/persephone.db` (git-ignored). Interactive API docs
are at <http://127.0.0.1:8000/docs>.

## Features

- **Library** with cover grid or list layout, search (title, author, series,
  publisher, ISBN), filters (status, owned / wishlist / lent out, owner, tag,
  author) and sorting (title, author, added, rating, year, finished). The
  filters live in the URL, so back/forward and bookmarks work.
- **Book page:** reading status (dates are filled in when you start or finish a
  book), half-star rating (mouse or arrow keys), favorite, start/finish dates,
  review and notes, tags, and full details.
- **Ownership:** any number of copies per book, each with an owner, format
  (hardcover, paperback, e-book, audiobook, other), location, acquisition date,
  notes and who it is *lent to*. A book without copies is on the wishlist.
- **Favorites** page and **Stats** (status counts, rating distribution, books
  finished per year, top authors and tags, copies per owner and format).
- **ISBN lookup** on [Open Library](https://openlibrary.org) to fill in the
  add-book form, plus Open Library covers for books that have an ISBN.
  Turn both off with `PERSEPHONE_OPENLIBRARY=0`.
- **Help** page written for non-technical users, plus an **API** tab that opens
  the interactive API docs.
- Light and dark themes (follows the system, with a toggle). Keyboard
  shortcuts: `/` focuses the search box, `n` adds a book.

ISBNs are validated and stored as ISBN-13, so `0-441-17271-7` and
`9780441172719` are recognized as the same edition.

## Commands

| Command | What it does |
| --- | --- |
| `make serve` | Run the app with auto-reload (`python -m persephone.web --help` for host/port/db) |
| `make seed` | Add demo books (safe to run twice) |
| `make import` | Import every `.csv` / `.json` file in `info/` |
| `make export-data [OWNER=name]` | Back up the library to `local/persephone-export-….json` |
| `make import-data FILE=path` | Restore / merge a backup |
| `make test` | Run the tests with coverage |
| `make lint` | Run ruff (`ruff format` is also used for formatting) |
| `make build` | Build the sdist and wheel |

Every command accepts `--db PATH` when run directly, e.g.
`python -m persephone.importer info --db /tmp/test.db --owner Ana`.

### Importing (`make import`)

Put your files in `info/` (git-ignored, as it holds personal data):

- **Goodreads export** (*My Books → Import and export → Export library*).
  Ratings, shelves (as tags, with `favorites` becoming the favorite flag),
  read dates, reviews and *owned copies* are carried over, and series written
  in titles, like `Dune (Dune Chronicles, #1)`, are split out.
- **Any CSV** with a header row (`,`, `;` or tab separated). Column names are
  matched loosely, in English or Spanish: `title`, `author(s)`, `isbn`,
  `publisher`, `year`, `pages`, `language`, `description`, `cover`, `series`,
  `series_index`, `status`, `rating`, `favorite`, `tags`/`genres`, `started`,
  `finished`/`date read`, `review`/`notes`, `owner`, `format`, `location`,
  `owned`/`copies` (a number or yes/no). Separate several authors or tags with `;`.
- **JSON**: a list of objects with the same field names, or a Persephone backup.

Importing again is safe. Books already in the library are matched by ISBN, or
by title plus author, and only get their *missing* details filled in, so edits
you made in the app are kept. Copies are created for `--owner` (default:
`PERSEPHONE_OWNER`) unless the file names an owner.

### Backups (`make export-data` / `make import-data`)

The export is a complete JSON snapshot: book details, status, rating, favorite,
dates, review, tags and copies. With `OWNER=name` only that person's copies are
included (all books are still exported) and the file is labelled with their
name, which is useful for handing over "your" part of a shared library.

Restoring merges into the current library. Matching books take the file's
personal data (status, rating, favorite, dates, review), have missing details
filled in, gain the file's tags and get any missing copies. Unknown books are
created. Backups can also be downloaded and restored from the *Backup* page.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PERSEPHONE_DB` | `local/persephone.db` | SQLite file |
| `PERSEPHONE_DATABASE_URL` | – | Full SQLAlchemy URL, overrides `PERSEPHONE_DB` |
| `PERSEPHONE_OWNER` | `Me` | Owner of new copies when none is given |
| `PERSEPHONE_OPENLIBRARY` | `1` | `0` disables ISBN lookups and Open Library covers |

The schema is created automatically on start-up. There are no migrations yet:
after a model change, export your data, delete the database and import it back.

## Project layout

```
src/persephone/
  web.py          FastAPI app factory and `python -m persephone.web`
  api.py          JSON API under /api
  services.py     business logic shared by the API and the CLIs
  models.py       SQLAlchemy models (Book, Author, Tag, Copy)
  schemas.py      Pydantic request/response models and validation
  importer.py     info/ importer (Goodreads, CSV, JSON)
  userdata.py     JSON backup and restore
  seed.py         demo data
  openlibrary.py  ISBN lookup
  isbn.py         ISBN-10/13 validation and normalization
  static/         the frontend (index.html, app.css, app.js)
tests/            pytest suite
```

### API overview

`GET/POST /api/books` (filters: `q status favorite owned owner author tag lent
min_rating`, `sort`, `order`, `limit`, `offset`) ·
`GET/PATCH/DELETE /api/books/{id}` · `POST /api/books/{id}/copies` ·
`PATCH/DELETE /api/copies/{id}` · `GET /api/authors | /api/tags | /api/owners |
/api/stats | /api/config` · `GET /api/lookup/isbn/{isbn}` ·
`GET /api/export` · `POST /api/import`.

## Theme

The palette follows classical paintings of Persephone: laurel green, pomegranate
red and a little gold on parchment, with a deep-green dark mode. All colors are
CSS custom properties at the top of `src/persephone/static/app.css`.
