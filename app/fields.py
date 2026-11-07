"""Single source of truth for bill fields, their types, labels and grouping."""
from pydantic import create_model

# (key, label, type)
FIELDS = [
    ("account_id", "Account ID", str),
    ("rr_number", "RR No", str),
    ("bill_number", "Bill No", str),
    ("bill_period_start", "Bill period start", str),
    ("bill_period_end", "Bill period end", str),
    ("units_consumed", "Units consumed", int),
    ("fixed_charges", "Fixed charges", float),
    ("energy_charges", "Energy charges", float),
    ("tax", "Tax", float),
    ("net_payable", "Net payable", float),
    ("due_date", "Due date", str),
    # secondary
    ("mr_code", "M.R code", str),
    ("consumer_name", "Name", str),
    ("address", "Address", str),
    ("tariff", "Tariff", str),
    ("sanctioned_load", "Sanctioned load", str),
    ("reading_date", "Reading date", str),
    ("previous_reading", "Previous reading", int),
    ("present_reading", "Present reading", int),
    ("constant", "Meter constant", int),
    ("recorded_md", "Recorded MD", str),
    ("power_factor", "Power factor", str),
    ("fixed_charges_rate", "Fixed charges rate", float),
    ("energy_charges_rate", "Energy charges rate", float),
    ("fppca_rate", "FPPCA rate", float),
    ("fppca", "FPPCA", float),
    ("pg_surcharge_rate", "P&G surcharge rate", float),
    ("pg_surcharge", "P&G surcharge", float),
    ("interest", "Interest", float),
    ("debit_credit", "Debit/credit", float),
    ("arrears", "Arrears", float),
    ("current_demand", "Current demand payable", float),
    ("asd", "ASD to be paid", int),
]

PRIMARY = ["account_id", "rr_number", "bill_number", "bill_period_start", "bill_period_end",
           "units_consumed", "fixed_charges", "energy_charges", "tax", "net_payable", "due_date"]
CRITICAL = list(PRIMARY)   # critical fields for accuracy == primary fields
SECONDARY = [k for k, _, _ in FIELDS if k not in PRIMARY]
KEYS = [k for k, _, _ in FIELDS]
LABELS = {k: l for k, l, _ in FIELDS}
TYPES = {k: t for k, _, t in FIELDS}
NUMERIC_PRIMARY = {k for k in PRIMARY if TYPES[k] in (int, float)}

# Pydantic model used as the Gemini response schema: every field optional (null if unreadable).
BillExtraction = create_model("BillExtraction", **{k: (t | None, None) for k, _, t in FIELDS})
