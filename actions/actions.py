"""
ΣΚΟΠΟΣ
Εδω περιέχονται όλες οι υλοποιήσεις των custom actions.
Οι δράσεις εξυπηρετούν αιτήματα χρηστών που σχετίζονται με:
- Αναζήτηση στοιχείων καθηγητών (email, γραφείο, τηλέφωνο, ιστοσελίδα)
- Λίστες μαθημάτων/μαθήματα ανά εξάμηνο/λεπτομέρειες μαθήματος
- Υπηρεσίες/δομές τμήματος (ωράρια, επικοινωνία, τοποθεσία)
- Ηλεκτρονικές πλατφόρμες, Φοιτητικές υπηρεσίες, Επικοινωνία τμήματος
- carousel οδηγών/συνδέσμων
- Βαθμολόγηση/feedback από τον χρήστη
"""

from __future__ import annotations
from typing import Any, Dict, List, Text, Optional, Tuple, Iterable
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, EventType
import os
import re
import sqlite3
import datetime
import httpx
from bs4 import BeautifulSoup
from difflib import get_close_matches

# ΡΥΘΜΙΣΕΙΣ / CONFIGURATION

# path αρχείου SQLite DB (το παιρνουμε από env var, αλλιώς default)
DB_PATH = os.getenv("SQLITE_PATH", "./db/huahelper.db")

# Σελίδα προπτυχιακών
DIT_UNDERGRAD_URL = "https://dit.hua.gr/index.php/el/studies/undergraduate-studies"

# Συνδεση στο SQLite.
def db_conn():
    return sqlite3.connect(DB_PATH)

# Δημιοργια του payload "Facebook Generic Template" για το carousel
def _fb_generic(elements: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "attachment": {
            "type": "template",
            "payload": {
                "template_type": "generic",
                "elements": elements
            }
        }
    }

#    Μετατρέπει εγγραφή καθηγητή (row από DB) σε στοιχείο (element) του template.
#    row = (email, f, l, gender, office, phone, category, area, page, image)
#    - Τίτλος: Ονοματεπώνυμο (+ κατηγορία σε παρένθεση εφοσον υπάρχει)
#    - Υπότιτλος: email/τηλέφωνο/γραφείο
#    - Κουμπιά: Άνοιγμα σελίδας (αν υπάρχει), mailto: (αν υπάρχει email)
#   - default_action: άνοιγμα της σελίδας όταν γίνει tap στην κάρτα (αν υπάρχει)
def _prof_to_fb_element(row) -> Dict[str, Any]:
    email, f, l, _g, office, phone, category, _area, page, image = row
    title = f"{(f or '').strip()} {(l or '').strip()}".strip() or (email or "")
    if category:
        title = f"{title} ({category})"

    # URL για tap στην κάρτα
    default_url = page if (page and page != "Δεν υποστηρίζεται") else None

    buttons: List[Dict[str, str]] = []
    if default_url:
        buttons.append({"type": "web_url", "title": "Άνοιγμα", "url": default_url})
    if email:
        buttons.append({"type": "web_url", "title": "Email", "url": f"mailto:{email}"})

    # Υπότιτλος: bullets (μέχρι 80 χαρακτήρες)
    subtitle_bits = []
    if email:
        subtitle_bits.append(f"Email: {email}")
    if phone:
        subtitle_bits.append(f"Τηλ: {phone}")
    if office:
        subtitle_bits.append(f"Γραφ.: {office}")
    subtitle = " • ".join(subtitle_bits)[:80]

    el: Dict[str, Any] = {
        "title": title,
        "subtitle": subtitle,
        "image_url": (image or "").strip(),
        "buttons": buttons[:3], 
    }
    if default_url:
        el["default_action"] = {
            "type": "web_url",
            "url": default_url,
            "webview_height_ratio": "tall"
        }
    return el

# Αποστέλλει (α) σύντομο text fallback με έως 3 γραμμές και carousel (έως 10 κάρτες).
# Είναι χρήσιμο όταν υπάρχουν πολλαπλά αποτελέσματα.
def _send_prof_carousel(dispatcher: CollectingDispatcher, rows: List[tuple]) -> None:

    #Text fallback, ώστε να φαινονται δεδομενα ακομα και χωρις carousel
    lines = []
    for r in rows[:3]:
        email, f, l, _g, office, phone, category, area, page, image = r
        name = f"{(f or '').strip()} {(l or '').strip()}".strip()
        bits = []
        if email:
            bits.append(f"Email: {email}")
        if phone:
            bits.append(f"Τηλ: {phone}")
        if office:
            bits.append(f"Γραφείο: {office}")
        if page and page != "Δεν υποστηρίζεται":
            bits.append(page)
        lines.append(f"{name}{f' ({category})' if category else ''}\n" + " | ".join(bits))
    if lines:
        dispatcher.utter_message(text="Αποτελέσματα:\n• " + "\n• ".join(lines))

    #Το carousel
    elements = [_prof_to_fb_element(r) for r in rows[:10]]
    dispatcher.utter_message(custom={"facebook": _fb_generic(elements)})

# UTILITIES / ΓΕΝΙΚΑ

#    Κανονικοποιεί κωδικούς μαθημάτων:
#    - strip/trim
#    - lower()
#   - αφαίρεση κενών
#   Παράδειγμα: " ΜΥ 01 " -> "μυ01"
def normalize_code(code: str) -> str:
    return (code or "").strip().lower().replace(" ", "")

#   Δημιουργεί απλό element για carousel
def carousel_element(
    title: str,
    subtitle: str = "",
    buttons: Optional[List[Dict[str, str]]] = None,
    image_url: Optional[str] = None,
) -> Dict[str, Any]:

    return {
        "title": title,
        "subtitle": subtitle,
        "buttons": buttons or [],
        "image_url": image_url,
    }


#Κανονικοποίηση Ελληνικών χαρακτηρων(χωρίς τόνους)
REPLACEMENTS = {
    "ά": "α", "ί": "ι", "ϊ": "ι", "ΐ": "ι", "ώ": "ω", "ΰ": "υ", "ϋ": "υ", "ύ": "υ", "έ": "ε", "ό": "ο", "ή": "η",
    "Ά": "α", "Ί": "ι", "Ϊ": "ι", "Ώ": "ω", "Ϋ": "υ", "Ύ": "υ", "Έ": "ε", "Ό": "ο", "Ή": "η",
}

#    Επιστρέφει πεζοποιημένο κείμενο σε ελληνικούς χαρακτήρες.
#    Π.χ. "Βαρλάμης" -> "βαρλαμης"
def normalize_greek(text: str) -> str:
    t = text or ""
    for src, dst in REPLACEMENTS.items():
        t = t.replace(src, dst)
    return t.lower()

#    Ενώνει μη κενά/μη None strings με διαχωριστικό `sep` και
#    εξασφαλίζει ότι η τελική πρόταση τελειώνει με τελεία.
def safe_join(bits: Iterable[str], sep: str = ". ") -> str:
    chunks = [b.strip() for b in bits if b and b.strip()]
    if not chunks:
        return ""
    s = sep.join(chunks)
    return s if s.endswith(".") else s + "."


# Ανάκτηση καθηγητών από DB & αντιστοίχιση με ερώτημα 
def _db_all_professors_full() -> List[
    Tuple[str, str, str, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]
]:
    """
    Διαβάζει όλους τους καθηγητές από τη βάση και επιστρέφει λίστα από tuples:
    (email, f_name, l_name, gender, office, phone, category, area_of, academic_web_page, image_url)
    """
    with db_conn() as con:
        return con.execute(
            "SELECT email, f_name, l_name, gender, office, phone, category, area_of, academic_web_page, image_url "
            "FROM professors"
        ).fetchall()


def _display_name(f: Optional[str], l: Optional[str]) -> str:
    """Συνθέτει το πλήηρες ονοματεπώνυμο από f_name/l_name."""
    return f"{(f or '').strip()} {(l or '').strip()}".strip()


def _ranked_matches(query_text: str) -> List[Tuple]:
    """
    Κατατάσσει καθηγητές ως προς το ερώτημα.
    Σειρά προτεραιότητας:
      1) ακριβές ταίριασμα στο επώνυμο αρχικά
      2) ακριβές ταίριασμα στο όνομα
      3) ακριβές ταίριασμα στο πλήρες ονοματεπώνυμο
      4) υποσυμβολοσειρά (σε όνομα/επώνυμο/πλήρες)
      5) fuzzy ταίριασμα (get_close_matches) δλδ το κοντινότερο δυνατό αποτέλεσμα σε αυτό που έγραψε ο χρήστης

    Επιστρέφει ordered unique λίστα (μοναδικοποίηση βάση email).
    """
    q = normalize_greek((query_text or "").strip())
    if not q:
        return []

    rows = _db_all_professors_full()

    def norm_parts(row):
        _, f, l, *_ = row
        nf, nl = normalize_greek(f or ""), normalize_greek(l or "")
        return nf, nl, normalize_greek(_display_name(f, l))

    exact_last, exact_first, exact_full, sub_hits, fuzzy_hits = [], [], [], [], []

    full_names_norm, full_map = [], {}
    last_names_norm, last_map = [], {}
    first_names_norm, first_map = [], {}

    # Σάρωση όλων των εγγραφών και ταξινόμηση σε buckets
    for r in rows:
        nf, nl, nfull = norm_parts(r)
        full_names_norm.append(nfull)
        full_map.setdefault(nfull, []).append(r)
        last_names_norm.append(nl)
        last_map.setdefault(nl, []).append(r)
        first_names_norm.append(nf)
        first_map.setdefault(nf, []).append(r)

        if nl and q == nl:
            exact_last.append(r)
        elif nf and q == nf:
            exact_first.append(r)
        elif nfull and q == nfull:
            exact_full.append(r)
        elif (nl and nl in q) or (nf and nf in q) or (nfull and nfull in q) or (q in nl) or (q in nf) or (q in nfull):
            sub_hits.append(r)

    # Fuzzy ταιριάσματα (π.χ. απο ορθογραφικά)
    for cand in get_close_matches(q, full_names_norm, n=5, cutoff=0.85):
        fuzzy_hits.extend(full_map.get(cand, []))
    for cand in get_close_matches(q, last_names_norm, n=5, cutoff=0.85):
        fuzzy_hits.extend(last_map.get(cand, []))
    for cand in get_close_matches(q, first_names_norm, n=5, cutoff=0.85):
        fuzzy_hits.extend(first_map.get(cand, []))

    # Σύνθεση μοναδικής λίστας με τη σειρά των buckets
    seen, ordered = set(), []
    for bucket in (exact_last, exact_first, exact_full, sub_hits, fuzzy_hits):
        for r in bucket:
            # email ως μοναδικό κλειδί
            key = (r[0] or "").lower()
            if key not in seen:
                seen.add(key)
                ordered.append(r)
    return ordered

def _resolve_prof_from_slot_or_text(tracker: Tracker) -> List[Tuple]:
    """
    Προσπαθεί πρώτα από το slot 'professor_name'. Αν αποτύχει/λείπει,
    πάμε στο raw κείμενο του χρήστη (latest_message['text']).

    Επιστρέφει ταξινομημένη λίστα εγγραφών DB (tuples) όπως κάνααμε στο _ranked_matches().
    """
    slot_q = tracker.get_slot("professor_name")
    if slot_q:
        m = _ranked_matches(slot_q)
        if m:
            return m
    raw = (tracker.latest_message.get("text") or "").strip()
    return _ranked_matches(raw) if raw else []

def _prof_subtitle(email: Optional[str], phone: Optional[str], office: Optional[str]) -> str:
    """Δημιουργεί σύντομο υπότιτλο με email/τηλέφωνο/γραφείο (έως 80 χαρακτήρες)."""
    bits = []
    if email:
        bits.append(f"Email: {email}")
    if phone:
        bits.append(f"Τηλ: {phone}")
    if office:
        bits.append(f"Γραφείο: {office}")
    return " • ".join(bits)[:80]

def _prof_to_carousel_element(row) -> Dict[str, Any]:
    """
    Μετατρέπει καθηγητή (row) σε στοιχείο genericc arousel:
    - 'Περισσότερα' (link στη σελίδα)
    - 'Email' (mailto:)
    - 'Λεπτομέρειες' (postback intent με email)
    """
    email, f, l, _g, office, phone, category, _area, page, image = row
    title = _display_name(f, l) or (email or "")
    if category:
        title = f"{title} ({category})"
    buttons: List[Dict[str, str]] = []
    if page and page != "Δεν υποστηρίζεται":
        buttons.append({"type": "web_url", "title": "Περισσότερα", "url": page})
    if email:
        buttons.append({"type": "web_url", "title": "Email", "url": f"mailto:{email}"})
        buttons.append({
            "type": "postback",
            "title": "Λεπτομέρειες",
            "payload": f'/ask_professor_info{{"email":"{email}"}}'
        })
    return carousel_element(
        title=title,
        subtitle=_prof_subtitle(email, phone, office),
        buttons=buttons,
        image_url=image or None,
    )

def _send_prof_carousel(dispatcher: CollectingDispatcher, rows: List[tuple]) -> None:
    """
    Στέλνει μικτό αποτέλεσμα: (α) text fallback + (β) JSON payload για Generic Template.
    Χρήσιμο όταν υπάρχουν πολλαπλά ταίρια καθηγητών.
    """
    elements = [_prof_to_carousel_element(r) for r in rows[:10]]

    # 1) Plain-text fallback για να υπάρχει ορατότητα και σε text-only clients πχ στο testing που κανουμε rasa shell
    lines = []
    for r in rows[:3]:
        email, f, l, _g, office, phone, category, area, page, image = r
        title = f"{(f or '').strip()} {(l or '').strip()}".strip()
        bits = []
        if email:
            bits.append(f"Email: {email}")
        if phone:
            bits.append(f"Τηλ: {phone}")
        if office:
            bits.append(f"Γραφείο: {office}")
        if page and page != "Δεν υποστηρίζεται":
            bits.append(page)
        lines.append(f"{title}{f' ({category})' if category else ''}\n" + " | ".join(bits))
    if lines:
        dispatcher.utter_message(text="Αποτελέσματα:\n• " + "\n• ".join(lines))

    # 2) Facebook Generic Template (json_message)
    dispatcher.utter_message(
        json_message={
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "generic",
                    "elements": elements
                }
            }
        }
    )

# Εξαγωγή κωδικού μαθήματος από κείμενο
# Regex: 1-3 γράμματα (λατινικά ή ελληνικά) + προαιρετικό κενό + 1-3 ψηφία
CODE_RE = re.compile(r"(?i)\b([A-Za-zΑ-Ωα-ω]{1,3}\s?\d{1,3})\b")

def _extract_course_code(text: str) -> Optional[str]:
    """
    Προσπαθεί να εντοπίσει κωδικό μαθήματος μέσα στο κείμενο.
    Αν βρεθεί, τον κανονικοποιεί (μέσω normalize_code) και τον επιστρέφει.
    """
    m = CODE_RE.search(text or "")
    if not m:
        return None
    return normalize_code(m.group(1))

# Default fallback / Γενική απάντηση
class ActionDefaultFallback(Action):
    """Default δράση όταν δεν υπάρχει σίγουρη πρόθεση (intent)."""

    def name(self) -> Text:
        return "action_default_fallback"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[EventType]:
        # Σύντομο παράδειγμα ζητούμενων για να καθοδηγήσουμε τον χρήστη
        dispatcher.utter_message(
            text=(
                "Δεν είμαι σίγουρος για αυτό. Πες μου π.χ. «Καθηγητές», «Email Βαρλάμης» ή «Που είναι η βιβλιοθήκη»."
            )
        )
        return []

# Καθηγητές / Professors
def _format_professor_message(
    f_name: Optional[str],
    l_name: Optional[str],
    gender: Optional[str],
    category: Optional[str],
    area: Optional[str],
    office: Optional[str],
    email: Optional[str],
    phone: Optional[str],
    page: Optional[str],
) -> str:
    """
    Δημιουργεί ανθρώπινο κείμενο με τα στοιχεία καθηγητή,
    με πρόθεμα(θα το πουμε polite) ανάλογα το φύλο (Ο/Η).
    """
    polite = "Ο/Η" if not gender else ("Ο" if gender == "M" else "Η")
    parts: List[str] = []
    parts.append(f"{polite} {f_name or ''} {l_name or ''}".strip())
    if category:
        parts.append(f"({category})")
    msg = " ".join(parts)

    details: List[str] = []
    if area:
        details.append(f"Γνωστικό αντικείμενο: {area}")
    if office:
        details.append(f"Γραφείο: {office}")
    if email:
        details.append(f"Email: {email}")
    if phone:
        details.append(f"Τηλ: {phone}")
    if page and page != "Δεν υποστηρίζεται":
        details.append(f"Ιστοσελίδα: {page}")

    return safe_join([msg] + details)

class ActionGetProfessorInfo(Action):
    """
    Επιστρέφει πλήρης πληροφορίες καθηγητή.
    - Αν υπάρχει ένα μόνο ταίριασμα: κάρτα + αναλυτικό κείμενο.
    - Αν υπάρχουν πολλά: carousel επιλογών.
    Θέτει επίσης το slot 'email' στο email του πρώτου ταιριάσματος.
    """

    def name(self) -> Text:
        return "action_get_professor_info"

    def run(self, dispatcher, tracker, domain):
        matches = _resolve_prof_from_slot_or_text(tracker)
        if not matches:
            dispatcher.utter_message(text="Δεν βρήκα καθηγητή με αυτό το όνομα.")
            return [SlotSet("professor_name", None)]

        if len(matches) == 1:
            e, f, l, g, office, phone, category, area, page, image = matches[0]

            # 1) Κάρτα με εικόνα (tap ανοίγει URL εφόσον υπάρχει)
            element = _prof_to_fb_element(matches[0])
            dispatcher.utter_message(custom={"facebook": _fb_generic([element])})

            # 2) Αναλυτικές λεπτομέρειες σε κείμενο
            txt = _format_professor_message(f, l, g, category, area, office, e, phone, page)
            dispatcher.utter_message(text=txt)
        else:
            _send_prof_carousel(dispatcher, matches)

        # Θέτουμε slot 'email' για πιθανή μετέπειτα χρήση και καθαρίζουμε 'professor_name'
        return [SlotSet("email", matches[0][0]), SlotSet("professor_name", None)]

class ActionGetProfessorInfoFromEmail(Action):
    """
    Επιστρέφει στοιχεία καθηγητή με βάση email (slot 'email').
    Χρήσιμο όταν το email είναι γνωστό από προηγούμενο βήμα/postback.
    """

    def name(self) -> Text:
        return "action_get_professor_info_from_email"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[EventType]:

        email = tracker.get_slot("email")
        if not email:
            dispatcher.utter_message(text="Δεν έχω email για αναζήτηση.")
            return []

        with db_conn() as con:
            row = con.execute(
                "SELECT email, f_name, l_name, gender, office, phone, "
                "category, area_of, academic_web_page "
                "FROM professors WHERE email = ?",
                (email,),
            ).fetchone()

        if not row:
            dispatcher.utter_message(text="Δεν βρέθηκαν στοιχεία.")
            return []

        (
            email,
            f_name,
            l_name,
            gender,
            office,
            phone,
            category,
            area,
            page,
        ) = row

        msg = _format_professor_message(
            f_name, l_name, gender, category, area, office, email, phone, page
        )
        dispatcher.utter_message(text=msg)
        return []

class ActionGetProfessorEmail(Action):
    """Ανακτά και απαντά μόνο με το email του καθηγητή (ή carousel αν >1)."""

    def name(self) -> Text:
        return "action_get_professor_email"

    def run(self, dispatcher, tracker, domain):
        matches = _resolve_prof_from_slot_or_text(tracker)
        if not matches:
            dispatcher.utter_message(text="Δεν βρήκα καθηγητή με αυτό το όνομα.")
            return [SlotSet("professor_name", None)]
        if len(matches) > 1:
            _send_prof_carousel(dispatcher, matches)
            return [SlotSet("professor_name", None)]
        email, f, l, *_ = matches[0]
        dispatcher.utter_message(text=f"Email {f} {l}: {email or '—'}")
        return [SlotSet("professor_name", None)]

class ActionGetProfessorOffice(Action):
    """Απαντά με το γραφείο του καθηγητή (ή carousel αν είναι πολλαπλά αποτελέσματα)."""

    def name(self) -> Text:
        return "action_get_professor_office"

    def run(self, dispatcher, tracker, domain):
        matches = _resolve_prof_from_slot_or_text(tracker)
        if not matches:
            dispatcher.utter_message(text="Δεν βρήκα καθηγητή με αυτό το όνομα.")
            return [SlotSet("professor_name", None)]
        if len(matches) > 1:
            _send_prof_carousel(dispatcher, matches)
            return [SlotSet("professor_name", None)]
        _email, f, l, _g, office, *_rest = matches[0]
        dispatcher.utter_message(text=f"Γραφείο {f} {l}: {office or '—'}")
        return [SlotSet("professor_name", None)]

class ActionGetProfessorPhone(Action):
    """Απαντά με το τηλέφωνο του καθηγητή (ή carousel αν πολλαπλά αποτελέσματα)."""

    def name(self) -> Text:
        return "action_get_professor_phone"

    def run(self, dispatcher, tracker, domain):
        matches = _resolve_prof_from_slot_or_text(tracker)
        if not matches:
            dispatcher.utter_message(text="Δεν βρήκα καθηγητή με αυτό το όνομα.")
            return [SlotSet("professor_name", None)]
        if len(matches) > 1:
            _send_prof_carousel(dispatcher, matches)
            return [SlotSet("professor_name", None)]
        _email, f, l, _g, _office, phone, *_rest = matches[0]
        dispatcher.utter_message(text=f"Τηλέφωνο {f} {l}: {phone or '—'}")
        return [SlotSet("professor_name", None)]


class ActionGetProfessorWebsite(Action):
    """Απαντά με την ιστοσελίδα του καθηγητή (αν υπάρχει)."""

    def name(self) -> Text:
        return "action_get_professor_website"

    def run(self, dispatcher, tracker, domain):
        matches = _resolve_prof_from_slot_or_text(tracker)
        if not matches:
            dispatcher.utter_message(text="Δεν βρήκα καθηγητή με αυτό το όνομα.")
            return [SlotSet("professor_name", None)]
        if len(matches) > 1:
            _send_prof_carousel(dispatcher, matches)
            return [SlotSet("professor_name", None)]
        row = matches[0]
        email, f, l = row[0], row[1], row[2]
        page = row[8]  # academic_web_page
        if page and page != "Δεν υποστηρίζεται":
            dispatcher.utter_message(text=f"Ιστοσελίδα {f} {l}: {page}")
        else:
            dispatcher.utter_message(text=f"Δεν υπάρχει διαθέσιμη ιστοσελίδα για {f} {l}.")
        return [SlotSet("professor_name", None)]


class ActionListProfessors(Action):
    """Επιστρέφει λίστα καθηγητών ως απλό κείμενο."""

    def name(self) -> Text:
        return "action_list_professors"

    def run(self, dispatcher, tracker, domain):
        with db_conn() as con:
            rows = con.execute(
                "SELECT DISTINCT f_name, l_name, email FROM professors ORDER BY l_name ASC"
            ).fetchall()
        if not rows:
            dispatcher.utter_message(text="Δεν υπάρχουν καταχωρημένοι καθηγητές στη βάση.")
            return []
        names = [f"{(f or '').strip()} {(l or '').strip()}" for f, l, _ in rows][:20]
        extra_hint = "\n…γράψε «στοιχεία για τον <όνομα>» για περισσότερες πληροφορίες." if len(rows) > 20 else ""
        dispatcher.utter_message(text="Καθηγητές:\n• " + "\n• ".join(names) + extra_hint)
        return []


class ActionGetCoursesByProfessor(Action):
    """
    Placeholder δράση: προς το παρόν η βάση δεν έχει αναθέσεις διδασκόντων στα μαθήματα,
    οπότε ενημερώνουμε ρητά τον χρήστη.
    """

    def name(self) -> Text:
        return "action_get_courses_by_professor"

    def run(self, dispatcher, tracker, domain):
        prof_query = tracker.get_slot("professor_name")
        if not prof_query:
            dispatcher.utter_message(text="Για ποιον/ποια καθηγητή;")
            return []
        dispatcher.utter_message(
            text=("Δεν μπορώ να βρω μαθήματα ανά διδάσκοντα ακόμη, "
                  "γιατί λείπουν αναθέσεις διδασκόντων στη βάση. "
                  "Ζήτησε λεπτομέρειες με κωδικό μαθήματος (π.χ. «πες μου για ΜΥ01»).")
        )
        return []


# Υπηρεσίες/Facilities
class ActionGetFacilityWorkingHours(Action):
    """Ανακτά ωράριο λειτουργίας δομής/υπηρεσίας (π.χ. Βιβλιοθήκη)."""

    def name(self) -> Text:
        return "action_get_facility_working_hours"

    def run(self, dispatcher, tracker, domain):
        fac = tracker.get_slot("facility_name")
        if not fac:
            dispatcher.utter_message(text="Για ποια υπηρεσία; (π.χ. Βιβλιοθήκη, Γραμματεία)")
            return []
        with db_conn() as con:
            row = con.execute(
                "SELECT working_hours, url FROM facilities WHERE name LIKE ?",
                ("%"+fac+"%",),
            ).fetchone()
        if not row:
            dispatcher.utter_message(text="Δεν έχω ωράριο για αυτήν την υπηρεσία.")
            return []
        hours, url = row
        txt = hours or "Δεν δίνεται από τον ιστότοπο."
        if url:
            txt += f" (Περισσότερα: {url})"
        dispatcher.utter_message(text=txt)
        return []


class ActionGetFacilityContactInfo(Action):
    """Δίνει email/τηλέφωνο/fax μιας υπηρεσίας, αν υπάρχουν."""

    def name(self) -> Text:
        return "action_get_facility_contact_info"

    def run(self, dispatcher, tracker, domain):
        fac = tracker.get_slot("facility_name") or ""
        with db_conn() as con:
            row = con.execute(
                "SELECT email, phone, fax, url FROM facilities WHERE name LIKE ?",
                ("%"+fac+"%",),
            ).fetchone()
        if not row:
            dispatcher.utter_message(text="Δεν βρήκα στοιχεία επικοινωνίας.")
            return []
        email, phone, fax, url = row
        email = email or "—"
        phone = phone or "—"
        fax   = fax or "—"
        suffix = f" Περισσότερα: {url}" if url else ""
        dispatcher.utter_message(text=f"Email: {email}, Τηλέφωνο: {phone}, Fax: {fax}.{suffix}")
        return []


class ActionGetFacilityLocation(Action):
    """Επιστρέφει τοποθεσία/διεύθυνση υπηρεσίας και link για περισσότερα (αν υπάρχει)."""

    def name(self) -> Text:
        return "action_get_facility_location"

    def run(self, dispatcher, tracker, domain):
        fac = tracker.get_slot("facility_name") or ""
        with db_conn() as con:
            row = con.execute(
                "SELECT location, url FROM facilities WHERE name LIKE ?",
                ("%"+fac+"%",),
            ).fetchone()
        if not row:
            dispatcher.utter_message(text="Δεν βρήκα τοποθεσία.")
            return []
        location, url = row
        txt = location or "Δεν δίνεται από τον ιστότοπο."
        if url:
            txt += f" (Περισσότερα: {url})"
        dispatcher.utter_message(text=txt)
        return []


# Μαθήματα / Courses
class ActionListAllCourses(Action):
    """
    Επιστρέφει λίστα όλων των μαθημάτων.
    """

    def name(self) -> Text:
        return "action_list_all_courses"

    def run(self, dispatcher, tracker, domain):
        with db_conn() as con:
            rows = con.execute(
                "SELECT course_code, course_name FROM courses ORDER BY course_code"
            ).fetchall()
        if not rows:
            dispatcher.utter_message(text="Δεν υπάρχουν μαθήματα στη βάση.")
            return []
        items = [f"{(c or '').upper()} — {n or ''}" for c, n in rows][:40]
        dispatcher.utter_message(text="Μαθήματα :\n• " + "\n".join(items))
        return []


class ActionGetCoursesPerSemester(Action):
    """
    Λίστα μαθημάτων ανά εξάμηνο. Αναγνωρίζει το εξάμηνο είτε από slot 'semester'
    είτε κάνει extract αριθμό από το ελεύθερο κείμενο.
    Κάνει σύγκριση, ακόμη κι αν τα semesters είναι TEXT στη DB.
    """

    def name(self) -> Text:
        return "action_get_courses_per_semester"

    def run(self, dispatcher, tracker, domain):
        sem = tracker.get_slot("semester")
        if not sem:
            # Προσπάθεια εύρεσης αριθμού από το τελευταίο μήνυμα
            raw = (tracker.latest_message.get("text") or "")
            m = re.search(r"(\d{1,2})", raw)
            if m:
                sem = m.group(1)

        try:
            sem_int = int(sem) if sem is not None else None
        except Exception:
            sem_int = None

        if not sem_int:
            dispatcher.utter_message(
                text="Πες μου το εξάμηνο (π.χ. «Μαθήματα 3ου εξαμήνου»)."
            )
            return []

        # Χειρισμός με TRIM/CAST για ανθεκτικότητα σε σχήματα DB
        sem_s = str(sem_int)
        with db_conn() as con:
            rows = con.execute(
                "SELECT course_code, course_name, type, ects_points "
                "FROM courses "
                "WHERE TRIM(CAST(semester_1 AS TEXT)) = ? "
                "   OR TRIM(CAST(semester_2 AS TEXT)) = ? "
                "ORDER BY course_code",
                (sem_s, sem_s),
            ).fetchall()
        if not rows:
            dispatcher.utter_message(
                text=(f"Δεν βρήκα μαθήματα για {sem_int}ο εξαμήνο. "
                      "Ίσως δεν υπάρχουν δεδομένα εξαμήνου στη βάση.")
            )
            return []
        items = [
            f"{(code or '').upper()}: {name or ''} ({typ or '–'}, {ects or '?'} ECTS)"
            for code, name, typ, ects in rows
        ]
        msg = f"Μαθήματα {sem_int}ου εξαμήνου:\n• " + "\n".join(items[:20])
        dispatcher.utter_message(text=msg)
        return []


class ActionGetCourseDetails(Action):
    """
    Δίνει λεπτομέρειες μαθήματος (όνομα, ECTS, τύπος, εξάμηνα, διδάσκοντες, URL).
    Ο κωδικός αναζητείται από slot 'course_code' ή εξάγεται από το κείμενο.
    """

    def name(self) -> Text:
        return "action_get_course_details"

    def run(self, dispatcher, tracker, domain):
        # Από slot ή από εξαγωγή κειμένου
        code = normalize_code(tracker.get_slot("course_code") or "") or \
               _extract_course_code(tracker.latest_message.get("text") or "")
        if not code:
            dispatcher.utter_message(text="Δώσε κωδικό μαθήματος (π.χ. ΜΥ01).")
            return []
        with db_conn() as con:
            row = con.execute(
                "SELECT course_name, ects_points, type, professor_1, "
                "professor_2, semester_1, semester_2, url "
                "FROM courses WHERE lower(course_code)=?",
                (code,),
            ).fetchone()
            if not row:
                # fallback
                row = con.execute(
                    "SELECT course_name, ects_points, type, professor_1, professor_2, semester_1, semester_2, url "
                    "FROM courses WHERE lower(course_code) LIKE ? ORDER BY course_code LIMIT 1",
                    (code + "%",),
                ).fetchone()
        if not row:
            dispatcher.utter_message(text=f"Δεν βρήκα το μάθημα με κωδικό {code.upper()}.")
            return []
        name, ects, ctype, p1, p2, s1, s2, url = row
        profs = ", ".join([p for p in [p1, p2] if p and p != "Δεν υποστηρίζεται"]) or "–"
        sems = ", ".join([str(s).strip() for s in [s1, s2] if s]) or "–"
        msg = (
            f"{code.upper()} – {name or ''}. ΕCTS: {ects or '?'}. "
            f"Τύπος: {ctype or '–'}. Εξάμηνο: {sems}. Διδάσκοντες: {profs}."
        )
        if url and url != "Δεν υποστηρίζεται":
            msg += f" Σελίδα μαθήματος: {url}"
        dispatcher.utter_message(text=msg)
        return []


class ActionGetCourseInfo(Action):
    """
    Γρήγορος έλεγχος ύπαρξης κωδικού στη σελίδα προπτυχιακών
    Δεν δίνει πλήρη στοιχεία, μόνο ειδοποιεί αν εντοπίστηκε το string στη σελίδα.
    """

    def name(self) -> Text:
        return "action_get_course_info"

    async def run(self, dispatcher, tracker, domain) -> List[EventType]:
        # Από slot ή από εξαγωγή ελεύθερου κειμένου
        code = normalize_code(tracker.get_slot("course_code") or "") or \
               _extract_course_code(tracker.latest_message.get("text") or "")
        if not code:
            dispatcher.utter_message(text="Δώσε κωδικό μαθήματος (π.χ. ΜΥ01).")
            return []
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(DIT_UNDERGRAD_URL)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "lxml")
                # Αναζήτηση σε κείμενο (case-insensitive)
                found = soup.find(
                    string=lambda s: s and (code.upper() in s or code.lower() in s)
                )
                if found:
                    dispatcher.utter_message(
                        text=(f"Βρήκα αναφορά του {code.upper()} στη σελίδα προπτυχιακών: {DIT_UNDERGRAD_URL}")
                    )
                else:
                    dispatcher.utter_message(
                        text=("Δεν εντόπισα πληροφορίες από τη σελίδα. "
                              "Δοκίμασε αναζήτηση στη βάση.")
                    )
        except Exception:
            # Αντικατάσταση συγκεκριμένων σφαλμάτων με γενικό μήνυμα για τον χρήστη
            dispatcher.utter_message(
                text=("Δεν μπόρεσα να ελέγξω τη σελίδα αυτή τη στιγμή. "
                      "Δοκίμασε ξανά ή ρώτα για άλλο μάθημα.")
            )
        return []


# Φοιτητικές Υπηρεσίες / Student Services
class ActionListStudentServices(Action):
    """Λίστα με ονόματα φοιτητικών υπηρεσιών από τη βάση."""

    def name(self) -> Text:
        return "action_list_student_services"

    def run(self, dispatcher, tracker, domain):
        with db_conn() as con:
            rows = con.execute(
                "SELECT name FROM student_services ORDER BY name"
            ).fetchall()
        if not rows:
            dispatcher.utter_message(text="Δεν υπάρχουν υπηρεσίες στη βάση.")
            return []
        names = [r[0] for r in rows][:30]
        dispatcher.utter_message(text="Υπηρεσίες:\n• " + "\n".join(names))
        return []


class ActionGetStudentService(Action):
    """Επιστρέφει πληροφορίες συγκεκριμένης φοιτητικής υπηρεσίας (LIKE)."""

    def name(self) -> Text:
        return "action_get_student_service"

    def run(self, dispatcher, tracker, domain):
        q = tracker.get_slot("service_name") or ""
        with db_conn() as con:
            row = con.execute(
                "SELECT name, description, email, phone, url "
                "FROM student_services WHERE name LIKE ?",
                ("%"+q+"%",),
            ).fetchone()
        if not row:
            dispatcher.utter_message(text="Δεν βρήκα την υπηρεσία.")
            return []
        name, desc, email, phone, url = row
        lines = [
            name or "",
            (desc or "").strip(),
            f"Email: {email or '—'}  Τηλ: {phone or '—'}",
            f"Περισσότερα: {url}" if url else "",
        ]
        dispatcher.utter_message(text="\n".join([l for l in lines if l]))
        return []


# Ηλεκτρονικές Πλατφόρμες / E-Platforms
class ActionListEPlatforms(Action):
    """Λίστα ηλεκτρονικών πλατφορμών με τους βασικούς συνδέσμους."""

    def name(self) -> Text:
        return "action_list_eplatforms"

    def run(self, dispatcher, tracker, domain):
        with db_conn() as con:
            rows = con.execute(
                "SELECT name, url FROM e_platforms ORDER BY name"
            ).fetchall()
        if not rows:
            dispatcher.utter_message(text="Δεν υπάρχουν πλατφόρμες.")
            return []
        items = [f"{n} — {u or '—'}" for n, u in rows][:30]
        dispatcher.utter_message(text="Ηλεκτρονικές πλατφόρμες:\n• " + "\n".join(items))
        return []


class ActionGetEPlatform(Action):
    """Επιστρέφει περιγραφή/συνδέσμους για συγκεκριμένη πλατφόρμα (LIKE)."""

    def name(self) -> Text:
        return "action_get_eplatform"

    def run(self, dispatcher, tracker, domain):
        q = tracker.get_slot("platform_name") or ""
        with db_conn() as con:
            row = con.execute(
                "SELECT name, description, url, help_url "
                "FROM e_platforms WHERE name LIKE ?",
                ("%"+q+"%",),
            ).fetchone()
        if not row:
            dispatcher.utter_message(text="Δεν βρήκα πλατφόρμα με αυτό το όνομα.")
            return []
        name, desc, url, help_url = row
        lines = [
            name or "",
            (desc or "").strip(),
            f"Σύνδεσμος: {url or '—'}",
            f"Οδηγός: {help_url}" if help_url else "",
        ]
        dispatcher.utter_message(text="\n".join([l for l in lines if l]))
        return []


# Επικοινωνία Τμήματος
class ActionGetDepartmentContacts(Action):
    """Επιστρέφει λίστα βασικών στοιχείων επικοινωνίας (labels + τιμές + URLs)."""

    def name(self) -> Text:
        return "action_get_department_contacts"

    def run(self, dispatcher, tracker, domain):
        with db_conn() as con:
            rows = con.execute(
                "SELECT key, label, value, url FROM contacts ORDER BY key"
            ).fetchall()
        if not rows:
            dispatcher.utter_message(text="Δεν υπάρχουν στοιχεία επικοινωνίας.")
            return []
        parts = []
        for key, label, value, url in rows:
            line = f"{(label or key)}: {(value or '—')}"
            if url:
                line += f" ({url})"
            parts.append("• " + line)
        dispatcher.utter_message(text="\n".join(parts))
        return []


# Tutorials (στατικό carousel)
class ActionTutorialsList(Action):
    """
    Επιστρέφει ένα στατικό carousel με βασικές πλατφόρμες/συνδέσμους.
    """

    def name(self) -> Text:
        return "action_tutorials_list"

    def run(self, dispatcher, tracker, domain):
        elements = [
            carousel_element(
                "e-Class",
                "Οδηγός & είσοδος",
                buttons=[{"type": "web_url", "title": "Άνοιγμα", "url": "https://eclass.hua.gr"}],
            ),
            carousel_element(
                "e-Studies",
                "Φοιτητολόγιο",
                buttons=[{"type": "web_url", "title": "Άνοιγμα", "url": "https://e-studies.hua.gr"}],
            ),
            carousel_element(
                "Nextcloud",
                "Cloud & συνεργασία",
                buttons=[{"type": "web_url", "title": "Άνοιγμα", "url": "https://mycloud.ditapps.hua.gr"}],
            ),
            carousel_element(
                "Rocket.Chat",
                "Επικοινωνία",
                buttons=[{"type": "web_url", "title": "Άνοιγμα", "url": "https://chat.ditapps.hua.gr"}],
            ),
        ]

        # Facebook custom payload
        dispatcher.utter_message(custom={"facebook": {"type": "carousel", "elements": elements}})

        # Σύντομο βοηθητικό μήνυμα
        dispatcher.utter_message(text="Χρήσιμες πλατφόρμες: e-Class, e-Studies, Nextcloud, Rocket.Chat")
        return []


# Erasmus
class ActionGetErasmusApplicationInfo(Action):
    """
    Προσπαθεί να βρει email/τηλ. γραφείου Erasmus από τον πίνακα facilities.
    Αν δεν υπάρχει σχετική εγγραφή, ενημερώνει τον χρήστη.
    """

    def name(self) -> Text:
        return "action_get_erasmus_application_info"

    def run(self, dispatcher, tracker, domain):
        with db_conn() as con:
            row = con.execute(
                "SELECT email, phone FROM facilities WHERE name LIKE '%Erasmus%'"
            ).fetchone()
        if row:
            dispatcher.utter_message(text=f"Erasmus: Email {row[0] or '—'}, Τηλ {row[1] or '—'}")
        else:
            dispatcher.utter_message(text="Δεν βρήκα γραφείο Erasmus.")
        return []


# Βαθμολόγηση / Feedback
class RatingForm(Action):
    """
    Δράση λήψης αξιολόγησης (excellent/mediocre/bad) και καταγραφής στη DB (ratings).
    Μετά την καταγραφή, καθαρίζει το slot 'rating'.
    """

    def name(self) -> Text:
        return "rating_form"

    def run(self, dispatcher, tracker, domain) -> List[EventType]:
        rating = tracker.get_slot("rating")
        if rating not in {"excellent", "mediocre", "bad"}:
            dispatcher.utter_message(text="Παρακαλώ επίλεξε μια από τις διαθέσιμες αξιολογήσεις.")
            return []
        with db_conn() as con:
            con.execute(
                "INSERT INTO ratings(timestamp, user_id, rating) VALUES(?,?,?)",
                (
                    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    tracker.sender_id,
                    rating,
                ),
            )
            con.commit()
        dispatcher.utter_message(text="Ευχαριστώ για την αξιολόγηση! 🙏")
        return [SlotSet("rating", None)]
