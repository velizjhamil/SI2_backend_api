"""Reusable SQL expressions for accounting balance semantics."""


def nature_balance_delta_sql(nature: str, debit: str, credit: str) -> str:
    """Build the signed balance expression used by the accounting books."""
    return (
        f"CASE WHEN {nature}='DEUDORA' THEN ({debit})-({credit}) "
        f"ELSE ({credit})-({debit}) END"
    )
