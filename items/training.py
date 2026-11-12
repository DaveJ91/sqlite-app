"""Labelled lines for the mock classifier and the mock deductibility check.

The chains are a working paper: section references are the reasoning, not a Revenue ruling.
Blocked inputs follow VATCA 2010 s.60 (food, drink, accommodation, entertainment,
personal services, petrol, passenger cars). Qualifying cars can keep 20% under s.59(2)(d).
"""

from decimal import Decimal

from .vat import LABELS

RATE_FRACTION = {
    "standard": Decimal("0.23"),
    "reduced": Decimal("0.135"),
    "second_reduced": Decimal("0.09"),
    "livestock": Decimal("0.048"),
    "zero": Decimal("0"),
    "exempt": Decimal("0"),
    "reverse_charge": Decimal("0"),
    "outside_scope": Decimal("0"),
}

SUPPLY = {
    "taxable": "Taxable supply",
    "exempt": "Exempt supply",
    "outside": "Outside the scope",
    "none": "No supply",
}
NATURE = {"good": "Good", "service": "Service"}
PLACE = {"ireland": "Ireland", "eu": "Other EU", "non_eu": "Outside the EU"}
WHO = {
    "supplier": "Supplier accounts",
    "customer": "Customer accounts (reverse charge)",
    "none": "No VAT to account for",
}
BLOCKED = {
    "none": "Not a blocked input",
    "food": "Food or drink",
    "accommodation": "Accommodation",
    "entertainment": "Entertainment",
    "personal": "Other personal services",
    "petrol": "Petrol",
    "passenger_car": "Passenger motor vehicle",
    "exempt_use": "Used for exempt supplies",
    "non_business": "Non-business use",
    "no_vat": "No VAT on the invoice",
}


def _case(category, nature, deductible="full", blocked="none", confidence=0.92, deduct_confidence=0.9, **more):
    if category == "reverse_charge":
        supply, place, who = "taxable", "eu", "customer"
    elif category == "exempt":
        supply, place, who = "exempt", "ireland", "none"
    elif category == "outside_scope":
        supply, place, who = "outside", "ireland", "none"
    else:
        supply, place, who = "taxable", "ireland", "supplier"
    if deductible == "full":
        portion, recover = "100% business use", "Fully recoverable"
        deduct_reason = "Recoverable in full. Not a blocked input."
    elif deductible == "partial":
        portion, recover = "Partial recovery", "Partly recoverable"
        deduct_reason = "Only part of the VAT is recoverable."
    else:
        portion, recover = "Nothing recoverable", "Not recoverable"
        deduct_reason = "None of this VAT goes into T2."
    spec = {
        "category": category,
        "supply": supply,
        "nature": nature,
        "place": place,
        "who_accounts": who,
        "confidence": confidence,
        "reason": f"{LABELS[category]}.",
        "supply_because": "The supplier invoiced the business for this line.",
        "nature_because": "The description reads as a good." if nature == "good" else "The description reads as a service.",
        "place_because": "The supplier is Irish, and nothing on the line shifts the place of supply.",
        "rate_because": f"The treatment used here is {LABELS[category]}.",
        "who_because": "The supplier charges any Irish VAT due.",
        "deductible": deductible,
        "deductible_percent": "100" if deductible == "full" else ("0" if deductible == "none" else "20"),
        "blocked_reason": blocked,
        "deduct_confidence": deduct_confidence,
        "deduct_reason": deduct_reason,
        "use_finding": "Business use",
        "use_because": "The cost is invoiced to the business and looks like a business input.",
        "block_because": "It is not food, drink, accommodation, entertainment, petrol, or a passenger car.",
        "portion_finding": portion,
        "portion_because": "Nothing on the invoice shows a split between taxable and exempt use.",
        "recover_finding": recover,
        "recover_because": "The recoverable amount follows that apportionment.",
    }
    spec.update(more)
    spec["class_chain"] = [
        {"step": "Supply", "finding": SUPPLY[spec["supply"]], "because": spec["supply_because"]},
        {"step": "Nature", "finding": NATURE[spec["nature"]], "because": spec["nature_because"]},
        {"step": "Place of supply", "finding": PLACE[spec["place"]], "because": spec["place_because"]},
        {"step": "Rate", "finding": LABELS[spec["category"]], "because": spec["rate_because"]},
        {"step": "Who accounts", "finding": WHO[spec["who_accounts"]], "because": spec["who_because"]},
    ]
    spec["deduct_chain"] = [
        {"step": "Taxable use", "finding": spec["use_finding"], "because": spec["use_because"]},
        {"step": "Blocked input", "finding": BLOCKED[spec["blocked_reason"]], "because": spec["block_because"]},
        {"step": "Apportionment", "finding": spec["portion_finding"], "because": spec["portion_because"]},
        {"step": "Recoverable VAT", "finding": spec["recover_finding"], "because": spec["recover_because"]},
    ]
    return spec


TREATMENTS = {
    "consulting": _case(
        "standard",
        "service",
        reason="Irish professional services are a standard-rated taxable supply.",
        rate_because="Professional services are standard-rated at 23%.",
        block_because="Professional services are not in the s.60 blocked list.",
        deduct_reason="Used for the taxable business, and not a blocked input.",
    ),
    "overhead": _case(
        "standard",
        "service",
        reason="Ordinary Irish business overhead, standard-rated.",
        rate_because="This overhead is not in a reduced, zero, or exempt list.",
    ),
    "heating": _case(
        "reduced",
        "good",
        reason="Heating fuel is reduced-rated and deductible for the premises.",
        nature_because="Oil supplied in bulk is a good.",
        rate_because="Fuel for heating is in the 13.5% reduced rate.",
        deduct_reason="Business premises heat. Heating oil is not petrol, so the petrol block does not apply.",
        block_because="Heating oil is not petrol. Section 60 blocks petrol, not heating fuel.",
    ),
    "diesel": _case(
        "standard",
        "good",
        reason="Road diesel is standard-rated and is not in the petrol block.",
        nature_because="Fuel supplied in bulk is a good.",
        rate_because="Auto-diesel is standard-rated, unlike marked heating oil.",
        block_because="Section 60 blocks petrol. Diesel is not on that list.",
        deduct_reason="Business diesel. The petrol block does not apply.",
    ),
    "petrol": _case(
        "standard",
        "good",
        deductible="none",
        blocked="petrol",
        reason="Petrol is standard-rated. The VAT is charged, but it is not deductible.",
        nature_because="Fuel supplied in bulk is a good.",
        rate_because="Petrol is standard-rated at 23%.",
        use_finding="Business fuel",
        use_because="It may well be for business travel. That does not lift the block.",
        block_because="VATCA s.60 denies a deduction for petrol, other than petrol held as stock-in-trade.",
        portion_because="The block is absolute for petrol. There is no 20% car-style fraction.",
        recover_because="Leave the petrol VAT out of T2.",
        deduct_reason="Petrol is a blocked input under s.60, unless it is stock-in-trade.",
    ),
    "repairs": _case(
        "reduced",
        "service",
        reason="Repair and maintenance of property is reduced-rated and deductible.",
        rate_because="Building repair and maintenance is in the 13.5% rate.",
        deduct_reason="Property repair for the taxable business. Not a blocked personal service.",
    ),
    "newspaper": _case(
        "second_reduced",
        "good",
        reason="Newspapers and periodicals are at the second reduced rate.",
        nature_because="A newspaper is a good.",
        rate_because="Newspapers and periodicals are at 9%.",
    ),
    "stationery": _case(
        "standard",
        "good",
        reason="Ordinary goods, standard-rated and deductible.",
        nature_because="The line is a supply of goods.",
        rate_because="These goods are not in a reduced or zero-rated list.",
    ),
    "stamps": _case(
        "exempt",
        "good",
        deductible="none",
        blocked="no_vat",
        reason="Postage stamps follow the exempt postal service. There is no VAT to deduct.",
        supply_because="Universal postal services are exempt, and stamps follow that treatment.",
        rate_because="Exempt. No VAT is charged.",
        who_because="There is no VAT on the stamp.",
        block_because="There is no VAT on the invoice to deduct.",
        portion_because="With no VAT, the recoverable amount is zero.",
        deduct_reason="Exempt postal service. T2 stays at zero for this line.",
    ),
    "food": _case(
        "reduced",
        "service",
        deductible="none",
        blocked="food",
        reason="Catering is reduced-rated. Staff and client food is a blocked input.",
        rate_because="Catering and restaurant services are at 13.5%.",
        use_because="A working meal can still be a business cost for income tax. VAT is stricter.",
        block_because="VATCA s.60 blocks food and drink for the accountable person, agents, or employees, unless it is stock or a qualifying staff canteen.",
        portion_because="This is a working lunch, not a staff canteen and not stock.",
        deduct_reason="Food and drink blocked by s.60. Override this if it is stock in a catering trade.",
    ),
    "canteen": _case(
        "reduced",
        "service",
        reason="A staff canteen is the s.60 exception: reduced-rated, and deductible.",
        rate_because="Catering is at 13.5%.",
        block_because="Section 60 blocks food, then excepts a supply that is itself a taxable canteen service.",
        deduct_reason="Staff canteen exception in s.60. Confirm it is a real canteen, not client entertaining.",
        use_finding="Staff canteen",
        use_because="The description says staff canteen rather than client hospitality.",
    ),
    "entertainment": _case(
        "standard",
        "service",
        deductible="none",
        blocked="entertainment",
        confidence=0.44,
        deduct_confidence=0.58,
        reason="Looks like entertainment, but the description is thin, so the rate is uncertain.",
        rate_because="Client entertainment is usually standard-rated. Confidence is low because the wording is vague.",
        block_because="VATCA s.60 blocks entertainment. A reception for guests is the textbook case.",
        portion_because="Entertainment is blocked in full. There is no partial recovery.",
        deduct_reason="Entertainment is blocked. The low confidence is on the rate, not on the block.",
    ),
    "hotel": _case(
        "reduced",
        "service",
        deductible="none",
        blocked="accommodation",
        reason="Hotel accommodation is reduced-rated, and the VAT is blocked.",
        nature_because="A hotel stay is a service.",
        rate_because="Hotel and holiday accommodation is at 13.5%.",
        block_because="VATCA s.60 blocks accommodation, other than qualifying conference accommodation.",
        portion_because="Nothing says this was a qualifying conference.",
        deduct_reason="Accommodation blocked by s.60. Override only for a qualifying conference.",
    ),
    "software": _case(
        "standard",
        "service",
        reason="An Irish software supply is a standard-rated service.",
        nature_because="A subscription or implementation is a service, even when software is delivered electronically.",
        rate_because="Electronic services from an Irish supplier are standard-rated.",
        deduct_reason="Used for the taxable business. Software is not a blocked input.",
    ),
    "eu_software": _case(
        "reverse_charge",
        "service",
        reason="B2B services from another EU country. The Irish customer accounts under the reverse charge.",
        supply_because="There is a supply of services for consideration.",
        nature_because="A software licence from an EU supplier is a service.",
        place_because="For B2B services the general rule puts the place of supply where the customer belongs, which is Ireland.",
        rate_because="The customer self-accounts at the Irish standard rate. The supplier should not charge Irish VAT.",
        who_because="Reverse charge: the customer accounts, and the same amount can be deducted if the service is for taxable supplies.",
        use_because="A business software licence is a taxable-business input unless you know it is for an exempt activity.",
        block_because="A software licence is not in the s.60 blocked list.",
        deduct_reason="Notional VAT in T1 and, because it is for taxable use, the same amount in T2. Net also goes to ES2.",
        recover_because="Deduct the notional VAT. The invoice itself should show no Irish VAT.",
    ),
    "car": _case(
        "standard",
        "service",
        deductible="partial",
        blocked="passenger_car",
        confidence=0.8,
        deduct_confidence=0.47,
        deductible_percent="20",
        reason="Hire of a passenger car is standard-rated. Deductibility is the hard part.",
        nature_because="Short-term hire is a service. A purchase of the car would be a good.",
        rate_because="Car hire is standard-rated.",
        use_finding="Possibly a qualifying car",
        use_because="Business hire is a start. Qualifying-car recovery also needs business use of at least 60%.",
        block_because="VATCA s.60 blocks passenger motor vehicles, with narrow exceptions for stock, hire businesses, and driving schools.",
        portion_finding="20% if it qualifies",
        portion_because="Section 59(2)(d) allows 20% where the car is a qualifying low-CO2 vehicle used at least 60% for business. CO2 and business use are not on the invoice.",
        recover_finding="20% provisional",
        recover_because="Held at 20% so the return is not overstated. Move it to 0% or 100% when you know the facts.",
        deduct_reason="Provisional 20% under the qualifying-car rule. Confirm CO2 and that business use is at least 60%.",
    ),
    "legal": _case(
        "standard",
        "service",
        reason="Legal services are standard-rated unless the matter itself is exempt.",
        rate_because="Nothing in the description suggests a financial, insurance, or other exempt matter.",
        deduct_reason="Legal costs of the taxable business are deductible. Exempt-use work would not be.",
    ),
    "disbursement": _case(
        "outside_scope",
        "service",
        deductible="none",
        blocked="no_vat",
        reason="A filing fee passed on as a disbursement is outside the scope. There is no VAT.",
        supply_because="A true disbursement is the client's own cost, paid on their behalf, not a supply by the solicitor.",
        place_because="Outside the scope, so place of supply is not the question.",
        rate_because="No VAT. Do not treat the fee as exempt income or as a taxed supply.",
        who_because="Nobody accounts for VAT on a disbursement.",
        block_because="There is no VAT on the line.",
        deduct_reason="Outside the scope. Keep it out of T1 and T2.",
    ),
    "livestock": _case(
        "livestock",
        "good",
        reason="Live cattle are at the livestock rate of 4.8%.",
        nature_because="Live animals are goods.",
        rate_because="The livestock rate is 4.8%.",
        deduct_reason="Stock in trade of a mart or farm. The VAT is deductible.",
        use_because="Bought as stock, not as a private animal.",
    ),
    "feed": _case(
        "zero",
        "good",
        deductible="none",
        blocked="no_vat",
        reason="Most animal feed is zero-rated, so there is no VAT to deduct.",
        rate_because="Animal feeding stuffs are zero-rated.",
        who_because="The supplier makes a zero-rated supply. The invoice VAT is zero.",
        block_because="Zero-rated does not mean blocked. There is simply no VAT on the line.",
        deduct_reason="Zero-rated feed. T2 gets nothing because the VAT is zero.",
    ),
}

# Unknown descriptions, used when a real invoice is mocked.
DEFAULT = _case(
    "standard",
    "service",
    confidence=0.55,
    deduct_confidence=0.55,
    reason="No training row for this description. Defaulted to a standard-rated Irish service.",
    deduct_reason="No training row. Defaulted to fully deductible. Review the chain before sign-off.",
    supply_because="The mock has no labelled example for this wording, so it assumes a taxable supply.",
    rate_because="Defaulted to 23% because the description is not in the training set.",
    block_because="Defaulted to not blocked. Check food, cars, petrol, and entertainment yourself.",
)


def treatment_for(description):
    key = DESCRIPTION_TREATMENT.get(description)
    if key is None:
        return DEFAULT
    return TREATMENTS[key]


def amounts(net, treatment_key):
    net_amount = Decimal(net)
    vat_amount = (net_amount * RATE_FRACTION[TREATMENTS[treatment_key]["category"]]).quantize(Decimal("0.01"))
    return {
        "net_amount": f"{net_amount:.2f}",
        "vat_amount": f"{vat_amount:.2f}",
        "gross_amount": f"{(net_amount + vat_amount):.2f}",
    }


def classification_for(description):
    spec = treatment_for(description)
    return {
        "category": spec["category"],
        "confidence": spec["confidence"],
        "reason": spec["reason"],
        "supply": spec["supply"],
        "nature": spec["nature"],
        "place": spec["place"],
        "who_accounts": spec["who_accounts"],
        "chain": spec["class_chain"],
        "error": "",
    }


def deductibility_for(description):
    spec = treatment_for(description)
    return {
        "deductible": spec["deductible"],
        "deductible_percent": spec["deductible_percent"],
        "blocked_reason": spec["blocked_reason"],
        "confidence": spec["deduct_confidence"],
        "reason": spec["deduct_reason"],
        "chain": spec["deduct_chain"],
        "error": "",
    }


# Filled by mocks when the catalogue is defined. Keys are line descriptions.
DESCRIPTION_TREATMENT = {}


def register_descriptions(pairs):
    DESCRIPTION_TREATMENT.update(pairs)
