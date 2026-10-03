"""Read-only CAAN search shared by the CAAN Search page."""

from sqlalchemy import or_

from archives_application.models import CAANModel


def search_caans(query: str) -> list[dict[str, str]]:
    """Match every whitespace-separated term against CAAN, name, or description."""
    records = CAANModel.query
    for term in query.split():
        pattern = f"%{term}%"
        records = records.filter(or_(
            CAANModel.caan.ilike(pattern),
            CAANModel.name.ilike(pattern),
            CAANModel.description.ilike(pattern),
        ))
    results = records.order_by(CAANModel.caan.asc()).all()
    return [
        {"caan": row.caan, "name": row.name or "", "description": row.description or ""}
        for row in results
    ]
