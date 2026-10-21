import sys
import os
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super(NumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_number(self, page_count):
        if self._pageNumber == 1:
            return  # Suppress page number on cover page
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        
        # Header
        self.drawString(54, 750, "Inventory Intelligence Dashboard (IID) — Technical Report")
        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(54, 742, 558, 742)
        
        # Footer
        text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 40, text)
        self.drawString(54, 40, "CONFIDENTIAL — FOR ACADEMIC & COMMERCIAL EVALUATION")
        self.line(54, 52, 558, 52)
        self.restoreState()

def build_pdf(dest_path):
    doc = SimpleDocTemplate(
        dest_path,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch
    )

    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        "CoverTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=26,
        leading=32,
        textColor=colors.HexColor("#0f172a"),
        alignment=0,
        spaceAfter=15
    )
    
    subtitle_style = ParagraphStyle(
        "CoverSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#475569"),
        alignment=0,
        spaceAfter=30
    )
    
    h1_style = ParagraphStyle(
        "Heading1_Custom",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1e3a8a"),
        spaceBefore=18,
        spaceAfter=10,
        keepWithNext=True
    )

    h2_style = ParagraphStyle(
        "Heading2_Custom",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True
    )
    
    body_style = ParagraphStyle(
        "Body_Custom",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#334155"),
        spaceAfter=8
    )

    code_style = ParagraphStyle(
        "Code_Custom",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#0f172a"),
        backColor=colors.HexColor("#f1f5f9"),
        borderColor=colors.HexColor("#cbd5e1"),
        borderWidth=0.5,
        borderPadding=6,
        spaceAfter=8
    )

    elements = []

    # ------------------ COVER PAGE ------------------
    elements.append(Spacer(1, 1.2 * inch))
    elements.append(Paragraph("INVENTORY INTELLIGENCE<br/>DASHBOARD (IID)", title_style))
    elements.append(Paragraph("A World-Class Decision Support System (DSS) integrating Stochastic Calculations, Dynamic Multi-Criteria Classifications, and ERP Readiness Gates.", subtitle_style))
    
    metadata_data = [
        [Paragraph("<b>Document Scope:</b>", body_style), Paragraph("MPhil Thesis Reference & Operational Guide", body_style)],
        [Paragraph("<b>Primary Author:</b>", body_style), Paragraph("Lead Solutions Architect & Systems Engineer", body_style)],
        [Paragraph("<b>Authentication Access:</b>", body_style), Paragraph("Username: <b>admin</b> / Password: <b>admin123</b>", body_style)],
        [Paragraph("<b>Database Engine:</b>", body_style), Paragraph("SQLite (Local) / PostgreSQL Ready", body_style)],
        [Paragraph("<b>Date of Release:</b>", body_style), Paragraph(datetime.now().strftime("%Y-%m-%d"), body_style)]
    ]
    t_meta = Table(metadata_data, colWidths=[2 * inch, 4 * inch])
    t_meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
    ]))
    elements.append(t_meta)
    elements.append(PageBreak())

    # ------------------ SECTION 1 ------------------
    elements.append(Paragraph("1. System Abstract & Design Architecture", h1_style))
    elements.append(Paragraph(
        "The Inventory Intelligence Dashboard (IID) is designed as a night-operations command center for modern supply chains. "
        "It acts as a decision support framework, transforming raw warehouse datasets into actionable operational instructions. "
        "Built on a decoupled SPA (Single Page Application) frontend layout utilizing HTML, CSS, and vanilla JavaScript, it connects "
        "to a lightweight and high-throughput Python API served by FastAPI and backed by SQLAlchemy ORM.",
        body_style
    ))
    elements.append(Paragraph(
        "For deployment ease and thesis demonstration, IID mounts the frontend files directly onto the FastAPI server process, "
        "allowing the entire platform to start with a single console command. The system automatically initializes and structures "
        "a local SQLite database (`iid_local.db`) if not present, seeding it with the initial user access credentials.",
        body_style
    ))
    
    # ------------------ SECTION 2 ------------------
    elements.append(Paragraph("2. User Access & Security Protocols", h1_style))
    elements.append(Paragraph(
        "User operations are secured behind a custom authentication gateway in `backend/api/auth.py`. "
        "To eliminate heavy framework dependencies and compile-time issues on target environments, password security is implemented "
        "via high-strength SHA-256 hashing. Passwords are salted with a standard system salt before hashing.",
        body_style
    ))
    elements.append(Paragraph(
        "<b>Seeding Default Credentials:</b> On server startup, the database is audited and automatically seeds a default user if the table is empty.",
        body_style
    ))
    elements.append(Paragraph("Default Credentials:", h2_style))
    elements.append(Paragraph(
        "• Username: <b>admin</b><br/>"
        "• Password: <b>admin123</b>",
        body_style
    ))
    elements.append(Paragraph("How to Modify Credentials Programmatically:", h2_style))
    elements.append(Paragraph(
        "To change the pre-seeded admin credentials before building, navigate to `backend/api/auth.py` and modify the "
        "`seed_admin_user` function:",
        body_style
    ))
    elements.append(Paragraph(
        "def seed_admin_user(db: Session):<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;admin = db.query(User).filter(User.username == \"NEW_USERNAME\").first()<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;if not admin:<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;admin = User(<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;username=\"NEW_USERNAME\",<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;password_hash=hash_password(\"NEW_PASSWORD\"),<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;role=\"admin\"<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;)<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;db.add(admin)<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;db.commit()",
        code_style
    ))
    elements.append(Paragraph(
        "<i>Note: If you have already run the application once, delete the SQLite file `backend/database/iid_local.db` before restarting. "
        "The system will recreate the database and seed it with your newly specified credentials automatically.</i>",
        body_style
    ))
    elements.append(PageBreak())

    # ------------------ SECTION 3 ------------------
    elements.append(Paragraph("3. Optimization & Academic Formulation Models", h1_style))
    elements.append(Paragraph(
        "The mathematical optimization engine lies in `backend/optimization/stock_optimizer.py` and `backend/analytics/analytics_engine.py`. "
        "It supports advanced academic and doctoral research inventory optimization formulations:",
        body_style
    ))
    
    elements.append(Paragraph("Economic Order Quantity (EOQ)", h2_style))
    elements.append(Paragraph(
        "$$EOQ = \\sqrt{\\frac{2 \\cdot D \\cdot S}{H}}$$<br/>"
        "Where:<br/>"
        "• <b>D</b>: Annual demand in units (Daily Average Demand × 365)<br/>"
        "• <b>S</b>: Fixed order setup cost (configured at a standard $50.00)<br/>"
        "• <b>H</b>: Holding cost per unit per year (calculated as unit cost × 20% annual holding rate)",
        body_style
    ))

    elements.append(Paragraph("Stochastic Safety Stock (SS) under Variance", h2_style))
    elements.append(Paragraph(
        "$$SS = Z \\times \\sqrt{L \\cdot \\sigma_D^2 + D^2 \\cdot \\sigma_{LT}^2}$$<br/>"
        "Where:<br/>"
        "• <b>Z</b>: Standard service factor (fixed at 1.65 for a 95% service fill rate target)<br/>"
        "• <b>L</b>: Vendor lead time in days<br/>"
        "• <b>D</b>: Daily average demand in units<br/>"
        "• <b>sigma_D</b>: Standard deviation of demand (stochastically calculated at 25% of daily demand)<br/>"
        "• <b>sigma_LT</b>: Standard deviation of lead time (stochastically calculated at 15% of lead time)",
        body_style
    ))

    elements.append(Paragraph("Stochastic Reorder Point (ROP)", h2_style))
    elements.append(Paragraph(
        "$$ROP = (D \\times L) + SS$$<br/>"
        "Triggers replenishment orders whenever the quantity on hand falls below the ROP threshold.",
        body_style
    ))

    elements.append(Paragraph("Multi-Criteria Decision Classifications", h2_style))
    elements.append(Paragraph(
        "• <b>ABC Analysis</b>: Classifies inventory by annual cost usage: <b>A</b> (top 80% usage), <b>B</b> (next 15%), <b>C</b> (remaining 5%).<br/>"
        "• <b>XYZ Analysis</b>: Segmented by demand predictability (Coefficient of Variation): <b>X</b> (CV ≤ 10%), <b>Y</b> (CV ≤ 30%), <b>Z</b> (CV > 30%).<br/>"
        "• <b>FSN Analysis</b>: Classifies inventory speed based on turnover ratio: <b>Fast (F)</b>, <b>Slow (S)</b>, <b>Non-moving (N)</b>.<br/>"
        "• <b>VED Analysis</b>: Segments inventory by operational criticality buffer: <b>Vital (V)</b>, <b>Essential (E)</b>, <b>Desirable (D)</b>.",
        body_style
    ))
    elements.append(PageBreak())

    # ------------------ SECTION 4 ------------------
    elements.append(Paragraph("4. Editable and Touchable Telemetry", h1_style))
    elements.append(Paragraph(
        "Every single table element rendered across the pages (including Overstock, Expiry, Safety Stock, Warehouses, and Suppliers) "
        "is wired with the `touchable-row` class. Clicking any row opens a Control Tower details modal.",
        body_style
    ))
    elements.append(Paragraph(
        "From this control tower, users can edit parameters in real time. Clicking <b>Save Changes</b> submits a `PUT` request to `/api/update/*`, "
        "committing the modifications to the database, refreshing the active view, and re-calculating the global KPIs. "
        "Clicking <b>Delete Entity</b> prompts a double confirmation check, executing a `DELETE` request, clearing the warehouse cache, and refreshing the active view.",
        body_style
    ))

    # ------------------ SECTION 5 ------------------
    elements.append(Paragraph("5. Step-by-Step Portability & Deployment Guide", h1_style))
    elements.append(Paragraph(
        "To run this application on any other laptop, follow this guide precisely. No build compilation or heavy tools are required.",
        body_style
    ))
    
    elements.append(Paragraph("Step A: Prepare Python Environment", h2_style))
    elements.append(Paragraph(
        "1. Download and install **Python 3.10+** (ensure you check 'Add Python to PATH' during installation).<br/>"
        "2. Copy the entire project folder (`Inventory-Intelligence-Dashboard`) onto the new laptop.",
        body_style
    ))

    elements.append(Paragraph("Step B: Configure the Local Virtual Environment", h2_style))
    elements.append(Paragraph(
        "1. Open Command Prompt (cmd) or PowerShell on the laptop.<br/>"
        "2. Navigate into the `backend/` directory:<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;<b>cd D:\\path\\to\\Inventory-Intelligence-Dashboard\\backend</b><br/>"
        "3. Create a clean virtual environment:<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;<b>python -m venv venv</b><br/>"
        "4. Activate the virtual environment:<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;Windows cmd: <b>.\\venv\\Scripts\\activate</b><br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;PowerShell: <b>.\\venv\\Scripts\\activate.ps1</b><br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;macOS/Linux: <b>source venv/bin/activate</b><br/>"
        "5. Install all libraries locally from PyPI:<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;<b>pip install -r requirements.txt</b>",
        body_style
    ))

    elements.append(Paragraph("Step C: Start the Dashboard", h2_style))
    elements.append(Paragraph(
        "1. Run the FastAPI server in the active console:<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;<b>uvicorn main:app --reload --port 8000</b><br/>"
        "2. Launch any modern browser and open:<br/>"
        "&nbsp;&nbsp;&nbsp;&nbsp;<b>http://127.0.0.1:8000</b><br/>"
        "3. Enter username: <b>admin</b> and password: <b>admin123</b> to log in and build your control tower.",
        body_style
    ))

    doc.build(elements, canvasmaker=NumberedCanvas)
    print("System Description Report PDF generated successfully!")

if __name__ == "__main__":
    pdf_path = r"D:\Downlods\DASHBOARD\Inventory-Intelligence-Dashboard\System_Description_Report.pdf"
    build_pdf(pdf_path)
