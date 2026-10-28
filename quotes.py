"""Ferro quote engine — Phase 1 revenue line.
Estimate: stock (volume+15% removal allowance) -> material cost,
machine time from removal rate per material, shop rate, setup fee, margin.
All numbers are shop-rate assumptions; refine with calibration data as it accumulates.
"""

# $/kg stock, small-quantity aluminum/steel/plastic round & plate retail
MATERIAL_PRICE = {
    "6061-T6": 11.0, "7075-T6": 22.0, "steel-1018": 3.5,
    "304-SS": 9.0, "316L": 14.0, "PEEK": 120.0, "delrin": 18.0,
}
# rough+finish removal rate, cm^3/min (conservative shop milling/turning)
MRR = {
    "6061-T6": 9.0, "7075-T6": 7.0, "steel-1018": 4.0,
    "304-SS": 3.5, "316L": 3.0, "PEEK": 12.0, "delrin": 15.0,
}
SHOP_RATE = 75.0     # $/hr machine+operator
SETUP_FEE = 45.0     # per job, 2.5D single-setup
MARGIN = 1.35        # markup on cost
CUT_STOCK_FEE = 8.0  # stock cut & handling

# complexity multiplier by family (simple turned/plate parts vs multi-feature)
FAMILY_COMPLEXITY = {
    "spacer": 0.9, "plate": 0.9, "grid_plate": 1.0, "flange": 1.0,
    "shaft_collar": 1.0, "leveling_foot": 1.0, "pulley": 1.1,
    "extrusion_bracket": 1.0, "bracket": 1.1, "tube_clamp": 1.0,
    "tube_rack": 1.25, "port_block": 1.2, "enclosure": 1.3,
}


def quote(volume_mm3, area_mm2, material="316L", family=None, solids=1):
    """Return a price + lead estimate from kernel-measured geometry (mm^3)."""
    mat = MATERIAL_PRICE.get(material, 11.0)
    mrr = MRR.get(material, 9.0)
    stock_vol_cm3 = (volume_mm3 * 1.15) / 1000.0     # 15% removal allowance
    removal_cm3 = max(stock_vol_cm3 - volume_mm3 / 1000.0, 0.0)
    stock_kg = stock_vol_cm3 / 1000.0 * _density(material)
    material_cost = stock_kg * mat + CUT_STOCK_FEE
    machine_hrs = (removal_cm3 / mrr) / 60.0
    machine_cost = machine_hrs * SHOP_RATE
    cx = FAMILY_COMPLEXITY.get(family, 1.1) * max(1, solids)
    cost = (material_cost + machine_cost * cx + SETUP_FEE) * 1.06  # waste/scrap
    price = cost * MARGIN
    lead_days = 7 if machine_hrs < 0.75 else (10 if machine_hrs < 2 else 14)
    return {
        "stock_kg": round(stock_kg, 3),
        "material_cost": round(material_cost, 2),
        "machine_hrs": round(machine_hrs, 2),
        "machine_cost": round(machine_cost, 2),
        "complexity": round(cx, 2),
        "cost": round(cost, 2),
        "price": round(price, 2),
        "lead_days": lead_days,
        "currency": "USD",
        "basis": "estimate from kernel-measured geometry; firm quote after design approval",
    }


def _density(material):
    return {"6061-T6": 2.70, "7075-T6": 2.81, "steel-1018": 7.87,
            "304-SS": 7.90, "316L": 8.00, "PEEK": 1.32, "delrin": 1.41}.get(material, 2.70)