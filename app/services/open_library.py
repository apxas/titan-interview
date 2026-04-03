import asyncio

import httpx

BASE_URL = "https://openlibrary.org"
COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"


async def search_works(query_type: str, query_value: str, limit: int = 50) -> list[dict]:
    """Call the OL search endpoint and return raw docs."""
    param_key = "author" if query_type == "author" else "subject"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"{BASE_URL}/search.json",
            params={param_key: query_value, "limit": limit},
        )
        resp.raise_for_status()
        return resp.json().get("docs", [])


async def resolve_work(
    work_key: str,
    search_result: dict,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """
    Assemble a complete, normalised book record from a search result.
    Makes follow-up calls to Work Detail and Author Detail endpoints only
    when the search result is missing data.
    """
    _own_client = client is None
    if _own_client:
        client = httpx.AsyncClient(timeout=60)

    try:
        title = search_result.get("title") or ""
        authors: list[str] = list(search_result.get("author_name") or [])
        first_publish_year = search_result.get("first_publish_year")
        subjects: list[str] = list(search_result.get("subject") or [])
        cover_i = search_result.get("cover_i")

        need_subjects = not subjects
        need_authors = not authors
        work_detail: dict = {}

        # Fetch Work Detail when subjects are missing (or authors too and no author_keys)
        if need_subjects:
            work_id = work_key.lstrip("/").split("/")[-1]  # "/works/OL123W" → "OL123W"
            try:
                resp = await client.get(f"{BASE_URL}/works/{work_id}.json")
                if resp.status_code == 200:
                    work_detail = resp.json()
                await asyncio.sleep(0.5)
            except Exception:
                pass

        # Resolve title fallback
        if not title:
            title = work_detail.get("title") or ""

        # Resolve subjects
        if need_subjects:
            subjects = list(work_detail.get("subjects") or [])

        # Resolve cover_url
        cover_url: str | None = None
        if cover_i:
            cover_url = COVER_URL.format(cover_id=cover_i)
        elif work_detail.get("covers"):
            cover_url = COVER_URL.format(cover_id=work_detail["covers"][0])

        # Resolve authors via author follow-up calls if needed
        if need_authors:
            # Prefer author_keys from search; fall back to work detail authors array
            raw_keys: list[str] = list(search_result.get("author_keys") or [])
            if not raw_keys and work_detail.get("authors"):
                raw_keys = [
                    entry.get("author", {}).get("key", "")
                    for entry in work_detail["authors"]
                ]

            for key in raw_keys:
                if not key:
                    continue
                author_id = key.lstrip("/").split("/")[-1]  # "/authors/OL123A" → "OL123A"
                try:
                    resp = await client.get(f"{BASE_URL}/authors/{author_id}.json")
                    if resp.status_code == 200:
                        data = resp.json()
                        name = data.get("name") or data.get("personal_name")
                        if name:
                            authors.append(name)
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

        return {
            "ol_work_id": work_key,
            "title": title,
            "authors": authors,
            "first_publish_year": first_publish_year,
            "subjects": subjects,
            "cover_url": cover_url,
            "raw_data": search_result,
        }
    finally:
        if _own_client:
            await client.aclose()
