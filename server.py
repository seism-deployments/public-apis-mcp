from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn
from fastmcp import FastMCP
import httpx
import os
import re
from typing import Optional, List

mcp = FastMCP("public-apis")

# The public APIs list is hosted on GitHub
README_URL = "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"

VALID_AUTH_KEYS = ["apiKey", "OAuth", "X-Mashape-Key", "User-Agent", "No", ""]
VALID_HTTPS_KEYS = ["Yes", "No"]
VALID_CORS_KEYS = ["Yes", "No", "Unknown"]
MAX_DESCRIPTION_LENGTH = 100


async def fetch_readme() -> str:
    """Fetch the README.md from the public-apis GitHub repository."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(README_URL)
        response.raise_for_status()
        return response.text


def parse_readme(content: str) -> dict:
    """
    Parse the README.md and return a dict:
    {
        "Category Name": [
            {"name": ..., "url": ..., "description": ..., "auth": ..., "https": ..., "cors": ...},
            ...
        ],
        ...
    }
    """
    categories = {}
    current_category = None
    lines = content.split("\n")

    for line in lines:
        # Detect category headers like "### Animals"
        category_match = re.match(r'^###\s+(.+)$', line)
        if category_match:
            current_category = category_match.group(1).strip()
            categories[current_category] = []
            continue

        # Parse table rows
        if current_category and line.startswith('|') and not line.startswith('| API') and not line.startswith('|---'):
            parts = [p.strip() for p in line.split('|')]
            # parts[0] is empty, parts[1] is API name/link, parts[2] desc, parts[3] auth, parts[4] https, parts[5] cors
            if len(parts) >= 6:
                name_cell = parts[1]
                desc_cell = parts[2]
                auth_cell = parts[3]
                https_cell = parts[4]
                cors_cell = parts[5] if len(parts) > 5 else "Unknown"

                # Extract name and URL from markdown link [Name](URL)
                link_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', name_cell)
                if link_match:
                    api_name = link_match.group(1).strip()
                    api_url = link_match.group(2).strip()
                else:
                    api_name = name_cell
                    api_url = ""

                # Clean auth (remove backticks)
                auth_clean = auth_cell.replace('`', '').strip()

                entry = {
                    "name": api_name,
                    "url": api_url,
                    "description": desc_cell,
                    "auth": auth_clean,
                    "https": https_cell,
                    "cors": cors_cell,
                    "category": current_category
                }
                categories[current_category].append(entry)

    return categories


@mcp.tool()
async def search_apis(
    query: str,
    category: Optional[str] = None,
    auth: Optional[str] = None,
    https: Optional[bool] = None,
    cors: Optional[str] = None
) -> dict:
    """Search the public APIs repository for APIs matching a keyword, category, or feature.
    Use this when a user wants to find APIs for a specific purpose, domain, or technology.
    Returns matching API entries with name, description, auth type, HTTPS support, and CORS status."""
    try:
        content = await fetch_readme()
        all_categories = parse_readme(content)

        results = []
        query_lower = query.lower()

        for cat_name, entries in all_categories.items():
            # Filter by category if provided
            if category and cat_name.lower() != category.lower():
                continue

            for entry in entries:
                # Search in name and description
                if query_lower not in entry["name"].lower() and query_lower not in entry["description"].lower():
                    continue

                # Filter by auth
                if auth is not None:
                    if auth.lower() == "no":
                        if entry["auth"].strip() not in ["", "No"]:
                            continue
                    else:
                        if entry["auth"].lower() != auth.lower():
                            continue

                # Filter by https
                if https is not None:
                    if https and entry["https"] != "Yes":
                        continue
                    if not https and entry["https"] != "No":
                        continue

                # Filter by cors
                if cors is not None:
                    if entry["cors"].lower() != cors.lower():
                        continue

                results.append(entry)

        return {
            "query": query,
            "filters": {
                "category": category,
                "auth": auth,
                "https": https,
                "cors": cors
            },
            "count": len(results),
            "results": results
        }
    except Exception as e:
        return {"error": str(e), "results": []}


@mcp.tool()
async def list_categories(include_count: bool = True) -> dict:
    """List all available API categories in the public APIs repository.
    Use this when a user wants to explore what domains or topics are covered,
    or when they want to browse APIs by category. Returns all category names with entry counts."""
    try:
        content = await fetch_readme()
        all_categories = parse_readme(content)

        if include_count:
            categories = [
                {"name": cat, "count": len(entries)}
                for cat, entries in all_categories.items()
                if cat  # skip empty category names
            ]
        else:
            categories = [
                {"name": cat}
                for cat in all_categories.keys()
                if cat
            ]

        return {
            "total_categories": len(categories),
            "categories": categories
        }
    except Exception as e:
        return {"error": str(e), "categories": []}


@mcp.tool()
async def get_category_apis(category: str) -> dict:
    """Retrieve all APIs within a specific category from the public APIs repository.
    Use this when a user wants to see all available APIs in a particular domain
    like 'Music', 'Sports', or 'Cryptocurrency'."""
    try:
        content = await fetch_readme()
        all_categories = parse_readme(content)

        # Case-insensitive search
        matched_category = None
        for cat_name in all_categories.keys():
            if cat_name.lower() == category.lower():
                matched_category = cat_name
                break

        if matched_category is None:
            available = list(all_categories.keys())
            return {
                "error": f"Category '{category}' not found.",
                "available_categories": available
            }

        entries = all_categories[matched_category]
        return {
            "category": matched_category,
            "count": len(entries),
            "apis": entries
        }
    except Exception as e:
        return {"error": str(e), "apis": []}


@mcp.tool()
async def validate_format(
    file_path: Optional[str] = "README.md",
    entry: Optional[str] = None
) -> dict:
    """Validate the format of the public APIs README.md or a specific API entry.
    Checks alphabetical order, description length, valid auth/HTTPS/CORS values, and overall structure."""
    errors = []
    warnings = []

    if entry:
        # Validate a single entry line
        # Expected format: | [Name](URL) | Description | auth | Yes/No | Yes/No/Unknown |
        parts = [p.strip() for p in entry.split('|')]
        # Remove empty first/last
        parts = [p for p in parts if p != '']

        if len(parts) < 5:
            errors.append(f"Entry must have 5 columns: API, Description, Auth, HTTPS, CORS. Got {len(parts)}.")
        else:
            name_cell = parts[0]
            desc_cell = parts[1]
            auth_cell = parts[2].replace('`', '').strip()
            https_cell = parts[3].strip()
            cors_cell = parts[4].strip()

            # Check name/url format
            link_match = re.match(r'\[([^\]]+)\]\(([^)]+)\)', name_cell)
            if not link_match:
                errors.append("API name must be in markdown link format: [Name](URL)")
            else:
                url = link_match.group(2)
                if not url.startswith('http'):
                    errors.append(f"URL '{url}' should start with http or https")

            # Check description length
            if len(desc_cell) > MAX_DESCRIPTION_LENGTH:
                errors.append(f"Description too long: {len(desc_cell)} chars (max {MAX_DESCRIPTION_LENGTH})")
            if not desc_cell:
                errors.append("Description cannot be empty")

            # Check auth
            if auth_cell not in VALID_AUTH_KEYS:
                errors.append(f"Auth '{auth_cell}' is invalid. Must be one of: {VALID_AUTH_KEYS}")

            # Check https
            if https_cell not in VALID_HTTPS_KEYS:
                errors.append(f"HTTPS '{https_cell}' is invalid. Must be 'Yes' or 'No'")

            # Check cors
            if cors_cell not in VALID_CORS_KEYS:
                errors.append(f"CORS '{cors_cell}' is invalid. Must be one of: {VALID_CORS_KEYS}")

        return {
            "valid": len(errors) == 0,
            "entry": entry,
            "errors": errors,
            "warnings": warnings
        }

    else:
        # Validate the full README from GitHub
        try:
            content = await fetch_readme()
            lines = content.split("\n")
            all_categories = parse_readme(content)

            # Check categories are in alphabetical order
            cat_names = list(all_categories.keys())
            sorted_cats = sorted(cat_names)
            if cat_names != sorted_cats:
                errors.append(f"Categories are not in alphabetical order. Expected: {sorted_cats}")

            # Check each entry
            for cat_name, entries in all_categories.items():
                if len(entries) == 0:
                    warnings.append(f"Category '{cat_name}' has no entries")

                entry_names = [e["name"] for e in entries]
                sorted_names = sorted(entry_names, key=lambda x: x.lower())
                if entry_names != sorted_names:
                    errors.append(f"Entries in '{cat_name}' are not in alphabetical order")

                for e in entries:
                    if len(e["description"]) > MAX_DESCRIPTION_LENGTH:
                        errors.append(f"[{cat_name}] '{e['name']}': description too long ({len(e['description'])} chars)")
                    if e["auth"] not in VALID_AUTH_KEYS:
                        errors.append(f"[{cat_name}] '{e['name']}': invalid auth '{e['auth']}'")
                    if e["https"] not in VALID_HTTPS_KEYS:
                        errors.append(f"[{cat_name}] '{e['name']}': invalid https '{e['https']}'")
                    if e["cors"] not in VALID_CORS_KEYS:
                        errors.append(f"[{cat_name}] '{e['name']}': invalid cors '{e['cors']}'")

            return {
                "valid": len(errors) == 0,
                "file": file_path,
                "total_categories": len(all_categories),
                "errors": errors,
                "warnings": warnings
            }
        except Exception as ex:
            return {"error": str(ex), "valid": False}


@mcp.tool()
async def validate_links(
    urls: Optional[List[str]] = None,
    category: Optional[str] = None,
    timeout: int = 30
) -> dict:
    """Check whether API links in the repository are still active and accessible.
    Use this when you want to verify that API URLs are not broken or returning errors."""
    results = []
    links_to_check = []

    if urls:
        links_to_check = [(url, None) for url in urls]
    else:
        try:
            content = await fetch_readme()
            all_categories = parse_readme(content)
            for cat_name, entries in all_categories.items():
                if category and cat_name.lower() != category.lower():
                    continue
                for entry in entries:
                    if entry["url"]:
                        links_to_check.append((entry["url"], entry["name"]))
        except Exception as e:
            return {"error": str(e), "results": []}

    # Limit to avoid overwhelming
    MAX_LINKS = 50
    if len(links_to_check) > MAX_LINKS:
        links_to_check = links_to_check[:MAX_LINKS]
        truncated = True
    else:
        truncated = False

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for url, name in links_to_check:
            result = {"url": url, "name": name}
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (compatible; PublicAPIsBot/1.0)"
                }
                response = await client.get(url, headers=headers)
                result["status_code"] = response.status_code
                result["accessible"] = response.status_code < 400
                result["error"] = None
            except httpx.TimeoutException:
                result["status_code"] = None
                result["accessible"] = False
                result["error"] = "Timeout"
            except Exception as e:
                result["status_code"] = None
                result["accessible"] = False
                result["error"] = str(e)
            results.append(result)

    accessible = [r for r in results if r["accessible"]]
    broken = [r for r in results if not r["accessible"]]

    return {
        "total_checked": len(results),
        "accessible": len(accessible),
        "broken": len(broken),
        "truncated": truncated,
        "results": results
    }


@mcp.tool()
async def get_api_details(api_name: str) -> dict:
    """Retrieve detailed information about a specific API by name.
    Use this when a user wants to learn more about a particular API including
    its URL, description, authentication method, HTTPS support, and CORS policy."""
    try:
        content = await fetch_readme()
        all_categories = parse_readme(content)

        matches = []
        for cat_name, entries in all_categories.items():
            for entry in entries:
                if entry["name"].lower() == api_name.lower():
                    matches.append(entry)
                elif api_name.lower() in entry["name"].lower():
                    matches.append(entry)

        if not matches:
            return {
                "found": False,
                "query": api_name,
                "message": f"No API found with name '{api_name}'"
            }

        # Return exact match if found, otherwise all partial matches
        exact = [m for m in matches if m["name"].lower() == api_name.lower()]
        if exact:
            return {
                "found": True,
                "query": api_name,
                "count": len(exact),
                "apis": exact
            }

        return {
            "found": True,
            "query": api_name,
            "count": len(matches),
            "note": "No exact match found, returning partial matches",
            "apis": matches
        }
    except Exception as e:
        return {"error": str(e), "found": False}


@mcp.tool()
async def suggest_api_entry(
    name: str,
    url: str,
    description: str,
    auth: str,
    https: bool,
    cors: str,
    category: str
) -> dict:
    """Generate a correctly formatted markdown table entry for a new API to be contributed
    to the public APIs repository. Validates all fields before generating the entry."""
    errors = []
    warnings = []

    # Validate name
    if not name.strip():
        errors.append("Name cannot be empty")

    # Validate URL
    if not url.startswith("http"):
        errors.append(f"URL must start with 'http' or 'https', got: '{url}'")

    # Validate description
    if not description.strip():
        errors.append("Description cannot be empty")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        errors.append(f"Description is too long ({len(description)} chars). Max is {MAX_DESCRIPTION_LENGTH} characters.")

    # Validate auth
    if auth not in VALID_AUTH_KEYS:
        errors.append(f"Auth '{auth}' is invalid. Must be one of: {VALID_AUTH_KEYS}")

    # Validate cors
    if cors not in VALID_CORS_KEYS:
        errors.append(f"CORS '{cors}' is invalid. Must be one of: {VALID_CORS_KEYS}")

    # Validate category exists
    try:
        content = await fetch_readme()
        all_categories = parse_readme(content)
        category_names_lower = [c.lower() for c in all_categories.keys()]
        matched_category = None
        for cat_name in all_categories.keys():
            if cat_name.lower() == category.lower():
                matched_category = cat_name
                break

        if matched_category is None:
            errors.append(f"Category '{category}' does not exist. Available: {list(all_categories.keys())}")
        else:
            # Check alphabetical position
            entries = all_categories[matched_category]
            entry_names = [e["name"] for e in entries]
            suggested_position = None
            for i, en in enumerate(sorted(entry_names + [name], key=lambda x: x.lower())):
                if en == name:
                    suggested_position = i + 1
                    break
            warnings.append(f"This entry should be inserted at position {suggested_position} in the '{matched_category}' category (alphabetical order).")
    except Exception as e:
        warnings.append(f"Could not validate category: {str(e)}")
        matched_category = category

    # Build the formatted entry
    https_str = "Yes" if https else "No"
    auth_formatted = f"`{auth}`" if auth and auth != "No" and auth != "" else auth if auth else ""

    formatted_entry = f"| [{name}]({url}) | {description} | {auth_formatted} | {https_str} | {cors} |"

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "formatted_entry": formatted_entry if len(errors) == 0 else None,
        "fields": {
            "name": name,
            "url": url,
            "description": description,
            "auth": auth,
            "https": https_str,
            "cors": cors,
            "category": matched_category or category
        },
        "instructions": (
            f"Add this line to the '{matched_category or category}' section of README.md in alphabetical order:\n"
            + formatted_entry
        ) if len(errors) == 0 else None
    }




async def health(request):
    return JSONResponse({"status": "ok", "server": mcp.name})

async def tools(request):
    registered = await mcp.list_tools()
    tool_list = [{"name": t.name, "description": t.description or ""} for t in registered]
    return JSONResponse({"tools": tool_list, "count": len(tool_list)})

sse_app = mcp.http_app(transport="sse")

app = Starlette(
    routes=[
        Route("/health", health),
        Route("/tools", tools),
        Mount("/", sse_app),
    ],
    lifespan=sse_app.lifespan,
)
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
