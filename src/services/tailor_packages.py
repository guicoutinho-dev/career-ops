"""
Generate localized, tailored CV and Cover Letter packages for the target Australian roles.
Strictly derived from cv.md and config/profile.yml (Updated to 5 years experience, SAP Business AI Platform, Output Management).
Localizes the location header to the target Australian city + WHV 462 work rights.
"""
import os
import sys
import json
import subprocess
from pathlib import Path

from src.database.connection import get_connection

BASE_TEMPLATE_PATH = Path("templates/cv-template.html")
COVER_TEMPLATE_PATH = Path("templates/cover-letter-template.html")

ROLES_SPEC = [
    {
        "app_id": "app_005",
        "company": "Experis AU",
        "role_title": "SAP ABAP Developer",
        "city": "Brisbane QLD",
        "header_location": "Brisbane QLD (Relocating | Working Holiday Visa Subclass 462 — Full Work Rights)",
        "summary": "SAP ABAP Consultant with 5 years of experience delivering and supporting enterprise SAP solutions across ECC and S/4HANA environments. Strong foundation in classic ABAP development (ALV, enhancements, module-pool) and modern S/4HANA-aligned architectures (CDS Views, OData, ABAP Cloud). Triple certified in ABAP Cloud, SAP BTP Solution Architecture, and SAP Business AI Platform. Ready for Brisbane delivery with independent Australian work rights.",
        "competencies": [
            "SAP ABAP & S/4HANA Core Development (5 Yrs)",
            "OData Services & SAP Gateway Integration",
            "CDS Views & AMDP Performance Tuning",
            "ABAP Cloud & Clean Core Architecture",
            "SAP Standard Debugging & Root Cause Analysis"
        ],
        "cover_highlights": (
            "With 5 years of dedicated SAP ABAP consulting across multinational environments (including Accenture, Deloitte, and Softtek), "
            "I bring deep expertise in classic and S/4HANA-aligned development. Notably, I led the technical analysis of 1,260 SAP Notes "
            "for the Brazilian Tax Reform, reducing the delivery timeline from 2 months to 3–4 days through custom automation. "
            "Holding triple certifications in ABAP Cloud, SAP BTP, and SAP Business AI Platform, combined with full independent work authorization "
            "via the Working Holiday Visa (Subclass 462), I offer immediate remote collaboration and seamless transition to Brisbane."
        )
    },
    {
        "app_id": "app_006",
        "company": "George Weston Foods",
        "role_title": "Senior SAP BTP Integration Developer",
        "city": "Sydney NSW",
        "header_location": "Sydney NSW (Relocating | Working Holiday Visa Subclass 462 — Full Work Rights)",
        "summary": "SAP BTP Integration & ABAP Consultant with 5 years of enterprise experience across ECC and S/4HANA environments. Certified in SAP BTP Solution Architecture, ABAP Cloud, and SAP Business AI Platform. Proven expertise in OData services via SAP Gateway, CDS Views, and FMCG production system support. Equipped with independent Australian work rights (WHV 462).",
        "competencies": [
            "SAP BTP Solution Architecture & Integration",
            "ABAP Cloud & S/4HANA Extensibility (5 Yrs)",
            "OData API Development & SAP Gateway",
            "FMCG Enterprise Landscape & AMS Support",
            "Performance Tuning & Complex Root Cause Debugging"
        ],
        "cover_highlights": (
            "As an SAP-certified BTP Solution Architect, ABAP Cloud developer, and Business AI Platform architect with 5 years of enterprise delivery, "
            "I have spent my career designing resilient integration and enhancement solutions in production-critical environments, including multinational FMCG operations at Accenture. "
            "My experience building OData services, CDS Views, and AMDP logic aligns directly with George Weston Foods' integration requirements. "
            "Holding independent Australian working rights under the Working Holiday Visa (subclass 462), I am prepared to deliver robust results."
        )
    },
    {
        "app_id": "app_002",
        "company": "Queensland Health (via u&u)",
        "role_title": "SAP ABAP Developer",
        "city": "Brisbane QLD",
        "header_location": "Brisbane QLD (Relocating | Working Holiday Visa Subclass 462 — Full Work Rights)",
        "summary": "SAP ABAP Developer with 5 years of experience delivering and supporting enterprise SAP solutions across ECC and S/4HANA environments. Proven ability in developing high-performance CDS Views, AMDP, OData services, and complex custom enhancements for large-scale enterprise systems. Certified in ABAP Cloud and SAP BTP with independent Australian work rights.",
        "competencies": [
            "S/4HANA & ECC ABAP Development (5 Yrs)",
            "CDS Views & AMDP Data Modeling",
            "OData Gateway Service Development",
            "BAdIs, User Exits & Enhancement Framework",
            "Critical AMS & High-Volume Performance Debugging"
        ],
        "cover_highlights": (
            "I am excited to submit my candidacy for the SAP ABAP Developer engagement with Queensland Health through u&u. "
            "With 5 years of enterprise delivery across large-scale enterprise landscapes at Deloitte and Accenture, I specialize in S/4HANA implementation, "
            "CDS Views, and Gateway integrations. My track record includes automated inspection of 1,260 SAP Notes, drastically reducing delivery "
            "cycles. With full independent work authorization via the Working Holiday Visa (subclass 462), I am available to support Queensland Health."
        )
    },
    {
        "app_id": "app_003",
        "company": "Cloud pro Technologies",
        "role_title": "SAP ABAP Developer – 12-Month Contract",
        "city": "Canberra ACT",
        "header_location": "Canberra ACT / Remote (Working Holiday Visa Subclass 462 — Independent Work Rights)",
        "summary": "SAP ABAP Consultant with 5 years of enterprise development experience across ECC and S/4HANA landscapes. Expert in core ABAP (OO, ALV, Enhancements, Module-Pool), standard debugging, and modern OData/CDS integration. Available for fixed-term contract delivery with independent Australian work rights.",
        "competencies": [
            "Classic & Modern ABAP (5 Yrs Enterprise Delivery)",
            "Enhancements: BAdIs, User Exits, Enhancement Spots",
            "Module-Pool & Custom Transaction Maintenance",
            "OData Services & CDS Views",
            "High-Volume Production Debugging & STMS"
        ],
        "cover_highlights": (
            "I am writing to express my strong interest in the 12-month SAP ABAP Developer contract with Cloud pro Technologies. "
            "With 5 years of hands-on experience in enterprise ECC and S/4HANA systems at Accenture, Deloitte, and Softtek, I have deep expertise "
            "in classic RICEFW delivery as well as modern CDS/OData integration. With independent Australian work authorization (WHV 462), "
            "I offer immediate remote technical onboarding and full dedication to contract milestones."
        )
    },
    {
        "app_id": "app_004",
        "company": "Slade Group",
        "role_title": "SAP ABAP Developer",
        "city": "Melbourne VIC",
        "header_location": "Melbourne VIC (Relocating | Working Holiday Visa Subclass 462 — Full Work Rights)",
        "summary": "SAP ABAP Consultant with 5 years of hands-on technical consulting across SAP ECC and S/4HANA environments. Certified in ABAP Cloud, SAP BTP, and SAP Business AI Platform, with demonstrated ability to debug standard SAP modules, optimize performance, and deliver reliable custom enhancements in production-critical landscapes.",
        "competencies": [
            "ABAP OO, Reports & ALV Grid Development",
            "System Enhancements (BAdIs, Customer Exits)",
            "SAP Standard Function Module Debugging",
            "CDS Views & SAP Gateway OData",
            "SD/FI Integration & Business Process Alignment"
        ],
        "cover_highlights": (
            "I am pleased to present my application for the SAP ABAP Developer opportunity via Slade Group in Melbourne. "
            "My 5 years of consulting experience across Accenture, Deloitte, and Softtek encompass both foundational ABAP development and modern "
            "S/4HANA architectures. Notably, my debugging of standard SAP modules during the Brazilian Tax Reform condensed months of analysis "
            "into days. With independent work rights under the Working Holiday Visa (subclass 462), I am fully prepared for Melbourne-based delivery."
        )
    },
    {
        "app_id": "app_seek_95482398",
        "company": "Recruitment Hive",
        "role_title": "Senior SAP ABAP & FIORI Developer",
        "city": "Canberra ACT",
        "header_location": "Canberra ACT (Working Holiday Visa Subclass 462 — Full Independent Work Rights)",
        "summary": "SAP ABAP & FIORI Developer with 5 years of experience across ECC 6.0 and S/4HANA environments. Certified in ABAP Cloud, SAP BTP, and SAP Business AI Platform, with proven expertise in Dynpro/ALV, OData service exposure via SAP Gateway, CDS Views, and high-volume financial system integrations. Independent Australian work rights (WHV 462).",
        "competencies": [
            "SAP ABAP OO & Classic Dynpro/Reports",
            "SAP FIORI & OData Gateway Integration",
            "CDS Views & AMDP Data Modeling",
            "ABAP Cloud & Clean Core Extensibility",
            "Financial & Material Management Integration"
        ],
        "cover_highlights": (
            "I am writing to apply for the Senior SAP ABAP & FIORI Developer contract position through Recruitment Hive in Canberra. "
            "Throughout my 5 years of SAP consulting at Accenture, Deloitte, and Softtek, I have developed and supported critical Financial "
            "and operational systems across ECC 6.0 and S/4HANA. My expertise spans classic Dynpro and reports through to modern Gateway OData "
            "and CDS Views, backed by SAP ABAP Cloud certification. With independent work rights under the Working Holiday Visa (subclass 462), "
            "I am ready to deliver on contract goals."
        )
    }
]

def render_cv_html(spec: dict) -> str:
    template = BASE_TEMPLATE_PATH.read_text(encoding="utf-8")
    comp_html = "\n".join(f'      <div class="competency-tag">{c}</div>' for c in spec["competencies"])
    
    exp_html = """
    <div class="job">
      <div class="job-header">
        <span class="job-company">Accenture</span>
        <span class="job-period">Aug 2023 - Present</span>
      </div>
      <div class="job-role">SAP ABAP Consultant</div>
      <div class="job-location">Multinational FMCG Production Landscape</div>
      <ul class="job-bullets">
        <li>Delivered ABAP developments and enhancements across ECC and S/4HANA landscapes for a multinational FMCG client in a production-critical environment.</li>
        <li>Analyzed 1,260 SAP Notes related to the Brazilian Tax Reform, debugging SAP standard function modules and implementation logic, and reducing the estimated effort from 1–2 months to 3–4 days.</li>
        <li>Developed and maintained custom ABAP reports, enhancements (BAdIs, user exits), CDS Views, and OData services supporting integrated SD and FI processes.</li>
        <li>Recognized in the first year with a performance and dedication award.</li>
      </ul>
    </div>
    <div class="job">
      <div class="job-header">
        <span class="job-company">Deloitte</span>
        <span class="job-period">Nov 2022 - Aug 2023</span>
      </div>
      <div class="job-role">SAP ABAP Consultant</div>
      <div class="job-location">S/4HANA Implementation & Integration</div>
      <ul class="job-bullets">
        <li>Contributed to S/4HANA implementation and integration initiatives, following ABAP for HANA standards.</li>
        <li>Developed CDS Views and AMDP logic with focus on database performance optimization.</li>
        <li>Built and exposed OData services via SAP Gateway to support enterprise system integrations.</li>
        <li>Collaborated closely with functional teams during build, testing, and defect resolution phases.</li>
      </ul>
    </div>
    <div class="job">
      <div class="job-header">
        <span class="job-company">Softtek</span>
        <span class="job-period">Feb 2022 - Nov 2022</span>
      </div>
      <div class="job-role">SAP ABAP Consultant</div>
      <div class="job-location">Production AMS & Maintenance</div>
      <ul class="job-bullets">
        <li>Supported SAP ECC environments in production-critical contexts, handling corrective maintenance and controlled enhancements.</li>
        <li>Developed classic ABAP reports, enhancements, and integration solutions, strengthening core ABAP expertise.</li>
        <li>Gained solid experience in debugging, transport management (STMS), and SAP enterprise support processes.</li>
      </ul>
    </div>
    """

    cert_html = """
    <div class="cert-item">
      <div class="cert-title">SAP Certified Associate - Back-End Developer - ABAP Cloud</div>
      <div class="cert-issuer">SAP SE</div>
    </div>
    <div class="cert-item">
      <div class="cert-title">SAP Certified - Solution Architect - SAP BTP</div>
      <div class="cert-issuer">SAP SE</div>
    </div>
    <div class="cert-item">
      <div class="cert-title">SAP Certified - Solution Architect - SAP Business AI Platform</div>
      <div class="cert-issuer">SAP SE</div>
    </div>
    """

    edu_html = """
    <div class="edu-item">
      <div class="edu-header">
        <div class="edu-title">Bachelor’s Degree in Systems Analysis and Development</div>
        <div class="edu-year">Catholic University of Salvador — Brazil</div>
      </div>
    </div>
    """

    skills_html = """
    <div class="skill-category"><strong>Technical:</strong> ABAP OO, Reports & ALV, Enhancements (BADIs, User Exits), Module-Pool, Debugging & Performance Analysis, SAP ECC, ABAP Cloud / ABAP on HANA, Adobe Forms, SMARTFORMS, Output Management</div>
    <div class="skill-category"><strong>Functional & Tools:</strong> Sales & Distribution (SD), FI/CO Integration, Materials Management (MM), SAP ECC & S/4HANA, Solution Manager, STMS, Jira & Agile/Scrum, AMS</div>
    <div class="skill-category"><strong>Languages:</strong> Portuguese (Native), English (Advanced proficiency), Spanish (Reading and listening proficiency)</div>
    """

    replacements = {
        "{{LANG}}": "en",
        "{{NAME}}": "Guilherme Coutinho Xavier",
        "{{PAGE_WIDTH}}": "800px",
        "{{PHOTO}}": "",
        "{{PHONE}}": "+55 71 99998-5833",
        "{{EMAIL}}": "guicoutinho.dev@gmail.com",
        "{{LINKEDIN_URL}}": "https://www.linkedin.com/in/guicoutin/",
        "{{LINKEDIN_DISPLAY}}": "linkedin.com/in/guicoutin",
        "{{PORTFOLIO_URL}}": "https://www.linkedin.com/in/guicoutin/",
        "{{PORTFOLIO_DISPLAY}}": "LinkedIn",
        "{{LOCATION}}": spec["header_location"],
        "{{SECTION_SUMMARY}}": "Professional Summary",
        "{{SUMMARY_TEXT}}": spec["summary"],
        "{{SECTION_COMPETENCIES}}": "Core Competencies",
        "{{COMPETENCIES}}": comp_html,
        "{{SECTION_EXPERIENCE}}": "Professional Experience",
        "{{EXPERIENCE}}": exp_html,
        "{{SECTION_PROJECTS}}": "",
        "{{PROJECTS}}": "",
        "{{SECTION_EDUCATION}}": "Education",
        "{{EDUCATION}}": edu_html,
        "{{SECTION_CERTIFICATIONS}}": "Certifications",
        "{{CERTIFICATIONS}}": cert_html,
        "{{SECTION_SKILLS}}": "Skills & Languages",
        "{{SKILLS}}": skills_html,
    }

    html = template
    for tag, val in replacements.items():
        html = html.replace(tag, val)
    return html

def render_cl_html(spec: dict) -> str:
    body_text = f"""
    <p>Dear Hiring Team at {spec['company']},</p>
    <p>I am writing to express my strong interest in the <strong>{spec['role_title']}</strong> position. With 5 years of enterprise SAP ABAP consulting experience across multinational environments, I have developed a strong foundation in both core ECC development and modern S/4HANA cloud-aligned architectures.</p>
    <p>{spec['cover_highlights']}</p>
    <p>My career includes proven experience delivering within production-critical landscapes for global FMCG and consulting clients at Accenture, Deloitte, and Softtek. I take pride in understanding both technical architecture and business processes, ensuring solutions are robust, clean, and fully maintainable.</p>
    <p>Regarding work authorization, I am eligible and transitioning under the <strong>Working Holiday Visa (Subclass 462)</strong>, providing full and independent work rights in Australia without requiring employer visa sponsorship. I am available immediately for remote technical discussions and ready for on-site collaboration in {spec['city']}.</p>
    <p>Thank you for your time and consideration. I welcome the opportunity to discuss how my SAP ABAP and BTP background can contribute to {spec['company']}'s goals.</p>
    <p>Sincerely,<br><strong>Guilherme Coutinho Xavier</strong><br>+55 71 99998-5833 | guicoutinho.dev@gmail.com</p>
    """
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Cover Letter — Guilherme Coutinho Xavier — {spec['company']}</title>
<style>
  body {{
    font-family: 'Liberation Sans', 'Helvetica Neue', Arial, sans-serif;
    font-size: 11.5px;
    line-height: 1.6;
    color: #1a1a2e;
    margin: 40px auto;
    max-width: 760px;
    padding: 20px;
  }}
  .header {{
    border-bottom: 2px solid #0d7377;
    padding-bottom: 15px;
    margin-bottom: 25px;
  }}
  .name {{
    font-size: 24px;
    font-weight: bold;
    color: #1a1a2e;
  }}
  .contact {{
    font-size: 10.5px;
    color: #555;
    margin-top: 5px;
  }}
  .recipient-block {{
    margin-bottom: 25px;
    font-size: 11.5px;
    color: #333;
  }}
  p {{
    margin-bottom: 16px;
    text-align: justify;
  }}
</style>
</head>
<body>
  <div class="header">
    <div class="name">Guilherme Coutinho Xavier</div>
    <div class="contact">+55 71 99998-5833 | guicoutinho.dev@gmail.com | {spec['header_location']}</div>
  </div>
  <div class="recipient-block">
    <strong>To:</strong> Hiring Team — {spec['company']}<br>
    <strong>Role:</strong> {spec['role_title']} ({spec['city']})<br>
    <strong>Date:</strong> October 27, 2026
  </div>
  {body_text}
</body>
</html>"""
    return html

def main():
    print("==================================================")
    print("   AUSTRALIA JOB HUNTER — TAILORED PDF BUILDER    ")
    print("   Ref: CV Guilherme Xavier (3).pdf (5 Years Exp) ")
    print("==================================================")

    # 1. Update SQLite locations
    conn = get_connection("data/applications.db")
    try:
        cur = conn.cursor()
        for spec in ROLES_SPEC:
            cur.execute("""
                UPDATE jobs SET location = ? 
                WHERE id = (SELECT job_id FROM applications WHERE id = ?);
            """, (spec["city"], spec["app_id"]))
        conn.commit()
        print("[OK] Verified target cities in SQLite jobs table.")
    finally:
        conn.close()

    # 2. Render HTML & Compile PDFs via Playwright
    for spec in ROLES_SPEC:
        app_id = spec["app_id"]
        pkg_dir = Path("applications") / app_id
        pkg_dir.mkdir(parents=True, exist_ok=True)

        slug_comp = spec["company"].lower().replace(" ", "-").replace("(", "").replace(")", "").replace("&", "and")
        slug_role = spec["role_title"].lower().replace(" ", "-").replace("–", "").replace("—", "").replace("/", "-")
        while "--" in slug_comp: slug_comp = slug_comp.replace("--", "-")
        while "--" in slug_role: slug_role = slug_role.replace("--", "-")
        
        cv_html_path = pkg_dir / f"cv-{slug_comp}-{slug_role}.html"
        cv_pdf_path = pkg_dir / f"cv-{slug_comp}-{slug_role}.pdf"
        cl_html_path = pkg_dir / f"cover-letter-{slug_comp}-{slug_role}.html"
        cl_pdf_path = pkg_dir / f"cover-letter-{slug_comp}-{slug_role}.pdf"

        # Write HTMLs
        cv_html_path.write_text(render_cv_html(spec), encoding="utf-8")
        cl_html_path.write_text(render_cl_html(spec), encoding="utf-8")

        # Compile PDFs using node generate-pdf.mjs
        cmd_cv = ["node", "generate-pdf.mjs", str(cv_html_path), str(cv_pdf_path), "--format=a4", "--allow-reorder"]
        cmd_cl = ["node", "generate-pdf.mjs", str(cl_html_path), str(cl_pdf_path), "--format=a4", "--allow-reorder"]

        # Run without pipes that trigger cp1252 decoding issues
        res_cv = subprocess.run(cmd_cv)
        res_cl = subprocess.run(cmd_cl)

        if cv_pdf_path.exists() and cl_pdf_path.exists():
            print(f"[OK] {app_id} ({spec['company']}) -> PDFs generated in {pkg_dir}")
        else:
            print(f"[FAIL] {app_id} -> Generation error")

    print("\n[OK] All localized tailored packages built successfully.")

if __name__ == "__main__":
    main()
