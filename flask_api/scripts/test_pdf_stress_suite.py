"""Intense PDF and OCR Stress Test Suite.

Generates 120 diverse test PDFs across:
  - 50 Policy-violating PDFs (off-platform evasion, wire scam, phishing, counterfeit, defamation, extortion)
  - 50 Legitimate business PDFs (RFQs, invoices, spec sheets, logistics, Indian English B2B negotiation)
  - 20 Mixed & edge-case PDFs (hard-negative idioms, multi-page threats on later pages, blank/corrupted)

Evaluates native vector text extraction and RapidOCR raster scanning against the live Flask API endpoints.
Computes precision, recall, F1, accuracy, latency percentiles, and writes a detailed JSON report.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RESULT_SINK"] = "log"

from PIL import Image, ImageDraw, ImageFont
import pypdf
from app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pdf_stress")

OUT_DIR = ROOT / "scripts" / "test_stress_pdfs"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- PDF Generation Helpers

def _escape(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def make_native_pdf(pages: list[str]) -> bytes:
    """Standard multi-page text PDF with genuine PDF text streams (pypdf-compatible)."""
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


def make_scanned_pdf(pages_lines: list[list[str]], font_size: int = 30, width: int = 1240, height: int = 750) -> bytes:
    """Render text as image pixels inside an image-only PDF page (triggers RapidOCR)."""
    images = []
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    for lines in pages_lines:
        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)
        for i, line in enumerate(lines):
            draw.text((70, 70 + i * 65), line, fill=(20, 20, 20), font=font)
        images.append(img)

    buf = io.BytesIO()
    if len(images) == 1:
        images[0].save(buf, format="PDF", resolution=150.0)
    else:
        images[0].save(buf, format="PDF", save_all=True, append_images=images[1:], resolution=150.0)
    return buf.getvalue()


def make_blank_image_pdf(width: int = 1240, height: int = 750) -> bytes:
    """Blank scanned image PDF with zero text."""
    img = Image.new("RGB", (width, height), "white")
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=150.0)
    return buf.getvalue()


# --------------------------------------------------------------------------- Dataset Definition

def build_test_cases() -> list[dict]:
    cases = []

    # ----------------------------------------------------------------------- 1. Policy Violations (50 PDFs)
    # Off-platform communication evasion (10)
    for i in range(1, 11):
        is_scanned = (i % 2 == 0)
        lines = [
            f"URGENT TRADE SETTLEMENT NOTICE #{1000 + i}",
            "To avoid high marketplace transaction fees, do not checkout on this portal.",
            f"Contact our direct export manager on Telegram @factory_direct_{i} or WhatsApp +91-9876543{i:03d}.",
            "We will issue an off-platform cash invoice with 15% flat direct discount.",
            "All guarantees valid only when paid directly to our private personal account."
        ]
        cases.append({
            "id": f"OFF-PLAT-{i:02d}",
            "category": "off_platform",
            "ground_truth": "reject",
            "is_scanned": is_scanned,
            "pages_data": [lines] if is_scanned else ["\n".join(lines)],
            "desc": f"Off-platform contact evasion attempt #{i}"
        })

    # Wire scam & advance fee fraud (10)
    for i in range(1, 11):
        is_scanned = (i % 2 == 0)
        lines = [
            f"CONFIDENTIAL WIRE TRANSFER INSTRUCTIONS #{2000 + i}",
            "Our primary corporate bank account is temporarily undergoing an audit.",
            f"Please remit the mandatory 20% advance security deposit of ${5000 * i} immediately.",
            f"Wire directly to offshore escrow account holder: FAST-SETTLE-HOLDINGS-LLC #{887766 + i}.",
            "Once wire receipt is sent to our offshore email, container dispatch will be approved within 24 hours."
        ]
        cases.append({
            "id": f"SCAM-WIRE-{i:02d}",
            "category": "financial_fraud",
            "ground_truth": "reject",
            "is_scanned": is_scanned,
            "pages_data": [lines] if is_scanned else ["\n".join(lines)],
            "desc": f"Advance-fee wire diversion scam #{i}"
        })

    # Phishing & account takeover (10)
    for i in range(1, 11):
        is_scanned = (i % 2 == 0)
        lines = [
            f"SECURITY ALERT: MERCHANT ACCOUNT SUSPENSION WARNING #{3000 + i}",
            "We detected unauthorized login anomalies on your seller administration profile.",
            "Your merchant storefront will be permanently deactivated within 6 hours unless verified.",
            f"Navigate immediately to http://secure-seller-kyc-verify-portal-{i}.net/login to confirm your master credentials.",
            "Failure to authenticate will forfeit all pending disbursements and freeze inventory."
        ]
        cases.append({
            "id": f"PHISH-SEC-{i:02d}",
            "category": "phishing",
            "ground_truth": "reject",
            "is_scanned": is_scanned,
            "pages_data": [lines] if is_scanned else ["\n".join(lines)],
            "desc": f"Phishing seller account suspension #{i}"
        })

    # Counterfeits & pirated goods (10)
    for i in range(1, 11):
        is_scanned = (i % 2 == 0)
        lines = [
            f"WHOLESALE LUXURY REPLICA INVENTORY LOT #{4000 + i}",
            "Premium grade 1:1 mirror replica designer chronographs and luxury leather accessories.",
            "Exact duplicate serial stampings, clone movement, cloned warranty card and retail box included.",
            f"Indistinguishable from authentic store items at 95% below MSRP. MOQ {10 * i} units.",
            "Fast discrete shipping with zero customs brand inspection risk."
        ]
        cases.append({
            "id": f"COUNTERFEIT-{i:02d}",
            "category": "counterfeit",
            "ground_truth": "reject",
            "is_scanned": is_scanned,
            "pages_data": [lines] if is_scanned else ["\n".join(lines)],
            "desc": f"Replica counterfeit goods catalogue #{i}"
        })

    # Commercial defamation & extortion threats (10)
    for i in range(1, 11):
        is_scanned = (i % 2 == 0)
        lines = [
            f"FORMAL NOTICE OF PUBLIC EXPOSURE & BLACKLISTING #{5000 + i}",
            "We have prepared a public exposé regarding your alleged fraudulent business dealings.",
            "Unless you transfer a settlement of $15,000 to our wallet before Friday 5 PM,",
            "we will distribute this dossier to all your major clients, industry forums, and local tax authorities.",
            "Govern yourself accordingly; this is your final opportunity to prevent brand destruction."
        ]
        cases.append({
            "id": f"EXTORT-DEF-{i:02d}",
            "category": "extortion",
            "ground_truth": "reject",
            "is_scanned": is_scanned,
            "pages_data": [lines] if is_scanned else ["\n".join(lines)],
            "desc": f"Commercial extortion blackmail letter #{i}"
        })

    # ----------------------------------------------------------------------- 2. Clean Business B2B (50 PDFs)
    # RFQs & Quotations (10)
    rfq_items = [
        ("Seamless Stainless Steel Pipes Grade 316L", "ASTM A312", "Outer Diameter 50mm, Wall Thickness 3.5mm", "USD 3,450 / MT"),
        ("Three-Phase Oil Immersed Distribution Transformer", "IS 1180", "Rating 500 kVA, 11kV/433V, Copper Wound", "USD 8,200 / Unit"),
        ("Solar Grid-Tie Inverters with MPPT", "IEC 62109", "Capacity 50 kW, Efficiency 98.7%, IP65", "USD 2,150 / Unit"),
        ("Industrial Cast Steel Flanged Ball Valves", "API 6D", "Class 150, Size 4 Inch, PTFE Seal", "USD 280 / Piece"),
        ("Corrugated Shipping Cartons 5-Ply", "IS 2771", "GSM 180/150, Bursting Strength 14 kg/cm2", "USD 0.85 / Box"),
        ("Spunbond Non-Woven Polypropylene Fabric", "ISO 9073", "GSM 70, Roll Width 1.6m, White", "USD 1.65 / kg"),
        ("High Tensile Structural Bolts Grade 8.8", "DIN 931", "M16 x 80mm Full Thread with Hex Nuts", "USD 0.45 / Set"),
        ("Hydraulic Gear Pump Assembly", "ISO 4409", "Displacement 32 cc/rev, Max Pressure 250 Bar", "USD 165 / Unit"),
        ("Submersible Borewell Pump Set", "IS 8034", "Power 7.5 HP, 10 Stage, 100mm Bore", "USD 320 / Set"),
        ("Industrial LED High Bay Lighting Fixture", "IEC 60598", "Power 150W, 140 lm/W, CCT 6500K", "USD 48 / Unit")
    ]
    for i, (prod, std, spec, price) in enumerate(rfq_items, start=1):
        is_scanned = (i % 2 == 0)
        lines = [
            f"REQUEST FOR FORMAL COMMERCIAL QUOTATION: RFQ-{202600 + i}",
            f"Product Description: {prod}",
            f"Applicable Industry Standard: {std}",
            f"Technical Specification: {spec}",
            f"Target Price: {price} | Minimum Quantity: {100 * i} units",
            "Payment Terms: 100% Irrevocable Letter of Credit (LC) at Sight.",
            "Delivery Terms: CIF Nhava Sheva / CIF Mundra Port Incoterms 2020.",
            "Please revert with complete technical datasheet and certificate of origin."
        ]
        cases.append({
            "id": f"BIZ-RFQ-{i:02d}",
            "category": "b2b_negotiation",
            "ground_truth": "allow",
            "is_scanned": is_scanned,
            "pages_data": [lines] if is_scanned else ["\n".join(lines)],
            "desc": f"B2B RFQ for {prod}"
        })

    # Commercial Invoices & Purchase Orders (10)
    for i in range(1, 11):
        is_scanned = (i % 2 == 0)
        lines = [
            f"TAX INVOICE & DISPATCH MEMORANDUM #{88900 + i}",
            f"Seller: Apex Industrial Fabrications Pvt Ltd | GSTIN: 27AABCU9603R1ZM",
            f"Buyer: Metro Heavy Engineering Works Ltd | PO Number: PO-2026-ENG-{500 + i}",
            f"HSN Code: 73041900 | Despatch Document No: RR-{9980 + i}",
            f"Subtotal: INR {125000 * i:,.2f} | CGST (9%): INR {11250 * i:,.2f} | SGST (9%): INR {11250 * i:,.2f}",
            f"Total Invoice Value: INR {147500 * i:,.2f}",
            "Terms: Payment within 30 days of accepted Delivery Challan.",
            "Certified that the particulars given above are true and correct."
        ]
        cases.append({
            "id": f"BIZ-INV-{i:02d}",
            "category": "procurement_tender",
            "ground_truth": "allow",
            "is_scanned": is_scanned,
            "pages_data": [lines] if is_scanned else ["\n".join(lines)],
            "desc": f"Commercial GST tax invoice #{i}"
        })

    # Material Test Reports & Technical Spec Sheets (10)
    for i in range(1, 11):
        is_scanned = (i % 2 == 0)
        lines = [
            f"MILL TEST CERTIFICATE (EN 10204 3.1) - HEAT NO: HT-2026-{400 + i}",
            "Material Grade: Carbon Steel Seamless Line Pipe ASTM A106 Grade B",
            f"Chemical Analysis: C: 0.18%, Mn: 0.85%, P: 0.015%, S: 0.008%, Si: 0.22%",
            f"Mechanical Tests: Yield Strength: 295 MPa | Tensile Strength: 460 MPa | Elongation: 32%",
            "Hydrostatic Pressure Test: 18.5 MPa held for 15 seconds - Satisfactory No Leakage",
            "Non-Destructive Ultrasonic Testing: 100% Full Body Examination - Passed Class 1",
            "Inspected and Approved by Quality Assurance Directorate."
        ]
        cases.append({
            "id": f"BIZ-MTR-{i:02d}",
            "category": "b2b_qa_specs",
            "ground_truth": "allow",
            "is_scanned": is_scanned,
            "pages_data": [lines] if is_scanned else ["\n".join(lines)],
            "desc": f"Mill test report MTR #{i}"
        })

    # Logistics & Shipping Documents (10)
    for i in range(1, 11):
        is_scanned = (i % 2 == 0)
        lines = [
            f"BILL OF LADING FOR MULTIMODAL TRANSPORT BL-EXP-{7700 + i}",
            f"Vessel / Voyage: MV PACIFIC CARRIER V.260{i} | Port of Loading: Shanghai Port",
            "Port of Discharge: Jawaharlal Nehru Port (Nhava Sheva), India",
            f"Container Number: MSKU{882340 + i} / Seal No: ML-IN-{5540 + i}",
            f"Commodity: {20 * i} Pallets of Corrugated Packaging Raw Materials",
            f"Gross Weight: {18500 + i * 200} KGS | Net Weight: {17900 + i * 200} KGS",
            "Freight Status: FREIGHT PREPAID AS PER CHARTER PARTY AGREEMENT",
            "Three (3) Original Bills of Lading signed, one being accomplished others stand void."
        ]
        cases.append({
            "id": f"BIZ-LOG-{i:02d}",
            "category": "b2b_logistics_docs",
            "ground_truth": "allow",
            "is_scanned": is_scanned,
            "pages_data": [lines] if is_scanned else ["\n".join(lines)],
            "desc": f"Logistics bill of lading #{i}"
        })

    # Non-native / Indian English B2B Negotiation (10)
    for i in range(1, 11):
        is_scanned = (i % 2 == 0)
        lines = [
            f"COMMERCIAL INQUIRY REGARDING BULK SUPPLY SCHEDULE #{6600 + i}",
            "Dear Sir, With reference to your previous quotation, we are having requirement of 200 units.",
            "Kindly revert back at your earliest convenience with best bulk discount rate.",
            "We are knowing your esteemed company since many years and doing regular procurement.",
            "Please discuss about extending payment terms to 60 days credit against post-dated cheque.",
            "All machineries and equipments supplied in last order are working satisfactory.",
            "Kindly do the needful and confirm the dispatch schedule."
        ]
        cases.append({
            "id": f"BIZ-LANG-{i:02d}",
            "category": "grammar_business_prose",
            "ground_truth": "allow",
            "is_scanned": is_scanned,
            "pages_data": [lines] if is_scanned else ["\n".join(lines)],
            "desc": f"Indian English trade negotiation #{i}"
        })

    # ----------------------------------------------------------------------- 3. Mixed, Multi-Page & Edge Cases (20 PDFs)
    # Hard-Negative pricing idioms (10)
    idiom_texts = [
        "Factory clearance sale! Dirt cheap bulk price on surplus prime alloy steel rods.",
        "We are ready to kill the competition with aggressive volume discounts on heavy flanges.",
        "Our warehouse blowout offers a steal of a deal on remaining hydraulic fittings inventory.",
        "Experiencing explosive market demand for high voltage capacitors; reserving wholesale quota now.",
        "This high torque CNC lathe is the ultimate weapon of choice for precision aerospace tooling.",
        "Unbeatable rock bottom wholesale rates on industrial safety boots and protective helmets.",
        "Slashing distributor margins to provide aggressive factory floor pricing on induction motors.",
        "Surplus stock liquidation at dead cheap rates to clear warehouse capacity before fiscal year end.",
        "Take immediate advantage of this killer quotation for full container load packaging film.",
        "Providing cut-throat pricing on electrical switchgear panels for infrastructure tenders."
    ]
    for i, idiom in enumerate(idiom_texts, start=1):
        lines = [
            f"SPECIAL COMMERCIAL CLEARANCE BULLETIN #{9100 + i}",
            idiom,
            "All materials are 100% prime brand new quality with complete manufacturer test warranty.",
            "Standard trade escrow and credit terms available through authorized portal checkout.",
            "Contact our verified commercial sales desk for formal contract drafting."
        ]
        cases.append({
            "id": f"MIX-IDIOM-{i:02d}",
            "category": "hard_negative_pricing_idiom",
            "ground_truth": "allow",
            "is_scanned": (i % 2 == 0),
            "pages_data": [lines] if (i % 2 == 0) else ["\n".join(lines)],
            "desc": f"Hard negative business idiom #{i}"
        })

    # Multi-page mixed documents (Page 1 Clean, Page 2 Threat) (5)
    for i in range(1, 6):
        page1 = [
            f"PRECISION INDUSTRIAL VALVES CATALOGUE 2026 - PAGE 1",
            "Leading manufacturer of high pressure gate, globe, and check valves.",
            "Manufacturing facilities certified to ISO 9001:2015 and API 6D specifications.",
            "All valves undergo hydrostatic shell test and seat leakage inspection before dispatch."
        ]
        page2 = [
            f"IMPORTANT PAYMENT SETTLEMENT ADVISORY - PAGE 2",
            "ATTENTION: Do not make payment through the authorized marketplace checkout gateway.",
            f"Send 30% advance deposit to offshore account HOLDING-LLC #{443322 + i} to avoid 18% GST.",
            f"Direct WhatsApp contact for wire confirmation: +91-99887766{i:02d}."
        ]
        cases.append({
            "id": f"MIX-MULTI-THREAT-{i:02d}",
            "category": "multipage_infiltrated",
            "ground_truth": "reject",
            "is_scanned": (i % 2 == 0),
            "pages_data": [page1, page2] if (i % 2 == 0) else ["\n".join(page1), "\n".join(page2)],
            "desc": f"Multi-page brochure with off-platform scam on Page 2 #{i}"
        })

    # Format Edge Cases (5)
    # Blank scan
    cases.append({
        "id": "EDGE-BLANK-01",
        "category": "format_edge",
        "ground_truth": "error_422",
        "is_scanned": True,
        "is_blank": True,
        "desc": "Blank scanned raster image page (Fail-closed test)"
    })
    # Corrupted stream
    cases.append({
        "id": "EDGE-CORRUPT-02",
        "category": "format_edge",
        "ground_truth": "error_422",
        "is_corrupted": True,
        "desc": "Damaged corrupted PDF stream (Fail-closed test)"
    })
    # Very sparse clean text
    cases.append({
        "id": "EDGE-SPARSE-03",
        "category": "format_edge",
        "ground_truth": "allow",
        "is_scanned": False,
        "pages_data": ["Standard Quotation Reference: SQ-9901. Valid for 30 days."],
        "desc": "Sparse clean quotation text"
    })
    # Multi-page clean (3 pages)
    p1 = "COMPANY PROFILE: GLOBAL FABRICATIONS LTD\nSpecializing in EPC infrastructure contracts."
    p2 = "CORE COMPETENCIES: Heavy structural steel, pressure vessels, and cryogenic tanks."
    p3 = "COMPLIANCE ACCREDITATIONS: ASME Section VIII Div 1, ISO 14001, OHSAS 18001."
    cases.append({
        "id": "EDGE-CLEAN-MULTI-04",
        "category": "format_edge",
        "ground_truth": "allow",
        "is_scanned": False,
        "pages_data": [p1, p2, p3],
        "desc": "3-page clean corporate credentials dossier"
    })
    # Scanned multi-page clean (2 pages)
    sp1 = ["DELIVERY CHALLAN & DISPATCH NOTE DC-4401", "Carrier: Blue Dart Express | Transporter ID: TR-8890", "Total packages: 15 wooden crates containing machined shafts."]
    sp2 = ["RECEIVING STORE ACKNOWLEDGEMENT", "Goods received in apparent good order and condition without external damage.", "Inspected and accepted by Store Superintendent."]
    cases.append({
        "id": "EDGE-SCAN-MULTI-05",
        "category": "format_edge",
        "ground_truth": "allow",
        "is_scanned": True,
        "pages_data": [sp1, sp2],
        "desc": "2-page scanned delivery challan and receipt"
    })

    return cases


# --------------------------------------------------------------------------- Runner Execution

def run_stress_suite():
    print("=" * 80)
    print("STARTING INTENSE PDF MODERATION STRESS TEST (120 TEST CASES)")
    print("=" * 80)

    app = create_app()
    client = app.test_client()

    ready = client.get("/ready")
    if ready.status_code != 200:
        raise RuntimeError(f"Service not ready: {ready.status_code}")

    test_cases = build_test_cases()
    print(f"Loaded {len(test_cases)} structured test cases.")

    results = []
    t_start_total = time.perf_counter()

    for idx, tc in enumerate(test_cases, start=1):
        tc_id = tc["id"]
        tc_name = tc["desc"]
        gt = tc["ground_truth"]
        is_scanned = tc.get("is_scanned", False)

        # Generate PDF bytes
        if tc.get("is_corrupted"):
            pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\nCORRUPTED_STREAM_GARBAGE_BYTES\n%%EOF"
            filename = f"{tc_id}.pdf"
        elif tc.get("is_blank"):
            pdf_bytes = make_blank_image_pdf()
            filename = f"{tc_id}.pdf"
        elif is_scanned:
            pdf_bytes = make_scanned_pdf(tc["pages_data"])
            filename = f"{tc_id}.pdf"
        else:
            pdf_bytes = make_native_pdf(tc["pages_data"])
            filename = f"{tc_id}.pdf"

        # Save to disk
        out_file = OUT_DIR / filename
        out_file.write_bytes(pdf_bytes)

        # Execute request
        t0 = time.perf_counter()
        resp = client.post(
            "/v1/moderate/pdf",
            data={"file": (io.BytesIO(pdf_bytes), filename), "content_type": "article"},
            content_type="multipart/form-data"
        )
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)

        status_code = resp.status_code
        data = resp.get_json(silent=True) or {}
        decision = data.get("decision")
        if status_code != 200:
            err_code = data.get("error", {}).get("code")
            decision = f"error_{status_code}"

        # Evaluate correctness
        passed = False
        if gt == "error_422":
            passed = (status_code == 422)
        elif gt == "reject":
            passed = (status_code == 200 and decision == "reject")
        elif gt == "allow":
            passed = (status_code == 200 and decision == "allow")

        pdf_meta = data.get("pdf") or {}
        feedback = data.get("feedback") or {}
        issues_list = feedback.get("issues") or []
        triage = data.get("triage") or {}
        triage_path = triage.get("path") or ""
        model_data = data.get("model") or {}
        risk_score = model_data.get("risk_score") if model_data.get("risk_score") is not None else data.get("risk_score")

        res_record = {
            "id": tc_id,
            "category": tc["category"],
            "type": "scanned" if is_scanned else "native",
            "ground_truth": gt,
            "decision": decision,
            "status_code": status_code,
            "passed": passed,
            "latency_ms": latency_ms,
            "pages": pdf_meta.get("pages", 1),
            "ocr_confidence": pdf_meta.get("ocr_confidence"),
            "issues": len(issues_list),
            "gate_triggered": "gate" in triage_path,
            "model_risk": risk_score
        }
        results.append(res_record)

        if idx % 10 == 0 or not passed:
            print(f"[{idx:03d}/120] {tc_id:<18} | GT: {gt:<9} | Got: {decision:<9} | {latency_ms:>6.1f} ms | {'PASS' if passed else 'FAIL'}")

    total_time = round(time.perf_counter() - t_start_total, 2)

    # ----------------------------------------------------------------------- Statistical Analysis
    total_count = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    failed_count = total_count - passed_count

    # Binary Classification Metrics (excluding format error tests for F1)
    eval_set = [r for r in results if r["ground_truth"] in ("allow", "reject")]
    tp = sum(1 for r in eval_set if r["ground_truth"] == "reject" and r["decision"] == "reject")
    tn = sum(1 for r in eval_set if r["ground_truth"] == "allow" and r["decision"] == "allow")
    fp = sum(1 for r in eval_set if r["ground_truth"] == "allow" and r["decision"] == "reject")
    fn = sum(1 for r in eval_set if r["ground_truth"] == "reject" and r["decision"] == "allow")

    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else 1.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else 1.0
    f1 = round(2 * (precision * recall) / (precision + recall), 4) if (precision + recall) > 0 else 0.0
    acc = round((tp + tn) / len(eval_set), 4) if eval_set else 1.0

    # Latencies
    native_latencies = [r["latency_ms"] for r in results if r["type"] == "native" and r["status_code"] == 200]
    scanned_latencies = [r["latency_ms"] for r in results if r["type"] == "scanned" and r["status_code"] == 200]

    def percentiles(vals):
        if not vals:
            return {"p50": 0, "p95": 0, "p99": 0, "mean": 0}
        s = sorted(vals)
        n = len(s)
        return {
            "p50": round(s[int(n * 0.50)], 1),
            "p95": round(s[min(int(n * 0.95), n - 1)], 1),
            "p99": round(s[min(int(n * 0.99), n - 1)], 1),
            "mean": round(sum(s) / n, 1),
            "min": round(s[0], 1),
            "max": round(s[-1], 1)
        }

    native_stats = percentiles(native_latencies)
    scanned_stats = percentiles(scanned_latencies)

    # Save complete JSON report
    report_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_tests": total_count,
        "passed": passed_count,
        "failed": failed_count,
        "accuracy": acc,
        "precision": precision,
        "recall": recall,
        "f1_score": f1,
        "confusion_matrix": {"TP": tp, "FP": fp, "TN": tn, "FN": fn},
        "latency_benchmarks": {
            "native_text": native_stats,
            "scanned_ocr": scanned_stats
        },
        "total_suite_duration_sec": total_time,
        "details": results
    }

    report_path = OUT_DIR / "stress_test_report.json"
    report_path.write_text(json.dumps(report_data, indent=2))
    print(f"\nWrote structured report to: {report_path}")

    # Output Summary Table
    print("\n" + "=" * 80)
    print("STRESS TEST EXECUTION SUMMARY")
    print("=" * 80)
    print(f"Total PDFs Evaluated: {total_count} (50 Offensive, 50 Business, 20 Mixed/Edge)")
    print(f"Overall Passed:       {passed_count} / {total_count} ({passed_count / total_count * 100:.1f}%)")
    print(f"Accuracy:             {acc * 100:.2f}%")
    print(f"Precision:            {precision * 100:.2f}%")
    print(f"Recall:               {recall * 100:.2f}%")
    print(f"F1 Score:             {f1:.4f}")
    print(f"Confusion Matrix:     TP={tp}, FP={fp}, TN={tn}, FN={fn}")
    print("-" * 80)
    print(f"Native Text Latency:  p50={native_stats['p50']}ms | p95={native_stats['p95']}ms | mean={native_stats['mean']}ms")
    print(f"Scanned OCR Latency:  p50={scanned_stats['p50']}ms | p95={scanned_stats['p95']}ms | mean={scanned_stats['mean']}ms")
    print(f"Total Duration:       {total_time} seconds")
    print("=" * 80)

    return report_data


if __name__ == "__main__":
    run_stress_suite()
