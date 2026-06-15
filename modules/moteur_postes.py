"""
moteur_postes.py — Refonte des étapes 3 & 4 (consolidation + séquençage)
=========================================================================

Remplace `tunnel_consolidation_flux` + `sequencage_engine`/`sim_engine`.

Philosophie
-----------
Les données du CHU sont en étoile (hub-and-spoke) autour de HSJ :
presque tout flux part ou arrive du dépôt, et 48 paires Origine→Destination
sur 50 possèdent leur flux inverse (ex : HSJ→HGRL "propre" et HGRL→HSJ "sale").

Le gisement d'optimisation principal n'est donc PAS le remplissage de camion,
mais le CHAÎNAGE : après une mission chargée A→B, faire repartir le véhicule
sur une mission chargée B→A (ou B→C) au lieu d'un retour à vide. Chaque
retour à vide consomme de l'amplitude sans produire → multiplie les postes.

Pipeline (lisible, déterministe, sans OR-Tools)
-----------------------------------------------
  1. Affectation véhicule + capacité utile      (réutilise la logique existante)
  2. Fragmentation                              → N missions pleines + 1 reliquat
  3. Consolidation des reliquats                → tournées multi-arrêts (option)
  4. Chaînage + construction des postes         ← cœur de la refonte
  5. Comptage flotte (postes simultanés)        → nb de véhicules physiques

Sortie : liste de `Poste` (un poste = une vacation chauffeur), chacun
contenant une séquence ordonnée de missions chaînées, prête pour l'affichage.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from datetime import time, datetime, timedelta


# =====================================================================
# 0. HELPERS — temps, matrice, capacité
# =====================================================================

def to_min(val, defaut=0.0):
    """Convertit time / 'HH:MM[:SS]' / fraction Excel / minutes -> minutes."""
    if val is None:
        return defaut
    if isinstance(val, float) and math.isnan(val):
        return defaut
    if isinstance(val, (time, datetime)):
        return val.hour * 60 + val.minute + val.second / 60
    if isinstance(val, timedelta):
        return val.total_seconds() / 60
    if isinstance(val, str):
        s = val.strip()
        if s == "" or s.upper() == "NC":
            return defaut
        if ":" in s:
            p = s.split(":")
            return int(p[0]) * 60 + int(p[1]) + (int(p[2]) / 60 if len(p) > 2 else 0)
        try:
            return float(s)
        except ValueError:
            return defaut
    try:
        f = float(val)
        # fraction de journée Excel (0.25 -> 360 min) si < 2, sinon déjà des minutes
        return f * 1440 if 0 < f < 2 else f
    except (ValueError, TypeError):
        return defaut


def nettoyer_matrice(matrice_duree):
    """Normalise la matrice de durée en dict {ORIG: {DEST: minutes}}."""
    df = matrice_duree.copy()
    col0 = df.columns[0]
    df = df.set_index(col0)
    df.index = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()
    return {o: {d: float(v) for d, v in row.items()} for o, row in df.to_dict("index").items()}


def duree_trajet(matrice, a, b):
    a, b = str(a).strip().upper(), str(b).strip().upper()
    if a == b:
        return 0.0
    return matrice.get(a, {}).get(b, 30.0)  # 30 min de repli si paire absente


def capacite_max(vehicule, contenant):
    """Bin-packing guillotine 2D + limite poids (identique au moteur existant)."""
    v = {str(k).strip(): val for k, val in vehicule.items()}
    c = {str(k).strip(): val for k, val in contenant.items()}
    nom = c.get("libellé")
    if not nom or v.get(nom) != "OUI":
        return 0
    try:
        L_v = float(v["dim longueur interne (m)"]); l_v = float(v["dim largeur interne (m)"])
        P = float(v["Poids max chargement"])
        L_c = float(c["dim longueur (m)"]); l_c = float(c["dim largeur (m)"]); p = float(c["Poids plein (T)"])
    except (KeyError, ValueError, TypeError):
        return 0
    memo = {}
    def solve(L, l, w, h):
        if (L < w and L < h) or (l < w and l < h):
            return 0
        s = (round(L, 3), round(l, 3))
        if s in memo:
            return memo[s]
        r = 0
        if L >= w and l >= h:
            r = max(r, 1 + solve(L - w, l, w, h) + solve(w, l - h, w, h),
                       1 + solve(L, l - h, w, h) + solve(L - w, h, w, h))
        if L >= h and l >= w:
            r = max(r, 1 + solve(L - h, l, w, h) + solve(h, l - w, w, h),
                       1 + solve(L, l - w, w, h) + solve(L - h, w, h, h))
        memo[s] = r
        return r
    nb = solve(L_v, l_v, L_c, l_c)
    if p > 0:
        nb = min(nb, int(P // p))
    return int(nb)


# =====================================================================
# 1. STRUCTURES DE DONNÉES
# =====================================================================

@dataclass
class Mission:
    """
    Une mission = un trajet CHARGÉ exécutable par un véhicule.
    Mono-OD (groupage simple) ou multi-arrêts (distribution / ramassage).
    Les temps sont en minutes depuis minuit.
    """
    id: str
    v_type: str
    propre_sale: str
    site_debut: str            # premier site visité (1er chargement)
    site_fin: str              # dernier site visité (dernier déchargement)
    h_dispo: float             # au plus tôt : démarrage du 1er chargement
    h_deadline: float          # au plus tard : fin du dernier déchargement
    duree: float               # durée opérationnelle (manut + trajets internes)
    etapes: list = field(default_factory=list)   # chronologie relative pour l'affichage
    nb_contenants: int = 0
    libelle: str = ""
    fenetre_tendue: bool = False   # fenêtre relâchée car incohérente / trop courte

    def __repr__(self):
        return (f"Mission({self.id} | {self.v_type} | {self.site_debut}->{self.site_fin} "
                f"| {self.duree:.0f}min | {self.propre_sale})")


@dataclass
class Etape:
    """Une étape de la vie d'un poste (chargée, à vide, attente, pause, admin)."""
    type: str                  # APPROCHE_VIDE | MISSION | ATTENTE | PAUSE | PRISE | FIN | RETOUR_VIDE | NETTOYAGE
    h_debut: float
    h_fin: float
    detail: str = ""
    mission: Mission = None

    @property
    def duree(self):
        return self.h_fin - self.h_debut


@dataclass
class Poste:
    """Une vacation chauffeur : suite de missions chaînées sur un véhicule."""
    id: str
    v_type: str
    depot: str
    h_debut: float = 0.0       # prise de poste (incl. préparation)
    h_fin: float = 0.0         # fin de poste (incl. clôture)
    position: str = ""         # site courant pendant la construction
    t_curr: float = 0.0        # horloge courante pendant la construction
    pause_faite: bool = False
    missions: list = field(default_factory=list)
    etapes: list = field(default_factory=list)
    id_vehicule: str = ""      # affecté à l'étape de comptage flotte
    shift: int = 0             # index du créneau (0 = matin, 1 = après-midi, ...)

    # --- métriques calculées ---
    @property
    def amplitude(self):
        return self.h_fin - self.h_debut

    def temps_charge(self):
        return sum(e.duree for e in self.etapes if e.type == "MISSION")

    def temps_vide(self):
        return sum(e.duree for e in self.etapes if e.type in ("APPROCHE_VIDE", "RETOUR_VIDE", "NETTOYAGE"))

    def temps_attente(self):
        return sum(e.duree for e in self.etapes if e.type in ("ATTENTE", "DISPONIBLE"))

    def taux_charge_roulage(self):
        roul = self.temps_charge() + self.temps_vide()
        return self.temps_charge() / roul if roul > 0 else 0.0


# =====================================================================
# 2. AFFECTATION VÉHICULE + FRAGMENTATION  (étapes 1-2, réutilisées)
# =====================================================================

def _accessible(df_sites, col_lib, v_type, site):
    try:
        val = df_sites.loc[df_sites[col_lib] == str(site).strip().upper(), str(v_type).strip().upper()].values[0]
        return str(val).strip().upper() == "OUI"
    except Exception:
        return False


def affecter_vehicule(site_dep, site_arr, type_cont, df_v_actifs, df_contenants, df_sites, col_lib):
    """Choisit le véhicule le plus capacitif compatible (accès + contenant)."""
    try:
        cont = df_contenants[df_contenants["libellé"].astype(str).str.strip().str.upper()
                             == str(type_cont).strip().upper()].iloc[0]
    except Exception:
        return None, 0
    best_v, best_cap = None, 0
    for _, v in df_v_actifs.iterrows():
        vt = str(v["Types"]).strip().upper()
        if _accessible(df_sites, col_lib, vt, site_dep) and _accessible(df_sites, col_lib, vt, site_arr):
            cap = capacite_max(v, cont)
            if cap > best_cap:
                best_cap, best_v = cap, v
    return best_v, best_cap


# =====================================================================
# 3. CALCUL DE LA DURÉE OPÉRATIONNELLE D'UNE MISSION + CHRONOLOGIE
# =====================================================================

def _params_manut(df_vehicules, v_type):
    row = df_vehicules[df_vehicules["Types"].astype(str).str.strip().str.upper()
                       == str(v_type).strip().upper()]
    if row.empty:
        return 3.0, 25 / 60, 15 / 60
    r = row.iloc[0]
    t_quai = to_min(r.get("Temps de mise à quai - manœuvre, contact/admin (minutes)"), 3.0)
    t_sans = to_min(r.get("Manutention sans quai (minutes / contenants)"), 25 / 60)
    t_avec = to_min(r.get("Manutention avec quai (minutes / contenants)"), 15 / 60)
    return t_quai, t_sans, t_avec


def _a_quai(df_sites, col_quai, col_lib, site):
    row = df_sites.loc[df_sites[col_lib] == str(site).strip().upper(), col_quai]
    return (not row.empty) and str(row.values[0]).strip().upper() == "OUI"


def construire_mission_mono(mid, v_type, propre_sale, orig, dest, qte, libelle,
                            h_dispo, h_deadline, matrice, df_vehicules, df_sites,
                            col_quai, col_lib):
    """Mission mono-OD : chargement en O, trajet, déchargement en D."""
    t_quai, t_sans, t_avec = _params_manut(df_vehicules, v_type)
    tc_o = t_avec if _a_quai(df_sites, col_quai, col_lib, orig) else t_sans
    tc_d = t_avec if _a_quai(df_sites, col_quai, col_lib, dest) else t_sans
    trajet = duree_trajet(matrice, orig, dest)

    etapes, t = [], 0.0
    def add(site, action, d, label):
        nonlocal t
        etapes.append({"site": site, "action": action, "t_debut": round(t, 1),
                       "t_fin": round(t + d, 1), "label": label})
        t += d
    add(orig, "MISE_A_QUAI", t_quai, f"Mise à quai @ {orig}")
    add(orig, "CHARGEMENT", qte * tc_o, f"Chargement {qte} {libelle}")
    add(dest, "TRAJET", trajet, f"Trajet {orig} → {dest}")
    add(dest, "MISE_A_QUAI", t_quai, f"Mise à quai @ {dest}")
    add(dest, "DECHARGEMENT", qte * tc_d, f"Déchargement {qte} {libelle}")

    return Mission(id=mid, v_type=v_type, propre_sale=propre_sale,
                   site_debut=orig, site_fin=dest, h_dispo=h_dispo,
                   h_deadline=h_deadline, duree=round(t, 1), etapes=etapes,
                   nb_contenants=qte, libelle=libelle)


def construire_mission_tournee(mid, v_type, propre_sale, origines, livraisons,
                               libelle, h_dispo, h_deadline, matrice,
                               df_vehicules, df_sites, col_quai, col_lib, sens):
    """
    Mission multi-arrêts.
      sens='DISTRIB'  : 1 origine, chargement unique, livraisons successives.
      sens='RAMASSE'  : collectes successives, 1 destination, déchargement unique.
    livraisons : liste de (site, qte). origines : liste de (site, qte).
    """
    t_quai, t_sans, t_avec = _params_manut(df_vehicules, v_type)
    etapes, t = [], 0.0
    def add(site, action, d, label):
        nonlocal t
        etapes.append({"site": site, "action": action, "t_debut": round(t, 1),
                       "t_fin": round(t + d, 1), "label": label})
        t += d

    if sens == "DISTRIB":
        orig = origines[0][0]
        nb_tot = sum(q for _, q in livraisons)
        tc_o = t_avec if _a_quai(df_sites, col_quai, col_lib, orig) else t_sans
        add(orig, "MISE_A_QUAI", t_quai, f"Mise à quai @ {orig}")
        add(orig, "CHARGEMENT", nb_tot * tc_o, f"Chargement {nb_tot} {libelle}")
        pos = orig
        for site, q in livraisons:
            tc_d = t_avec if _a_quai(df_sites, col_quai, col_lib, site) else t_sans
            add(site, "TRAJET", duree_trajet(matrice, pos, site), f"Trajet {pos} → {site}")
            add(site, "MISE_A_QUAI", t_quai, f"Mise à quai @ {site}")
            add(site, "DECHARGEMENT", q * tc_d, f"Déchargement {q} {libelle} @ {site}")
            pos = site
        site_debut, site_fin, nb = orig, livraisons[-1][0], nb_tot
    else:  # RAMASSE
        dest = livraisons[0][0]
        nb_tot = sum(q for _, q in origines)
        pos = origines[0][0]
        for site, q in origines:
            tc_o = t_avec if _a_quai(df_sites, col_quai, col_lib, site) else t_sans
            if site != pos:
                add(site, "TRAJET", duree_trajet(matrice, pos, site), f"Trajet {pos} → {site}")
            add(site, "MISE_A_QUAI", t_quai, f"Mise à quai @ {site}")
            add(site, "CHARGEMENT", q * tc_o, f"Chargement {q} {libelle} @ {site}")
            pos = site
        tc_d = t_avec if _a_quai(df_sites, col_quai, col_lib, dest) else t_sans
        add(dest, "TRAJET", duree_trajet(matrice, pos, dest), f"Trajet {pos} → {dest}")
        add(dest, "MISE_A_QUAI", t_quai, f"Mise à quai @ {dest}")
        add(dest, "DECHARGEMENT", nb_tot * tc_d, f"Déchargement {nb_tot} {libelle}")
        site_debut, site_fin, nb = origines[0][0], dest, nb_tot

    return Mission(id=mid, v_type=v_type, propre_sale=propre_sale,
                   site_debut=site_debut, site_fin=site_fin, h_dispo=h_dispo,
                   h_deadline=h_deadline, duree=round(t, 1), etapes=etapes,
                   nb_contenants=nb, libelle=libelle)


# =====================================================================
# 4. CONSOLIDATION : flux du jour -> liste de Missions
# =====================================================================

def consolider_missions(df_jour, df_vehicules, df_contenants, df_sites,
                        matrice, params, col_lib, col_quai, df_v_actifs,
                        autoriser_tournees=True):
    """
    Transforme les flux du jour en missions exécutables.
      - chaque flux : N missions pleines (capa utile) + 1 reliquat
      - reliquats regroupés par (origine | destination) en tournées multi-arrêts
    """
    taux = params.get("securite_remplissage", 0.85)
    missions, reliquats = [], []
    cpt = 0

    for idx, flux in df_jour.iterrows():
        orig = str(flux["Point de départ"]).strip().upper()
        dest = str(flux["Point de destination"]).strip().upper()
        cont = str(flux["Nature de contenant"]).strip()
        ps = str(flux.get("Type (propre/sale)", flux.get("Sale / propre", "Propre"))).strip().upper()
        qte = flux.get("Quantite_du_jour", 0)
        try:
            qte = int(float(qte))
        except (ValueError, TypeError):
            qte = 0
        if qte <= 0:
            continue

        v_elu, capa_max_ = affecter_vehicule(orig, dest, cont, df_v_actifs, df_contenants, df_sites, col_lib)
        if v_elu is None or capa_max_ <= 0:
            continue
        v_type = str(v_elu["Types"]).strip().upper()
        capa_utile = max(1, math.floor(capa_max_ * taux))

        h_dispo = to_min(flux.get("Heure de mise à disposition min départ"),
                         to_min(params.get("rh", {}).get("h_prise_min"), 360))
        h_dead = to_min(flux.get("Heure max de livraison à la destination"),
                        to_min(params.get("rh", {}).get("h_fin_max"), 1260))
        h_fin_max = to_min(params.get("rh", {}).get("h_fin_max"), 1260)
        if h_dead <= h_dispo:
            # Fenêtre incohérente (deadline avant dispo) = erreur de saisie :
            # on rend le flux totalement flexible, il sera signalé.
            h_dead = h_fin_max

        # Missions pleines
        nb_pleins = qte // capa_utile
        for _ in range(nb_pleins):
            cpt += 1
            missions.append(construire_mission_mono(
                f"M{cpt}", v_type, ps, orig, dest, capa_utile, cont,
                h_dispo, h_dead, matrice, df_vehicules, df_sites, col_quai, col_lib))
        # Reliquat
        reste = qte % capa_utile
        if reste > 0:
            reliquats.append(dict(orig=orig, dest=dest, cont=cont, ps=ps, qte=reste,
                                  v_type=v_type, capa=capa_utile, h_dispo=h_dispo, h_dead=h_dead))

    # ---- Reliquats : fusion même-OD puis tournées ----
    if not autoriser_tournees:
        for r in reliquats:
            cpt += 1
            missions.append(construire_mission_mono(
                f"M{cpt}", r["v_type"], r["ps"], r["orig"], r["dest"], r["qte"], r["cont"],
                r["h_dispo"], r["h_dead"], matrice, df_vehicules, df_sites, col_quai, col_lib))
        return missions

    cpt = _consolider_reliquats(reliquats, missions, cpt, matrice,
                                df_vehicules, df_sites, col_quai, col_lib)
    return missions


def _fenetre_ok(group):
    """La tournée est faisable si la dispo la plus tardive < deadline la plus stricte."""
    return max(r["h_dispo"] for r in group) < min(r["h_dead"] for r in group)


def _consolider_reliquats(reliquats, missions, cpt, matrice,
                          df_vehicules, df_sites, col_quai, col_lib):
    """
    Regroupe les reliquats en tournées :
      1. même (orig, dest, contenant, v_type, ps) -> camions combinés
      2. même origine, contenant, ps (DISTRIBUTION multi-livraisons)
      3. même destination, contenant, ps (RAMASSAGE multi-collectes)
      4. solitaires -> mono-OD
    Respecte capacité utile et fenêtre horaire.
    """
    from collections import defaultdict

    # 1) fusion stricte même-OD
    par_od = defaultdict(list)
    for r in reliquats:
        par_od[(r["orig"], r["dest"], r["cont"], r["v_type"], r["ps"])].append(r)
    restants = []
    for (orig, dest, cont, v_type, ps), grp in par_od.items():
        capa = grp[0]["capa"]
        q_cumul, h_dispo, h_dead = 0, max(r["h_dispo"] for r in grp), min(r["h_dead"] for r in grp)
        for r in grp:
            if q_cumul + r["qte"] <= capa:
                q_cumul += r["qte"]
            else:
                restants.append(dict(orig=orig, dest=dest, cont=cont, ps=ps, qte=q_cumul,
                                     v_type=v_type, capa=capa, h_dispo=h_dispo, h_dead=h_dead))
                q_cumul = r["qte"]
        if q_cumul > 0:
            restants.append(dict(orig=orig, dest=dest, cont=cont, ps=ps, qte=q_cumul,
                                 v_type=v_type, capa=capa, h_dispo=h_dispo, h_dead=h_dead))

    # 2) DISTRIBUTION : même origine
    par_orig = defaultdict(list)
    for r in restants:
        par_orig[(r["orig"], r["cont"], r["v_type"], r["ps"])].append(r)
    consommes, encore = set(), []
    for (orig, cont, v_type, ps), grp in par_orig.items():
        grp = [r for r in grp if id(r) not in consommes]
        grp.sort(key=lambda r: -r["qte"])
        i = 0
        while i < len(grp):
            tour, capa, q = [grp[i]], grp[i]["capa"], grp[i]["qte"]
            j = i + 1
            while j < len(grp):
                if q + grp[j]["qte"] <= capa and _fenetre_ok(tour + [grp[j]]):
                    tour.append(grp[j]); q += grp[j]["qte"]
                j += 1
            for r in tour:
                consommes.add(id(r))
            if len(tour) >= 2:
                cpt += 1
                livr = [(r["dest"], r["qte"]) for r in tour]
                missions.append(construire_mission_tournee(
                    f"M{cpt}", v_type, ps, [(orig, q)], livr, cont,
                    max(r["h_dispo"] for r in tour), min(r["h_dead"] for r in tour),
                    matrice, df_vehicules, df_sites, col_quai, col_lib, "DISTRIB"))
            else:
                encore.append(tour[0])
            i += 1
            while i < len(grp) and id(grp[i]) in consommes:
                i += 1

    # 3) RAMASSAGE : même destination (sur ce qui reste)
    par_dest = defaultdict(list)
    for r in encore:
        par_dest[(r["dest"], r["cont"], r["v_type"], r["ps"])].append(r)
    solitaires = []
    for (dest, cont, v_type, ps), grp in par_dest.items():
        grp.sort(key=lambda r: -r["qte"])
        i = 0
        while i < len(grp):
            tour, capa, q = [grp[i]], grp[i]["capa"], grp[i]["qte"]
            j = i + 1
            while j < len(grp):
                if q + grp[j]["qte"] <= capa and _fenetre_ok(tour + [grp[j]]):
                    tour.append(grp[j]); q += grp[j]["qte"]
                j += 1
                if j >= len(grp):
                    break
            used = set(id(r) for r in tour)
            if len(tour) >= 2:
                cpt += 1
                coll = [(r["orig"], r["qte"]) for r in tour]
                missions.append(construire_mission_tournee(
                    f"M{cpt}", v_type, ps, coll, [(dest, q)], cont,
                    max(r["h_dispo"] for r in tour), min(r["h_dead"] for r in tour),
                    matrice, df_vehicules, df_sites, col_quai, col_lib, "RAMASSE"))
                grp = [r for r in grp if id(r) not in used]
                i = 0
            else:
                solitaires.append(tour[0])
                i += 1

    # 4) solitaires -> mono-OD
    for r in solitaires:
        cpt += 1
        missions.append(construire_mission_mono(
            f"M{cpt}", r["v_type"], r["ps"], r["orig"], r["dest"], r["qte"], r["cont"],
            r["h_dispo"], r["h_dead"], matrice, df_vehicules, df_sites, col_quai, col_lib))
    return cpt


# =====================================================================
# 5. CRÉNEAUX (SHIFTS) + LISSAGE DE CHARGE
# =====================================================================

def calculer_shifts(params):
    """
    Découpe la journée en créneaux de poste de durée exacte = amplitude RH.
    Avec amplitude=450 et plage 06h–21h (900 min) -> 2 créneaux qui pavent la
    journée : matin [06h00–13h30] et après-midi [13h30–21h00].
    Le nombre de créneaux par véhicule (relève) = nb de créneaux qui tiennent
    dans la plage horaire, tant que la somme des amplitudes <= plage.
    """
    rh = params.get("rh", {})
    duree = float(rh.get("amplitude_totale", 450))
    h0 = to_min(rh.get("h_prise_min"), 360)
    h1 = to_min(rh.get("h_fin_max"), 1260)
    n = max(1, int((h1 - h0) // duree))   # nb de postes empilables sur un véhicule
    shifts = [(h0 + k * duree, h0 + (k + 1) * duree) for k in range(n)]
    return shifts, duree


def _shifts_feasibles(m, shifts, matrice, depot):
    approche = duree_trajet(matrice, depot, m.site_debut)
    feasibles = []
    for k, (s, e) in enumerate(shifts):
        debut = max(m.h_dispo, s + approche)
        if debut + m.duree <= min(m.h_deadline, e):
            feasibles.append(k)
    return feasibles


def assigner_shifts(missions, shifts, matrice, depot, h_fin_max=1260):
    """
    Répartit les missions entre les créneaux pour LISSER la charge (éviter le pic
    du matin). Une mission n'est exécutable dans un créneau que si sa fenêtre le
    permet. Les fenêtres incohérentes / trop courtes sont relâchées (et signalées
    via mission.fenetre_tendue) plutôt que perdues.

    Équilibrage en deux temps :
      1. missions contraintes (1 seul créneau) d'abord,
      2. missions flexibles vers le créneau le moins chargé.

    Retourne {idx_shift: [missions]}.
    """
    charge = [0.0] * len(shifts)
    assign = {k: [] for k in range(len(shifts))}

    infos = []
    for m in missions:
        feas = _shifts_feasibles(m, shifts, matrice, depot)
        if not feas:
            # fenêtre impossible -> relâchement total + signalement
            m.h_deadline = h_fin_max
            m.fenetre_tendue = True
            feas = _shifts_feasibles(m, shifts, matrice, depot)
            if not feas:
                # encore impossible (dispo trop tardive) -> dernier créneau possible
                feas = [max(range(len(shifts)),
                            key=lambda k: 1 if shifts[k][0] <= m.h_dispo else 0)]
        infos.append((m, feas))

    infos.sort(key=lambda x: len(x[1]))   # plus contraint d'abord
    for m, feas in infos:
        k = min(feas, key=lambda kk: charge[kk])
        assign[k].append(m)
        charge[k] += m.duree
    return assign


# =====================================================================
# 6. CONSTRUCTION DES POSTES PAR CRÉNEAU
#    (chaînage à vide minimal, pause au dépôt, durée exacte)
# =====================================================================

def _besoin_nettoyage(poste, mission):
    return (poste.missions and poste.missions[-1].propre_sale == "SALE"
            and mission.propre_sale == "PROPRE")


def _placer_mission(poste, mission, matrice, t_nettoyage=15.0):
    """Insère : (nettoyage si sale→propre) + approche à vide + attente + mission."""
    if _besoin_nettoyage(poste, mission):
        if poste.position != poste.depot:
            d = duree_trajet(matrice, poste.position, poste.depot)
            poste.etapes.append(Etape("RETOUR_VIDE", poste.t_curr, poste.t_curr + d,
                                      f"Retour nettoyage {poste.position} → {poste.depot}"))
            poste.t_curr += d
            poste.position = poste.depot
        poste.etapes.append(Etape("NETTOYAGE", poste.t_curr, poste.t_curr + t_nettoyage,
                                  "Nettoyage véhicule (sale → propre)"))
        poste.t_curr += t_nettoyage

    approche = duree_trajet(matrice, poste.position, mission.site_debut)
    if approche > 0.1:
        poste.etapes.append(Etape("APPROCHE_VIDE", poste.t_curr, poste.t_curr + approche,
                                  f"Approche {poste.position} → {mission.site_debut}"))
        poste.t_curr += approche
        poste.position = mission.site_debut

    if poste.t_curr < mission.h_dispo:
        poste.etapes.append(Etape("ATTENTE", poste.t_curr, mission.h_dispo,
                                  f"Attente dispo @ {mission.site_debut}"))
        poste.t_curr = mission.h_dispo

    poste.etapes.append(Etape("MISSION", poste.t_curr, poste.t_curr + mission.duree,
                              f"{mission.site_debut} → {mission.site_fin} "
                              f"({mission.nb_contenants} {mission.libelle})", mission=mission))
    poste.t_curr += mission.duree
    poste.position = mission.site_fin
    poste.missions.append(mission)


def _placer_pause(poste, depot, matrice, pause_duree):
    """Pause IMPÉRATIVEMENT au dépôt. Retour à vide d'abord si nécessaire."""
    if poste.position != depot:
        d = duree_trajet(matrice, poste.position, depot)
        if d > 0.1:
            poste.etapes.append(Etape("RETOUR_VIDE", poste.t_curr, poste.t_curr + d,
                                      f"Retour dépôt pour pause {poste.position} → {depot}"))
            poste.t_curr += d
            poste.position = depot
    poste.etapes.append(Etape("PAUSE", poste.t_curr, poste.t_curr + pause_duree,
                              "Pause obligatoire (au dépôt)"))
    poste.t_curr += pause_duree
    poste.pause_faite = True


def _cout_successeur(poste, mission, matrice, t_nettoyage):
    """Simule l'insertion -> (t_fin_mission, cout_vide, attente)."""
    t, pos, vide = poste.t_curr, poste.position, 0.0
    if _besoin_nettoyage(poste, mission):
        if pos != poste.depot:
            d = duree_trajet(matrice, pos, poste.depot); t += d; vide += d; pos = poste.depot
        t += t_nettoyage; vide += t_nettoyage
    approche = duree_trajet(matrice, pos, mission.site_debut)
    t += approche; vide += approche
    attente = max(0.0, mission.h_dispo - t)
    t = max(t, mission.h_dispo)
    return t + mission.duree, vide, attente


def _meilleur_successeur_shift(poste, restants, matrice, depot, s_end,
                               t_fin, pause_duree, vers_depot=False, t_nettoyage=15.0):
    """
    Meilleure mission à chaîner dans le créneau courant :
      - finit avant sa deadline,
      - le poste peut encore se clôturer dans le créneau (retour dépôt +
        pause restante + fin <= fin du créneau),
      - critère : minimiser le temps à vide, puis l'attente, puis la deadline.
    Si vers_depot=True : on ne retient que les missions qui FINISSENT au dépôt
    (pour enchaîner naturellement vers la pause sans trajet à vide).
    """
    pause_restante = 0.0 if poste.pause_faite else pause_duree
    best, best_key = None, None
    for m in restants:
        if m.v_type != poste.v_type:
            continue
        if vers_depot and m.site_fin != depot:
            continue
        t_fin_mission, cout_vide, attente = _cout_successeur(poste, m, matrice, t_nettoyage)
        if t_fin_mission > m.h_deadline:
            continue
        retour = duree_trajet(matrice, m.site_fin, depot)
        if t_fin_mission + retour + pause_restante + t_fin > s_end:
            continue
        key = (round(cout_vide, 1), round(attente, 1), m.h_deadline)
        if best_key is None or key < best_key:
            best_key, best = key, m
    return best


def construire_postes_creneau(missions, shift, idx_shift, v_type, depot, matrice,
                              params, t_nettoyage=15.0):
    """
    Construit les postes (lanes) d'un créneau. Chaque poste :
      - dure EXACTEMENT la durée du créneau (padding inactif au dépôt),
      - prend sa pause obligatoire au dépôt,
      - chaîne les missions pour minimiser le roulage à vide.
    """
    s_start, s_end = shift
    rh = params.get("rh", {})
    duree_poste = s_end - s_start
    t_prise = float(rh.get("temps_fixes_prise", 20))
    t_fin = float(rh.get("temps_fixes_fin", 15))
    pause_duree = float(rh.get("pause", 30))
    # seuil : on vise une pause au milieu de la partie travaillée
    pause_seuil = s_start + t_prise + (duree_poste - t_prise - t_fin) / 2

    postes = []
    restants = sorted(missions, key=lambda m: (m.h_dispo, m.h_deadline))
    num = 0

    while restants:
        num += 1
        p = Poste(id=f"{v_type}_S{idx_shift + 1}_{num:02d}", v_type=v_type, depot=depot)
        p.shift = idx_shift
        p.h_debut = s_start
        p.position = depot
        p.t_curr = s_start
        p.pause_faite = False
        p.etapes.append(Etape("PRISE", s_start, s_start + t_prise, "Prise de poste"))
        p.t_curr = s_start + t_prise

        # --- Remplissage ---
        while True:
            pause_due = (not p.pause_faite) and (p.t_curr >= pause_seuil)
            if pause_due:
                # Privilégier la fin de chaîne qui aboutit au dépôt (HSJ)
                # plutôt qu'un trajet à vide pour aller faire la pause.
                if p.position == depot:
                    _placer_pause(p, depot, matrice, pause_duree)
                    continue
                cand = _meilleur_successeur_shift(p, restants, matrice, depot, s_end,
                                                  t_fin, pause_duree, vers_depot=True,
                                                  t_nettoyage=t_nettoyage)
                if cand is not None:
                    restants.remove(cand)
                    _placer_mission(p, cand, matrice, t_nettoyage)  # finit au dépôt
                    _placer_pause(p, depot, matrice, pause_duree)
                    continue
                # sinon : retour à vide au dépôt puis pause
                _placer_pause(p, depot, matrice, pause_duree)
                continue

            cand = _meilleur_successeur_shift(p, restants, matrice, depot, s_end,
                                              t_fin, pause_duree, t_nettoyage=t_nettoyage)
            if cand is None:
                if not p.missions:
                    # Poste encore vide et aucun successeur plaçable dans le
                    # créneau (mission tendue) : on amorce de force la plus
                    # urgente pour garantir la progression (restants diminue).
                    seed = restants.pop(0)
                    _placer_mission(p, seed, matrice, t_nettoyage)
                    continue
                break
            restants.remove(cand)
            _placer_mission(p, cand, matrice, t_nettoyage)

        _cloturer_poste(p, depot, matrice, s_end, t_fin, pause_duree)
        postes.append(p)

    return postes


def _cloturer_poste(poste, depot, matrice, s_end, t_fin, pause_duree):
    """Retour dépôt + pause si non prise + inactivité jusqu'à la fin exacte du créneau."""
    if poste.position != depot:
        d = duree_trajet(matrice, poste.position, depot)
        if d > 0.1:
            poste.etapes.append(Etape("RETOUR_VIDE", poste.t_curr, poste.t_curr + d,
                                      f"Retour dépôt {poste.position} → {depot}"))
            poste.t_curr += d
            poste.position = depot

    # Pause due mais non encore prise (poste peu chargé) -> au dépôt
    if not poste.pause_faite:
        poste.etapes.append(Etape("PAUSE", poste.t_curr, poste.t_curr + pause_duree,
                                  "Pause obligatoire (au dépôt)"))
        poste.t_curr += pause_duree
        poste.pause_faite = True

    # Inactivité jusqu'à fin de créneau - t_fin (poste = durée EXACTE)
    fin_inactif = s_end - t_fin
    if poste.t_curr < fin_inactif:
        poste.etapes.append(Etape("DISPONIBLE", poste.t_curr, fin_inactif,
                                  "Disponible au dépôt"))
        poste.t_curr = fin_inactif

    poste.etapes.append(Etape("FIN", fin_inactif, s_end, "Nettoyage / clôture"))
    poste.t_curr = s_end
    poste.h_fin = s_end       # durée du poste = durée paramétrée, exactement


# =====================================================================
# 7. COMPTAGE FLOTTE : relève (2 chauffeurs / véhicule) + pic simultané
# =====================================================================

def affecter_vehicules_physiques(postes, shifts):
    """
    Un véhicule physique enchaîne plusieurs créneaux (relève chauffeurs) tant
    que la somme des amplitudes tient dans la plage horaire. On apparie le
    poste du créneau k avec un poste du créneau k+1 sur le même véhicule.

    Nb de véhicules d'un type = max sur les créneaux du nb de postes de ce
    créneau (= pic simultané). C'est ce pic que le lissage cherche à réduire.
    """
    from collections import defaultdict
    par_type = defaultdict(lambda: defaultdict(list))
    for p in postes:
        par_type[p.v_type][p.shift].append(p)

    nb_vehicules = {}
    for v_type, par_shift in par_type.items():
        n_creneaux = len(shifts)
        pic = max((len(par_shift.get(k, [])) for k in range(n_creneaux)), default=0)
        # apparier verticalement : véhicule v <- poste #v de chaque créneau
        for k in range(n_creneaux):
            postes_k = sorted(par_shift.get(k, []), key=lambda p: p.h_debut)
            for v, p in enumerate(postes_k):
                p.id_vehicule = f"{v_type}_VEH{v + 1}"
        nb_vehicules[v_type] = pic
    return nb_vehicules


def courbe_concurrence(postes, pas=15):
    """Nb de postes simultanés par tranche de temps (preuve du lissage)."""
    if not postes:
        return [], []
    h_min = min(p.h_debut for p in postes)
    h_max = max(p.h_fin for p in postes)
    bins = list(range(int(h_min), int(h_max) + 1, pas))
    conc = []
    for b in bins:
        conc.append(sum(1 for p in postes if p.h_debut <= b < p.h_fin))
    return bins, conc


# =====================================================================
# 8. POINT D'ENTRÉE
# =====================================================================

def _capacite_productive(params):
    rh = params.get("rh", {})
    duree = float(rh.get("amplitude_totale", 450))
    return max(60.0, duree - float(rh.get("temps_fixes_prise", 20))
               - float(rh.get("temps_fixes_fin", 15)) - float(rh.get("pause", 30)))


def _estim_lanes(missions_shift, cap_prod):
    """Estimation rapide du nb de postes : charge / capacité productive."""
    charge = sum(m.duree for m in missions_shift)
    return math.ceil(charge / cap_prod) if charge > 0 else 0


def _rebalancer_lanes(assign, shifts, v_type, depot, matrice, params):
    """
    Affine le lissage à partir d'une estimation analytique du nombre de postes
    par créneau (charge / capacité productive) — sans reconstruire les postes.
    Déplace des missions flexibles du créneau le plus chargé vers le moins
    chargé tant que cela réduit le pic estimé.
    """
    if len(shifts) < 2:
        return assign
    cap_prod = _capacite_productive(params)
    for _ in range(50):
        lanes = [_estim_lanes(assign.get(k, []), cap_prod) for k in range(len(shifts))]
        k_max = max(range(len(shifts)), key=lambda k: lanes[k])
        k_min = min(range(len(shifts)), key=lambda k: lanes[k])
        if lanes[k_max] - lanes[k_min] <= 1:
            break
        deplacee = None
        for m in sorted(assign[k_max], key=lambda x: -x.duree):
            if k_min in _shifts_feasibles(m, shifts, matrice, depot):
                deplacee = m
                break
        if deplacee is None:
            break
        assign[k_max].remove(deplacee)
        assign[k_min].append(deplacee)
        new_lanes = [_estim_lanes(assign.get(k, []), cap_prod) for k in range(len(shifts))]
        if max(new_lanes) >= max(lanes):     # pas d'amélioration -> annuler
            assign[k_min].remove(deplacee)
            assign[k_max].append(deplacee)
            break
    return assign


def optimiser_postes_jour(df_jour, df_vehicules, df_contenants, df_sites,
                          matrice_duree, params_logistique, nom_jour="Lundi",
                          autoriser_tournees=True):
    """
    Pipeline complet : flux du jour -> postes lissés sur la journée + flotte.

    Contraintes intégrées :
      - pause obligatoire au dépôt (HSJ),
      - chaque poste dure EXACTEMENT l'amplitude paramétrée,
      - relève possible (2 chauffeurs / véhicule) si somme des amplitudes <= plage,
      - lissage de la charge entre créneaux pour minimiser le pic de véhicules.

    Retourne dict : postes, nb_vehicules, missions, metriques, concurrence...
    """
    matrice = nettoyer_matrice(matrice_duree)

    ds = df_sites.copy()
    ds.columns = [str(c).strip().upper() for c in ds.columns]
    col_lib = next((c for c in ds.columns if "LIBEL" in c or "SITE" in c), ds.columns[0])
    col_quai = next((c for c in ds.columns if "QUAI" in c), "PRÉSENCE DE QUAI")
    ds[col_lib] = ds[col_lib].astype(str).str.strip().str.upper()

    vehicules_autorises = params_logistique.get("vehicules_selectionnes",
                                                df_vehicules["Types"].tolist())
    df_v_actifs = df_vehicules[df_vehicules["Types"].isin(vehicules_autorises)].copy()
    depot = (str(df_v_actifs["Stationnement initial"].iloc[0]).strip().upper()
             if not df_v_actifs.empty else "HSJ")

    # 1-3 : missions
    missions = consolider_missions(df_jour, df_vehicules, df_contenants, ds, matrice,
                                   params_logistique, col_lib, col_quai, df_v_actifs,
                                   autoriser_tournees=autoriser_tournees)

    # 4 : créneaux + lissage + postes (par type de véhicule)
    shifts, duree_poste = calculer_shifts(params_logistique)
    h_fin_max = to_min(params_logistique.get("rh", {}).get("h_fin_max"), 1260)
    postes = []
    par_type = {}
    for m in missions:
        par_type.setdefault(m.v_type, []).append(m)

    for v_type, liste in par_type.items():
        assign = assigner_shifts(liste, shifts, matrice, depot, h_fin_max)
        assign = _rebalancer_lanes(assign, shifts, v_type, depot, matrice, params_logistique)
        for k, (s_start, s_end) in enumerate(shifts):
            postes.extend(construire_postes_creneau(
                assign.get(k, []), (s_start, s_end), k, v_type, depot,
                matrice, params_logistique))

    non_traitees = [m for m in missions if m.fenetre_tendue]

    # 5 : flotte (relève) + pic
    nb_vehicules = affecter_vehicules_physiques(postes, shifts)
    bins, conc = courbe_concurrence(postes)
    pic_simultane = max(conc) if conc else 0

    # métriques
    t_charge = sum(p.temps_charge() for p in postes)
    t_vide = sum(p.temps_vide() for p in postes)
    t_attente = sum(p.temps_attente() for p in postes)
    metriques = {
        "nb_missions": len(missions),
        "nb_missions_non_traitees": len(non_traitees),
        "nb_postes": len(postes),
        "nb_vehicules_total": sum(nb_vehicules.values()),
        "nb_vehicules_par_type": nb_vehicules,
        "pic_vehicules_simultanes": pic_simultane,
        "temps_charge_min": round(t_charge, 0),
        "temps_vide_min": round(t_vide, 0),
        "temps_inactif_min": round(t_attente, 0),
        "taux_charge_global": round(t_charge / (t_charge + t_vide) * 100, 1) if (t_charge + t_vide) else 0,
    }
    return {"postes": postes, "missions": missions, "non_traitees": non_traitees,
            "nb_vehicules": nb_vehicules, "metriques": metriques,
            "concurrence": {"bins": bins, "valeurs": conc},
            "jour": nom_jour, "depot": depot, "shifts": shifts}
