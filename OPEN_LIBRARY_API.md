# Open Library API Reference

## Base URL
https://openlibrary.org

## Endpoints Used

### 1. Search Works
GET /search.json?author={name}&limit=50
GET /search.json?subject={subject}&limit=50

Returns a list of works. Fields are inconsistent — treat all as nullable.

Useful fields per work:
- key (string) — e.g. "/works/OL123W" — always present, used for follow-up calls
- title (string) — usually present
- author_keys (array) — e.g. ["/authors/OL123A"] — use for author follow-up calls
- author_name (array of strings) — sometimes present, use if available to skip author follow-up
- first_publish_year (integer) — sometimes present
- subject (array of strings) — often missing, requires work detail follow-up
- cover_i (integer) — cover ID, use to construct cover URL if present
- isbn (array of strings) — sometimes present

### 2. Work Detail
GET /works/{work_id}.json
e.g. GET /works/OL123W.json

Use this when search results are missing subjects.

Useful fields:
- title (string)
- authors (array of {author: {key}}) — use keys for author follow-up
- subjects (array of strings) — more reliable than search results
- covers (array of integers) — use first value for cover URL

### 3. Author Detail
GET /authors/{author_id}.json
e.g. GET /authors/OL123A.json

Use this when author_name is missing from search results.

Useful fields:
- name (string)
- personal_name (string)

## Cover Image URL
Constructed from cover ID — no API call needed:
https://covers.openlibrary.org/b/id/{cover_id}-M.jpg

## Field Resolution Priority

### title
1. search result title
2. work detail title

### authors
1. search result author_name array (skip author follow-up if present)
2. fetch /authors/{id}.json for each key in author_keys

### first_publish_year
1. search result first_publish_year
2. leave null if missing — do not make additional calls

### subjects
1. search result subject array if present and non-empty
2. work detail subjects array

### cover_url
1. construct from search result cover_i if present
2. construct from first value of work detail covers array
3. null if neither present

## Rate Limiting
- Be respectful — add a small delay (0.5s) between bulk requests
- Do not cache or hard-code API responses as fixtures
- All calls must be made to the live API