"""Synthetic electricity bill generator (English-only, fictional utility).

Writes, under --out:
  bills/bill_NNN.jpg        bill image (clean / medium / noisy)
  ground_truth/bill_NNN.json  exact printed values for every field
  seed.json                 owners, properties (1:1 account_id) and bill manifest
Deterministic for a given --seed.
"""
import argparse, json, random
from datetime import date, timedelta
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

UTILITY = "EXAMPLE POWER SUPPLY CO. LTD."
FONT = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
FONT_B = "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"

PROPERTIES = [  # owner, property, city, area, pin prefix, rr prefix
    ("Ravi Kumar", "Lakeview Apartments 4B", "Bengaluru", "HSR LAYOUT", "5601", "BLR"),
    ("Ravi Kumar", "Seaside Heights 12", "Mumbai", "ANDHERI WEST", "4000", "MUM"),
    ("Meera Iyer", "Palm Residency 201", "Bengaluru", "WHITEFIELD", "5600", "BLR"),
    ("Meera Iyer", "Adyar Court 7", "Chennai", "ADYAR", "6000", "CHE"),
    ("Farhan Sheikh", "Andheri Nest", "Mumbai", "ANDHERI EAST", "4000", "MUM"),
    ("Anita Verma", "Dwarka Villa", "Delhi", "DWARKA SEC 12", "1100", "DEL"),
    ("Anita Verma", "Gachibowli Towers 9C", "Hyderabad", "GACHIBOWLI", "5000", "HYD"),
    ("Suresh Nair", "Koregaon Park Row 3", "Pune", "KOREGAON PARK", "4110", "PUN"),
    ("Priya Menon", "Salt Lake Greens 5A", "Kolkata", "SALT LAKE", "7000", "KOL"),
    ("Suresh Nair", "Indiranagar Court 8", "Bengaluru", "INDIRANAGAR", "5600", "BLR"),
    ("Kavita Rao", "Jayanagar Heights 6", "Bengaluru", "JAYANAGAR", "5600", "BLR"),
    ("Imran Qureshi", "Bandra Pearl 14", "Mumbai", "BANDRA WEST", "4000", "MUM"),
    ("Neha Kapoor", "Vasant Kunj Flat 22", "Delhi", "VASANT KUNJ", "1100", "DEL"),
    ("Arjun Reddy", "Jubilee Hills Villa 3", "Hyderabad", "JUBILEE HILLS", "5000", "HYD"),
    ("Lakshmi Narayanan", "T Nagar Residency 9", "Chennai", "T NAGAR", "6000", "CHE"),
    ("Kavita Rao", "Baner Hills 11B", "Pune", "BANER", "4110", "PUN"),
    ("Sourav Das", "Park Street Court 4", "Kolkata", "PARK STREET", "7000", "KOL"),
    ("Neha Kapoor", "Koramangala Apt 12", "Bengaluru", "KORAMANGALA", "5600", "BLR"),
    ("Arjun Reddy", "Powai Lake View 7", "Mumbai", "POWAI", "4000", "MUM"),
    ("Priya Menon", "Anna Nagar West 15", "Chennai", "ANNA NAGAR", "6000", "CHE"),
]

def fmt(d): return d.strftime("%d/%m/%Y")
def m2(x): return f"{x:.2f}"

def make_data(rng):
    owners = sorted({p[0] for p in PROPERTIES})
    props, bills = [], []
    for i, (owner, name, city, area, pin, rrp) in enumerate(PROPERTIES, start=1):
        acc = f"10000{i:05d}"
        prop = dict(property_id=i, owner=owner, property_name=name, city=city,
                    account_id=acc, rr_number=f"{rrp}{rng.randint(10,99)}SMP{rng.randint(1000,9999)}",
                    address=f"NO {rng.randint(2,99)} {name.upper()}, {area}, {city.upper()} - {pin}{rng.randint(10,99)}",
                    tariff="LT1", sanctioned_kw=rng.choice([2, 3, 4, 5]), mr_code=str(rng.randint(10000000, 19999999)))
        props.append(prop)
        start = date(2026, 10, rng.randint(1, 8))
        reading = rng.randint(1000, 8000)
        for k in range(1):  # one bill per property
            end = start + timedelta(days=rng.randint(29, 31))
            units = rng.randint(90, 420)
            prev, pres = reading, reading + units
            fixed_rate = rng.choice([110.0, 130.0, 150.0])
            e_rate = rng.choice([5.4, 5.8, 5.9, 6.1, 6.5])
            fp_rate = rng.choice([0.25, 0.30, 0.35]); pg_rate = 0.35
            fixed = round(prop["sanctioned_kw"] * fixed_rate, 2)
            energy = round(units * e_rate, 2); fppca = round(units * fp_rate, 2)
            pg = round(units * pg_rate, 2)
            tax = round((fixed + energy + fppca) * 0.09, 2)
            interest = rng.choice([0.0, 0.0, 0.0, round(rng.uniform(2, 25), 2)])
            cur = round(fixed + energy + fppca + pg + tax + interest, 2)
            credit = rng.choice([0.0, 0.0, -round(rng.uniform(10, 300), 2), round(rng.uniform(10, 150), 2)])
            arrears = rng.choice([0.0, 0.0, 0.0, round(rng.uniform(100, 900), 2)])
            net = round(cur + credit + arrears, 2)
            gt = {
                "account_id": acc, "rr_number": prop["rr_number"], "bill_number": str(rng.randint(10**14, 10**15 - 1)),
                "mr_code": prop["mr_code"], "consumer_name": owner.upper(), "address": prop["address"],
                "tariff": "LT1", "sanctioned_load": f"{prop['sanctioned_kw']}KW+0HP",
                "bill_period_start": fmt(start), "bill_period_end": fmt(end), "reading_date": fmt(end),
                "previous_reading": prev, "present_reading": pres, "constant": 1, "units_consumed": units,
                "recorded_md": f"{rng.choice([0.5,0.6,0.8,1.0,1.2])} KW", "power_factor": f"0.{rng.randint(95,99)}",
                "fixed_charges_rate": fixed_rate, "fixed_charges": fixed,
                "energy_charges_rate": e_rate, "energy_charges": energy,
                "fppca_rate": fp_rate, "fppca": fppca, "pg_surcharge_rate": pg_rate, "pg_surcharge": pg,
                "tax": tax, "interest": interest, "debit_credit": credit, "arrears": arrears,
                "current_demand": cur, "net_payable": net, "due_date": fmt(end + timedelta(days=15)),
                "asd": rng.choice([0, 0, 540]),
            }
            bills.append(dict(property_id=i, gt=gt))
            start, reading = end + timedelta(days=1), pres
    return owners, props, bills

def rows_for(g):
    r = []
    r.append(("sep",))
    def kv(l, v, b=False): r.append(("kv", l, v, b))
    kv("Account Details", None)
    kv("RR No", g["rr_number"], True); kv("Acc Id", g["account_id"], True); kv("M.R Code", g["mr_code"], True)
    kv("Name and Address", None); r.append(("txt", g["consumer_name"] + ","))
    a = g["address"]; cut = a.rfind(",", 0, 44) + 1 if len(a) > 44 else len(a)
    r.append(("txt", a[:cut].strip())); 
    if a[cut:].strip(): r.append(("txt", a[cut:].strip()))
    kv("Tariff", g["tariff"]); kv("Sanc Load", g["sanctioned_load"]); r.append(("sep",))
    kv("Bill Period", f'{g["bill_period_start"]} - {g["bill_period_end"]}')
    kv("Rdng. Date", g["reading_date"]); kv("Bill No", g["bill_number"]); r.append(("sep",))
    kv("Pres. Rdg.", str(g["present_reading"])); kv("Prev. Rdg.", str(g["previous_reading"]))
    kv("Constant", "1"); kv("Consumption (Units)", str(g["units_consumed"]))
    kv("Recorded MD", g["recorded_md"]); kv("Power Factor", g["power_factor"]); r.append(("sep",))
    kv("Bill for Consumed Units", None)
    kv("Fixed Charges (Unit, Rate, Amount)", None)
    r.append(("t", f'{g["sanctioned_load"].split("KW")[0]}.00 KW', str(g["fixed_charges_rate"]), m2(g["fixed_charges"])))
    kv("Energy Charges (Unit, Rate, Amount)", None)
    r.append(("t", f'{g["units_consumed"]}.0', str(g["energy_charges_rate"]), m2(g["energy_charges"])))
    kv("FPPCA Charges (Unit, Rate, Amt)", None)
    r.append(("t", f'{g["units_consumed"]}.00', f'{g["fppca_rate"]:.2f}', m2(g["fppca"])))
    kv("P & G Surcharge (Unit, Rate, Amt)", None)
    r.append(("t", f'{g["units_consumed"]}.0', f'{g["pg_surcharge_rate"]:.2f}', m2(g["pg_surcharge"])))
    kv("Tax @ 9.0%", m2(g["tax"])); r.append(("sep",))
    kv("PF Penalty", "0.00"); kv("Ex.Load/MD Penalty", "0.00"); kv("Interest", m2(g["interest"]))
    kv("Debit or Credit", m2(g["debit_credit"])); kv("Cur.Demand Payable", "Rs " + m2(g["current_demand"]), True)
    kv("Arrears", m2(g["arrears"])); r.append(("sep",))
    kv("Net Payable", "Rs " + m2(g["net_payable"]), True); kv("Due Date", g["due_date"], True)
    r.append(("ce", f'ASD to be paid: Rs. {g["asd"]} (ignore if paid)', False)); r.append(("sep",))
    r.append(("ce", "*** SAMPLE - NOT A VALID BILL - SYNTHETIC DATA ***", True))
    return r

def render_clean(g, W=600):
    fe, feb = ImageFont.truetype(FONT, 16), ImageFont.truetype(FONT_B, 17)
    fh = ImageFont.truetype(FONT_B, 19)
    img = Image.new("L", (W, 2400), 238); d = ImageDraw.Draw(img); y, M = 24, 28
    for t in (UTILITY, "ELECTRICITY BILL", f'O/o.AEE(Ele.) SAMPLE-DIVISION'):
        w = d.textlength(t, font=fh); d.text(((W - w) / 2, y), t, font=fh, fill=20); y += 32
    y += 8
    for r in rows_for(g):
        k = r[0]
        if k == "sep": d.text((M, y), "- " * int((W - 2 * M) / d.textlength("- ", font=fe)), font=fe, fill=40); y += 18
        elif k == "txt": d.text((M, y), r[1], font=feb, fill=20); y += 26
        elif k == "ce":
            w = d.textlength(r[1], font=feb if r[2] else fe); d.text(((W - w) / 2, y), r[1], font=feb if r[2] else fe, fill=20); y += 30
        elif k == "t":
            d.text((M + 4, y), r[1], font=feb, fill=20); d.text((W / 2 - 30, y), r[2], font=feb, fill=20)
            w = d.textlength(r[3], font=feb); d.text((W - M - w, y), r[3], font=feb, fill=20); y += 28
        else:
            d.text((M, y), r[1], font=fe, fill=30)
            if r[2]: 
                w = d.textlength(r[2], font=feb); d.text((W - M - w, y), r[2], font=feb, fill=15)
            y += 30
    H = y + 30; img = img.crop((0, 0, W, H))
    wm = Image.new("L", (W, H), 0); ImageDraw.Draw(wm).text((70, H // 2 - 50), "SAMPLE", font=ImageFont.truetype(FONT_B, 90), fill=255)
    wm = wm.rotate(25, resample=Image.BICUBIC)
    img = Image.composite(Image.new("L", (W, H), 175), img, wm.point(lambda v: v // 6))
    return img

def add_noise(img, level, rng, nrng):
    """level: 'clean' | 'medium' | 'noisy'. Returns RGB image."""
    if level == "clean":
        a = np.asarray(img, dtype=np.float32) + nrng.normal(0, 3, img.size[::-1])
        return Image.fromarray(np.clip(a, 0, 255).astype("uint8")).convert("RGB"), 92
    medium = level == "medium"
    ang = rng.uniform(-0.8, 0.8) if medium else rng.choice([-1, 1]) * rng.uniform(0.9, 1.5)
    pad = 40 if medium else 70
    W, H = img.size
    bg_col = (176, 138, 101) if not medium else (205, 195, 180)
    canvas = Image.new("RGB", (W + 2 * pad, H + 2 * pad), bg_col)
    cn = nrng.normal(0, 10, (canvas.size[1], canvas.size[0], 1))
    canvas = Image.fromarray(np.clip(np.asarray(canvas, dtype=np.float32) + cn, 0, 255).astype("uint8"))
    bill = img.convert("RGB").rotate(ang, resample=Image.BICUBIC, expand=True, fillcolor=bg_col)
    canvas.paste(bill, ((canvas.width - bill.width) // 2, (canvas.height - bill.height) // 2))
    a = np.asarray(canvas, dtype=np.float32)
    yy = np.linspace(0, 1, a.shape[0])[:, None, None]
    shade = 1 - (0.10 if medium else 0.35) * yy * rng.choice([0, 1]) - (0.04 if medium else 0.15) * np.linspace(0, 1, a.shape[1])[None, :, None]
    a = a * shade
    mean = a.mean(); a = (a - mean) * (0.92 if medium else 0.72) + mean + (0 if medium else 12)
    a = a + nrng.normal(0, 6 if medium else 15, a.shape)
    out = Image.fromarray(np.clip(a, 0, 255).astype("uint8"))
    out = out.filter(ImageFilter.GaussianBlur(0.7 if medium else 1.3))
    return out, (75 if medium else 45)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out"); ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--count", type=int, default=20)
    a = ap.parse_args()
    rng = random.Random(a.seed); nrng = np.random.default_rng(a.seed)
    owners, props, bills = make_data(rng)
    rng.shuffle(bills); bills = bills[:a.count]
    n = len(bills); levels = (["clean"] * ((n + 2) // 3) + ["medium"] * ((n + 1) // 3) + ["noisy"] * (n // 3))
    rng.shuffle(levels)
    out = Path(a.out); (out / "bills").mkdir(parents=True, exist_ok=True); (out / "ground_truth").mkdir(exist_ok=True)
    manifest = []
    for i, (b, lv) in enumerate(zip(bills, levels), start=1):
        name = f"bill_{i:03d}"
        img, q = add_noise(render_clean(b["gt"]), lv, rng, nrng)
        img.save(out / "bills" / f"{name}.jpg", quality=q)
        (out / "ground_truth" / f"{name}.json").write_text(json.dumps(b["gt"], indent=2))
        manifest.append(dict(bill_id=name, property_id=b["property_id"], account_id=b["gt"]["account_id"],
                             bill_period_start=b["gt"]["bill_period_start"], noise_level=lv,
                             image=f"bills/{name}.jpg", ground_truth=f"ground_truth/{name}.json"))
    used = {m["property_id"] for m in manifest}
    (out / "seed.json").write_text(json.dumps(dict(seed=a.seed, owners=owners,
        properties=[p for p in props if p["property_id"] in used], bills=manifest), indent=2))
    print(f"wrote {n} bills to {out}; noise mix:", {l: levels.count(l) for l in set(levels)})

if __name__ == "__main__": main()
