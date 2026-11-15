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
  author) and sorting. By default favorites come first, then owned books, then
  the rest, each group A-Z (`sort=shelf`); title, author, added, rating, year
  and finished are also available. The
  filters live in the URL, so back/forward and bookmarks work. 50 books per
  page (10 columns x 5 rows on a full-width 1920px window, 5 columns at half
  width), with the same pager above and below the books.
- **Book page:** reading status (dates are filled in when you start or finish a
  book), half-star rating (mouse or arrow keys), favorite, start/finish dates,
  review and notes, tags, and full details.
- **Ownership:** any number of copies per book, each with an owner, format
  (hardcover, paperback, e-book, audiobook, other), location, acquisition date,
  notes and who it is *lent to*. A book without copies is on the wishlist.
- **Budget** tab: an active cart of books with a price, an optional discount
  per book and optional shipping per cart, plus a cart history. Carts can be
  saved (their books go to the wishlist), reopened for editing, bought (their
  books become owned, the cart is kept) and deleted (their books go back to the
  wishlist). Adding a book you already own shows a warning but is allowed.
- The library itself opens from the **Persephone** logo; there is no separate
  Library tab.
- Two statuses per book: **ownership** (none, Wanted or Owned, where owned means
  it has a copy) and, for owned books only, the **reading status** (none, To read,
  Reading, Read, Abandoned). Favorites are separate and can be any book. The heart
  and plus buttons on each cover mark favorites and wanted books, and the library
  can be filtered by both.
- **Stats** (status counts, rating distribution, books
  finished per year, top authors and tags, copies per owner and format).
- **ISBN lookup** on [Open Library](https://openlibrary.org) to fill in the
  add-book form, plus Open Library covers for books that have an ISBN.
  Turn both off with `PERSEPHONE_OPENLIBRARY=0`.
- **Covers are saved locally** (in `local/covers/`, next to the database), so
  they keep working when the original link breaks. New and edited books get
  theirs in the background; `make covers` or the Backup page fetches the rest.
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
| `make covers [FORCE=1]` | Save a local copy of every book cover |
| `make import` | Import `info/books.yaml` and every `.csv` / `.json` file in `info/` |
| `make export-data [OWNER=name]` | Back up the library to `local/persephone-export-….json` |
| `make import-data FILE=path` | Restore / merge a backup |
| `make test` | Run the tests with coverage |
| `make lint` | Run ruff (`ruff format` is also used for formatting) |
| `make build` | Build the sdist and wheel |

Every command accepts `--db PATH` when run directly, e.g.
`python -m persephone.importer info --db /tmp/test.db --owner Ana`.

### Importing (`make import`)

Put your files in `info/` (git-ignored, as it holds personal data):

- **YAML library**: three files that refer to each other by id.

  ```yaml
  # authors.yaml: id -> name, or id -> details (all optional but the name)
  frank-herbert:
    name: Frank Herbert
    gender: male
    origin: United States
    year_born: 1920
    year_deceased: 1986    # leave out for living authors

  # publishers.yaml: id -> name, or id -> details
  ace:
    name: Ace Books
    origin: United States
    year_created: 1952
    year_closed: 2001      # leave out if still running

  # books.yaml: a list of books
  - title: Dune
    authors: [frank-herbert]
    publisher: ace
    isbn: 0-441-17271-7
    series: Dune Chronicles
    series_index: 1
    original_year: 1965    # first publication; published_year is your edition
    status: read
    rating: 4.5
    favorite: true
    tags: [sci-fi]
    finished_on: 2023-02-14
    copies:
      - {owner: Demeter, format: hardcover, location: Study, lent_to: Hecate}
  ```

  Books take the same field names as the CSV columns below, plus
  `original_title`, `original_language`, `original_year` and `translator` for
  translations and later editions. A book with no
  `copies` is on the wishlist. A book that names an author or publisher id
  missing from `authors.yaml` / `publishers.yaml` is reported and skipped.
  Author and publisher details fill in empty fields only, like book details;
  details for an author with no books are ignored.
- **Goodreads export** (*My Books → Import and export → Export library*).
  Ratings, shelves (as tags, with `favorites` becoming the favorite flag),
  read dates, reviews and *owned copies* are carried over, and series written
  in titles, like `Dune (Dune Chronicles, #1)`, are split out.
- **Any CSV** with a header row (`,`, `;` or tab separated). Column names are
  matched loosely, in English or Spanish: `title`, `author(s)`, `isbn`,
  `publisher`, `year`, `pages`, `language`, `description`, `cover`, `series`,
  `series_index`, `original_title`, `original_language`, `original_year`,
  `translator`, `status`, `rating`, `favorite`, `tags`/`genres`, `started`,
  `finished`/`date read`, `review`/`notes`, `owner`, `format`, `location`,
  `owned`/`copies` (a number or yes/no). Separate several authors or tags with `;`.
- **JSON**: a list of objects with the same field names, or a Persephone backup.

Importing again is safe. Books already in the library are matched by ISBN, or
by title plus author, and only get their *missing* details filled in, so edits
you made in the app are kept. Copies are created for `--owner` (default:
`PERSEPHONE_OWNER`) unless the file names an owner.

### Backups (`make export-data` / `make import-data`)

The export is a complete JSON snapshot: book details, status, rating, favorite,
dates, review, tags and copies, plus author and publisher details. With `OWNER=name` only that person's copies are
included (all books are still exported) and the file is labelled with their
name, which is useful for handing over "your" part of a shared library.

Restoring merges into the current library. Matching books take the file's
personal data (status, rating, favorite, dates, review), have missing details
filled in, gain the file's tags and get any missing copies. Unknown books are
created. Backups can also be downloaded and restored from the *Backup* page.
The export keeps each book's cover link but not the image files: copy
`local/covers/` along with it, or run `make covers` after restoring.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PERSEPHONE_DB` | `local/persephone.db` | SQLite file |
| `PERSEPHONE_DATABASE_URL` | – | Full SQLAlchemy URL, overrides `PERSEPHONE_DB` |
| `PERSEPHONE_OWNER` | `Me` | Owner of new copies when none is given |
| `PERSEPHONE_OPENLIBRARY` | `1` | `0` disables ISBN lookups and Open Library covers |
| `PERSEPHONE_COVERS` | `covers/` next to the database | Folder for downloaded covers |
| `PERSEPHONE_CURRENCY` | `$` | Symbol shown next to prices in the Budget tab |

The schema is created automatically on start-up, new tables included, and older
databases are upgraded where needed (for example to make a book's status
optional). For other model changes, export your data, delete the database and
import it back.

## Project layout

```
src/persephone/
  web.py          FastAPI app factory and `python -m persephone.web`
  api.py          JSON API under /api
  services.py     business logic shared by the API and the CLIs
  budget.py       Budget tab: carts, prices and their effect on ownership
  covers.py       local copies of the book covers
  models.py       SQLAlchemy models (Book, Author, Publisher, Tag, Copy, Cart)
  schemas.py      Pydantic request/response models and validation
  importer.py     info/ importer (YAML, Goodreads, CSV, JSON)
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
`PATCH/DELETE /api/copies/{id}` · `GET /api/authors | /api/publishers | /api/tags |
/api/owners | /api/stats | /api/config` (authors and publishers include their details) · `GET /api/lookup/isbn/{isbn}` ·
`GET /api/carts | /api/carts/active | /api/carts/{id}` · `POST /api/carts/active/items` ·
`PATCH/DELETE /api/carts/{id}` · `POST /api/carts/{id}/save | buy | edit` ·
`PATCH/DELETE /api/cart-items/{id}` ·
`GET /api/covers` · `POST /api/covers/download` · `GET /api/export` · `POST /api/import`.

## Theme

The palette follows classical paintings of Persephone: laurel green, pomegranate
red and a little gold on parchment, with a deep-green dark mode. All colors are
CSS custom properties at the top of `src/persephone/static/app.css`.
