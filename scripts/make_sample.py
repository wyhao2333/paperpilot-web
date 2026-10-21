"""Generate an original, explicitly synthetic test PDF. Requires requirements-dev.txt."""
import sys
import textwrap
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]


def make_sample(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setTitle("Coastal landscapes: a synthetic reading exercise")
    pdf.setAuthor("PaperPilot Web contributors")

    def header(page):
        pdf.setFont("Helvetica", 8)
        pdf.setFillColorRGB(.45, .5, .4)
        pdf.drawString(48, 774, "PAPERPILOT | SYNTHETIC TEST DOCUMENT | NOT A RESEARCH PAPER")
        pdf.drawRightString(564, 22, str(page))
        pdf.setFillColorRGB(.15, .22, .16)

    def paragraph(text, x, y, width=90, size=11):
        pdf.setFont("Times-Roman", size)
        for line in textwrap.wrap(text, width):
            pdf.drawString(x, y, line)
            y -= size * 1.4
        return y - 16

    header(1)
    pdf.setFont("Times-Bold", 23)
    pdf.drawString(48, 720, "Coastal landscapes:")
    pdf.drawString(48, 690, "a synthetic reading exercise")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(48, 657, "An original example for testing paragraph extraction and bilingual reading")
    pdf.setFont("Times-Bold", 14)
    pdf.drawString(48, 614, "Abstract")
    y = paragraph("This is a synthetic demonstration document created for PaperPilot Web. It contains no measured observations, experimental results, or real citations. Its purpose is to let readers inspect paragraph ordering, translation requests, source-page navigation, and saved reading progress before uploading their own literature.", 48, 591)
    pdf.setFont("Times-Bold", 14)
    pdf.drawString(48, y - 8, "1. Introduction")
    y = paragraph("Coastal landscapes change through interacting processes. Waves redistribute sediment, tides alter water levels, and human activities can modify sediment pathways. A reader needs to distinguish a physical explanation from an association reported in a particular study.", 48, y - 33)
    y = paragraph("A shoreline position is a measurement with a definition. Different indicators, acquisition dates, and reference water levels can produce different mapped positions. Comparing studies therefore requires attention to the observation method and the spatial and temporal support of each measurement.", 48, y)
    paragraph("When a translated sentence appears uncertain, return to the original paragraph and its context. Qualifiers such as may, suggests, and is associated with should remain visible in the translated interpretation. Translation should preserve the evidence boundary of the source text.", 48, y)
    pdf.showPage()
    header(2)
    pdf.setFont("Times-Bold", 17)
    pdf.drawString(48, 727, "2. Methods (synthetic example)")
    left = [
        "LEFT COLUMN FIRST. This page uses two columns. All paragraphs in the left column should appear before the right column in the reading view. The text is deliberately labeled so that ordering errors can be identified without assuming that the parser is correct.",
        "A hypothetical workflow begins by recording the coordinate system, units, acquisition date, and vertical reference of every source. These metadata fields describe the measurements; they do not establish that two datasets are interchangeable.",
        "Validation should retain difficult cases. Large errors may identify a mismatch in spatial support, an incorrect reference level, or an extraction failure. Removing these cases without documenting the reason can make an assessment appear more reliable than it is.",
    ]
    right = [
        "RIGHT COLUMN SECOND. These paragraphs should follow the entire left column. The example tests reading order only. Successful extraction of this synthetic layout does not prove equivalent performance for all publisher templates.",
        "Uncertainty can arise from both the observations and the processing procedure. A useful reading note records what was measured, the comparison that was performed, and the conclusions that the available evidence cannot support.",
        "For long documents, paragraph translations should be saved incrementally. If a network request fails, already completed translations remain available and the failed paragraph can be retried. This is a software behavior to verify, not a scientific result.",
    ]
    for x, texts in [(48, left), (318, right)]:
        y = 690
        for text in texts:
            y = paragraph(text, x, y, width=47, size=10)
    pdf.showPage()
    header(3)
    pdf.setFont("Times-Bold", 18)
    pdf.drawString(48, 725, "3. Figures and source checking")
    paragraph("Figures, tables, and equations should be checked in the original PDF. The reflow view prioritizes readable text; it does not reconstruct every visual element. The schematic below contains no observations and must not be interpreted as a trend estimate.", 48, 690)
    pdf.setStrokeColorRGB(.35, .5, .3)
    pdf.rect(90, 370, 430, 190)
    pdf.line(120, 400, 120, 535)
    pdf.line(120, 400, 485, 400)
    pdf.setFont("Helvetica", 12)
    pdf.drawCentredString(305, 482, "ILLUSTRATIVE SCHEMATIC - NO DATA")
    pdf.setFont("Helvetica", 9)
    pdf.drawCentredString(305, 453, "Use the original-page panel to inspect this figure.")
    y = paragraph("Figure 1. A synthetic rectangle and axes for checking source-page rendering. No observations or statistical estimates are shown.", 65, 345, width=96, size=10)
    pdf.setFont("Times-Bold", 14)
    pdf.drawString(48, y - 15, "4. Reading checklist")
    paragraph("Check that the title, section headings, and body paragraphs are readable. Open a paragraph's original page, save a note, reload the browser, and confirm that the reading position is restored. Configure a real translation provider only when you are ready to test actual translations.", 48, y - 41)
    pdf.save()


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples" / "paperpilot-sample.pdf"
    make_sample(output)
    print(output)
