"""
1099 Eligibility Classifier
----------------------------
Determines whether a vendor requires a 1099-NEC (or 1099-MISC) filing
based on IRS rules for tax year 2024.

IRS RULE SUMMARY:
    File 1099-NEC when ALL of the following are true:
        1. Payment was made in the course of your trade or business
        2. Payment was made to an individual, partnership, estate, or LLC
           taxed as a partnership/sole proprietor (NOT a C-corp or S-corp)
        3. Total payments to that vendor in the tax year >= $600
        4. Payment was for services (not merchandise, freight, storage,
           telephone, or rent paid to a real estate agent)

    EXCEPTIONS — always file regardless of entity type:
        - Attorneys / law firms: file 1099-NEC for fees >= $600
          regardless of incorporation status (IRS §6045(f))
        - Medical / health care payments to corporations: file
          1099-MISC Box 6 for payments >= $600

    THRESHOLDS:
        - 1099-NEC (nonemployee compensation): $600
        - 1099-MISC Box 1 (rents):             $600
        - 1099-MISC Box 6 (medical payments):  $600
        - 1099-INT (interest income):           $10
        - 1099-DIV (dividends):                 $10
        - 1099-K (payment cards):               $5,000 (2024 transitional)

References:
    IRS Publication 1220, IRS Publication 15-A
    Instructions for Form 1099-NEC (2024)
    Instructions for Form 1099-MISC (2024)
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from transaction_aggregator import VendorSummary


# ---------------------------------------------------------------------------
# IRS thresholds
# ---------------------------------------------------------------------------

THRESHOLD_NEC  = 600.0    # 1099-NEC nonemployee compensation
THRESHOLD_MISC = 600.0    # 1099-MISC rents / medical
THRESHOLD_INT  = 10.0     # 1099-INT interest (informational only here)


# ---------------------------------------------------------------------------
# Entity classification
# ---------------------------------------------------------------------------

# C-corps and S-corps are EXEMPT from 1099-NEC (unless attorney/medical)
EXEMPT_ENTITY_TYPES = {
    "INC", "CORP", "NA", "FSB",
    # S-corps file as INC — same exemption applies
}

# These entity types ARE subject to 1099-NEC
NONEXEMPT_ENTITY_TYPES = {
    "LLC",   # Taxed as partnership/sole proprietor unless they elected corp treatment
    "LLP",
    "LP",
    "PC",    # Professional Corporation — attorneys, doctors: special rules apply
    "PLLC",
    "CO",
}

# ---------------------------------------------------------------------------
# Known public entity lists — vendors that appear in real small-business
# bank statements. Organized by category so additions are easy to find.
# Rule: if a junior accountant would immediately recognize the name,
# it belongs here with the correct classification.
# ---------------------------------------------------------------------------

# ── ATTORNEY / LEGAL ────────────────────────────────────────────────────────
# File 1099-NEC regardless of entity type (IRS §6045(f))
ATTORNEY_KEYWORDS = [
    r"\bATTORNEY\b", r"\bATTORNEYS\b",
    r"\bLAW\s+FIRM\b", r"\bLAW\s+OFFICE\b", r"\bLAW\s+GROUP\b",
    r"\bLAW\s+CENTER\b", r"\bLAW\s+ASSOCIATES\b",
    r"\bLEGAL\s+GROUP\b", r"\bLEGAL\s+SERVICES\b",
    r"\bLEGAL\s+CENTER\b", r"\bLEGAL\s+ASSOCIATES\b",
    r"\bCOUNSEL\b", r"\bCOUNSELORS\b",
    r"\bESQ\b", r"\bESQUIRE\b",
    r"\bLLP\b",          # Most LLPs are law firms
    r"\bLITIGATION\b",
    r"\bLEGAL\b",        # Broad catch — below merchandise/utility so no false positives
]

# ── MEDICAL / HEALTHCARE ────────────────────────────────────────────────────
# File 1099-MISC Box 6 even for corporations (IRS §6041)
MEDICAL_KEYWORDS = [
    # Generic terms
    r"\bMEDICAL\b", r"\bMEDICINE\b", r"\bMEDICAL\s+CENTER\b",
    r"\bHEALTH\b", r"\bHEALTHCARE\b", r"\bHEALTH\s+SYSTEM\b",
    r"\bHOSPITAL\b", r"\bCLINIC\b", r"\bCLINICS\b",
    r"\bPHYSICIAN\b", r"\bPHYSICIANS\b",
    r"\bDOCTOR\b", r"\bDOCTORS\b",
    r"\bDENTAL\b", r"\bDENTIST\b", r"\bDENTISTRY\b",
    r"\bPHARMACY\b", r"\bPHARMACEUTICAL\b",
    r"\bTHERAPY\b", r"\bTHERAPEUT\b",
    r"\bSURGERY\b", r"\bSURGICAL\b", r"\bSURGEON\b",
    r"\bCHIROPRACT\b", r"\bCHIROPRACTIC\b",
    r"\bNURSING\b", r"\bNURSE\b",
    r"\bPSYCHIATR\b", r"\bPSYCHOLOG\b",
    r"\bORTHOPED\b", r"\bORTHODONT\b",
    r"\bOPTOMETR\b", r"\bOPHTHALM\b",
    r"\bPEDIATR\b", r"\bOBSTETR\b", r"\bGYNECOL\b",
    r"\bRADIOLOG\b", r"\bONCOLOG\b", r"\bCARDIOL\b",
    r"\bDERMATOL\b", r"\bNEUROLOG\b",
    r"\bURGENT\s+CARE\b",
    # Known national chains
    r"\bCVS\s+HEALTH\b", r"\bCVS\s+PHARMACY\b",
    r"\bWALGREENS\b", r"\bWALGREEN\b",
    r"\bRITE\s+AID\b",
    r"\bKAISER\b",
    r"\bUNITEDHEALTH\b", r"\bAETNA\b", r"\bCIGNA\b",
    r"\bHUMANA\b",
    r"\bBLUE\s+CROSS\b", r"\bBLUE\s+SHIELD\b",
]

# ── RETAIL / MERCHANDISE — EXEMPT ───────────────────────────────────────────
# These sell goods, not services → exempt from 1099-NEC
# IMPORTANT: Amazon/Google/Microsoft cloud services ARE taxable
# — those are handled by CLOUD_SERVICES_KEYWORDS below
MERCHANDISE_KEYWORDS = [
    # Online retail
    r"\bAMAZON\.COM\b",
    r"\bAMZN\s+MKTP\b", r"\bAMZN\s+MARKETPLACE\b",
    # Note: plain "AMAZON" or "AWS" intentionally NOT here → handled by cloud services
    r"\bEBAY\b",
    r"\bETSY\b",
    r"\bSHOPIFY\b",          # platform fees ARE services, but most charges are pass-through
    r"\bWAYFAIR\b",
    r"\bCHEWY\b",
    r"\bNEWEGG\b",
    # Big box retail
    r"\bWALMART\b", r"\bWAL-MART\b", r"\bWAL\s+MART\b",
    r"\bTARGET\b",
    r"\bCOSTCO\b",
    r"\bSAM'?S\s+CLUB\b",
    r"\bBJ'?S\s+WHOLESALE\b",
    r"\bHOME\s+DEPOT\b", r"\bHOMEDEPOT\b",
    r"\bLOWE'?S\b",
    r"\bSTAPLES\b",
    r"\bOFFICE\s+DEPOT\b", r"\bOFFICEMAX\b",
    r"\bBEST\s+BUY\b",
    r"\bIKEA\b",
    r"\bBED\s+BATH\b",
    # Grocery / food retail
    r"\bWHOLE\s+FOODS\b",
    r"\bTRADER\s+JOE\b",
    r"\bSAFEWAY\b",
    r"\bKROGER\b",
    r"\bALBERTSON\b",
    r"\bPUBLIX\b",
    r"\bMEIJER\b",
    r"\bH-E-B\b", r"\bHEB\b",
    r"\bWEGMAN\b",
    r"\bALDI\b", r"\bLIDL\b",
    r"\bSPROUTS\b",
    # Gas stations (fuel = goods)
    r"\bSHELL\s+OIL\b", r"\bSHELL\s+GAS\b",
    r"\bCHEVRON\b",
    r"\bEXXON\b", r"\bMOBIL\b",
    r"\bBP\s+GAS\b",
    r"\bARCO\b",
    r"\b76\s+GAS\b",
    r"\bCIRCLE\s+K\b",
    r"\b7-ELEVEN\b", r"\b7\s+ELEVEN\b",
    r"\bWAWA\b",
    r"\bQUICKTRIP\b", r"\bQT\s+GAS\b",
]

# ── CLOUD / DIGITAL SERVICES — TAXABLE ──────────────────────────────────────
# These provide services (not goods) → 1099-NEC eligible if $600+
# This list overrides MERCHANDISE_KEYWORDS when matched
CLOUD_SERVICE_KEYWORDS = [
    # Amazon Web Services
    r"\bAMAZON\s+WEB\s+SERVICES\b", r"\bAWS\b",
    # Google
    r"\bGOOGLE\s+CLOUD\b", r"\bGOOGLE\s+ADS\b",
    r"\bGOOGLE\s+WORKSPACE\b", r"\bG\s+SUITE\b",
    r"\bGOOGLE\s+LLC\b",
    # Microsoft
    r"\bMICROSOFT\s+365\b", r"\bMICROSOFT\s+AZURE\b",
    r"\bMICROSOFT\s+CORP\b",
    r"\bOFFICE\s+365\b",
    r"\bGITHUB\b",
    # Adobe
    r"\bADOBE\b",
    # Salesforce / CRM
    r"\bSALESFORCE\b",
    r"\bHUBSPOT\b",
    r"\bZOHO\b",
    r"\bPIPEDRIVE\b",
    # Accounting / payroll software
    r"\bQUICKBOOKS\b", r"\bINTUIT\b",
    r"\bGUSTO\b",
    r"\bADP\s+LLC\b", r"\bADP\s+INC\b",
    r"\bPAYCHEX\b",
    r"\bFRESHBOOKS\b",
    r"\bXERO\b",
    # Communication / productivity
    r"\bSLACK\b",
    r"\bZOOM\b",
    r"\bDROPBOX\b",
    r"\bNOTION\b",
    r"\bASANA\b",
    r"\bMONDAY\.COM\b",
    r"\bTRELLO\b",
    r"\bBASECAMP\b",
    r"\bCONFLUENCE\b", r"\bJIRA\b", r"\bATLASSIAN\b",
    # Marketing / analytics
    r"\bMAILCHIMP\b",
    r"\bCONSTANT\s+CONTACT\b",
    r"\bHOOTSUITE\b",
    r"\bSEMRUSH\b",
    r"\bCANVA\b",
    r"\bFIGMA\b",
    # Payment processing (fees = services)
    r"\bSTRIPE\b",
    r"\bSQUARE\b",
    r"\bPAYPAL\b",
    r"\bTWILIO\b",
    r"\bSENDGRID\b",
    # Dev tools / hosting
    r"\bGITLAB\b",
    r"\bDIGITAL\s+OCEAN\b",
    r"\bHEROKU\b",
    r"\bCLOUDFLARE\b",
    r"\bNETLIFY\b",
    r"\bVERCEL\b",
]

# ── UTILITIES / TELEPHONE — EXEMPT ───────────────────────────────────────────
# Essential services billed monthly — exempt from 1099-NEC
UTILITY_KEYWORDS = [
    # Major telecom carriers
    r"\bVERIZON\b",
    r"\bAT&T\b", r"\bATT\b",
    r"\bT-MOBILE\b", r"\bTMOBILE\b",
    r"\bSPRINT\b",
    r"\bUS\s+CELLULAR\b",
    r"\bCRICKET\s+WIRELESS\b",
    r"\bBOOST\s+MOBILE\b",
    # Cable / internet / satellite
    r"\bCOMCAST\b", r"\bXFINITY\b",
    r"\bCHARTER\b", r"\bSPECTRUM\b",
    r"\bCOX\s+COMMUNICATIONS\b", r"\bCOX\s+CABLE\b",
    r"\bCENTURYLINK\b", r"\bLUMEN\b",
    r"\bFRONTIER\b",
    r"\bAT&T\s+UVERSE\b", r"\bDIRECTV\b",
    r"\bDISH\s+NETWORK\b",
    # Electric utilities
    r"\bPG&E\b", r"\bPGE\b",
    r"\bCONED\b", r"\bCON\s+EDISON\b",
    r"\bEDISON\s+ELECTRIC\b",
    r"\bDUKE\s+ENERGY\b",
    r"\bSOUTHERN\s+COMPANY\b",
    r"\bEXELON\b",
    r"\bDOMINION\s+ENERGY\b",
    r"\bAMEREN\b",
    r"\bEVERGY\b",
    r"\bNATIONAL\s+GRID\b",
    r"\bFPL\b", r"\bFLORIDA\s+POWER\b",
    r"\bSCE\b", r"\bSOCALGAS\b",
    r"\bSDG&E\b",
    r"\bPSE&G\b", r"\bPSEG\b",
    r"\bCLEVELAND\s+ELECTRIC\b",
    r"\bWEST\s+PENN\s+POWER\b",
    r"\bXCEL\s+ENERGY\b",
    r"\bELECTRIC\b",            # Generic catch
    # Gas utilities
    r"\bSOCALGAS\b",
    r"\bCONSOLIDATED\s+GAS\b",
    r"\bNATURAL\s+GAS\b",
    r"\bGAS\s+CO\b", r"\bGAS\s+COMPANY\b",
    r"\bNICOR\b", r"\bPECO\b",
    r"\bCOLUMBIA\s+GAS\b",
    r"\bATMOS\s+ENERGY\b",
    r"\bSPIRE\s+GAS\b",
    # Water / sewer
    r"\bWATER\s+DISTRICT\b", r"\bWATER\s+AUTHORITY\b",
    r"\bWATER\s+DEPT\b", r"\bWATER\s+DEPT\b",
    r"\bMUNICIPAL\s+WATER\b",
    r"\bAMERICAN\s+WATER\b",
    r"\bVEOLIA\s+WATER\b",
    # Waste / recycling
    r"\bWASTE\s+MANAGEMENT\b", r"\bWM\s+CORP\b",
    r"\bREPUBLIC\s+SERVICES\b",
    r"\bCLEAN\s+HARBORS\b",
]

# ── FINANCIAL INSTITUTIONS — EXEMPT ──────────────────────────────────────────
# Banks, credit unions, insurance companies — payments are typically
# loan payments, interest, or premiums (not service payments)
FINANCIAL_INSTITUTION_KEYWORDS = [
    # Major banks
    r"\bBANK\s+OF\s+AMERICA\b", r"\bBOFА\b",
    r"\bJPMORGAN\b", r"\bJP\s+MORGAN\b", r"\bCHASE\b",
    r"\bWELLS\s+FARGO\b",
    r"\bCITIBANK\b", r"\bCITI\b",
    r"\bUS\s+BANK\b", r"\bUSBANCORP\b",
    r"\bPNC\s+BANK\b", r"\bPNC\b",
    r"\bTD\s+BANK\b",
    r"\bCAPITAL\s+ONE\b",
    r"\bAMERICAN\s+EXPRESS\b", r"\bAMEX\b",
    r"\bDISCOVER\b",
    r"\bCITIZENS\s+BANK\b",
    r"\bREGIONS\s+BANK\b",
    r"\bSUNTRUST\b", r"\bTRUIST\b",
    r"\bFIFTH\s+THIRD\b",
    r"\bHUNTINGTON\b",
    r"\bNEW\s+YORK\s+MELLON\b",
    r"\bSILICON\s+VALLEY\s+BANK\b",
    r"\bFIRST\s+REPUBLIC\b",
    r"\bSIGNATURE\s+BANK\b",
    # Credit unions (generic)
    r"\bCREDIT\s+UNION\b",
    r"\bFEDERAL\s+CREDIT\b",
    # Insurance
    r"\bSTATE\s+FARM\b",
    r"\bALLSTATE\b",
    r"\bGEICO\b",
    r"\bPROGRESSIVE\b",
    r"\bFARMERS\s+INS\b",
    r"\bNATIONWIDE\b",
    r"\bLIBERTY\s+MUTUAL\b",
    r"\bTRAVELERS\b",
    r"\bHARTFORD\b",
    r"\bCHUBB\b",
    r"\bZURICH\b",
    r"\bMETLIFE\b",
    r"\bPRUDENTIAL\b",
    r"\bNORTHWESTERN\s+MUTUAL\b",
    r"\bNEW\s+YORK\s+LIFE\b",
    r"\bMASS\s+MUTUAL\b",
    r"\bINSURANCE\b",          # Generic catch
]

# ── GOVERNMENT / TAX AGENCIES — EXEMPT ───────────────────────────────────────
# Tax payments, government fees — never 1099-eligible
GOVERNMENT_KEYWORDS = [
    r"\bIRS\b",
    r"\bINTERNAL\s+REVENUE\b",
    r"\bDEPT\s+OF\s+TREASURY\b",
    r"\bU\.?S\.?\s+TREASURY\b",
    r"\bFRANCHISE\s+TAX\s+BOARD\b", r"\bFTB\b",
    r"\bSTATE\s+TAX\b",
    r"\bCITY\s+TAX\b",
    r"\bCOUNTY\s+TAX\b",
    r"\bSALES\s+TAX\b",
    r"\bPROPERTY\s+TAX\b",
    r"\bDMV\b", r"\bDEPT\s+OF\s+MOTOR\b",
    r"\bSECRETARY\s+OF\s+STATE\b",
    r"\bUSPS\b", r"\bU\.S\.\s+POSTAL\b", r"\bUS\s+POST\b",
    r"\bFEDEX\s+GOV\b",
    r"\bSBA\b", r"\bSMALL\s+BUSINESS\s+ADMIN\b",
    r"\bSSD\b", r"\bSOCIAL\s+SECURITY\b",
    r"\bMEDICARE\s+PAYMENT\b",
    r"\bMEDICAID\b",
    r"\bLICENSE\s+FEE\b",
    r"\bPERMIT\s+FEE\b",
    r"\bCITY\s+OF\b",
    r"\bCOUNTY\s+OF\b",
    r"\bSTATE\s+OF\b",
]

# ── TRAVEL / HOSPITALITY — TAXABLE SERVICES ──────────────────────────────────
# Travel agents, hotels providing conference/event services to businesses
# (note: personal hotel stays for employees are reimbursement, not vendor payments)
# These are generally 1099-eligible if paid directly for services
TRAVEL_SERVICE_KEYWORDS = [
    r"\bTRAVEL\s+AGENCY\b", r"\bTRAVEL\s+AGENT\b",
    r"\bEVENT\s+PLANNING\b", r"\bEVENT\s+MANAGEMENT\b",
    r"\bCATERING\b",
    r"\bCONFERENCE\s+CENTER\b",
]

# ── STAFFING / TEMP AGENCIES — TAXABLE ───────────────────────────────────────
# Staffing agencies providing contract workers — clearly 1099-NEC eligible
STAFFING_KEYWORDS = [
    r"\bSTAFFING\b",
    r"\bTEMP\s+AGENCY\b", r"\bTEMPORARY\s+SERVICES\b",
    r"\bRECRUITING\b", r"\bRECRUITMENT\b",
    r"\bHEADHUNT\b",
    r"\bROBERT\s+HALF\b",
    r"\bMANPOWER\b",
    r"\bKELLY\s+SERVICES\b",
    r"\bADECCO\b",
    r"\bINDEED\b",       # Hiring platform fees
    r"\bZIPRECRUITER\b",
]

# ── SHIPPING / FREIGHT — EXEMPT ───────────────────────────────────────────────
# Freight and courier payments are exempt from 1099-NEC per IRS rules
SHIPPING_KEYWORDS = [
    r"\bFEDEX\b", r"\bFED\s+EX\b",
    r"\bUPS\b",
    r"\bDHL\b",
    r"\bUSPS\b",
    r"\bU\.S\.\s+POSTAL\b",
    r"\bFREIGHT\b",
    r"\bSHIPPING\b",
    r"\bCOURIER\b",
    r"\bLAST\s+MILE\b",
]

# ── FOOD DELIVERY — CHECK CAREFULLY ──────────────────────────────────────────
# Food delivery to employees/clients → meals expense, not vendor payment
# Food delivery to customers → could be a service cost
# Generally not 1099-eligible in small business context
FOOD_DELIVERY_KEYWORDS = [
    r"\bDOORDASH\b",
    r"\bUBER\s+EATS\b", r"\bUBEREATS\b",
    r"\bGRUBHUB\b",
    r"\bPOSTMATES\b",
    r"\bCHOWNOW\b",
    r"\bEZCATE\b",
    r"\bCAVIAR\b",
]

# ── RIDE SHARE — EXEMPT ───────────────────────────────────────────────────────
# Transportation reimbursements — not vendor service payments
RIDESHARE_KEYWORDS = [
    r"\bUBER\s*\*\b", r"\bUBER\s+TRIP\b", r"\bUBER\s+RIDE\b",
    r"\bLYFT\b",
    r"\bCURB\b",
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class EligibilityResult:
    """Full 1099 eligibility determination for one vendor."""
    vendor_name: str
    entity_type: Optional[str]
    total_amount: float

    # Primary determination
    eligible: bool                    # True = must file 1099
    form_type: str                    # "1099-NEC" | "1099-MISC" | "EXEMPT" | "REVIEW"
    threshold_met: bool               # Total >= applicable threshold
    threshold_amount: float           # Which threshold applied

    # Reason codes
    exemption_reason: str = ""        # Why exempt (if applicable)
    exception_reason: str = ""        # Why NOT exempt despite corp status
    needs_w9: bool = True             # Always True if eligible
    notes: str = ""                   # Human-readable explanation

    # W-9 status (placeholder — Session 5 adds SQLite persistence)
    w9_on_file: str = "Unknown"       # "Yes" | "No" | "Unknown"


# ---------------------------------------------------------------------------
# Core classification logic
# ---------------------------------------------------------------------------

def _matches_any(name: str, patterns: list[str]) -> bool:
    """Check if vendor name matches any keyword pattern."""
    name_upper = name.upper()
    return any(re.search(p, name_upper) for p in patterns)


def classify_vendor_1099(summary: VendorSummary) -> EligibilityResult:
    """
    Apply IRS 1099-NEC/MISC eligibility rules to a single vendor summary.

    Decision order (priority highest → lowest):
        1.  Below threshold?                    → EXEMPT
        2.  Government / tax agency?            → EXEMPT
        3.  Financial institution?              → EXEMPT
        4.  Shipping / freight?                 → EXEMPT
        5.  Attorney keyword?                   → 1099-NEC (exception overrides corp)
        6.  Medical keyword?                    → 1099-MISC if corp, 1099-NEC if not
        7.  Cloud / digital services?           → 1099-NEC eligible (overrides Amazon/Google retail)
        8.  Ride share / food delivery?         → EXEMPT
        9.  Merchandise / retail?               → EXEMPT
        10. Utility / telephone?                → EXEMPT
        11. Staffing / temp agency?             → 1099-NEC
        12. C-corp / S-corp entity type?        → EXEMPT
        13. Individual / LLC / unknown?         → 1099-NEC (with REVIEW if low confidence)
    """
    name = summary.canonical_name
    entity = (summary.entity_type or "").upper().strip()
    total = summary.total_amount

    # ── 1. Threshold ──
    if total < THRESHOLD_NEC:
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=False, form_type="EXEMPT", threshold_met=False,
            threshold_amount=THRESHOLD_NEC, needs_w9=False,
            exemption_reason=f"Total ${total:,.2f} below $600 threshold",
            notes=f"No 1099 required. Total paid ${total:,.2f} < $600 threshold.",
        )

    # ── 2. Government / tax agency ──
    if _matches_any(name, GOVERNMENT_KEYWORDS):
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=False, form_type="EXEMPT", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=False,
            exemption_reason="Government agency or tax payment — exempt",
            notes=f"{name} appears to be a government entity or tax payment. No 1099 required.",
        )

    # ── 3. Financial institution ──
    if _matches_any(name, FINANCIAL_INSTITUTION_KEYWORDS):
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=False, form_type="EXEMPT", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=False,
            exemption_reason="Bank, insurance, or financial institution — exempt",
            notes=f"{name} is a financial institution. Loan payments, premiums, and fees to banks are not 1099-reportable.",
        )

    # ── 4. Shipping / freight ──
    if _matches_any(name, SHIPPING_KEYWORDS):
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=False, form_type="EXEMPT", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=False,
            exemption_reason="Freight / shipping carrier — exempt from 1099-NEC",
            notes=f"{name} is a shipping carrier. Freight charges are exempt from 1099-NEC per IRS rules.",
        )

    # ── 5. Attorney exception (overrides corporate status) ──
    if _matches_any(name, ATTORNEY_KEYWORDS):
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=True, form_type="1099-NEC", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=True,
            exception_reason="Attorney exception — file regardless of entity type (IRS §6045(f))",
            notes=(
                f"File 1099-NEC for ${total:,.2f}. Attorney and legal fees require "
                "1099 filing regardless of corporate status. Obtain W-9 if not on file."
            ),
        )

    # ── 6. Medical exception ──
    if _matches_any(name, MEDICAL_KEYWORDS):
        if entity in EXEMPT_ENTITY_TYPES:
            return EligibilityResult(
                vendor_name=name, entity_type=entity, total_amount=total,
                eligible=True, form_type="1099-MISC", threshold_met=True,
                threshold_amount=THRESHOLD_MISC, needs_w9=True,
                exception_reason="Medical exception — corporations included for Box 6",
                notes=(
                    f"File 1099-MISC Box 6 for ${total:,.2f}. Medical/healthcare "
                    "payments to corporations are reportable. Obtain W-9 if not on file."
                ),
            )
        else:
            return EligibilityResult(
                vendor_name=name, entity_type=entity or None, total_amount=total,
                eligible=True, form_type="1099-NEC", threshold_met=True,
                threshold_amount=THRESHOLD_NEC, needs_w9=True,
                notes=(
                    f"File 1099-NEC for ${total:,.2f}. Medical/healthcare provider "
                    "(non-corporate). Obtain W-9 if not on file."
                ),
            )

    # ── 7. Cloud / digital services (overrides retail exemptions) ──
    if _matches_any(name, CLOUD_SERVICE_KEYWORDS):
        if entity in EXEMPT_ENTITY_TYPES:
            return EligibilityResult(
                vendor_name=name, entity_type=entity, total_amount=total,
                eligible=False, form_type="EXEMPT", threshold_met=True,
                threshold_amount=THRESHOLD_NEC, needs_w9=False,
                exemption_reason=f"Cloud/digital services from corporation ({entity}) — exempt",
                notes=(
                    f"{name} is a corporation providing digital services. "
                    "Corporations are exempt from 1099-NEC."
                ),
            )
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=True, form_type="1099-NEC", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=True,
            notes=(
                f"File 1099-NEC for ${total:,.2f}. {name} provides digital/cloud "
                "services. Service fees are 1099-reportable. Obtain W-9 if not on file."
            ),
        )

    # ── 8. Ride share / food delivery ──
    if _matches_any(name, RIDESHARE_KEYWORDS) or _matches_any(name, FOOD_DELIVERY_KEYWORDS):
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=False, form_type="EXEMPT", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=False,
            exemption_reason="Ride share / food delivery — typically meal/travel expense",
            notes=f"{name} is a ride share or food delivery platform. These are typically meal or travel reimbursements, not vendor service payments.",
        )

    # ── 9. Merchandise / retail ──
    if _matches_any(name, MERCHANDISE_KEYWORDS):
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=False, form_type="EXEMPT", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=False,
            exemption_reason="Merchandise/retail — 1099-NEC covers services only",
            notes=(
                f"{name} is a retail or merchandise vendor. "
                "1099-NEC applies to services, not goods purchases."
            ),
        )

    # ── 10. Utility / telephone ──
    if _matches_any(name, UTILITY_KEYWORDS):
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=False, form_type="EXEMPT", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=False,
            exemption_reason="Utility / telephone — exempt from 1099-NEC",
            notes=(
                f"{name} is a utility or telephone provider. "
                "These are generally exempt from 1099-NEC reporting."
            ),
        )

    # ── 11. Staffing / temp agency ──
    if _matches_any(name, STAFFING_KEYWORDS):
        if entity in EXEMPT_ENTITY_TYPES:
            return EligibilityResult(
                vendor_name=name, entity_type=entity, total_amount=total,
                eligible=False, form_type="EXEMPT", threshold_met=True,
                threshold_amount=THRESHOLD_NEC, needs_w9=False,
                exemption_reason=f"Staffing agency incorporated ({entity}) — exempt",
                notes=f"{name} is an incorporated staffing agency. Corporations are exempt from 1099-NEC.",
            )
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=True, form_type="1099-NEC", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=True,
            notes=(
                f"File 1099-NEC for ${total:,.2f}. {name} is a staffing or "
                "recruiting service. Obtain W-9 if not on file."
            ),
        )

    # ── 12. Corporation exemption ──
    if entity in EXEMPT_ENTITY_TYPES:
        return EligibilityResult(
            vendor_name=name, entity_type=entity, total_amount=total,
            eligible=False, form_type="EXEMPT", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=False,
            exemption_reason=f"Corporation ({entity}) — exempt from 1099-NEC",
            notes=(
                f"{name} is incorporated ({entity}). Corporations are generally "
                "exempt from 1099-NEC reporting."
            ),
        )

    # ── 13. Individual / LLC / unknown → eligible with REVIEW if low confidence ──
    low_confidence = summary.match_confidence < 0.80
    entity_unknown = not entity

    if low_confidence or entity_unknown:
        reasons = []
        if entity_unknown:
            reasons.append("entity type unknown — could not determine if individual or corporation")
        if low_confidence:
            reasons.append(f"vendor name confidence {summary.match_confidence:.0%} — verify this is the correct payee")
        return EligibilityResult(
            vendor_name=name, entity_type=entity or None, total_amount=total,
            eligible=True, form_type="REVIEW", threshold_met=True,
            threshold_amount=THRESHOLD_NEC, needs_w9=True,
            notes=(
                f"Likely requires 1099-NEC for ${total:,.2f} but needs human review: "
                f"{'; '.join(reasons)}. "
                "Obtain W-9 and confirm entity type before filing."
            ),
        )

    # Known non-exempt entity
    entity_label = {
        "LLC": "LLC (taxed as partnership/sole proprietor)",
        "LLP": "LLP", "LP": "LP",
        "PC": "Professional Corporation",
        "PLLC": "PLLC", "CO": "Company",
    }.get(entity, entity)

    return EligibilityResult(
        vendor_name=name, entity_type=entity, total_amount=total,
        eligible=True, form_type="1099-NEC", threshold_met=True,
        threshold_amount=THRESHOLD_NEC, needs_w9=True,
        notes=(
            f"File 1099-NEC for ${total:,.2f}. {name} is a {entity_label}, "
            "subject to 1099-NEC for service payments. "
            "Obtain W-9 if not already on file."
        ),
    )


def classify_all_vendors(
    summaries: list[VendorSummary],
) -> dict[str, EligibilityResult]:
    """
    Run the classifier on every vendor summary.
    Returns a dict keyed by canonical_name for O(1) lookup in the Excel generator.
    """
    return {s.canonical_name: classify_vendor_1099(s) for s in summaries}


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from transaction_aggregator import VendorSummary

    test_vendors = [
        # (name, entity_type, total, confidence, expected_form)
        ("John Smith",              None,   3950.00, 0.82, "REVIEW"),
        ("Mary Johnson Consulting", "LLC",  3950.00, 1.0,  "1099-NEC"),
        ("David Lee Contractor",    None,   3350.00, 1.0,  "REVIEW"),
        ("Robert Kim",              "LLC",  1850.00, 1.0,  "1099-NEC"),
        ("Comcast Business",        None,    499.98, 1.0,  "EXEMPT"),
        ("PG&E Electric",           None,    900.54, 1.0,  "EXEMPT"),
        ("Amazon",                  None,    475.25, 0.80, "EXEMPT"),
        ("Home Depot",              None,    735.04, 0.95, "EXEMPT"),
        ("Staples",                 "INC",   392.14, 1.0,  "EXEMPT"),
        ("Office Depot",            "CORP",   78.22, 1.0,  "EXEMPT"),
        ("Smith & Jones Law LLP",   "LLP",  8500.00, 1.0,  "1099-NEC"),
        ("City Medical Center",     "INC",  12000.0, 1.0,  "1099-MISC"),
        ("Check",                   None,    375.00, 1.0,  "EXEMPT"),
        ("Verizon Wireless",        None,    378.00, 1.0,  "EXEMPT"),
        ("Amzn US",                 None,    415.70, 1.0,  "EXEMPT"),
    ]

    # Build mock VendorSummary objects
    mocks = []
    for name, entity, total, conf, _ in test_vendors:
        s = VendorSummary(
            canonical_name=name,
            entity_type=entity,
            total_amount=total,
            transaction_count=1,
            first_payment_date=None,
            last_payment_date=None,
            match_confidence=conf,
        )
        mocks.append(s)

    results = classify_all_vendors(mocks)

    print(f"\n{'Vendor':<28} {'Entity':<6} {'Total':>9} {'Form':<12} {'Notes'}")
    print("-" * 110)
    for s, (name, entity, total, conf, expected) in zip(mocks, test_vendors):
        r = results[s.canonical_name]
        status = "✓" if r.form_type == expected else f"✗ (expected {expected})"
        print(
            f"{r.vendor_name:<28} {(r.entity_type or '?'):<6} "
            f"${r.total_amount:>8,.2f} {r.form_type:<12} "
            f"{r.notes[:45]}...  {status}"
        )
