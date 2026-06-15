"""
moteur_postes.py — Moteur de séquençage des tournées de transport (CHU Nantes)
==============================================================================

Refonte complète (étapes 3-4 du pipeline). Heuristique maison, lisible,
déterministe par graine, sans OR-Tools.

Principes
---------
Réseau hub-and-spoke autour du dépôt (HSJ) : le levier d'optimisation est le
CHAÎNAGE (A→B puis B→A) pour limiter les trajets à vide, pas le remplissage.

Pipeline
--------
  1. Affectation véhicule + capacité utile (par SURFACE au sol)
  2. Fragmentation : N missions pleines + 1 reliquat par flux
  3. Consolidation des reliquats en tournées multi-arrêts
       - plafond = "durée max d'une tournée" (param)
       - réaffectation au véhicule le mieux rempli compatible
  4. Construction des postes :
       - solution initiale par PAVAGE (2 créneaux : matin / après-midi)
       - MULTI-START avec aléa dans l'ordre de construction
       - RECHERCHE LOCALE : fermer les postes creux, compacter
       - sélection lexicographique : flotte → nb postes → homogénéité
  5. Comptage flotte par APPARIEMENT (relève 2 chauffeurs / véhicule)

Contraintes intégrées
----------------------
  - pause obligatoire AU DÉPÔT (cumulée avec la marge inter-job)
  - marge inter-job entre deux missions consécutives
  - aléa de circulation appliqué à TOUTES les durées de trajet
  - durée max de tournée sur les missions multi-arrêts uniquement
  - poste d'une durée <= amplitude (pas de bourrage à 450)
  - flux infaisables (fenêtre incohérente, etc.) -> NON SERVIS (signalés)
"""

from __future__ import annotations
import math
import random
import time as _time
from dataclasses import dataclass, field
from datetime import time, datetime, timedelta


# =====================================================================
# 0. HELPERS
# =====================================================================

def to_min(val, defaut=0.0):
    """time / 'HH:MM[:SS]' / fraction Excel / minutes -> minutes."""
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
        return f * 1440 if 0 < f < 2 else f
    except (ValueError, TypeError):
        return defaut


def nettoyer_matrice(matrice_duree, alea=0.0):
    """Matrice de durée -> dict {O:{D: minutes*(1+alea)}}. L'aléa s'applique ici,
    donc à TOUTES les durées de trajet du moteur."""
    df = matrice_duree.copy()
    col0 = df.columns[0]
    df = df.set_index(col0)
    df.index = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()
    f = 1.0 + float(alea)
    return {o: {d: float(v) * f for d, v in row.items()} for o, row in df.to_dict("index").items()}


def nettoyer_matrice_dist(matrice_dist):
    """Matrice de distance -> dict {O:{D: km}} (pas d'aléa sur les km)."""
    df = matrice_dist.copy()
    col0 = df.columns[0]
    df = df.set_index(col0)
    df.index = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()
    return {o: {d: float(v) for d, v in row.items()} for o, row in df.to_dict("index").items()}


def duree_trajet(matrice, a, b):
    a, b = str(a).strip().upper(), str(b).strip().upper()
    if a == b:
        return 0.0
    return matrice.get(a, {}).get(b, 30.0)


def dist_trajet(matrice_dist, a, b):
    a, b = str(a).strip().upper(), str(b).strip().upper()
    if a == b:
        return 0.0
    return matrice_dist.get(a, {}).get(b, 0.0)


def _f(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return 0.0


def surface_contenant(cont_row):
    c = {str(k).strip(): v for k, v in cont_row.items()}
    return _f(c.get("dim longueur (m)")) * _f(c.get("dim largeur (m)"))


def poids_contenant(cont_row):
    c = {str(k).strip(): v for k, v in cont_row.items()}
    return _f(c.get("Poids plein (T)"))


def surface_sol_vehicule(veh_row):
    v = {str(k).strip(): val for k, val in veh_row.items()}
    return _f(v.get("dim longueur interne (m)")) * _f(v.get("dim largeur interne (m)"))


def capacite_max(vehicule, contenant):
    """Bin-packing guillotine 2D + limite poids. Renvoie un nombre de contenants."""
    v = {str(k).strip(): val for k, val in vehicule.items()}
    c = {str(k).strip(): val for k, val in contenant.items()}
    nom = str(c.get("libellé", "")).strip()
    if not nom or str(v.get(nom, "")).strip().upper() != "OUI":
        return 0
    try:
        L_v = _f(v["dim longueur interne (m)"]); l_v = _f(v["dim largeur interne (m)"])
        P = _f(v["Poids max chargement"])
        L_c = _f(c["dim longueur (m)"]); l_c = _f(c["dim largeur (m)"]); p = _f(c["Poids plein (T)"])
    except (KeyError, ValueError, TypeError):
        return 0
    if L_c <= 0 or l_c <= 0:
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


def _params_manut(df_vehicules, v_type):
    row = df_vehicules[df_vehicules["Types"].astype(str).str.strip().str.upper()
                       == str(v_type).strip().upper()]
    if row.empty:
        return 3.0, 25 / 60, 15 / 60
    r = row.iloc[0]
    t_quai = to_min(r.get("Temps de mise à quai - manœuvre, contact/admin (minutes)"), 3.0)
    t_sans = to_min(r.get("Manutention sans quai (minutes / contenants)"), 25 / 60)
    t_avec = to_min(r.get("Manutention avec quai (minutes / contenants)"), 15 / 60)
    if t_quai <= 0:
        t_quai = 3.0
    if t_sans <= 0:
        t_sans = 25 / 60
    if t_avec <= 0:
        t_avec = 15 / 60
    return t_quai, t_sans, t_avec


def _a_quai(df_sites, col_quai, col_lib, site):
    row = df_sites.loc[df_sites[col_lib] == str(site).strip().upper(), col_quai]
    return (not row.empty) and str(row.values[0]).strip().upper() == "OUI"


# =====================================================================
# 1. STRUCTURES
# =====================================================================

@dataclass
class Mission:
    id: str
    v_type: str
    propre_sale: str
    site_debut: str
    site_fin: str
    h_dispo: float
    h_deadline: float
    duree: float
    etapes: list = field(default_factory=list)   # chrono relative (action/site/t_debut/t_fin/...)
    nb_contenants: int = 0
    libelle: str = ""
    fonction_support: str = ""
    composantes: list = field(default_factory=list)  # [(flux_id, orig, dest, cont, qte)]
    surface: float = 0.0          # surface au sol occupée (m²)
    poids: float = 0.0            # poids transporté (T)
    fill: float = 0.0             # taux de remplissage au sol (0-1)
    sens: str = "MONO"            # MONO | DISTRIB | RAMASSE
    fenetre_tendue: bool = False

    def __repr__(self):
        return (f"Mission({self.id}|{self.v_type}|{self.site_debut}->{self.site_fin}"
                f"|{self.duree:.0f}min|fill={self.fill:.0%})")


@dataclass
class Etape:
    type: str   # PRISE|APPROCHE_VIDE|MISSION|MARGE|RETOUR_VIDE|NETTOYAGE|PAUSE|DISPONIBLE|FIN
    h_debut: float
    h_fin: float
    detail: str = ""
    mission: Mission = None
    site_debut: str = ""
    site_fin: str = ""
    distance: float = 0.0
    a_vide: bool = None

    @property
    def duree(self):
        return self.h_fin - self.h_debut


@dataclass(eq=False)
class Poste:
    id: str
    v_type: str
    depot: str
    h_debut: float = 0.0
    h_fin: float = 0.0
    position: str = ""
    t_curr: float = 0.0
    pause_faite: bool = False
    missions: list = field(default_factory=list)
    etapes: list = field(default_factory=list)
    id_vehicule: str = ""
    shift: int = 0

    @property
    def amplitude(self):
        return self.h_fin - self.h_debut

    def temps_charge(self):
        return sum(e.duree for e in self.etapes if e.type == "MISSION")

    def temps_vide(self):
        return sum(e.duree for e in self.etapes if e.type in ("APPROCHE_VIDE", "RETOUR_VIDE"))

    def temps_attente(self):
        return sum(e.duree for e in self.etapes if e.type in ("ATTENTE", "DISPONIBLE"))

    def temps_marge(self):
        return sum(e.duree for e in self.etapes if e.type == "MARGE")

    def occupation(self):
        """Taux d'occupation utile = (chargé + roulage à vide) / amplitude."""
        a = self.amplitude
        return (self.temps_charge() + self.temps_vide()) / a if a > 0 else 0.0

    def taux_charge_roulage(self):
        roul = self.temps_charge() + self.temps_vide()
        return self.temps_charge() / roul if roul > 0 else 0.0


# =====================================================================
# 2. AFFECTATION VÉHICULE
# =====================================================================

def _accessible(acces, v_type, site):
    return acces.get((str(site).strip().upper(), str(v_type).strip().upper()), False)


def affecter_vehicule_capacitaire(orig, dest, cont, v_types, caps, acces):
    """Pour les missions PLEINES : véhicule le plus capacitaire compatible."""
    best_v, best_cap = None, 0
    for vt in v_types:
        if _accessible(acces, vt, orig) and _accessible(acces, vt, dest):
            cap = caps.get(vt, {}).get(cont, 0)
            if cap > best_cap:
                best_cap, best_v = cap, vt
    return best_v, best_cap


def choisir_vehicule_groupe(sites, contenants, surface_load, count_load,
                            v_types, caps, acces, floor, taux):
    """
    Pour un groupe (reliquat / tournée) : véhicule compatible avec TOUS les sites
    et TOUS les contenants, le MIEUX REMPLI possible (taux de surface au sol le
    plus élevé) tout en respectant le seuil de remplissage. Évite de faire rouler
    un gros véhicule à moitié vide.
    """
    compatibles = []
    for vt in v_types:
        if any(not _accessible(acces, vt, s) for s in sites):
            continue
        if any(caps.get(vt, {}).get(c, 0) <= 0 for c in contenants):
            continue
        if len(contenants) == 1 and count_load > caps.get(vt, {}).get(contenants[0], 0):
            continue
        compatibles.append(vt)
    if not compatibles:
        return None
    # Ceux qui tiennent la charge sous le seuil de remplissage (surface)
    sous_seuil = [vt for vt in compatibles if surface_load <= taux * floor[vt] + 1e-9]
    if sous_seuil:
        # le mieux rempli = plus petit plancher adéquat
        return max(sous_seuil, key=lambda vt: surface_load / floor[vt])
    # Le taux maximal paramétré est une contrainte stricte, pas une préférence.
    return None


# =====================================================================
# 3. CONSTRUCTION DES MISSIONS (avec chronologie + distance)
# =====================================================================

def construire_mission_mono(mid, v_type, ps, orig, dest, qte, cont, libelle, fsupport,
                            flux_id, h_dispo, h_deadline, matrice, matrice_dist,
                            df_vehicules, df_sites, col_quai, col_lib,
                            surface_u, poids_u, floor_v):
    t_quai, t_sans, t_avec = _params_manut(df_vehicules, v_type)
    tc_o = t_avec if _a_quai(df_sites, col_quai, col_lib, orig) else t_sans
    tc_d = t_avec if _a_quai(df_sites, col_quai, col_lib, dest) else t_sans
    trajet = duree_trajet(matrice, orig, dest)
    dist = dist_trajet(matrice_dist, orig, dest)
    surface = qte * surface_u
    poids = qte * poids_u

    etapes, t = [], 0.0

    def add(site, action, d, label, **kw):
        nonlocal t
        e = {"site": site, "action": action, "t_debut": round(t, 1),
             "t_fin": round(t + d, 1), "label": label}
        e.update(kw)
        etapes.append(e)
        t += d
    add(orig, "MISE_A_QUAI", t_quai, f"Mise à quai @ {orig}")
    add(orig, "CHARGEMENT", qte * tc_o, f"Chargement {qte} {libelle}",
        charge={cont: qte}, surface_apres=surface, poids_apres=poids)
    add(dest, "TRAJET", trajet, f"Trajet {orig} → {dest}", distance=dist, a_plein=True)
    add(dest, "MISE_A_QUAI", t_quai, f"Mise à quai @ {dest}")
    add(dest, "DECHARGEMENT", qte * tc_d, f"Déchargement {qte} {libelle}",
        decharge={cont: qte}, surface_apres=0.0, poids_apres=0.0)

    return Mission(id=mid, v_type=v_type, propre_sale=ps, site_debut=orig, site_fin=dest,
                   h_dispo=h_dispo, h_deadline=h_deadline, duree=round(t, 1), etapes=etapes,
                   nb_contenants=qte, libelle=libelle, fonction_support=fsupport,
                   composantes=[(flux_id, orig, dest, cont, qte)],
                   surface=surface, poids=poids,
                   fill=(surface / floor_v if floor_v else 0.0), sens="MONO")


def construire_mission_tournee(mid, v_type, ps, origines, livraisons, cont, libelle, fsupport,
                               composantes, h_dispo, h_deadline, matrice, matrice_dist,
                               df_vehicules, df_sites, col_quai, col_lib, sens,
                               surface_u, poids_u, floor_v):
    t_quai, t_sans, t_avec = _params_manut(df_vehicules, v_type)
    etapes, t = [], 0.0
    nb_tot = sum(q for _, q in (livraisons if sens == "DISTRIB" else origines))
    surface = nb_tot * surface_u
    poids = nb_tot * poids_u

    def add(site, action, d, label, **kw):
        nonlocal t
        e = {"site": site, "action": action, "t_debut": round(t, 1),
             "t_fin": round(t + d, 1), "label": label}
        e.update(kw)
        etapes.append(e)
        t += d

    if sens == "DISTRIB":
        orig = origines[0][0]
        tc_o = t_avec if _a_quai(df_sites, col_quai, col_lib, orig) else t_sans
        add(orig, "MISE_A_QUAI", t_quai, f"Mise à quai @ {orig}")
        charge = nb_tot
        add(orig, "CHARGEMENT", nb_tot * tc_o, f"Chargement {nb_tot} {libelle}",
            charge={cont: nb_tot}, surface_apres=charge * surface_u, poids_apres=charge * poids_u)
        pos = orig
        for site, q in livraisons:
            tc_d = t_avec if _a_quai(df_sites, col_quai, col_lib, site) else t_sans
            add(site, "TRAJET", duree_trajet(matrice, pos, site), f"Trajet {pos} → {site}",
                distance=dist_trajet(matrice_dist, pos, site), a_plein=True)
            add(site, "MISE_A_QUAI", t_quai, f"Mise à quai @ {site}")
            charge -= q
            add(site, "DECHARGEMENT", q * tc_d, f"Déchargement {q} {libelle} @ {site}",
                decharge={cont: q}, surface_apres=charge * surface_u, poids_apres=charge * poids_u)
            pos = site
        site_debut, site_fin = orig, livraisons[-1][0]
    else:  # RAMASSE
        dest = livraisons[0][0]
        pos = origines[0][0]
        charge = 0
        for site, q in origines:
            tc_o = t_avec if _a_quai(df_sites, col_quai, col_lib, site) else t_sans
            if site != pos:
                add(site, "TRAJET", duree_trajet(matrice, pos, site), f"Trajet {pos} → {site}",
                    distance=dist_trajet(matrice_dist, pos, site), a_plein=(charge > 0))
            add(site, "MISE_A_QUAI", t_quai, f"Mise à quai @ {site}")
            charge += q
            add(site, "CHARGEMENT", q * tc_o, f"Chargement {q} {libelle} @ {site}",
                charge={cont: q}, surface_apres=charge * surface_u, poids_apres=charge * poids_u)
            pos = site
        tc_d = t_avec if _a_quai(df_sites, col_quai, col_lib, dest) else t_sans
        add(dest, "TRAJET", duree_trajet(matrice, pos, dest), f"Trajet {pos} → {dest}",
            distance=dist_trajet(matrice_dist, pos, dest), a_plein=True)
        add(dest, "MISE_A_QUAI", t_quai, f"Mise à quai @ {dest}")
        add(dest, "DECHARGEMENT", nb_tot * tc_d, f"Déchargement {nb_tot} {libelle}",
            decharge={cont: nb_tot}, surface_apres=0.0, poids_apres=0.0)
        site_debut, site_fin = origines[0][0], dest

    return Mission(id=mid, v_type=v_type, propre_sale=ps, site_debut=site_debut, site_fin=site_fin,
                   h_dispo=h_dispo, h_deadline=h_deadline, duree=round(t, 1), etapes=etapes,
                   nb_contenants=nb_tot, libelle=libelle, fonction_support=fsupport,
                   composantes=composantes, surface=surface, poids=poids,
                   fill=(surface / floor_v if floor_v else 0.0), sens=sens)


# =====================================================================
# 4. CONSOLIDATION  +  DÉTECTION DES FLUX NON SERVIS
# =====================================================================

def consolider_missions(df_jour, df_vehicules, df_contenants, df_sites, matrice, matrice_dist,
                        params, col_lib, col_quai, v_types, caps, acces, floor,
                        surf_cont, poids_cont, autoriser_tournees=True):
    """Flux du jour -> (missions, flux_non_servis)."""
    taux = params.get("securite_remplissage", 0.85)
    rh = params.get("rh", {})
    h_prise = to_min(rh.get("h_prise_min"), 360)
    h_fin = to_min(rh.get("h_fin_max"), 1260)
    duree_max_tournee = float(params.get("duree_max_superjob", 225))
    cap_productive = _capacite_productive(params)

    missions, reliquats, non_servis = [], [], []
    cpt = 0

    for idx, flux in df_jour.iterrows():
        orig = str(flux["Point de départ"]).strip().upper()
        dest = str(flux["Point de destination"]).strip().upper()
        cont = str(flux["Nature de contenant"]).strip()
        ps = str(flux.get("Type (propre/sale)", flux.get("Sale / propre", "Propre"))).strip().upper()
        fsupport = str(flux.get("Fonction Support associée", "")).strip()
        try:
            qte = int(float(flux.get("Quantite_du_jour", 0)))
        except (ValueError, TypeError):
            qte = 0
        if qte <= 0:
            continue

        def nonservi(raison, contrainte):
            non_servis.append({"flux_id": idx, "origine": orig, "destination": dest,
                               "contenant": cont, "raison": raison, "contrainte": contrainte})

        # --- véhicule capacitaire de référence ---
        v_cap, capa_cap = affecter_vehicule_capacitaire(orig, dest, cont, v_types, caps, acces)
        if v_cap is None or capa_cap <= 0:
            # diagnostic précis
            acc_o = [vt for vt in v_types if _accessible(acces, vt, orig)]
            acc_d = [vt for vt in v_types if _accessible(acces, vt, dest)]
            if not acc_o:
                nonservi(f"Aucun véhicule sélectionné n'a accès au site de départ '{orig}'",
                         "Accessibilité site de départ (param Sites)")
            elif not acc_d:
                nonservi(f"Aucun véhicule sélectionné n'a accès au site d'arrivée '{dest}'",
                         "Accessibilité site d'arrivée (param Sites)")
            else:
                nonservi(f"Aucun véhicule compatible ne peut transporter le contenant '{cont}'",
                         "Compatibilité contenant/véhicule (param Véhicules)")
            continue

        # --- fenêtre horaire ---
        h_dispo = to_min(flux.get("Heure de mise à disposition min départ"), h_prise)
        h_dead = to_min(flux.get("Heure max de livraison à la destination"), h_fin)
        if h_dead <= h_dispo:
            nonservi(f"Fenêtre incohérente : livraison ({_hhmm(h_dead)}) avant mise à "
                     f"disposition ({_hhmm(h_dispo)})",
                     "Heures du flux à corriger (M flux col. 26/27)")
            continue

        # capa utile par SURFACE (respecte le seuil de remplissage)
        floor_cap = floor[v_cap]
        s_u = surf_cont[cont]
        capa_utile = max(1, min(capa_cap, int((taux * floor_cap) / s_u) if s_u > 0 else capa_cap))

        # durée d'une mission mono (pour test de faisabilité fenêtre)
        m_test = construire_mission_mono(
            "TEST", v_cap, ps, orig, dest, min(qte, capa_utile), cont, cont, fsupport, idx,
            h_dispo, h_dead, matrice, matrice_dist, df_vehicules, df_sites, col_quai, col_lib,
            s_u, poids_cont[cont], floor_cap)
        if (h_dead - h_dispo) < m_test.duree:
            nonservi(f"Fenêtre trop courte : durée mission {m_test.duree:.0f} min > fenêtre "
                     f"{(h_dead - h_dispo):.0f} min ({_hhmm(h_dispo)}→{_hhmm(h_dead)})",
                     "Fenêtre horaire ou durée de manutention")
            continue
        if m_test.duree > cap_productive:
            nonservi(f"Mission trop longue ({m_test.duree:.0f} min) pour un poste "
                     f"(capacité productive {cap_productive:.0f} min)",
                     "Amplitude de poste / durée de la mission")
            continue

        # --- fragmentation ---
        nb_pleins = qte // capa_utile
        for _ in range(nb_pleins):
            cpt += 1
            missions.append(construire_mission_mono(
                f"M{cpt}", v_cap, ps, orig, dest, capa_utile, cont, cont, fsupport, idx,
                h_dispo, h_dead, matrice, matrice_dist, df_vehicules, df_sites, col_quai, col_lib,
                s_u, poids_cont[cont], floor_cap))
        reste = qte % capa_utile
        if reste > 0:
            reliquats.append(dict(flux_id=idx, orig=orig, dest=dest, cont=cont, ps=ps, qte=reste,
                                  fsupport=fsupport, h_dispo=h_dispo, h_dead=h_dead))

    # --- reliquats ---
    if not autoriser_tournees:
        for r in reliquats:
            cpt += 1
            missions.append(_mission_reliquat_mono(cpt, r, v_types, caps, acces, floor,
                                                   surf_cont, poids_cont, matrice, matrice_dist,
                                                   df_vehicules, df_sites, col_quai, col_lib, taux))
    else:
        cpt = _consolider_reliquats(reliquats, missions, cpt, matrice, matrice_dist,
                                    df_vehicules, df_sites, col_quai, col_lib,
                                    v_types, caps, acces, floor, surf_cont, poids_cont,
                                    taux, duree_max_tournee)
    return missions, non_servis


def _mission_reliquat_mono(cpt, r, v_types, caps, acces, floor, surf_cont, poids_cont,
                           matrice, matrice_dist, df_vehicules, df_sites, col_quai, col_lib, taux):
    s_u = surf_cont[r["cont"]]
    v = choisir_vehicule_groupe([r["orig"], r["dest"]], [r["cont"]], r["qte"] * s_u, r["qte"],
                                v_types, caps, acces, floor, taux) or r.get("v_type")
    return construire_mission_mono(
        f"M{cpt}", v, r["ps"], r["orig"], r["dest"], r["qte"], r["cont"], r["cont"],
        r["fsupport"], r["flux_id"], r["h_dispo"], r["h_dead"], matrice, matrice_dist,
        df_vehicules, df_sites, col_quai, col_lib, s_u, poids_cont[r["cont"]], floor[v])


def _fenetre_ok(group):
    return max(r["h_dispo"] for r in group) < min(r["h_dead"] for r in group)


def _duree_tournee_estimee(sites_ordonnes, matrice, t_quai_moy=3.0):
    """Estimation rapide de la durée d'une tournée (trajets + mises à quai)."""
    d = 0.0
    for i in range(len(sites_ordonnes) - 1):
        d += duree_trajet(matrice, sites_ordonnes[i], sites_ordonnes[i + 1])
    d += t_quai_moy * len(sites_ordonnes)
    return d


def _duree_est(ordre, nb, matrice, manut=0.5, quai=3.0):
    """Durée estimée d'une mission (trajets + mises à quai + manutention)."""
    d = sum(duree_trajet(matrice, ordre[i], ordre[i + 1]) for i in range(len(ordre) - 1))
    return d + quai * len(set(ordre)) + nb * manut


def _tournee_faisable(group, ordre, nb, matrice, duree_max):
    """Vrai si la tournée tient dans la fenêtre commune ET sous la durée max."""
    if not _fenetre_ok(group):
        return False
    hd = max(r["h_dispo"] for r in group)
    hf = min(r["h_dead"] for r in group)
    de = _duree_est(ordre, nb, matrice)
    return de <= duree_max and de <= (hf - hd) + 1e-6


def _consolider_reliquats(reliquats, missions, cpt, matrice, matrice_dist,
                          df_vehicules, df_sites, col_quai, col_lib,
                          v_types, caps, acces, floor, surf_cont, poids_cont,
                          taux, duree_max_tournee):
    """Regroupe les reliquats : même-OD, puis DISTRIB (même origine), puis RAMASSE
    (même destination). Respecte capacité (surface), fenêtre et DURÉE MAX TOURNÉE.
    Le véhicule de la tournée est réaffecté au mieux rempli compatible."""
    from collections import defaultdict

    # 1) fusion même-OD (même contenant/ps) sous le seuil de remplissage
    #    ET avec des fenêtres horaires COMPATIBLES (sinon on scinde)
    par_od = defaultdict(list)
    for r in reliquats:
        par_od[(r["orig"], r["dest"], r["cont"], r["ps"])].append(r)
    restants = []
    for (orig, dest, cont, ps), grp in par_od.items():
        s_u = surf_cont[cont]
        v0 = choisir_vehicule_groupe([orig, dest], [cont], s_u, 1, v_types, caps, acces, floor, taux)
        grp.sort(key=lambda r: (r["h_dispo"], r["h_dead"]))
        cur = None

        def flush(c):
            if c:
                restants.append(dict(orig=orig, dest=dest, cont=cont, ps=ps, qte=c["q"],
                                     h_dispo=c["hd"], h_dead=c["hf"], comp=c["comp"],
                                     fsupport=grp[0]["fsupport"]))
        for r in grp:
            if cur is None:
                cur = dict(q=r["qte"], hd=r["h_dispo"], hf=r["h_dead"],
                           comp=[(r["flux_id"], orig, dest, cont, r["qte"])])
                continue
            new_hd, new_hf = max(cur["hd"], r["h_dispo"]), min(cur["hf"], r["h_dead"])
            de = _duree_est([orig, dest], cur["q"] + r["qte"], matrice)
            v_new = choisir_vehicule_groupe(
                [orig, dest], [cont], (cur["q"] + r["qte"]) * s_u,
                cur["q"] + r["qte"], v_types, caps, acces, floor, taux)
            if (v_new is not None
                    and new_hd < new_hf and de <= (new_hf - new_hd) + 1e-6):
                cur["q"] += r["qte"]; cur["hd"], cur["hf"] = new_hd, new_hf
                cur["comp"].append((r["flux_id"], orig, dest, cont, r["qte"]))
            else:
                flush(cur)
                cur = dict(q=r["qte"], hd=r["h_dispo"], hf=r["h_dead"],
                           comp=[(r["flux_id"], orig, dest, cont, r["qte"])])
        flush(cur)

    def emettre_mono(r):
        nonlocal cpt
        cpt += 1
        s_u = surf_cont[r["cont"]]
        v = choisir_vehicule_groupe([r["orig"], r["dest"]], [r["cont"]], r["qte"] * s_u, r["qte"],
                                    v_types, caps, acces, floor, taux)
        comp = r.get("comp") or [(r.get("flux_id"), r["orig"], r["dest"], r["cont"], r["qte"])]
        missions.append(construire_mission_mono(
            f"M{cpt}", v, r["ps"], r["orig"], r["dest"], r["qte"], r["cont"], r["cont"],
            r.get("fsupport", ""), comp[0][0], r["h_dispo"], r["h_dead"], matrice, matrice_dist,
            df_vehicules, df_sites, col_quai, col_lib, s_u, poids_cont[r["cont"]], floor[v]))

    # 2) DISTRIBUTION : même origine + même contenant + même ps
    par_orig = defaultdict(list)
    for r in restants:
        par_orig[(r["orig"], r["cont"], r["ps"])].append(r)
    encore = []
    for (orig, cont, ps), grp in par_orig.items():
        s_u = surf_cont[cont]
        grp.sort(key=lambda r: -r["qte"])
        i = 0
        while i < len(grp):
            tour, q = [grp[i]], grp[i]["qte"]
            sites = {orig, grp[i]["dest"]}
            v = choisir_vehicule_groupe(list(sites), [cont], q * s_u, q, v_types, caps, acces, floor, taux)
            j = i + 1
            while j < len(grp):
                cand = grp[j]
                new_sites = sites | {cand["dest"]}
                ordre = [orig] + [r["dest"] for r in tour + [cand]]
                v_new = choisir_vehicule_groupe(
                    list(new_sites), [cont], (q + cand["qte"]) * s_u,
                    q + cand["qte"], v_types, caps, acces, floor, taux)
                if (v_new is not None
                        and _tournee_faisable(tour + [cand], ordre, q + cand["qte"],
                                              matrice, duree_max_tournee)):
                    tour.append(cand); q += cand["qte"]; sites = new_sites; v = v_new
                j += 1
            for r in tour:
                grp.remove(r)
            if len(tour) >= 2:
                cpt += 1
                vfin = choisir_vehicule_groupe(list(sites), [cont], q * s_u, q,
                                               v_types, caps, acces, floor, taux)
                livr = [(r["dest"], r["qte"]) for r in tour]
                comp = [c for r in tour for c in r["comp"]]
                missions.append(construire_mission_tournee(
                    f"M{cpt}", vfin, ps, [(orig, q)], livr, cont, cont, tour[0]["fsupport"],
                    comp, max(r["h_dispo"] for r in tour), min(r["h_dead"] for r in tour),
                    matrice, matrice_dist, df_vehicules, df_sites, col_quai, col_lib, "DISTRIB",
                    s_u, poids_cont[cont], floor[vfin]))
            else:
                encore.append(tour[0])
            i = 0

    # 3) RAMASSAGE : même destination + même contenant + même ps
    par_dest = defaultdict(list)
    for r in encore:
        par_dest[(r["dest"], r["cont"], r["ps"])].append(r)
    solitaires = []
    for (dest, cont, ps), grp in par_dest.items():
        s_u = surf_cont[cont]
        grp.sort(key=lambda r: -r["qte"])
        i = 0
        while i < len(grp):
            tour, q = [grp[i]], grp[i]["qte"]
            sites = {dest, grp[i]["orig"]}
            v = choisir_vehicule_groupe(list(sites), [cont], q * s_u, q, v_types, caps, acces, floor, taux)
            j = i + 1
            while j < len(grp):
                cand = grp[j]
                new_sites = sites | {cand["orig"]}
                ordre = [r["orig"] for r in tour + [cand]] + [dest]
                v_new = choisir_vehicule_groupe(
                    list(new_sites), [cont], (q + cand["qte"]) * s_u,
                    q + cand["qte"], v_types, caps, acces, floor, taux)
                if (v_new is not None
                        and _tournee_faisable(tour + [cand], ordre, q + cand["qte"],
                                              matrice, duree_max_tournee)):
                    tour.append(cand); q += cand["qte"]; sites = new_sites; v = v_new
                j += 1
            for r in tour:
                grp.remove(r)
            if len(tour) >= 2:
                cpt += 1
                vfin = choisir_vehicule_groupe(list(sites), [cont], q * s_u, q,
                                               v_types, caps, acces, floor, taux)
                coll = [(r["orig"], r["qte"]) for r in tour]
                comp = [c for r in tour for c in r["comp"]]
                missions.append(construire_mission_tournee(
                    f"M{cpt}", vfin, ps, coll, [(dest, q)], cont, cont, tour[0]["fsupport"],
                    comp, max(r["h_dispo"] for r in tour), min(r["h_dead"] for r in tour),
                    matrice, matrice_dist, df_vehicules, df_sites, col_quai, col_lib, "RAMASSE",
                    s_u, poids_cont[cont], floor[vfin]))
            else:
                solitaires.append(tour[0])
            i = 0

    # 4) solitaires -> mono (réaffectés au mieux rempli)
    for r in solitaires:
        emettre_mono(r)
    return cpt


# =====================================================================
# 5. CRÉNEAUX + LISSAGE
# =====================================================================

def _capacite_productive(params):
    rh = params.get("rh", {})
    duree = float(rh.get("amplitude_totale", 450))
    return max(60.0, duree - float(rh.get("temps_fixes_prise", 20))
               - float(rh.get("temps_fixes_fin", 15)) - float(rh.get("pause", 30)))


def calculer_shifts(params):
    rh = params.get("rh", {})
    duree = float(rh.get("amplitude_totale", 450))
    h0 = to_min(rh.get("h_prise_min"), 360)
    h1 = to_min(rh.get("h_fin_max"), 1260)
    n = max(1, int((h1 - h0) // duree))
    return [(h0 + k * duree, h0 + (k + 1) * duree) for k in range(n)], duree


def _shifts_feasibles(m, shifts, matrice, depot, t_prise=20.0):
    approche = duree_trajet(matrice, depot, m.site_debut)
    feas = []
    for k, (s, e) in enumerate(shifts):
        debut = max(m.h_dispo, s + t_prise + approche)
        if debut + m.duree <= min(m.h_deadline, e):
            feas.append(k)
    return feas


def marquer_fenetres_tendues(missions, shifts, matrice, depot, h_fin_max, t_prise=20.0):
    """Détecte les missions incompatibles avec les deux postes fixes."""
    for m in missions:
        if not _shifts_feasibles(m, shifts, matrice, depot, t_prise):
            m.h_deadline = h_fin_max
            m.fenetre_tendue = True


def assigner_shifts(missions, shifts, matrice, depot, h_fin_max, rng=None, t_prise=20.0):
    """Répartit les missions entre créneaux pour lisser la charge. rng != None ->
    ordre aléatoire (multi-start). Ne mute PAS les missions (déjà préparées)."""
    charge = [0.0] * len(shifts)
    assign = {k: [] for k in range(len(shifts))}
    infos = []
    for m in missions:
        feas = _shifts_feasibles(m, shifts, matrice, depot, t_prise) or [0]
        infos.append((m, feas))
    if rng is not None:
        rng.shuffle(infos)
        infos.sort(key=lambda x: len(x[1]))   # contraints d'abord, aléa sur les ex-aequo
    else:
        infos.sort(key=lambda x: len(x[1]))
    for m, feas in infos:
        k = min(feas, key=lambda kk: charge[kk])
        assign[k].append(m); charge[k] += m.duree
    return assign


# =====================================================================
# 6. CONSTRUCTION DES POSTES (chaînage, marge inter-job, pause dépôt)
# =====================================================================

def _besoin_nettoyage(poste, mission):
    return (poste.missions and poste.missions[-1].propre_sale == "SALE"
            and mission.propre_sale == "PROPRE")


def _placer_mission(poste, mission, matrice, matrice_dist, marge_inter, t_nettoyage=15.0):
    # marge inter-job AVANT toute nouvelle mission (sauf la 1ère)
    if poste.missions and marge_inter > 0:
        poste.etapes.append(Etape("MARGE", poste.t_curr, poste.t_curr + marge_inter,
                                   "Marge inter-job"))
        poste.t_curr += marge_inter
    # nettoyage sale -> propre (au dépôt)
    if _besoin_nettoyage(poste, mission):
        if poste.position != poste.depot:
            d = duree_trajet(matrice, poste.position, poste.depot)
            km = dist_trajet(matrice_dist, poste.position, poste.depot)
            poste.etapes.append(Etape("RETOUR_VIDE", poste.t_curr, poste.t_curr + d,
                                      f"Retour dépôt (nettoyage) {poste.position} → {poste.depot}",
                                      site_debut=poste.position, site_fin=poste.depot,
                                      distance=km, a_vide=True))
            poste.t_curr += d; poste.position = poste.depot
        poste.etapes.append(Etape("NETTOYAGE", poste.t_curr, poste.t_curr + t_nettoyage,
                                  "Désinfection (sale → propre)", site_debut=poste.depot,
                                  site_fin=poste.depot))
        poste.t_curr += t_nettoyage
    # approche à vide
    approche = duree_trajet(matrice, poste.position, mission.site_debut)
    if approche > 0.1:
        km = dist_trajet(matrice_dist, poste.position, mission.site_debut)
        poste.etapes.append(Etape("APPROCHE_VIDE", poste.t_curr, poste.t_curr + approche,
                                  f"Approche {poste.position} → {mission.site_debut}",
                                  site_debut=poste.position, site_fin=mission.site_debut,
                                  distance=km, a_vide=True))
        poste.t_curr += approche; poste.position = mission.site_debut
    # attente dispo
    if poste.t_curr < mission.h_dispo:
        poste.etapes.append(Etape("ATTENTE", poste.t_curr, mission.h_dispo,
                                  f"Attente dispo @ {mission.site_debut}",
                                  site_debut=mission.site_debut, site_fin=mission.site_debut))
        poste.t_curr = mission.h_dispo
    # mission
    km_plein = sum(e.get("distance", 0.0) for e in mission.etapes if e["action"] == "TRAJET")
    poste.etapes.append(Etape("MISSION", poste.t_curr, poste.t_curr + mission.duree,
                              f"{mission.site_debut} → {mission.site_fin} "
                              f"({mission.nb_contenants} {mission.libelle})",
                              mission=mission, site_debut=mission.site_debut,
                              site_fin=mission.site_fin, distance=km_plein, a_vide=False))
    poste.t_curr += mission.duree
    poste.position = mission.site_fin
    poste.missions.append(mission)


def _placer_pause(poste, depot, matrice, matrice_dist, pause_duree):
    if poste.position != depot:
        d = duree_trajet(matrice, poste.position, depot)
        if d > 0.1:
            km = dist_trajet(matrice_dist, poste.position, depot)
            poste.etapes.append(Etape("RETOUR_VIDE", poste.t_curr, poste.t_curr + d,
                                      f"Retour dépôt pour pause {poste.position} → {depot}",
                                      site_debut=poste.position, site_fin=depot,
                                      distance=km, a_vide=True))
            poste.t_curr += d; poste.position = depot
    poste.etapes.append(Etape("PAUSE", poste.t_curr, poste.t_curr + pause_duree,
                              "Pause obligatoire (au dépôt)", site_debut=depot, site_fin=depot))
    poste.t_curr += pause_duree
    poste.pause_faite = True


def _placer_pause_si_absorbable(poste, mission, matrice, matrice_dist, pause_duree,
                                marge_inter):
    """Utilise une attente au dépôt pour prendre la pause sans retarder la mission."""
    if poste.pause_faite or poste.position != poste.depot:
        return False
    t_arrivee = poste.t_curr
    if poste.missions:
        t_arrivee += marge_inter
    t_arrivee += duree_trajet(matrice, poste.position, mission.site_debut)
    if mission.h_dispo - t_arrivee + 1e-6 < pause_duree:
        return False
    _placer_pause(poste, poste.depot, matrice, matrice_dist, pause_duree)
    return True


def _cout_successeur(poste, mission, matrice, marge_inter, t_nettoyage):
    t, pos, vide = poste.t_curr, poste.position, 0.0
    if poste.missions and marge_inter > 0:
        t += marge_inter
    if _besoin_nettoyage(poste, mission):
        if pos != poste.depot:
            d = duree_trajet(matrice, pos, poste.depot); t += d; vide += d; pos = poste.depot
        t += t_nettoyage; vide += t_nettoyage
    approche = duree_trajet(matrice, pos, mission.site_debut)
    t += approche; vide += approche
    attente = max(0.0, mission.h_dispo - t)
    t = max(t, mission.h_dispo)
    return t + mission.duree, vide, attente


def _meilleur_successeur(poste, restants, matrice, depot, s_end, t_fin, pause_duree,
                         marge_inter, vers_depot=False, t_nettoyage=15.0, rng=None, jitter=0.0,
                         seuil_critique=30.0, seuil_urgent=90.0, attente_courte=10.0):
    """Choisit le meilleur enchaînement : urgence, faible attente, puis distance.

    L'urgence est mesurée par la marge restante après exécution prévisionnelle.
    La distance à vide ne départage que des missions de priorité comparable.
    """
    best, best_key = None, None
    for m in restants:
        if m.v_type != poste.v_type:
            continue
        if vers_depot and m.site_fin != depot:
            continue
        t_fin_m, vide, attente = _cout_successeur(poste, m, matrice, marge_inter, t_nettoyage)
        if t_fin_m > m.h_deadline:
            continue
        pause_absorbable = (not poste.pause_faite and poste.position == depot
                            and attente + 1e-6 >= pause_duree)
        pause_restante = 0.0 if poste.pause_faite or pause_absorbable else pause_duree
        attente_effective = max(0.0, attente - (pause_duree if pause_absorbable else 0.0))
        retour = duree_trajet(matrice, m.site_fin, depot)
        if t_fin_m + retour + pause_restante + t_fin > s_end:
            continue
        bruit = (rng.random() * jitter) if (rng is not None and jitter) else 0.0
        marge_deadline = max(0.0, m.h_deadline - t_fin_m)
        classe_urgence = (0 if marge_deadline <= seuil_critique
                          else 1 if marge_deadline <= seuil_urgent else 2)
        classe_attente = 0 if attente_effective <= attente_courte else 1
        key = (classe_urgence, classe_attente, round(attente_effective, 1),
               round(marge_deadline, 1), round(vide + bruit, 2), m.h_deadline)
        if best_key is None or key < best_key:
            best_key, best = key, m
    return best


def _penalite_corridor(poste, mission):
    """0 = continuité parfaite, 1 = même activité, 2 = corridor proche, 4 = rupture."""
    if not poste.missions:
        return 2
    precedente = poste.missions[-1]
    if precedente.site_fin == mission.site_debut:
        return 0
    if (precedente.fonction_support and mission.fonction_support
            and precedente.fonction_support == mission.fonction_support):
        return 1
    sites_prec = {precedente.site_debut, precedente.site_fin}
    sites_mission = {mission.site_debut, mission.site_fin}
    return 2 if sites_prec & sites_mission else 4


def _initialiser_poste_fixe(v_type, depot, shift, idx_shift, numero, params):
    s_start, _ = shift
    t_prise = float(params.get("rh", {}).get("temps_fixes_prise", 20))
    p = Poste(id=f"{v_type}_S{idx_shift + 1}_{numero:02d}", v_type=v_type, depot=depot)
    p.shift = idx_shift; p.h_debut = s_start; p.position = depot; p.t_curr = s_start
    p.pause_faite = False
    p.etapes.append(Etape("PRISE", s_start, s_start + t_prise, "Prise de poste",
                          site_debut=depot, site_fin=depot))
    p.t_curr = s_start + t_prise
    return p


def _prendre_pause_centree_si_due(poste, depot, matrice, matrice_dist, pause_duree,
                                  pause_debut):
    if not poste.pause_faite and poste.t_curr >= pause_debut:
        _placer_pause(poste, depot, matrice, matrice_dist, pause_duree)


def _absorber_pause_centree_avant_mission(poste, mission, matrice, matrice_dist,
                                          pause_duree, pause_debut):
    """Prend la pause au dépôt dans une attente déjà inévitable avant mission."""
    if poste.pause_faite or poste.position != poste.depot:
        return False
    approche = duree_trajet(matrice, poste.depot, mission.site_debut)
    if mission.h_dispo < pause_debut + pause_duree + approche - 1e-6:
        return False
    if poste.t_curr < pause_debut:
        poste.etapes.append(Etape("DISPONIBLE", poste.t_curr, pause_debut,
                                  "Temps disponible au dépôt avant pause",
                                  site_debut=poste.depot, site_fin=poste.depot))
        poste.t_curr = pause_debut
    _placer_pause(poste, poste.depot, matrice, matrice_dist, pause_duree)
    return True


def _cloturer_poste_pause_centree(poste, depot, matrice, matrice_dist, params, shift):
    """Clôture avec une pause dont le début reste dans la fenêtre centrale."""
    s_start, s_end = shift
    rh = params.get("rh", {})
    pause_duree = float(rh.get("pause", 30))
    t_fin = float(rh.get("temps_fixes_fin", 15))
    amplitude = s_end - s_start
    centre = s_start + amplitude / 2
    tolerance = float(params.get("tolerance_pause_milieu", 60))
    pause_debut = centre - tolerance
    if not poste.pause_faite:
        if poste.position != depot:
            d = duree_trajet(matrice, poste.position, depot)
            if d > 0.1:
                km = dist_trajet(matrice_dist, poste.position, depot)
                poste.etapes.append(Etape("RETOUR_VIDE", poste.t_curr, poste.t_curr + d,
                                          f"Retour dépôt pour pause {poste.position} → {depot}",
                                          site_debut=poste.position, site_fin=depot,
                                          distance=km, a_vide=True))
                poste.t_curr += d; poste.position = depot
        if poste.t_curr < pause_debut:
            poste.etapes.append(Etape("DISPONIBLE", poste.t_curr, pause_debut,
                                      "Temps disponible au dépôt avant pause",
                                      site_debut=depot, site_fin=depot))
            poste.t_curr = pause_debut
        _placer_pause(poste, depot, matrice, matrice_dist, pause_duree)
    _cloturer_poste(poste, depot, matrice, matrice_dist, t_fin, 0.0, target_end=s_end)


def construire_n_postes_fixes(missions, shift, idx_shift, v_type, n_postes, depot,
                              matrice, matrice_dist, params, rng=None, min_actifs=0):
    """Tente de servir toutes les missions avec au plus N postes au départ fixe."""
    if not missions:
        return []
    s_start, s_end = shift
    rh = params.get("rh", {})
    t_fin = float(rh.get("temps_fixes_fin", 15))
    pause_duree = float(rh.get("pause", 30))
    marge_inter = float(params.get("marge_inter_job", 0))
    seuil_critique = float(params.get("seuil_urgence_critique", 30))
    seuil_urgent = float(params.get("seuil_urgence", 90))
    attente_courte = float(params.get("attente_courte_max", 10))
    tolerance_pause = float(params.get("tolerance_pause_milieu", 60))
    pause_debut = s_start + (s_end - s_start) / 2 - tolerance_pause
    pause_fin = s_start + (s_end - s_start) / 2 + tolerance_pause
    postes = [_initialiser_poste_fixe(v_type, depot, shift, idx_shift, i + 1, params)
              for i in range(n_postes)]
    restants = list(missions)

    while restants:
        candidats = []
        actifs_courants = sum(bool(p.missions) for p in postes)
        for p in postes:
            _prendre_pause_centree_si_due(
                p, depot, matrice, matrice_dist, pause_duree, pause_debut)
            for m in restants:
                t_fin_m, vide, attente = _cout_successeur(p, m, matrice, marge_inter, 15.0)
                if t_fin_m > m.h_deadline + 1e-6:
                    continue
                retour = duree_trajet(matrice, m.site_fin, depot)
                approche = duree_trajet(matrice, p.position, m.site_debut)
                pause_absorbable = (not p.pause_faite and p.position == depot
                                    and m.h_dispo >= pause_debut + pause_duree + approche)
                if (not p.pause_faite and not pause_absorbable
                        and t_fin_m + retour > pause_fin + 1e-6):
                    continue
                pause_restante = 0.0 if p.pause_faite or pause_absorbable else pause_duree
                if t_fin_m + retour + pause_restante + t_fin > s_end + 1e-6:
                    continue
                marge_deadline = max(0.0, m.h_deadline - t_fin_m)
                urgence = (0 if marge_deadline <= seuil_critique
                           else 1 if marge_deadline <= seuil_urgent else 2)
                attente_classe = 0 if attente <= attente_courte else 1
                bruit = rng.random() * 0.01 if rng is not None else 0.0
                priorite_ouverture = 0 if actifs_courants < min_actifs and not p.missions else 1
                key = (priorite_ouverture, urgence, attente_classe, _penalite_corridor(p, m),
                       round(attente, 1), round(vide + bruit, 2), round(marge_deadline, 1))
                candidats.append((key, p, m))
        if not candidats:
            return None
        _, poste, mission = min(candidats, key=lambda x: x[0])
        restants.remove(mission)
        _absorber_pause_centree_avant_mission(
            poste, mission, matrice, matrice_dist, pause_duree, pause_debut)
        _placer_mission(poste, mission, matrice, matrice_dist, marge_inter)

    actifs = [p for p in postes if p.missions]
    for p in actifs:
        _cloturer_poste_pause_centree(p, depot, matrice, matrice_dist, params, shift)
        if p.amplitude > (s_end - s_start) + 1e-6 or not _missions_ok(p, p.missions):
            return None
        pauses = [e.h_debut for e in p.etapes if e.type == "PAUSE"]
        centre = s_start + (s_end - s_start) / 2
        if not pauses or abs(pauses[0] - centre) > tolerance_pause + 1e-6:
            return None
    return actifs


def _repartir_missions_deux_shifts(missions, shifts, matrice, matrice_dist, depot, params):
    """Affecte les missions au shift qui minimise l'attente initiale, sous fenêtres."""
    t_prise = float(params.get("rh", {}).get("temps_fixes_prise", 20))
    repartition = {0: [], 1: []}
    charge = [0.0, 0.0]
    for m in sorted(missions, key=lambda x: (x.h_deadline, x.h_dispo)):
        faisables = [k for k, shift in enumerate(shifts)
                     if construire_n_postes_fixes(
                         [m], shift, k, m.v_type, 1, depot, matrice, matrice_dist, params)
                     is not None]
        if not faisables:
            m.h_deadline = shifts[-1][1]
            m.fenetre_tendue = True
            faisables = [k for k, shift in enumerate(shifts)
                         if construire_n_postes_fixes(
                             [m], shift, k, m.v_type, 1, depot, matrice, matrice_dist, params)
                         is not None]
        if not faisables:
            return None
        couts = []
        for k in faisables:
            s, _ = shifts[k]
            arrivee = s + t_prise + duree_trajet(matrice, depot, m.site_debut)
            attente = max(0.0, m.h_dispo - arrivee)
            couts.append((round(attente, 1), charge[k], k))
        _, _, choisi = min(couts)
        repartition[choisi].append(m)
        charge[choisi] += m.duree
    return repartition


def construire_effectif_minimal_type(missions, shifts, v_type, depot, matrice, matrice_dist,
                                     params, rng=None):
    """Recherche le plus petit N faisable ; après-midi <= N du matin."""
    repartition = _repartir_missions_deux_shifts(
        missions, shifts, matrice, matrice_dist, depot, params)
    if repartition is None:
        return None, None
    cap = _capacite_productive(params)
    borne = max(1, int(math.ceil(sum(m.duree for m in repartition[0]) / cap)),
                int(math.ceil(sum(m.duree for m in repartition[1]) / cap)))
    for n in range(borne, len(missions) + 1):
        apres_midi = construire_n_postes_fixes(
            repartition[1], shifts[1], 1, v_type, n, depot, matrice, matrice_dist, params, rng)
        if apres_midi is None:
            continue
        if not repartition[0]:
            return apres_midi, n
        besoin_matin = len(apres_midi)
        while len(repartition[0]) < besoin_matin:
            transferables = [
                m for m in repartition[1]
                if construire_n_postes_fixes(
                    [m], shifts[0], 0, v_type, 1, depot, matrice, matrice_dist, params)
                is not None]
            if not transferables:
                break
            m = min(transferables, key=lambda x: (x.h_deadline, x.h_dispo))
            repartition[1].remove(m); repartition[0].append(m)
        matin = construire_n_postes_fixes(
            repartition[0], shifts[0], 0, v_type, n, depot, matrice, matrice_dist, params, rng,
            min_actifs=besoin_matin)
        apres_midi = construire_n_postes_fixes(
            repartition[1], shifts[1], 1, v_type, n, depot, matrice, matrice_dist, params, rng)
        if matin is None or apres_midi is None or len(apres_midi) > len(matin):
            continue
        return matin + apres_midi, n
    return None, None


def affecter_vehicules_shifts_fixes(postes):
    """Apparie le poste matin et le poste après-midi de même rang."""
    from collections import defaultdict
    par_type_shift = defaultdict(lambda: defaultdict(list))
    for p in postes:
        par_type_shift[p.v_type][p.shift].append(p)
    nb = {}
    for vt, shifts_type in par_type_shift.items():
        matin = sorted(shifts_type.get(0, []), key=lambda p: p.id)
        apres_midi = sorted(shifts_type.get(1, []), key=lambda p: p.id)
        effectif = max(len(matin), len(apres_midi))
        nb[vt] = effectif
        for i, p in enumerate(matin):
            p.id_vehicule = f"{vt}_VEH{i + 1:02d}"
        for i, p in enumerate(apres_midi):
            p.id_vehicule = f"{vt}_VEH{i + 1:02d}"
    return nb


def construire_postes_creneau(missions, shift, idx_shift, v_type, depot, matrice, matrice_dist,
                              params, num0=0, rng=None):
    s_start, s_end = shift
    rh = params.get("rh", {})
    duree_poste = s_end - s_start
    t_prise = float(rh.get("temps_fixes_prise", 20))
    t_fin = float(rh.get("temps_fixes_fin", 15))
    pause_duree = float(rh.get("pause", 30))
    marge_inter = float(params.get("marge_inter_job", 0))
    seuil_critique = float(params.get("seuil_urgence_critique", 30))
    seuil_urgent = float(params.get("seuil_urgence", 90))
    attente_courte = float(params.get("attente_courte_max", 10))
    jitter = 6.0 if rng is not None else 0.0
    pause_seuil = s_start + t_prise + (duree_poste - t_prise - t_fin) / 2

    postes = []
    restants = sorted(missions, key=lambda m: (
        m.h_deadline - m.h_dispo - m.duree, m.h_deadline, m.h_dispo))
    num = num0
    while restants:
        num += 1
        p = Poste(id=f"{v_type}_S{idx_shift + 1}_{num:02d}", v_type=v_type, depot=depot)
        p.shift = idx_shift; p.h_debut = s_start; p.position = depot; p.t_curr = s_start
        p.pause_faite = False
        p.etapes.append(Etape("PRISE", s_start, s_start + t_prise, "Prise de poste",
                              site_debut=depot, site_fin=depot))
        p.t_curr = s_start + t_prise

        while True:
            if not restants:
                break
            if (not p.pause_faite) and p.t_curr >= pause_seuil:
                if p.position == depot:
                    _placer_pause(p, depot, matrice, matrice_dist, pause_duree); continue
                cand = _meilleur_successeur(p, restants, matrice, depot, s_end, t_fin, pause_duree,
                                            marge_inter, vers_depot=True, rng=rng, jitter=jitter,
                                            seuil_critique=seuil_critique,
                                            seuil_urgent=seuil_urgent,
                                            attente_courte=attente_courte)
                if cand is not None:
                    restants.remove(cand)
                    _placer_pause_si_absorbable(
                        p, cand, matrice, matrice_dist, pause_duree, marge_inter)
                    _placer_mission(p, cand, matrice, matrice_dist, marge_inter)
                    _placer_pause(p, depot, matrice, matrice_dist, pause_duree); continue
                _placer_pause(p, depot, matrice, matrice_dist, pause_duree); continue
            cand = _meilleur_successeur(p, restants, matrice, depot, s_end, t_fin, pause_duree,
                                        marge_inter, rng=rng, jitter=jitter,
                                        seuil_critique=seuil_critique,
                                        seuil_urgent=seuil_urgent,
                                        attente_courte=attente_courte)
            if cand is None:
                if not p.missions:
                    seed = restants.pop(0)
                    _placer_pause_si_absorbable(
                        p, seed, matrice, matrice_dist, pause_duree, marge_inter)
                    _placer_mission(p, seed, matrice, matrice_dist, marge_inter)
                    continue
                break
            restants.remove(cand)
            _placer_pause_si_absorbable(
                p, cand, matrice, matrice_dist, pause_duree, marge_inter)
            _placer_mission(p, cand, matrice, matrice_dist, marge_inter)

        if p.missions:
            _cloturer_poste(p, depot, matrice, matrice_dist, t_fin, pause_duree,
                            target_end=s_start + duree_poste)
            postes.append(p)
    return postes


def _cloturer_poste(poste, depot, matrice, matrice_dist, t_fin, pause_duree, target_end=None):
    """Retour dépôt, pause et fin ; complète le poste jusqu'à l'amplitude contractuelle."""
    if poste.position != depot:
        d = duree_trajet(matrice, poste.position, depot)
        if d > 0.1:
            km = dist_trajet(matrice_dist, poste.position, depot)
            poste.etapes.append(Etape("RETOUR_VIDE", poste.t_curr, poste.t_curr + d,
                                      f"Retour dépôt {poste.position} → {depot}",
                                      site_debut=poste.position, site_fin=depot,
                                      distance=km, a_vide=True))
            poste.t_curr += d; poste.position = depot
    if not poste.pause_faite:
        poste.etapes.append(Etape("PAUSE", poste.t_curr, poste.t_curr + pause_duree,
                                  "Pause obligatoire (au dépôt)", site_debut=depot, site_fin=depot))
        poste.t_curr += pause_duree; poste.pause_faite = True
    if target_end is not None:
        disponible = target_end - poste.t_curr - t_fin
        if disponible > 1e-6:
            poste.etapes.append(Etape("DISPONIBLE", poste.t_curr, poste.t_curr + disponible,
                                      "Temps disponible au dépôt",
                                      site_debut=depot, site_fin=depot))
            poste.t_curr += disponible
    poste.etapes.append(Etape("FIN", poste.t_curr, poste.t_curr + t_fin, "Clôture / fin de poste",
                              site_debut=depot, site_fin=depot))
    poste.t_curr += t_fin
    poste.h_fin = poste.t_curr


# =====================================================================
# 7. COMPTAGE FLOTTE : appariement relève (2 chauffeurs / véhicule)
# =====================================================================

def affecter_vehicules_physiques(postes, params):
    """Conservé pour compat : appariement simple sur la temporisation courante."""
    return _apparier_simple(postes, params)


def _apparier_simple(postes, params):
    rh = params.get("rh", {})
    plage = to_min(rh.get("h_fin_max"), 1260) - to_min(rh.get("h_prise_min"), 360)
    releve = float(params.get("temps_releve", rh.get("temps_releve", 15)))
    from collections import defaultdict
    par_type = defaultdict(list)
    for p in postes:
        par_type[p.v_type].append(p)
    nb_vehicules = {}
    for v_type, liste in par_type.items():
        liste = sorted(liste, key=lambda p: p.h_debut)
        libres, nb = [], 0
        for p in liste:
            cible = None
            for slot in libres:
                fin, amp, vid, _ = slot
                if p.h_debut >= fin + releve and amp + p.amplitude <= plage + 1e-6:
                    if cible is None or fin > cible[0]:
                        cible = slot
            if cible is not None:
                libres.remove(cible); p.id_vehicule = cible[2]
            else:
                nb += 1; p.id_vehicule = f"{v_type}_VEH{nb:02d}"
                libres.append((p.h_fin, p.amplitude, p.id_vehicule, p))
        nb_vehicules[v_type] = nb
    return nb_vehicules


def _feas_starts(p, depot, matrice, matrice_dist, params, h0, h1, amplitude, pas=15):
    miss = list(p.missions)
    feas = []
    for s in range(int(h0), int(h1), pas):
        tmp = _reconstruire_poste(p, miss, depot, matrice, matrice_dist, params, start=s)
        if tmp.amplitude <= amplitude + 1e-6 and _missions_ok(tmp, miss):
            feas.append((s, tmp))
    if not feas:
        feas = [(p.h_debut, _reconstruire_poste(p, miss, depot, matrice, matrice_dist, params))]
    return feas


def _match_fleet(timing, params):
    """timing = liste de (poste, tmp). Apparie au plus 2 postes disjoints (écart
    >= relève, somme amplitudes <= plage) par type via un glouton maximal par
    intervalles. Renvoie (nb_par_type, total, assignation poste->vehicule)."""
    from collections import defaultdict
    rh = params.get("rh", {})
    plage = to_min(rh.get("h_fin_max"), 1260) - to_min(rh.get("h_prise_min"), 360)
    releve = float(params.get("temps_releve", rh.get("temps_releve", 15)))
    par_type = defaultdict(list)
    for p, tmp in timing:
        par_type[p.v_type].append((p, tmp))
    nb, ids = {}, {}
    for vt, items in par_type.items():
        items.sort(key=lambda it: it[1].h_debut)
        avail, vid_count = [], 0
        for p, tmp in items:
            cands = [(q, qt) for (q, qt) in avail
                     if qt.h_fin + releve <= tmp.h_debut
                     and qt.amplitude + tmp.amplitude <= plage + 1e-6]
            if cands:
                q, qt = max(cands, key=lambda it: it[1].h_fin)
                avail.remove((q, qt)); ids[p] = ids[q]
            else:
                vid_count += 1; ids[p] = f"{vt}_VEH{vid_count:02d}"
                avail.append((p, tmp))
        nb[vt] = vid_count
    return nb, sum(nb.values()), ids


def planifier_flotte(postes, depot, matrice, matrice_dist, params, pas=15):
    """
    Co-optimise temporisation des postes et relève. Chaque véhicule effectue au
    plus 2 postes disjoints (écart >= relève) dans la plage horaire.

    On traite les postes les plus contraints (fenêtre de départ étroite) d'abord ;
    pour chacun on tente d'abord la RELÈVE sur un véhicule n'ayant qu'un poste
    (créneau disjoint, en collant au plus près pour laisser de la place ailleurs),
    sinon on ouvre un véhicule au départ le plus tôt faisable. Ce choix garde des
    grappes matin/après-midi nettes (bon appariement) tout en respectant les
    fenêtres horaires de chaque mission. Minimise directement la flotte.
    """
    from collections import defaultdict
    rh = params.get("rh", {})
    h0 = int(to_min(rh.get("h_prise_min"), 360))
    h1 = int(to_min(rh.get("h_fin_max"), 1260))
    amplitude = float(rh.get("amplitude_totale", 450))
    releve = float(params.get("temps_releve", rh.get("temps_releve", 15)))

    par_type = defaultdict(list)
    for p in postes:
        par_type[p.v_type].append(p)

    nb_vehicules = {}
    for vt, liste in par_type.items():
        specs = []
        for p in liste:
            feas = _feas_starts(p, depot, matrice, matrice_dist, params, h0, h1, amplitude, pas)
            specs.append([p, feas, feas[-1][0] - feas[0][0]])
        specs.sort(key=lambda x: x[2])      # postes les plus contraints d'abord
        vehicules = []                       # listes de slots [(h_debut, h_fin, poste, tmp)]
        for p, feas, _w in specs:
            place = False
            for veh in vehicules:
                if len(veh) >= 2:
                    continue
                s0, e0, _, _ = veh[0]
                meilleur = None
                for s, tmp in feas:
                    if s >= e0 + releve or tmp.h_fin + releve <= s0:
                        ecart = (s - e0) if s >= e0 else (s0 - tmp.h_fin)
                        if meilleur is None or ecart < meilleur[0]:
                            meilleur = (ecart, s, tmp)
                if meilleur is not None:
                    _, s, tmp = meilleur
                    veh.append((s, tmp.h_fin, p, tmp)); place = True
                    break
            if not place:
                s, tmp = feas[0]
                vehicules.append([(s, tmp.h_fin, p, tmp)])
        for i, veh in enumerate(vehicules, 1):
            vid = f"{vt}_VEH{i:02d}"
            for (s, e, p, tmp) in veh:
                p.h_debut = tmp.h_debut; p.etapes = tmp.etapes; p.missions = tmp.missions
                p.h_fin = tmp.h_fin; p.position = tmp.position; p.pause_faite = tmp.pause_faite
                p.id_vehicule = vid
        nb_vehicules[vt] = len(vehicules)
    return nb_vehicules


# =====================================================================
# 8. MULTI-START + RECHERCHE LOCALE
# =====================================================================

def _peut_accueillir(poste, mission, matrice, depot, params, amplitude_max):
    """Test : peut-on insérer mission en fin de poste sans dépasser amplitude,
    deadline, et en restant clôturable au dépôt ?"""
    rh = params.get("rh", {})
    t_fin = float(rh.get("temps_fixes_fin", 15))
    pause_duree = float(rh.get("pause", 30))
    marge_inter = float(params.get("marge_inter_job", 0))
    if mission.v_type != poste.v_type:
        return False
    t_fin_m, _, _ = _cout_successeur(poste, mission, matrice, marge_inter, 15.0)
    if t_fin_m > mission.h_deadline:
        return False
    retour = duree_trajet(matrice, mission.site_fin, depot)
    pause_restante = 0.0 if poste.pause_faite else pause_duree
    fin_poste = t_fin_m + retour + pause_restante + t_fin
    return (fin_poste - poste.h_debut) <= amplitude_max + 1e-6


def _reconstruire_poste(poste, missions, depot, matrice, matrice_dist, params, start=None):
    """Reconstruit proprement un poste à partir d'une liste ordonnée de missions
    (compacté au plus tôt à partir de son h_debut, ou de `start` si fourni)."""
    rh = params.get("rh", {})
    t_prise = float(rh.get("temps_fixes_prise", 20))
    t_fin = float(rh.get("temps_fixes_fin", 15))
    pause_duree = float(rh.get("pause", 30))
    marge_inter = float(params.get("marge_inter_job", 0))
    amplitude = float(rh.get("amplitude_totale", 450))
    s_start = poste.h_debut if start is None else start
    p = Poste(id=poste.id, v_type=poste.v_type, depot=depot)
    p.shift = poste.shift; p.h_debut = s_start; p.position = depot; p.t_curr = s_start
    p.pause_faite = False
    p.etapes.append(Etape("PRISE", s_start, s_start + t_prise, "Prise de poste",
                          site_debut=depot, site_fin=depot))
    p.t_curr = s_start + t_prise
    pause_seuil = s_start + t_prise + (amplitude - t_prise - t_fin) / 2
    for m in missions:
        if (not p.pause_faite) and p.t_curr >= pause_seuil:
            _placer_pause(p, depot, matrice, matrice_dist, pause_duree)
        _placer_pause_si_absorbable(
            p, m, matrice, matrice_dist, pause_duree, marge_inter)
        _placer_mission(p, m, matrice, matrice_dist, marge_inter)
    _cloturer_poste(p, depot, matrice, matrice_dist, t_fin, pause_duree,
                    target_end=s_start + amplitude)
    return p


def recherche_locale(postes, depot, matrice, matrice_dist, params, deadline_t):
    """Ferme les postes creux : on tente de vider chaque poste (du moins occupé)
    en réinsérant ses missions dans d'autres postes du même type. Puis on
    compacte. Borné par le temps."""
    amplitude = float(params.get("rh", {}).get("amplitude_totale", 450))
    postes = list(postes)
    ameliore = True
    while ameliore and _time.time() < deadline_t:
        ameliore = False
        postes.sort(key=lambda p: p.occupation())   # le plus creux d'abord
        for source in list(postes):
            if _time.time() >= deadline_t:
                break
            cibles = [p for p in postes if p is not source and p.v_type == source.v_type]
            # ordre des cibles : plus rempli d'abord (CONCENTRER)
            cibles.sort(key=lambda p: -p.occupation())
            plan = {}   # cible -> missions ajoutées
            ok = True
            etat = {c: list(c.missions) for c in cibles}
            for m in source.missions:
                place = False
                for c in cibles:
                    tmp = _reconstruire_poste(c, etat[c] + [m], depot, matrice, matrice_dist, params)
                    if tmp.amplitude <= amplitude + 1e-6 and _missions_ok(tmp, etat[c] + [m]):
                        etat[c].append(m); plan[c] = tmp; place = True
                        break
                if not place:
                    ok = False; break
            if ok:
                for c, miss in etat.items():
                    if c in plan or miss != list(c.missions):
                        rebuilt = _reconstruire_poste(c, miss, depot, matrice, matrice_dist, params)
                        c.etapes = rebuilt.etapes; c.missions = rebuilt.missions
                        c.h_fin = rebuilt.h_fin; c.position = rebuilt.position
                        c.pause_faite = rebuilt.pause_faite
                postes.remove(source)
                ameliore = True
                break
    # compactage final (déjà compacté par reconstruction ; on régénère par sûreté)
    return postes


def _missions_ok(poste, missions):
    """Vérifie que toutes les missions du poste finissent avant leur deadline."""
    for e in poste.etapes:
        if e.type == "MISSION":
            if e.h_fin > e.mission.h_deadline + 1e-6:
                return False
    return True


def reparer_postes_invalides(postes, depot, matrice, matrice_dist, params):
    """Scinde un poste que la reconstruction ne peut plus rendre faisable."""
    amplitude = float(params.get("rh", {}).get("amplitude_totale", 450))
    repares = []
    for p in postes:
        test = _reconstruire_poste(p, list(p.missions), depot, matrice, matrice_dist, params)
        if test.amplitude <= amplitude + 1e-6 and _missions_ok(test, p.missions):
            repares.append(test)
            continue
        groupes = []
        courant = []
        for mission in p.missions:
            candidat = courant + [mission]
            tmp = _reconstruire_poste(p, candidat, depot, matrice, matrice_dist, params)
            if courant and (tmp.amplitude > amplitude + 1e-6 or not _missions_ok(tmp, candidat)):
                groupes.append(courant)
                courant = [mission]
            else:
                courant = candidat
        if courant:
            groupes.append(courant)
        for i, groupe in enumerate(groupes, 1):
            bloc = Poste(id=f"{p.id}_R{i:02d}", v_type=p.v_type, depot=depot)
            bloc.shift = p.shift
            bloc.h_debut = p.h_debut
            repares.append(_reconstruire_poste(
                bloc, groupe, depot, matrice, matrice_dist, params, start=p.h_debut))
    return repares


def retemporiser(postes, depot, matrice, matrice_dist, params, pas=15):
    """Re-temporisation : replace chaque poste dans la journée à l'heure qui
    MINIMISE la concurrence (donc la flotte), tout en respectant les deadlines.
    C'est le "décalage" qui casse la fausse pointe du pavage initial."""
    from collections import defaultdict
    rh = params.get("rh", {})
    h0 = int(to_min(rh.get("h_prise_min"), 360))
    h1 = int(to_min(rh.get("h_fin_max"), 1260))
    amplitude = float(rh.get("amplitude_totale", 450))

    specs = []
    for p in postes:
        miss = list(p.missions)
        feas = []
        for s in range(h0, h1, pas):
            tmp = _reconstruire_poste(p, miss, depot, matrice, matrice_dist, params, start=s)
            if tmp.amplitude <= amplitude + 1e-6 and _missions_ok(tmp, miss):
                feas.append((s, tmp))
        if not feas:
            feas = [(p.h_debut, _reconstruire_poste(p, miss, depot, matrice, matrice_dist, params))]
        specs.append((p, feas))

    # les postes les plus contraints (peu de starts possibles) d'abord
    specs.sort(key=lambda sp: len(sp[1]))
    timeline = defaultdict(int)
    for p, feas in specs:
        best, best_cost = None, None
        for s, tmp in feas:
            peak = 0
            for b in range(int(tmp.h_debut), int(tmp.h_fin), pas):
                peak = max(peak, timeline[b] + 1)
            cost = (peak, s)            # minimiser la pointe, puis au plus tôt
            if best_cost is None or cost < best_cost:
                best_cost, best = cost, (s, tmp)
        s, tmp = best
        for b in range(int(tmp.h_debut), int(tmp.h_fin), pas):
            timeline[b] += 1
        p.h_debut = tmp.h_debut; p.etapes = tmp.etapes; p.missions = tmp.missions
        p.h_fin = tmp.h_fin; p.position = tmp.position; p.pause_faite = tmp.pause_faite
    return postes


def _score_solution(postes, nb_vehicules):
    """Minimise flotte, postes, attente opérationnelle, vide, puis dispersion."""
    flotte = sum(nb_vehicules.values())
    nb_postes = len(postes)
    occ = [p.occupation() for p in postes] or [0]
    moy = sum(occ) / len(occ)
    var = sum((o - moy) ** 2 for o in occ) / len(occ)
    attente = sum(sum(e.duree for e in p.etapes if e.type == "ATTENTE") for p in postes)
    vide = sum(p.temps_vide() for p in postes)
    return (flotte, nb_postes, round(attente, 1), round(vide, 1), var)


# =====================================================================
# 9. POINT D'ENTRÉE
# =====================================================================

def _hhmm(m):
    try:
        return f"{int(m // 60):02d}h{int(m % 60):02d}"
    except Exception:
        return "--:--"


def courbe_concurrence(postes, pas=15):
    if not postes:
        return [], []
    h_min = min(p.h_debut for p in postes); h_max = max(p.h_fin for p in postes)
    bins = list(range(int(h_min), int(h_max) + 1, pas))
    return bins, [sum(1 for p in postes if p.h_debut <= b < p.h_fin) for b in bins]


def concurrence_quais(postes):
    """Nb de véhicules simultanément à quai par site (pour repérer les blocages)."""
    evenements = {}   # site -> liste (t_debut, t_fin)
    for p in postes:
        for e in p.etapes:
            if e.type == "MISSION" and e.mission:
                anchor = e.h_debut
                for et in e.mission.etapes:
                    if et["action"] == "MISE_A_QUAI":
                        evenements.setdefault(et["site"], []).append(
                            (anchor + et["t_debut"], anchor + et["t_fin"]))
    pics = {}
    for site, evs in evenements.items():
        pts = sorted([(t0, 1) for t0, _ in evs] + [(t1, -1) for _, t1 in evs])
        cur = pic = 0
        for _, d in pts:
            cur += d; pic = max(pic, cur)
        pics[site] = pic
    return pics


def optimiser_postes_jour(df_jour, df_vehicules, df_contenants, df_sites,
                          matrice_duree, matrice_dist, params_logistique, nom_jour="Lundi",
                          autoriser_tournees=True, budget_s=60.0, n_starts=8, progress_cb=None):
    """
    Pipeline complet. Renvoie un dict riche pour l'affichage et l'export.
    progress_cb(frac, message) : callback de progression optionnel (0..1).
    """
    def _prog(frac, msg):
        if progress_cb:
            try:
                progress_cb(max(0.0, min(1.0, frac)), msg)
            except Exception:
                pass

    t0 = _time.time()
    alea = float(params_logistique.get("alea_circulation", 0.0))
    matrice = nettoyer_matrice(matrice_duree, alea)
    mdist = nettoyer_matrice_dist(matrice_dist)

    ds = df_sites.copy()
    ds.columns = [str(c).strip() for c in ds.columns]
    col_lib = next((c for c in ds.columns if "libel" in c.lower() or "site" in c.lower()), ds.columns[0])
    col_quai = next((c for c in ds.columns if "quai" in c.lower()), "Présence de quai")
    ds[col_lib] = ds[col_lib].astype(str).str.strip().str.upper()

    v_select = params_logistique.get("vehicules_selectionnes", df_vehicules["Types"].tolist())
    df_v = df_vehicules[df_vehicules["Types"].isin(v_select)].copy()
    v_types = [str(x).strip().upper() for x in df_v["Types"].tolist()]
    depot = (str(df_v["Stationnement initial"].iloc[0]).strip().upper() if not df_v.empty else "HSJ")

    # --- pré-calculs : capacités, accès, surfaces, planchers ---
    _prog(0.02, "Pré-calcul des capacités véhicules × contenants…")
    conts = {str(r["libellé"]).strip(): r for _, r in df_contenants.iterrows()}
    caps, floor = {}, {}
    for _, v in df_v.iterrows():
        vt = str(v["Types"]).strip().upper()
        floor[vt] = surface_sol_vehicule(v)
        caps[vt] = {cn: capacite_max(v, cr) for cn, cr in conts.items()}
    surf_cont = {cn: surface_contenant(cr) for cn, cr in conts.items()}
    poids_cont = {cn: poids_contenant(cr) for cn, cr in conts.items()}
    # table d'accès (site, v_type) -> bool
    acces = {}
    for _, srow in ds.iterrows():
        site = srow[col_lib]
        for vt in v_types:
            col = next((c for c in ds.columns if c.strip().upper() == vt), None)
            acces[(site, vt)] = (col is not None and str(srow.get(col, "")).strip().upper() == "OUI")
    # df_v indexé par type majuscule pour les params manut
    df_v = df_v.copy()
    df_v["Types"] = df_v["Types"].astype(str)

    # --- missions + non servis ---
    _prog(0.08, "Éclatement des flux en missions…")
    missions, non_servis = consolider_missions(
        df_jour, df_v, df_contenants, ds, matrice, mdist, params_logistique,
        col_lib, col_quai, v_types, caps, acces, floor, surf_cont, poids_cont,
        autoriser_tournees=autoriser_tournees)

    shifts, _ = calculer_shifts(params_logistique)
    if len(shifts) != 2:
        raise ValueError("Le dimensionnement fixe nécessite exactement deux postes de 7h30 "
                         "dans la plage d'exploitation.")
    h_fin_max = to_min(params_logistique.get("rh", {}).get("h_fin_max"), 1260)
    t_prise = float(params_logistique.get("rh", {}).get("temps_fixes_prise", 20))
    # détection déterministe des fenêtres tendues (une seule fois)
    marquer_fenetres_tendues(missions, shifts, matrice, depot, h_fin_max, t_prise)
    par_type = {}
    for m in missions:
        par_type.setdefault(m.v_type, []).append(m)

    # --- multi-start ---
    budget = float(budget_s)
    deadline = t0 + budget
    meilleure, meilleur_score, meilleur_nbveh = None, None, None
    n_starts = max(1, int(n_starts))

    for start in range(n_starts):
        if _time.time() >= deadline and meilleure is not None:
            break
        rng = random.Random(1234 + start) if start > 0 else None
        _prog(0.15 + 0.7 * start / n_starts, f"Construction & optimisation (essai {start + 1}/{n_starts})…")
        postes = []
        construction_ok = True
        for v_type, liste in par_type.items():
            construits, _n = construire_effectif_minimal_type(
                liste, shifts, v_type, depot, matrice, mdist, params_logistique, rng=rng)
            if construits is None:
                construction_ok = False
                break
            postes.extend(construits)
        if not construction_ok:
            continue
        nb_veh = affecter_vehicules_shifts_fixes(postes)
        score = _score_solution(postes, nb_veh)
        if meilleur_score is None or score < meilleur_score:
            meilleure, meilleur_score, meilleur_nbveh = postes, score, nb_veh

    postes = meilleure or []
    nb_vehicules = meilleur_nbveh or {}
    _prog(0.92, "Calcul des indicateurs…")

    # marquer les missions tendues (fenêtre relâchée) comme non servies (option b)
    for m in missions:
        if m.fenetre_tendue:
            for (fid, o, d, c, q) in m.composantes:
                non_servis.append({"flux_id": fid, "origine": o, "destination": d,
                                   "contenant": c,
                                   "raison": "Fenêtre relâchée pour pouvoir planifier (incohérence horaire)",
                                   "contrainte": "Heures du flux à vérifier (M flux)"})

    bins, conc = courbe_concurrence(postes)
    pic = max(conc) if conc else 0
    quais = concurrence_quais(postes)

    t_charge = sum(p.temps_charge() for p in postes)
    t_vide = sum(p.temps_vide() for p in postes)
    t_attente = sum(sum(e.duree for e in p.etapes if e.type == "ATTENTE") for p in postes)
    km_plein = sum(e.distance for p in postes for e in p.etapes if e.type == "MISSION")
    km_vide = sum(e.distance for p in postes for e in p.etapes
                  if e.type in ("APPROCHE_VIDE", "RETOUR_VIDE"))
    occ = [p.occupation() for p in postes]
    postes_matin = sum(p.shift == 0 for p in postes)
    postes_apres_midi = sum(p.shift == 1 for p in postes)
    metriques = {
        "nb_missions": len(missions),
        "nb_postes": len(postes),
        "nb_postes_matin": postes_matin,
        "nb_postes_apres_midi": postes_apres_midi,
        "nb_vehicules_total": sum(nb_vehicules.values()),
        "nb_vehicules_par_type": nb_vehicules,
        "pic_vehicules_simultanes": pic,
        "nb_flux_non_servis": len(non_servis),
        "temps_charge_min": round(t_charge),
        "temps_vide_min": round(t_vide),
        "temps_attente_min": round(t_attente),
        "km_plein": round(km_plein, 1),
        "km_vide": round(km_vide, 1),
        "km_total": round(km_plein + km_vide, 1),
        "taux_km_vide": round(km_vide / (km_plein + km_vide) * 100, 1) if (km_plein + km_vide) else 0,
        "taux_charge_global": round(t_charge / (t_charge + t_vide) * 100, 1) if (t_charge + t_vide) else 0,
        "occupation_moyenne": round(sum(occ) / len(occ) * 100, 1) if occ else 0,
        "pic_quais": max(quais.values()) if quais else 0,
        "temps_calcul_s": round(_time.time() - t0, 1),
    }
    _prog(1.0, "Terminé.")
    return {"postes": postes, "missions": missions, "non_servis": non_servis,
            "nb_vehicules": nb_vehicules, "metriques": metriques,
            "concurrence": {"bins": bins, "valeurs": conc}, "quais": quais,
            "jour": nom_jour, "depot": depot, "shifts": shifts}
