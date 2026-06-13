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

    # --- métriques calculées ---
    @property
    def amplitude(self):
        return self.h_fin - self.h_debut

    def temps_charge(self):
        return sum(e.duree for e in self.etapes if e.type == "MISSION")

    def temps_vide(self):
        return sum(e.duree for e in self.etapes if e.type in ("APPROCHE_VIDE", "RETOUR_VIDE", "NETTOYAGE"))

    def temps_attente(self):
        return sum(e.duree for e in self.etapes if e.type == "ATTENTE")

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
        if h_dead <= h_dispo:
            h_dead = h_dispo + 120

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
# 5. CHAÎNAGE + CONSTRUCTION DES POSTES  (cœur de la refonte)
# =====================================================================

def construire_postes(missions, depot, matrice, params, t_nettoyage=15.0):
    """
    Construit les postes par CHAÎNAGE glouton, en privilégiant les liaisons
    sans trajet à vide (successeur dont le site de départ == position courante).

    Un poste = une vacation chauffeur sur un véhicule, bornée par l'amplitude RH.
    On traite chaque type de véhicule séparément (un poste = un seul type).
    """
    rh = params.get("rh", {})
    amplitude_max = float(rh.get("temps_productif_max") or rh.get("amplitude_totale", 450))
    pause_duree = float(rh.get("pause", 30))
    t_prise = float(rh.get("temps_fixes_prise", 20))
    t_fin = float(rh.get("temps_fixes_fin", 15))
    h_prise_min = to_min(rh.get("h_prise_min"), 360)
    h_fin_max = to_min(rh.get("h_fin_max"), 1260)
    pause_seuil = amplitude_max / 2  # pause après la moitié de l'amplitude

    postes = []
    par_type = {}
    for m in missions:
        par_type.setdefault(m.v_type, []).append(m)

    for v_type, liste in par_type.items():
        restants = sorted(liste, key=lambda m: (m.h_dispo, m.h_deadline))
        cpt_poste = 0

        while restants:
            cpt_poste += 1
            seed = restants.pop(0)
            poste = Poste(id=f"{v_type}_{cpt_poste:02d}", v_type=v_type, depot=depot)

            # --- Démarrage : approche dépôt -> 1er site, prise de poste ---
            approche = duree_trajet(matrice, depot, seed.site_debut)
            # On démarre au plus tard pour coller à h_dispo (réduit l'attente)
            depart_depot = max(h_prise_min + t_prise, seed.h_dispo - approche)
            poste.h_debut = depart_depot - t_prise
            poste.etapes.append(Etape("PRISE", poste.h_debut, depart_depot,
                                      "Préparation / check véhicule"))
            poste.position = depot
            poste.t_curr = depart_depot
            _placer_mission(poste, seed, matrice, t_nettoyage, pause_seuil, pause_duree)

            # --- Extension : chaîner tant que c'est faisable ---
            while True:
                cand, info = _meilleur_successeur(
                    poste, restants, matrice, depot, amplitude_max, h_fin_max,
                    t_fin, t_nettoyage, pause_seuil, pause_duree)
                if cand is None:
                    break
                restants.remove(cand)
                _placer_mission(poste, cand, matrice, t_nettoyage, pause_seuil, pause_duree,
                                pre=info)

            # --- Clôture : retour dépôt à vide si nécessaire + fin de poste ---
            if poste.position != depot:
                ret = duree_trajet(matrice, poste.position, depot)
                if ret > 0.1:
                    poste.etapes.append(Etape("RETOUR_VIDE", poste.t_curr, poste.t_curr + ret,
                                              f"Retour dépôt {poste.position} → {depot}"))
                    poste.t_curr += ret
                    poste.position = depot
            poste.etapes.append(Etape("FIN", poste.t_curr, poste.t_curr + t_fin,
                                      "Nettoyage / clôture"))
            poste.t_curr += t_fin
            poste.h_fin = poste.t_curr
            postes.append(poste)

    return postes


def _besoin_nettoyage(poste, mission):
    """Un véhicule SALE qui enchaîne sur du PROPRE doit être nettoyé."""
    return (poste.missions and poste.missions[-1].propre_sale == "SALE"
            and mission.propre_sale == "PROPRE")


def _placer_mission(poste, mission, matrice, t_nettoyage, pause_seuil, pause_duree, pre=None):
    """Insère une mission dans le poste : approche à vide + (nettoyage) + mission."""
    # Pause obligatoire si on dépasse le seuil et qu'elle n'est pas faite
    if not poste.pause_faite and (poste.t_curr - poste.h_debut) >= pause_seuil:
        poste.etapes.append(Etape("PAUSE", poste.t_curr, poste.t_curr + pause_duree,
                                  "Pause obligatoire"))
        poste.t_curr += pause_duree
        poste.pause_faite = True

    # Nettoyage sale -> propre : détour par le dépôt
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

    # Approche à vide vers le site de départ de la mission
    approche = duree_trajet(matrice, poste.position, mission.site_debut)
    if approche > 0.1:
        poste.etapes.append(Etape("APPROCHE_VIDE", poste.t_curr, poste.t_curr + approche,
                                  f"Approche {poste.position} → {mission.site_debut}"))
        poste.t_curr += approche
        poste.position = mission.site_debut

    # Attente si on arrive avant la mise à disposition
    if poste.t_curr < mission.h_dispo:
        poste.etapes.append(Etape("ATTENTE", poste.t_curr, mission.h_dispo,
                                  f"Attente dispo @ {mission.site_debut}"))
        poste.t_curr = mission.h_dispo

    # La mission elle-même
    poste.etapes.append(Etape("MISSION", poste.t_curr, poste.t_curr + mission.duree,
                              f"{mission.site_debut} → {mission.site_fin} "
                              f"({mission.nb_contenants} {mission.libelle})", mission=mission))
    poste.t_curr += mission.duree
    poste.position = mission.site_fin
    poste.missions.append(mission)


def _cout_insertion(poste, mission, matrice, t_nettoyage, pause_seuil, pause_duree):
    """
    Simule l'insertion et renvoie (faisable_finish, cout_vide, attente, t_fin_mission).
    cout_vide = temps à vide (approche + éventuel détour nettoyage). On le minimise.
    """
    t = poste.t_curr
    pos = poste.position
    cout_vide = 0.0

    if not poste.pause_faite and (t - poste.h_debut) >= pause_seuil:
        t += pause_duree
    if _besoin_nettoyage(poste, mission):
        if pos != poste.depot:
            d = duree_trajet(matrice, pos, poste.depot)
            t += d; cout_vide += d; pos = poste.depot
        t += t_nettoyage; cout_vide += t_nettoyage
    approche = duree_trajet(matrice, pos, mission.site_debut)
    t += approche; cout_vide += approche
    attente = max(0.0, mission.h_dispo - t)
    t = max(t, mission.h_dispo)
    t_fin_mission = t + mission.duree
    return t_fin_mission, cout_vide, attente


def _meilleur_successeur(poste, restants, matrice, depot, amplitude_max, h_fin_max,
                         t_fin, t_nettoyage, pause_seuil, pause_duree):
    """
    Choisit la prochaine mission à chaîner :
      - faisable : finit avant sa deadline, et le poste (retour dépôt + clôture inclus)
        reste sous l'amplitude max et avant h_fin_max
      - critère : minimiser le temps à vide, puis l'attente, puis la deadline
    """
    best, best_key, best_info = None, None, None
    for m in restants:
        if m.v_type != poste.v_type:
            continue
        t_fin_mission, cout_vide, attente = _cout_insertion(
            poste, m, matrice, t_nettoyage, pause_seuil, pause_duree)
        if t_fin_mission > m.h_deadline:
            continue
        # le poste doit pouvoir se clôturer (retour dépôt à vide + fin)
        retour = duree_trajet(matrice, m.site_fin, depot)
        fin_poste = t_fin_mission + retour + t_fin
        if (fin_poste - poste.h_debut) > amplitude_max or fin_poste > h_fin_max:
            continue
        key = (round(cout_vide, 1), round(attente, 1), m.h_deadline)
        if best_key is None or key < best_key:
            best_key, best, best_info = key, m, {"cout_vide": cout_vide}
    return best, best_info


# =====================================================================
# 6. COMPTAGE FLOTTE : postes simultanés -> véhicules physiques
# =====================================================================

def affecter_vehicules_physiques(postes):
    """
    Un véhicule physique peut enchaîner plusieurs postes non chevauchants (relève).
    Coloration d'intervalles gloutonne par type -> nombre minimal de véhicules.
    """
    from collections import defaultdict
    par_type = defaultdict(list)
    for p in postes:
        par_type[p.v_type].append(p)

    nb_vehicules = {}
    for v_type, liste in par_type.items():
        liste.sort(key=lambda p: p.h_debut)
        fins_vehicules = []  # (h_fin, id_vehicule)
        for p in liste:
            dispo = None
            for k, (hf, vid) in enumerate(fins_vehicules):
                if hf <= p.h_debut:
                    dispo = k
                    break
            if dispo is None:
                vid = f"{v_type}_VEH{len(fins_vehicules) + 1}"
                fins_vehicules.append((p.h_fin, vid))
                p.id_vehicule = vid
            else:
                p.id_vehicule = fins_vehicules[dispo][1]
                fins_vehicules[dispo] = (p.h_fin, p.id_vehicule)
        nb_vehicules[v_type] = len(fins_vehicules)
    return nb_vehicules


# =====================================================================
# 7. POINT D'ENTRÉE
# =====================================================================

def optimiser_postes_jour(df_jour, df_vehicules, df_contenants, df_sites,
                          matrice_duree, params_logistique, nom_jour="Lundi",
                          autoriser_tournees=True):
    """
    Pipeline complet : flux du jour -> postes chauffeurs chaînés + flotte.

    df_jour : flux du jour (sortie de preparer_flux_complets_du_jour),
              colonnes 'Point de départ', 'Point de destination',
              'Nature de contenant', 'Quantite_du_jour',
              'Heure de mise à disposition min départ',
              'Heure max de livraison à la destination',
              'Type (propre/sale)' (ou 'Sale / propre').

    Retourne dict : postes, nb_vehicules, nb_postes, missions, metriques.
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

    depot = str(df_v_actifs["Stationnement initial"].iloc[0]).strip().upper() if not df_v_actifs.empty else "HSJ"

    # 1-3 : missions
    missions = consolider_missions(df_jour, df_vehicules, df_contenants, ds, matrice,
                                   params_logistique, col_lib, col_quai, df_v_actifs,
                                   autoriser_tournees=autoriser_tournees)
    # 4 : postes chaînés
    postes = construire_postes(missions, depot, matrice, params_logistique)
    # 5 : flotte
    nb_vehicules = affecter_vehicules_physiques(postes)

    # métriques
    t_charge = sum(p.temps_charge() for p in postes)
    t_vide = sum(p.temps_vide() for p in postes)
    t_attente = sum(p.temps_attente() for p in postes)
    metriques = {
        "nb_missions": len(missions),
        "nb_postes": len(postes),
        "nb_vehicules_total": sum(nb_vehicules.values()),
        "nb_vehicules_par_type": nb_vehicules,
        "temps_charge_min": round(t_charge, 0),
        "temps_vide_min": round(t_vide, 0),
        "temps_attente_min": round(t_attente, 0),
        "taux_charge_global": round(t_charge / (t_charge + t_vide) * 100, 1) if (t_charge + t_vide) else 0,
    }
    return {"postes": postes, "missions": missions, "nb_vehicules": nb_vehicules,
            "metriques": metriques, "jour": nom_jour, "depot": depot}
