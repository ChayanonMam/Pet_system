"""
Pet Clinic System — report.py
================================
สร้างรายงาน .txt 3 ไฟล์ (1 ไฟล์ = 1 ตาราง, JOIN จาก owners/pets/visits/doctors .dat)

  1. report_overview.txt       ภาพรวมคลินิก      : รายสัตวแพทย์ (1 แถว = 1 หมอ: งานที่ทำแล้ว/นัดที่รอ/รายได้)
  2. report_owner_history.txt  ประวัติเจ้าของ     : Visit ที่ตรวจเสร็จแล้ว ทั้งหมดของ Owner ที่เลือก
  3. report_upcoming.txt       นัดหมายล่วงหน้า   : Visit ที่จองไว้ (ยังไม่ตรวจ) ทั้งหมดในระบบ

ไม่มีการตัดช่วง 30 วัน: ข้อมูลใหม่ที่เพิ่ม/แก้/ลบ แล้วสั่งสร้างรายงานซ้ำ จะสะท้อนในรายงานเสมอ
"History" กับ "Upcoming" แบ่งตามสถานะของ Visit (ตรวจเสร็จแล้ว / จองไว้) ส่วนนัดที่ยกเลิกไม่แสดงในรายงาน
"""

import os
import datetime
import unicodedata

from main import (
    DATA_DIR,
    APP_VERSION,
    get_data_snapshot,
    now_local,
    log_action,
    choose_owner,
    STATUS_BOOKED,
    STATUS_COMPLETED,
    STATUS_CANCELLED,
)

REPORT_OVERVIEW_FILE = os.path.join(DATA_DIR, "report_overview.txt")
REPORT_OWNER_HISTORY_FILE = os.path.join(DATA_DIR, "report_owner_history.txt")
REPORT_UPCOMING_FILE = os.path.join(DATA_DIR, "report_upcoming.txt")

REPORT_WIDTH = 92            # ความกว้างขั้นต่ำของเส้นคั่น (ขยายอัตโนมัติถ้าตารางกว้างกว่า)
TABLE_MAX_COL_WIDTH = 24
DIVIDER = "=" * REPORT_WIDTH
SUB_DIVIDER = "-" * REPORT_WIDTH

# ความกว้างของ "สระบน/สระล่าง/วรรณยุกต์" ไทยในตาราง
#   0 = ไม่กินที่ (Windows Terminal, VS Code terminal, macOS/Linux terminal, ฟอนต์ monospace ไทย)
#   1 = กินที่ 1 ช่อง (บางโปรแกรม เช่น cmd.exe แบบเก่า ที่นับทีละ code point)
# ถ้าเปิดแล้วตารางยังเหลื่อม ให้ลองสลับค่านี้เป็น 1
THAI_MARK_WIDTH = 0

# ตัวแทนเส้นคั่น — ถูกแทนด้วยเส้นจริงตอนจบ (_finalize) เพื่อให้กว้างเท่าตารางที่กว้างที่สุด
_DIV = "\x00DIV"
_SUB = "\x00SUB"


# ----------------------------------------------------------------------
# ความกว้างตัวอักษร (รองรับภาษาไทย)
# ----------------------------------------------------------------------

def _char_width(ch):
    """ความกว้างของอักขระบนจอ monospace
    สระบน/ล่าง (ั ิ ี ึ ื ุ ู ็), วรรณยุกต์ (่ ้ ๊ ๋), ์ ฯลฯ เป็นอักขระ Mn = กว้าง 0
    (หมายเหตุ: unicodedata.combining() ไม่ครอบคลุมสระ ั ิ ี ึ ื ็ จึงใช้ category แทน)"""
    if unicodedata.category(ch) in ("Mn", "Me", "Cf") or unicodedata.combining(ch):
        return THAI_MARK_WIDTH
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def disp_len(text):
    """ความกว้างที่แสดงจริงบนหน้าจอ"""
    return sum(_char_width(ch) for ch in str(text))


def clip_text(text, max_width=TABLE_MAX_COL_WIDTH):
    text = str(text)
    if disp_len(text) <= max_width:
        return text

    kept = []
    width = 0
    limit = max_width - 2
    for ch in text:
        w = _char_width(ch)
        if width + w > limit:
            break
        kept.append(ch)
        width += w
    return "".join(kept) + ".."


def _pad(text, width, align="l"):
    fill = " " * max(0, width - disp_len(text))
    if align == "r":
        return fill + text
    if align == "c":
        left = len(fill) // 2
        return " " * left + text + " " * (len(fill) - left)
    return text + fill


def _has_thai(text):
    return any("\u0e00" <= ch <= "\u0e7f" for ch in str(text))


TAIL_SEP = "  /  "


def render_table(headers, rows, aligns=None, max_col_width=TABLE_MAX_COL_WIDTH):
    """สร้างตารางข้อความที่ไม่แตกแม้เปิดในโปรแกรมที่วาดภาษาไทยกว้างไม่เท่ากัน

    ปัญหา: ฟอนต์ภาษาไทยในเทอร์มินัล/Notepad ส่วนใหญ่ไม่ใช่ monospace จริง ตัวอักษรไทยแต่ละ
    ตัวกว้างไม่เท่ากับ 1 ช่อง ทำให้เส้น | ที่อยู่ "หลัง" ข้อความไทยเหลื่อมไปเรื่อยๆ

    วิธีแก้: คอลัมน์ที่เป็นตัวเลข/อังกฤษล้วนจะอยู่ซ้ายและเรียงตรงเป๊ะ ส่วนคอลัมน์ที่มีภาษาไทย
    จะถูกรวมเป็นคอลัมน์สุดท้ายคอลัมน์เดียว (คั่นด้วย /) และไม่มีเส้นปิดด้านขวา
    จึงไม่มีเส้นไหนอยู่หลังข้อความไทยให้เหลื่อม
    ตารางที่ไม่มีภาษาไทยเลยจะเป็นกรอบเต็มตามปกติ"""
    headers = [str(h) for h in headers]
    rows = [[clip_text(c, max_col_width) for c in row] for row in rows]
    n = len(headers)
    aligns = list(aligns or []) + ["l"] * max(0, n - len(aligns or []))

    # กันข้อมูลที่มีจำนวนช่องไม่เท่ากับ header
    norm = []
    for row in rows:
        row = list(row[:n])
        row += [""] * (n - len(row))
        norm.append(row)

    empty_msg = "(ไม่มีข้อมูล)"
    thai_cols = [i for i in range(n) if any(_has_thai(r[i]) for r in norm)]
    tail = bool(thai_cols)

    if tail:
        latin = [i for i in range(n) if i not in thai_cols]
        head_cells = [headers[i] for i in latin] + [" / ".join(headers[i] for i in thai_cols)]
        body = [[r[i] for i in latin] + [TAIL_SEP.join(r[i] for i in thai_cols)] for r in norm]
        col_aligns = [aligns[i] for i in latin] + ["l"]
    else:
        head_cells, body, col_aligns = headers, norm, aligns

    m = len(head_cells)
    widths = [disp_len(h) for h in head_cells]
    for row in body:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], disp_len(cell))
    if not body:
        widths[0] = max(widths[0], disp_len(empty_msg))

    if tail:
        # ตารางแบบเปิดด้านขวา: คอลัมน์สุดท้ายไม่มีเส้นปิด
        border = "+" + "+".join("-" * (w + 2) for w in widths[:-1]) + "+" + "-" * (widths[-1] + 2)

        def fmt(row, row_aligns):
            cells = [_pad(str(c), widths[i], row_aligns[i]) for i, c in enumerate(row[:-1])]
            return "| " + " | ".join(cells + [str(row[-1])]) if m > 1 else "| " + str(row[0])
    else:
        border = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

        def fmt(row, row_aligns):
            return "| " + " | ".join(
                _pad(str(c), widths[i], row_aligns[i]) for i, c in enumerate(row)
            ) + " |"

    result = [border, fmt(head_cells, ["c"] * (m - 1) + ["l"] if tail else ["c"] * m), border]
    if body:
        result.extend(fmt(row, col_aligns) for row in body)
    else:
        result.append(fmt([empty_msg] + [""] * (m - 1), ["l"] * m))
    result.append(border)
    return result


# ----------------------------------------------------------------------
# โครงหน้ารายงาน: 1. หัวเรื่อง / 2. ข้อมูล/เนื้อหา / 3. สรุป
# ----------------------------------------------------------------------

def _report_header(title, generated_at):
    """ส่วนที่ 1: หัวเรื่อง"""
    return [
        _DIV,
        f"Pet Clinic System — {title}",
        f"Generated At : {generated_at}",
        f"App Version  : {APP_VERSION}",
        _DIV,
        "",
    ]


def _content_start():
    """ส่วนที่ 2: ข้อมูล/เนื้อหา (ไม่มีป้ายหัวข้อ — ต่อจากหัวรายงานได้เลย)"""
    return []


def _summary_start():
    """ส่วนที่ 3: สรุป"""
    return ["สรุป", _SUB]


def _caption(lines, text, count=None):
    """หัวข้อย่อยของตาราง เช่น Owners — เจ้าของทั้งหมด (5 รายการ)"""
    suffix = f"  ({count} รายการ)" if count is not None else ""
    lines.append(f"{text}{suffix}")


def _kv_lines(pairs, indent=""):
    """บรรทัดสรุปแบบ 'ป้ายชื่อ : ค่า' จัดให้เครื่องหมาย : ตรงกันทุกบรรทัด"""
    width = max((disp_len(k) for k, _ in pairs), default=0)
    return [f"{indent}{k}{' ' * (width - disp_len(k))} : {v}" for k, v in pairs]


def _footer():
    return ["", _DIV, "จบรายงาน", _DIV, ""]


def _finalize(lines):
    """แทนเส้นคั่นด้วยเส้นจริง โดยกว้างเท่าบรรทัดที่ยาวที่สุด (ไม่ให้ตารางล้นเส้น)"""
    width = max([REPORT_WIDTH] + [disp_len(l) for l in lines if l not in (_DIV, _SUB)])
    out = []
    for line in lines:
        if line == _DIV:
            out.append("=" * width)
        elif line == _SUB:
            out.append("-" * width)
        else:
            out.append(line)
    return "\n".join(out)



def _status(rec):
    return "Active" if rec["is_active"] else "Deleted"


# ----------------------------------------------------------------------
# ข้อมูลรายงาน — อ่าน 4 .dat ใหม่ทุกครั้งที่สั่งสร้าง (ไม่มี cache)
# ----------------------------------------------------------------------

def _parse_dt(visit):
    try:
        return datetime.datetime.strptime(
            f"{visit['visit_date']} {visit['visit_time']}", "%Y-%m-%d %H:%M"
        )
    except ValueError:
        return None


def build_report_data():
    """อ่านข้อมูล 4 ไฟล์ครั้งเดียว และใช้ข้อมูลชุดเดียวกันกับทั้ง 3 รายงาน"""
    snapshot = get_data_snapshot(include_logs=False)
    now = now_local()
    maps = {
        "owners": {o["owner_id"]: o for o in snapshot["owners"]},
        "pets": {p["pet_id"]: p for p in snapshot["pets"]},
        "doctors": {d["doctor_id"]: d for d in snapshot["doctors"]},
    }

    rows = []   # Visit ที่ Active ทุกรายการ ที่ JOIN แล้ว
    for v in snapshot["visits"]:
        if not v["is_active"] or v["status"] == STATUS_CANCELLED:
            continue                      # ข้าม Visit ที่ลบ และนัดที่ยกเลิก
        pet = maps["pets"].get(v["pet_id"], {})
        owner = maps["owners"].get(pet.get("owner_id"), {})
        doctor = maps["doctors"].get(v["doctor_id"], {})
        rows.append({
            "visit_id": v["visit_id"],
            "date": v["visit_date"],
            "time": v["visit_time"],
            "dt": _parse_dt(v),
            "owner_id": owner.get("owner_id", "?"),
            "owner_name": owner.get("name", "?"),
            "pet_id": pet.get("pet_id", v["pet_id"]),
            "pet_name": pet.get("name", "?"),
            "species": pet.get("species", "?"),
            "doctor_id": doctor.get("doctor_id", v["doctor_id"]),
            "doctor_name": doctor.get("name", "?"),
            "reason": v["reason"],
            "cost": float(v["cost"]),
            "status": v["status"],
        })

    # แบ่งตามสถานะ (ไม่ใช่ตามเวลา): ตรวจเสร็จแล้ว = History, จองไว้ = Upcoming
    # นัดที่เลยเวลาแต่ยังไม่ปิด จึงยังอยู่ใน Upcoming จนกว่าจะกด "ตรวจเสร็จ" หรือ "ยกเลิก"
    past = [r for r in rows if r["status"] == STATUS_COMPLETED]
    future = [r for r in rows if r["status"] == STATUS_BOOKED]

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "owners": maps["owners"],
        "doctors": maps["doctors"],
        "visits": rows,
        "past": past,
        "future": future,
        "counts": {
            "owners": sum(1 for o in snapshot["owners"] if o["is_active"]),
            "pets": sum(1 for p in snapshot["pets"] if p["is_active"]),
            "doctors": sum(1 for d in snapshot["doctors"] if d["is_active"]),
        },
    }


def _tag(rec_id, name):
    """รวมรหัสกับชื่อไว้ในช่องเดียว เช่น '#1 สมชาย'"""
    return f"#{rec_id} {name}"


def _by_time(r):
    return (r["date"], r["time"], r["visit_id"])


# ----------------------------------------------------------------------
# REPORT 1 — ภาพรวมคลินิก (1 แถว = 1 สัตวแพทย์)
# ----------------------------------------------------------------------

def render_overview_report(data):
    """1 ตาราง: ภาระงานและรายได้ของสัตวแพทย์แต่ละคน
    JOIN Doctor + Visit + Pet + Owner (ไม่ใช่รายการ Visit — รายการอยู่ใน History / Upcoming)"""
    by_doc = {}
    for r in data["visits"]:
        by_doc.setdefault(r["doctor_id"], []).append(r)

    # หมอที่ Active ทุกคน + หมอที่มี Visit (กรณีหมอถูกลบแต่ยังมีประวัติ) — ยอดรวมจึงตรงกับ Visit ทั้งหมด
    doctor_ids = sorted(
        {d_id for d_id, d in data["doctors"].items() if d["is_active"]} | set(by_doc)
    )

    rows = []
    for d_id in doctor_ids:
        d = data["doctors"].get(d_id, {})
        vs = by_doc.get(d_id, [])
        done = [v for v in vs if v in data["past"]]
        pending = [v for v in vs if v in data["future"]]
        rows.append([
            _tag(d_id, d.get("name", "?")),
            d.get("specialty", "?"),
            f"{float(d.get('fee', 0.0)):.2f}",
            len(done), len(pending),
            len({v["pet_id"] for v in vs}),
            len({v["owner_id"] for v in vs}),
            f"{sum(v['cost'] for v in done):.2f}",
            f"{sum(v['cost'] for v in pending):.2f}",
        ])

    visits = data["visits"]
    total_cost = sum(v["cost"] for v in visits)
    avg_cost = total_cost / len(visits) if visits else 0.0
    c = data["counts"]

    lines = _report_header("Pet Clinic — Clinic Overview", data["generated_at"])
    lines += _content_start()
    _caption(lines, "ภาพรวมคลินิกรายสัตวแพทย์ — ภาระงานและรายได้", len(rows))
    lines += render_table(
        ["Doctor", "Specialty", "Fee", "Done", "Upcoming", "Pets", "Owners", "Revenue", "Pending"],
        rows,
        ["l", "l", "r", "r", "r", "r", "r", "r", "r"],
        max_col_width=22,
    )
    lines.append("")

    lines += _summary_start()
    lines += _kv_lines([
        ("จำนวนเจ้าของ", f"{c['owners']} คน"),
        ("จำนวนสัตว์เลี้ยง", f"{c['pets']} ตัว"),
        ("จำนวนสัตวแพทย์", f"{c['doctors']} คน"),
        ("จำนวน Visit", f"{len(visits)} รายการ"),
        ("ค่าใช้บริการรวม", f"{total_cost:.2f} บาท"),
        ("ค่าใช้บริการเฉลี่ย", f"{avg_cost:.2f} บาท/ครั้ง"),
    ])
    lines += _footer()
    return _finalize(lines)


# ----------------------------------------------------------------------
# REPORT 2 — ประวัติเจ้าของ (Owner ที่เลือก, Visit ที่ตรวจเสร็จแล้ว ทั้งหมด)
# ----------------------------------------------------------------------

def render_owner_history_report(data, owner_id):
    """1 ตาราง: ประวัติการใช้บริการที่ตรวจเสร็จแล้วของ Owner 1 คน เรียงตามวันที่"""
    owner = data["owners"].get(owner_id)
    lines = _report_header(f"Pet Clinic — Owner {owner_id} Service History", data["generated_at"])
    lines += _content_start()

    if owner is None:
        lines += ["ไม่พบข้อมูลเจ้าของที่ระบุ", ""]
        lines += _summary_start()
        lines += _kv_lines([("Owner ID", owner_id)])
        lines += _footer()
        return _finalize(lines)

    past = sorted(
        (r for r in data["past"] if r["owner_id"] == owner_id),
        key=_by_time,
    )
    rows = [[
        _tag(r["pet_id"], r["pet_name"]),
        r["species"], r["date"], r["time"],
        _tag(r["doctor_id"], r["doctor_name"]),
        r["reason"], f"{r['cost']:.2f}",
    ] for r in past]

    total_cost = sum(r["cost"] for r in past)
    avg_cost = total_cost / len(past) if past else 0.0

    lines += _kv_lines([
        ("เจ้าของ", f"{owner['name']} (Owner ID {owner_id})"),
        ("เบอร์โทร", owner.get("phone", "")),
    ])
    lines.append("")
    _caption(lines, "ประวัติการใช้บริการของเจ้าของ — ทุกครั้งที่ใช้บริการแล้ว", len(rows))
    lines += render_table(
        ["Pet", "Type", "Date", "Time", "Doctor", "Reason", "Cost"],
        rows,
        ["l", "l", "l", "l", "l", "l", "r"],
        max_col_width=22,
    )
    lines.append("")

    lines += _summary_start()
    lines += _kv_lines([
        ("Owner ID", owner_id),
        ("ชื่อเจ้าของ", owner["name"]),
        ("สัตว์เลี้ยงที่เคยใช้บริการ", f"{len({r['pet_id'] for r in past})} ตัว"),
        ("จำนวนครั้งที่เข้ารับบริการ", f"{len(past)} ครั้ง"),
        ("ค่าใช้จ่ายรวม", f"{total_cost:.2f} บาท"),
        ("ค่าใช้จ่ายเฉลี่ย", f"{avg_cost:.2f} บาท/ครั้ง"),
    ])
    lines += _footer()
    return _finalize(lines)


# ----------------------------------------------------------------------
# REPORT 3 — นัดหมายล่วงหน้า (Visit ที่จองไว้ ยังไม่ตรวจ ทั้งหมด)
# ----------------------------------------------------------------------

def render_upcoming_report(data):
    """1 ตาราง: นัดหมายตั้งแต่ตอนนี้เป็นต้นไป เรียงจากใกล้ที่สุดไปไกลที่สุด"""
    upcoming = sorted(data["future"], key=_by_time)
    rows = [[
        r["date"], r["time"],
        _tag(r["owner_id"], r["owner_name"]),
        _tag(r["pet_id"], r["pet_name"]),
        r["species"],
        _tag(r["doctor_id"], r["doctor_name"]),
        r["reason"], f"{r['cost']:.2f}",
    ] for r in upcoming]

    total_cost = sum(r["cost"] for r in upcoming)

    lines = _report_header("Pet Clinic — Upcoming Appointments", data["generated_at"])
    lines += _content_start()
    _caption(lines, "นัดหมายล่วงหน้า — ทุกนัดที่จองไว้ (ยังไม่ตรวจ)", len(rows))
    lines += render_table(
        ["Date", "Time", "Owner", "Pet", "Type", "Doctor", "Reason", "Cost"],
        rows,
        ["l", "l", "l", "l", "l", "l", "l", "r"],
        max_col_width=22,
    )
    lines.append("")

    lines += _summary_start()
    lines += _kv_lines([
        ("จำนวนการนัดหมาย", f"{len(upcoming)} นัด"),
        ("จำนวนเจ้าของ", f"{len({r['owner_id'] for r in upcoming})} คน"),
        ("จำนวนสัตว์เลี้ยง", f"{len({r['pet_id'] for r in upcoming})} ตัว"),
        ("จำนวนสัตวแพทย์", f"{len({r['doctor_id'] for r in upcoming})} คน"),
        ("ค่าใช้จ่ายโดยประมาณรวม", f"{total_cost:.2f} บาท"),
    ])
    lines += _footer()
    return _finalize(lines)


# ----------------------------------------------------------------------
# เขียนไฟล์ + สั่งสร้างรายงาน
# ----------------------------------------------------------------------

def _write_report(path, content, label):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content + ("" if content.endswith("\n") else "\n"))

    print(DIVIDER)
    print(content)
    print(f"[{label}] บันทึกไว้ที่: {os.path.abspath(path)}")
    print(DIVIDER)


def generate_report(owner_id):
    """สร้างรายงานหลัก 3 ไฟล์ (1 ตาราง/ไฟล์) — ประวัติเป็นของ Owner ที่เลือก
    อ่านข้อมูลใหม่ทุกครั้ง"""
    data = build_report_data()
    if owner_id not in data["owners"]:
        print(f"ไม่พบ Owner ID {owner_id}")
        return False
    _write_report(REPORT_OVERVIEW_FILE, render_overview_report(data), "Clinic Overview")
    _write_report(REPORT_OWNER_HISTORY_FILE, render_owner_history_report(data, owner_id),
                  f"Owner {owner_id} Service History")
    _write_report(REPORT_UPCOMING_FILE, render_upcoming_report(data), "Upcoming Appointments")
    log_action(f"Generated all reports (owner history: Owner ID {owner_id})")
    print("สร้างรายงานสำเร็จ: 3 ไฟล์ / ไฟล์ละ 1 ตาราง")
    return True


if __name__ == "__main__":
    print("สร้างรายงานจาก owners.dat / pets.dat / doctors.dat / visits.dat ...")
    _, owner_rec = choose_owner()
    if owner_rec is not None:
        generate_report(owner_rec["owner_id"])