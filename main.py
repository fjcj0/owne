
import os
import io
import re
import json
import base64
import requests
import fitz

from PIL import Image
from tkinter import Tk, filedialog, messagebox


# =====================================================
# SETTINGS
# =====================================================

OLLAMA_URL = "http://localhost:11434"

MODEL = "qwen3-vl:32b"

DPI = 240

MAX_IMAGE_SIZE = 2600

TIMEOUT = 900


# =====================================================
# JSON SCHEMA HELPERS
# =====================================================

def nullable(value_type):
    return [value_type, "null"]


INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_number": nullable("string"),
        "invoice_date": nullable("string"),

        "supplier_name_he": nullable("string"),
        "supplier_name_ar": nullable("string"),
        "supplier_tax_id": nullable("string"),

        "subtotal": nullable("number"),
        "discount_amount": nullable("number"),
        "other_charges": nullable("number"),

        "vat_rate": nullable("number"),
        "vat_amount": nullable("number"),

        "total_amount": nullable("number"),

        "currency": nullable("string"),
        "notes": nullable("string")
    },

    "required": [
        "invoice_number",
        "invoice_date",
        "supplier_name_he",
        "supplier_name_ar",
        "supplier_tax_id",
        "subtotal",
        "discount_amount",
        "other_charges",
        "vat_rate",
        "vat_amount",
        "total_amount",
        "currency",
        "notes"
    ],

    "additionalProperties": False
}


ITEM_SCHEMA = {
    "type": "object",

    "properties": {
        "item_name_he": nullable("string"),
        "item_name_ar": nullable("string"),
        "item_code": nullable("string"),

        "quantity": nullable("number"),
        "unit": nullable("string"),

        "unit_price": nullable("number"),
        "total": nullable("number")
    },

    "required": [
        "item_name_he",
        "item_name_ar",
        "item_code",
        "quantity",
        "unit",
        "unit_price",
        "total"
    ],

    "additionalProperties": False
}


PAGE_SCHEMA = {
    "type": "object",

    "properties": {
        "invoice": INVOICE_SCHEMA,

        "items": {
            "type": "array",
            "items": ITEM_SCHEMA
        },

        "page_notes": {
            "type": "array",
            "items": {
                "type": "string"
            }
        }
    },

    "required": [
        "invoice",
        "items",
        "page_notes"
    ],

    "additionalProperties": False
}


# =====================================================
# PROMPTS
# =====================================================

SYSTEM_PROMPT = """
You are an expert Hebrew invoice OCR reader,
Israeli purchase invoice specialist,
accountant and Hebrew to Arabic translator.

You receive scanned images of Hebrew purchase invoices.

Your job is to extract every visible piece of
financial information accurately.

IMPORTANT RULES:

1. Read Hebrew from right to left.

2. Understand complex invoice tables.

3. Extract every product row.

4. Translate product names from Hebrew into Arabic.

5. Keep the original Hebrew product name.

6. Never invent missing information.

7. If a value is unclear, return null.

8. Preserve invoice numbers exactly.

9. Preserve product codes exactly.

10. Preserve all prices and quantities.

11. Never confuse quantity with unit price.

12. Never confuse unit price with line total.

13. Extract VAT rate and VAT amount separately.

14. Extract subtotal and final total separately.

15. Do not calculate missing values.

16. Never modify numbers to make calculations match.

17. Use numeric values for prices and quantities.

18. Use ILS, USD or EUR as currency when identifiable.

19. Keep the original printed invoice date.
    Return it in ISO format only if its meaning is clear.
    Otherwise preserve the printed date.

20. A document may contain multiple pages.

21. A page may contain only continued product rows.

22. Do not invent header information on continuation pages.

23. Ignore logos and decorative elements.

24. The invoice may contain Hebrew and English.

25. Return all visible product rows, even if some
    fields are null.

26. Arabic product names must be translations,
    not transliterations.

27. If the meaning of a product is unclear,
    preserve its Hebrew name and return null for Arabic.

28. If the invoice includes multiple VAT rates,
    do not merge them into one guessed rate.

29. Do not confuse a package price with a unit price.

30. Preserve decimal precision as printed.

Return only data matching the JSON schema.
"""


USER_PROMPT = """
Analyze this scanned purchase invoice page.

Extract the invoice header and all product rows.

For every product extract:

- Hebrew original name
- Arabic translated name
- Product code
- Quantity
- Unit
- Unit price
- Total amount

For invoice information extract:

- Invoice number
- Invoice date
- Supplier name Hebrew
- Supplier name Arabic
- Supplier tax ID
- Subtotal
- Discount
- Other charges
- VAT rate
- VAT amount
- Final total
- Currency
- Notes

This may be one page of a multi-page invoice.

Extract only what is visible.
Never guess missing data.
Do not omit any product row.
"""


# =====================================================
# GLOBAL RESULTS
# =====================================================

# All extracted invoices stay in memory.

all_results = []


# =====================================================
# CHECK OLLAMA
# =====================================================

def check_ollama():

    response = requests.get(
        f"{OLLAMA_URL}/api/tags",
        timeout=15
    )

    response.raise_for_status()

    models = response.json().get("models", [])

    installed = [
        model["name"]
        for model in models
    ]

    if MODEL not in installed:

        raise Exception(
            f"النموذج غير مثبت: {MODEL}\n"
            f"نفذ الأمر:\nollama pull {MODEL}"
        )


# =====================================================
# PDF PAGE TO IMAGE
# =====================================================

def page_to_image(page):

    scale = DPI / 72

    matrix = fitz.Matrix(
        scale,
        scale
    )

    pix = page.get_pixmap(
        matrix=matrix,
        alpha=False
    )

    image = Image.open(
        io.BytesIO(
            pix.tobytes("png")
        )
    ).convert("RGB")

    image.thumbnail(
        (MAX_IMAGE_SIZE, MAX_IMAGE_SIZE),
        Image.Resampling.LANCZOS
    )

    buffer = io.BytesIO()

    image.save(
        buffer,
        format="JPEG",
        quality=92,
        optimize=True
    )

    return buffer.getvalue()


# =====================================================
# IMAGE ENCODING
# =====================================================

def image_to_base64(image_bytes):

    return base64.b64encode(
        image_bytes
    ).decode("utf-8")


# =====================================================
# OLLAMA REQUEST
# =====================================================

def ask_ollama(
    image_bytes,
    page_number,
    total_pages
):

    image_base64 = image_to_base64(
        image_bytes
    )

    prompt = f"""
{USER_PROMPT}

Page number: {page_number}

Total pages: {total_pages}

This is page {page_number} of the document.

If this is a continuation page,
extract all product rows even if
the invoice header is not repeated.
"""

    payload = {

        "model": MODEL,

        "messages": [

            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },

            {
                "role": "user",
                "content": prompt,
                "images": [
                    image_base64
                ]
            }

        ],

        "format": PAGE_SCHEMA,

        "stream": False,

        "keep_alive": "10m",

        "options": {
            "temperature": 0,
            "num_ctx": 32768
        }

    }

    response = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json=payload,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    result = response.json()

    content = result["message"]["content"]

    return json.loads(content)


# =====================================================
# MERGE INVOICE DATA
# =====================================================

def merge_invoice(
    existing,
    new_data,
    warnings,
    page_number
):

    if existing is None:

        return new_data.copy()

    for key, new_value in new_data.items():

        old_value = existing.get(key)

        if new_value is None:
            continue

        if old_value is None:

            existing[key] = new_value

        elif old_value != new_value:

            warnings.append(
                f"الصفحة {page_number}: "
                f"اختلاف في {key}: "
                f"{old_value} مقابل {new_value}"
            )

    return existing


# =====================================================
# VALIDATION
# =====================================================

def is_number(value):

    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def validate_invoice(
    invoice_data,
    items_data
):

    warnings = []

    item_totals = [
        item.get("total")
        for item in items_data
    ]

    if (
        item_totals
        and all(is_number(x) for x in item_totals)
    ):

        items_sum = round(
            sum(item_totals),
            2
        )

    else:

        items_sum = None

        warnings.append(
            "بعض الأصناف لا تحتوي على مبلغ واضح."
        )

    subtotal = invoice_data.get("subtotal")

    if (
        items_sum is not None
        and is_number(subtotal)
    ):

        difference = round(
            items_sum - subtotal,
            2
        )

        if abs(difference) > 0.05:

            warnings.append(
                f"مجموع الأصناف {items_sum} "
                f"لا يساوي صافي الفاتورة {subtotal}. "
                f"الفرق: {difference}"
            )

    else:

        difference = None

    vat = invoice_data.get("vat_amount")

    total = invoice_data.get("total_amount")

    discount = (
        invoice_data.get("discount_amount") or 0
    )

    charges = (
        invoice_data.get("other_charges") or 0
    )

    calculated_total = None

    total_difference = None

    if (
        is_number(subtotal)
        and is_number(vat)
        and is_number(total)
        and is_number(discount)
        and is_number(charges)
    ):

        calculated_total = round(
            subtotal - discount + charges + vat,
            2
        )

        total_difference = round(
            total - calculated_total,
            2
        )

        if abs(total_difference) > 0.05:

            warnings.append(
                "الإجمالي لا يطابق مجموع "
                "الصافي والضريبة والخصم والرسوم. "
                f"الفرق: {total_difference}"
            )

    return {

        "items_sum": items_sum,

        "subtotal": subtotal,

        "items_difference": difference,

        "calculated_total": calculated_total,

        "actual_total": total,

        "total_difference": total_difference,

        "review_required": bool(warnings),

        "warnings": warnings

    }


# =====================================================
# PROCESS ONE PDF
# =====================================================

def process_pdf(pdf_path):

    print("\n" + "=" * 60)

    print(
        "معالجة الفاتورة:",
        os.path.basename(pdf_path)
    )

    document = fitz.open(pdf_path)

    if len(document) == 0:

        raise Exception("ملف PDF فارغ.")

    invoice_data = None

    items_data = []

    warnings = []

    total_pages = len(document)

    try:

        for page_index, page in enumerate(document):

            page_number = page_index + 1

            print(
                f"\nالصفحة {page_number} "
                f"من {total_pages}"
            )

            # Convert scanned PDF page to image

            image_bytes = page_to_image(page)

            # Send image directly to Ollama

            result = ask_ollama(
                image_bytes,
                page_number,
                total_pages
            )

            # Extract invoice header

            page_invoice = result["invoice"]

            # Extract items

            page_items = result["items"]

            # Merge invoice header from all pages

            invoice_data = merge_invoice(
                invoice_data,
                page_invoice,
                warnings,
                page_number
            )

            # Append products from current page

            for item in page_items:

                item["source_page"] = page_number

                items_data.append(item)

            # Page notes

            for note in result["page_notes"]:

                warnings.append(
                    f"الصفحة {page_number}: {note}"
                )

            print(
                f"تم استخراج {len(page_items)} صنف."
            )

        # Validate data

        validation_data = validate_invoice(
            invoice_data,
            items_data
        )

        warnings.extend(
            validation_data["warnings"]
        )

        validation_data["warnings"] = warnings

        validation_data["review_required"] = bool(
            warnings
        )

        # Final result in Python variables

        result_data = {

            "source_pdf": os.path.basename(pdf_path),

            "pages_processed": total_pages,

            "invoice": invoice_data,

            "items": items_data,

            "validation": validation_data

        }

        # Print result

        print("\n" + "=" * 60)

        print("بيانات الفاتورة:")

        print(
            json.dumps(
                invoice_data,
                ensure_ascii=False,
                indent=4
            )
        )

        print("\nالأصناف:")

        print(
            json.dumps(
                items_data,
                ensure_ascii=False,
                indent=4
            )
        )

        print("\nالتحقق:")

        print(
            json.dumps(
                validation_data,
                ensure_ascii=False,
                indent=4
            )
        )

        print("\nاكتملت الفاتورة.")

        return result_data

    finally:

        document.close()


# =====================================================
# MAIN
# =====================================================

def main():

    global all_results

    root = Tk()

    root.withdraw()

    # Select PDFs

    pdf_files = filedialog.askopenfilenames(

        title="اختر فواتير المشتريات",

        filetypes=[
            ("PDF Files", "*.pdf")
        ]

    )

    if not pdf_files:

        return

    # Check Ollama

    try:

        print("فحص Ollama...")

        check_ollama()

    except Exception as error:

        messagebox.showerror(
            "خطأ",
            str(error)
        )

        return

    all_results = []

    success = 0

    failed = 0

    errors = []

    # Process invoices

    for index, pdf_path in enumerate(
        pdf_files,
        start=1
    ):

        print("\n" + "#" * 60)

        print(
            f"الفاتورة {index} من {len(pdf_files)}"
        )

        try:

            result_data = process_pdf(
                pdf_path
            )

            all_results.append(
                result_data
            )

            success += 1

        except Exception as error:

            failed += 1

            errors.append(
                f"{os.path.basename(pdf_path)}: {error}"
            )

            print(
                "خطأ في الفاتورة:",
                error
            )

    # Final summary

    print("\n" + "=" * 60)

    print("انتهت العملية.")

    print("الفواتير الناجحة:", success)

    print("الفواتير الفاشلة:", failed)

    print(
        "إجمالي الأصناف:",
        sum(
            len(x["items"])
            for x in all_results
        )
    )

    print(
        "عدد الفواتير التي تحتاج مراجعة:",
        sum(
            x["validation"]["review_required"]
            for x in all_results
        )
    )

    if errors:

        print("\nالأخطاء:")

        for error in errors:

            print(error)

    messagebox.showinfo(

        "اكتملت العملية",

        f"الفواتير الناجحة: {success}\n"
        f"الفواتير الفاشلة: {failed}\n"
        f"إجمالي الأصناف: "
        f"{sum(len(x['items']) for x in all_results)}\n\n"
        "البيانات محفوظة في ذاكرة Python فقط."

    )

    # all_results remains available
    # for the next stage of the program.


# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":

    main()
