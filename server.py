from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn
from fastmcp import FastMCP
import httpx
import os
import re
import random
from typing import Optional, List

mcp = FastMCP("Public APIs")

PUBLIC_APIS_URL = "https://api.publicapis.org/entries"
PUBLIC_APIS_CATEGORIES_URL = "https://api.publicapis.org/categories"
PUBLIC_APIS_RANDOM_URL = "https://api.publicapis.org/random"

VALID_AUTH_KEYS = ["", "apiKey", "OAuth", "X-Mashape-Key", "User-Agent", "No"]
VALID_HTTPS_KEYS = ["Yes", "No"]
VALID_CORS_KEYS = ["Yes", "No", "Unknown"]
MAX_DESCRIPTION_LENGTH = 100
MIN_ENTRIES_PER_CATEGORY = 3


async def fetch_all_entries() -> List[dict]:
    """Fetch all entries from the public APIs service."""
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(PUBLIC_APIS_URL)
        response.raise_for_status()
        data = response.json()
        return data.get("entries", [])


@mcp.tool()
async def search_apis(
    query: Optional[str] = None,
    category: Optional[str] = None,
    auth: Optional[str] = None,
    https: Optional[bool] = None,
    cors: Optional[str] = None
) -> dict:
    """Search and filter public APIs from the repository by category, authentication type, HTTPS support, or CORS support."""
    try:
        params = {}
        if query:
            params["title"] = query
        if category:
            params["category"] = category
        if auth is not None:
            params["auth"] = auth
        if https is not None:
            params["https"] = "true" if https else "false"
        if cors is not None:
            params["cors"] = cors

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(PUBLIC_APIS_URL, params=params)
            response.raise_for_status()
            data = response.json()

        entries = data.get("entries", []) or []

        # Additional client-side filtering for query if not fully handled server-side
        if query and entries:
            q = query.lower()
            entries = [
                e for e in entries
                if q in e.get("API", "").lower() or q in e.get("Description", "").lower()
            ]

        results = []
        for entry in entries:
            results.append({
                "name": entry.get("API", ""),
                "description": entry.get("Description", ""),
                "auth": entry.get("Auth", ""),
                "https": entry.get("HTTPS", False),
                "cors": entry.get("Cors", "Unknown"),
                "category": entry.get("Category", ""),
                "link": entry.get("Link", "")
            })

        return {
            "count": len(results),
            "filters_applied": {
                "query": query,
                "category": category,
                "auth": auth,
                "https": https,
                "cors": cors
            },
            "results": results
        }
    except httpx.HTTPError as e:
        return {"error": f"HTTP error fetching APIs: {str(e)}", "results": []}
    except Exception as e:
        return {"error": f"Error searching APIs: {str(e)}", "results": []}


@mcp.tool()
async def list_categories(sort_by_count: bool = False) -> dict:
    """List all available API categories in the public APIs repository with their entry counts."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            cat_response = await client.get(PUBLIC_APIS_CATEGORIES_URL)
            cat_response.raise_for_status()
            categories_data = cat_response.json()

        categories_list = categories_data if isinstance(categories_data, list) else []

        # Fetch entries to count per category
        async with httpx.AsyncClient(timeout=15) as client:
            entries_response = await client.get(PUBLIC_APIS_URL)
            entries_response.raise_for_status()
            entries_data = entries_response.json()

        entries = entries_data.get("entries", []) or []

        # Count entries per category
        category_counts = {}
        for entry in entries:
            cat = entry.get("Category", "Unknown")
            category_counts[cat] = category_counts.get(cat, 0) + 1

        # Build result
        result_categories = []
        for cat in categories_list:
            result_categories.append({
                "name": cat,
                "count": category_counts.get(cat, 0)
            })

        # Add any categories found in entries but not in categories endpoint
        for cat, count in category_counts.items():
            if cat not in categories_list:
                result_categories.append({"name": cat, "count": count})

        if sort_by_count:
            result_categories.sort(key=lambda x: x["count"], reverse=True)
        else:
            result_categories.sort(key=lambda x: x["name"])

        return {
            "total_categories": len(result_categories),
            "total_apis": len(entries),
            "categories": result_categories
        }
    except httpx.HTTPError as e:
        return {"error": f"HTTP error fetching categories: {str(e)}", "categories": []}
    except Exception as e:
        return {"error": f"Error listing categories: {str(e)}", "categories": []}


@mcp.tool()
async def get_api_details(api_name: str) -> dict:
    """Get detailed information about a specific API by name."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(PUBLIC_APIS_URL, params={"title": api_name})
            response.raise_for_status()
            data = response.json()

        entries = data.get("entries", []) or []

        if not entries:
            return {"found": False, "message": f"No API found matching '{api_name}'", "results": []}

        # Try exact match first, then partial
        exact_matches = [e for e in entries if e.get("API", "").lower() == api_name.lower()]
        partial_matches = [e for e in entries if api_name.lower() in e.get("API", "").lower()]

        matches = exact_matches if exact_matches else partial_matches

        results = []
        for entry in matches:
            results.append({
                "name": entry.get("API", ""),
                "description": entry.get("Description", ""),
                "auth": entry.get("Auth", "") or "No authentication required",
                "https_supported": entry.get("HTTPS", False),
                "cors": entry.get("Cors", "Unknown"),
                "category": entry.get("Category", ""),
                "link": entry.get("Link", "")
            })

        return {
            "found": True,
            "query": api_name,
            "count": len(results),
            "results": results
        }
    except httpx.HTTPError as e:
        return {"error": f"HTTP error fetching API details: {str(e)}", "found": False}
    except Exception as e:
        return {"error": f"Error getting API details: {str(e)}", "found": False}


@mcp.tool()
async def validate_format(
    content: str,
    check_alphabetical: bool = True,
    check_description_length: bool = True
) -> dict:
    """Validate the format of a README.md entry or full markdown file against public-apis repository standards."""
    errors = []
    warnings = []
    lines = content.split("\n")

    valid_auth_values = ["`apiKey`", "`OAuth`", "`X-Mashape-Key`", "`User-Agent`", "No"]
    valid_https_values = ["Yes", "No"]
    valid_cors_values = ["Yes", "No", "Unknown"]

    # Regex for a table entry row
    entry_pattern = re.compile(
        r'^\|\s*\[(.+?)\]\((.+?)\)\s*\|\s*(.+?)\s*\|\s*(`\w[\w-]*`|No)\s*\|\s*(Yes|No)\s*\|\s*(Yes|No|Unknown)\s*\|'
    )
    category_pattern = re.compile(r'^###\s+(.+)$')
    header_pattern = re.compile(r'^API\s*\|\s*Description\s*\|\s*Auth\s*\|\s*HTTPS\s*\|\s*CORS\s*\|')
    separator_pattern = re.compile(r'^\|[-]+\|[-]+\|[-]+\|[-]+\|[-]+\|')

    categories = {}
    current_category = None
    category_line_nums = {}
    entry_names_in_category = []

    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()

        # Detect category header
        cat_match = category_pattern.match(stripped)
        if cat_match:
            if current_category and entry_names_in_category is not None:
                categories[current_category] = entry_names_in_category
                if len(entry_names_in_category) < MIN_ENTRIES_PER_CATEGORY:
                    warnings.append({
                        "line": category_line_nums.get(current_category),
                        "type": "warning",
                        "message": f"Category '{current_category}' has only {len(entry_names_in_category)} entries (minimum recommended: {MIN_ENTRIES_PER_CATEGORY})"
                    })
            current_category = cat_match.group(1).strip()
            category_line_nums[current_category] = line_num
            entry_names_in_category = []
            continue

        if header_pattern.match(stripped) or separator_pattern.match(stripped):
            continue

        # Check table entry
        entry_match = entry_pattern.match(stripped)
        if entry_match and current_category is not None:
            name = entry_match.group(1).strip()
            url = entry_match.group(2).strip()
            description = entry_match.group(3).strip()
            auth = entry_match.group(4).strip()
            https_val = entry_match.group(5).strip()
            cors_val = entry_match.group(6).strip()

            entry_names_in_category.append(name)

            # Check description length
            if check_description_length and len(description) > MAX_DESCRIPTION_LENGTH:
                errors.append({
                    "line": line_num,
                    "type": "error",
                    "message": f"Description for '{name}' exceeds {MAX_DESCRIPTION_LENGTH} characters ({len(description)} chars): '{description}'"
                })

            # Check auth value
            if auth not in valid_auth_values:
                errors.append({
                    "line": line_num,
                    "type": "error",
                    "message": f"Invalid auth value '{auth}' for '{name}'. Must be one of: {', '.join(valid_auth_values)}"
                })

            # Check HTTPS value
            if https_val not in valid_https_values:
                errors.append({
                    "line": line_num,
                    "type": "error",
                    "message": f"Invalid HTTPS value '{https_val}' for '{name}'. Must be one of: {', '.join(valid_https_values)}"
                })

            # Check CORS value
            if cors_val not in valid_cors_values:
                errors.append({
                    "line": line_num,
                    "type": "error",
                    "message": f"Invalid CORS value '{cors_val}' for '{name}'. Must be one of: {', '.join(valid_cors_values)}"
                })

            # Check URL format
            if not url.startswith("http://") and not url.startswith("https://"):
                errors.append({
                    "line": line_num,
                    "type": "error",
                    "message": f"Invalid URL '{url}' for '{name}'. Must start with http:// or https://"
                })

    # Handle last category
    if current_category and entry_names_in_category is not None:
        categories[current_category] = entry_names_in_category
        if len(entry_names_in_category) < MIN_ENTRIES_PER_CATEGORY:
            warnings.append({
                "line": category_line_nums.get(current_category),
                "type": "warning",
                "message": f"Category '{current_category}' has only {len(entry_names_in_category)} entries (minimum recommended: {MIN_ENTRIES_PER_CATEGORY})"
            })

    # Check alphabetical order within each category
    if check_alphabetical:
        for cat, names in categories.items():
            sorted_names = sorted(names, key=lambda x: x.lower())
            if names != sorted_names:
                errors.append({
                    "line": category_line_nums.get(cat),
                    "type": "error",
                    "message": f"Entries in category '{cat}' are not in alphabetical order. Expected order: {sorted_names}"
                })

    is_valid = len(errors) == 0

    return {
        "valid": is_valid,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "categories_found": list(categories.keys()),
        "total_entries_checked": sum(len(v) for v in categories.values()),
        "checks_performed": {
            "alphabetical_order": check_alphabetical,
            "description_length": check_description_length,
            "auth_values": True,
            "https_values": True,
            "cors_values": True,
            "url_format": True,
            "min_entries_per_category": True
        }
    }


@mcp.tool()
async def validate_links(
    urls: Optional[List[str]] = None,
    markdown_content: Optional[str] = None,
    timeout_seconds: int = 10
) -> dict:
    """Check whether the URLs in a set of API entries are reachable and return valid HTTP responses."""
    # Extract URLs from markdown if provided
    all_urls = list(urls) if urls else []

    if markdown_content:
        # Find all hyperlinks in markdown
        link_pattern = re.compile(r'https?://[^\s\)\]\>\"]+', re.IGNORECASE)
        found_urls = link_pattern.findall(markdown_content)
        # Also find markdown-style links [text](url)
        md_link_pattern = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')
        md_links = md_link_pattern.findall(markdown_content)
        for _, url in md_links:
            if url not in found_urls:
                found_urls.append(url)
        for url in found_urls:
            if url not in all_urls:
                all_urls.append(url)

    if not all_urls:
        return {
            "error": "No URLs provided or found in markdown content",
            "results": []
        }

    # Deduplicate
    unique_urls = list(dict.fromkeys(all_urls))

    results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PublicAPIsBot/1.0; +https://github.com/public-apis/public-apis)"
    }

    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=True,
        headers=headers
    ) as client:
        for url in unique_urls:
            try:
                response = await client.get(url)
                results.append({
                    "url": url,
                    "status_code": response.status_code,
                    "reachable": response.status_code < 500,
                    "ok": 200 <= response.status_code < 400,
                    "error": None
                })
            except httpx.TimeoutException:
                results.append({
                    "url": url,
                    "status_code": None,
                    "reachable": False,
                    "ok": False,
                    "error": f"Timeout after {timeout_seconds} seconds"
                })
            except httpx.ConnectError as e:
                results.append({
                    "url": url,
                    "status_code": None,
                    "reachable": False,
                    "ok": False,
                    "error": f"Connection error: {str(e)}"
                })
            except Exception as e:
                results.append({
                    "url": url,
                    "status_code": None,
                    "reachable": False,
                    "ok": False,
                    "error": str(e)
                })

    ok_count = sum(1 for r in results if r["ok"])
    failed_count = len(results) - ok_count

    return {
        "total_checked": len(results),
        "ok_count": ok_count,
        "failed_count": failed_count,
        "results": results
    }


@mcp.tool()
async def generate_entry(
    name: str,
    url: str,
    description: str,
    auth: str,
    https: bool,
    cors: str,
    category: str
) -> dict:
    """Generate a properly formatted markdown table row for a new API entry suitable for submission to the public-apis repository."""
    errors = []
    warnings = []

    # Validate inputs
    valid_auth_values = ["apiKey", "OAuth", "X-Mashape-Key", "User-Agent", "No"]
    valid_cors_values = ["Yes", "No", "Unknown"]

    if auth not in valid_auth_values:
        errors.append(f"Invalid auth value '{auth}'. Must be one of: {', '.join(valid_auth_values)}")

    if cors not in valid_cors_values:
        errors.append(f"Invalid CORS value '{cors}'. Must be one of: {', '.join(valid_cors_values)}")

    if not url.startswith("http://") and not url.startswith("https://"):
        errors.append(f"URL must start with http:// or https://")

    if len(description) > MAX_DESCRIPTION_LENGTH:
        errors.append(f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters ({len(description)} chars). Please shorten it.")
    elif len(description) > 80:
        warnings.append(f"Description is {len(description)} characters. Consider keeping it under 80 for readability.")

    if not name.strip():
        errors.append("API name cannot be empty")

    if errors:
        return {
            "success": False,
            "errors": errors,
            "warnings": warnings,
            "entry": None
        }

    # Format auth for markdown
    auth_formatted = "`" + auth + "`" if auth != "No" else "No"
    https_formatted = "Yes" if https else "No"

    # Generate the markdown table row
    markdown_row = f"| [{name}]({url}) | {description} | {auth_formatted} | {https_formatted} | {cors} |"

    # Generate a category section snippet
    category_snippet = f"""### {category}

| API | Description | Auth | HTTPS | CORS |
|---|---|---|---|---|
{markdown_row}"""

    return {
        "success": True,
        "errors": [],
        "warnings": warnings,
        "entry": {
            "name": name,
            "url": url,
            "description": description,
            "auth": auth,
            "https": https,
            "cors": cors,
            "category": category
        },
        "markdown_row": markdown_row,
        "category_snippet": category_snippet,
        "instructions": (
            f"Add the following row alphabetically within the '{category}' category section of the README.md. "
            "If the category doesn't exist, create a new section in alphabetical order among the other categories. "
            "Ensure the entry is placed in alphabetical order by API name within the category."
        )
    }


@mcp.tool()
async def get_random_apis(
    count: int = 5,
    category: Optional[str] = None,
    https_only: bool = False,
    no_auth_required: bool = False
) -> dict:
    """Return a random selection of public APIs, optionally filtered by category or other criteria."""
    # Clamp count
    count = max(1, min(20, count))

    try:
        # Use the random endpoint for single random if no filters needed
        params = {}
        if category:
            params["category"] = category
        if https_only:
            params["https"] = "true"
        if no_auth_required:
            params["auth"] = ""

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(PUBLIC_APIS_URL, params=params)
            response.raise_for_status()
            data = response.json()

        entries = data.get("entries", []) or []

        # Apply additional client-side filtering
        if https_only:
            entries = [e for e in entries if e.get("HTTPS", False)]
        if no_auth_required:
            entries = [e for e in entries if not e.get("Auth", "")]

        if not entries:
            return {
                "count": 0,
                "message": "No APIs found matching the given criteria.",
                "filters": {
                    "category": category,
                    "https_only": https_only,
                    "no_auth_required": no_auth_required
                },
                "results": []
            }

        # Random sample
        sample_size = min(count, len(entries))
        selected = random.sample(entries, sample_size)

        results = []
        for entry in selected:
            results.append({
                "name": entry.get("API", ""),
                "description": entry.get("Description", ""),
                "auth": entry.get("Auth", "") or "No authentication required",
                "https": entry.get("HTTPS", False),
                "cors": entry.get("Cors", "Unknown"),
                "category": entry.get("Category", ""),
                "link": entry.get("Link", "")
            })

        return {
            "count": len(results),
            "total_available": len(entries),
            "filters": {
                "category": category,
                "https_only": https_only,
                "no_auth_required": no_auth_required
            },
            "results": results
        }
    except httpx.HTTPError as e:
        return {"error": f"HTTP error fetching random APIs: {str(e)}", "results": []}
    except Exception as e:
        return {"error": f"Error getting random APIs: {str(e)}", "results": []}




async def health(request):
    return JSONResponse({"status": "ok", "server": mcp.name})

async def tools(request):
    registered = await mcp.list_tools()
    tool_list = [{"name": t.name, "description": t.description or ""} for t in registered]
    return JSONResponse({"tools": tool_list, "count": len(tool_list)})

mcp_app = mcp.http_app(transport="streamable-http")

class _FixAcceptHeader:
    """Ensure Accept header includes both types FastMCP requires."""
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            accept = headers.get(b"accept", b"").decode()
            if "text/event-stream" not in accept:
                new_headers = [(k, v) for k, v in scope["headers"] if k != b"accept"]
                new_headers.append((b"accept", b"application/json, text/event-stream"))
                scope = dict(scope, headers=new_headers)
        await self.app(scope, receive, send)

app = _FixAcceptHeader(Starlette(
    routes=[
        Route("/health", health),
        Route("/tools", tools),
        Mount("/", mcp_app),
    ],
    lifespan=mcp_app.lifespan,
))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
