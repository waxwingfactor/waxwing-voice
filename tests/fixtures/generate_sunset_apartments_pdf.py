"""
Generate a multi-page property knowledge PDF for RAG testing.

Output: tests/fixtures/sunset_apartments_knowledge.pdf

Property data is consistent with seed_property.json and seed_property.py
(same UUIDs, addresses, amenities, etc.) so the document can be uploaded
via POST /v1/documents/upload after the seed has been applied, and the
resulting knowledge_chunks will be associated with the seeded property.

Run via:

    uv run --with reportlab python tests/fixtures/generate_sunset_apartments_pdf.py

The script avoids adding reportlab as a permanent project dependency since
this is a one-off fixture generator. The resulting PDF is committed to
the repo so the rest of the team doesn't need reportlab to use it.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


def _build_styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            spaceAfter=18,
            textColor=colors.HexColor("#1a3a5c"),
            alignment=TA_CENTER,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=12,
            spaceAfter=24,
            textColor=colors.HexColor("#555555"),
            alignment=TA_CENTER,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            spaceBefore=18,
            spaceAfter=10,
            textColor=colors.HexColor("#1a3a5c"),
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            spaceBefore=10,
            spaceAfter=6,
            textColor=colors.HexColor("#2a4a6c"),
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            spaceAfter=8,
            alignment=TA_JUSTIFY,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10.5,
            leading=14,
            spaceAfter=4,
            leftIndent=18,
            bulletIndent=8,
        ),
        "footer_note": ParagraphStyle(
            "footer_note",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=8,
            textColor=colors.HexColor("#888888"),
            alignment=TA_CENTER,
        ),
    }


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def _cover(styles):
    return [
        Spacer(1, 1.5 * inch),
        Paragraph("Sunset Apartments", styles["title"]),
        Paragraph("Resident & Leasing Information Guide", styles["subtitle"]),
        Spacer(1, 0.3 * inch),
        Paragraph("123 Sunset Blvd, Austin, TX 78701", styles["body"]),
        Paragraph("(512) 555-0100  |  leasing@sunsetapts.example", styles["body"]),
        Paragraph("residents.sunsetapts.example", styles["body"]),
        Spacer(1, 2.5 * inch),
        Paragraph(
            "This guide contains everything you need to know about Sunset Apartments — "
            "available units, pricing, lease terms, amenities, pet policy, parking, "
            "maintenance procedures, community rules, and frequently asked questions. "
            "It is the canonical reference for our leasing team and the AI voice assistant.",
            styles["body"],
        ),
        Spacer(1, 0.4 * inch),
        Paragraph(
            "Document version: May 2026  |  Effective for the 2026 leasing season",
            styles["footer_note"],
        ),
        PageBreak(),
    ]


def _about(styles):
    return [
        Paragraph("About Sunset Apartments", styles["h1"]),
        Paragraph(
            "Sunset Apartments is a modern multifamily community in central Austin, Texas, "
            "comprising 120 thoughtfully designed residences across two mid-rise buildings. "
            "Completed in 2021, the property combines contemporary architecture with the "
            "warmth of an established neighborhood — walkable to South Congress, the Lady "
            "Bird Lake hike-and-bike trail, and the downtown core.",
            styles["body"],
        ),
        Paragraph(
            "Our residents include young professionals, graduate students, remote workers, "
            "and small families who value walkability, on-site amenities, and a quieter "
            "alternative to high-rise downtown living. The community is professionally managed "
            "on-site Monday through Saturday, with 24/7 emergency maintenance coverage.",
            styles["body"],
        ),
        Paragraph("Property at a Glance", styles["h2"]),
        Paragraph("• 120 residences across studios, one-bedrooms, and two-bedrooms", styles["bullet"]),
        Paragraph("• Two mid-rise buildings (4 stories each) connected by a central courtyard", styles["bullet"]),
        Paragraph("• Built 2021, professionally managed since opening", styles["bullet"]),
        Paragraph("• Pet-friendly with breed restrictions (see Pet Policy)", styles["bullet"]),
        Paragraph("• Covered parking included with every lease, no extra fee", styles["bullet"]),
        Paragraph("• On-site leasing office, residents portal, 24/7 emergency maintenance line", styles["bullet"]),
    ]


def _pricing(styles):
    rows = [
        ["Floor Plan", "Approx. Sq. Ft.", "Beds / Baths", "Starting Rent (May 2026)"],
        ["Studio (Plan S1)", "520 sq ft", "0 / 1", "$1,450 / month"],
        ["1 Bedroom (Plan A1)", "720 sq ft", "1 / 1", "$1,750 / month"],
        ["1 Bedroom + Den (Plan A2)", "830 sq ft", "1 / 1", "$1,950 / month"],
        ["2 Bedroom (Plan B1)", "1,050 sq ft", "2 / 2", "$2,200 / month"],
        ["2 Bedroom Penthouse (Plan B2)", "1,180 sq ft", "2 / 2", "$2,650 / month"],
    ]
    table = Table(rows, hAlign="LEFT", colWidths=[2.0 * inch, 1.2 * inch, 1.2 * inch, 1.7 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3a5c")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f4")]),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return [
        Paragraph("Floor Plans &amp; Pricing", styles["h1"]),
        Paragraph(
            "Rents are quoted as the starting rate for available units; final pricing varies "
            "by floor, view, and specific unit. Prices are effective for new leases signed "
            "in May 2026 and are subject to change with notice. All units include in-unit "
            "washer/dryer, stainless steel appliances, quartz countertops, and a private "
            "balcony or patio.",
            styles["body"],
        ),
        Spacer(1, 6),
        table,
        Spacer(1, 12),
        Paragraph(
            "Pricing premiums apply for top-floor units, units with a downtown skyline view, "
            "and corner units (typically $50 to $150/month above the starting rate). "
            "Specials and concessions are offered seasonally — please ask the leasing team "
            "about current move-in incentives.",
            styles["body"],
        ),
        Paragraph(
            "Currently available units are listed in real time on the resident portal at "
            "residents.sunsetapts.example. Availability changes daily.",
            styles["body"],
        ),
    ]


def _amenities(styles):
    return [
        PageBreak(),
        Paragraph("Amenities", styles["h1"]),
        Paragraph(
            "Sunset Apartments offers a full suite of community amenities designed for the "
            "way modern residents actually live — from remote work to recreation. All "
            "amenities are included with the lease at no additional charge, except where "
            "explicitly noted.",
            styles["body"],
        ),
        Paragraph("Outdoor Pool &amp; Sun Deck", styles["h2"]),
        Paragraph(
            "Heated saltwater pool open year-round from 6:00 AM to 10:00 PM. Loungers, "
            "cabanas, and grilling stations available on a first-come basis. Pool access "
            "uses your fob; guests are welcome but residents are responsible for guest conduct.",
            styles["body"],
        ),
        Paragraph("24-Hour Fitness Center", styles["h2"]),
        Paragraph(
            "Cardio equipment (Peloton, Technogym treadmills), free weights, cable machines, "
            "and a small studio for stretching and yoga. Towels are provided. The gym is "
            "accessible 24/7 with your fob.",
            styles["body"],
        ),
        Paragraph("Rooftop Deck", styles["h2"]),
        Paragraph(
            "Building B's rooftop deck offers panoramic views of downtown Austin and the "
            "hill country. Open from 8:00 AM to midnight. Available for private resident "
            "events with advance reservation through the leasing office (no fee, two-hour "
            "limit on weekends).",
            styles["body"],
        ),
        Paragraph("Coworking Lounge &amp; Conference Room", styles["h2"]),
        Paragraph(
            "Ground-floor coworking space with high-speed Wi-Fi, six private phone booths, "
            "and a six-person conference room available by reservation. Coffee and tea are "
            "provided. Open 24/7 with your fob; the conference room must be booked through "
            "the resident portal.",
            styles["body"],
        ),
        Paragraph("Community Lounge &amp; Coffee Bar", styles["h2"]),
        Paragraph(
            "First-floor lounge with a TV, fireplace, and a self-serve specialty coffee "
            "bar (espresso, cold brew, hot tea — included). Hosts monthly resident events "
            "including holiday parties, food truck nights, and yoga in the courtyard.",
            styles["body"],
        ),
    ]


def _parking(styles):
    return [
        Paragraph("Parking", styles["h1"]),
        Paragraph(
            "Covered parking is included with every unit at no additional cost. Each lease "
            "comes with one assigned spot in the parking garage. The garage uses fob access "
            "and is monitored by 24/7 video surveillance.",
            styles["body"],
        ),
        Paragraph("Additional Parking Options", styles["h2"]),
        Paragraph(
            "• Second covered spot: $75/month, subject to availability.",
            styles["bullet"],
        ),
        Paragraph(
            "• Reserved EV charging spaces: $25/month surcharge on top of the standard "
            "covered spot. Twelve Level-2 chargers in Building A garage.",
            styles["bullet"],
        ),
        Paragraph(
            "• Guest parking: 18 surface spots near the leasing office on a first-come, "
            "first-served basis. Guests must register their plate via the resident portal "
            "for stays longer than four hours.",
            styles["bullet"],
        ),
        Paragraph(
            "• Bicycle storage: secured indoor racks in both buildings, included.",
            styles["bullet"],
        ),
        Paragraph(
            "Vehicles parked without a valid resident or guest registration may be towed "
            "at the owner's expense. The towing company is Austin Tow &amp; Recover (ATR), "
            "(512) 555-0177.",
            styles["body"],
        ),
    ]


def _pet_policy(styles):
    return [
        PageBreak(),
        Paragraph("Pet Policy", styles["h1"]),
        Paragraph(
            "Sunset Apartments is a pet-friendly community. We welcome cats and dogs, "
            "subject to the policies below. Exotic pets, reptiles, and birds may be "
            "permitted on a case-by-case basis with prior written approval from the "
            "leasing office.",
            styles["body"],
        ),
        Paragraph("Allowed Pets &amp; Limits", styles["h2"]),
        Paragraph("• Up to two pets per unit (any combination of cats and dogs).", styles["bullet"]),
        Paragraph("• Maximum weight per dog: 75 lbs at adulthood.", styles["bullet"]),
        Paragraph(
            "• Restricted breeds (not permitted, no exceptions): Pit Bull, Staffordshire "
            "Terrier, Rottweiler, Doberman Pinscher, Akita, Chow Chow, Wolf Hybrid, "
            "and any mix containing one of these breeds.",
            styles["bullet"],
        ),
        Paragraph("Pet Fees &amp; Rent", styles["h2"]),
        Paragraph("• Non-refundable pet fee: $300 per pet, due at move-in.", styles["bullet"]),
        Paragraph("• Monthly pet rent: $50 per pet.", styles["bullet"]),
        Paragraph(
            "• Pet deposit: $200 per pet, refundable subject to pet-related damage.",
            styles["bullet"],
        ),
        Paragraph("Required Documentation", styles["h2"]),
        Paragraph(
            "Before move-in, residents must provide a current rabies vaccination certificate, "
            "proof of spay/neuter for dogs over 6 months old, and a clear photo of each pet. "
            "Pets must be registered with the leasing office and re-registered annually.",
            styles["body"],
        ),
        Paragraph("Service &amp; Emotional Support Animals", styles["h2"]),
        Paragraph(
            "Service animals and verified emotional support animals are not subject to "
            "weight limits, breed restrictions, pet fees, or pet rent. Reasonable "
            "accommodation requests are processed in accordance with the Fair Housing Act. "
            "Documentation requirements follow current HUD guidance.",
            styles["body"],
        ),
        Paragraph("Pet Conduct", styles["h2"]),
        Paragraph(
            "Pets must be leashed or carried in all common areas. Owners are responsible "
            "for cleanup; bag stations are located at five spots around the property. "
            "Excessive barking that disturbs neighbors will result in a written notice "
            "and may, after repeated violations, lead to a requirement to rehome the pet.",
            styles["body"],
        ),
    ]


def _lease_terms(styles):
    return [
        Paragraph("Lease Terms", styles["h1"]),
        Paragraph(
            "Standard lease length is 12 months. Shorter and longer terms are available "
            "with rent adjustments as noted below.",
            styles["body"],
        ),
        Paragraph("Lease Length Options", styles["h2"]),
        Paragraph("• 12-month lease (standard): quoted rent applies.", styles["bullet"]),
        Paragraph("• 6-month lease: $150/month premium over the 12-month rate.", styles["bullet"]),
        Paragraph(
            "• Month-to-month (after a completed initial lease): $250/month premium with "
            "60 days' written notice required for any change of status.",
            styles["bullet"],
        ),
        Paragraph(
            "• Lease renewal: residents are offered renewal terms 90 days before lease end. "
            "Renewal rents are subject to market adjustment.",
            styles["bullet"],
        ),
        Paragraph("Early Termination", styles["h2"]),
        Paragraph(
            "Early lease termination is permitted with 60 days' written notice and payment "
            "of two months' rent as a termination fee, in addition to rent through the move-out "
            "date. Active-duty military members covered by the SCRA are exempt from these fees "
            "with proper documentation.",
            styles["body"],
        ),
    ]


def _application_and_movein(styles):
    return [
        PageBreak(),
        Paragraph("Application &amp; Move-In", styles["h1"]),
        Paragraph("Application Requirements", styles["h2"]),
        Paragraph("• Application fee: $75 per applicant (non-refundable).", styles["bullet"]),
        Paragraph(
            "• Credit check (soft pull): minimum credit score of 620. Co-signers accepted "
            "for scores between 580 and 619.",
            styles["bullet"],
        ),
        Paragraph(
            "• Income verification: minimum gross monthly income of 3x the monthly rent. "
            "Acceptable documentation: two recent pay stubs, employment offer letter, "
            "tax returns (for self-employed applicants), or three months of bank statements.",
            styles["bullet"],
        ),
        Paragraph(
            "• Background check: criminal background screening conducted by a third-party "
            "vendor in compliance with applicable Fair Housing laws.",
            styles["bullet"],
        ),
        Paragraph(
            "• Rental history: two prior landlord references covering the past 24 months.",
            styles["bullet"],
        ),
        Paragraph("Move-In Costs", styles["h2"]),
        Paragraph(
            "At lease signing, the following are due in full:",
            styles["body"],
        ),
        Paragraph("• First month's rent (prorated if mid-month move-in)", styles["bullet"]),
        Paragraph("• Last month's rent", styles["bullet"]),
        Paragraph("• Security deposit equal to one month's rent", styles["bullet"]),
        Paragraph("• Pet fees and deposits, if applicable (see Pet Policy)", styles["bullet"]),
        Paragraph("• $150 one-time amenity activation fee (covers fob, gym towels, lounge access setup)", styles["bullet"]),
        Paragraph("Accepted Payment Methods", styles["h2"]),
        Paragraph(
            "Move-in payments must be made by certified check, money order, or ACH bank "
            "transfer. Personal checks and credit cards are not accepted at signing. "
            "Subsequent monthly rent can be paid by ACH, debit card, or credit card "
            "(credit card payments incur a 2.5% processing fee).",
            styles["body"],
        ),
    ]


def _maintenance(styles):
    return [
        Paragraph("Maintenance", styles["h1"]),
        Paragraph("Routine Maintenance Requests", styles["h2"]),
        Paragraph(
            "Submit non-emergency maintenance requests through the resident portal at "
            "residents.sunsetapts.example. Most routine requests (leaky faucet, light bulbs, "
            "appliance issues) are addressed within 1 to 2 business days. Residents will "
            "receive an automated notification when a technician is scheduled.",
            styles["body"],
        ),
        Paragraph("Emergency Maintenance", styles["h2"]),
        Paragraph(
            "For after-hours emergencies, call the 24/7 emergency maintenance line at "
            "<b>(512) 555-0911</b>. Emergencies include: active water leaks, no heat or "
            "AC during extreme weather, gas smell, lockouts (resident is on-site), broken "
            "exterior locks, and electrical issues that pose a safety risk.",
            styles["body"],
        ),
        Paragraph(
            "<b>Life-safety emergencies (fire, gas leak, medical emergency, break-in) "
            "should always be reported to 911 first, then to building staff.</b>",
            styles["body"],
        ),
        Paragraph("What's Covered (No Charge)", styles["h2"]),
        Paragraph("• Plumbing repairs not caused by resident negligence", styles["bullet"]),
        Paragraph("• HVAC servicing and repairs", styles["bullet"]),
        Paragraph("• Appliance repair (refrigerator, dishwasher, stove, washer/dryer)", styles["bullet"]),
        Paragraph("• Electrical issues, light fixture replacement", styles["bullet"]),
        Paragraph("• Common-area issues", styles["bullet"]),
        Paragraph("Resident-Responsible Charges", styles["h2"]),
        Paragraph("• Lockouts during business hours: free for first occurrence, $50 thereafter", styles["bullet"]),
        Paragraph("• Lockouts after hours: $75 per occurrence", styles["bullet"]),
        Paragraph("• Damage from negligence (e.g., toilet clog from non-flushable items): billed at cost", styles["bullet"]),
        Paragraph("• Lost fob replacement: $50", styles["bullet"]),
    ]


def _office_hours(styles):
    return [
        PageBreak(),
        Paragraph("Office Hours &amp; Contact", styles["h1"]),
        Paragraph("Leasing Office", styles["h2"]),
        Paragraph("• Monday through Friday: 9:00 AM to 6:00 PM", styles["bullet"]),
        Paragraph("• Saturday: 10:00 AM to 4:00 PM", styles["bullet"]),
        Paragraph("• Sunday: closed", styles["bullet"]),
        Paragraph("• Federal holidays: closed (notice posted on the portal)", styles["bullet"]),
        Paragraph("Contact Information", styles["h2"]),
        Paragraph("• Leasing office: (512) 555-0100", styles["bullet"]),
        Paragraph("• Email: leasing@sunsetapts.example", styles["bullet"]),
        Paragraph("• Property Manager: Jane Smith, jane@sunsetapts.example", styles["bullet"]),
        Paragraph("• Emergency Maintenance (24/7): (512) 555-0911", styles["bullet"]),
        Paragraph("• Resident Portal: residents.sunsetapts.example", styles["bullet"]),
        Paragraph("Tour Scheduling", styles["h2"]),
        Paragraph(
            "Tours can be scheduled online through the leasing site, by phone, or via the "
            "AI voice assistant. Available tour types include: in-person guided tours "
            "(45 minutes), self-guided tours with a leasing agent on-site (30 minutes), "
            "and virtual video tours via Zoom or FaceTime (30 minutes). Tours are offered "
            "during office hours; weekend tours fill up quickly during peak season.",
            styles["body"],
        ),
    ]


def _community_rules(styles):
    return [
        Paragraph("Community Rules", styles["h1"]),
        Paragraph("Quiet Hours", styles["h2"]),
        Paragraph(
            "Quiet hours are 10:00 PM to 7:00 AM Sunday through Thursday and 11:00 PM to "
            "8:00 AM Friday and Saturday. During quiet hours, noise should not be audible "
            "from neighboring units. Repeat violations may result in lease termination.",
            styles["body"],
        ),
        Paragraph("Guests &amp; Visitors", styles["h2"]),
        Paragraph(
            "Overnight guests are welcome for up to 14 consecutive nights and 30 cumulative "
            "nights per calendar year. Stays beyond these limits require leasing office "
            "approval and may necessitate adding the guest to the lease. Guests in amenity "
            "spaces (pool, gym, rooftop) must be accompanied by a resident.",
            styles["body"],
        ),
        Paragraph("Smoking", styles["h2"]),
        Paragraph(
            "Sunset Apartments is a smoke-free community. Smoking — including tobacco, "
            "cannabis, and e-cigarettes — is prohibited in all units, balconies, common "
            "areas, and within 25 feet of any building entrance. Violation results in a "
            "$250 fine for the first occurrence and is grounds for lease termination on "
            "repeat offenses.",
            styles["body"],
        ),
        Paragraph("Trash &amp; Recycling", styles["h2"]),
        Paragraph(
            "Trash and recycling chutes are located on every floor. Bulky items (furniture, "
            "large boxes) must be taken to the dumpsters in the parking garage; do not "
            "leave items in chute rooms or hallways. Recycling pickup is twice weekly.",
            styles["body"],
        ),
        Paragraph("Renters Insurance", styles["h2"]),
        Paragraph(
            "All residents are required to maintain renters insurance with at least "
            "$100,000 in liability coverage and $25,000 in personal property coverage. "
            "Sunset Apartments must be listed as an interested party. Proof of insurance "
            "is due at lease signing and at every renewal.",
            styles["body"],
        ),
    ]


def _security_and_moveout(styles):
    return [
        PageBreak(),
        Paragraph("Building Security", styles["h1"]),
        Paragraph(
            "Both buildings use fob access at all exterior entrances, parking garage, "
            "amenity spaces, and elevators. Fobs are individually programmed and can be "
            "deactivated remotely if lost or stolen. Replacement fobs are $50 each.",
            styles["body"],
        ),
        Paragraph(
            "Common areas, parking garage, mail room, and entrances are monitored by "
            "24/7 video surveillance. Recordings are retained for 30 days per industry "
            "standard. Surveillance footage is released only in response to law enforcement "
            "requests or legal proceedings.",
            styles["body"],
        ),
        Paragraph(
            "Package management is handled through Luxer One smart lockers in both "
            "buildings. Residents receive a notification with a one-time code when a "
            "package arrives. Packages are held for seven days; oversized items are kept "
            "at the leasing office.",
            styles["body"],
        ),
        Paragraph("Move-Out Procedures", styles["h1"]),
        Paragraph(
            "Notice to vacate must be submitted in writing at least 60 days before the "
            "intended move-out date, even at the end of a fixed-term lease. Notice can be "
            "submitted through the resident portal or by certified mail.",
            styles["body"],
        ),
        Paragraph("Move-Out Inspection", styles["h2"]),
        Paragraph(
            "A move-out inspection is scheduled within 48 hours of vacancy. Residents may "
            "be present (recommended) or not. The inspection identifies any damage beyond "
            "ordinary wear and tear; charges are itemized and deducted from the security "
            "deposit. The remaining deposit (if any) is returned within 30 days as required "
            "by Texas Property Code.",
            styles["body"],
        ),
        Paragraph("Cleaning Expectations", styles["h2"]),
        Paragraph(
            "Units must be returned in broom-clean condition: appliances cleaned interior "
            "and exterior, bathrooms scrubbed, floors vacuumed and mopped, all personal "
            "items removed. Professional cleaning is not required but is strongly recommended; "
            "the leasing office can recommend cleaners ($150 to $250 typical).",
            styles["body"],
        ),
        Paragraph(
            "Trash, furniture, and any remaining items left in the unit will be removed "
            "by management and billed at $75 per hour plus disposal fees, deducted from "
            "the security deposit.",
            styles["body"],
        ),
    ]


def _faq(styles):
    qa_pairs = [
        (
            "Do you offer short-term or furnished rentals?",
            "We do not offer fully furnished units. The shortest lease term is 6 months "
            "(at a $150/month premium). Furniture rental can be arranged through "
            "third-party services; ask the leasing office for recommended providers.",
        ),
        (
            "Are utilities included in the rent?",
            "Water, trash, and high-speed internet (1 Gbps fiber) are included. Electricity "
            "and gas are billed separately by the resident's chosen provider; estimated "
            "monthly cost ranges from $40 to $120 depending on unit size and season.",
        ),
        (
            "Can I have a roommate? How does that work for the lease?",
            "Yes. All adult occupants must be on the lease and pass the application process "
            "individually. Combined household income must meet the 3x rent requirement. "
            "Roommates are jointly and severally liable for the full lease.",
        ),
        (
            "Is there storage available?",
            "Limited additional storage units (4'x8') are available in the parking garage "
            "for $50/month, subject to availability. The waiting list is typically two to "
            "four months long.",
        ),
        (
            "Do you accept Section 8 / Housing Choice Vouchers?",
            "We comply with all applicable fair housing laws. Voucher applications are "
            "processed using the same screening criteria as any other application. "
            "Specific questions about voucher acceptance and program participation should "
            "be directed to the leasing office.",
        ),
        (
            "What's the policy on subletting and Airbnb?",
            "Subletting and short-term rental of the unit (including platforms like Airbnb, "
            "Vrbo, and Furnished Finder) are strictly prohibited. Violation is grounds for "
            "immediate lease termination.",
        ),
        (
            "Can I install fixtures, paint, or hang TVs?",
            "Hanging items with standard nails or small drywall anchors is permitted. "
            "Painting requires written approval and must be returned to the original color "
            "at move-out. Wall-mounted TVs are permitted; the resident is responsible for "
            "patching and repainting any holes at move-out.",
        ),
        (
            "How do I add a roommate or remove someone from the lease?",
            "Lease modifications require leasing office approval. The new roommate must "
            "pass screening and pay a $50 lease modification fee. Removing a roommate "
            "requires the remaining residents to re-qualify on income alone.",
        ),
        (
            "What happens if my rent payment is late?",
            "Rent is due on the 1st of the month. A 5-day grace period applies; rent "
            "received on or before the 5th is not considered late. After the 5th, a late "
            "fee of 5% of monthly rent applies. After the 10th, formal eviction proceedings "
            "may begin in accordance with Texas Property Code.",
        ),
        (
            "Is the property on a flood plain or earthquake zone?",
            "The property is not on a designated flood plain per current FEMA mapping, "
            "and central Texas is not a high seismic risk area. Residents are nonetheless "
            "encouraged to maintain renters insurance with appropriate coverage.",
        ),
    ]

    flow = [
        PageBreak(),
        Paragraph("Frequently Asked Questions", styles["h1"]),
        Paragraph(
            "These are the questions our leasing team and resident services receive most "
            "often. If your question is not answered here, please reach out to the leasing "
            "office or check the resident portal knowledge base.",
            styles["body"],
        ),
    ]
    for question, answer in qa_pairs:
        flow.append(Paragraph(f"<b>Q: {question}</b>", styles["h2"]))
        flow.append(Paragraph(f"A: {answer}", styles["body"]))
    return flow


def _closing(styles):
    return [
        Spacer(1, 0.3 * inch),
        Paragraph(
            "This document is the canonical reference for Sunset Apartments and is updated "
            "as policies, pricing, and amenities change. The most recent version is always "
            "available on the resident portal.",
            styles["footer_note"],
        ),
        Paragraph(
            "© 2026 Demo Property Management Co.  |  All information current as of May 2026.",
            styles["footer_note"],
        ),
    ]


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_pdf(output_path: Path) -> None:
    styles = _build_styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=LETTER,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.85 * inch,
        title="Sunset Apartments — Resident & Leasing Information Guide",
        author="Demo Property Management Co.",
        subject="Property knowledge base for AI voice agent and dashboard search",
    )

    flow = []
    flow += _cover(styles)
    flow += _about(styles)
    flow += _pricing(styles)
    flow += _amenities(styles)
    flow += _parking(styles)
    flow += _pet_policy(styles)
    flow += _lease_terms(styles)
    flow += _application_and_movein(styles)
    flow += _maintenance(styles)
    flow += _office_hours(styles)
    flow += _community_rules(styles)
    flow += _security_and_moveout(styles)
    flow += _faq(styles)
    flow += _closing(styles)

    doc.build(flow)


def main() -> None:
    here = Path(__file__).resolve().parent
    output = here / "sunset_apartments_knowledge.pdf"
    build_pdf(output)
    size_kb = output.stat().st_size / 1024
    print(f"Wrote {output} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
