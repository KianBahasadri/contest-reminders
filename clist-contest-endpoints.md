# CLIST Contest Endpoints

Source: `https://clist.by/api/v4/doc/`

## Base

- Base path: `https://clist.by/api/v4`
- Main contest collection: `GET /contest/`
- Single contest by id: `GET /contest/{id}/`
- Default rate limit: `10` requests per minute
- Formats: `json`, `jsonp`, `yaml`, `xml`, `plist`

## Auth

- CLIST API access requires an API key or an authenticated website session.
- Format can be selected with a path prefix like `/api/v4/json/...` or with `?format=json`.

## Contest List

`GET https://clist.by/api/v4/contest/`

Useful query params:

- `limit`, `offset`, `total_count`
- `upcoming=true` to keep only upcoming contests
- `with_problems=true` to include problem data
- `format_time=true` to convert times to the user timezone/time format
- `start_time__during=1 day` or `10 days`
- `end_time__during=1 day`
- `resource`, `resource_id`, `host`, `event`
- `start__gte`, `start__lte`, `end__gte`, `end__lte`
- `duration__gte`, `duration__lte`
- `order_by=start` or `order_by=-start`

Common fields in each contest object:

- `id`
- `resource`, `resource_id`
- `host`
- `event`
- `start`, `end`
- `duration`
- `href`
- `n_statistics`, `n_problems`
- `parsed_at`
- `problems`

## Contest Detail

`GET https://clist.by/api/v4/contest/{id}/`

Returns one contest object for the given CLIST contest id.

## RSS / Atom

- The contest endpoint also supports feeds.
- Use `?format=atom` or `?format=rss` on `/api/v4/contest/`.
- Feed defaults include `?upcoming=true&format_time=true&start_time__during=1 day`.

## Examples

Upcoming contests in JSON:

`https://clist.by/api/v4/contest/?upcoming=true&format=json`

Upcoming contests for the next 7 days, sorted by start:

`https://clist.by/api/v4/contest/?upcoming=true&start_time__during=7%20days&order_by=start&format=json`

Single contest by id:

`https://clist.by/api/v4/contest/2673/?format=json`
