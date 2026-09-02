"""Build the evaluation corpus and its ground truth. Owner: person 2.

Thirty questions whose correct answer page is *known*, because the page is
recorded by the writer as it lays the fact down rather than assigned by a human
reading the output afterwards. That distinction is the whole value of this
file: an eval set with eyeballed ground truth measures how well retrieval
agrees with whoever built it.

The corpus is deliberately awkward in the ways a real one is:

* Four of the eight documents are **image-only scans** with no text layer, so
  the OCR path cannot be skipped. One of them is 20 pages, which is the
  document the ingest benchmark in the acceptance criteria is timed against.
* Facts hide in **table cells** as often as in prose, because that is where
  they live in refinery paperwork.
* Several questions are **exact identifier lookups** -- PSV-2103, CML-12,
  QTN/BHE/2024/0871 -- which dense retrieval is bad at and BM25 is good at.
  Without them the hybrid retriever would look like pointless complexity.

    python ragsvc/eval/make_corpus.py --out demo/documents

Everything here is synthetic. The equipment tags, readings and names are
invented to be plausible, not copied from any real plant. Replace this corpus
with real documents when they arrive: `questions.jsonl` is the only thing the
harness reads, and it does not care where the PDFs came from.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdfgen import PdfWriter, scan_effect  # noqa: E402

# --- questions --------------------------------------------------------------
# Every entry names the fact id the writer tags, so the page is filled in from
# what was actually rendered. `kind` drives the per-category breakdown the
# harness prints, which is how you find out *which* retrieval mode is weak.

QUESTIONS: list[dict] = [
    # V-1201 external inspection report (native text)
    {"id": "q01", "fact": "f01", "question": "What is the design pressure of vessel V-1201?",
     "answer": "10.5 barg", "kind": "table"},
    {"id": "q02", "fact": "f02", "question": "What corrosion allowance was specified for V-1201?",
     "answer": "3.0 mm", "kind": "table"},
    {"id": "q03", "fact": "f03", "question": "What is the minimum recorded shell thickness at CML-07 on V-1201?",
     "answer": "11.4 mm", "kind": "table"},
    {"id": "q04", "fact": "f04", "question": "When is the next internal inspection of V-1201 due?",
     "answer": "March 2028", "kind": "prose"},
    {"id": "q05", "fact": "f05", "question": "Which API standard governs the in-service inspection of V-1201?",
     "answer": "API 510", "kind": "prose"},
    {"id": "q06", "fact": "f06", "question": "What was the condition of the V-1201 insulation at the north nozzle?",
     "answer": "Damaged cladding with suspected CUI", "kind": "table"},

    # SOP-INSP-014 relief valve testing (20-page scan)
    {"id": "q07", "fact": "f07", "question": "What is the set pressure of PSV-2103?",
     "answer": "12.5 barg", "kind": "identifier"},
    {"id": "q08", "fact": "f08", "question": "What pop test tolerance applies to set pressures above 70 psig?",
     "answer": "plus or minus 3 percent", "kind": "scanned"},
    {"id": "q09", "fact": "f09", "question": "Which test medium is specified for relief valve bench testing?",
     "answer": "Dry nitrogen", "kind": "scanned"},
    {"id": "q10", "fact": "f10", "question": "What is the maximum interval between relief valve overhauls in clean service?",
     "answer": "60 months", "kind": "scanned"},
    {"id": "q11", "fact": "f11", "question": "Who authorises removal of a relief valve from service?",
     "answer": "Shift Manager, Operations", "kind": "scanned"},
    {"id": "q12", "fact": "f12", "question": "On which line is PSV-2103 installed?",
     "answer": "Debutaniser overhead line 6-P-2104", "kind": "identifier"},
    {"id": "q13", "fact": "f13", "question": "At what pressure is the seat tightness test carried out?",
     "answer": "90 percent of set pressure", "kind": "scanned"},
    {"id": "q14", "fact": "f14", "question": "How long must relief valve test certificates be retained?",
     "answer": "10 years", "kind": "scanned"},

    # Thickness survey (native, table-heavy)
    {"id": "q15", "fact": "f15", "question": "What thickness was measured at CML-12 on line 8-P-1104?",
     "answer": "7.8 mm", "kind": "identifier"},
    {"id": "q16", "fact": "f16", "question": "What is the calculated corrosion rate at CML-12?",
     "answer": "0.12 mm per year", "kind": "table"},
    {"id": "q17", "fact": "f17", "question": "Which condition monitoring location has the lowest remaining life?",
     "answer": "CML-19", "kind": "table"},
    {"id": "q18", "fact": "f18", "question": "What is the retirement thickness for line 8-P-1104?",
     "answer": "5.2 mm", "kind": "prose"},

    # P&ID line list (scanned)
    {"id": "q19", "fact": "f19", "question": "What is the design temperature of line 6-P-2104?",
     "answer": "185 degrees C", "kind": "scanned"},
    {"id": "q20", "fact": "f20", "question": "What material specification is used for line 6-P-2104?",
     "answer": "ASTM A106 Gr.B", "kind": "identifier"},
    {"id": "q21", "fact": "f21", "question": "Which drawing number shows the debutaniser overhead circuit?",
     "answer": "4102-PID-006", "kind": "identifier"},
    {"id": "q22", "fact": "f22", "question": "What insulation is specified for line 10-S-3301?",
     "answer": "Hot service, calcium silicate", "kind": "scanned"},

    # Vendor correspondence (scanned)
    {"id": "q23", "fact": "f23", "question": "What delivery lead time did the vendor quote for the replacement bellows?",
     "answer": "14 weeks from order", "kind": "scanned"},
    {"id": "q24", "fact": "f24", "question": "What is the vendor's quotation reference number?",
     "answer": "QTN/BHE/2024/0871", "kind": "identifier"},
    {"id": "q25", "fact": "f25", "question": "What warranty period did the vendor offer?",
     "answer": "18 months from despatch", "kind": "scanned"},

    # Approval note (native)
    {"id": "q26", "fact": "f26", "question": "What expenditure was approved for the V-1201 nozzle repair?",
     "answer": "INR 42.6 lakh", "kind": "prose"},
    {"id": "q27", "fact": "f27", "question": "Who gave final approval for the V-1201 nozzle repair note?",
     "answer": "General Manager, Technical Services", "kind": "prose"},

    # Pump alignment SOP (native)
    {"id": "q28", "fact": "f28", "question": "What is the maximum permissible angular misalignment for a coupled pump?",
     "answer": "0.05 mm per 100 mm of coupling diameter", "kind": "table"},
    {"id": "q29", "fact": "f29", "question": "What is the vibration alarm limit for centrifugal pumps?",
     "answer": "7.1 mm/s RMS", "kind": "table"},

    # Incident summary (scanned)
    {"id": "q30", "fact": "f30", "question": "What was the immediate cause of the P-101A seal failure in August 2023?",
     "answer": "Mechanical seal face cracking after dry running", "kind": "scanned"},
]


LOREM_CLAUSES = [
    "All work shall be carried out under a valid work permit issued by the area "
    "authority. The permit shall remain displayed at the work site for the duration "
    "of the activity and shall be returned on completion.",
    "Personnel engaged in this activity shall have completed the department "
    "competency assessment within the preceding twenty four months. Records of "
    "assessment shall be available for audit.",
    "Any deviation from this procedure shall be recorded on the deviation register "
    "and shall be approved in writing by the Head of Inspection before work "
    "proceeds. Verbal approval is not acceptable.",
    "Calibration certificates for all test instruments shall be current and "
    "traceable to a national standard. An instrument whose calibration has lapsed "
    "shall be withdrawn from service immediately.",
    "On completion, the work site shall be restored, all temporary supports "
    "removed, and the area handed back to Operations against a signed clearance.",
    "Where the results of the test fall outside the acceptance criteria stated in "
    "this procedure, the item shall be quarantined and referred to the Inspection "
    "Engineer for disposition.",
]


# --- document builders ------------------------------------------------------


def build_vessel_inspection() -> tuple[str, PdfWriter]:
    writer = PdfWriter(
        title="External Inspection Report",
        doc_no="INS/2024/0117",
    )
    writer.add_title(
        "PRESSURE VESSEL EXTERNAL INSPECTION REPORT",
        "Crude Distillation Unit - Overhead Accumulator",
    )
    writer.add_key_values([
        ("Report No.", "INS/2024/0117"),
        ("Date of Report", "18 Apr 2024"),
        ("Equipment Tag", "V-1201"),
        ("Service", "Crude overhead accumulator"),
        ("Unit", "CDU-I"),
        ("Inspected By", "S. Rajagopal, Inspection Engineer"),
    ])

    writer.add_heading("1. Scope of Inspection")
    writer.add_paragraph(
        "This report covers the external in-service inspection of pressure vessel "
        "V-1201 carried out on 12 April 2024 during the unit run. The inspection "
        "comprised close visual examination of the shell, heads, nozzles, supports "
        "and attached piping, together with ultrasonic thickness measurement at the "
        "established condition monitoring locations."
    )
    writer.add_paragraph(
        "The inspection was performed in accordance with API 510, Pressure Vessel "
        "Inspection Code, and the departmental procedure SOP-INSP-002. No internal "
        "inspection was carried out, the vessel being in service throughout.",
        fact="f05",
    )

    writer.add_heading("2. Design and Construction Data")
    writer.add_table(
        ["Parameter", "Value", "Unit", "Source"],
        [
            ["Design pressure", "10.5", "barg", "Data sheet DS-V-1201 Rev.3"],
            ["Design temperature", "165", "deg C", "Data sheet DS-V-1201 Rev.3"],
            ["Operating pressure", "8.2", "barg", "Process data"],
            ["Shell material", "SA-516 Gr.70", "-", "Data sheet DS-V-1201 Rev.3"],
            ["Nominal shell thickness", "14.0", "mm", "Fabrication drawing"],
            ["Corrosion allowance", "3.0", "mm", "Data sheet DS-V-1201 Rev.3"],
            ["Joint efficiency", "1.00", "-", "Radiography 100 percent"],
            ["Year of commissioning", "2009", "-", "Equipment register"],
        ],
        row_facts={0: "f01", 5: "f02"},
        widths=[0.34, 0.18, 0.14, 0.34],
    )

    writer.add_heading("3. Thickness Measurement Results")
    writer.add_paragraph(
        "Ultrasonic thickness readings were taken at the twelve established "
        "condition monitoring locations using a calibrated digital thickness gauge. "
        "Readings are the minimum of five measurements taken within each location."
    )
    writer.add_table(
        ["CML", "Location", "Nominal (mm)", "Measured (mm)", "Loss (mm)", "Status"],
        [
            ["CML-01", "Shell course 1, north", "14.0", "13.2", "0.8", "Acceptable"],
            ["CML-02", "Shell course 1, south", "14.0", "13.4", "0.6", "Acceptable"],
            ["CML-03", "Shell course 2, north", "14.0", "12.9", "1.1", "Acceptable"],
            ["CML-04", "Shell course 2, south", "14.0", "13.1", "0.9", "Acceptable"],
            ["CML-05", "Top head, crown", "16.0", "15.2", "0.8", "Acceptable"],
            ["CML-06", "Bottom head, crown", "16.0", "14.7", "1.3", "Acceptable"],
            ["CML-07", "Shell course 3, weld seam", "14.0", "11.4", "2.6", "Monitor"],
            ["CML-08", "Inlet nozzle N1 neck", "12.0", "11.1", "0.9", "Acceptable"],
            ["CML-09", "Outlet nozzle N2 neck", "12.0", "11.3", "0.7", "Acceptable"],
            ["CML-10", "Drain nozzle N4 neck", "10.0", "9.2", "0.8", "Acceptable"],
            ["CML-11", "Manway neck", "14.0", "13.5", "0.5", "Acceptable"],
            ["CML-12", "Skirt to shell junction", "14.0", "13.0", "1.0", "Acceptable"],
        ],
        row_facts={6: "f03"},
        widths=[0.11, 0.31, 0.14, 0.15, 0.13, 0.16],
    )

    writer.add_heading("4. Visual Examination Findings")
    writer.add_table(
        ["S.No", "Observation", "Severity", "Recommended Action"],
        [
            ["1", "Insulation cladding damaged at north nozzle N1, suspected corrosion under insulation",
             "Major", "Strip insulation and inspect at next opportunity"],
            ["2", "Minor surface rust on skirt fireproofing", "Minor", "Touch up during next painting round"],
            ["3", "Support saddle grout intact, no cracking observed", "Satisfactory", "No action"],
            ["4", "Nameplate legible and securely attached", "Satisfactory", "No action"],
            ["5", "Localised thinning at CML-07 weld seam", "Major", "Increase inspection frequency to annual"],
        ],
        row_facts={0: "f06"},
        widths=[0.08, 0.44, 0.14, 0.34],
    )

    writer.add_heading("5. Conclusion and Next Inspection")
    writer.add_paragraph(
        "The vessel is fit for continued service at the current operating "
        "conditions. The remaining life governed by CML-07 is calculated as 9.2 "
        "years at the observed corrosion rate, giving a next internal inspection "
        "due date of March 2028. External inspection is to be repeated annually "
        "and the CML-07 location is to be added to the annual UT round.",
        fact="f04",
    )
    writer.add_signature_block(["Inspected By", "Reviewed By", "Approved By"])
    return "INS-2024-0117-vessel-inspection.pdf", writer


def build_relief_valve_sop() -> tuple[str, PdfWriter]:
    """The 20-page scanned SOP the ingest benchmark is timed against."""
    writer = PdfWriter(
        title="Relief Valve Testing and Certification",
        doc_no="SOP-INSP-014 Rev.4",
    )
    writer.add_title(
        "STANDARD OPERATING PROCEDURE",
        "SOP-INSP-014 Rev.4 - Testing and Certification of Pressure Relief Valves",
    )
    writer.add_key_values([
        ("Document No.", "SOP-INSP-014"),
        ("Revision", "4"),
        ("Effective Date", "01 Jan 2024"),
        ("Next Review", "01 Jan 2027"),
        ("Custodian", "Head - Inspection and Reliability"),
        ("Classification", "Internal"),
    ])

    writer.add_heading("1. Purpose")
    writer.add_paragraph(
        "This procedure defines the requirements for the removal, bench testing, "
        "overhaul, certification and reinstatement of pressure relief valves in "
        "process service. It applies to all spring loaded and pilot operated relief "
        "valves installed within the refinery battery limits."
    )

    writer.add_heading("2. Responsibilities")
    writer.add_paragraph(
        "Removal of a relief valve from service shall be authorised in writing by "
        "the Shift Manager, Operations, who shall confirm that the protected "
        "equipment is either depressurised or adequately protected by a second "
        "relieving device before the valve is removed.",
        fact="f11",
    )
    writer.add_paragraph(
        "The Inspection Engineer is responsible for the technical adequacy of the "
        "test, for review of the test record, and for issue of the certificate. The "
        "Workshop Supervisor is responsible for the physical conduct of the test."
    )

    writer.add_heading("3. Test Medium and Equipment")
    writer.add_paragraph(
        "Dry nitrogen shall be used as the test medium for all bench testing of "
        "relief valves in gas or vapour service. Air shall not be used where the "
        "valve has been in hydrocarbon service. Water may be used only for valves "
        "in liquid service and only with the written agreement of the Inspection "
        "Engineer.",
        fact="f09",
    )

    writer.add_heading("4. Acceptance Criteria")
    writer.add_table(
        ["Set Pressure Range", "Pop Test Tolerance", "Blowdown", "Seat Tightness"],
        [
            ["Up to 70 psig", "plus or minus 2 psi", "Max 7 percent", "API 527"],
            ["Above 70 psig", "plus or minus 3 percent", "Max 7 percent", "API 527"],
            ["Above 1000 psig", "plus or minus 3 percent", "Max 5 percent", "API 527"],
        ],
        row_facts={1: "f08"},
        widths=[0.28, 0.26, 0.20, 0.26],
    )
    writer.add_paragraph(
        "The seat tightness test shall be carried out at 90 percent of the set "
        "pressure, held for a minimum of one minute, with leakage measured in "
        "accordance with API 527. A valve that fails the tightness test shall be "
        "dismantled and the seat lapped before retest.",
        fact="f13",
    )

    writer.add_heading("5. Test Intervals")
    writer.add_table(
        ["Service Category", "Description", "Maximum Interval", "Basis"],
        [
            ["Clean", "Non fouling, non corrosive vapour", "60 months", "Risk assessment 2021"],
            ["Normal", "General hydrocarbon service", "36 months", "Risk assessment 2021"],
            ["Severe", "Fouling, coking or corrosive", "24 months", "Risk assessment 2021"],
            ["Critical", "Fired equipment protection", "12 months", "Statutory"],
        ],
        row_facts={0: "f10"},
        widths=[0.20, 0.40, 0.20, 0.20],
    )

    writer.add_heading("6. Valve Register - Debutaniser Section")
    writer.add_table(
        ["Tag No.", "Location", "Set Pressure", "Size", "Last Tested", "Next Due"],
        [
            ["PSV-2101", "Debutaniser reflux drum D-2101", "9.8 barg", "2 x 3", "12 Feb 2023", "12 Feb 2026"],
            ["PSV-2102", "Debutaniser reboiler E-2105 shell", "11.0 barg", "1.5 x 2.5", "03 Mar 2023", "03 Mar 2026"],
            ["PSV-2103", "Debutaniser overhead line 6-P-2104", "12.5 barg", "3 x 4", "22 May 2023", "22 May 2026"],
            ["PSV-2104", "Debutaniser bottoms pump P-2107 discharge", "18.0 barg", "1 x 2", "09 Jun 2023", "09 Jun 2025"],
            ["PSV-2105", "LPG treater vessel V-2110", "16.4 barg", "2 x 3", "28 Jul 2023", "28 Jul 2026"],
            ["PSV-2106", "Naphtha splitter overhead condenser", "7.2 barg", "3 x 4", "14 Sep 2023", "14 Sep 2026"],
        ],
        row_facts={2: "f07"},
        widths=[0.14, 0.34, 0.14, 0.12, 0.13, 0.13],
    )
    writer.add_paragraph(
        "PSV-2103 protects the debutaniser overhead line 6-P-2104 against blocked "
        "outlet on the overhead condenser. Its relieving capacity was reconfirmed "
        "against the 2022 revalidation study and found adequate.",
        fact="f12",
    )

    # Pad to twenty pages with the kind of clause-by-clause procedural text a
    # real SOP carries. The benchmark in the acceptance criteria is a 20-page
    # scan, so the corpus has to contain one.
    section = 7
    while writer.document.page_count < 20:
        writer.add_heading(f"{section}. Procedure Step {section - 6}")
        for index, clause in enumerate(LOREM_CLAUSES, start=1):
            writer.add_paragraph(f"{section}.{index}  {clause}")
        writer.add_table(
            ["Step", "Action", "Responsible", "Record"],
            [
                ["1", "Confirm isolation and depressurisation of the protected system",
                 "Operations", "Permit"],
                ["2", "Fit blind flange and tag the removed valve with its tag number",
                 "Maintenance", "Tag register"],
                ["3", "Transport valve to workshop in the vertical position",
                 "Maintenance", "Movement note"],
                ["4", "Record as-received condition photographically before dismantling",
                 "Workshop", "Test record"],
                ["5", "Carry out as-received pop test and record the lift pressure",
                 "Workshop", "Test record"],
                ["6", "Dismantle, clean and inspect all internal components",
                 "Workshop", "Inspection sheet"],
            ],
            widths=[0.08, 0.52, 0.20, 0.20],
        )
        section += 1

    writer.add_heading(f"{section}. Records")
    writer.add_paragraph(
        "The completed test record and the certificate issued against it shall be "
        "retained for a period of 10 years from the date of test. Records shall be "
        "held in the equipment history file and in the departmental document "
        "management system.",
        fact="f14",
    )
    writer.add_signature_block(["Prepared By", "Reviewed By", "Approved By"])
    return "SOP-INSP-014-relief-valve-testing.pdf", writer


def build_thickness_survey() -> tuple[str, PdfWriter]:
    writer = PdfWriter(title="Piping Thickness Survey", doc_no="TMS/2024/CDU/03")
    writer.add_title(
        "PIPING THICKNESS MONITORING SURVEY",
        "Crude Distillation Unit - Circuit CDU-P-11, Survey Round 2024",
    )
    writer.add_key_values([
        ("Survey No.", "TMS/2024/CDU/03"),
        ("Survey Date", "26 Feb 2024"),
        ("Circuit", "CDU-P-11"),
        ("Line", "8-P-1104"),
        ("Technician", "M. Fernandes, UT Level II"),
        ("Instrument", "DMS Go, Sl. 88213, cal. 04 Jan 2024"),
    ])

    writer.add_heading("1. Circuit Description")
    writer.add_paragraph(
        "Circuit CDU-P-11 comprises the 8 inch overhead transfer line 8-P-1104 from "
        "the atmospheric column overhead to the first stage condenser, including "
        "four elbows, one reducer and the associated branch connections. The circuit "
        "operates in wet hydrogen sulphide service and is subject to a documented "
        "corrosion mechanism of ammonium chloride under-deposit attack."
    )
    writer.add_paragraph(
        "The retirement thickness for line 8-P-1104 is 5.2 mm, derived from the "
        "pressure design thickness of 4.4 mm plus a structural minimum margin. "
        "Readings approaching this value require immediate referral to the "
        "Inspection Engineer.",
        fact="f18",
    )

    writer.add_heading("2. Measurement Results")
    writer.add_table(
        ["CML", "Component", "Nominal", "Previous", "Current", "Rate (mm/yr)", "Remaining Life"],
        [
            ["CML-01", "Straight run A", "8.18", "7.95", "7.91", "0.04", "> 20 yr"],
            ["CML-02", "Elbow E1 extrados", "8.18", "7.62", "7.50", "0.12", "19.2 yr"],
            ["CML-03", "Elbow E1 intrados", "8.18", "7.41", "7.26", "0.15", "13.7 yr"],
            ["CML-04", "Straight run B", "8.18", "7.88", "7.83", "0.05", "> 20 yr"],
            ["CML-05", "Tee T1 branch", "8.18", "7.55", "7.44", "0.11", "20.4 yr"],
            ["CML-06", "Tee T1 run", "8.18", "7.71", "7.64", "0.07", "> 20 yr"],
            ["CML-07", "Reducer R1 large end", "8.18", "7.33", "7.19", "0.14", "14.2 yr"],
            ["CML-08", "Reducer R1 small end", "6.35", "5.98", "5.90", "0.08", "8.8 yr"],
            ["CML-09", "Straight run C", "8.18", "7.90", "7.86", "0.04", "> 20 yr"],
            ["CML-10", "Elbow E2 extrados", "8.18", "7.68", "7.59", "0.09", "> 20 yr"],
            ["CML-11", "Elbow E2 intrados", "8.18", "7.29", "7.14", "0.15", "12.9 yr"],
            ["CML-12", "Straight run D", "8.18", "7.92", "7.80", "0.12", "21.7 yr"],
            ["CML-13", "Dead leg to drain", "8.18", "6.98", "6.79", "0.19", "8.3 yr"],
            ["CML-14", "Straight run E", "8.18", "7.87", "7.81", "0.06", "> 20 yr"],
            ["CML-15", "Elbow E3 extrados", "8.18", "7.55", "7.47", "0.08", "> 20 yr"],
            ["CML-16", "Elbow E3 intrados", "8.18", "7.18", "7.02", "0.16", "11.4 yr"],
            ["CML-17", "Straight run F", "8.18", "7.84", "7.79", "0.05", "> 20 yr"],
            ["CML-18", "Condenser inlet nozzle", "8.18", "7.44", "7.35", "0.09", "> 20 yr"],
            ["CML-19", "Low point drain boss", "6.35", "5.86", "5.62", "0.24", "1.8 yr"],
            ["CML-20", "Vent connection", "6.35", "6.02", "5.97", "0.05", "15.4 yr"],
        ],
        row_facts={11: "f15"},
        widths=[0.11, 0.27, 0.11, 0.12, 0.12, 0.14, 0.13],
    )

    writer.add_heading("3. Corrosion Rate Assessment")
    writer.add_table(
        ["CML", "Short Term Rate", "Long Term Rate", "Governing Rate", "Assessment"],
        [
            ["CML-12", "0.12 mm/yr", "0.09 mm/yr", "0.12 mm/yr", "Acceptable, continue monitoring"],
            ["CML-13", "0.19 mm/yr", "0.16 mm/yr", "0.19 mm/yr", "Elevated, dead leg to be removed"],
            ["CML-16", "0.16 mm/yr", "0.13 mm/yr", "0.16 mm/yr", "Acceptable, annual monitoring"],
            ["CML-19", "0.24 mm/yr", "0.21 mm/yr", "0.24 mm/yr", "Lowest remaining life in circuit"],
        ],
        row_facts={0: "f16", 3: "f17"},
        widths=[0.12, 0.19, 0.19, 0.19, 0.31],
    )
    writer.add_paragraph(
        "CML-19 at the low point drain boss shows the lowest remaining life in the "
        "circuit at 1.8 years and governs the next inspection interval for the "
        "whole of CDU-P-11. A repair or replacement plan is to be raised before the "
        "next turnaround."
    )
    writer.add_signature_block(["Surveyed By", "Reviewed By", "Approved By"])
    return "TMS-2024-CDU-03-thickness-survey.pdf", writer


def build_line_list() -> tuple[str, PdfWriter]:
    writer = PdfWriter(title="Line List and P&ID Index", doc_no="4102-LL-002 Rev.7")
    writer.add_title(
        "PIPING LINE LIST",
        "Unit 4102 - Debutaniser and Naphtha Splitter Section",
    )
    writer.add_key_values([
        ("Document No.", "4102-LL-002"),
        ("Revision", "7"),
        ("Issued", "09 Nov 2023"),
        ("Prepared By", "Design and Projects"),
    ])

    writer.add_heading("1. Drawing Index")
    writer.add_table(
        ["Drawing No.", "Title", "Revision", "Sheet"],
        [
            ["4102-PID-004", "Debutaniser feed and preheat", "5", "1 of 1"],
            ["4102-PID-005", "Debutaniser column and reboiler", "6", "1 of 2"],
            ["4102-PID-006", "Debutaniser overhead and reflux", "4", "1 of 1"],
            ["4102-PID-007", "Naphtha splitter column", "3", "1 of 2"],
            ["4102-PID-008", "LPG treating and storage", "2", "1 of 1"],
        ],
        row_facts={2: "f21"},
        widths=[0.22, 0.48, 0.15, 0.15],
    )

    writer.add_heading("2. Line Schedule")
    writer.add_table(
        ["Line No.", "From", "To", "Size", "Material", "Design T", "Design P", "Insulation"],
        [
            ["6-P-2104", "Debutaniser C-2101 overhead", "Condenser E-2103", "6 in",
             "ASTM A106 Gr.B", "185 C", "14.0 barg", "None"],
            ["4-P-2105", "Condenser E-2103", "Reflux drum D-2101", "4 in",
             "ASTM A106 Gr.B", "95 C", "14.0 barg", "None"],
            ["3-P-2106", "Reflux drum D-2101", "Reflux pump P-2105", "3 in",
             "ASTM A106 Gr.B", "95 C", "14.0 barg", "None"],
            ["8-P-2110", "Reboiler E-2105", "Debutaniser C-2101", "8 in",
             "ASTM A335 P11", "245 C", "16.0 barg", "Hot"],
            ["10-S-3301", "Steam header", "Reboiler E-2105", "10 in",
             "ASTM A106 Gr.B", "260 C", "18.0 barg", "Hot, calcium silicate"],
            ["2-P-2115", "LPG treater V-2110", "Storage sphere", "2 in",
             "ASTM A106 Gr.B", "60 C", "20.0 barg", "None"],
        ],
        row_facts={0: "f19", 4: "f22"},
        widths=[0.12, 0.19, 0.16, 0.07, 0.15, 0.10, 0.10, 0.11],
    )
    writer.add_paragraph(
        "Line 6-P-2104 is the debutaniser overhead vapour line, fabricated in ASTM "
        "A106 Gr.B seamless pipe to schedule 40, and is shown on drawing "
        "4102-PID-006. It carries the relief connection to PSV-2103.",
        fact="f20",
    )
    writer.add_heading("3. Notes")
    writer.add_paragraph(
        "Insulation marked Hot denotes heat conservation insulation. Calcium "
        "silicate is specified for surfaces above 200 degrees C. All insulated "
        "carbon steel lines operating between minus 12 and 175 degrees C are "
        "within the corrosion under insulation susceptibility range and are "
        "included in the CUI inspection programme."
    )
    return "4102-LL-002-line-list.pdf", writer


def build_vendor_letter() -> tuple[str, PdfWriter]:
    writer = PdfWriter(
        title="Vendor Correspondence",
        org="BHARAT HEAT EXCHANGERS LIMITED",
        unit="Sales and Contracts Division, Vadodara",
        doc_no="QTN/BHE/2024/0871",
    )
    writer.add_title("QUOTATION", "Replacement Expansion Bellows for Exchanger E-2103")
    writer.add_key_values([
        ("Quotation No.", "QTN/BHE/2024/0871"),
        ("Date", "07 Mar 2024"),
        ("Your Enquiry", "MRPL/PUR/2024/0455"),
        ("Validity", "90 days"),
        ("Attention", "Head - Materials"),
        ("Our Reference", "BHE/MKT/SR/2024"),
    ])

    writer.add_paragraph(
        "Dear Sir, with reference to your enquiry cited above, we are pleased to "
        "submit our offer for the supply of replacement expansion bellows for shell "
        "and tube exchanger E-2103. Our offer reference is QTN/BHE/2024/0871 and "
        "should be quoted in all further correspondence.",
        fact="f24",
    )

    writer.add_heading("1. Scope of Supply")
    writer.add_table(
        ["Item", "Description", "Qty", "Unit Rate (INR)", "Amount (INR)"],
        [
            ["1", "Expansion bellows, SS 321, 24 inch, 6 convolution", "2", "486,000", "972,000"],
            ["2", "Gasket set, spiral wound, SS 316 with graphite filler", "4", "12,500", "50,000"],
            ["3", "Fastener set, ASTM A193 B7 studs with 2H nuts", "1", "38,000", "38,000"],
            ["4", "Third party inspection and certification", "1", "45,000", "45,000"],
        ],
        widths=[0.08, 0.46, 0.09, 0.18, 0.19],
    )

    writer.add_heading("2. Commercial Terms")
    writer.add_table(
        ["Term", "Offered"],
        [
            ["Delivery", "14 weeks from date of firm order"],
            ["Payment", "30 percent advance, 70 percent against despatch documents"],
            ["Warranty", "18 months from date of despatch or 12 months from commissioning"],
            ["Price basis", "Ex works Vadodara, GST extra at applicable rate"],
            ["Packing", "Seaworthy, included in the quoted price"],
        ],
        row_facts={0: "f23", 2: "f25"},
        widths=[0.26, 0.74],
    )
    writer.add_paragraph(
        "The quoted delivery of 14 weeks from order is based on current shop "
        "loading and confirmed availability of SS 321 plate. Any delay in approval "
        "of manufacturing drawings will extend this period correspondingly."
    )
    writer.add_signature_block(["For Bharat Heat Exchangers Limited"])
    return "QTN-BHE-2024-0871-vendor-quotation.pdf", writer


def build_approval_note() -> tuple[str, PdfWriter]:
    writer = PdfWriter(title="Approval Note", doc_no="MRPL/I&R/APR/2023/0088")
    writer.add_title("APPROVAL NOTE", "Repair of Nozzle N1 on Vessel V-1201")
    writer.add_key_values([
        ("Ref. No.", "MRPL/I&R/APR/2023/0088"),
        ("Date", "22 Nov 2023"),
        ("Department", "Inspection and Reliability"),
        ("Category", "Capital repair"),
        ("Originated By", "S. Rajagopal, Inspection Engineer"),
        ("Priority", "High"),
    ])

    writer.add_heading("1. Background")
    writer.add_paragraph(
        "During the external inspection round of October 2023, corrosion under "
        "insulation was confirmed at the N1 inlet nozzle of vessel V-1201. Stripping "
        "of the insulation revealed general wall loss over an area of approximately "
        "180 mm by 220 mm on the nozzle neck, with a minimum remaining thickness of "
        "8.6 mm against a required minimum of 9.4 mm."
    )

    writer.add_heading("2. Recommendation")
    writer.add_paragraph(
        "It is recommended that the affected section of the N1 nozzle neck be "
        "replaced with a new forged section to the original specification during "
        "the CDU-I shutdown scheduled for February 2024, and that the insulation "
        "system be renewed with a moisture barrier to the current CUI standard."
    )

    writer.add_heading("3. Financial Implication")
    writer.add_paragraph(
        "The estimated cost of the repair, comprising material, fabrication, "
        "non destructive examination and insulation renewal, is INR 42.6 lakh. "
        "Provision exists within the approved shutdown budget for CDU-I under head "
        "CAP/2024/CDU/11.",
        fact="f26",
    )

    writer.add_heading("4. Approval")
    writer.add_paragraph(
        "The recommendation at clause 2 above, and the expenditure of INR 42.6 lakh "
        "at clause 3, were approved by the General Manager, Technical Services, on "
        "24 November 2023. Execution is entrusted to the Maintenance Department "
        "under the supervision of the Inspection Engineer.",
        fact="f27",
    )
    writer.add_signature_block(["Prepared By", "Reviewed By", "Approved By"])
    return "APR-2023-0088-approval-note.pdf", writer


def build_pump_sop() -> tuple[str, PdfWriter]:
    writer = PdfWriter(title="Pump Alignment Procedure", doc_no="SOP-MECH-021 Rev.2")
    writer.add_title(
        "STANDARD OPERATING PROCEDURE",
        "SOP-MECH-021 Rev.2 - Alignment of Centrifugal Pumps and Drivers",
    )
    writer.add_key_values([
        ("Document No.", "SOP-MECH-021"),
        ("Revision", "2"),
        ("Effective Date", "15 Jun 2023"),
        ("Custodian", "Head - Mechanical Maintenance"),
    ])

    writer.add_heading("1. Scope")
    writer.add_paragraph(
        "This procedure covers cold alignment of horizontal centrifugal pumps to "
        "their drivers using reverse dial indicator or laser alignment methods. It "
        "applies to all pumps within the refinery of frame size 2 and above."
    )

    writer.add_heading("2. Alignment Tolerances")
    writer.add_table(
        ["Parameter", "Acceptable", "Alert", "Unacceptable", "Method"],
        [
            ["Angular misalignment", "0.05 mm per 100 mm", "0.08 mm per 100 mm",
             "Above 0.10 mm per 100 mm", "Dial or laser"],
            ["Parallel offset", "0.05 mm", "0.10 mm", "Above 0.15 mm", "Dial or laser"],
            ["Soft foot", "0.05 mm", "0.08 mm", "Above 0.10 mm", "Dial indicator"],
            ["Shaft run out", "0.03 mm", "0.05 mm", "Above 0.08 mm", "Dial indicator"],
        ],
        row_facts={0: "f28"},
        widths=[0.24, 0.19, 0.18, 0.22, 0.17],
    )
    writer.add_paragraph(
        "Soft foot shall be corrected to within 0.05 mm before any alignment "
        "readings are taken. Attempting to align a machine with an uncorrected soft "
        "foot produces readings that cannot be repeated."
    )

    writer.add_heading("3. Post Alignment Vibration Acceptance")
    writer.add_table(
        ["Machine Class", "Good", "Alarm", "Trip", "Standard"],
        [
            ["Centrifugal pump, below 15 kW", "2.8 mm/s RMS", "4.5 mm/s RMS", "7.1 mm/s RMS", "ISO 10816-3"],
            ["Centrifugal pump, 15 to 300 kW", "4.5 mm/s RMS", "7.1 mm/s RMS", "11.0 mm/s RMS", "ISO 10816-3"],
            ["Motor, rigid mounted", "2.8 mm/s RMS", "4.5 mm/s RMS", "7.1 mm/s RMS", "ISO 10816-3"],
        ],
        row_facts={1: "f29"},
        widths=[0.32, 0.17, 0.17, 0.17, 0.17],
    )
    writer.add_paragraph(
        "The vibration alarm limit for centrifugal pumps in the 15 to 300 kW range "
        "is 7.1 mm/s RMS measured at the bearing housing in the horizontal "
        "direction. A machine exceeding the alarm limit shall be reported to the "
        "Reliability Engineer within one shift."
    )
    for index, clause in enumerate(LOREM_CLAUSES[:4], start=1):
        writer.add_paragraph(f"4.{index}  {clause}")
    writer.add_signature_block(["Prepared By", "Reviewed By", "Approved By"])
    return "SOP-MECH-021-pump-alignment.pdf", writer


def build_incident_summary() -> tuple[str, PdfWriter]:
    writer = PdfWriter(title="Incident Investigation Summary", doc_no="INC/2023/014")
    writer.add_title(
        "INCIDENT INVESTIGATION SUMMARY",
        "Seal Failure and Hydrocarbon Release, Pump P-101A",
    )
    writer.add_key_values([
        ("Incident No.", "INC/2023/014"),
        ("Date of Incident", "14 Aug 2023"),
        ("Unit", "CDU-I"),
        ("Equipment", "P-101A, crude charge pump"),
        ("Classification", "Process safety event, Tier 2"),
        ("Investigation Lead", "K. Menon, Reliability Engineer"),
    ])

    writer.add_heading("1. Summary of Events")
    writer.add_paragraph(
        "At approximately 0340 hours on 14 August 2023, the mechanical seal of "
        "crude charge pump P-101A failed, releasing an estimated 40 litres of hot "
        "crude to the pump plinth. The release did not ignite. The standby pump "
        "P-101B was started and the affected pump isolated within eleven minutes."
    )

    writer.add_heading("2. Causes")
    writer.add_paragraph(
        "The immediate cause was cracking of the mechanical seal faces following a "
        "period of dry running. The seal flush line had become blocked with coke "
        "fines, interrupting the flush supply to the seal chamber.",
        fact="f30",
    )
    writer.add_paragraph(
        "The underlying cause was the absence of a low flow alarm on the seal flush "
        "line, and a plan review that had not identified the flush line as a "
        "safety critical element."
    )

    writer.add_heading("3. Timeline")
    writer.add_table(
        ["Time", "Event", "Source"],
        [
            ["0318", "Seal flush flow begins to decline", "DCS trend"],
            ["0334", "Pump outboard bearing temperature rises 12 deg C", "DCS trend"],
            ["0340", "Seal failure, hydrocarbon release to plinth", "Field operator"],
            ["0344", "Gas detector LD-1104 in alarm", "F&G log"],
            ["0351", "P-101B started, P-101A isolated", "Shift log"],
            ["0412", "Area declared safe, clean up commenced", "Shift log"],
        ],
        widths=[0.12, 0.58, 0.30],
    )

    writer.add_heading("4. Actions")
    writer.add_table(
        ["S.No", "Action", "Owner", "Target Date", "Status"],
        [
            ["1", "Install low flow alarm on seal flush lines of all crude charge pumps",
             "Instrumentation", "31 Dec 2023", "Complete"],
            ["2", "Revise seal flush strainer cleaning frequency to monthly",
             "Maintenance", "30 Sep 2023", "Complete"],
            ["3", "Add seal flush line to the safety critical element register",
             "Reliability", "31 Oct 2023", "Complete"],
            ["4", "Include dry running scenario in operator competency refresher",
             "Training", "31 Mar 2024", "In progress"],
        ],
        widths=[0.08, 0.46, 0.16, 0.16, 0.14],
    )
    writer.add_signature_block(["Investigated By", "Reviewed By", "Approved By"])
    return "INC-2023-014-incident-summary.pdf", writer


# Which documents are delivered as scans. The mix matters: four of eight, and
# the two most-questioned documents among them, so OCR failure cannot hide.
BUILDERS = [
    (build_vessel_inspection, False),
    (build_relief_valve_sop, True),
    (build_thickness_survey, False),
    (build_line_list, True),
    (build_vendor_letter, True),
    (build_approval_note, False),
    (build_pump_sop, False),
    (build_incident_summary, True),
]


def build(out_dir: Path, questions_path: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    fact_index: dict[str, tuple[str, int]] = {}
    report: list[dict] = []

    for seed, (builder, scanned) in enumerate(BUILDERS):
        filename, writer = builder()
        pages = writer.document.page_count
        fact_pages = dict(writer.fact_pages)
        target = out_dir / filename

        if scanned:
            data = scan_effect(writer.to_bytes(), seed=seed)
            target.write_bytes(data)
        else:
            writer.document.save(str(target))
        writer.document.close()

        for fact, page in fact_pages.items():
            fact_index[fact] = (filename, page)
        report.append(
            {"filename": filename, "pages": pages, "scanned": scanned, "facts": len(fact_pages)}
        )
        print(f"  {filename:<44} {pages:>3} pages  {'scanned' if scanned else 'native ':>8}"
              f"  {len(fact_pages)} facts")

    missing = [q["id"] for q in QUESTIONS if q["fact"] not in fact_index]
    if missing:
        raise SystemExit(
            f"make_corpus: {len(missing)} question(s) reference a fact that was never "
            f"rendered: {', '.join(missing)}. Ground truth would be wrong; refusing to "
            f"write questions.jsonl."
        )

    with questions_path.open("w", encoding="utf-8") as handle:
        for question in QUESTIONS:
            filename, page = fact_index[question["fact"]]
            handle.write(
                json.dumps(
                    {
                        "id": question["id"],
                        "question": question["question"],
                        "expected_doc": filename,
                        "expected_page": page,
                        "answer": question["answer"],
                        "kind": question["kind"],
                        "notes": "generated by eval/make_corpus.py; page recorded at render time",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    return {"documents": report, "questions": len(QUESTIONS), "facts": len(fact_index)}


def main() -> int:
    root = Path(__file__).resolve().parent.parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(root / "demo" / "documents"))
    parser.add_argument(
        "--questions", default=str(Path(__file__).resolve().parent / "questions.jsonl")
    )
    args = parser.parse_args()

    print(f"make_corpus: writing to {args.out}")
    summary = build(Path(args.out), Path(args.questions))
    total_pages = sum(d["pages"] for d in summary["documents"])
    scanned_pages = sum(d["pages"] for d in summary["documents"] if d["scanned"])
    print(
        f"make_corpus: {len(summary['documents'])} documents, {total_pages} pages "
        f"({scanned_pages} scanned), {summary['questions']} questions with known pages"
    )
    print(f"make_corpus: questions written to {args.questions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
