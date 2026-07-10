import os
import re
from datetime import date

import httpx
from fastmcp import FastMCP

from dotenv import load_dotenv


load_dotenv()


SALESFORCE_INSTANCE_URL = os.environ.get("SALESFORCE_INSTANCE_URL")
SALESFORCE_ACCESS_TOKEN = os.environ.get("SALESFORCE_ACCESS_TOKEN")
SALESFORCE_API_VERSION = os.environ.get("SALESFORCE_API_VERSION", "v61.0")


mcp = FastMCP("My MCP Server")


def _escape_soql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _escape_soql_like(value: str) -> str:
    return _escape_soql_string(value).replace("%", "\\%").replace("_", "\\_")


def _validate_salesforce_date(value: str, field_name: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be an ISO date in YYYY-MM-DD format"
        ) from exc

    return value


def _validate_field_api_name(value: str, field_name: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:__[a-z])?", value):
        raise ValueError(f"{field_name} must be a valid Salesforce field API name")

    return value


def _parse_region_from_description(description: str | None) -> str:
    if not description:
        return "Unspecified"

    match = re.search(r"(?:^|[\s,;])Region\s*[:=-]\s*([^\n,;|]+)", description, re.I)
    if not match:
        return "Unspecified"

    return match.group(1).strip() or "Unspecified"


async def _salesforce_query(soql: str) -> dict:
    if not SALESFORCE_ACCESS_TOKEN:
        raise RuntimeError("SALESFORCE_ACCESS_TOKEN is not configured")

    query_url = (
        f"{SALESFORCE_INSTANCE_URL}/services/data/{SALESFORCE_API_VERSION}/query"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            query_url,
            headers={"Authorization": f"Bearer {SALESFORCE_ACCESS_TOKEN}"},
            params={"q": soql},
        )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Salesforce query failed with {response.status_code}: {response.text}"
        )

    return response.json()


def _format_pipeline_summary(records: list[dict], group_field: str) -> list[dict]:
    summary = []

    for record in records:
        group_value = record.get(group_field)

        if group_field == "AccountId":
            account = record.get("Account") or {}
            label = (
                record.get("Name")
                or account.get("Name")
                or group_value
                or "Unspecified"
            )
        else:
            label = group_value or "Unspecified"

        summary.append(
            {
                "group": label,
                "totalAmount": record.get("totalAmount") or record.get("expr0") or 0,
                "opportunityCount": (
                    record.get("opportunityCount") or record.get("expr1") or 0
                ),
            }
        )

    return summary


@mcp.tool
def greet(name: str) -> str:
    return f"Hello, {name}!"


@mcp.tool
async def get_one_salesforce_account() -> dict:
    """Query one Account from the configured Salesforce org."""
    soql = "SELECT Id, Name, Type, Industry, Phone, Website FROM Account LIMIT 1"
    payload = await _salesforce_query(soql)
    records = payload.get("records", [])

    return {
        "totalSize": payload.get("totalSize", 0),
        "done": payload.get("done", True),
        "account": records[0] if records else None,
    }


@mcp.tool
async def get_opportunities(
    stage: str | None = None,
    account: str | None = None,
    account_id: str | None = None,
    close_date_from: str | None = None,
    close_date_to: str | None = None,
    owner: str | None = None,
    limit: int = 20,
) -> dict:
    """Fetch Opportunities with optional Stage, Account, AccountId, CloseDate, and Owner filters."""
    safe_limit = min(max(limit, 1), 200)
    filters = []

    if stage:
        filters.append(f"StageName = '{_escape_soql_string(stage)}'")

    if account:
        filters.append(f"Account.Name LIKE '%{_escape_soql_like(account)}%'")

    if account_id:
        filters.append(f"AccountId = '{_escape_soql_string(account_id)}'")

    if close_date_from:
        filters.append(
            f"CloseDate >= {_validate_salesforce_date(close_date_from, 'close_date_from')}"
        )

    if close_date_to:
        filters.append(
            f"CloseDate <= {_validate_salesforce_date(close_date_to, 'close_date_to')}"
        )

    if owner:
        filters.append(f"Owner.Name LIKE '%{_escape_soql_like(owner)}%'")

    fields = [
        "Id",
        "Name",
        "StageName",
        "Amount",
        "CloseDate",
        "AccountId",
        "Account.Name",
        "OwnerId",
        "Owner.Name",
    ]
    soql = f"SELECT {', '.join(fields)} FROM Opportunity"

    if filters:
        soql += f" WHERE {' AND '.join(filters)}"

    soql += f" ORDER BY CloseDate DESC LIMIT {safe_limit}"

    payload = await _salesforce_query(soql)

    return {
        "totalSize": payload.get("totalSize", 0),
        "done": payload.get("done", True),
        "opportunities": payload.get("records", []),
    }


@mcp.tool
async def get_opportunity_by_id(
    opportunity_id: str,
) -> dict:
    """Fetch one Opportunity by Id with detailed fields including Description and NextStep."""
    fields = [
        "Id",
        "Name",
        "StageName",
        "Amount",
        "Probability",
        "CloseDate",
        "Type",
        "LeadSource",
        "Description",
        "NextStep",
        "AccountId",
        "Account.Name",
        "OwnerId",
        "Owner.Name",
        "CreatedDate",
        "LastModifiedDate",
    ]
    soql = (
        f"SELECT {', '.join(fields)} FROM Opportunity "
        f"WHERE Id = '{_escape_soql_string(opportunity_id)}' LIMIT 1"
    )

    payload = await _salesforce_query(soql)
    records = payload.get("records", [])

    return {
        "totalSize": payload.get("totalSize", 0),
        "done": payload.get("done", True),
        "opportunity": records[0] if records else None,
    }


@mcp.tool
async def get_pipeline_summary(
    group_by: str = "stage",
    region_field: str | None = None,
    max_records: int = 2000,
) -> dict:
    """Aggregate Opportunity Amount by Stage, Account, or Region."""
    normalized_group_by = group_by.strip().lower()

    if normalized_group_by == "stage":
        soql = (
            "SELECT StageName, SUM(Amount) totalAmount, COUNT(Id) opportunityCount "
            "FROM Opportunity GROUP BY StageName ORDER BY SUM(Amount) DESC"
        )
        payload = await _salesforce_query(soql)

        return {
            "groupBy": "stage",
            "totalSize": payload.get("totalSize", 0),
            "done": payload.get("done", True),
            "summary": _format_pipeline_summary(
                payload.get("records", []), "StageName"
            ),
        }

    if normalized_group_by == "account":
        soql = (
            "SELECT AccountId, Account.Name, SUM(Amount) totalAmount, "
            "COUNT(Id) opportunityCount FROM Opportunity "
            "GROUP BY AccountId, Account.Name ORDER BY SUM(Amount) DESC"
        )
        payload = await _salesforce_query(soql)

        return {
            "groupBy": "account",
            "totalSize": payload.get("totalSize", 0),
            "done": payload.get("done", True),
            "summary": _format_pipeline_summary(
                payload.get("records", []), "AccountId"
            ),
        }

    if normalized_group_by != "region":
        raise ValueError("group_by must be one of: stage, account, region")

    if region_field:
        safe_region_field = _validate_field_api_name(region_field, "region_field")
        soql = (
            f"SELECT {safe_region_field}, SUM(Amount) totalAmount, "
            f"COUNT(Id) opportunityCount FROM Opportunity "
            f"GROUP BY {safe_region_field} ORDER BY SUM(Amount) DESC"
        )
        payload = await _salesforce_query(soql)

        return {
            "groupBy": "region",
            "regionSource": safe_region_field,
            "totalSize": payload.get("totalSize", 0),
            "done": payload.get("done", True),
            "summary": _format_pipeline_summary(
                payload.get("records", []), safe_region_field
            ),
        }

    safe_max_records = min(max(max_records, 1), 2000)
    soql = f"SELECT Id, Amount, Description FROM Opportunity LIMIT {safe_max_records}"
    payload = await _salesforce_query(soql)
    regions: dict[str, dict] = {}

    for opportunity in payload.get("records", []):
        region = _parse_region_from_description(opportunity.get("Description"))
        amount = opportunity.get("Amount") or 0
        current = regions.setdefault(
            region,
            {
                "group": region,
                "totalAmount": 0,
                "opportunityCount": 0,
            },
        )
        current["totalAmount"] += amount
        current["opportunityCount"] += 1

    summary = sorted(
        regions.values(),
        key=lambda item: item["totalAmount"],
        reverse=True,
    )

    return {
        "groupBy": "region",
        "regionSource": "Description",
        "totalSize": payload.get("totalSize", 0),
        "done": payload.get("done", True),
        "recordLimit": safe_max_records,
        "summary": summary,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
