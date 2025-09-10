"""
Στόχος: scrape της ιστοσελίδας απο το Τμήμα Πληροφορικής & Τηλεματικής και ενοποίηση των στοιχείων (καθηγητές/μαθήματα/υπηρεσίες κ.λπ.) σε SQLite ώστε
να τα καταναλώνει το Rasa bot.
"""

from __future__ import annotations
import os
import re
import sqlite3
from typing import Dict, List, Optional, Tuple, Iterable
from urllib.parse import urljoin, urldefrag
import httpx
from bs4 import BeautifulSoup

# ΡΥΘΜΙΣΕΙΣ

SQLITE_DB_PATH: str = os.getenv("SQLITE_PATH", "./db/huahelper.db")
BASE_URL: str = "https://dit.hua.gr"

# Τρέχουσες πηγές (URLs) σελίδων
URL_FACULTY: str = f"{BASE_URL}/index.php/el/department-gr/faculty-members"
URL_UNDERGRAD: str = f"{BASE_URL}/index.php/el/studies/undergraduate-studies"
URL_FACILITIES: str = f"{BASE_URL}/index.php/el/department-gr/facilities"
URL_STUDENT_SERVICES: str = f"{BASE_URL}/index.php/el/department-gr/student-services"
URL_EPLATFORMS: str = f"{BASE_URL}/index.php/el/department-gr/e-platforms-gr"
URL_CONTACT: str = f"{BASE_URL}/index.php/el/department-gr/contact-access"

# ΒΟΗΘΗΤΙΚΑ

def collapse_ws(text: str | None) -> str:
    """Συμπύκνωση πολλαπλών whitespaces/νέων γραμμών σε ένα κενό."""
    return re.sub(r"\s+", " ", (text or "").strip())


def absolutize(href: Optional[str]) -> Optional[str]:
    """
    Μετατροπή σχετικών συνδέσμων σε απόλυτους έναντι BASE_URL.
    - mailto:, tel:, κτλ. μένουν ως έχουν
    - αφαιρούνται τυχόν fragments
    - διατηρούνται ήδη-απόλυτα URLs
    """
    if not href:
        return None
    # αφαιρουμε το fragment
    href = urldefrag(href)[0]  
    if re.match(r"^[a-zA-Z0-9+.-]+:.*", href):
         # π.χ. mailto:, tel:, http:, https:
        return href 
    if href.startswith("/"):
        return urljoin(BASE_URL, href)
    return href


def fetch_soup(client: httpx.Client, url: str) -> BeautifulSoup:
    """
    Λήψη HTML σελίδας και parsing με BeautifulSoup (lxml parser).
    Χρησιμοποιεί shared httpx.Client για επαναχρησιμοποίηση συνδέσεων.
    """
    r = client.get(url, timeout=30.0)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")

def safe_int(x: Optional[str]) -> Optional[int]:
    """Ασφαλής μετατροπή σε int (None σε αποτυχία)."""
    try:
        return int(x) if x is not None else None
    except Exception:
        return None


def norm_code(code: str | None) -> str | None:
    """Κανονικοποίηση κωδικού μαθήματος (strip + lower)."""
    return (code or "").strip().lower() or None


def deobfuscate_email(token: str | None) -> Optional[str]:
    """Μετατροπή 'it[at]hua[dot]gr' σε κανοικη διεύθυνση 'it@hua.gr'."""
    if not token:
        return None
    result = token
    result = re.sub(r"\s*\[at\]\s*", "@", result, flags=re.I)
    result = re.sub(r"\s*\(at\)\s*", "@", result, flags=re.I)
    result = re.sub(r"\s*\[dot\]\s*", ".", result, flags=re.I)
    result = re.sub(r"\s*\(dot\)\s*", ".", result, flags=re.I)
    return result.strip()


def slugify(text: str) -> str:
    """Δημιουργεί «κλειδί» (slug) μόνο με αλφαριθμητικούς/underscore."""
    text_norm = re.sub(r"[^\w]+", "_", collapse_ws(text))
    return re.sub(r"_+", "_", text_norm).strip("_").lower()


# Βοηθήματα Βάσης Δεδομένων

SCHEMA_EXTRA = """
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

def ensure_extra_tables(con: sqlite3.Connection) -> None:
    """Δημιουργία συμπληρωματικών πινάκων αν λείπουν."""
    con.executescript(SCHEMA_EXTRA)
    con.commit()

def upsert_professor(cur: sqlite3.Cursor, row: Dict[str, Optional[str]]) -> None:
    """
    Εισαγωγή/ενημέρωση καθηγητή (PRIMARY KEY: email).
    Αν λείπει email, προηγείται σύνθεση 'firstname.lastname@unknown' και αργότερα
    επικαιροποιείται όταν βρεθεί πραγματικό email.
    """
    cur.execute(
        """
        INSERT INTO professors(email, f_name, l_name, gender, office, phone, category,
                               area_of, academic_web_page, image_url)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(email) DO UPDATE SET
          f_name=COALESCE(excluded.f_name, professors.f_name),
          l_name=COALESCE(excluded.l_name, professors.l_name),
          gender=COALESCE(excluded.gender, professors.gender),
          office=COALESCE(excluded.office, professors.office),
          phone=COALESCE(excluded.phone, professors.phone),
          category=COALESCE(excluded.category, professors.category),
          area_of=COALESCE(excluded.area_of, professors.area_of),
          academic_web_page=COALESCE(excluded.academic_web_page, professors.academic_web_page),
          image_url=COALESCE(excluded.image_url, professors.image_url)
        """,
        (
            row.get("email"),
            row.get("f_name"),
            row.get("l_name"),
            row.get("gender"),
            row.get("office"),
            row.get("phone"),
            row.get("category"),
            row.get("area_of"),
            row.get("page"),
            row.get("image"),
        ),
    )


def upsert_course(cur: sqlite3.Cursor, row: Dict[str, Optional[str]]) -> None:
    """Upsert μαθήματος. Τα περισσότερα πεδία ίσως είναι ακόμη NULL από scraping."""
    cur.execute(
        """
        INSERT INTO courses(course_code, course_name, ects_points, type,
                            professor_1, professor_2, semester_1, semester_2, url)
        VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(course_code) DO UPDATE SET
          course_name=COALESCE(excluded.course_name, courses.course_name),
          ects_points=COALESCE(excluded.ects_points, courses.ects_points),
          type=COALESCE(excluded.type, courses.type),
          professor_1=COALESCE(excluded.professor_1, courses.professor_1),
          professor_2=COALESCE(excluded.professor_2, courses.professor_2),
          semester_1=COALESCE(excluded.semester_1, courses.semester_1),
          semester_2=COALESCE(excluded.semester_2, courses.semester_2),
          url=COALESCE(excluded.url, courses.url)
        """,
        (
            row.get("code"),
            row.get("name"),
            row.get("ects"),
            row.get("type"),
            row.get("prof1"),
            row.get("prof2"),
            row.get("sem1"),
            row.get("sem2"),
            row.get("url"),
        ),
    )


def upsert_facility(cur: sqlite3.Cursor, row: Dict[str, Optional[str]]) -> None:
    """Upsert facility/υπηρεσίας (facilities)."""
    cur.execute(
        """
        INSERT INTO facilities(name, email, phone, fax, location, working_hours, url)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
          email=COALESCE(excluded.email, facilities.email),
          phone=COALESCE(excluded.phone, facilities.phone),
          fax=COALESCE(excluded.fax, facilities.fax),
          location=COALESCE(excluded.location, facilities.location),
          working_hours=COALESCE(excluded.working_hours, facilities.working_hours),
          url=COALESCE(excluded.url, facilities.url)
        """,
        (
            row.get("name"),
            row.get("email"),
            row.get("phone"),
            row.get("fax"),
            row.get("location"),
            row.get("hours"),
            row.get("url"),
        ),
    )


def upsert_student_service(cur: sqlite3.Cursor, row: Dict[str, Optional[str]]) -> None:
    """Upsert εγγραφής στις (γενικές) φοιτητικές υπηρεσίες."""
    cur.execute(
        """
        INSERT INTO student_services(name, description, email, phone, url)
        VALUES(?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
          description=COALESCE(excluded.description, student_services.description),
          email=COALESCE(excluded.email, student_services.email),
          phone=COALESCE(excluded.phone, student_services.phone),
          url=COALESCE(excluded.url, student_services.url)
        """,
        (
            row.get("name"),
            row.get("description"),
            row.get("email"),
            row.get("phone"),
            row.get("url"),
        ),
    )


def upsert_eplatform(cur: sqlite3.Cursor, row: Dict[str, Optional[str]]) -> None:
    """Upsert ηλεκτρονικής πλατφόρμας (όνομα/περιγραφή/url/help_url)."""
    cur.execute(
        """
        INSERT INTO e_platforms(name, description, url, help_url)
        VALUES(?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
          description=COALESCE(excluded.description, e_platforms.description),
          url=COALESCE(excluded.url, e_platforms.url),
          help_url=COALESCE(excluded.help_url, e_platforms.help_url)
        """,
        (
            row.get("name"),
            row.get("description"),
            row.get("url"),
            row.get("help_url"),
        ),
    )


def upsert_contact(cur: sqlite3.Cursor, row: Dict[str, Optional[str]]) -> None:
    """Upsert επαφής/κλειδιού επικοινωνίας (key/label/value/url)."""
    cur.execute(
        """
        INSERT INTO contacts(key, label, value, url)
        VALUES(?,?,?,?)
        ON CONFLICT(key) DO UPDATE SET
          label=COALESCE(excluded.label, contacts.label),
          value=COALESCE(excluded.value, contacts.value),
          url=COALESCE(excluded.url, contacts.url)
        """,
        (
            row.get("key"),
            row.get("label"),
            row.get("value"),
            row.get("url"),
        ),
    )

# SCRAPERS

def scrape_professors(client: httpx.Client, cur: sqlite3.Cursor) -> int:
    """
    Scrape καθηγητών από τη σελίδα μελών ΔΕΠ.
    Προσπαθεί πρώτα από την βασική σελίδα και (αν λείπουν πεδία) ανοίγει τη σελίδα λεπτομερειών.
    Επιστρέφει πλήθος upserts.
    """
    soup = fetch_soup(client, URL_FACULTY)
    # Τα containers έχουν inline padding ή είναι <article>
    cards: Iterable[BeautifulSoup] = soup.select("div[style*='padding']") or soup.select("article") or []

    count = 0
    for card in cards:
        # Επικεφαλίδα με πλήρες όνομα και κατηγορία (π.χ. «Μάρα Νικολαΐδου, Καθηγήτρια»)
        h = card.find(["h2", "h3", "h4"])
        if not h:
            continue
        heading = collapse_ws(h.get_text(" ", strip=True))
        if not heading:
            continue
        parts = [p.strip() for p in heading.split(",")]
        full_name = parts[0]
        category = parts[1] if len(parts) > 1 else None

        # Χοντρική διάσπαση ονόματος σε (μικρό/επώνυμο)
        name_tokens = full_name.split()
        f_name, l_name = (name_tokens[0], " ".join(name_tokens[1:])) if len(name_tokens) > 1 else (full_name, "")

        # Προετοιμασία εγγραφής για upsert
        prof = {
            "email": None,
            "f_name": f_name,
            "l_name": l_name,
            "gender": None,
            "office": None,
            "phone": None,
            "category": category,
            "area_of": None,
            "page": None,
            "image": None,
        }

        # Εικόνα (αν υπάρχει)
        img_el = card.find("img")
        if img_el and img_el.has_attr("src"):
            prof["image"] = absolutize(img_el["src"])

        # Αναζήτηση πληροφοριών σε παραγράφους
        for p in card.find_all("p"):
            txt = collapse_ws(p.get_text(" ", strip=True))
            if not txt:
                continue
            # Γνωστικό αντικείμενο
            if re.search(r"γνωστικ(?:ό|ο) αντικείμενο", txt, flags=re.I):
                parts_ = re.split(r"[:：]", txt, maxsplit=1)
                prof["area_of"] = collapse_ws(parts_[1]) if len(parts_) == 2 else None
            # Γραφείο/Τηλέφωνο
            if re.search(r"γραφεί(?:ο|ο)", txt, flags=re.I) or re.search(r"τηλ", txt, flags=re.I):
                m_off = re.search(r"γραφεί(?:ο|ο)\s*[:：]\s*([^,]+)", txt, flags=re.I)
                if m_off:
                    prof["office"] = collapse_ws(m_off.group(1))
                phones = re.findall(r"\+?\d[\d\s\-]{6,}\d", txt)
                if phones:
                    prof["phone"] = phones[0].strip()
            # Email (obfuscated/λανθασμενο οποτε καλουμε την deobfuscate)
            if re.search(r"@|\[at\]|\(at\)", txt, flags=re.I):
                m_em = re.search(r"[A-Za-z0-9._%+-]+\s*(?:\[at\]|@|\(at\))\s*[A-Za-z0-9.-]+\s*(?:\[dot\]|\.|\(dot\))\s*[A-Za-z]{2,}", txt, flags=re.I)
                if m_em:
                    prof["email"] = deobfuscate_email(m_em.group(0))

        # Link «Περισσότερες Πληροφορίες»
        detail_a = card.find("a", string=re.compile("Περισσότερες", re.I))
        detail_url = absolutize(detail_a["href"]) if detail_a and detail_a.has_attr("href") else None

        # Αν λείπουν πεδία και υπάρχει link, επιχειρούμε scraping στη σελίδα λεπτομερειών
        need_detail = any(prof[k] is None for k in ["email", "office", "phone", "area_of", "page"])
        if need_detail and detail_url:
            try:
                ds = fetch_soup(client, detail_url)
                full_txt = collapse_ws(ds.get_text(" ", strip=True))

                def find_after(label_regex: str) -> Optional[str]:
                    # Σκανάρει p/li/div για μοτίβο 'Label: Value'
                    for el in ds.find_all(["p", "li", "div"]):
                        text = collapse_ws(el.get_text(" ", strip=True))
                        m = re.search(label_regex + r"\s*[:：]\s*([^\n]+)", text, flags=re.I)
                        if m:
                            return collapse_ws(m.group(1))
                    # fallback στο όλο κείμενο
                    m_full = re.search(label_regex + r"\s*[:：]\s*([^\n]+)", full_txt, flags=re.I)
                    return collapse_ws(m_full.group(1)) if m_full else None

                if prof["email"] is None:
                    m_de = re.search(r"[A-Za-z0-9._%+-]+\s*(?:\[at\]|@|\(at\))\s*[A-Za-z0-9.-]+\s*(?:\[dot\]|\.|\(dot\))\s*[A-Za-z]{2,}", full_txt)
                    if m_de:
                        prof["email"] = deobfuscate_email(m_de.group(0))
                if prof["office"] is None:
                    prof["office"] = find_after(r"γραφεί(?:ο|ο)|office")
                if prof["phone"] is None:
                    phones = re.findall(r"\+?\d[\d\s\-]{6,}\d", full_txt)
                    prof["phone"] = phones[0] if phones else None
                if prof["area_of"] is None:
                    prof["area_of"] = find_after(r"γνωστικ(?:ό|ο) αντικείμενο|research area|field")
                if prof["page"] is None:
                    a_page = ds.find("a", href=True, string=re.compile(r"site|web|home", re.I))
                    if a_page:
                        prof["page"] = absolutize(a_page["href"])
                    else:
                        ext = ds.find("a", href=re.compile(r"^https?://"))
                        prof["page"] = ext["href"] if ext else None
            except Exception as exc:
                print(f"[warn] details page failed for {full_name}: {exc}")

        # Αν ακόμη δεν υπάρχει email, συνθέτουμε προσωρινό μοναδικό απο το όνομα
        if not prof.get("email"):
            synthetic = f"{slugify(f_name)}.{slugify(l_name)}@unknown"
            prof["email"] = synthetic

        upsert_professor(cur, prof)
        count += 1

    return count


# Patterns για "CODE - Τίτλος"
COURSE_PATTERNS = [
    re.compile(r"^\s*([A-Za-zΑ-Ωα-ωΆΈΉΊΌΎΏΪΫ0-9]{1,12})\s*[-–—]\s*(.{3,})$"),
    re.compile(r"^\s*([A-Za-zΑ-Ωα-ωΆΈΉΊΌΎΏΪΫ0-9]{1,12})\s*[:：]\s*(.{3,})$"),
]

def extract_code_and_title(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Επιστρέφει (κωδικός, τίτλος) για μοτίβα «MY01 - Υπολογιστικά Μαθηματικά» ή «ΠΛΗ20:...»."""
    txt = collapse_ws(text)
    for pat in COURSE_PATTERNS:
        m = pat.match(txt)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None, None

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+\s*(?:\[at\]|@|\(at\))\s*[A-Za-z0-9.-]+\s*(?:\[dot\]|\.|\(dot\))\s*[A-Za-z]{2,}", re.I)

def extract_emails_from_soup(soup: BeautifulSoup) -> List[str]:
    # mailto: links
    emails = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            emails.append(href.split(":", 1)[1].strip())

    #  κειμενο
    txt = collapse_ws(soup.get_text(" ", strip=True))
    emails.extend(m.group(0) for m in EMAIL_RE.finditer(txt))

    # normalize και deobfyscate 
    norm = []
    seen = set()
    for e in emails:
        e2 = deobfuscate_email(e).lower()
        # vaidation για μειλ
        if "@" not in e2 or " " in e2 or len(e2) > 100:
            continue
        if e2 not in seen:
            seen.add(e2)
            norm.append(e2)
    # προτιμηση σε @hua.gr mails
    norm.sort(key=lambda x: (not x.endswith("@hua.gr"), x))
    return norm

def find_label_value_like(soup: BeautifulSoup, label_regex: str) -> Optional[str]:
    # ψαχνουμε για label: value στο p/li/div
    pat = re.compile(label_regex + r"\s*[:：]\s*([^\n]+)", re.I)
    for el in soup.find_all(["p", "li", "div", "td"]):
        t = collapse_ws(el.get_text(" ", strip=True))
        m = pat.search(t)
        if m:
            return collapse_ws(m.group(1))
    return None


def scrape_undergrad_courses(client: httpx.Client, cur: sqlite3.Cursor) -> int:
    """
    Scrape προπτυχιακών μαθημάτων + εμπλουτισμός από σελίδα μαθήματος.
    - Βρίσκει (code, title, href) από τη συγκεντρωτική σελίδα
    - Ανοίγει το href (αν υπάρχει) και εξάγει ects/type/semesters/emails
    - Γράφει στο professor_1/2 μόνο αν η τιμή μοιάζει με email
    """
    soup = fetch_soup(client, URL_UNDERGRAD)

    candidates: List[Tuple[str, str, Optional[str]]] = []
    for node in soup.find_all(["li", "p", "span", "a"]):
        text = collapse_ws(node.get_text(" ", strip=True))
        if not text or len(text) > 150:
            continue
        code, title = extract_code_and_title(text)
        if code and title:
            href = None
            if node.name == "a" and node.has_attr("href"):
                href = absolutize(node["href"])
            candidates.append((norm_code(code), title, href))

    seen = set()
    count = 0

    for code, title, href in candidates:
        if not code or code in seen:
            continue
        seen.add(code)

        ects = None
        ctype = None
        sem1 = None
        sem2 = None
        prof1 = None
        prof2 = None
        page_url = href

        if href:
            try:
                ds = fetch_soup(client, href)
                # ECTS
                ects_txt = find_label_value_like(ds, r"ects|πιστωτικ")
                if ects_txt:
                    m_ects = re.search(r"\d{1,2}", ects_txt)
                    if m_ects:
                        ects = safe_int(m_ects.group(0))

                # τυπος (ΥΠ/ΕΕ/ΕΡΓ κ.λπ.)
                type_txt = find_label_value_like(ds, r"τύπ|type|κατηγορ")
                if type_txt:
                    ctype = collapse_ws(type_txt.split()[0]) 

                # εξαμηνα
                sem_txt = find_label_value_like(ds, r"εξάμηνο|semester")
                if sem_txt:
                    nums = re.findall(r"\d{1,2}", sem_txt)
                    if nums:
                        sem1 = safe_int(nums[0])
                        if len(nums) > 1:
                            sem2 = safe_int(nums[1])

                # emails
                emails = extract_emails_from_soup(ds)
                if emails:
                    prof1 = emails[0]
                    if len(emails) > 1:
                        prof2 = emails[1]

            except Exception as e:
                print(f"[warn] course details failed for {code}: {e}")

        row = {
            "code": code,
            "name": title,
            "ects": ects,
            "type": ctype,
            "prof1": prof1,
            "prof2": prof2,
            "sem1": sem1,
            "sem2": sem2,
            "url": page_url,
        }

        # για ασφαλεια, μονο πληροφοριες που μοιαζουν με mail
        for k in ("prof1", "prof2"):
            v = row[k]
            if v and not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", v):
                row[k] = None

        upsert_course(cur, row)
        count += 1

    return count


def scrape_facilities(client: httpx.Client, cur: sqlite3.Cursor) -> int:
    """
    Scrape υποδομών: εντοπίζει επικεφαλίδες (h2/h3) και
    συλλέγει περιγραφές από επόμενα siblings.
    """
    soup = fetch_soup(client, URL_FACILITIES)
    count = 0
    for header in soup.find_all(["h2", "h3"]):
        name = collapse_ws(header.get_text(" ", strip=True))
        if not name:
            continue
        description_parts: List[str] = []
        for sib in header.next_siblings:
            if getattr(sib, "name", None) in ["h2", "h3"]:
                break
            if getattr(sib, "name", None) in ["p", "div", "ul", "li"]:
                text = collapse_ws(sib.get_text(" ", strip=True))
                if text:
                    description_parts.append(text)
        description = " ".join(description_parts) if description_parts else None
        upsert_facility(
            cur,
            {
                "name": name,
                "email": None,
                "phone": None,
                "fax": None,
                "location": None,
                "hours": None,
                "url": URL_FACILITIES,
            },
        )
        count += 1
    return count


def scrape_student_services(client: httpx.Client, cur: sqlite3.Cursor) -> int:
    """
    Scrape σελίδας υπηρεσιών φοιτητών: λίστα με επικεφαλίδες & περιγραφές.
    """
    soup = fetch_soup(client, URL_STUDENT_SERVICES)
    count = 0
    for header in soup.find_all(["h2", "h3", "h4"]):
        name = collapse_ws(header.get_text(" ", strip=True))
        if not name:
            continue
        description = None
        for sib in header.next_siblings:
            if getattr(sib, "name", None) in ["h2", "h3", "h4"]:
                break
            if getattr(sib, "name", None) in ["p", "div", "li", "ul"]:
                text = collapse_ws(sib.get_text(" ", strip=True))
                if text:
                    description = text
                    break
        upsert_student_service(
            cur,
            {
                "name": name,
                "description": description,
                "email": None,
                "phone": None,
                "url": URL_STUDENT_SERVICES,
            },
        )
        count += 1
    return count


def scrape_eplatforms(client: httpx.Client, cur: sqlite3.Cursor) -> int:
    """
    Scrape e-platforms: εντοπίζει rows με strong/b και εξάγει
    όνομα, περιγραφή και συνδέσμους (κύριο URL + help_url αν υπάρχει).
    """
    soup = fetch_soup(client, URL_EPLATFORMS)
    rows = soup.find_all("div", class_=re.compile(r"row")) or []
    count = 0
    for row in rows:
        strong = row.find(["strong", "b"])
        if not strong:
            continue
        name = collapse_ws(strong.get_text(" ", strip=True))
        if not name:
            continue
        parent_p = strong.find_parent("p")
        description = None
        if parent_p:
            full_p = collapse_ws(parent_p.get_text(" ", strip=True))
            description = full_p.replace(name, "", 1).lstrip(" :–—-")
        primary_url: Optional[str] = None
        help_url: Optional[str] = None
        for a in row.find_all("a", href=True):
            text = collapse_ws(a.get_text(" ", strip=True)).lower()
            href = absolutize(a["href"])
            if not primary_url:
                primary_url = href
            if any(k in text for k in ["guide", "help", "οδηγ", "βοήθεια"]):
                help_url = href
        upsert_eplatform(
            cur,
            {
                "name": name,
                "description": description,
                "url": primary_url or URL_EPLATFORMS,
                "help_url": help_url,
            },
        )
        count += 1
    return count


def scrape_contact_access(client: httpx.Client, cur: sqlite3.Cursor) -> int:
    """
    Scrape επαφων: διεύθυνση τμήματος + γραμματείες (τηλ/email).
    Κανονικοποίηση σε (key,label,value,url) με slugified keys.
    """
    soup = fetch_soup(client, URL_CONTACT)
    count = 0
    # Διεύθυνση (h3)
    h3 = soup.find("h3")
    if h3:
        addr = None
        for sib in h3.next_siblings:
            if getattr(sib, "name", None) == "p":
                text = collapse_ws(sib.get_text(" ", strip=True))
                if text:
                    addr = text
                    break
        if addr:
            upsert_contact(cur, {"key": "address", "label": collapse_ws(h3.get_text(" ", strip=True)), "value": addr, "url": None})
            count += 1
    # Γραμματείες (h4/h5)
    for h in soup.find_all(["h4", "h5"]):
        section = collapse_ws(h.get_text(" ", strip=True))
        if not section:
            continue
        slug = slugify(section)
        details = ""
        for sib in h.next_siblings:
            if getattr(sib, "name", None) in ["h4", "h5"]:
                break
            if getattr(sib, "name", None) in ["p", "div"]:
                txt = collapse_ws(sib.get_text(" ", strip=True))
                if txt:
                    details += " " + txt
        details = details.strip()
        phone_match = re.search(r"\+?\d[\d\s\-]{6,}\d", details)
        phone = phone_match.group(0).strip() if phone_match else None
        email_match = re.search(r"[A-Za-z0-9._%+-]+\s*(?:\[at\]|@|\(at\))\s*[A-Za-z0-9.-]+\s*(?:\[dot\]|\.|\(dot\))\s*[A-Za-z]{2,}", details)
        email = deobfuscate_email(email_match.group(0)) if email_match else None
        if phone:
            upsert_contact(cur, {"key": f"{slug}_phone", "label": section, "value": phone, "url": None})
            count += 1
        if email:
            upsert_contact(cur, {"key": f"{slug}_email", "label": section, "value": email, "url": None})
            count += 1
    # Χάρτης (Google/OpenStreetMap)
    map_a = soup.find("a", href=re.compile(r"(google\.com/maps|openstreetmap|goo\.gl/maps)", re.I))
    if map_a:
        upsert_contact(cur, {"key": "map", "label": "Χάρτης", "value": "Τοποθεσία", "url": map_a["href"]})
        count += 1
    return count

# main()
def main() -> None:
    """
    Οδηγεί όλη τη ροή scraping:
      1) Δημιουργία πινάκων αν λείπουν
      2) Κλήση επί μέρους scrapers
      3) Ενιαίο commit και κλείσιμο πόρων
    """
    
    con = sqlite3.connect(SQLITE_DB_PATH)
    cur = con.cursor()
    ensure_extra_tables(con)

    # trust_env=False: αγνοεί system proxies για να μην απαιτεί socksio
    client = httpx.Client(headers={"User-Agent": "huahelper-scraper/4.0"}, trust_env=False)
    try:
        try:
            prof_count = scrape_professors(client, cur)
            print(f"[professors] upsert: {prof_count}")
        except Exception as e:
            print(f"[warn] professors: {e}")

        try:
            course_count = scrape_undergrad_courses(client, cur)
            print(f"[courses] upsert: {course_count}")
        except Exception as e:
            print(f"[warn] courses: {e}")

        try:
            fac_count = scrape_facilities(client, cur)
            print(f"[facilities] upsert: {fac_count}")
        except Exception as e:
            print(f"[warn] facilities: {e}")

        try:
            ss_count = scrape_student_services(client, cur)
            print(f"[student_services] upsert: {ss_count}")
        except Exception as e:
            print(f"[warn] student_services: {e}")

        try:
            ep_count = scrape_eplatforms(client, cur)
            print(f"[e_platforms] upsert: {ep_count}")
        except Exception as e:
            print(f"[warn] e_platforms: {e}")

        try:
            contact_count = scrape_contact_access(client, cur)
            print(f"[contacts] upsert: {contact_count}")
        except Exception as e:
            print(f"[warn] contacts: {e}")

        con.commit()
    finally:
        cur.close()
        con.close()
        client.close()
    print("Ολοκληρώθηκε ο συγχρονισμός όλων των πηγών.")


if __name__ == "__main__":
    main()
