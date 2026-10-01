"""Automated PDF and OCR test suite.

Generates diverse test PDFs (text-native, scanned image, multi-page, blank, corrupted, etc.),
runs them through the live moderation service pipeline (/v1/moderate/pdf and /v1/moderate/media),
and generates a structured test report.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
import zlib
from pathlib import Path

# Setup paths
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RESULT_SINK"] = "log"

from app import create_app
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pdf_test")

OUT_DIR = ROOT / "scripts" / "test_generated_pdfs"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- PDF generators

def _escape(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_text_pdf(pages: list[str]) -> bytes:
    """Standard multi-page text PDF with genuine PDF text streams."""
    objects: dict[int, bytes] = {}
    page_numbers: list[int] = []
    number = 4

    for i, text in enumerate(pages):
        stream = "BT /F1 12 Tf 72 720 Td 16 TL\n"
        for line in text.split("\n"):
            stream += f"({_escape(line)}) Tj T*\n"
        stream += "ET"
        raw = stream.encode("latin-1", errors="replace")
        content_num, number = number, number + 1
        objects[content_num] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(raw), raw)

        resources = "<< /Font << /F1 3 0 R >> >>"
        page_num, number = number, number + 1
        objects[page_num] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                             f"/Contents {content_num} 0 R /Resources {resources} >>").encode("latin-1")
        page_numbers.append(page_num)

    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (f"<< /Type /Pages /Kids [{' '.join(f'{n} 0 R' for n in page_numbers)}] "
                  f"/Count {len(page_numbers)} >>").encode("latin-1")
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for n in sorted(objects):
        offsets[n] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (n, objects[n])
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for n in sorted(objects):
        out += b"%010d 00000 n \n" % offsets[n]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_at)
    return bytes(out)


def _pdf_with_image(jpeg: bytes, width: int, height: int) -> bytes:
    """One-page image-only PDF sized to image aspect ratio (no text stream)."""
    page_w = 612.0
    page_h = round(612.0 * height / width, 2)
    content = f"q {page_w} 0 0 {page_h} 0 0 cm /Im0 Do Q".encode()
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] /Contents 4 0 R "
            f"/Resources << /XObject << /Im0 5 0 R >> >> >>").encode(),
        4: b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        5: (b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB "
            b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n%s\nendstream"
            % (width, height, len(jpeg), jpeg)),
    }
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for n in sorted(objects):
        offsets[n] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (n, objects[n])
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for n in sorted(objects):
        out += b"%010d 00000 n \n" % offsets[n]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref_at)
    return bytes(out)


def make_scanned_pdf(lines: list[str], font_size: int = 34, width: int = 1240, height: int = 700) -> bytes:
    """Render text as image pixels inside an image-only PDF page."""
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    for i, line in enumerate(lines):
        draw.text((70, 70 + i * 60), line, fill=(20, 20, 20), font=font)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return _pdf_with_image(buf.getvalue(), img.width, img.height)


def make_blank_image_pdf(width: int = 1240, height: int = 700) -> bytes:
    """An image-only PDF containing only blank white pixels (zero text)."""
    img = Image.new("RGB", (width, height), "white")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return _pdf_with_image(buf.getvalue(), img.width, img.height)


def _make_test_image(lines: list[str], width: int = 1100, height: int = 350) -> bytes:
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((60, 60 + i * 80), line, fill=(20, 20, 20), font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- Runner

def run_suite():
    print("=" * 80)
    print("INITIALIZING FLASK MODERATION APP WITH LIVE MODEL & OCR ENGINE")
    print("=" * 80)
    app = create_app()
    client = app.test_client()

    ready_resp = client.get("/ready")
    print(f"Ready endpoint returned: {ready_resp.status_code}")
    assert ready_resp.status_code == 200, "Service is not ready!"

    test_cases = [
        {
            "id": "TC-PDF-01",
            "name": "Clean Native Text B2B Catalog",
            "filename": "clean_catalog.pdf",
            "generator": lambda: make_text_pdf([
                "Apex Stainless Steel Works - Industrial Valve Catalog\n"
                "Manufactured from high-grade SS304 and SS316 stainless steel.\n"
                "All valves tested up to 25 bar pressure, ISO 9001:2015 certified.\n"
                "Commercial terms: 30 days credit for verified accounts, Ex-Works Pune.",
                "Page 2: Standard Dimensions and Tolerances\n"
                "Flanged ball valves from 15 mm to 300 mm NB.\n"
                "Mill Test Certificates (MTC) supplied with every dispatched lot."
            ]),
            "expected_decision": "allow",
            "expected_status": 200,
            "description": "2-page clean business catalog with native text streams."
        },
        {
            "id": "TC-PDF-02",
            "name": "Native Text Advance-Fee Scam",
            "filename": "scam_advance_fee.pdf",
            "generator": lambda: make_text_pdf([
                "Special High-Return Business Investment Scheme\n"
                "Invest today and get guaranteed returns of 50% monthly profit.\n"
                "Double your money in 7 days without risk.\n"
                "Pay a small processing fee of Rs 2500 to our agent to confirm your slot."
            ]),
            "expected_decision": "reject",
            "expected_status": 200,
            "description": "Native text PDF containing advance fee and guaranteed returns violation."
        },
        {
            "id": "TC-PDF-03",
            "name": "Scanned Image PDF with Clean B2B Text (RapidOCR)",
            "filename": "scanned_clean_invoice.pdf",
            "generator": lambda: make_scanned_pdf([
                "Surat Textile Mills Limited",
                "Tax Invoice for Cotton Bedsheets, King and Queen sizes.",
                "Quantity: 500 sets. Total Amount: Rs 3,50,000.",
                "ISO 9001 certified. Material dispatched via V-Trans."
            ]),
            "expected_decision": "allow",
            "expected_status": 200,
            "description": "Image-only raster PDF requiring RapidOCR text recognition."
        },
        {
            "id": "TC-PDF-04",
            "name": "Scanned Image PDF with Phishing Threat (RapidOCR + Reject)",
            "filename": "scanned_phishing_threat.pdf",
            "generator": lambda: make_scanned_pdf([
                "URGENT SELLER ACCOUNT WARNING",
                "Your BONC seller account will be suspended today.",
                "Send your net banking password and ATM PIN immediately to verify.",
                "Call support right now and share the OTP on your phone."
            ]),
            "expected_decision": "reject",
            "expected_status": 200,
            "description": "Image-only PDF where OCR extracts words that violate phishing policy."
        },
        {
            "id": "TC-PDF-05",
            "name": "Blank Scanned Image PDF (Fail-Closed Rejection)",
            "filename": "blank_scanned_page.pdf",
            "generator": lambda: make_blank_image_pdf(),
            "expected_decision": "error_422",
            "expected_status": 422,
            "description": "Scanned PDF page with white pixels and no readable text."
        },
        {
            "id": "TC-PDF-06",
            "name": "Multi-page PDF with Threat on Page 2",
            "filename": "multipage_threat_page2.pdf",
            "generator": lambda: make_text_pdf([
                "Company Overview - Zenith Engineering Works\n"
                "Supplying precision fasteners and fittings in Coimbatore since 2005.\n"
                "GST registered and ISO certified supplier.",
                "Confidential Communication\n"
                "We know where your warehouse is. Think carefully before you file that complaint.\n"
                "Cancel the complaint or your family will pay for it."
            ]),
            "expected_decision": "reject",
            "expected_status": 200,
            "description": "Page 1 is completely clean, Page 2 carries an explicit violent threat."
        },
        {
            "id": "TC-PDF-07",
            "name": "Corrupted PDF File Header (Error Handling)",
            "filename": "corrupted_document.pdf",
            "generator": lambda: b"%PDF-1.4\ncorrupted stream binary junk 0x00 0xFF\ntrailer\n%%EOF",
            "expected_decision": "error_422",
            "expected_status": 422,
            "description": "Malformed PDF byte stream testing fail-safe error handling."
        },
        {
            "id": "TC-PDF-08",
            "name": "Native Text with Off-Platform Payment (Payment Rule Violation)",
            "filename": "off_platform_payment.pdf",
            "generator": lambda: make_text_pdf([
                "Wholesale Electronic Components Catalog\n"
                "Supplying resistors, capacitors, and microcontrollers in Delhi.\n"
                "Special deal: please pay via crypto or gift card for extra 15% discount."
            ]),
            "expected_decision": "reject",
            "expected_status": 200,
            "description": "Triggers payment.off_platform rule and model reject (v4 single-boundary policy)."
        },
        {
            "id": "TC-MEDIA-01",
            "name": "Standalone PNG Image with Clean Business Text (OCR Endpoint)",
            "filename": "product_spec_sheet.png",
            "endpoint": "/v1/moderate/media",
            "generator": lambda: _make_test_image([
                "Cotton bedsheets in king size.",
                "Wholesale bulk supplier in Surat Gujarat."
            ]),
            "expected_decision": "allow",
            "expected_status": 200,
            "description": "Image attachment uploaded to /v1/moderate/media, OCR text read cleanly."
        },
        {
            "id": "TC-MEDIA-02",
            "name": "MP4 Video Attachment (Allowed Unchecked Policy)",
            "filename": "factory_tour.mp4",
            "endpoint": "/v1/moderate/media",
            "generator": lambda: b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41" + b"\x00" * 300,
            "expected_decision": "allow",
            "expected_status": 200,
            "description": "Video attachment verified under media.allow_unchecked_video policy."
        }
    ]

    results = []

    print("\n" + "=" * 80)
    print("RUNNING PDF & OCR TEST EXECUTION MATRIX")
    print("=" * 80)

    for tc in test_cases:
        print(f"\n---> Executing {tc['id']}: {tc['name']}")
        pdf_bytes = tc["generator"]()
        file_path = OUT_DIR / tc["filename"]
        file_path.write_bytes(pdf_bytes)
        print(f"     Saved artifact to: {file_path} ({len(pdf_bytes)} bytes)")

        t0 = time.perf_counter()
        endpoint = tc.get("endpoint", "/v1/moderate/pdf")
        resp = client.post(endpoint,
                           data={"file": (io.BytesIO(pdf_bytes), tc["filename"]), "content_type": "article"},
                           content_type="multipart/form-data")
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)

        status_code = resp.status_code
        data = resp.get_json(silent=True) or {}
        decision = data.get("decision")
        if status_code != 200:
            err = data.get("error", {})
            decision = f"error_{status_code} ({err.get('code')})"

        # Verification
        passed = False
        if tc["expected_decision"].startswith("error_"):
            passed = status_code == tc["expected_status"]
        else:
            passed = (status_code == tc["expected_status"]) and (decision == tc["expected_decision"])

        pdf_info = data.get("pdf", {}) or {}
        media_info = data.get("media", {}) or {}
        ocr_confidence = media_info.get("ocr_confidence") or pdf_info.get("ocr_confidence")
        pages_count = pdf_info.get("pages")
        fb = data.get("feedback") or {}
        issues_count = len(fb.get("issues", []))

        res_record = {
            "id": tc["id"],
            "name": tc["name"],
            "status_code": status_code,
            "decision": decision,
            "expected": tc["expected_decision"],
            "passed": passed,
            "latency_ms": latency_ms,
            "pages": pages_count,
            "ocr_conf": ocr_confidence,
            "issues": issues_count,
            "response": data
        }
        results.append(res_record)

        print(f"     Status: {status_code} | Decision: {decision} | Expected: {tc['expected_decision']}")
        print(f"     Latency: {latency_ms} ms | Pages: {pages_count} | Issues: {issues_count} | Verdict: {'PASS' if passed else 'FAIL'}")
        if data.get("feedback"):
            print(f"     Feedback: {data['feedback'].get('title')} - {data['feedback'].get('message')}")
        elif data.get("error"):
            print(f"     Error Code: {data['error'].get('code')} - {data['error'].get('message')}")

    # Summary table
    print("\n" + "=" * 90)
    print(f"{'ID':<12} | {'Test Scenario':<32} | {'Status':<6} | {'Decision':<14} | {'Latency':<8} | {'Result'}")
    print("-" * 90)
    for r in results:
        verdict = "PASS [OK]" if r["passed"] else "FAIL [X]"
        print(f"{r['id']:<12} | {r['name']:<32} | {r['status_code']:<6} | {str(r['decision']):<14} | {r['latency_ms']:<6} ms | {verdict}")
    print("=" * 90)

    all_passed = all(r["passed"] for r in results)
    print(f"\nTOTAL TESTS: {len(results)} | PASSED: {sum(1 for r in results if r['passed'])} | FAILED: {sum(1 for r in results if not r['passed'])}")
    print(f"OVERALL RESULT: {'SUITE PASSED' if all_passed else 'SUITE FAILED'}\n")
    return results


if __name__ == "__main__":
    run_suite()
