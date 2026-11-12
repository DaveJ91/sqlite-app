import os
from enum import Enum

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from pypdf import PdfReader

RATES = {
    "standard": "23%",
    "reduced": "13.5%",
    "second_reduced": "9%",
    "livestock": "4.8%",
    "zero": "0%",
    "exempt": "Exempt",
    "reverse_charge": "Reverse charge",
    "outside_scope": "Outside scope",
}


class ClassificationError(Exception):
    pass


class VatCategory(str, Enum):
    standard = "standard"
    reduced = "reduced"
    second_reduced = "second_reduced"
    livestock = "livestock"
    zero = "zero"
    exempt = "exempt"
    reverse_charge = "reverse_charge"
    outside_scope = "outside_scope"


class LineClassification(BaseModel):
    description: str
    net_amount: str = ""
    vat_amount: str = ""
    category: VatCategory
    reason: str


class InvoiceClassification(BaseModel):
    supplier: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    currency: str = ""
    net_total: str = ""
    vat_total: str = ""
    gross_total: str = ""
    summary: str = ""
    lines: list[LineClassification] = Field(default_factory=list)


SYSTEM_PROMPT = """You classify Irish VAT on a supplier invoice.
Use only descriptions and figures printed in the invoice. Do not invent amounts.
If a figure is missing, leave it blank.
Choose one category for each line:
- standard: 23%
- reduced: 13.5%
- second_reduced: 9%
- livestock: 4.8%
- zero: 0%
- exempt: exempt supply, no VAT charged
- reverse_charge: the customer accounts for the VAT
- outside_scope: not an Irish VAT supply
If the invoice already prints a VAT rate, use the category that matches it unless the description clearly contradicts that rate.
Give a short reason for each line.
"""


def extract_text(upload) -> str:
    upload.seek(0)
    try:
        reader = PdfReader(upload)
        if reader.is_encrypted:
            raise ClassificationError("This PDF is encrypted.")
        parts = [(page.extract_text() or "") for page in reader.pages]
    except ClassificationError:
        raise
    except Exception as exc:
        raise ClassificationError("Could not read this PDF.") from exc
    finally:
        upload.seek(0)
    return "\n".join(parts).strip()


def classify_invoice(text: str) -> dict:
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise ClassificationError(
            "Set XAI_API_KEY in a .env file in the project folder."
        )
    model = ChatOpenAI(
        model="grok-4.7",
        api_key=api_key,
        base_url="https://api.x.ai/v1",
    )
    structured = model.with_structured_output(
        InvoiceClassification,
        method="function_calling",
    )
    try:
        result = structured.invoke(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=text[:50000]),
            ]
        )
    except Exception as exc:
        raise ClassificationError(
            f"Classification failed ({type(exc).__name__})."
        ) from exc
    if not isinstance(result, InvoiceClassification):
        raise ClassificationError("Classification failed (bad response).")
    data = result.model_dump(mode="json")
    for line in data["lines"]:
        line["rate"] = RATES[line["category"]]
    return data
