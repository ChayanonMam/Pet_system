"""
Pet Clinic System — main.py (ไฟล์หลักของระบบ)
==================================================
โครงสร้างไฟล์นี้ (เรียงจากล่างขึ้นบน):
  1.  CONFIG         ค่าคงที่ (path ไฟล์, struct format, field lists, ตารางราคา)
  2.  STORAGE        pack/unpack + BinaryStore (I/O ระดับล่าง)
  3.  STORES         instance owners/pets/doctors/visits ให้ใช้ร่วมกันทั้งไฟล์
  4.  LOGGER         บันทึก/อ่าน activity log
  5.  VALIDATORS     รับอินพุตพร้อมตรวจสอบ + choose_entity + รับวัน/เวลานัด
  6.  OWNER SERVICE  Add/Update/Delete Owner
  7.  PET SERVICE    Add/Update/Delete Pet
  8.  DOCTOR SERVICE Add/Update/Delete Doctor + เลือกหมอ + เช็คคิวซ้ำ + คิดราคา
  9.  VISIT SERVICE  Add/Update/Delete Visit (มีหมอ + เวลา + เช็คคิวชน)
  10. BOOKING        จองคิวรวดเร็ว: หลายสัตว์ -> เลือกหมอ -> คิดราคา -> นัดวันเวลา
  11. VIEW           View ทั้ง 6 แบบ + compute_summary() + join ข้ามไฟล์
  12. REPORT INTERFACE  เรียก report.py เพื่อสร้างรายงาน 3 ไฟล์
  13. SEED DATA      ใส่ข้อมูลตัวอย่างสำหรับทดสอบ
  14. MAIN MENU      เมนูหลัก (entry point)
"""

import os
import re
import sys
import shutil
import struct
import datetime
import math


# ======================================================================
# 1) CONFIG — ค่าคงที่ของระบบ
# ======================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
APP_VERSION = "1.4"
LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=7), name="Asia/Bangkok")
OWNER_FILE = os.path.join(DATA_DIR, "owners.dat")
PET_FILE = os.path.join(DATA_DIR, "pets.dat")
VISIT_FILE = os.path.join(DATA_DIR, "visits.dat")
DOCTOR_FILE = os.path.join(DATA_DIR, "doctors.dat")
LOG_FILE = os.path.join(DATA_DIR, "activity_log.txt")

# Endianness: Little-Endian ("<") ตามสเปกโครงการ

OWNER_FMT = "<I30s15s50s?"
OWNER_FIELDS = ["owner_id", "name", "phone", "address", "is_active"]

PET_FMT = "<II20s10s20sH?"
PET_FIELDS = ["pet_id", "owner_id", "name", "species", "breed",
              "birth_year", "is_active"]

# Visit เพิ่ม doctor_id + visit_time ("HH:MM" 6 ไบต์) -> record = 113 ไบต์
# v2: เพิ่ม status (1 ไบต์) ท้าย record -> record = 114 ไบต์
VISIT_FMT = "<III10s6s40s40sf?B"
VISIT_FIELDS = ["visit_id", "pet_id", "doctor_id", "visit_date", "visit_time",
                "reason", "diagnosis", "cost", "is_active", "status"]

# รูปแบบเดิม (v1, 113 ไบต์ ไม่มี status) — ใช้ตอนแปลงไฟล์เก่าอัตโนมัติ
VISIT_FMT_V1 = "<III10s6s40s40sf?"
VISIT_FIELDS_V1 = VISIT_FIELDS[:-1]

# สถานะของ Visit: จองไว้ -> ตรวจเสร็จแล้ว / ยกเลิก
STATUS_BOOKED = 0
STATUS_COMPLETED = 1
STATUS_CANCELLED = 2
VISIT_STATUS_EN = {STATUS_BOOKED: "Booked", STATUS_COMPLETED: "Completed",
                   STATUS_CANCELLED: "Cancelled"}
VISIT_STATUS_TH = {STATUS_BOOKED: "จองไว้", STATUS_COMPLETED: "ตรวจเสร็จแล้ว",
                   STATUS_CANCELLED: "ยกเลิก"}

DOCTOR_FMT = "<I30s30sf?"
DOCTOR_FIELDS = ["doctor_id", "name", "specialty", "fee", "is_active"]

# ตารางราคาอาการพื้นฐาน (บาท) — ราคารวม = อาการ + ค่าตรวจของหมอ (แก้ได้ตามใจ)
SYMPTOM_PRICES = {
    "ฉีดวัคซีน": 500.0,
    "ฉีดวัคซีนพิษ": 400.0,
    "ตรวจสุขภาพ": 300.0,
    "ตรวจฟัน": 800.0,
    "ขูดหินปูน": 1500.0,
    "ทำหมัน": 1200.0,
    "ผ่าตัด": 4500.0,
    "ผ่าตัดขา": 4500.0,
    "ตรวจแผล": 300.0,
    "ตรวจแผลผ่าตัด": 300.0,
    "ตัดขน": 350.0,
    "นัดติดตามผล": 250.0,
}
DEFAULT_SYMPTOM_PRICE = 300.0


def now_local():
    """เวลาปัจจุบันตามเขตเวลาไทย และคืนเป็น datetime แบบไม่มี timezone
    เพื่อใช้เทียบกับวัน/เวลาที่เก็บในไฟล์ visits.dat"""
    return datetime.datetime.now(LOCAL_TZ).replace(tzinfo=None)


# ======================================================================
# 2) STORAGE — pack/unpack + BinaryStore (I/O ระดับล่าง)
# ======================================================================

def _split_format(fmt):
    """แยก struct format string (ไม่รวม endianness char) เป็นชิ้นๆ
    เช่น "<I30s15s50s?" -> ["I", "30s", "15s", "50s", "?"]"""
    return re.findall(r"\d*[a-zA-Z?]", fmt[1:])


def pad_str(text, size):
    """แปลง str -> bytes ความยาวคงที่ size โดยไม่ตัด UTF-8 กลางตัวอักษร"""
    raw = text.encode("utf-8")
    if len(raw) > size:
        raise ValueError(f"ข้อความยาวเกิน {size} ไบต์")
    return raw.ljust(size, b"\x00")


def unpad_str(raw):
    """แปลง bytes fixed-length -> str, ตัด \\x00 ท้ายออก"""
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="ignore")


def pack_record(fmt, fields, record):
    parts = _split_format(fmt)
    values = []
    for part, field in zip(parts, fields):
        val = record[field]
        if part.endswith("s"):
            size = int(part[:-1]) if part[:-1] else 1
            values.append(pad_str(val, size))
        else:
            values.append(val)
    return struct.pack(fmt, *values)


def unpack_record(fmt, fields, data):
    parts = _split_format(fmt)
    values = struct.unpack(fmt, data)
    record = {}
    for part, field, val in zip(parts, fields, values):
        record[field] = unpad_str(val) if part.endswith("s") else val
    return record


class BinaryStore:
    """ตัวจัดการไฟล์ไบนารีหนึ่งไฟล์ (เช่น owners.dat) แบบ fixed-length record"""

    def __init__(self, path, fmt, fields, id_field):
        self.path = path
        self.fmt = fmt
        self.fields = fields
        self.id_field = id_field
        self.record_size = struct.calcsize(fmt)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            open(path, "wb").close()

    def count(self):
        """จำนวนช่อง (slot) ทั้งหมดในไฟล์ รวมทั้ง active และ deleted"""
        size = os.path.getsize(self.path)
        if size % self.record_size != 0:
            print(f"[คำเตือน] ไฟล์ {self.path} มีขนาดไม่ตรงกับ record size "
                  f"(อาจเสียหาย) — ระเบียนท้ายที่ไม่ครบจะถูกข้าม")
        return size // self.record_size

    def read_at(self, index):
        with open(self.path, "rb") as f:
            f.seek(index * self.record_size)
            data = f.read(self.record_size)
        if len(data) < self.record_size:
            return None
        try:
            return unpack_record(self.fmt, self.fields, data)
        except struct.error:
            return None

    def write_at(self, index, record):
        data = pack_record(self.fmt, self.fields, record)
        with open(self.path, "r+b") as f:
            f.seek(index * self.record_size)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    def append(self, record):
        data = pack_record(self.fmt, self.fields, record)
        with open(self.path, "ab") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

    def all_slots(self):
        """คืนค่า [(index, record), ...] โดยเปิดไฟล์ครั้งเดียวต่อการ scan"""
        result = []
        with open(self.path, "rb") as f:
            index = 0
            while True:
                data = f.read(self.record_size)
                if len(data) < self.record_size:
                    break
                try:
                    record = unpack_record(self.fmt, self.fields, data)
                except struct.error:
                    break
                result.append((index, record))
                index += 1
        return result

    def active_records(self):
        return [(i, r) for i, r in self.all_slots() if r["is_active"]]

    def find_free_slot(self):
        for i, r in self.all_slots():
            if not r["is_active"]:
                return i
        return None

    def add(self, record):
        """เพิ่มระเบียนใหม่ — ใช้ช่องว่าง (deleted) ก่อน ถ้าไม่มีค่อยต่อท้ายไฟล์"""
        slot = self.find_free_slot()
        if slot is not None:
            self.write_at(slot, record)
            return slot
        self.append(record)
        return self.count() - 1

    def next_id(self):
        """หา id ถัดไป โดยดูค่ามากที่สุดจากทุกระเบียน (รวม deleted) แล้ว +1"""
        max_id = 0
        for _, r in self.all_slots():
            if r[self.id_field] > max_id:
                max_id = r[self.id_field]
        return max_id + 1

    def find_by_id(self, id_value, active_only=True):
        for i, r in self.all_slots():
            if r[self.id_field] == id_value:
                if active_only and not r["is_active"]:
                    continue
                return i, r
        return None, None


# ======================================================================
# 3) STORES — instance ของแต่ละไฟล์ข้อมูล ใช้ร่วมกันทั้งไฟล์นี้
# ======================================================================

owners = BinaryStore(OWNER_FILE, OWNER_FMT, OWNER_FIELDS, "owner_id")
pets = BinaryStore(PET_FILE, PET_FMT, PET_FIELDS, "pet_id")
doctors = BinaryStore(DOCTOR_FILE, DOCTOR_FMT, DOCTOR_FIELDS, "doctor_id")
visits = BinaryStore(VISIT_FILE, VISIT_FMT, VISIT_FIELDS, "visit_id")


def migrate_visits_if_needed():
    """แปลง visits.dat แบบเก่า (113 ไบต์/record ไม่มี status) เป็นแบบใหม่ (114 ไบต์)
    - สำรองไฟล์เดิมเป็น visits.dat.bak_v1 ก่อนเสมอ
    - นัดที่เลยเวลาแล้ว -> ตรวจเสร็จแล้ว, นัดที่ยังมาไม่ถึง -> จองไว้
    ทำงานครั้งเดียว: ไฟล์ใหม่ขนาดหาร 114 ลงตัวจึงไม่ถูกแปลงซ้ำ"""
    old_size = struct.calcsize(VISIT_FMT_V1)
    new_size = struct.calcsize(VISIT_FMT)
    size = os.path.getsize(VISIT_FILE)
    if size == 0 or size % new_size == 0 or size % old_size != 0:
        return False

    backup = VISIT_FILE + ".bak_v1"
    if os.path.exists(backup):
        backup += "." + now_local().strftime("%Y%m%d%H%M%S")
    shutil.copy2(VISIT_FILE, backup)

    now = now_local()
    converted = []
    with open(VISIT_FILE, "rb") as f:
        while True:
            data = f.read(old_size)
            if len(data) < old_size:
                break
            rec = unpack_record(VISIT_FMT_V1, VISIT_FIELDS_V1, data)
            try:
                dt = datetime.datetime.strptime(
                    f"{rec['visit_date']} {rec['visit_time']}", "%Y-%m-%d %H:%M")
                rec["status"] = STATUS_COMPLETED if dt <= now else STATUS_BOOKED
            except ValueError:
                rec["status"] = STATUS_COMPLETED
            converted.append(rec)

    with open(VISIT_FILE, "wb") as f:
        for rec in converted:
            f.write(pack_record(VISIT_FMT, VISIT_FIELDS, rec))
    print(f"[แปลงไฟล์] visits.dat -> รูปแบบใหม่ (เพิ่มสถานะ) {len(converted)} รายการ "
          f"| สำรองไว้ที่ {os.path.basename(backup)}")
    return True


migrate_visits_if_needed()


def visit_status_en(rec):
    """ข้อความสถานะสำหรับตาราง: Deleted / Booked / Completed / Cancelled"""
    if not rec["is_active"]:
        return "Deleted"
    return VISIT_STATUS_EN.get(rec["status"], "?")


# ======================================================================
# 4) LOGGER — บันทึก/อ่านกิจกรรม (activity log)
# ======================================================================

def log_action(message):
    os.makedirs(DATA_DIR, exist_ok=True)
    ts = now_local().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {message}\n")


def read_recent_logs(n=5, exclude_report_generation=True):
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f]
    if exclude_report_generation:
        lines = [line for line in lines
                 if "] Generated report_" not in line
                 and "] Generated all reports" not in line]
    return lines[-n:]


# ======================================================================
# 5) VALIDATORS — รับอินพุตพร้อมตรวจสอบความถูกต้อง
# ======================================================================

def input_int(prompt, min_val=None, max_val=None):
    while True:
        raw = input(prompt).strip()
        try:
            val = int(raw)
        except ValueError:
            print("  -> กรุณากรอกเป็นตัวเลขจำนวนเต็ม")
            continue
        if min_val is not None and val < min_val:
            print(f"  -> ค่าต้องไม่น้อยกว่า {min_val}")
            continue
        if max_val is not None and val > max_val:
            print(f"  -> ค่าต้องไม่เกิน {max_val}")
            continue
        return val


def input_float(prompt, min_val=0):
    while True:
        raw = input(prompt).strip()
        try:
            val = float(raw)
        except ValueError:
            print("  -> กรุณากรอกเป็นตัวเลข")
            continue
        if not math.isfinite(val):
            print("  -> กรุณากรอกค่าตัวเลขที่เป็นจำนวนจริง")
            continue
        if val < min_val:
            print(f"  -> ค่าต้องไม่น้อยกว่า {min_val}")
            continue
        return val


def input_str(prompt, max_bytes, allow_empty=False):
    while True:
        raw = input(prompt).strip()
        if not raw and not allow_empty:
            print("  -> ห้ามเว้นว่าง")
            continue
        if len(raw.encode("utf-8")) > max_bytes:
            print(f"  -> ข้อความยาวเกินไป (สูงสุด {max_bytes} ไบต์)")
            continue
        return raw


def input_time(prompt):
    """รับเวลา HH:MM แล้ว normalize (เช่น "9:30" -> "09:30") กันการเช็คคิวเพี้ยน"""
    while True:
        raw = input(prompt).strip()
        try:
            t = datetime.datetime.strptime(raw, "%H:%M")
            return t.strftime("%H:%M")
        except ValueError:
            print("  -> รูปแบบเวลาไม่ถูกต้อง ต้องเป็น HH:MM เช่น 09:30")


def input_appointment(future_only=False):
    """ถามวัน/เดือน/ปี/เวลา และคืนค่า (date, time) ที่ผ่านการตรวจแล้ว
    future_only=True จะบังคับให้นัดเป็นเวลาปัจจุบันหรืออนาคต"""
    while True:
        day = input_int("วัน (1-31): ", 1, 31)
        month = input_int("เดือน (1-12): ", 1, 12)
        year = input_int("ปี (ค.ศ.): ", 2000, 2100)
        time_str = input_time("เวลา (HH:MM): ")
        try:
            appointment_dt = datetime.datetime(year, month, day,
                                               int(time_str[:2]), int(time_str[3:5]))
        except ValueError:
            print("  -> วันที่นี้ไม่มีจริงในปฏิทิน (เช่น 31 ก.พ.) กรุณากรอกใหม่")
            continue
        if future_only and appointment_dt < now_local():
            print("  -> นัดหมายต้องเป็นวันเวลาในปัจจุบันหรืออนาคต กรุณากรอกใหม่")
            continue
        return f"{year:04d}-{month:02d}-{day:02d}", time_str


def input_yn(prompt):
    while True:
        raw = input(prompt + " (y/n): ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  -> กรุณาตอบ y หรือ n")


def pause():
    input("\nกด Enter เพื่อกลับสู่เมนู...")


MENU_WIDTH = 64


def print_menu(title, items, zero_label):
    """แสดงเมนูหน้าตาเดียวกันทุกเมนู
    items = รายการที่เป็น  str            -> หัวข้อกลุ่ม
                           (เลข, ชื่ออังกฤษ, คำอธิบายไทย) -> ตัวเลือก
    ชื่ออังกฤษถูกจัดคอลัมน์ให้ตรงกัน ส่วนคำอธิบายไทยวางท้ายบรรทัด (ภาษาไทยจึงไม่ทำให้แนวเหลื่อม)"""
    label_w = max((len(it[1]) for it in items if not isinstance(it, str)), default=0)
    print()
    print("=" * MENU_WIDTH)
    print(f" {title}")
    print("=" * MENU_WIDTH)
    first = True
    for it in items:
        if isinstance(it, str):
            if not first:
                print()
            print(f" [{it}]")
        else:
            num, label, desc = it
            print(f"   {num}) {label.ljust(label_w)}  {desc}")
        first = False
    print("-" * MENU_WIDTH)
    print(f"   0) {zero_label}")
    print("=" * MENU_WIDTH)


def choose_entity(prompt="เลือกประเภทข้อมูล"):
    print_menu(prompt, [
        (1, "Owner",  "เจ้าของ"),
        (2, "Pet",    "สัตว์เลี้ยง"),
        (3, "Visit",  "ประวัติรักษา / นัดหมาย"),
        (4, "Doctor", "สัตวแพทย์"),
    ], "ย้อนกลับ")
    return input_int("เลือกหมายเลข: ", 0, 4)


def choose_record(records, title, formatter, zero_label="Cancel (ยกเลิก)"):
    """แสดงรายการเป็นหมายเลขสั้น ๆ แล้วคืน (index, record)
    ช่วยให้ผู้ใช้ไม่ต้องจำ ID เอง"""
    if not records:
        print(f"\n{title}")
        print("  (No data - ไม่มีข้อมูล)")
        return None, None

    print(f"\n{title}")
    for number, (store_index, record) in enumerate(records, start=1):
        print(f"  {number}) {formatter(record)}")
    print(f"  0) {zero_label}")

    choice = input_int("Select number (เลือกหมายเลข): ", 0, len(records))
    if choice == 0:
        return None, None
    return records[choice - 1]


def choose_owner(allow_new=False, include_deleted=False):
    records = owners.all_slots() if include_deleted else owners.active_records()
    records = sorted(records, key=lambda x: x[1]["owner_id"])
    zero_label = "Add New Owner (เพิ่มเจ้าของใหม่)" if allow_new else "Cancel (ยกเลิก)"
    idx, rec = choose_record(
        records,
        "Select Owner (เลือกเจ้าของ)",
        lambda r: f"Owner ID {r['owner_id']} | {r['name']} | โทร {r['phone']}"
                 + (" | Deleted" if not r['is_active'] else ""),
        zero_label=zero_label,
    )
    if idx is None and allow_new:
        return None, None
    return idx, rec


def choose_pet(owner_id=None, include_deleted=False):
    records = pets.all_slots() if include_deleted else pets.active_records()
    if owner_id is not None:
        records = [(i, p) for i, p in records if p['owner_id'] == owner_id]
    records = sorted(records, key=lambda x: x[1]["pet_id"])
    return choose_record(
        records,
        "Select Pet (เลือกสัตว์เลี้ยง)",
        lambda r: f"Pet ID {r['pet_id']} | {r['name']} ({r['species']}/{r['breed']}) | Owner {r['owner_id']}"
                 + (" | Deleted" if not r['is_active'] else ""),
    )


def choose_visit(include_deleted=False):
    records = visits.all_slots() if include_deleted else visits.active_records()
    records = sorted(records, key=lambda x: (x[1]["visit_date"], x[1]["visit_time"], x[1]["visit_id"]))
    return choose_record(
        records,
        "Select Visit (เลือกประวัติ/นัดหมาย)",
        lambda r: f"Visit ID {r['visit_id']} | {r['visit_date']} {r['visit_time']} | Pet {r['pet_id']} | Doctor {r['doctor_id']} | {r['reason']}"
                 + f" | {visit_status_en(r)}",
    )


# ======================================================================
# 6) OWNER SERVICE — Add / Update / Delete สำหรับเจ้าของ
# ======================================================================

def create_owner_record(name, phone, address, log_suffix=""):
    oid = owners.next_id()
    record = {
        "owner_id": oid, "name": name, "phone": phone, "address": address,
        "is_active": True,
    }
    owners.add(record)
    suffix = f" {log_suffix}" if log_suffix else ""
    log_action(f"Added Owner ID {oid} \"{name}\"{suffix}")
    return record


def create_pet_record(owner_id, name, species, breed, birth_year, log_suffix=""):
    pid = pets.next_id()
    record = {
        "pet_id": pid, "owner_id": owner_id, "name": name, "species": species,
        "breed": breed, "birth_year": birth_year, "is_active": True,
    }
    pets.add(record)
    suffix = f" {log_suffix}" if log_suffix else ""
    log_action(f"Added Pet ID {pid} \"{name}\"{suffix}")
    return record


def add_owner():
    print("\n-- เพิ่มเจ้าของ --")
    name = input_str("ชื่อ-สกุล: ", 30)
    phone = input_str("เบอร์โทร: ", 15)
    address = input_str("ที่อยู่: ", 50)
    record = create_owner_record(name, phone, address)
    print(f"เพิ่มเจ้าของสำเร็จ (Owner ID: {record['owner_id']})")


def update_owner():
    print("\n-- แก้ไขเจ้าของ --")
    idx, rec = choose_owner(include_deleted=False)
    if rec is None:
        return
    if not rec.get("is_active", False):
        print("ไม่สามารถแก้ไขข้อมูลที่ถูกลบแล้วได้")
        return
    oid = rec["owner_id"]
    if rec is None:
        print("ไม่พบเจ้าของรหัสนี้ (หรือถูกลบไปแล้ว)")
        return
    print(f"ข้อมูลปัจจุบัน: {rec['name']} | {rec['phone']} | {rec['address']}")
    rec["name"] = input_str(f"ชื่อ-สกุลใหม่ [{rec['name']}]: ", 30, allow_empty=True) or rec["name"]
    rec["phone"] = input_str(f"เบอร์โทรใหม่ [{rec['phone']}]: ", 15, allow_empty=True) or rec["phone"]
    rec["address"] = input_str(f"ที่อยู่ใหม่ [{rec['address']}]: ", 50, allow_empty=True) or rec["address"]
    owners.write_at(idx, rec)
    log_action(f"Updated Owner ID {oid}")
    print("แก้ไขสำเร็จ")


def delete_owner():
    print("\n-- ลบเจ้าของ --")
    idx, rec = choose_owner()
    if rec is None:
        return
    oid = rec["owner_id"]
    if rec is None:
        print("ไม่พบเจ้าของรหัสนี้ (หรือถูกลบไปแล้ว)")
        return
    if not input_yn(f"ยืนยันลบเจ้าของ \"{rec['name']}\" (ID {oid})?"):
        print("ยกเลิกการลบ")
        return
    owned_pets = [(i, p) for i, p in pets.active_records() if p["owner_id"] == oid]
    if owned_pets:
        if not input_yn(f"พบสัตว์เลี้ยงของเจ้าของนี้ {len(owned_pets)} ตัว ต้องการลบด้วยหรือไม่?"):
            print("ยกเลิกการลบเจ้าของ เพราะยังมีสัตว์เลี้ยงที่ใช้งานอยู่")
            return
        for i, p in owned_pets:
            p["is_active"] = False
            pets.write_at(i, p)
            log_action(f"Deleted Pet ID {p['pet_id']} \"{p['name']}\" (cascade from Owner {oid})")
        print(f"ลบสัตว์เลี้ยงที่เกี่ยวข้อง {len(owned_pets)} ตัวแล้ว")

    rec["is_active"] = False
    owners.write_at(idx, rec)
    log_action(f"Deleted Owner ID {oid} \"{rec['name']}\"")
    print("ลบสำเร็จ (logical delete)")


# ======================================================================
# 7) PET SERVICE — Add / Update / Delete สำหรับสัตว์เลี้ยง (เพิ่มหลายตัวต่อเจ้าของได้)
# ======================================================================

def add_pet():
    print("\n-- เพิ่มสัตว์เลี้ยง --")
    _, owner_rec = choose_owner()
    if owner_rec is None:
        return

    oid = owner_rec["owner_id"]
    existing_count = sum(1 for _, p in pets.active_records() if p["owner_id"] == oid)
    print(f"เจ้าของ: {owner_rec['name']} (Owner ID {oid})")
    print(f"ปัจจุบันมีสัตว์เลี้ยงที่ใช้งานอยู่ {existing_count} ตัว")

    count = input_int("ต้องการเพิ่มสัตว์เลี้ยงกี่ตัว: ", 1)
    added = []

    for i in range(1, count + 1):
        print(f"\n-- สัตว์เลี้ยงตัวที่ {i}/{count} --")
        name = input_str("ชื่อสัตว์เลี้ยง: ", 20)
        species = input_str("ชนิด (เช่น Dog, Cat, Bird): ", 10)
        breed = input_str("สายพันธุ์: ", 20)
        year = input_int("ปีเกิด (ค.ศ.): ", 1990, datetime.date.today().year)
        record = create_pet_record(oid, name, species, breed, year)
        added.append(record)
        print(f"เพิ่มสัตว์เลี้ยงสำเร็จ (Pet ID: {record['pet_id']})")

    print(f"\nเพิ่มสัตว์เลี้ยงให้ {owner_rec['name']} สำเร็จทั้งหมด {len(added)} ตัว")
    for p in added:
        print(f"  - Pet ID {p['pet_id']} | {p['name']} | {p['species']} / {p['breed']}")


def update_pet():
    print("\n-- แก้ไขสัตว์เลี้ยง --")
    idx, rec = choose_pet(include_deleted=False)
    if rec is None:
        return
    if not rec.get("is_active", False):
        print("ไม่สามารถแก้ไขข้อมูลที่ถูกลบแล้วได้")
        return
    pid = rec["pet_id"]
    print(f"ข้อมูลปัจจุบัน: {rec['name']} | {rec['species']} | {rec['breed']} | {rec['birth_year']}")
    rec["name"] = input_str(f"ชื่อใหม่ [{rec['name']}]: ", 20, allow_empty=True) or rec["name"]
    rec["species"] = input_str(f"ชนิดใหม่ [{rec['species']}]: ", 10, allow_empty=True) or rec["species"]
    rec["breed"] = input_str(f"สายพันธุ์ใหม่ [{rec['breed']}]: ", 20, allow_empty=True) or rec["breed"]
    if input_yn("ต้องการแก้ปีเกิดหรือไม่?"):
        rec["birth_year"] = input_int("ปีเกิดใหม่: ", 1990, datetime.date.today().year)
    pets.write_at(idx, rec)
    log_action(f"Updated Pet ID {pid}")
    print("แก้ไขสำเร็จ")


def delete_pet():
    print("\n-- ลบสัตว์เลี้ยง --")
    idx, rec = choose_pet()
    if rec is None:
        return
    pid = rec["pet_id"]
    if not input_yn(f"ยืนยันลบสัตว์เลี้ยง \"{rec['name']}\" (ID {pid})?"):
        print("ยกเลิกการลบ")
        return
    related_visits = [(i, v) for i, v in visits.active_records() if v["pet_id"] == pid]
    if related_visits:
        if not input_yn(f"พบประวัตินัดหมาย {len(related_visits)} รายการ ต้องการลบด้วยหรือไม่?"):
            print("ยกเลิกการลบสัตว์เลี้ยง เพราะยังมีประวัติ/นัดหมายที่ active")
            return
        for i, v in related_visits:
            v["is_active"] = False
            visits.write_at(i, v)
            log_action(f"Deleted Visit ID {v['visit_id']} (cascade from Pet {pid})")
        print(f"ลบประวัตินัดหมายที่เกี่ยวข้อง {len(related_visits)} รายการแล้ว")

    rec["is_active"] = False
    pets.write_at(idx, rec)
    log_action(f"Deleted Pet ID {pid} \"{rec['name']}\"")
    print("ลบสำเร็จ (logical delete)")


# ======================================================================
# 8) DOCTOR SERVICE — จัดการข้อมูลหมอ + เลือกหมอ + เช็คคิวซ้ำ + คิดราคา
# ======================================================================

def add_doctor():
    print("\n-- เพิ่มสัตวแพทย์ --")
    name = input_str("ชื่อหมอ: ", 30)
    specialty = input_str("ความถนัด (เช่น ศัลยกรรม): ", 30)
    fee = input_float("ค่าตรวจของหมอ (บาท): ", 0)
    record = {"doctor_id": doctors.next_id(), "name": name,
              "specialty": specialty, "fee": fee, "is_active": True}
    doctors.add(record)
    log_action(f"Added Doctor ID {record['doctor_id']} \"{name}\"")
    print(f"เพิ่มสัตวแพทย์สำเร็จ (Doctor ID: {record['doctor_id']})")


def update_doctor():
    print("\n-- แก้ไขสัตวแพทย์ --")
    idx, rec = choose_doctor(include_deleted=False)
    if rec is None:
        return
    if not rec.get("is_active", False):
        print("ไม่สามารถแก้ไขข้อมูลที่ถูกลบแล้วได้")
        return
    did = rec["doctor_id"]
    print(f"ข้อมูลปัจจุบัน: {rec['name']} | {rec['specialty']} | ค่าตรวจ {rec['fee']:.2f} บาท")
    rec["name"] = input_str(f"ชื่อใหม่ [{rec['name']}]: ", 30, allow_empty=True) or rec["name"]
    rec["specialty"] = input_str(f"ความถนัดใหม่ [{rec['specialty']}]: ", 30, allow_empty=True) or rec["specialty"]
    if input_yn("ต้องการแก้ค่าตรวจหรือไม่?"):
        rec["fee"] = input_float("ค่าตรวจใหม่ (บาท): ", 0)
    doctors.write_at(idx, rec)
    log_action(f"Updated Doctor ID {did}")
    print("แก้ไขสำเร็จ")


def delete_doctor():
    print("\n-- ลบสัตวแพทย์ --")
    idx, rec = choose_doctor()
    if rec is None:
        return
    did = rec["doctor_id"]
    if not input_yn(f"ยืนยันลบหมอ \"{rec['name']}\" (ID {did})?"):
        print("ยกเลิกการลบ")
        return
    related = [(i, v) for i, v in visits.active_records() if v["doctor_id"] == did]
    if related:
        if not input_yn(f"พบนัดหมายที่หมอคนนี้ดูแล {len(related)} รายการ ต้องการลบด้วยหรือไม่?"):
            print("ยกเลิกการลบหมอ เพราะยังมีประวัติ/นัดหมายที่ active")
            return
        for i, v in related:
            v["is_active"] = False
            visits.write_at(i, v)
            log_action(f"Deleted Visit ID {v['visit_id']} (cascade from Doctor {did})")
        print(f"ลบนัดหมายที่เกี่ยวข้อง {len(related)} รายการแล้ว")

    rec["is_active"] = False
    doctors.write_at(idx, rec)
    log_action(f"Deleted Doctor ID {did} \"{rec['name']}\"")
    print("ลบสำเร็จ (logical delete)")


def choose_doctor():
    """เลือกหมอจากรายการที่มีหมายเลข แทนการต้องจำ Doctor ID"""
    records = sorted(doctors.active_records(), key=lambda x: x[1]["doctor_id"])
    return choose_record(
        records,
        "Select Doctor (เลือกสัตวแพทย์)",
        lambda d: f"Doctor ID {d['doctor_id']} | {d['name']} | ถนัด {d['specialty']} | ค่าตรวจ {d['fee']:.2f} บาท",
    )


def estimate_cost(reason, doctor_rec):
    """คิดราคาอัตโนมัติ = ราคาอาการพื้นฐาน + ค่าตรวจของหมอ"""
    base = SYMPTOM_PRICES.get(reason, DEFAULT_SYMPTOM_PRICE)
    return base + doctor_rec["fee"]


def doctor_slot_taken(doctor_id, date_str, time_str, exclude_visit_id=None):
    """เช็คคิวชน: หมอคนเดียวกัน + วันเดียวกัน + เวลาเดียวกัน = จองไม่ได้
    (exclude_visit_id ใช้ตอนแก้ไขรายการตัวเอง เพื่อไม่นับชนตัวเอง)"""
    for _, v in visits.active_records():
        if v["status"] == STATUS_CANCELLED:
            continue            # นัดที่ยกเลิกแล้วไม่กินคิวหมอ
        if (v["doctor_id"] == doctor_id
                and v["visit_date"] == date_str
                and v["visit_time"] == time_str):
            if exclude_visit_id is None or v["visit_id"] != exclude_visit_id:
                return True
    return False


def pick_appointment(doctor_id, exclude_visit_id=None, future_only=False):
    """วนถามวัน/เวลาและเช็คคิวชน จนกว่าจะได้ช่วงที่ใช้ได้"""
    while True:
        print("กำหนดวันเวลานัดหมาย (วัน/เดือน/ปี/เวลา):")
        date, time_str = input_appointment(future_only=future_only)
        if not doctor_slot_taken(doctor_id, date, time_str, exclude_visit_id):
            return date, time_str
        print(f"!! หมอคนนี้มีคนจองช่วง {date} เวลา {time_str} อยู่แล้ว กรุณาเลือกใหม่")


# ======================================================================
# 9) VISIT SERVICE — Add / Update / Delete ประวัติรักษา/นัดหมาย
#     (มีหมอผู้ดูแล + เวลา + คิดราคาอัตโนมัติ + เช็คคิวชน)
# ======================================================================

def add_visit():
    print("\n-- เพิ่มประวัติรักษา/นัดหมาย --")
    _, pet_rec = choose_pet()
    if pet_rec is None:
        return
    pid = pet_rec["pet_id"]
    _, doc = choose_doctor()
    if doc is None:
        return
    reason = input_str("อาการ/สาเหตุ: ", 40)
    diagnosis = input_str("ผลวินิจฉัย (ยังไม่รู้ใส่ -): ", 40, allow_empty=True) or "-"
    cost = estimate_cost(reason, doc)
    print(f"-> ราคาที่คำนวณให้อัตโนมัติ: {cost:.2f} บาท "
          f"(อาการ {SYMPTOM_PRICES.get(reason, DEFAULT_SYMPTOM_PRICE):.2f} "
          f"+ ค่าตรวจหมอ {doc['fee']:.2f})")
    if not input_yn("ใช้ราคานี้หรือไม่?"):
        cost = input_float("ระบุราคาเอง (บาท): ", 0)
    date, time_str = pick_appointment(doc["doctor_id"])
    record = {
        "visit_id": visits.next_id(), "pet_id": pid, "doctor_id": doc["doctor_id"],
        "visit_date": date, "visit_time": time_str, "reason": reason,
        "diagnosis": diagnosis, "cost": cost, "is_active": True,
        "status": STATUS_BOOKED,
    }
    visits.add(record)
    log_action(f"Added Visit ID {record['visit_id']} "
               f"(Pet {pid}, Doctor {doc['doctor_id']}, {date} {time_str})")
    print(f"เพิ่มประวัติสำเร็จ (Visit ID: {record['visit_id']}, นัด {date} เวลา {time_str})")


def update_visit():
    print("\n-- แก้ไขประวัติรักษา/นัดหมาย --")
    idx, rec = choose_visit(include_deleted=False)
    if rec is None:
        return
    if not rec.get("is_active", False):
        print("ไม่สามารถแก้ไขข้อมูลที่ถูกลบแล้วได้")
        return
    if rec["status"] == STATUS_CANCELLED:
        print("นัดนี้ถูกยกเลิกแล้ว ไม่สามารถแก้ไขได้")
        return
    vid = rec["visit_id"]
    _, doc = doctors.find_by_id(rec["doctor_id"], active_only=False)
    doc_name = doc["name"] if doc else "?"
    print(f"ข้อมูลปัจจุบัน: {rec['visit_date']} {rec['visit_time']} | หมอ: {doc_name} | "
          f"{rec['reason']} -> {rec['diagnosis']} | {rec['cost']:.2f} บาท")
    rec["reason"] = input_str(f"อาการใหม่ [{rec['reason']}]: ", 40, allow_empty=True) or rec["reason"]
    rec["diagnosis"] = input_str(f"ผลวินิจฉัยใหม่ [{rec['diagnosis']}]: ", 40, allow_empty=True) or rec["diagnosis"]

    doctor_changed = False
    if input_yn("ต้องการเปลี่ยนหมอหรือไม่?"):
        _, new_doc = choose_doctor()
        if new_doc is not None and new_doc["doctor_id"] != rec["doctor_id"]:
            rec["doctor_id"] = new_doc["doctor_id"]
            doctor_changed = True

    if input_yn("ต้องการแก้วัน/เวลานัดหรือไม่?"):
        rec["visit_date"], rec["visit_time"] = pick_appointment(
            rec["doctor_id"], exclude_visit_id=vid)
    elif (doctor_changed and doctor_slot_taken(
            rec["doctor_id"], rec["visit_date"], rec["visit_time"],
            exclude_visit_id=vid)):
        # เปลี่ยนหมอแต่คงวันเวลาเดิม แล้วชนคิวของหมอใหม่ -> ต้องเปลี่ยนวันเวลา
        print("!! หมอใหม่มีคนจองวันเวลาเดิมของนัดนี้แล้ว ต้องเปลี่ยนวันเวลา")
        rec["visit_date"], rec["visit_time"] = pick_appointment(
            rec["doctor_id"], exclude_visit_id=vid)

    if input_yn("ต้องการแก้ค่าใช้จ่ายหรือไม่?"):
        rec["cost"] = input_float("ค่าใช้จ่ายใหม่ (บาท): ", 0)
    visits.write_at(idx, rec)
    log_action(f"Updated Visit ID {vid}")
    print("แก้ไขสำเร็จ")


def delete_visit():
    print("\n-- ลบประวัติรักษา/นัดหมาย --")
    idx, rec = choose_visit()
    if rec is None:
        return
    vid = rec["visit_id"]
    if not input_yn(f"ยืนยันลบ Visit ID {vid}?"):
        print("ยกเลิกการลบ")
        return
    rec["is_active"] = False
    visits.write_at(idx, rec)
    log_action(f"Deleted Visit ID {vid}")
    print("ลบสำเร็จ (logical delete)")


# ======================================================================
# 10) BOOKING — จองคิวรวดเร็ว: หลายสัตว์ -> เลือกหมอ -> คิดราคา -> นัดวันเวลา
# ======================================================================

def finish_visit():
    """ปิดนัดที่จองไว้: ตรวจเสร็จแล้ว (บันทึกผลวินิจฉัย + ค่าใช้จ่ายจริง) หรือ ยกเลิกนัด"""
    print("\n-- ปิดนัด: ตรวจเสร็จแล้ว / ยกเลิกนัด --")
    pet_by_id = {r["pet_id"]: r for _, r in pets.all_slots()}
    booked = [(i, r) for i, r in visits.active_records() if r["status"] == STATUS_BOOKED]
    booked.sort(key=lambda x: (x[1]["visit_date"], x[1]["visit_time"], x[1]["visit_id"]))
    if not booked:
        print("ไม่มีนัดที่รอตรวจ (ทุกนัดปิดแล้ว)")
        return

    now = now_local()

    def fmt(r):
        name = pet_by_id.get(r["pet_id"], {}).get("name", f"ID {r['pet_id']}")
        late = ""
        try:
            if datetime.datetime.strptime(
                    f"{r['visit_date']} {r['visit_time']}", "%Y-%m-%d %H:%M") < now:
                late = " | เลยกำหนด"
        except ValueError:
            pass
        return (f"Visit {r['visit_id']} | {r['visit_date']} {r['visit_time']} | "
                f"{name} | {r['reason']}{late}")

    idx, rec = choose_record(booked, "Select Booked Visit (เลือกนัดที่ยังไม่ปิด)", fmt)
    if rec is None:
        return
    vid = rec["visit_id"]
    print_visit(rec)

    print_menu(f"Visit {vid} — จะทำอะไร", [
        (1, "Complete", "ตรวจเสร็จแล้ว (บันทึกผลวินิจฉัย)"),
        (2, "Cancel",   "ยกเลิกนัด"),
    ], "กลับ")
    action = input_int("เลือกหมายเลข: ", 0, 2)
    if action == 0:
        return

    if action == 1:
        rec["diagnosis"] = input_str("ผลวินิจฉัย: ", 40)
        if not input_yn(f"ใช้ค่าใช้จ่ายเดิม {rec['cost']:.2f} บาทหรือไม่?"):
            rec["cost"] = input_float("ค่าใช้จ่ายจริง (บาท): ", 0)
        rec["status"] = STATUS_COMPLETED
        visits.write_at(idx, rec)
        log_action(f"Completed Visit ID {vid}")
        print(f"บันทึกแล้ว: Visit {vid} ตรวจเสร็จแล้ว ({rec['diagnosis']} | {rec['cost']:.2f} บาท)")
    else:
        if not input_yn(f"ยืนยันยกเลิกนัด Visit {vid}?"):
            print("ไม่ได้ยกเลิก")
            return
        rec["status"] = STATUS_CANCELLED
        visits.write_at(idx, rec)
        log_action(f"Cancelled Visit ID {vid}")
        print(f"ยกเลิกนัด Visit {vid} แล้ว (คิวของหมอว่างอีกครั้ง)")


def quick_booking():
    """ฟลุวเดียวจบ: เจ้าของ -> บอกว่ามีสัตว์กี่ตัว -> ใส่ชื่อ/พันธุ์/อาการทีละตัว
    -> เลือกหมอ (ระบบคิดราคาให้อัตโนมัติ) -> นัด วัน/เดือน/ปี/เวลา
    -> ถ้าหมอคนนั้นถูกจอง วัน+เวลา นั้นแล้ว ระบบแจ้งและให้เลือกใหม่"""
    print("\n== จองคิวรวดเร็ว (เพิ่มสัตว์ + เลือกหมอ + นัดวันเวลา) ==")

    # ---- เจ้าของ (มีอยู่แล้ว หรือสร้างใหม่ได้เลย) ----
    idx, owner_rec = choose_owner(allow_new=True)
    if owner_rec is None:
        print("-- เพิ่มเจ้าของใหม่ --")
        name = input_str("ชื่อ-สกุล: ", 30)
        phone = input_str("เบอร์โทร: ", 15)
        address = input_str("ที่อยู่: ", 50)
        owner_rec = create_owner_record(name, phone, address, log_suffix="(booking)")
        print(f"เพิ่มเจ้าของสำเร็จ (Owner ID: {owner_rec['owner_id']})")
    oid = owner_rec["owner_id"]

    # แสดงสัตว์เลี้ยงเดิมของเจ้าของคนนี้ (ถ้ามี)
    existing = [p for _, p in pets.active_records() if p["owner_id"] == oid]
    if existing:
        print("สัตว์เลี้ยงปัจจุบันของเจ้าของคนนี้:")
        for p in existing:
            print(f"  [{p['pet_id']}] {p['name']} ({p['species']}/{p['breed']})")

    # ---- ขั้น 1: มีสัตว์กี่ตัว -> ใส่ข้อมูลทีละตัว ----
    n_new = input_int("ต้องการเพิ่มสัตว์เลี้ยงใหม่กี่ตัว (0 = ไม่เพิ่ม): ", 0)
    if n_new == 0 and not existing:
        print("ไม่มีสัตว์เลี้ยงจะจองคิว — ยกเลิก")
        return

    booking_items = []   # [(pet_record, reason), ...] ที่รอจองคิว
    for i in range(1, n_new + 1):
        print(f"\n-- สัตว์เลี้ยงตัวที่ {i}/{n_new} --")
        name = input_str("ชื่อ: ", 20)
        species = input_str("ชนิด (Dog/Cat/Bird): ", 10)
        breed = input_str("สายพันธุ์: ", 20)
        year = input_int("ปีเกิด (ค.ศ.): ", 1990, datetime.date.today().year)
        reason = input_str("อาการ/สาเหตุที่จะรักษา: ", 40)
        pet_rec = create_pet_record(oid, name, species, breed, year, log_suffix="(booking)")
        print(f"บันทึกสัตว์เลี้ยงแล้ว (Pet ID: {pet_rec['pet_id']})")
        booking_items.append((pet_rec, reason))

    # สัตว์เดิมก็จองคิวด้วยได้ (ถ้ามี)
    if existing:
        print("\nเลือกสัตว์เดิมที่ต้องการจองคิว (เลือกทีละตัว)")
        chosen_existing = set()
        while True:
            available = [(i, p) for i, p in pets.active_records()
                         if p["owner_id"] == oid and p["pet_id"] not in chosen_existing]
            if not available:
                break
            _, pet_rec = choose_record(
                available,
                "Select Existing Pet (เลือกสัตว์เดิม)",
                lambda p: f"Pet ID {p['pet_id']} | {p['name']} ({p['species']}/{p['breed']})",
                zero_label="Done (ไม่เลือกเพิ่ม)",
            )
            if pet_rec is None:
                break
            chosen_existing.add(pet_rec["pet_id"])
            reason = input_str(f"อาการของ {pet_rec['name']}: ", 40)
            booking_items.append((pet_rec, reason))
            if not input_yn("ต้องการเลือกสัตว์เดิมเพิ่มอีกหรือไม่?"):
                break

    if not booking_items:
        print("ไม่มีรายการที่ต้องจองคิว — จบการทำงาน")
        return

    # ---- ขั้น 2: ทีละตัว เลือกหมอ -> คิดราคา -> นัดวันเวลา (เช็คคิวชน) ----
    print(f"\n== เริ่มจองคิว {len(booking_items)} รายการ ==")
    booked = []
    for pet_rec, reason in booking_items:
        print(f"\n>> สัตว์: {pet_rec['name']} | อาการ: {reason}")
        _, doc = choose_doctor()
        if doc is None:
            print("ไม่มีข้อมูลหมอ — ข้ามรายการนี้")
            continue
        cost = estimate_cost(reason, doc)
        print(f"-> ราคา: {cost:.2f} บาท "
              f"(อาการ {SYMPTOM_PRICES.get(reason, DEFAULT_SYMPTOM_PRICE):.2f} "
              f"+ ค่าตรวจหมอ {doc['fee']:.2f})")

        date = time_str = None
        while True:
            print("กำหนดวันเวลานัดหมาย (วัน/เดือน/ปี/เวลา):")
            date, time_str = input_appointment(future_only=True)
            if not doctor_slot_taken(doc["doctor_id"], date, time_str):
                break
            print(f"!! หมอ {doc['name']} มีคนจองช่วง {date} เวลา {time_str} อยู่แล้ว")
            if not input_yn("เปลี่ยนหมอหรือไม่? (n = เลือกวันเวลาใหม่กับหมอคนเดิม)"):
                continue
            _, doc = choose_doctor()
            if doc is None:
                break
            cost = estimate_cost(reason, doc)
            print(f"-> ราคาใหม่: {cost:.2f} บาท")
            if not doctor_slot_taken(doc["doctor_id"], date, time_str):
                break          # หมอใหม่ว่างช่วงเวลาเดิม ใช้วันเวลาเดิมได้เลย
            print("!! หมอใหม่ก็มีคนจองช่วงเวลานี้แล้ว กรุณาเลือกวันเวลาใหม่")

        if doc is None:
            print("ไม่มีข้อมูลหมอ — ข้ามรายการนี้")
            continue

        vid = visits.next_id()
        visits.add({
            "visit_id": vid, "pet_id": pet_rec["pet_id"], "doctor_id": doc["doctor_id"],
            "visit_date": date, "visit_time": time_str, "reason": reason,
            "diagnosis": "-", "cost": cost, "is_active": True,
            "status": STATUS_BOOKED,
        })
        log_action(f"Added Visit ID {vid} (Pet {pet_rec['pet_id']}, "
                   f"Doctor {doc['doctor_id']}, {date} {time_str}) (booking)")
        booked.append((pet_rec, doc, date, time_str, cost, vid))

    # ---- สรุป ----
    print("\n-- สรุปการจอง --")
    if not booked:
        print("  (ไม่ได้จองคิวสำเร็จแม้แต่รายการเดียว)")
    for pet_rec, doc, date, time_str, cost, vid in booked:
        print(f"  Visit {vid} | {pet_rec['name']} | {date} {time_str} | "
              f"หมอ {doc['name']} | {cost:.2f} บาท")


# ======================================================================
# 11) VIEW — ดูรายการเดียว / ทั้งหมด / กรอง / สถิติ / ประวัติรายเดือน / นัดล่วงหน้า
# ======================================================================

def print_owner(rec):
    status = "Active" if rec["is_active"] else "Deleted"
    print(f"  [{rec['owner_id']}] {rec['name']} | {rec['phone']} | "
          f"{rec['address']} | {status}")


def print_pet(rec):
    status = "Active" if rec["is_active"] else "Deleted"
    print(f"  [{rec['pet_id']}] {rec['name']} | {rec['species']} | "
          f"{rec['breed']} | เกิด {rec['birth_year']} | Owner {rec['owner_id']} | {status}")


def print_visit(rec):
    status = ("Deleted" if not rec["is_active"]
              else VISIT_STATUS_TH.get(rec["status"], "?"))
    _, doc = doctors.find_by_id(rec["doctor_id"], active_only=False)
    doc_name = doc["name"] if doc else f"? (ID {rec['doctor_id']})"
    print(f"  [{rec['visit_id']}] Pet {rec['pet_id']} | {rec['visit_date']} {rec['visit_time']} | "
          f"หมอ: {doc_name} | {rec['reason']} -> {rec['diagnosis']} | "
          f"{rec['cost']:.2f} บาท | {status}")


def print_doctor(rec):
    status = "Active" if rec["is_active"] else "Deleted"
    print(f"  [{rec['doctor_id']}] {rec['name']} | {rec['specialty']} | "
          f"ค่าตรวจ {rec['fee']:.2f} บาท | {status}")


# ----------------------------------------------------------------------
# ตัวช่วยแสดงผลแบบตาราง: หัวเรื่อง / เนื้อหา / สรุป (ใช้รูปแบบเดียวกับ report.py)
# ----------------------------------------------------------------------

ENTITY_INFO = {
    1: ("Owner", "เจ้าของ"),
    2: ("Pet", "สัตว์เลี้ยง"),
    3: ("Visit", "ประวัติรักษา/นัดหมาย"),
    4: ("Doctor", "สัตวแพทย์"),
}
ENTITY_STORES = {1: owners, 2: pets, 3: visits, 4: doctors}


def _status(rec):
    return "Active" if rec["is_active"] else "Deleted"


def _kv(label, value, label_width=22):
    """บรรทัดสรุปแบบ 'ป้ายชื่อ : ค่า' จัดคอลัมน์ให้ตรงกัน (รองรับภาษาไทย)"""
    import report as rp
    return f"{label}{' ' * max(1, label_width - rp.disp_len(label))}: {value}"


def print_view_page(title, blocks, summary_lines):
    """พิมพ์หน้าจอ View ในรูปแบบ หัวเรื่อง / เนื้อหา / สรุป
    blocks = [(หัวตาราง หรือ None, headers, rows, aligns[, wide]), ...]
    wide=True = ไม่ตัดข้อความยาวในตาราง (เช่น path ของไฟล์)
    เส้นคั่นจะขยายตามความกว้างของตารางที่กว้างที่สุดโดยอัตโนมัติ"""
    import report as rp
    rendered = [
        (b[0], rp.render_table(b[1], b[2], b[3],
                               max_col_width=200 if len(b) > 4 and b[4] else rp.TABLE_MAX_COL_WIDTH))
        for b in blocks
    ]
    width = max([rp.REPORT_WIDTH] + [rp.disp_len(line)
                                      for _, lines in rendered for line in lines])
    div = "=" * width
    sub = "-" * width
    generated_at = now_local().strftime("%Y-%m-%d %H:%M:%S")

    print()
    print(div)
    print("หัวเรื่อง")
    print(div)
    print(f"Pet Clinic System — {title}")
    print(f"Generated At : {generated_at}")
    print(f"App Version  : {APP_VERSION}")
    print(div)
    print()
    print("ข้อมูล/เนื้อหา")
    print(sub)
    print()
    for caption, lines in rendered:
        if caption:
            print(caption)
        for line in lines:
            print(line)
        print()
    print("สรุป")
    print(sub)
    for line in summary_lines:
        print(line)
    print()
    print(div)
    print("จบรายงาน")
    print(div)


def entity_table(entity, records):
    """แปลงรายการ record ของแต่ละ entity เป็น (headers, rows, aligns)"""
    owner_by_id = {r["owner_id"]: r for _, r in owners.all_slots()}
    pet_by_id = {r["pet_id"]: r for _, r in pets.all_slots()}
    doctor_by_id = {r["doctor_id"]: r for _, r in doctors.all_slots()}

    if entity == 1:
        pet_count = {}
        for _, p in pets.active_records():
            pet_count[p["owner_id"]] = pet_count.get(p["owner_id"], 0) + 1
        headers = ["OwnerID", "Name", "Phone", "Address", "Pets", "Status"]
        aligns = ["r", "l", "l", "l", "r", "l"]
        rows = [[r["owner_id"], r["name"], r["phone"], r["address"],
                 pet_count.get(r["owner_id"], 0), _status(r)] for r in records]
    elif entity == 2:
        headers = ["PetID", "Name", "Species", "Breed", "Born", "Owner", "Status"]
        aligns = ["r", "l", "l", "l", "r", "l", "l"]
        rows = [[r["pet_id"], r["name"], r["species"], r["breed"], r["birth_year"],
                 owner_by_id.get(r["owner_id"], {}).get("name", f"ID {r['owner_id']}"),
                 _status(r)] for r in records]
    elif entity == 3:
        headers = ["VisitID", "Date", "Time", "Pet", "Doctor", "Reason",
                   "Diagnosis", "Cost", "Status"]
        aligns = ["r", "l", "l", "l", "l", "l", "l", "r", "l"]
        rows = [[r["visit_id"], r["visit_date"], r["visit_time"],
                 pet_by_id.get(r["pet_id"], {}).get("name", f"ID {r['pet_id']}"),
                 doctor_by_id.get(r["doctor_id"], {}).get("name", f"ID {r['doctor_id']}"),
                 r["reason"], r["diagnosis"], f"{r['cost']:.2f}", visit_status_en(r)]
                for r in records]
    else:
        headers = ["DocID", "Name", "Specialty", "Fee", "Status"]
        aligns = ["r", "l", "l", "r", "l"]
        rows = [[r["doctor_id"], r["name"], r["specialty"], f"{r['fee']:.2f}",
                 _status(r)] for r in records]
    return headers, rows, aligns


def entity_summary(entity, records):
    """บรรทัดสรุปของรายการที่แสดง (ใช้ได้ทั้ง View All / Filter / View One)"""
    active = sum(1 for r in records if r["is_active"])
    lines = [_kv("จำนวนที่แสดง", f"{len(records)} รายการ "
                 f"(Active {active} / Deleted {len(records) - active})")]
    if not records:
        return lines
    if entity == 1:
        ids = {r["owner_id"] for r in records}
        n = sum(1 for _, p in pets.active_records() if p["owner_id"] in ids)
        lines.append(_kv("สัตว์เลี้ยง (Active) รวม", f"{n} ตัว"))
    elif entity == 2:
        count = {}
        for r in records:
            count[r["species"]] = count.get(r["species"], 0) + 1
        lines.append(_kv("แยกตามชนิด",
                         " | ".join(f"{k}: {v}" for k, v in sorted(count.items()))))
    elif entity == 3:
        costs = [r["cost"] for r in records]
        by_status = {}
        for r in records:
            label = visit_status_en(r)
            by_status[label] = by_status.get(label, 0) + 1
        lines.append(_kv("สถานะ", " | ".join(f"{k}: {v}" for k, v in sorted(by_status.items()))))
        lines.append(_kv("ค่าใช้จ่ายรวม", f"{sum(costs):.2f} บาท"))
        lines.append(_kv("ต่ำสุด / สูงสุด / เฉลี่ย",
                         f"{min(costs):.2f} / {max(costs):.2f} / {sum(costs) / len(costs):.2f} บาท"))
    else:
        fees = [r["fee"] for r in records]
        lines.append(_kv("ค่าตรวจเฉลี่ย", f"{sum(fees) / len(fees):.2f} บาท"))
    return lines


def view_single():
    entity = choose_entity("ดูรายการเดียว — เลือกประเภทข้อมูล")
    if entity == 0:
        return
    choices = {
        1: ("Select Owner (เลือกเจ้าของ)", lambda r: f"Owner ID {r['owner_id']} | {r['name']} | โทร {r['phone']}"),
        2: ("Select Pet (เลือกสัตว์เลี้ยง)", lambda r: f"Pet ID {r['pet_id']} | {r['name']} ({r['species']}/{r['breed']})"),
        3: ("Select Visit (เลือกประวัติ/นัดหมาย)", lambda r: f"Visit ID {r['visit_id']} | {r['visit_date']} {r['visit_time']} | Pet {r['pet_id']} | {r['reason']}"),
        4: ("Select Doctor (เลือกสัตวแพทย์)", lambda r: f"Doctor ID {r['doctor_id']} | {r['name']} | {r['specialty']}"),
    }
    title, formatter = choices[entity]
    _, rec = choose_record(ENTITY_STORES[entity].all_slots(), title, formatter,
                           zero_label="Back (กลับ)")
    if rec is None:
        return
    en, th = ENTITY_INFO[entity]
    headers, rows, _ = entity_table(entity, [rec])
    detail = [[h, v] for h, v in zip(headers, rows[0])]   # แนวตั้ง: Field | Value
    print_view_page(
        f"View One — {en} ({th})",
        [(f"{en} — รายละเอียด", ["Field", "Value"], detail, ["l", "l"])],
        [_kv("ประเภทข้อมูล", f"{en} ({th})"),
         _kv("สถานะ", _status(rec))],
    )


def view_all():
    entity = choose_entity("ดูทั้งหมด — เลือกประเภทข้อมูล")
    if entity == 0:
        return
    en, th = ENTITY_INFO[entity]
    records = sorted((r for _, r in ENTITY_STORES[entity].active_records()),
                     key=lambda r: tuple(r[k] for k in (
                         {1: ("owner_id",), 2: ("pet_id",),
                          3: ("visit_date", "visit_time", "visit_id"),
                          4: ("doctor_id",)}[entity])))
    headers, rows, aligns = entity_table(entity, records)
    print_view_page(
        f"View All — {en} ({th})",
        [(f"{en} — {th}ทั้งหมด (Active)", headers, rows, aligns)],
        entity_summary(entity, records),
    )


def view_filtered():
    entity = choose_entity("ดูแบบกรอง — เลือกประเภทข้อมูล")
    if entity == 0:
        return
    en, th = ENTITY_INFO[entity]
    if entity == 1:
        keyword = input_str("ค้นหาจากชื่อเจ้าของ (บางส่วนก็ได้): ", 30)
        condition = f"ชื่อเจ้าของมีคำว่า \"{keyword}\""
        results = [r for _, r in owners.active_records()
                   if keyword.lower() in r["name"].lower()]
    elif entity == 2:
        species = input_str("กรองตามชนิดสัตว์ (เช่น Dog): ", 10)
        condition = f"ชนิดสัตว์ = {species}"
        results = [r for _, r in pets.active_records()
                   if r["species"].lower() == species.lower()]
    elif entity == 3:
        _, pet = choose_pet()
        if pet is None:
            return
        pid = pet["pet_id"]
        condition = f"สัตว์เลี้ยง = {pet['name']} (Pet ID {pid})"
        results = sorted((r for _, r in visits.active_records() if r["pet_id"] == pid),
                         key=lambda x: (x["visit_date"], x["visit_time"], x["visit_id"]))
    else:
        keyword = input_str("ค้นหาจากความถนัดของหมอ (บางส่วนก็ได้): ", 30)
        condition = f"ความถนัดมีคำว่า \"{keyword}\""
        results = [r for _, r in doctors.active_records()
                   if keyword.lower() in r["specialty"].lower()]

    headers, rows, aligns = entity_table(entity, results)
    print_view_page(
        f"Filter — {en} ({th})",
        [(f"ผลการกรอง {en} — เงื่อนไข: {condition}", headers, rows, aligns)],
        [_kv("เงื่อนไข", condition)] + entity_summary(entity, results),
    )


def get_data_snapshot(include_logs=True, recent_logs=5):
    """อ่านข้อมูล 4 binary stores ครั้งเดียว แล้วให้ส่วนอื่นใช้ snapshot เดียวกัน"""
    return {
        "owners": [r for _, r in owners.all_slots()],
        "pets": [r for _, r in pets.all_slots()],
        "doctors": [r for _, r in doctors.all_slots()],
        "visits": [r for _, r in visits.all_slots()],
        "recent_logs": read_recent_logs(recent_logs) if include_logs else [],
    }


def compute_summary(snapshot=None):
    """คำนวณค่าสถิติจาก snapshot เดียวกัน"""
    snapshot = snapshot or get_data_snapshot(include_logs=False)
    owner_records = snapshot["owners"]
    pet_records = snapshot["pets"]
    visit_records = snapshot["visits"]
    doctor_records = snapshot["doctors"]

    active_pets = [r for r in pet_records if r["is_active"]]
    # Visit ที่ยกเลิกนัดไม่นับเป็นรายการ/ค่าใช้จ่าย (แต่ยังเก็บในไฟล์)
    active_visits = [r for r in visit_records
                     if r["is_active"] and r["status"] != STATUS_CANCELLED]
    cancelled_visits = [r for r in visit_records
                        if r["is_active"] and r["status"] == STATUS_CANCELLED]
    species_count = {}
    for p in active_pets:
        species_count[p["species"]] = species_count.get(p["species"], 0) + 1

    costs = [v["cost"] for v in active_visits]
    upcoming = build_upcoming_appointments(days=30, snapshot=snapshot)

    return {
        "total_owners": len(owner_records),
        "active_owners": sum(1 for r in owner_records if r["is_active"]),
        "total_doctors": len(doctor_records),
        "active_doctors": sum(1 for r in doctor_records if r["is_active"]),
        "total_pets": len(pet_records),
        "active_pets": len(active_pets),
        "deleted_pets": len(pet_records) - len(active_pets),
        "total_visits": len(visit_records),
        "active_visits": len(active_visits),
        "cancelled_visits": len(cancelled_visits),
        "deleted_visits": sum(1 for r in visit_records if not r["is_active"]),
        "upcoming_pets": len({u["pet_id"] for u in upcoming}),
        "species_count": species_count,
        "cost_min": min(costs) if costs else 0.0,
        "cost_max": max(costs) if costs else 0.0,
        "cost_avg": (sum(costs) / len(costs)) if costs else 0.0,
    }


def build_owner_visit_history(days=30, snapshot=None):
    """join Visit -> Pet -> Owner สำหรับช่วงย้อนหลังตามจำนวนวัน"""
    snapshot = snapshot or get_data_snapshot(include_logs=False)
    now = now_local()
    cutoff = now - datetime.timedelta(days=days)
    pet_by_id = {p["pet_id"]: p for p in snapshot["pets"]}
    owner_by_id = {o["owner_id"]: o for o in snapshot["owners"]}

    history = {}
    for v in snapshot["visits"]:
        if not v["is_active"]:
            continue
        try:
            visit_dt = datetime.datetime.strptime(
                f"{v['visit_date']} {v['visit_time']}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            continue
        if v["status"] != STATUS_COMPLETED or visit_dt < cutoff:
            continue                      # ประวัติ = ตรวจเสร็จแล้วเท่านั้น
        pet = pet_by_id.get(v["pet_id"])
        if pet is None:
            continue
        owner = owner_by_id.get(pet["owner_id"])
        if owner is None:
            continue

        oid = owner["owner_id"]
        entry = history.setdefault(oid, {
            "owner_name": owner["name"],
            "visits": [],
            "total_cost": 0.0,
        })
        entry["visits"].append({
            "visit_id": v["visit_id"],
            "date": v["visit_date"],
            "time": v["visit_time"],
            "pet_id": pet["pet_id"],
            "pet_name": pet["name"],
            "reason": v["reason"],
            "cost": v["cost"],
        })
        entry["total_cost"] += v["cost"]

    for entry in history.values():
        entry["visits"].sort(key=lambda x: (x["date"], x["time"], x["visit_id"]))
        entry["visit_count"] = len(entry["visits"])
    return history


def build_upcoming_appointments(days=30, snapshot=None):
    """join Visit -> Pet -> Owner -> Doctor สำหรับนัดหมายภายในช่วง 30 วัน"""
    snapshot = snapshot or get_data_snapshot(include_logs=False)
    now = now_local()
    horizon = now + datetime.timedelta(days=days)
    pet_by_id = {p["pet_id"]: p for p in snapshot["pets"]}
    owner_by_id = {o["owner_id"]: o for o in snapshot["owners"]}
    doctor_by_id = {d["doctor_id"]: d for d in snapshot["doctors"]}

    upcoming = []
    for v in snapshot["visits"]:
        if not v["is_active"]:
            continue
        try:
            visit_dt = datetime.datetime.strptime(
                f"{v['visit_date']} {v['visit_time']}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            continue
        if v["status"] != STATUS_BOOKED or visit_dt > horizon:
            continue                      # นัด = จองไว้ (รวมที่เลยกำหนดแต่ยังไม่ปิด)
        pet = pet_by_id.get(v["pet_id"])
        owner = owner_by_id.get(pet["owner_id"]) if pet else None
        doc = doctor_by_id.get(v["doctor_id"])
        upcoming.append({
            "visit_id": v["visit_id"],
            "date": v["visit_date"],
            "time": v["visit_time"],
            "pet_id": pet["pet_id"] if pet else v["pet_id"],
            "pet_name": pet["name"] if pet else "?",
            "owner_id": owner["owner_id"] if owner else None,
            "owner_name": owner["name"] if owner else "?",
            "doctor_id": doc["doctor_id"] if doc else v["doctor_id"],
            "doctor_name": doc["name"] if doc else "?",
            "reason": v["reason"],
            "cost": v["cost"],
            "overdue": visit_dt < now,
        })
    upcoming.sort(key=lambda x: (x["date"], x["time"], x["visit_id"]))
    return upcoming


def view_owner_history():
    """เมนู: ดูว่าเจ้าของคนหนึ่ง (หรือทุกคน) มาใช้บริการตอนไหนบ้าง ใช้เงินเท่าไหร่
    ในรอบ 30 วันที่ผ่านมา — join ข้อมูลจาก owners/pets/visits ให้อัตโนมัติ"""
    oid = input_int("กรอก Owner ID (กด 0 เพื่อดูทุกคน): ", 0)
    history = build_owner_visit_history(days=30, snapshot=get_data_snapshot(include_logs=False))

    if oid != 0:
        entry = history.get(oid)
        if entry is None:
            print(f"ไม่พบประวัติการมาใช้บริการของ Owner ID {oid} ในรอบ 30 วันที่ผ่านมา")
            return
        history = {oid: entry}

    if not history:
        print("ไม่มีประวัติการมาใช้บริการในรอบ 30 วันที่ผ่านมา")
        return

    print("\n-- ประวัติการมาใช้บริการ (ย้อนหลัง 30 วัน) --")
    for owner_id, entry in sorted(history.items()):
        print(f"\nเจ้าของ: {entry['owner_name']} (Owner ID {owner_id})")
        for v in entry["visits"]:
            print(f"  - {v['date']} {v['time']} | {v['pet_name']} | "
                  f"{v['reason']} | {v['cost']:.2f} บาท")
        print(f"  รวม: {entry['visit_count']} ครั้ง | ยอดใช้จ่ายทั้งหมด {entry['total_cost']:.2f} บาท")


def view_upcoming():
    """เมนู: ดูว่ามีใครนัดหมายอะไรบ้างในอีก 30 วันข้างหน้า (แสดงเวลา + หมอ)"""
    upcoming = build_upcoming_appointments(days=30, snapshot=get_data_snapshot(include_logs=False))
    print("\n-- นัดหมายล่วงหน้า (1 เดือนข้างหน้า) --")
    if not upcoming:
        print("  (ไม่มีนัดหมายในช่วง 1 เดือนข้างหน้า)")
        return
    for u in upcoming:
        print(f"  - {u['date']} {u['time']} | {u['pet_name']} (เจ้าของ: {u['owner_name']}) | "
              f"หมอ: {u['doctor_name']} | {u['reason']}"
              + (" | เลยกำหนด" if u["overdue"] else ""))


def view_stats():
    s = compute_summary(snapshot=get_data_snapshot(include_logs=False))
    count_rows = [
        ["Owner (เจ้าของ)", s["total_owners"], s["active_owners"],
         s["total_owners"] - s["active_owners"]],
        ["Pet (สัตว์เลี้ยง)", s["total_pets"], s["active_pets"], s["deleted_pets"]],
        ["Doctor (สัตวแพทย์)", s["total_doctors"], s["active_doctors"],
         s["total_doctors"] - s["active_doctors"]],
        ["Visit (ประวัติ/นัดหมาย)", s["total_visits"], s["active_visits"],
         s["deleted_visits"]],
    ]
    species_rows = [[sp, cnt] for sp, cnt in sorted(s["species_count"].items())]
    cost_rows = [[f"{s['cost_min']:.2f}", f"{s['cost_max']:.2f}", f"{s['cost_avg']:.2f}"]]
    total_cost = s["cost_avg"] * s["active_visits"]
    species_text = " | ".join(f"{sp}: {cnt}" for sp, cnt in sorted(s["species_count"].items())) or "-"

    print_view_page(
        "Summary Statistics (สถิติโดยสรุป)",
        [
            ("Records — จำนวนข้อมูล", ["Entity", "Total", "Active", "Deleted"],
             count_rows, ["l", "r", "r", "r"]),
            ("Species — สัตว์เลี้ยงแยกตามชนิด (Active)", ["Species", "Count"],
             species_rows, ["l", "r"]),
            ("Cost — ค่าใช้จ่ายต่อครั้ง (บาท, Active)", ["Min", "Max", "Average"],
             cost_rows, ["r", "r", "r"]),
        ],
        [
            _kv("เจ้าของ", f"{s['total_owners']} รายการ ({s['active_owners']} Active)"),
            _kv("สัตว์เลี้ยง", f"{s['total_pets']} รายการ ({s['active_pets']} Active / {s['deleted_pets']} Deleted)"),
            _kv("สัตวแพทย์", f"{s['total_doctors']} รายการ ({s['active_doctors']} Active)"),
            _kv("ประวัติ/นัดหมาย", f"{s['total_visits']} รายการ ({s['active_visits']} Active / "
                                   f"{s['cancelled_visits']} ยกเลิกนัด / {s['deleted_visits']} Deleted)"),
            _kv("สัตว์ที่มีนัด", f"{s['upcoming_pets']} ตัว ภายใน 30 วันข้างหน้า"),
            _kv("ค่าใช้จ่ายรวม", f"{total_cost:.2f} บาท"),
            _kv("ต่ำสุด / สูงสุด / เฉลี่ย", f"{s['cost_min']:.2f} / {s['cost_max']:.2f} / {s['cost_avg']:.2f} บาท"),
            _kv("จำนวนแยกตามชนิด", species_text),
        ],
    )


def menu_view():
    while True:
        print_menu("เมนูดูข้อมูล (View)", [
            "ดูรายการข้อมูล",
            (1, "View One", "ดูรายการเดียว"),
            (2, "View All", "ดูทั้งหมด"),
            (3, "Filter",   "ดูแบบกรอง"),
            "สถิติและสรุป",
            (4, "Summary",  "สถิติโดยสรุป"),
            (5, "History",  "ประวัติการมาใช้บริการของเจ้าของ (30 วันที่ผ่านมา)"),
            (6, "Upcoming", "นัดหมายล่วงหน้า (30 วันข้างหน้า)"),
        ], "กลับเมนูหลัก")
        choice = input_int("เลือกหมายเลข: ", 0, 6)
        if choice == 0:
            return
        elif choice == 1:
            view_single()
        elif choice == 2:
            view_all()
        elif choice == 3:
            view_filtered()
        elif choice == 4:
            view_stats()
        elif choice == 5:
            view_owner_history()
        elif choice == 6:
            view_upcoming()
        pause()



def seed20_data():
    """เพิ่มชุดข้อมูลตัวอย่างสำหรับทดสอบ Report และการมีสัตว์หลายตัวต่อเจ้าของ

    ชุดนี้มี 5 Owners, 20 Pets, 5 Doctors และ 20 Visits
    โดยเจ้าของแต่ละคนมีสัตว์เลี้ยง 4 ตัว และ Visit แบ่งเป็น 10 รายการย้อนหลัง
    กับ 10 รายการนัดล่วงหน้า เพื่อให้ทดสอบรายงานได้ครบ
    """
    if any(store.count() > 0 for store in (owners, pets, visits, doctors)):
        print("พบว่ามีข้อมูลอยู่แล้วในระบบ")
        confirm = input("ต้องการเพิ่มชุดข้อมูลตัวอย่าง 20 Pets + 20 Visits ต่อจากข้อมูลเดิมหรือไม่? (y/n): ").strip().lower()
        if confirm not in ("y", "yes"):
            print("ยกเลิกการเพิ่มข้อมูลตัวอย่าง")
            return

    owner_data = [
        ("สมชาย", "0811111111", "10 ถ.รัชดา"),
        ("วิภา", "0822222222", "25 ถ.พหลฯ"),
        ("ธนกร", "0833333333", "88 ถ.ลาดพร้าว"),
        ("ศิริพร", "0844444444", "42 ถ.พระราม2"),
        ("ปกรณ์", "0855555555", "19 ถ.สุขุมวิท"),
    ]
    owner_ids = []
    for name, phone, address in owner_data:
        check_fits(name, 30, "owner.name")
        check_fits(phone, 15, "owner.phone")
        check_fits(address, 50, "owner.address")
        oid = owners.next_id()
        owners.add({"owner_id": oid, "name": name, "phone": phone, "address": address, "is_active": True})
        log_action(f"Added Owner ID {oid} \"{name}\" (seed20)")
        owner_ids.append(oid)

    doctor_data = [
        ("หมอเอก", "อายุรกรรม", 300.0),
        ("หมอกมล", "ศัลยกรรม", 500.0),
        ("หมอชนนท์", "ผิวหนัง", 250.0),
        ("หมอธนา", "ทันตกรรม", 350.0),
        ("หมอภาคิน", "วัคซีน", 200.0),
    ]
    doctor_records = []
    for name, specialty, fee in doctor_data:
        check_fits(name, 30, "doctor.name")
        check_fits(specialty, 30, "doctor.specialty")
        did = doctors.next_id()
        rec = {"doctor_id": did, "name": name, "specialty": specialty, "fee": fee, "is_active": True}
        doctors.add(rec)
        log_action(f"Added Doctor ID {did} \"{name}\" (seed20)")
        doctor_records.append(rec)

    pet_data = [
        ("Bobby", "Dog", "Poodle", 2021, 0), ("Mimi", "Cat", "Persian", 2022, 0),
        ("Lucky", "Dog", "Beagle", 2020, 0), ("Nana", "Bird", "Cockatiel", 2023, 0),
        ("Leo", "Cat", "Siamese", 2021, 1), ("Mochi", "Dog", "Shiba Inu", 2022, 1),
        ("Snow", "Rabbit", "Holland Lop", 2023, 1), ("Pang", "Dog", "Golden Retriever", 2019, 1),
        ("Tiger", "Cat", "Bengal", 2020, 2), ("Oreo", "Dog", "French Bulldog", 2021, 2),
        ("Choco", "Bird", "Parakeet", 2022, 2), ("Mocha", "Cat", "Ragdoll", 2023, 2),
        ("Kiki", "Dog", "Corgi", 2020, 3), ("Fah", "Cat", "British Shorthair", 2021, 3),
        ("Mint", "Rabbit", "Mini Rex", 2022, 3), ("Tofu", "Bird", "Lovebird", 2023, 3),
        ("Coco", "Dog", "Chihuahua", 2019, 4), ("Milo", "Cat", "Scottish Fold", 2021, 4),
        ("Puffy", "Dog", "Pomeranian", 2022, 4), ("Lily", "Cat", "American Shorthair", 2020, 4),
    ]
    pet_ids = []
    for name, species, breed, year, owner_idx in pet_data:
        check_fits(name, 20, "pet.name")
        check_fits(species, 10, "pet.species")
        check_fits(breed, 20, "pet.breed")
        pid = pets.next_id()
        pets.add({"pet_id": pid, "owner_id": owner_ids[owner_idx], "name": name, "species": species, "breed": breed, "birth_year": year, "is_active": True})
        log_action(f"Added Pet ID {pid} \"{name}\" (seed20)")
        pet_ids.append(pid)

    reasons = ["ตรวจสุขภาพ", "ฉีดวัคซีน", "ตรวจฟัน", "ตรวจแผล", "ตัดขน", "ทำหมัน", "ฉีดวัคซีนพิษ", "ขูดหินปูน", "นัดติดตามผล", "ผ่าตัดขา"]
    diagnoses = ["ปกติดี", "รอฉีด", "พบหินปูน", "แผลดีขึ้น", "เรียบร้อย", "พักฟื้น", "เตรียมฉีด", "รอขูดหินปูน", "ติดตามอาการ", "กำลังพักฟื้น"]
    visit_offsets = [-29, -26, -23, -20, -17, -14, -11, -8, -5, -2, 2, 4, 6, 9, 12, 15, 18, 21, 24, 28]
    times = ["09:00", "09:30", "10:00", "10:30", "11:00", "13:00", "13:30", "14:00", "14:30", "15:00"]
    for i, offset in enumerate(visit_offsets):
        doc = doctor_records[i % len(doctor_records)]
        reason = reasons[i % len(reasons)]
        diagnosis = diagnoses[i % len(diagnoses)]
        time_str = times[i % len(times)]
        check_fits(reason, 40, "visit.reason")
        check_fits(diagnosis, 40, "visit.diagnosis")
        cost = estimate_cost(reason, doc)
        vid = visits.next_id()
        visit_date = _seed_date(offset)
        visits.add({
            "visit_id": vid, "pet_id": pet_ids[i], "doctor_id": doc["doctor_id"],
            "visit_date": visit_date, "visit_time": time_str, "reason": reason,
            "diagnosis": diagnosis if offset < 0 else "-", "cost": cost, "is_active": True,
            "status": STATUS_COMPLETED if offset < 0 else STATUS_BOOKED,
        })
        log_action(f"Added Visit ID {vid} (Pet {pet_ids[i]}, Doctor {doc['doctor_id']}, {visit_date} {time_str}) (seed20)")

    print("เพิ่มชุดข้อมูลตัวอย่างสำเร็จ")
    print("  - Owners  : 5 คน")
    print("  - Pets    : 20 ตัว (เจ้าของแต่ละคน 4 ตัว)")
    print("  - Doctors : 5 คน")
    print("  - Visits  : 20 รายการ (10 ย้อนหลัง + 10 นัดล่วงหน้า)")

# ======================================================================
# 13) SEED DATA — ใส่ข้อมูลตัวอย่างสำหรับทดสอบ (python main.py seed)
# ======================================================================

def check_fits(text, max_bytes, field_name):
    """ตรวจสอบว่าข้อความไม่ยาวเกินขนาดฟิลด์ (เป็นไบต์ ไม่ใช่ตัวอักษร)
    ถ้าเกิน ให้แจ้ง error ทันทีแทนที่จะปล่อยให้ pack_record ตัดข้อความแบบเงียบๆ
    (ภาษาไทยใช้ ~3 ไบต์ต่อตัวอักษร ต่างจากภาษาอังกฤษที่ใช้ 1 ไบต์)"""
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        raise ValueError(
            f"[seed_data] ฟิลด์ {field_name} = \"{text}\" ยาว {size} ไบต์ "
            f"เกินขนาดที่กำหนด ({max_bytes} ไบต์) กรุณาแก้ข้อความให้สั้นลง"
        )


def _seed_date(offset_days):
    """คืนวันที่ (string YYYY-MM-DD) เทียบจากวันนี้ +/- จำนวนวัน"""
    return (datetime.date.today() + datetime.timedelta(days=offset_days)).strftime("%Y-%m-%d")


def seed_data():
    if (owners.count() > 0 or pets.count() > 0
            or visits.count() > 0 or doctors.count() > 0):
        print("พบว่ามีข้อมูลอยู่แล้วในไฟล์ (owners/pets/doctors/visits ไม่ว่างเปล่า)")
        confirm = input("ต้องการเพิ่มข้อมูลตัวอย่างต่อท้ายของเดิมหรือไม่? (y/n): ").strip().lower()
        if confirm not in ("y", "yes"):
            print("ยกเลิกการ seed ข้อมูล")
            return

    # ---------------- Owners ----------------
    owner_data = [
        ("สมชาย ใจดี", "0812345678", "123 ถ.สุขุมวิท"),
        ("วิภา ศรี", "0898765432", "45 ถ.รามคำแหง"),
        ("ประเสริฐ ม.", "0865551234", "78 ถ.ลาดพร้าว"),
        ("กัญญา รุ่ง", "0912223333", "99 ถ.พระราม 9"),
        ("ธีร แสงทอง", "0876665555", "12 ถ.เพชรบุรี"),
    ]
    owner_ids = []
    for name, phone, address in owner_data:
        check_fits(name, 30, "owner.name")
        check_fits(phone, 15, "owner.phone")
        check_fits(address, 50, "owner.address")
        oid = owners.next_id()
        owners.add({
            "owner_id": oid, "name": name, "phone": phone,
            "address": address, "is_active": True,
        })
        log_action(f"Added Owner ID {oid} \"{name}\" (seed)")
        owner_ids.append(oid)

    # ---------------- Doctors ----------------
    doctor_data = [
        ("หมอวิชัย", "ศัลยกรรม", 500.0),
        ("หมอปิยะ", "อายุรกรรม", 300.0),
        ("หมออรุณี", "ผิวหนัง", 250.0),
    ]
    doctor_records = []
    for name, specialty, fee in doctor_data:
        check_fits(name, 30, "doctor.name")
        check_fits(specialty, 30, "doctor.specialty")
        did = doctors.next_id()
        rec = {"doctor_id": did, "name": name, "specialty": specialty,
               "fee": fee, "is_active": True}
        doctors.add(rec)
        log_action(f"Added Doctor ID {did} \"{name}\" (seed)")
        doctor_records.append(rec)

    # ---------------- Pets ----------------
    # (name, species, breed, birth_year, owner_index, is_active)
    pet_data = [
        ("Milo", "Dog", "Poodle", 2021, 0, True),
        ("Bella", "Cat", "Persian", 2020, 1, True),
        ("Rocky", "Dog", "Bulldog", 2019, 2, True),
        ("Coco", "Bird", "Cockatiel", 2022, 0, True),
        ("Luna", "Cat", "Siamese", 2021, 3, True),
        ("Max", "Dog", "Golden Retriever", 2023, 4, True),
        ("Simba", "Cat", "Ragdoll", 2018, 1, False),   # ตัวอย่างสัตว์ที่ถูกลบ (Deleted)
    ]
    pet_ids = []
    for name, species, breed, year, owner_idx, active in pet_data:
        check_fits(name, 20, "pet.name")
        check_fits(species, 10, "pet.species")
        check_fits(breed, 20, "pet.breed")
        pid = pets.next_id()
        pets.add({
            "pet_id": pid, "owner_id": owner_ids[owner_idx], "name": name,
            "species": species, "breed": breed, "birth_year": year,
            "is_active": active,
        })
        log_action(f"Added Pet ID {pid} \"{name}\" (seed)")
        pet_ids.append(pid)

    # ---------------- Visits ----------------
    # (pet_index, doctor_index, day_offset, time, reason, diagnosis)
    # day_offset ติดลบ = ผ่านมาแล้ว, บวก = นัดในอนาคต
    # ราคาคิดอัตโนมัติ = SYMPTOM_PRICES[reason] + ค่าตรวจของหมอ
    visit_data = [
        (0, 1, -25, "09:00", "ฉีดวัคซีน", "สุขภาพดี"),
        (0, 1, -10, "10:00", "ตรวจสุขภาพ", "ปกติดี"),
        (0, 1, 5, "09:30", "นัดติดตามผล", "รอผลตรวจ"),
        (1, 1, -15, "14:00", "ทำหมัน", "ฟื้นตัวดี ปกติ"),
        (1, 1, 20, "11:00", "ฉีดวัคซีน", "รอฉีด"),
        (2, 0, -5, "13:00", "ผ่าตัดขา", "พักฟื้น"),
        (2, 0, 2, "09:00", "ตรวจแผลผ่าตัด", "รอตรวจ"),
        (3, 2, -20, "15:00", "ตัดขน", "เรียบร้อยดี"),
        (4, 1, -1, "10:30", "ตรวจฟัน", "มีหินปูนบ้าง"),
        (4, 1, 15, "10:30", "ขูดหินปูน", "นัดขูดหินปูน"),
        (5, 1, 28, "16:00", "ฉีดวัคซีนพิษ", "รอฉีด"),
        (6, 1, -40, "09:00", "ตรวจสุขภาพ", "ปกติดี"),  # ของ Simba (ก่อนถูกลบ)
    ]
    for pet_idx, doc_idx, offset, time_str, reason, diagnosis in visit_data:
        check_fits(reason, 40, "visit.reason")
        check_fits(diagnosis, 40, "visit.diagnosis")
        doc = doctor_records[doc_idx]
        cost = estimate_cost(reason, doc)
        vid = visits.next_id()
        visits.add({
            "visit_id": vid, "pet_id": pet_ids[pet_idx], "doctor_id": doc["doctor_id"],
            "visit_date": _seed_date(offset), "visit_time": time_str,
            "reason": reason, "diagnosis": diagnosis,
            "cost": cost, "is_active": True,
            "status": STATUS_COMPLETED if offset < 0 else STATUS_BOOKED,
        })
        log_action(f"Added Visit ID {vid} (Pet {pet_ids[pet_idx]}, "
                   f"Doctor {doc['doctor_id']}) (seed)")

    print("ใส่ข้อมูลตัวอย่างสำเร็จ:")
    print(f"  - เจ้าของ     : {len(owner_data)} คน")
    print(f"  - สัตวแพทย์   : {len(doctor_data)} คน")
    print(f"  - สัตว์เลี้ยง  : {len(pet_data)} ตัว (มี 1 ตัวที่ถูกลบไว้เป็นตัวอย่าง)")
    print(f"  - ประวัติ/นัดหมาย : {len(visit_data)} รายการ "
          f"(คิดราคาอัตโนมัติ = อาการ + ค่าตรวจหมอ)")
    print("\nลองรัน python main.py แล้วเลือก 6) Booking เพื่อจองคิว "
          "หรือ 4) View -> 6) ดูนัดหมายล่วงหน้า")


# ======================================================================
# 13.5) DATA FILES — ดูข้อมูลดิบในไฟล์ .dat (รวมช่องที่ถูกลบแบบ logical delete)
# ======================================================================

DATA_FILES = {
    1: ("owners.dat", "เจ้าของ", owners, OWNER_FMT, OWNER_FIELDS),
    2: ("pets.dat", "สัตว์เลี้ยง", pets, PET_FMT, PET_FIELDS),
    3: ("visits.dat", "ประวัติรักษา/นัดหมาย", visits, VISIT_FMT, VISIT_FIELDS),
    4: ("doctors.dat", "สัตวแพทย์", doctors, DOCTOR_FMT, DOCTOR_FIELDS),
}


def _dat_cell(field, value):
    if field == "is_active":
        return "Active" if value else "Deleted"
    if field in ("cost", "fee"):
        return f"{value:.2f}"
    if field == "status":
        return VISIT_STATUS_EN.get(value, f"?{value}")
    return value


def _dat_stats(store):
    """(ขนาดไฟล์, รายการช่องทั้งหมด, จำนวน Active, ไบต์ส่วนเกินที่ไม่ครบ record)"""
    size = os.path.getsize(store.path)
    slots = store.all_slots()
    active = sum(1 for _, r in slots if r["is_active"])
    return size, slots, active, size % store.record_size


def view_data_file(choice):
    """แสดงข้อมูลทุกช่อง (slot) ในไฟล์ .dat หนึ่งไฟล์ รวมที่ถูกลบไปแล้ว"""
    fname, th, store, fmt, fields = DATA_FILES[choice]
    size, slots, active, extra = _dat_stats(store)

    info_rows = [
        ["File", fname],
        ["Location", store.path],
        ["Size", f"{size} bytes"],
        ["Record size", f"{store.record_size} bytes/record"],
        ["Byte order", "Little-Endian"],
        ["Struct format", fmt],
        ["Fields", ", ".join(fields)],
    ]
    headers = ["Slot", "Offset"] + fields
    rows = [[i, i * store.record_size] + [_dat_cell(f, r[f]) for f in fields]
            for i, r in slots]
    aligns = ["r", "r"] + [
        "r" if isinstance(slots[0][1][f], (int, float)) and not isinstance(slots[0][1][f], bool)
        else "l" for f in fields
    ] if slots else None

    healthy = "ปกติ (ขนาดไฟล์หารด้วยขนาด record ลงตัว)" if extra == 0 else \
        f"ผิดปกติ — มีไบต์เกิน {extra} ไบต์ (ระเบียนท้ายไฟล์ไม่ครบ)"
    print_view_page(
        f"Data File — {fname} ({th})",
        [
            ("ข้อมูลไฟล์", ["Item", "Value"], info_rows, ["l", "l"], True),
            (f"ข้อมูลในไฟล์ {fname} (ทุกช่อง รวมที่ถูกลบ)", headers, rows, aligns),
        ],
        [
            _kv("จำนวนช่องทั้งหมด", f"{len(slots)} ช่อง"),
            _kv("Active", f"{active} ช่อง"),
            _kv("Deleted (ช่องว่างรอใช้ซ้ำ)", f"{len(slots) - active} ช่อง"),
            _kv("สถานะไฟล์", healthy),
        ],
    )


def view_data_overview():
    """สรุปทุกไฟล์ .dat ในตารางเดียว"""
    rows = []
    total_slots = total_active = total_bytes = 0
    problems = 0
    for key in sorted(DATA_FILES):
        fname, th, store, _, _ = DATA_FILES[key]
        size, slots, active, extra = _dat_stats(store)
        rows.append([fname, th, store.record_size, size, len(slots), active,
                     len(slots) - active, "OK" if extra == 0 else f"Extra {extra}B"])
        total_slots += len(slots)
        total_active += active
        total_bytes += size
        problems += 1 if extra else 0
    print_view_page(
        "Data Files Overview (ภาพรวมไฟล์ .dat)",
        [("ไฟล์ข้อมูลทั้งหมดในระบบ", 
          ["File", "Entity", "RecSize", "Bytes", "Slots", "Active", "Deleted", "Health"],
          rows, ["l", "l", "r", "r", "r", "r", "r", "l"])],
        [
            _kv("โฟลเดอร์ข้อมูล", DATA_DIR),
            _kv("จำนวนไฟล์ .dat", f"{len(rows)} ไฟล์"),
            _kv("ขนาดรวม", f"{total_bytes} bytes"),
            _kv("ช่องทั้งหมด", f"{total_slots} ช่อง (Active {total_active} / Deleted {total_slots - total_active})"),
            _kv("ไฟล์ที่ผิดปกติ", f"{problems} ไฟล์"),
        ],
    )


def menu_data_files():
    while True:
        print_menu("ไฟล์ข้อมูล (Data Files)", [
            "ดูทีละไฟล์",
            (1, "owners.dat",  "เจ้าของ"),
            (2, "pets.dat",    "สัตว์เลี้ยง"),
            (3, "visits.dat",  "ประวัติรักษา / นัดหมาย"),
            (4, "doctors.dat", "สัตวแพทย์"),
            "ภาพรวม",
            (5, "Overview",    "ภาพรวมทุกไฟล์"),
        ], "กลับเมนูหลัก")
        choice = input_int("เลือกหมายเลข: ", 0, 5)
        if choice == 0:
            return
        elif choice == 5:
            view_data_overview()
        else:
            view_data_file(choice)
        pause()


# ======================================================================
# 14) MAIN MENU — เมนูหลัก (entry point)
# ======================================================================

def menu_add():
    entity = choose_entity("เพิ่มข้อมูล — เลือกประเภทข้อมูล")
    if entity == 0:
        return
    if entity == 1:
        add_owner()
    elif entity == 2:
        add_pet()
    elif entity == 3:
        add_visit()
    else:
        add_doctor()


def menu_update():
    entity = choose_entity("แก้ไขข้อมูล — เลือกประเภทข้อมูล")
    if entity == 0:
        return
    if entity == 1:
        update_owner()
    elif entity == 2:
        update_pet()
    elif entity == 3:
        update_visit()
    else:
        update_doctor()


def menu_delete():
    entity = choose_entity("ลบข้อมูล — เลือกประเภทข้อมูล")
    if entity == 0:
        return
    if entity == 1:
        delete_owner()
    elif entity == 2:
        delete_pet()
    elif entity == 3:
        delete_visit()
    else:
        delete_doctor()


def main():
    print("=" * MENU_WIDTH)
    print(" Pet Clinic System — ระบบจัดการคลินิกสัตว์เลี้ยง")
    print("=" * MENU_WIDTH)
    while True:
        print_menu("เมนูหลัก (Main Menu)", [
            "จัดการข้อมูล",
            (1, "Add",     "เพิ่มข้อมูล"),
            (2, "Update",  "แก้ไขข้อมูล"),
            (3, "Delete",  "ลบข้อมูล"),
            "ดูข้อมูล",
            (4, "View",    "ดูข้อมูล / สถิติ / นัดหมาย"),
            "รายงานและจองคิว",
            (5, "Reports", "สร้างรายงาน .txt 3 ไฟล์ (เลือก Owner สำหรับประวัติ)"),
            (6, "Booking", "จองคิวรวดเร็ว (หลายสัตว์ + เลือกหมอ + นัดวันเวลา)"),
            "การรักษา",
            (7, "Complete", "ปิดนัด: ตรวจเสร็จแล้ว / ยกเลิกนัด"),
            "ระบบ",
            (8, "Files",   "ดูข้อมูลไฟล์ .dat"),
        ], "ออกจากโปรแกรม")
        choice = input_int("เลือกเมนู: ", 0, 8)

        if choice == 1:
            menu_add()
            pause()
        elif choice == 2:
            menu_update()
            pause()
        elif choice == 3:
            menu_delete()
            pause()
        elif choice == 4:
            menu_view()
        elif choice == 5:
            from report import generate_report
            _, owner_rec = choose_owner()
            if owner_rec is not None:
                generate_report(owner_rec["owner_id"])
            pause()
        elif choice == 6:
            quick_booking()
            pause()
        elif choice == 7:
            finish_visit()
            pause()
        elif choice == 8:
            menu_data_files()
        elif choice == 0:
            print("ปิดโปรแกรมอย่างปลอดภัยแล้ว ขอบคุณที่ใช้บริการ")
            break


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "seed20":
        seed20_data()
    elif len(sys.argv) > 1 and sys.argv[1] == "seed":
        seed_data()
    else:
        main()