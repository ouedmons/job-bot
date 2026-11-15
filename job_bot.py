"""Bot d'alertes stage / alternance (efficacité énergétique, décarbonation du bâtiment).

Sources :
  - France Travail (API Offres d'emploi v2) -> contrats d'apprentissage / professionnalisation
  - Jooble (API gratuite) -> agrégateur, utile pour les stages

Envoi : webhook Discord. Dédoublonnage via seen.json.
Variables d'environnement : DISCORD_WEBHOOK, FT_CLIENT_ID, FT_CLIENT_SECRET, JOOBLE_KEY
(chaque source est ignorée si ses clés sont absentes).
"""
import json
import os
import time
from pathlib import Path

import requests

SEEN_FILE = Path(__file__).parent / "seen.json"

# Mots-clés tirés du CV
KEYWORDS = [
    "efficacité énergétique",
    "audit énergétique",
    "décarbonation bâtiment",
    "ingénieur énergie",
    "thermique bâtiment",
    "GTB",
    "maintenance électromécanique",
    "éco-énergétique",
]
# Départements : Paris + petite/grande couronne
DEPARTEMENTS = "75,91,92,78,94,93,95,77"
# Filtre sur le titre pour limiter le bruit
TITLE_MUST_CONTAIN = ["stage", "stagiaire", "alternance", "alternant", "apprenti", "apprentissage"]
TITLE_EXCLUDE = ["cuisine", "restauration", "vendeur", "commercial"]


def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


def title_ok(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in TITLE_MUST_CONTAIN) and not any(k in t for k in TITLE_EXCLUDE)


# ---------- France Travail ----------
def france_travail():
    cid, secret = os.getenv("FT_CLIENT_ID"), os.getenv("FT_CLIENT_SECRET")
    if not (cid and secret):
        return []
    tok = requests.post(
        "https://entreprise.francetravail.fr/connexion/oauth2/access_token",
        params={"realm": "/partenaire"},
        data={
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": secret,
            "scope": "api_offresdemploiv2 o2dsoffre",
        },
        timeout=30,
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}
    offers = []
    for kw in KEYWORDS:
        r = requests.get(
            "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search",
            headers=headers,
            params={
                "motsCles": kw,
                "departement": DEPARTEMENTS,
                "natureContrat": "E2,FS",  # apprentissage + professionnalisation (à ajuster)
                "range": "0-29",
                "sort": 1,
            },
            timeout=30,
        )
        if r.status_code not in (200, 206):
            continue
        for o in r.json().get("resultats", []):
            offers.append(
                {
                    "id": f"ft-{o['id']}",
                    "title": o.get("intitule", ""),
                    "company": (o.get("entreprise") or {}).get("nom", "N/C"),
                    "place": (o.get("lieuTravail") or {}).get("libelle", ""),
                    "url": (o.get("origineOffre") or {}).get("urlOrigine")
                    or f"https://candidat.francetravail.fr/offres/recherche/detail/{o['id']}",
                    "source": "France Travail",
                }
            )
        time.sleep(0.5)
    return offers


# ---------- Jooble ----------
def jooble():
    key = os.getenv("JOOBLE_KEY")
    if not key:
        return []
    offers = []
    for kw in KEYWORDS:
        for suffix in ("stage", "alternance"):
            r = requests.post(
                f"https://jooble.org/api/{key}",
                json={"keywords": f"{suffix} {kw}", "location": "Île-de-France", "page": 1},
                timeout=30,
            )
            if r.status_code != 200:
                continue
            for j in r.json().get("jobs", []):
                offers.append(
                    {
                        "id": f"jb-{j['id']}",
                        "title": j.get("title", ""),
                        "company": j.get("company", "N/C"),
                        "place": j.get("location", ""),
                        "url": j.get("link", ""),
                        "source": "Jooble",
                    }
                )
            time.sleep(0.5)
    return offers


# ---------- Discord ----------
def send_discord(o):
    webhook = os.environ["DISCORD_WEBHOOK"]
    content = (
        f"**{o['title']}**\n"
        f"🏢 {o['company']} — 📍 {o['place']}\n"
        f"🔗 {o['url']}\n"
        f"_{o['source']}_"
    )
    requests.post(webhook, json={"content": content}, timeout=30)
    time.sleep(1)  # évite le rate limit


def main():
    seen = load_seen()
    new = []
    for o in france_travail() + jooble():
        if o["id"] in seen or not title_ok(o["title"]):
            continue
        seen.add(o["id"])
        new.append(o)
    # Dédoublonnage sur titre+entreprise (même offre vue sur 2 sources)
    uniq = {(o["title"].lower(), o["company"].lower()): o for o in new}
    for o in uniq.values():
        send_discord(o)
    save_seen(seen)
    print(f"{len(uniq)} nouvelles offres envoyées")


if __name__ == "__main__":
    main()
