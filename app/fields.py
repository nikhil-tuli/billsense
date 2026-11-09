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
MEANINGS = {
    "account_id": "Unique consumer account number printed on the bill.",
    "rr_number": "Revenue Register number: the utility's ID for the meter connection.",
    "bill_number": "Serial number of this bill.",
    "bill_period_start": "First day of the billing period (previous meter reading date).",
    "bill_period_end": "Last day of the billing period (present meter reading date).",
    "units_consumed": "Electricity used in the period, in kWh (present reading minus previous reading, times meter constant).",
    "fixed_charges": "Fixed monthly charge based on sanctioned load.",
    "energy_charges": "Charge for units consumed: units x energy rate.",
    "tax": "Electricity tax (9% of fixed + energy + FPPCA).",
    "net_payable": "Final amount to pay by the due date, including arrears and adjustments.",
    "due_date": "Last date to pay without penalty.",
    "mr_code": "Meter reader code: identifies the route / reader.",
    "consumer_name": "Name of the consumer on the account.",
    "address": "Installation address of the connection.",
    "tariff": "Tariff category, e.g. LT-2 residential or commercial.",
    "sanctioned_load": "Maximum load approved for the connection, in kW/HP.",
    "reading_date": "Date the meter was read.",
    "previous_reading": "Meter reading at the start of the period.",
    "present_reading": "Meter reading at the end of the period.",
    "constant": "Meter multiplying constant applied to the reading difference.",
    "recorded_md": "Recorded maximum demand (peak load) in the period, in kW.",
    "power_factor": "Ratio of real to apparent power; a low value can attract a penalty.",
    "fixed_charges_rate": "Rate per kW used for the fixed charges.",
    "energy_charges_rate": "Rate per unit (kWh) used for the energy charges.",
    "fppca_rate": "Per-unit rate of the fuel and power purchase cost adjustment.",
    "fppca": "Fuel and power purchase cost adjustment amount: units x FPPCA rate.",
    "pg_surcharge_rate": "Rate for the Pension and Gratuity surcharge.",
    "pg_surcharge": "Pension and Gratuity surcharge amount.",
    "interest": "Interest on late or unpaid earlier bills.",
    "debit_credit": "Adjustment carried from earlier bills: debit adds, credit subtracts.",
    "arrears": "Unpaid amount from earlier bills.",
    "current_demand": "Charges for this period only: fixed + energy + FPPCA + tax + surcharges.",
    "asd": "Additional Security Deposit still to be paid.",
}

NUMERIC_PRIMARY = {k for k in PRIMARY if TYPES[k] in (int, float)}

# Pydantic model used as the Gemini response schema: every field optional (null if unreadable).
BillExtraction = create_model("BillExtraction", **{k: (t | None, None) for k, _, t in FIELDS})
