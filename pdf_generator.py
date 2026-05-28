"""Parcel Ops Control Tower — PDF document generator.

Generates PDF documents for customs operations:
- Amendment request letters to customs authorities
- Carrier notification templates
- Internal escalation reports

Usage:
    from pdf_generator import generate_amendment_letter, generate_carrier_notification, generate_escalation_report
"""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from typing import Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _get_styles():
    """Create custom paragraph styles for documents."""
    styles = getSampleStyleSheet()
    
    styles.add(ParagraphStyle(
        name='DocTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=HexColor('#0E1116'),
        spaceAfter=20,
        alignment=TA_LEFT,
    ))
    
    styles.add(ParagraphStyle(
        name='DocSubtitle',
        parent=styles['Heading2'],
        fontSize=12,
        textColor=HexColor('#6E7680'),
        spaceAfter=12,
        alignment=TA_LEFT,
    ))
    
    styles.add(ParagraphStyle(
        name='DocBody',
        parent=styles['Normal'],
        fontSize=10,
        textColor=HexColor('#1F242B'),
        spaceAfter=8,
        leading=14,
    ))
    
    styles.add(ParagraphStyle(
        name='DocLabel',
        parent=styles['Normal'],
        fontSize=9,
        textColor=HexColor('#6E7680'),
        spaceAfter=2,
        fontName='Helvetica-Bold',
    ))
    
    styles.add(ParagraphStyle(
        name='DocValue',
        parent=styles['Normal'],
        fontSize=10,
        textColor=HexColor('#0E1116'),
        spaceAfter=10,
    ))
    
    styles.add(ParagraphStyle(
        name='DocFooter',
        parent=styles['Normal'],
        fontSize=8,
        textColor=HexColor('#98A0A8'),
        alignment=TA_CENTER,
    ))
    
    return styles


# ---------------------------------------------------------------------------
# Amendment Letter
# ---------------------------------------------------------------------------

def generate_amendment_letter(
    batch_id: str,
    field: str,
    old_value: str,
    new_value: str,
    reason: str,
    carrier: str = "",
    origin: str = "",
    parcel_count: int = 0,
    hs_code: str = "",
) -> BytesIO:
    """Generate a PDF amendment request letter to customs authorities."""
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
    )
    
    styles = _get_styles()
    elements = []
    
    # Header
    elements.append(Paragraph("CUSTOMS DECLARATION AMENDMENT REQUEST", styles['DocTitle']))
    elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", styles['DocSubtitle']))
    elements.append(Spacer(1, 0.5*cm))
    
    # Batch information table
    batch_data = [
        ['Batch ID:', batch_id],
        ['Carrier:', carrier or 'N/A'],
        ['Origin:', origin or 'N/A'],
        ['Parcel Count:', str(parcel_count) if parcel_count else 'N/A'],
        ['HS Code:', hs_code or 'N/A'],
    ]
    
    batch_table = Table(batch_data, colWidths=[4*cm, 10*cm])
    batch_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), HexColor('#6E7680')),
        ('TEXTCOLOR', (1, 0), (1, -1), HexColor('#0E1116')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    
    elements.append(batch_table)
    elements.append(Spacer(1, 0.8*cm))
    
    # Amendment details
    elements.append(Paragraph("AMENDMENT DETAILS", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    
    elements.append(Paragraph("Field:", styles['DocLabel']))
    elements.append(Paragraph(field, styles['DocValue']))
    
    elements.append(Paragraph("Current Value:", styles['DocLabel']))
    elements.append(Paragraph(old_value, styles['DocValue']))
    
    elements.append(Paragraph("Proposed Value:", styles['DocLabel']))
    elements.append(Paragraph(f"<b>{new_value}</b>", styles['DocValue']))
    
    elements.append(Paragraph("Reason for Amendment:", styles['DocLabel']))
    elements.append(Paragraph(reason, styles['DocValue']))
    
    elements.append(Spacer(1, 1*cm))
    
    # Declaration
    elements.append(Paragraph("DECLARATION", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    
    declaration_text = (
        "We hereby request amendment of the customs declaration for the above-referenced batch. "
        "The information provided is accurate to the best of our knowledge. "
        "We understand that false declarations may result in penalties or seizure of goods."
    )
    elements.append(Paragraph(declaration_text, styles['DocBody']))
    
    elements.append(Spacer(1, 1.5*cm))
    
    # Signature block
    sig_data = [
        ['Authorized Signature:', '___________________________'],
        ['Name:', '___________________________'],
        ['Title:', '___________________________'],
        ['Date:', date.today().strftime('%Y-%m-%d')],
    ]
    
    sig_table = Table(sig_data, colWidths=[4*cm, 10*cm])
    sig_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), HexColor('#6E7680')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    
    elements.append(sig_table)
    
    # Footer
    elements.append(Spacer(1, 1*cm))
    elements.append(Paragraph(
        "Parcel Ops Control Tower — Automated Document Generation",
        styles['DocFooter']
    ))
    
    doc.build(elements)
    buffer.seek(0)
    return buffer


# ---------------------------------------------------------------------------
# Carrier Notification
# ---------------------------------------------------------------------------

def generate_carrier_notification(
    batch_id: str,
    carrier: str,
    issue_type: str,
    severity: str,
    detail: str,
    required_action: str,
    deadline: Optional[str] = None,
    contact_email: str = "ops@parcel-ops.example.com",
) -> BytesIO:
    """Generate a PDF carrier notification letter."""
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
    )
    
    styles = _get_styles()
    elements = []
    
    # Header
    severity_color = {
        'critical': '#C7372F',
        'high': '#A55B0B',
        'warning': '#A55B0B',
    }.get(severity.lower(), '#6E7680')
    
    elements.append(Paragraph("CARRIER NOTIFICATION", styles['DocTitle']))
    elements.append(Paragraph(
        f"<font color='{severity_color}'><b>{severity.upper()} PRIORITY</b></font>",
        styles['DocSubtitle']
    ))
    elements.append(Spacer(1, 0.5*cm))
    
    # Shipment information
    elements.append(Paragraph("SHIPMENT INFORMATION", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    
    shipment_data = [
        ['Batch ID:', batch_id],
        ['Carrier:', carrier],
        ['Issue Type:', issue_type.replace('_', ' ').title()],
        ['Severity:', severity.upper()],
    ]
    
    if deadline:
        shipment_data.append(['Response Required By:', deadline])
    
    shipment_table = Table(shipment_data, colWidths=[4*cm, 10*cm])
    shipment_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), HexColor('#6E7680')),
        ('TEXTCOLOR', (1, 0), (1, -1), HexColor('#0E1116')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    
    elements.append(shipment_table)
    elements.append(Spacer(1, 0.8*cm))
    
    # Issue details
    elements.append(Paragraph("ISSUE DETAILS", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    elements.append(Paragraph(detail, styles['DocBody']))
    
    elements.append(Spacer(1, 0.8*cm))
    
    # Required action
    elements.append(Paragraph("REQUIRED ACTION", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    elements.append(Paragraph(required_action, styles['DocBody']))
    
    if deadline:
        elements.append(Spacer(1, 0.3*cm))
        elements.append(Paragraph(
            f"<b>Response Deadline: {deadline}</b>",
            styles['DocBody']
        ))
    
    elements.append(Spacer(1, 1*cm))
    
    # Contact information
    elements.append(Paragraph("CONTACT INFORMATION", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    elements.append(Paragraph(
        f"For questions or clarifications, contact: <b>{contact_email}</b>",
        styles['DocBody']
    ))
    
    elements.append(Spacer(1, 1*cm))
    
    # Acknowledgment
    elements.append(Paragraph(
        "Please acknowledge receipt of this notification and confirm action plan within 24 hours.",
        styles['DocBody']
    ))
    
    # Footer
    elements.append(Spacer(1, 1*cm))
    elements.append(Paragraph(
        "Parcel Ops Control Tower — Automated Carrier Communication",
        styles['DocFooter']
    ))
    
    doc.build(elements)
    buffer.seek(0)
    return buffer


# ---------------------------------------------------------------------------
# Escalation Report
# ---------------------------------------------------------------------------

def generate_escalation_report(
    batch_id: str,
    carrier: str,
    origin: str,
    parcel_count: int,
    hs_code: str,
    critical_lanes: list[str],
    diagnostics: list[dict],
    escalation_reason: str,
    assigned_to: str = "Operations Manager",
) -> BytesIO:
    """Generate an internal escalation report."""
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
    )
    
    styles = _get_styles()
    elements = []
    
    # Header
    elements.append(Paragraph("INTERNAL ESCALATION REPORT", styles['DocTitle']))
    elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", styles['DocSubtitle']))
    elements.append(Spacer(1, 0.5*cm))
    
    # Batch summary
    elements.append(Paragraph("BATCH SUMMARY", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    
    batch_data = [
        ['Batch ID:', batch_id],
        ['Carrier:', carrier],
        ['Origin:', origin],
        ['Parcel Count:', str(parcel_count)],
        ['HS Code:', hs_code],
        ['Assigned To:', assigned_to],
    ]
    
    batch_table = Table(batch_data, colWidths=[4*cm, 10*cm])
    batch_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (0, -1), HexColor('#6E7680')),
        ('TEXTCOLOR', (1, 0), (1, -1), HexColor('#0E1116')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    
    elements.append(batch_table)
    elements.append(Spacer(1, 0.8*cm))
    
    # Critical lanes
    elements.append(Paragraph("CRITICAL LANES", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    
    if critical_lanes:
        lanes_text = ", ".join(critical_lanes)
        elements.append(Paragraph(f"<b>{lanes_text}</b>", styles['DocBody']))
    else:
        elements.append(Paragraph("No critical lanes identified.", styles['DocBody']))
    
    elements.append(Spacer(1, 0.8*cm))
    
    # Diagnostics
    elements.append(Paragraph("DIAGNOSTICS", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    
    if diagnostics:
        for diag in diagnostics:
            elements.append(Paragraph(
                f"<b>{diag.get('issue_type', 'Unknown').replace('_', ' ').title()}</b> "
                f"(Severity: {diag.get('severity', 'N/A').upper()}, "
                f"Confidence: {int(diag.get('confidence', 0) * 100)}%)",
                styles['DocBody']
            ))
            elements.append(Paragraph(diag.get('detail', ''), styles['DocBody']))
            elements.append(Paragraph(
                f"<i>Suggested Action: {diag.get('suggested_action', 'N/A')}</i>",
                styles['DocBody']
            ))
            elements.append(Spacer(1, 0.3*cm))
    else:
        elements.append(Paragraph("No diagnostics available.", styles['DocBody']))
    
    elements.append(Spacer(1, 0.8*cm))
    
    # Escalation reason
    elements.append(Paragraph("ESCALATION REASON", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    elements.append(Paragraph(escalation_reason, styles['DocBody']))
    
    elements.append(Spacer(1, 1*cm))
    
    # Action items
    elements.append(Paragraph("RECOMMENDED ACTIONS", styles['Heading2']))
    elements.append(Spacer(1, 0.3*cm))
    
    actions = [
        "1. Review diagnostics and assess impact on delivery timeline",
        "2. Contact carrier for status update and resolution timeline",
        "3. Determine if amendment request is required",
        "4. Update batch status in system once resolved",
        "5. Document resolution for future reference",
    ]
    
    for action in actions:
        elements.append(Paragraph(action, styles['DocBody']))
    
    # Footer
    elements.append(Spacer(1, 1*cm))
    elements.append(Paragraph(
        "Parcel Ops Control Tower — Internal Escalation System",
        styles['DocFooter']
    ))
    
    doc.build(elements)
    buffer.seek(0)
    return buffer
