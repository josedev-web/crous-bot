import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Liste des recherches à surveiller. Ajoute-en autant que tu veux.
URLS = [
    # Nice
    "https://trouverunlogement.lescrous.fr/tools/47/search?bounds=7.1819535_43.7607635_7.323912_43.6454189&locationName=Nice",
    # Valbonne (Sophia Antipolis)
    "https://trouverunlogement.lescrous.fr/tools/47/search?bounds=6.9879485_43.6521525_7.0702486_43.6013488&locationName=Valbonne+%2806560%29",
]

BASE = "https://trouverunlogement.lescrous.fr"
FICHIER = "seen.json"
MAX_PAGES = 10
NB_VERIFICATIONS = 14    # nombre de vérifications par exécution
PAUSE = 60              # secondes entre deux vérifications
RAPPEL_MINUTES = 60     # rappel des logements disponibles (0 = désactivé)


def envoyer(message):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message},
        timeout=15,
    )


def formater(titre, details, lien, entete):
    message = f"{entete}\n{titre}\n"
    if details:
        message += f"{details}\n"
    message += f"\n👉 {lien}"
    return message


def logements_page(url_base, page):
    sep = "&" if "?" in url_base else "?"
    url = url_base if page == 1 else f"{url_base}{sep}page={page}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    r.raise_for_status()
    r.encoding = "utf-8"  # le site n'indique pas son encodage

    soup = BeautifulSoup(r.text, "html.parser")

    # Protection : si la page n'est pas une vraie page de résultats, on s'arrête
    titre_page = soup.title.get_text(strip=True) if soup.title else ""
    if "résultats de recherche" not in titre_page:
        raise RuntimeError(
            f"Page inattendue (code {r.status_code}). Titre reçu : {titre_page!r}"
        )

    trouves = {}
    for a in soup.find_all("a", href=re.compile(r"/accommodations/(\d+)")):
        id_ = re.search(r"/accommodations/(\d+)", a["href"]).group(1)
        carte = a.find_parent("li")
        texte = carte.get_text(" ", strip=True) if carte else a.get_text(strip=True)

        # Filtre : code postal commençant par 06
        if not re.search(r"\b06\d{3}\b", texte):
            continue

        titre = a.get_text(strip=True)
        lien = a["href"] if a["href"].startswith("http") else BASE + a["href"]

        prix = re.search(r"(?:de\s+)?[\d.,]+(?:\s*à\s*[\d.,]+)?\s*€", texte)
        surface = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", texte)
        adresse = re.search(
            r"(\d+[^-]*?-\s*06\d{3}\s+[A-ZÀ-Ý'\- ]+?)(?=\s+\d+(?:[.,]\d+)?\s*m²)",
            texte,
        )
        type_ = re.search(r"(Individuel(?:,\s*Colocation)?|Colocation|Couple)", texte)

        details = []
        if adresse:
            details.append(f"📍 {adresse.group(1).strip()}")
        ligne = []
        if surface:
            ligne.append(f"📐 {surface.group(0)}")
        if type_:
            ligne.append(f"🛏 {type_.group(1)}")
        if prix:
            ligne.append(f"💶 {prix.group(0)}")
        if ligne:
            details.append(" · ".join(ligne))

        trouves[id_] = (titre, "\n".join(details), lien)
    return trouves


def logements_recherche(url_base):
    resultat = {}
    for page in range(1, MAX_PAGES + 1):
        trouves = logements_page(url_base, page)
        if not set(trouves) - set(resultat):  # plus rien de nouveau : on s'arrête
            break
        resultat.update(trouves)
    return resultat


def tous_les_logements():
    total = {}
    for url in URLS:
        total.update(logements_recherche(url))
    return total


def charger_etat():
    """Renvoie (ids déjà vus ou None au premier lancement, heure du dernier rappel)."""
    if os.path.exists(FICHIER):
        with open(FICHIER) as f:
            data = json.load(f)
        if isinstance(data, list):  # ancien format
            return set(data), 0
        return set(data.get("ids", [])), data.get("dernier_rappel", 0)
    return None, 0


def sauvegarder_etat(ids, dernier_rappel):
    with open(FICHIER, "w") as f:
        json.dump({"ids": sorted(ids), "dernier_rappel": dernier_rappel}, f)


deja_vus, dernier_rappel = charger_etat()

for i in range(NB_VERIFICATIONS):
    try:
        actuels = tous_les_logements()
        print(f"Vérification {i + 1}/{NB_VERIFICATIONS} : {len(actuels)} logement(s) trouvé(s)")
        maintenant = time.time()

        if deja_vus is None:
            # Premier lancement : message d'activation + détail de chaque logement
            envoyer(
                f"Surveillance CROUS activée ✅ "
                f"({len(actuels)} logements dans le secteur 06 actuellement)"
            )
            for id_, (titre, details, lien) in actuels.items():
                envoyer(formater(titre, details, lien, "🏠 Logement actuellement disponible"))
            dernier_rappel = maintenant
        else:
            # Alerte pour les nouveaux logements
            for id_, (titre, details, lien) in actuels.items():
                if id_ not in deja_vus:
                    envoyer(formater(titre, details, lien, "🆕 Nouveau logement CROUS !"))

            # Rappel périodique pour les logements toujours disponibles
            if (
                RAPPEL_MINUTES > 0
                and actuels
                and maintenant - dernier_rappel >= RAPPEL_MINUTES * 60
            ):
                for id_, (titre, details, lien) in actuels.items():
                    if id_ in deja_vus:  # les nouveaux viennent d'être signalés
                        envoyer(formater(
                            titre, details, lien,
                            "⏰ Rappel : logement toujours disponible",
                        ))
                dernier_rappel = maintenant

        deja_vus = set(actuels.keys())
        sauvegarder_etat(deja_vus, dernier_rappel)
    except Exception as e:
        print("Erreur :", e)

    if i < NB_VERIFICATIONS - 1:
        time.sleep(PAUSE)
