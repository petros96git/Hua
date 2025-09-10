"""
Αρχικοποίηση SQLite βάσης για τον scraper.

Δημιουργεί βασικούς πίνακες:
  - professors (με PRIMARY KEY: email)
  - courses (με PRIMARY KEY: course_code)
  - facilities (με PRIMARY KEY: name)

και συμπληρωματικούς:
  - student_services, e_platforms, contacts

Χρήση:
    python scripts/init_db.py [path/to/huahelper.db]
"""

import os
import sqlite3
import sys

DEFAULT_DB = "./db/huahelper.db"

# Σχήμα βασικών πινάκων
CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS professors (
  email TEXT PRIMARY KEY,
  f_name TEXT,
  l_name TEXT,
  gender TEXT,
  office TEXT,
  phone TEXT,
  category TEXT,
  area_of TEXT,
  academic_web_page TEXT,
  image_url TEXT
);

CREATE TABLE IF NOT EXISTS courses (
  course_code TEXT PRIMARY KEY,
  course_name TEXT,
  ects_points INTEGER,
  type TEXT,
  professor_1 TEXT,
  professor_2 TEXT,
  semester_1 INTEGER,
  semester_2 INTEGER,
  url TEXT
);

CREATE TABLE IF NOT EXISTS facilities (
  name TEXT PRIMARY KEY,
  email TEXT,
  phone TEXT,
  fax TEXT,
  location TEXT,
  working_hours TEXT,
  url TEXT
);
"""

# Συμπληρωματικοί πίνακες για υπηρεσίες, πλατφόρμες και γενικές επαφές
EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS student_services (
  name TEXT PRIMARY KEY,
  description TEXT,
  email TEXT,
  phone TEXT,
  url TEXT
);
CREATE TABLE IF NOT EXISTS e_platforms (
  name TEXT PRIMARY KEY,
  description TEXT,
  url TEXT,
  help_url TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
  key TEXT PRIMARY KEY,
  label TEXT,
  value TEXT,
  url TEXT
);
"""

def ensure_db(path: str) -> None:
    """Δημιουργεί path, συνδέεται, εκτελεί τα σχήματα και κλείνει."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(CORE_SCHEMA)
    cur.executescript(EXTRA_SCHEMA)
    con.commit()
    con.close()

def main(args: list[str]) -> None:
    """Entry point"""
    db_path = args[1] if len(args) > 1 else DEFAULT_DB
    ensure_db(db_path)
    print(f"Αρχικοποιήθηκε βάση δεδομένων: {db_path}")


if __name__ == "__main__":
    main(sys.argv)
