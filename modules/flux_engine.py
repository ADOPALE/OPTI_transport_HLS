"""
flux_engine.py
==============
Moteur d'optimisation des tournées de distribution logistique.
Utilise OR-Tools (VRPTW + Pickup & Delivery) pour trouver le plan optimal :
  - Nombre minimal de véhicules par type
  - Nombre minimal de chauffeurs (postes)
  - Horaires de chaque poste

Compatible avec les données de st.session_state issues de Import.py :
  - st.session_state["data"]["m_flux"]          → DataFrame des flux
  - st.session_state["data"]["param_vehicules"]  → DataFrame des véhicules
  - st.session_state["data"]["param_contenants"] → DataFrame des contenants
  - st.session_state["data"]["param_sites"]      → DataFrame des sites
  - st.session_state["data"]["matrice_duree"]    → DataFrame des durées
  - st.session_state["params_logistique"]        → dict des paramètres RH

Installation :
    pip install ortools pandas numpy
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    ORTOOLS_AVAILABLE = True
except Exception:
    pywrapcp = None
    routing_enums_pb2 = None
    ORTOOLS_AVAILABLE = False
    warnings.warn(
        "OR-Tools non disponible. Installez-le : pip install ortools",
        ImportWarning, stacklevel=2
    )

try:
    import streamlit as st
    _ST = True
except Exception:
    st = None
    _ST = False


# ============================================================
# UTILITAIRES
# ============================================================

def _log(msg: str, level: str = "info") -> None:
    """Affiche dans Streamlit si disponible, sinon print."""
    print(msg)
    if not _ST or st is None:
        return
    try:
        if level == "success":
            st.success(msg)
        elif level == "warning":
            st.warning(msg)
        elif level == "error":
            st.error(msg)
        else:
            st.info(msg)
    except Exception:
        pass


def _excel_time_to_minutes(val: Any, default: float = 360.0) -> float:
    """
    Convertit une valeur temporelle Excel en minutes depuis minuit.
    Formats acceptés :
      - float/int  : fraction de journée (0.25 → 360 min = 6h00)
      - str        : "HH:MM" ou "HH:MM:SS"
      - time/datetime : objet Python
    """
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return default
    if hasattr(val, 'hour'):
        return val.hour * 60 + val.minute + val.second / 60
    if isinstance(val, str) and ':' in val:
        parts = val.split(':')
        return int(parts[0]) * 60 + int(parts[1]) + (int(parts[2]) / 60 if len(parts) > 2 else 0)
    try:
        f = float(val)
        return f * 1440  # fraction de journée → minutes
    except (ValueError, TypeError):
        return default


def _norm(s: Any) -> str:
    """
    Normalise un nom de site/véhicule/contenant.
    Supprime les espaces, met en majuscules et retire les accents
    pour éviter les problèmes de correspondance (ex: 'Sté' vs 'STE').
    """
    import unicodedata
    val = str(s).strip().upper()
    # Décomposition unicode puis suppression des caractères de combinaison (accents)
    return ''.join(
        c for c in unicodedata.normalize('NFD', val)
        if unicodedata.category(c) != 'Mn'
    )


# ============================================================
# PHASE 0 — BIN-PACKING 2D (TETRIS / GUILLOTINE CUT)
# ============================================================

def calculer_capacite_max_2d(vehicule: pd.Series, contenant: pd.Series) -> int:
    """
    Calcule le nombre maximum de contenants dans un véhicule par découpe guillotine 2D.
    Prend en compte les deux orientations possibles du contenant.
    Limite ensuite par le poids maximum autorisé.

    Paramètres
    ----------
    vehicule  : ligne du DataFrame param_vehicules
    contenant : ligne du DataFrame param_contenants

    Retourne
    --------
    int  capacité maximale (0 si incompatible)
    """
    v = {_norm(k): v for k, v in vehicule.items()}
    c = {_norm(k): v for k, v in contenant.items()}

    # Vérification compatibilité (colonne = libellé du contenant → "OUI")
    nom_cont = c.get('LIBELLÉ') or c.get('LIBELLE')
    if not nom_cont:
        return 0
    if v.get(_norm(nom_cont)) != 'OUI':
        return 0

    try:
        L_v = float(v['DIM LONGUEUR INTERNE (M)'])
        l_v = float(v['DIM LARGEUR INTERNE (M)'])
        P_max = float(v['POIDS MAX CHARGEMENT'])
        L_c = float(c['DIM LONGUEUR (M)'])
        l_c = float(c['DIM LARGEUR (M)'])
        p_c = float(c['POIDS PLEIN (T)'])
    except (KeyError, ValueError, TypeError):
        return 0

    memo: dict = {}

    def solve(L: float, l: float) -> int:
        if (L < L_c and L < l_c) or (l < L_c and l < l_c):
            return 0
        state = (round(L, 4), round(l, 4))
        if state in memo:
            return memo[state]
        res = 0
        # Orientation normale
        if L >= L_c and l >= l_c:
            res = max(res,
                      1 + solve(L - L_c, l) + solve(L_c, l - l_c),
                      1 + solve(L, l - l_c) + solve(L - L_c, l_c))
        # Orientation pivotée 90°
        if L >= l_c and l >= L_c:
            res = max(res,
                      1 + solve(L - l_c, l) + solve(l_c, l - L_c),
                      1 + solve(L, l - L_c) + solve(L - l_c, L_c))
        memo[state] = res
        return res

    nb_sol = solve(L_v, l_v)
    nb_poids = int(P_max / p_c) if p_c > 0 else nb_sol
    return max(0, min(nb_sol, nb_poids))


def precalculer_capacites(
    df_vehicules: pd.DataFrame,
    df_contenants: pd.DataFrame
) -> dict[str, dict[str, int]]:
    """
    Pré-calcule la matrice de capacités : capacites[v_type][c_type] = N.
    Appelé une seule fois, le résultat est réutilisé par tout le moteur.
    """
    capacites: dict[str, dict[str, int]] = {}
    for _, v in df_vehicules.iterrows():
        v_type = _norm(v.iloc[0])
        capacites[v_type] = {}
        for _, c in df_contenants.iterrows():
            c_type = _norm(c.iloc[0])
            capacites[v_type][c_type] = calculer_capacite_max_2d(v, c)
    return capacites


# ============================================================
# PHASE 1 — DÉCOMPOSITION DES FLUX EN JOBS ÉLÉMENTAIRES
# ============================================================

@dataclass
class JobElementaire:
    """
    Un trajet élémentaire : charger N contenants en A, les livrer en B.
    Correspond à un nœud de pickup (chargement) + un nœud de delivery (livraison)
    dans le modèle OR-Tools Pickup & Delivery.
    """
    job_id: int
    flux_id: int                   # index dans df_flux
    origine: str                   # site de chargement
    destination: str               # site de livraison
    type_contenant: str
    nb_contenants: int             # quantité dans ce job (≤ capacité utile)
    h_dispo: float                 # minutes depuis minuit : au plus tôt pour charger
    h_deadline: float              # minutes depuis minuit : livraison terminée avant
    propre_sale: str               # "PROPRE" ou "SALE"
    v_type_requis: str             # type de véhicule requis (peut être "TOUT")
    est_urgent: bool = False
    surface_sol: float = 0.0       # surface occupée au sol en m² (bin-packing)


def _choisir_vehicule(
    origine: str,
    destination: str,
    type_contenant: str,
    v_type_demande: str,
    df_vehicules: pd.DataFrame,
    df_sites: pd.DataFrame,
    capacites: dict,
    vehicules_autorises: list[str],
    taux_remplissage: float,
) -> tuple[str, int]:
    """
    Choisit le meilleur type de véhicule pour un flux donné.
    Retourne (v_type, capacite_utile).
    Priorité : v_type_demande si fourni et accessible, sinon le plus grand compatible.
    """
    col_lib = next(
        (c for c in df_sites.columns if 'LIBEL' in c.upper() or c.upper() == 'LIBELLÉ'),
        df_sites.columns[0]
    )

    # Mapping colonnes normalisées → colonnes originales dans df_sites
    _cols_sites_norm = {_norm(c): c for c in df_sites.columns}

    def est_accessible(v_nom_norm: str) -> bool:
        """
        Vérifie si le véhicule (type normalisé) peut accéder aux deux sites.
        Cherche la colonne véhicule dans df_sites par correspondance normalisée.
        """
        try:
            row_o = df_sites[df_sites[col_lib].apply(_norm) == _norm(origine)]
            row_d = df_sites[df_sites[col_lib].apply(_norm) == _norm(destination)]
            if row_o.empty or row_d.empty:
                return False
            # Trouver la colonne originale correspondant au type véhicule normalisé
            col_orig = _cols_sites_norm.get(v_nom_norm)
            if col_orig is None:
                return False
            return (str(row_o[col_orig].values[0]).upper() == 'OUI' and
                    str(row_d[col_orig].values[0]).upper() == 'OUI')
        except Exception:
            return False

    # Collecter tous les véhicules compatibles avec leur capacité
    vehicules_compatibles = []  # list of (v_nom, capa)

    for _, v in df_vehicules.iterrows():
        v_nom = _norm(v.iloc[0])
        if v_nom not in vehicules_autorises:
            continue
        if v_type_demande and v_type_demande not in ('', 'NAN', 'NC'):
            if _norm(v_type_demande) not in v_nom and v_nom not in _norm(v_type_demande):
                continue
        if not est_accessible(v_nom):
            continue
        capa = capacites.get(v_nom, {}).get(_norm(type_contenant), 0)
        if capa > 0:
            vehicules_compatibles.append((v_nom, capa))

    if not vehicules_compatibles:
        return '', 0

    # Stratégie : choisir le véhicule le plus capacitaire compatible.
    # → minimise le nombre de trajets pour transporter le flux.
    vehicules_compatibles.sort(key=lambda x: x[1], reverse=True)
    meilleur_type, meilleure_capa = vehicules_compatibles[0]

    capa_utile = max(1, math.floor(meilleure_capa * taux_remplissage))
    return meilleur_type, capa_utile


def decomposer_flux_en_jobs(
    df_flux: pd.DataFrame,
    df_vehicules: pd.DataFrame,
    df_contenants: pd.DataFrame,
    df_sites: pd.DataFrame,
    df_contenants_indexed: dict,
    capacites: dict,
    params_logistique: dict,
    jour: str = "Lundi",
) -> list[JobElementaire]:
    """
    Transforme chaque ligne du tableau M flux en N jobs élémentaires
    en tenant compte de la capacité utile du véhicule.

    Paramètres
    ----------
    df_flux             : onglet M flux
    df_vehicules        : onglet param Véhicules
    df_contenants       : onglet param Contenants
    df_sites            : onglet param Sites
    df_contenants_indexed : dict {c_type: Series} pour accès rapide aux dimensions
    capacites           : sortie de precalculer_capacites()
    params_logistique   : dict de st.session_state["params_logistique"]
    jour                : "Lundi", "Mardi", ..., "Dimanche"

    Retourne
    --------
    list[JobElementaire]
    """
    rh = params_logistique.get('rh', {})
    h_debut_defaut = _excel_time_to_minutes(rh.get('h_prise_min'), 360.0)
    h_fin_defaut   = _excel_time_to_minutes(rh.get('h_fin_max'),   1260.0)
    taux_remplissage = params_logistique.get('securite_remplissage', 0.85)
    vehicules_autorises = [_norm(v) for v in params_logistique.get('vehicules_selectionnes', [])]

    col_qte = f'Quantité {jour}'
    jobs: list[JobElementaire] = []
    job_id = 0

    for flux_id, row in df_flux.iterrows():
        # Filtrage nature du flux : on ne traite que les flux "Volume"
        nature = str(row.get(
            "Nature du flux (les tournées sont elles à prévoir avec une obligation de transport ou une obligation de passage?)",
            "Volume"
        )).strip()
        if nature.lower() not in ('volume', 'nan', ''):
            continue

        # Quantité du jour
        try:
            raw_qte = row.get(col_qte, 0)
            qte = 0.0 if (raw_qte is None or (isinstance(raw_qte, float) and math.isnan(raw_qte))) else float(raw_qte)
        except (ValueError, TypeError):
            qte = 0.0
        if qte <= 0:
            continue

        origine      = _norm(row.get('Point de départ', ''))
        destination  = _norm(row.get('Point de destination', ''))
        type_cont    = _norm(row.get('Nature de contenant', ''))
        propre_sale  = _norm(row.get('Sale / propre', 'PROPRE'))
        type_cont    = _norm(row.get('Nature de contenant', ''))
        propre_sale  = _norm(row.get('Sale / propre', 'PROPRE'))
        est_urgent   = str(row.get('Urgence / flux prioritaire   (Oui/Non)', 'Non')).upper() == 'OUI'

        # ── Fenêtres horaires ─────────────────────────────────────────────────
        # Col 26 = "Heure de mise à disposition min départ" → h_dispo (toujours renseignée)
        # Col 27 = "Type de transporteur" → contient en réalité des heures de fin
        #          (décalage de colonne dans l'Excel)
        # Col 21 = "Plage horaire fin" → seulement 8 lignes renseignées, utilisée en fallback
        import datetime as _dt
        _col_dispo    = row.get('Heure de mise à disposition min départ', None)
        _col_deadline = row.get('Heure max de livraison à la destination', None)
        _col_vtype    = row.get('Type de transporteur (camion VL frigo)', None)

        h_dispo    = _excel_time_to_minutes(_col_dispo,    h_debut_defaut)
        h_deadline = _excel_time_to_minutes(_col_deadline, h_fin_defaut)                      if _col_deadline is not None and str(_col_deadline) not in ('nan', '')                      else h_fin_defaut

        # Type de transporteur (ignorer si c'est une heure par erreur de saisie)
        if isinstance(_col_vtype, (_dt.time, _dt.datetime)):
            v_type_req = ''
        else:
            v_type_req = _norm(str(_col_vtype or ''))

        if h_deadline <= h_dispo:
            h_deadline = h_dispo + 120

        # Sélection du véhicule et capacité
        v_type, capa_utile = _choisir_vehicule(
            origine, destination, type_cont, v_type_req,
            df_vehicules, df_sites, capacites,
            vehicules_autorises, taux_remplissage
        )
        if not v_type or capa_utile <= 0:
            warnings.warn(
                f"Flux {flux_id} ({origine}→{destination}, {type_cont}) : "
                f"aucun véhicule compatible trouvé parmi {vehicules_autorises}. "
                f"Capacités disponibles : { {v: capacites.get(v,{}).get(_norm(type_cont),0) for v in vehicules_autorises} }",
                RuntimeWarning, stacklevel=2
            )
            continue

        # Surface au sol du contenant (pour info / affichage)
        cont_info = df_contenants_indexed.get(_norm(type_cont))
        surface_unit = 0.0
        if cont_info is not None:
            try:
                surface_unit = float(cont_info.get('DIM LONGUEUR (M)', 0)) * \
                               float(cont_info.get('DIM LARGEUR (M)', 0))
            except Exception:
                pass

        # Décomposition en jobs élémentaires
        nb_jobs = math.ceil(qte / capa_utile)
        for k in range(nb_jobs):
            nb_cont_job = capa_utile if k < nb_jobs - 1 else (int(qte) - k * capa_utile)
            jobs.append(JobElementaire(
                job_id=job_id,
                flux_id=flux_id,
                origine=origine,
                destination=destination,
                type_contenant=_norm(type_cont),
                nb_contenants=nb_cont_job,
                h_dispo=h_dispo,
                h_deadline=h_deadline,
                propre_sale=propre_sale,
                v_type_requis=v_type,
                est_urgent=est_urgent,
                surface_sol=surface_unit * nb_cont_job,
            ))
            job_id += 1

    return jobs


# ============================================================
# PHASE 1b — REGROUPEMENT DES JOBS INCOMPLETS EN SUPER-JOBS
# ============================================================

@dataclass
class SuperJob:
    """
    Groupe de jobs incomplets compatibles.
    Trois formes possibles :
      - SIMPLE    : job complet, un seul nœud VRP (pas de PDPTW)
      - COLLECTE  : plusieurs pickups → un seul delivery
      - DISTRIB   : un seul pickup → plusieurs deliveries
      - GROUPAGE  : même origine+destination, on charge plus
    """
    super_id    : int
    forme       : str           # 'SIMPLE'|'COLLECTE'|'DISTRIB'|'GROUPAGE'
    jobs        : list          # list[JobElementaire]
    origines    : list[str]     # sites de chargement
    destinations: list[str]     # sites de livraison
    nb_total    : int           # contenants totaux
    h_dispo     : float         # max des h_dispo des jobs (fenêtre commune)
    h_deadline  : float         # min des h_deadline des jobs
    propre_sale : str
    v_type      : str


def _fenetres_compatibles(j1: 'JobElementaire', j2: 'JobElementaire') -> bool:
    """Vérifie que les fenêtres de deux jobs se chevauchent (Option A)."""
    return max(j1.h_dispo, j2.h_dispo) < min(j1.h_deadline, j2.h_deadline)


def _compatible(j1: 'JobElementaire', j2: 'JobElementaire') -> bool:
    """Vérifie les 3 règles de compatibilité pour le regroupement."""
    return (
        j1.propre_sale  == j2.propre_sale   # règle 1 : propre/sale
        and j1.v_type_requis == j2.v_type_requis  # règle 2 : véhicule
        and _fenetres_compatibles(j1, j2)         # règle 3 : fenêtres
    )


def regrouper_jobs_incomplets(
    jobs_incomplets: list,
    capa_utile: int,
) -> list:
    """
    Regroupe les jobs incomplets en super-jobs selon les 3 règles métier.

    Stratégie de regroupement :
    1. GROUPAGE PUR  : même origine ET même destination → on fusionne
    2. COLLECTE      : même destination, origines différentes → on collecte
    3. DISTRIBUTION  : même origine, destinations différentes → on distribue
    4. SIMPLE        : job seul (pas de regroupement possible)

    La contrainte de capacité est respectée : nb_total ≤ capa_utile.

    Retourne list[SuperJob].
    """
    non_traites = list(jobs_incomplets)
    super_jobs  = []
    sid         = 0

    def _make_super(jobs, forme):
        nonlocal sid
        h_d = max(j.h_dispo    for j in jobs)
        h_l = min(j.h_deadline for j in jobs)
        sj = SuperJob(
            super_id    = sid,
            forme       = forme,
            jobs        = jobs,
            origines    = list({j.origine     for j in jobs}),
            destinations= list({j.destination for j in jobs}),
            nb_total    = sum(j.nb_contenants for j in jobs),
            h_dispo     = h_d,
            h_deadline  = h_l,
            propre_sale = jobs[0].propre_sale,
            v_type      = jobs[0].v_type_requis,
        )
        sid += 1
        return sj

    while non_traites:
        j0 = non_traites.pop(0)
        groupe = [j0]
        cap_restante = capa_utile - j0.nb_contenants

        # Chercher des jobs compatibles à regrouper
        i = 0
        while i < len(non_traites) and cap_restante > 0:
            j = non_traites[i]
            if (not _compatible(j0, j)):
                i += 1
                continue
            if j.nb_contenants > cap_restante:
                i += 1
                continue

            # Vérifier compatibilité avec tout le groupe courant
            if all(_compatible(jg, j) for jg in groupe):
                groupe.append(j)
                cap_restante -= j.nb_contenants
                non_traites.pop(i)
            else:
                i += 1

        # Déterminer la forme du super-job
        origines    = {j.origine     for j in groupe}
        destinations= {j.destination for j in groupe}

        if len(origines) == 1 and len(destinations) == 1:
            forme = 'GROUPAGE'
        elif len(origines) > 1 and len(destinations) == 1:
            forme = 'COLLECTE'
        elif len(origines) == 1 and len(destinations) > 1:
            forme = 'DISTRIB'
        else:
            forme = 'SIMPLE'  # mixte non optimisable, on garde séparé

        if forme == 'SIMPLE' and len(groupe) > 1:
            # Pas de forme claire : créer des super-jobs individuels
            for j in groupe:
                super_jobs.append(_make_super([j], 'SIMPLE'))
        else:
            super_jobs.append(_make_super(groupe, forme))

    return super_jobs


def preparer_liste_unifiee(
    jobs_v      : list,
    capa_utile  : int,
) -> tuple[list, list]:
    """
    Sépare les jobs complets et incomplets, regroupe les incomplets,
    retourne (jobs_complets, super_jobs_incomplets).

    Les jobs complets sont renvoyés tels quels.
    Les super-jobs incomplets sont regroupés selon les règles métier.
    """
    jobs_complets   = [j for j in jobs_v if j.nb_contenants >= capa_utile]
    jobs_incomplets = [j for j in jobs_v if j.nb_contenants <  capa_utile]

    super_jobs = regrouper_jobs_incomplets(jobs_incomplets, capa_utile)

    return jobs_complets, super_jobs


# ============================================================
# PHASE 2 — MODÈLE OR-TOOLS
# ============================================================

SCALE = 10  # précision à 0.1 minute


def _sc(minutes: float) -> int:
    """Convertit des minutes en entier scalé pour OR-Tools."""
    return int(round(minutes * SCALE))


def _build_model_data(
    jobs_complets : list,
    super_jobs    : list,
    matrice_duree : pd.DataFrame,
    capacites     : dict,
    params_logistique: dict,
    v_type        : str,
    alea          : float = 0.0,
    df_vehicules  : "pd.DataFrame | None" = None,
    df_sites      : "pd.DataFrame | None" = None,
) -> dict:
    """
    Construit le modèle OR-Tools mixte VRP + PDPTW.

    Nœuds :
      0                              = dépôt (HLS)
      1 .. N_c                       = jobs complets (nœuds simples VRP)
      N_c+1, N_c+2                   = pickup_sj0, delivery_sj0
      N_c+3, N_c+4                   = pickup_sj1, delivery_sj1
      ...
    """
    if not jobs_complets and not super_jobs:
        return {}

    rh           = params_logistique.get('rh', {})
    amplitude_max= float(rh.get('temps_productif_max') or rh.get('amplitude_totale', 450))
    temps_nettoyage = float(rh.get('temps_fixes_fin', 15))
    h_debut      = _excel_time_to_minutes(rh.get('h_prise_min'), 360.0)
    h_fin        = _excel_time_to_minutes(rh.get('h_fin_max'),   1260.0)
    facteur      = 1 + alea

    # ── Matrice de durées ─────────────────────────────────────────────────
    df = matrice_duree.copy()
    col0 = df.columns[0]
    df = df.set_index(col0)
    df.index   = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    def duree(a, b):
        if _norm(a) == _norm(b): return 0
        try:
            return _sc(float(df.loc[_norm(a), _norm(b)]) * facteur)
        except Exception:
            return _sc(30 * facteur)

    # ── Paramètres manutention ────────────────────────────────────────────
    def _time_val_to_min(val, default=0.0):
        if val is None or (isinstance(val, float) and math.isnan(val)): return default
        if hasattr(val, 'hour'): return val.hour*60 + val.minute + val.second/60
        try: return float(val)*1440
        except: return default

    t_quai_min  = 10.0
    t_sans_quai = 25/60
    t_avec_quai = 15/60
    if df_vehicules is not None:
        veh_row = df_vehicules[df_vehicules.iloc[:,0].apply(_norm) == _norm(v_type)]
        if not veh_row.empty:
            r = veh_row.iloc[0]
            t_quai_min  = _time_val_to_min(r.get('Temps de mise à quai - manœuvre, contact/admin (minutes)'), 10.0)
            t_sans_quai = _time_val_to_min(r.get('Manutention sans quai (minutes / contenants)'), 25/60)
            t_avec_quai = _time_val_to_min(r.get('Manutention avec quai (minutes / contenants)'), 15/60)

    sites_avec_quai = set()
    if df_sites is not None:
        col_lib = next((c for c in df_sites.columns if 'libel' in c.lower()), df_sites.columns[0])
        for _, row in df_sites.iterrows():
            if str(row.get('Présence de quai','NON')).upper() == 'OUI':
                sites_avec_quai.add(_norm(str(row[col_lib])))

    taux_rempl   = params_logistique.get('securite_remplissage', 0.85)
    capa_brute_v = max(
        (capacites.get(_norm(v_type), {}).get(j.type_contenant, 1) for j in jobs_complets)
        if jobs_complets else [1],
        default=1
    )
    if super_jobs:
        capa_brute_v = max(capa_brute_v, max(
            (capacites.get(_norm(v_type), {}).get(j.type_contenant, 1)
             for sj in super_jobs for j in sj.jobs),
            default=1
        ))
    vehicle_capacity = max(1, math.floor(capa_brute_v * taux_rempl))

    # ── Construction des nœuds ────────────────────────────────────────────
    # Tous les items (jobs complets ET super-jobs) sont modélisés comme
    # des paires PDPTW (pickup, delivery) pour garantir des tournées réalisables.
    #
    # Dépôt = nœud 0
    # Job complet i  → pickup = 1+2i,   delivery = 2+2i
    # Super-job j    → pickup = 1+2(N_c+j), delivery = 2+2(N_c+j)
    depot      = 0
    depot_site = "HLS"

    N_c = len(jobs_complets)
    N_s = len(super_jobs)
    n_nodes = 1 + 2 * (N_c + N_s)  # dépôt + 2 nœuds par item

    # Sites par nœud
    node_sites = [depot_site]
    for j in jobs_complets:
        node_sites.append(j.origine)      # pickup
        node_sites.append(j.destination)  # delivery
    for sj in super_jobs:
        node_sites.append(sj.origines[0])      # pickup
        node_sites.append(sj.destinations[0])  # delivery

    # ── Offset inter-job ──────────────────────────────────────────────────
    all_nb = ([j.nb_contenants for j in jobs_complets] +
               [sj.nb_total for sj in super_jobs])
    nb_moy = sum(all_nb) / max(1, len(all_nb)) if all_nb else 1.0
    sites_jobs_list = node_sites[1:]
    prop_quai = sum(1 for s in sites_jobs_list if _norm(s) in sites_avec_quai) / max(1, len(sites_jobs_list))
    t_manu_moy = nb_moy * (t_avec_quai * prop_quai + t_sans_quai * (1-prop_quai))
    offset_min = t_quai_min * 2 + t_manu_moy * 2
    offset_sc  = _sc(offset_min)
    demi_offset_sc = offset_sc // 2

    _log(
        f"  ⏱️ Offset inter-job {v_type} : {offset_min:.1f} min "
        f"(t_quai×2={t_quai_min*2:.0f}, t_manu×2={t_manu_moy*2:.1f}, "
        f"nb_moy={nb_moy:.0f}, prop_quai={prop_quai:.0%})",
        "info"
    )

    # ── Matrice de durées avec offset ────────────────────────────────────
    node_is_pickup_loc = [False]                    # dépôt
    for _ in jobs_complets:
        node_is_pickup_loc += [True, False]         # pickup, delivery
    for _ in super_jobs:
        node_is_pickup_loc += [True, False]         # pickup, delivery

    time_matrix = [[0]*n_nodes for _ in range(n_nodes)]
    for i in range(n_nodes):
        for k in range(n_nodes):
            if i == k: continue
            base = duree(node_sites[i], node_sites[k])
            is_from_delivery = (i > 0 and not node_is_pickup_loc[i])
            is_from_depot    = (i == 0)
            is_to_depot      = (k == 0)
            if is_from_delivery and not is_to_depot:
                time_matrix[i][k] = base + offset_sc
            elif is_from_delivery and is_to_depot:
                time_matrix[i][k] = base + demi_offset_sc
            elif is_from_depot:
                time_matrix[i][k] = base + demi_offset_sc
            else:
                time_matrix[i][k] = base

    # ── Fenêtres temporelles ─────────────────────────────────────────────
    time_windows = [(_sc(h_debut), _sc(h_fin))]
    # Tous les items : pickup et delivery ont la fenêtre [h_dispo, h_deadline]
    for j in jobs_complets:
        time_windows.append((_sc(j.h_dispo), _sc(j.h_deadline)))  # pickup
        time_windows.append((_sc(j.h_dispo), _sc(j.h_deadline)))  # delivery
    for sj in super_jobs:
        time_windows.append((_sc(sj.h_dispo), _sc(sj.h_deadline)))  # pickup
        time_windows.append((_sc(sj.h_dispo), _sc(sj.h_deadline)))  # delivery

    # ── Demandes de capacité ──────────────────────────────────────────────
    demands = [0]
    # Jobs complets : demands = +nb_contenants au pickup, -nb_contenants à la delivery
    # Cela bloque tout autre chargement pendant le trajet (camion plein)
    for j in jobs_complets:
        demands.append(j.nb_contenants)   # pickup
        demands.append(-j.nb_contenants)  # delivery
    for sj in super_jobs:
        demands.append(sj.nb_total)    # pickup
        demands.append(-sj.nb_total)   # delivery

    # ── Paires pickup/delivery (super-jobs uniquement) ───────────────────
    pickups_deliveries = []
    # Jobs complets
    for idx in range(N_c):
        pickup_node   = 1 + 2*idx
        delivery_node = 2 + 2*idx
        pickups_deliveries.append((pickup_node, delivery_node))
    # Super-jobs
    for idx in range(N_s):
        pickup_node   = 1 + 2*N_c + 2*idx
        delivery_node = 2 + 2*N_c + 2*idx
        pickups_deliveries.append((pickup_node, delivery_node))

    # ── propre_sale par nœud ──────────────────────────────────────────────
    propre_sale_par_noeud = ['']
    for j in jobs_complets:
        propre_sale_par_noeud.extend([j.propre_sale, j.propre_sale])
    for sj in super_jobs:
        propre_sale_par_noeud.extend([sj.propre_sale, sj.propre_sale])

    # ── Nmax par pic de charge ────────────────────────────────────────────
    RESOLUTION = 15
    slots      = max(1, int((h_fin - h_debut) / RESOLUTION) + 1)
    charge_par_slot = [0.0] * slots

    all_items = (
        [(j.h_dispo, j.h_deadline,
          (time_matrix[0][1+2*i]
           + time_matrix[1+2*i][2+2*i]
           + time_matrix[2+2*i][0]) / SCALE)
         for i, j in enumerate(jobs_complets)]
        +
        [(sj.h_dispo, sj.h_deadline,
          (time_matrix[0][1+2*N_c+2*si]
           + time_matrix[1+2*N_c+2*si][2+2*N_c+2*si]
           + time_matrix[2+2*N_c+2*si][0]) / SCALE)
         for si, sj in enumerate(super_jobs)]
    )

    for tw_open, tw_close, duree_min in all_items:
        plage  = max(tw_close - tw_open, RESOLUTION)
        contrib = (duree_min / plage) * RESOLUTION
        slot_s  = max(0, int((tw_open  - h_debut) / RESOLUTION))
        slot_e  = min(slots-1, int((tw_close - h_debut) / RESOLUTION))
        for s in range(slot_s, slot_e+1):
            charge_par_slot[s] += contrib

    pic_charge     = max(charge_par_slot) if charge_par_slot else 1.0
    nmax_theorique = max(1, math.ceil(pic_charge / RESOLUTION))
    nmax           = min(1 + N_c + N_s, nmax_theorique * 2)

    # ── Mapping nœud → job ───────────────────────────────────────────────
    node_to_item   = [None]   # dépôt
    node_is_simple = [False]  # dépôt
    jobs_list      = []

    for i, j in enumerate(jobs_complets):
        node_to_item.extend([('complet', i), ('complet', i)])
        node_is_simple.extend([False, False])
        jobs_list.append(j)

    for si, sj in enumerate(super_jobs):
        node_to_item.extend([('super_pickup', si), ('super_delivery', si)])
        node_is_simple.extend([False, False])
        for j in sj.jobs:
            jobs_list.append(j)

    return {
        'n_nodes'            : n_nodes,
        'n_vehicles'         : nmax,
        'nmax'               : nmax,
        'nmax_theorique'     : nmax_theorique,
        'pic_charge_min'     : round(pic_charge, 1),
        'depot'              : depot,
        'time_matrix'        : time_matrix,
        'time_windows'       : time_windows,
        'pickups_deliveries' : pickups_deliveries,
        'demands'            : demands,
        'vehicle_capacity'   : vehicle_capacity,
        'max_poste_sc'       : _sc(amplitude_max),
        'h_debut_sc'         : _sc(h_debut),
        'h_fin_sc'           : _sc(h_fin),
        'jobs'               : jobs_list,
        'jobs_complets'      : jobs_complets,
        'super_jobs'         : super_jobs,
        'node_sites'         : node_sites,
        'node_to_item'       : node_to_item,
        'n_complets'         : N_c,  # nb de jobs complets (paires PDPTW)
        'node_is_pickup'     : node_is_pickup_loc,
        'node_is_simple'     : node_is_simple,
        'propre_sale_par_noeud': propre_sale_par_noeud,
        'temps_nettoyage_sc' : _sc(temps_nettoyage),
        'service_times'      : [0] * n_nodes,
        'SCALE'              : SCALE,
    }


def _solve_type(data: dict, time_limit_seconds: int = 60) -> dict | None:
    """
    Résout le PDPTW pour un type de véhicule avec OR-Tools.
    Ordre correct des opérations :
      1. Manager + RoutingModel
      2. Callback transit avec nettoyage (propre/sale)
      3. Dimension temporelle (basée sur ce callback)
      4. Fenêtres temporelles sur les nœuds
      5. Amplitude max par poste
      6. Capacité
      7. Contraintes Pickup & Delivery
      8. Objectif
      9. Résolution
    """
    if not data:
        return None

    SCALE      = data['SCALE']
    n_nodes    = data['n_nodes']
    n_vehicles = data['n_vehicles']
    depot      = data['depot']

    manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)

    # ── 1. Callback transit avec temps de nettoyage sale→propre ────────────
    ps             = data['propre_sale_par_noeud']
    node_is_pickup = data['node_is_pickup']
    T_NET          = data['temps_nettoyage_sc']

    def transit_avec_nettoyage(from_idx, to_idx):
        from_node = manager.IndexToNode(from_idx)
        to_node   = manager.IndexToNode(to_idx)
        # Transit = time_matrix[from][to]
        # (inclut déjà l'offset de manutention sur les arcs entre sites)
        transit = data['time_matrix'][from_node][to_node]
        # Nettoyage si delivery SALE → pickup PROPRE
        if (from_node > 0 and not node_is_pickup[from_node]
                and ps[from_node] == 'SALE'
                and to_node > 0 and node_is_pickup[to_node]
                and ps[to_node] == 'PROPRE'):
            return transit + T_NET
        return transit

    cb_transit = routing.RegisterTransitCallback(transit_avec_nettoyage)
    routing.SetArcCostEvaluatorOfAllVehicles(cb_transit)

    # ── 2. Dimension temporelle (doit être créée AVANT d'appliquer les TW) ─
    routing.AddDimension(
        cb_transit,
        slack_max=_sc(120),
        capacity=_sc(1440),
        fix_start_cumul_to_zero=False,
        name='Time'
    )
    time_dim = routing.GetDimensionOrDie('Time')

    # ── 3. Fenêtres temporelles sur les nœuds ──────────────────────────────
    for node in range(1, n_nodes):
        idx = manager.NodeToIndex(node)
        tw  = data['time_windows'][node]
        time_dim.CumulVar(idx).SetRange(tw[0], tw[1])

    # Fenêtres du dépôt (départ et retour)
    for v in range(n_vehicles):
        time_dim.CumulVar(routing.Start(v)).SetRange(
            data['h_debut_sc'], data['h_fin_sc']
        )
        time_dim.CumulVar(routing.End(v)).SetRange(
            data['h_debut_sc'], data['h_fin_sc']
        )

    # ── 4. Amplitude : gérée en post-traitement uniquement ─────────────────
    # OR-Tools peut planifier sur toute la journée (6h→21h).
    # construire_postes découpe ensuite chaque route en postes de
    # amplitude_max minutes avec relève entre chauffeurs.
    solver = routing.solver()

    # ── 5. Capacité par trajet individuel ───────────────────────────────────
    # Dans un PDPTW, la dimension cumulative classique est problématique :
    # si plusieurs pickups précèdent leurs deliveries, le cumul monte bien
    # au-delà de la capacité physique du véhicule.
    # Solution : contraindre UNIQUEMENT que pickup + delivery = 0 net,
    # ce qui est déjà garanti par AddPickupAndDelivery.
    # La vraie contrainte physique est : à tout instant, le chargement
    # effectif ≤ vehicle_capacity. On l'approche en limitant les demandes
    # à 0 (balance nulle) via la dimension avec max=vehicle_capacity
    # et start_cumul_to_zero=True.
    def demand_callback(from_idx):
        node = manager.IndexToNode(from_idx)
        return data['demands'][node]

    cb_demand = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        cb_demand,
        0,                                          # pas de slack
        [data['vehicle_capacity']] * n_vehicles,    # capacité physique
        True,                                       # start cumul = 0
        'Capacity'
    )

    # ── 6. Contraintes Pickup & Delivery ───────────────────────────────────
    for pickup_node, delivery_node in data['pickups_deliveries']:
        pickup_idx   = manager.NodeToIndex(pickup_node)
        delivery_idx = manager.NodeToIndex(delivery_node)
        routing.AddPickupAndDelivery(pickup_idx, delivery_idx)
        solver.Add(
            routing.VehicleVar(pickup_idx) == routing.VehicleVar(delivery_idx)
        )
        solver.Add(
            time_dim.CumulVar(pickup_idx) <= time_dim.CumulVar(delivery_idx)
        )

    # ── 7. Objectif : coût fixe par véhicule ───────────────────────────────
    # On ne met PAS de AddDisjunction — tous les nœuds sont obligatoires
    # par défaut dans OR-Tools si on ne les déclare pas optionnels.
    # Le coût fixe véhicule guide l'optimisation vers le minimum de véhicules.
    COUT_FIXE_VEH = int(1e8)
    for v in range(n_vehicles):
        routing.SetFixedCostOfVehicle(COUT_FIXE_VEH, v)

    # ── Résolution ──────────────────────────────────────────────────────────
    # Stratégies testées dans l'ordre jusqu'à trouver une solution
    strategies = [
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION,
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
        routing_enums_pb2.FirstSolutionStrategy.CHRISTOFIDES,
    ]

    solution = None
    status_labels = {
        0: "ROUTING_NOT_SOLVED",
        1: "ROUTING_SUCCESS",
        2: "ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED",
        3: "ROUTING_FAIL",
        4: "ROUTING_FAIL_TIMEOUT",
        5: "ROUTING_INVALID",
        6: "ROUTING_INFEASIBLE",
    }

    # Stratégie unique adaptée au PDPTW + budget complet time_limit_seconds
    # Pour les PDPTW larges, PARALLEL_CHEAPEST_INSERTION échoue souvent.
    # On essaie plusieurs stratégies dans l'ordre.
    strategies = [
        ("AUTOMATIC",                      routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC),
        ("PARALLEL_CHEAPEST_INSERTION",    routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION),
        ("LOCAL_CHEAPEST_INSERTION",       routing_enums_pb2.FirstSolutionStrategy.LOCAL_CHEAPEST_INSERTION),
        ("SAVINGS",                        routing_enums_pb2.FirstSolutionStrategy.SAVINGS),
        ("PATH_MOST_CONSTRAINED_ARC",      routing_enums_pb2.FirstSolutionStrategy.PATH_MOST_CONSTRAINED_ARC),
        ("CHRISTOFIDES",                   routing_enums_pb2.FirstSolutionStrategy.CHRISTOFIDES),
    ]
    solution = None
    for strat_name, strat in strategies:
        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = strat
        params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        params.time_limit.seconds = time_limit_seconds
        params.log_search = False
        solution = routing.SolveWithParameters(params)
        status = routing.status()
        _log(
            f"    {strat_name} → statut : "
            f"{status_labels.get(status, str(status))} "
            f"({'✓' if solution else '✗'})",
            "info"
        )
        if solution:
            break

    if solution is None:
        # Diagnostic supplémentaire
        status = routing.status()
        status_label = status_labels.get(status, str(status))
        if status == 6:  # ROUTING_INFEASIBLE
            _log(
                f"    ⛔ Modèle INFAISABLE (contraintes contradictoires). "
                f"Inutile d'augmenter le budget temps. "
                f"Vérifiez : fenêtres horaires trop serrées, "
                f"amplitude poste insuffisante, ou trop peu de véhicules.",
                "error"
            )
        elif status == 4:  # ROUTING_FAIL_TIMEOUT
            _log(
                f"    ⏱️ Timeout — le solveur n'a pas eu assez de temps. "
                f"Augmentez le budget.",
                "warning"
            )
        else:
            _log(f"    ⚠️ Statut final : {status_label}", "warning")
        return None

    # ── Extraction de la solution ────────────────────────────────────────────
    routes = []
    for v in range(n_vehicles):
        index = routing.Start(v)
        route_nodes, route_times = [], []
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            t    = solution.Min(time_dim.CumulVar(index)) / SCALE
            route_nodes.append(node)
            route_times.append(t)
            index = solution.Value(routing.NextVar(index))
        # Nœud final
        node = manager.IndexToNode(index)
        t    = solution.Min(time_dim.CumulVar(index)) / SCALE
        route_nodes.append(node)
        route_times.append(t)

        # Ignorer les routes vides (dépôt→dépôt)
        if all(n == depot for n in route_nodes):
            continue

        h_debut_poste = route_times[0]
        h_fin_poste   = route_times[-1]
        amplitude     = h_fin_poste - h_debut_poste

        routes.append({
            'nodes'         : route_nodes,
            'times'         : route_times,
            'sites'         : [data['node_sites'][n] for n in route_nodes],
            'h_debut'       : h_debut_poste,
            'h_fin'         : h_fin_poste,
            'amplitude'     : amplitude,
        })

    # Diagnostic : compter les nœuds planifiés vs total
    n_nodes_planifies = sum(
        sum(1 for n in r['nodes'] if n != depot) for r in routes
    )
    n_jobs_total   = len(data['jobs'])
    n_noeuds_total = 2 * n_jobs_total  # pickup + delivery par job
    n_jobs_dans_solution = n_nodes_planifies // 2

    if n_jobs_dans_solution < n_jobs_total:
        _log(
            f"    ⚠️ Solution partielle : {n_jobs_dans_solution}/{n_jobs_total} jobs "
            f"planifiés ({n_nodes_planifies}/{n_noeuds_total} nœuds dans les routes)",
            "warning"
        )
    else:
        _log(
            f"    ✅ Solution complète : {n_jobs_dans_solution}/{n_jobs_total} jobs planifiés",
            "success"
        )

    return {
        'routes'              : routes,
        'n_vehicules'         : len(routes),
        'n_postes'            : len(routes),
        'n_jobs_planifies'    : n_jobs_dans_solution,
        'n_jobs_total'        : n_jobs_total,
        'jobs_resolus'        : data['jobs'],
    }


# ============================================================
# PHASE 3 — POST-TRAITEMENT ET CALCUL DES MÉTRIQUES
# ============================================================

@dataclass
class PosteChauffeur:
    """Représente le planning d'un chauffeur pour la journée."""
    poste_id: str
    v_type: str
    h_debut: float
    h_fin: float
    amplitude: float
    missions: list[dict] = field(default_factory=list)
    taux_occupation: float = 0.0
    etapes: list[dict] = field(default_factory=list)
    # etapes : séquence complète reconstruite après OR-Tools
    # Chaque étape = {
    #   'type'  : str   # 'prise_poste'|'trajet_vide'|'trajet_plein'|'approche'
    #                   # |'mise_a_quai'|'chargement'|'dechargement'|'attente'
    #                   # |'pause'|'fin_poste'
    #   'h_debut': float  # minutes depuis minuit
    #   'h_fin'  : float
    #   'site'   : str   (si applicable)
    #   'info'   : str   description
    # }


def _calculer_taux_occupation(route: dict, data: dict, params_logistique: dict) -> float:
    """
    Taux d'occupation = (temps en mission + temps de trajet) / amplitude_max_poste.
    """
    rh = params_logistique.get('rh', {})
    # Amplitude effective = durée poste - pause - temps fixes
    # On utilise temps_productif_max si calculé par param_flux, sinon amplitude_totale
    amplitude_max = float(
        rh.get('temps_productif_max') or
        rh.get('amplitude_totale', 450)
    )
    # Temps de nettoyage (sale → propre) = temps de fin de poste
    temps_nettoyage = float(rh.get('temps_fixes_fin', 15))

    temps_productif = 0.0
    times = route['times']
    for i in range(len(times) - 1):
        temps_productif += (times[i + 1] - times[i])  # inclut trajets + manutention

    return min(temps_productif / amplitude_max, 1.0) if amplitude_max > 0 else 0.0


def construire_postes(
    resultats_par_type: dict[str, dict],
    data_par_type: dict[str, dict],
    params_logistique: dict,
    df_vehicules: 'pd.DataFrame | None' = None,
    df_sites: 'pd.DataFrame | None' = None,
    matrice_duree: 'pd.DataFrame | None' = None,
) -> list[PosteChauffeur]:
    """
    Construit la liste des postes chauffeurs à partir des résultats OR-Tools.
    Reconstitue la séquence complète : approche, mise à quai, chargement/déchargement,
    trajet plein/vide, attente, pause, fin de poste.
    """
    # ── Helpers manutention ───────────────────────────────────────────────────
    def _to_min(val, default=0.0):
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return default
        if hasattr(val, 'hour'):
            return val.hour * 60 + val.minute + val.second / 60
        try: return float(val) * 1440
        except: return default

    # Paramètres manutention par type de véhicule
    params_veh_cache = {}
    def get_params_veh(v_type):
        if v_type in params_veh_cache:
            return params_veh_cache[v_type]
        t_quai, t_sans, t_avec = 10.0, 25/60, 15/60
        if df_vehicules is not None:
            row = df_vehicules[df_vehicules.iloc[:,0].apply(_norm) == _norm(v_type)]
            if not row.empty:
                r = row.iloc[0]
                t_quai = _to_min(r.get('Temps de mise à quai - manœuvre, contact/admin (minutes)'), 10.0)
                t_sans = _to_min(r.get('Manutention sans quai (minutes / contenants)'), 25/60)
                t_avec = _to_min(r.get('Manutention avec quai (minutes / contenants)'), 15/60)
        params_veh_cache[v_type] = (t_quai, t_sans, t_avec)
        return t_quai, t_sans, t_avec

    # Présence de quai par site
    sites_avec_quai = set()
    if df_sites is not None:
        col_lib = next((c for c in df_sites.columns if 'libel' in c.lower()), df_sites.columns[0])
        for _, row in df_sites.iterrows():
            if str(row.get('Présence de quai', 'NON')).upper() == 'OUI':
                sites_avec_quai.add(_norm(str(row[col_lib])))

    # Matrice de durées pour les trajets réels
    df_dur = None
    if matrice_duree is not None:
        df_dur = matrice_duree.copy()
        c0 = df_dur.columns[0]
        df_dur = df_dur.set_index(c0)
        df_dur.index   = df_dur.index.astype(str).str.strip().str.upper()
        df_dur.columns = df_dur.columns.astype(str).str.strip().str.upper()

    def trajet_min(site_a, site_b):
        if site_a == site_b: return 0.0
        if df_dur is None: return 15.0
        try: return float(df_dur.loc[_norm(site_a), _norm(site_b)])
        except: return 15.0

    def duree_operation(site, nb_cont, v_type, est_complet, capa_utile):
        """Durée de chargement ou déchargement sur un site."""
        t_quai, t_sans, t_avec = get_params_veh(v_type)
        quai = _norm(site) in sites_avec_quai
        t_manu = nb_cont * (t_avec if quai else t_sans)
        taux = 1.0 if est_complet else nb_cont / max(1, capa_utile)
        return t_quai * taux, t_manu  # (t_quai_effectif, t_manu)

    def reconstruire_etapes(missions_sorted, v_type, h_debut_poste, duree_poste,
                             t_prise, t_fin_poste, t_pause, t_pause_seuil):
        """
        Reconstruit la séquence complète d'étapes depuis la liste des missions OR-Tools.
        Retourne list[dict] avec les étapes détaillées.
        """
        etapes = []
        rh = params_logistique.get('rh', {})

        # Prise de poste
        curseur = h_debut_poste
        etapes.append({'type':'prise_poste','h_debut':curseur,'h_fin':curseur+t_prise,
                       'site':'HLS','info':'Préparation / Check véhicule'})
        curseur += t_prise
        site_courant = 'HLS'
        charge = 0
        pause_faite = False

        for m in missions_sorted:
            h_ortools = m['heure']  # heure OR-Tools = heure d'arrivée sur site
            site_dest = m['site']
            nb_cont   = m['nb_contenants']
            is_pickup = m['is_pickup']
            ps        = m.get('propre_sale','')

            # Départ vers le site : h_depart = h_ortools - trajet
            duree_traj = trajet_min(site_courant, site_dest)
            h_depart   = max(curseur, h_ortools - duree_traj)

            # Attente avant de partir (si arrivée tardive prévue par OR-Tools)
            if h_depart > curseur + 0.5:
                etapes.append({'type':'attente','h_debut':curseur,'h_fin':h_depart,
                               'site':site_courant,'info':f'Attente sur {site_courant}'})

            # Pause si seuil dépassé pendant le trajet
            if not pause_faite and (h_depart - h_debut_poste) > t_pause_seuil:
                etapes.append({'type':'pause','h_debut':h_depart,'h_fin':h_depart+t_pause,
                               'site':site_courant,'info':'Pause obligatoire'})
                h_depart   += t_pause
                pause_faite = True

            # Trajet (vide ou plein selon charge courante)
            if duree_traj > 0.5:
                type_traj = 'trajet_plein' if charge > 0 else 'trajet_vide'
                etapes.append({'type':type_traj,'h_debut':h_depart,'h_fin':h_depart+duree_traj,
                               'site':f'{site_courant}→{site_dest}',
                               'info':f'{site_courant} → {site_dest} ({duree_traj:.0f} min)'})
            curseur = h_depart + duree_traj

            # Approche/mise à quai + opération
            # On calcule les durées réelles depuis les paramètres
            # est_complet approx : True si nb_cont >= capa_utile du type
            capa_brute = 20  # approximation si pas d'info
            est_complet = True  # simplifié ici
            t_q, t_m = duree_operation(site_dest, nb_cont, v_type, est_complet, capa_brute)

            if t_q > 0.1:
                etapes.append({'type':'mise_a_quai','h_debut':curseur,'h_fin':curseur+t_q,
                               'site':site_dest,'info':f'Mise à quai ({t_q:.1f} min)'})
                curseur += t_q

            op_type = 'chargement' if is_pickup else 'dechargement'
            etapes.append({'type':op_type,'h_debut':curseur,'h_fin':curseur+t_m,
                           'site':site_dest,
                           'info':f'{nb_cont} {m["type_contenant"]} ({ps}) — {t_m:.1f} min'})
            curseur += t_m

            if is_pickup: charge += nb_cont
            else:         charge  = max(0, charge - nb_cont)
            site_courant = site_dest

        # Retour HLS
        if site_courant != 'HLS':
            dur_ret = trajet_min(site_courant, 'HLS')
            if dur_ret > 0.5:
                type_traj = 'trajet_plein' if charge > 0 else 'trajet_vide'
                etapes.append({'type':type_traj,'h_debut':curseur,'h_fin':curseur+dur_ret,
                               'site':f'{site_courant}→HLS','info':f'Retour HLS ({dur_ret:.0f} min)'})
                curseur += dur_ret

        # Attente jusqu'à fin de poste
        h_fin_poste_off = h_debut_poste + duree_poste - t_fin_poste
        if curseur < h_fin_poste_off - 1:
            etapes.append({'type':'attente','h_debut':curseur,'h_fin':h_fin_poste_off,
                           'site':'HLS','info':'En attente de fin de poste'})

        # Fin de poste
        h_fin_poste = h_debut_poste + duree_poste
        etapes.append({'type':'fin_poste','h_debut':h_fin_poste-t_fin_poste,'h_fin':h_fin_poste,
                       'site':'HLS','info':'Nettoyage / Clôture'})
        return etapes
    postes = []
    compteur = 1

    for v_type, res in resultats_par_type.items():
        if res is None:
            continue
        data = data_par_type[v_type]
        for route in res['routes']:
            taux = _calculer_taux_occupation(route, data, params_logistique)
            missions = []
            for i, (node, site, t) in enumerate(
                zip(route['nodes'], route['sites'], route['times'])
            ):
                if node == data['depot']:
                    continue
                item = data['node_to_item'][node]
                if item is None:
                    continue
                kind, idx = item
                is_pickup = data['node_is_pickup'][node]

                if kind == 'complet':
                    job = data['jobs_complets'][idx]
                    missions.append({
                        'heure'         : t,
                        'site'          : site,
                        'job_id'        : job.job_id,
                        'flux_id'       : job.flux_id,
                        'type_contenant': job.type_contenant,
                        'nb_contenants' : job.nb_contenants,
                        'is_pickup'     : True,
                        'propre_sale'   : job.propre_sale,
                    })
                elif kind in ('super_pickup', 'super_delivery'):
                    sj = data['super_jobs'][idx]
                    # Pour un super-job : on liste tous les jobs qui le composent
                    for job in sj.jobs:
                        missions.append({
                            'heure'         : t,
                            'site'          : site,
                            'job_id'        : job.job_id,
                            'flux_id'       : job.flux_id,
                            'type_contenant': job.type_contenant,
                            'nb_contenants' : job.nb_contenants,
                            'is_pickup'     : is_pickup,
                            'propre_sale'   : job.propre_sale,
                        })
            missions.sort(key=lambda x: x['heure'])

            # Paramètres RH
            rh_cp         = params_logistique.get('rh', {})
            amplitude_max = float(rh_cp.get('amplitude_totale', 450))
            t_prise       = float(rh_cp.get('temps_fixes_prise', 20))
            t_fin_p       = float(rh_cp.get('temps_fixes_fin',   15))
            t_pause       = float(rh_cp.get('pause', 30))
            t_pause_seuil = 180.0
            h_debut_route = route['h_debut']
            h_fin_route   = route['h_fin']

            if h_fin_route - h_debut_route <= amplitude_max:
                # Cas normal : un seul poste
                etapes = reconstruire_etapes(
                    missions, v_type, h_debut_route, amplitude_max,
                    t_prise, t_fin_p, t_pause, t_pause_seuil
                )
                postes.append(PosteChauffeur(
                    poste_id        = f"{v_type}_{compteur:03d}",
                    v_type          = v_type,
                    h_debut         = h_debut_route,
                    h_fin           = h_fin_route,
                    amplitude       = h_fin_route - h_debut_route,
                    missions        = missions,
                    taux_occupation = taux,
                    etapes          = etapes,
                ))
                compteur += 1
            else:
                # Route trop longue → découper en postes de amplitude_max
                # Regrouper les missions par tranche horaire
                releve = float(rh_cp.get('releve', 15))
                h_debut_poste = h_debut_route
                missions_poste = []
                for m in missions:
                    h_m = m['heure']
                    if h_m - h_debut_poste > amplitude_max and missions_poste:
                        # Clore le poste courant
                        h_fin_poste = missions_poste[-1]['heure']
                        postes.append(PosteChauffeur(
                            poste_id        = f"{v_type}_{compteur:03d}",
                            v_type          = v_type,
                            h_debut         = h_debut_poste,
                            h_fin           = h_fin_poste,
                            amplitude       = h_fin_poste - h_debut_poste,
                            missions        = missions_poste,
                            taux_occupation = taux,
                        ))
                        compteur      += 1
                        h_debut_poste  = h_fin_poste + releve
                        missions_poste = []
                    missions_poste.append(m)
                # Dernier poste
                if missions_poste:
                    h_fin_poste = max(m['heure'] for m in missions_poste)
                    postes.append(PosteChauffeur(
                        poste_id        = f"{v_type}_{compteur:03d}",
                        v_type          = v_type,
                        h_debut         = h_debut_poste,
                        h_fin           = h_fin_poste,
                        amplitude       = h_fin_poste - h_debut_poste,
                        missions        = missions_poste,
                        taux_occupation = taux,
                    ))
                    compteur += 1

    return postes


def calculer_rapport(
    postes: list[PosteChauffeur],
    jobs_par_type: dict[str, list[JobElementaire]],
    resultats_par_type: dict[str, dict | None],
) -> dict:
    """
    Calcule le rapport global de la simulation.
    Retourne un dict compatible avec st.session_state["flux_rapport"].
    """
    nb_vehicules_par_type: dict[str, int] = {}
    nb_postes_par_type: dict[str, int] = {}
    taux_par_poste: list[float] = []

    for p in postes:
        nb_vehicules_par_type[p.v_type] = nb_vehicules_par_type.get(p.v_type, 0) + 1
        nb_postes_par_type[p.v_type]    = nb_postes_par_type.get(p.v_type, 0) + 1
        taux_par_poste.append(p.taux_occupation)

    # Jobs non planifiés
    # jobs_planifies : ensemble des flux_id planifiés
    # On utilise flux_id (pas job_id) car les jobs re-découpés ont des job_id
    # différents (10000+) des jobs originaux dans jobs_par_type
    flux_planifies = {p.missions[i]['flux_id'] for p in postes for i in range(len(p.missions))}

    jobs_non_planifies        = []
    jobs_non_planifies_detail = []
    for v_type, jlist in jobs_par_type.items():
        res_v = resultats_par_type.get(v_type)
        if res_v is None:
            raison = "Aucune solution OR-Tools trouvée pour ce type de véhicule"
        else:
            raison = "Non planifié par OR-Tools (fenêtre horaire ou capacité)"
        # Dédupliquer par flux_id : un flux est non planifié seulement si
        # AUCUN de ses jobs (originaux ou re-découpés) n'est dans la solution
        flux_vus = set()
        for j in jlist:
            if j.flux_id not in flux_planifies and j.flux_id not in flux_vus:
                flux_vus.add(j.flux_id)
                jobs_non_planifies.append(j)
                jobs_non_planifies_detail.append({
                    "job"    : j,
                    "raison" : raison,
                    "v_type" : v_type,
                })

    taux_tries  = sorted(taux_par_poste, reverse=True)
    taux_moyen  = sum(taux_tries) / len(taux_tries) if taux_tries else 0.0

    return {
        'nb_vehicules_par_type' : nb_vehicules_par_type,
        'nb_postes_par_type'    : nb_postes_par_type,
        'nb_vehicules_total'    : sum(nb_vehicules_par_type.values()),
        'nb_postes_total'       : sum(nb_postes_par_type.values()),
        'taux_moyen'            : taux_moyen,
        'taux_par_poste'        : taux_tries,
        'jobs_non_planifies'        : jobs_non_planifies,
        'jobs_non_planifies_detail' : jobs_non_planifies_detail,
        'nb_jobs_non_planifies'     : len(jobs_non_planifies),
        'solveur'               : 'OR-Tools' if ORTOOLS_AVAILABLE else 'indisponible',
    }



# ============================================================
# CONTRÔLE DE FAISABILITÉ (à appeler avant run_flux_optimization)
# ============================================================

@dataclass
class ProblemeFaisabilite:
    """Décrit un problème de faisabilité détecté."""
    flux_id: int
    origine: str
    destination: str
    type_contenant: str
    site_bloquant: str       # site qui pose problème
    raison: str              # "SITE_INCONNU" | "AUCUN_VEHICULE_ACCESSIBLE" | "AUCUNE_CAPACITE"
    vehicules_testes: list[str]
    detail: str              # message lisible


def verifier_faisabilite(
    df_flux: pd.DataFrame,
    df_vehicules: pd.DataFrame,
    df_contenants: pd.DataFrame,
    df_sites: pd.DataFrame,
    capacites: dict,
    params_logistique: dict,
    jour: str = "Lundi",
) -> dict:
    """
    Vérifie que chaque flux du jour est réalisable avec la flotte sélectionnée.

    Pour chaque flux, contrôle :
      1. Les deux sites (origine et destination) existent dans param_sites
      2. Au moins un véhicule sélectionné est accessible sur LES DEUX sites
      3. Ce véhicule peut transporter le contenant (capacité > 0)

    Paramètres
    ----------
    df_flux, df_vehicules, df_contenants, df_sites : DataFrames issus de session_state
    capacites           : sortie de precalculer_capacites()
    params_logistique   : dict de session_state["params_logistique"]
    jour                : "Lundi", "Mardi", etc.

    Retourne
    --------
    dict {
        "faisable"    : bool          True si aucun problème bloquant
        "problemes"   : list[ProblemeFaisabilite]
        "resume"      : str           message court pour affichage Streamlit
        "nb_flux_ok"  : int
        "nb_flux_ko"  : int
        "details_df"  : pd.DataFrame  tableau affichable dans st.dataframe()
    }
    """
    vehicules_autorises = [_norm(v) for v in params_logistique.get('vehicules_selectionnes', [])]
    col_qte = f'Quantité {jour}'

    # Mapping colonnes normalisées → colonnes originales dans df_sites
    col_lib = next(
        (c for c in df_sites.columns if _norm(c) in ('LIBELLE', 'LIBELLE', 'NOM', 'SITE')),
        df_sites.columns[0]
    )
    cols_sites_norm = {_norm(c): c for c in df_sites.columns}
    sites_connus    = {_norm(s) for s in df_sites[col_lib].dropna()}

    problemes: list[ProblemeFaisabilite] = []
    nb_ok = 0

    for flux_id, row in df_flux.iterrows():
        # Filtrer les flux sans quantité ce jour-là
        try:
            raw = row.get(col_qte, 0)
            qte = 0.0 if (raw is None or (isinstance(raw, float) and math.isnan(raw))) else float(raw)
        except (ValueError, TypeError):
            qte = 0.0
        if qte <= 0:
            continue

        # Filtrer les flux non-Volume
        nature = str(row.get(
            "Nature du flux (les tournées sont elles à prévoir avec une obligation de transport ou une obligation de passage?)",
            "Volume"
        )).strip().lower()
        if nature not in ('volume', 'nan', ''):
            continue

        origine     = _norm(row.get('Point de départ', ''))
        destination = _norm(row.get('Point de destination', ''))
        type_cont   = _norm(row.get('Nature de contenant', ''))

        # ── Contrôle 1 : sites connus ────────────────────────────────────────
        for site, role in [(origine, 'départ'), (destination, 'destination')]:
            if site not in sites_connus:
                problemes.append(ProblemeFaisabilite(
                    flux_id=flux_id, origine=origine, destination=destination,
                    type_contenant=type_cont, site_bloquant=site,
                    raison="SITE_INCONNU",
                    vehicules_testes=[],
                    detail=f"Le site de {role} '{site}' est absent de param_sites."
                ))

        if any(p.flux_id == flux_id and p.raison == "SITE_INCONNU" for p in problemes):
            continue  # pas la peine de continuer si le site est inconnu

        # ── Contrôle 2 & 3 : véhicule accessible + capacité ─────────────────
        vehicules_accessibles  = []
        vehicules_avec_capa    = []

        for v_nom in vehicules_autorises:
            # Accessibilité origine
            col_orig_v = cols_sites_norm.get(v_nom)
            if col_orig_v is None:
                continue
            row_o = df_sites[df_sites[col_lib].apply(_norm) == origine]
            row_d = df_sites[df_sites[col_lib].apply(_norm) == destination]
            if row_o.empty or row_d.empty:
                continue
            try:
                acc_o = str(row_o[col_orig_v].values[0]).upper() == 'OUI'
                acc_d = str(row_d[col_orig_v].values[0]).upper() == 'OUI'
            except Exception:
                continue

            if acc_o and acc_d:
                vehicules_accessibles.append(v_nom)
                # Capacité
                capa = capacites.get(v_nom, {}).get(type_cont, 0)
                if capa > 0:
                    vehicules_avec_capa.append(v_nom)

        if not vehicules_accessibles:
            problemes.append(ProblemeFaisabilite(
                flux_id=flux_id, origine=origine, destination=destination,
                type_contenant=type_cont, site_bloquant=f"{origine}↔{destination}",
                raison="AUCUN_VEHICULE_ACCESSIBLE",
                vehicules_testes=vehicules_autorises,
                detail=(
                    f"Aucun véhicule sélectionné ne peut accéder aux deux sites. "
                    f"Vérifiez les colonnes d'accessibilité dans param_sites."
                )
            ))
        elif not vehicules_avec_capa:
            problemes.append(ProblemeFaisabilite(
                flux_id=flux_id, origine=origine, destination=destination,
                type_contenant=type_cont, site_bloquant=f"{origine}↔{destination}",
                raison="AUCUNE_CAPACITE",
                vehicules_testes=vehicules_accessibles,
                detail=(
                    f"Véhicules accessibles ({', '.join(vehicules_accessibles)}) "
                    f"mais aucun ne peut transporter '{type_cont}' "
                    f"(colonne 'NON' dans param_vehicules ou dimensions incompatibles)."
                )
            ))
        else:
            nb_ok += 1

    nb_ko = len(problemes)
    faisable = nb_ko == 0

    # Tableau résumé pour st.dataframe()
    rows_df = []
    for p in problemes:
        rows_df.append({
            'Flux ID'       : p.flux_id,
            'Origine'       : p.origine,
            'Destination'   : p.destination,
            'Contenant'     : p.type_contenant,
            'Problème'      : p.raison,
            'Détail'        : p.detail,
        })
    details_df = pd.DataFrame(rows_df) if rows_df else pd.DataFrame()

    if faisable:
        resume = f"✅ Tous les {nb_ok} flux du {jour} sont faisables avec la flotte sélectionnée."
    else:
        resume = (
            f"⚠️ {nb_ko} flux non faisable(s) sur {nb_ok + nb_ko} flux actifs le {jour}. "
            f"La simulation sera lancée mais ces flux seront ignorés."
        )

    return {
        "faisable"  : faisable,
        "problemes" : problemes,
        "resume"    : resume,
        "nb_flux_ok": nb_ok,
        "nb_flux_ko": nb_ko,
        "details_df": details_df,
    }


# ============================================================
# DIAGNOSTIC D'INFAISABILITÉ
# ============================================================

def diagnostiquer_infaisabilite(data: dict, time_limit_seconds: int = 60) -> None:
    """
    Teste les contraintes une par une pour identifier celle qui rend
    le modèle infaisable. Affiche le résultat dans Streamlit.
    """
    if not data or not ORTOOLS_AVAILABLE:
        return

    SCALE      = data['SCALE']
    n_nodes    = data['n_nodes']
    n_vehicles = data['n_vehicles']
    depot      = data['depot']

    def _tester(label, avec_tw=True, avec_amplitude=True,
                avec_capacite=True, avec_pd=True, avec_nettoyage=True):
        mgr = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, depot)
        rte = pywrapcp.RoutingModel(mgr)
        ps             = data['propre_sale_par_noeud']
        node_is_pickup = data['node_is_pickup']
        T_NET          = data['temps_nettoyage_sc'] if avec_nettoyage else 0

        def cb_t(fi, ti):
            fn = mgr.IndexToNode(fi)
            tn = mgr.IndexToNode(ti)
            t  = data['time_matrix'][fn][tn]
            if (avec_nettoyage and fn > 0 and not node_is_pickup[fn]
                    and ps[fn] == 'SALE' and tn > 0
                    and node_is_pickup[tn] and ps[tn] == 'PROPRE'):
                return t + T_NET
            return t

        cb = rte.RegisterTransitCallback(cb_t)
        rte.SetArcCostEvaluatorOfAllVehicles(cb)
        rte.AddDimension(cb, _sc(120), _sc(1440), False, 'TimeDiag')
        td = rte.GetDimensionOrDie('TimeDiag')

        if avec_tw:
            for node in range(1, n_nodes):
                idx = mgr.NodeToIndex(node)
                tw  = data['time_windows'][node]
                td.CumulVar(idx).SetRange(tw[0], tw[1])
            for v in range(n_vehicles):
                td.CumulVar(rte.Start(v)).SetRange(data['h_debut_sc'], data['h_fin_sc'])
                td.CumulVar(rte.End(v)).SetRange(data['h_debut_sc'], data['h_fin_sc'])

        slv = rte.solver()
        if avec_amplitude:
            pass  # amplitude gérée en post-traitement

        if avec_capacite:
            def cb_d(fi):
                return data['demands'][mgr.IndexToNode(fi)]
            cbd = rte.RegisterUnaryTransitCallback(cb_d)
            rte.AddDimensionWithVehicleCapacity(
                cbd, 0,
                [data['vehicle_capacity']] * n_vehicles, True, 'CapDiag'
            )

        if avec_pd:
            for pickup_node, delivery_node in data['pickups_deliveries']:
                pi = mgr.NodeToIndex(pickup_node)
                di = mgr.NodeToIndex(delivery_node)
                rte.AddPickupAndDelivery(pi, di)
                slv.Add(rte.VehicleVar(pi) == rte.VehicleVar(di))
                slv.Add(td.CumulVar(pi) <= td.CumulVar(di))

        for v in range(n_vehicles):
            rte.SetFixedCostOfVehicle(int(1e8), v)

        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
        params.time_limit.seconds = time_limit_seconds
        params.log_search = False
        sol = rte.SolveWithParameters(params)
        statuts = {0:"NON_RESOLU",1:"OK",2:"PARTIEL",3:"ECHEC",4:"TIMEOUT",6:"INFAISABLE"}
        statut = statuts.get(rte.status(), str(rte.status()))
        ok = "✅" if sol else "❌"
        _log(f"    {ok} {label} → {statut}", "success" if sol else "warning")
        return sol is not None

    n_v  = data['n_vehicles']
    n_j  = (data['n_nodes'] - 1) // 2
    amp  = data['max_poste_sc'] // SCALE
    capa = data['vehicle_capacity']
    _log(f"  🔬 Diagnostic : {n_j} jobs, {n_v} véhicules, amplitude={amp}min, capacité={capa}", "info")

    _tester("Sans aucune contrainte",         False, False, False, False, False)
    _tester("+ Fenêtres temporelles seules",  True,  False, False, False, False)
    _tester("+ Amplitude poste",              True,  True,  False, False, False)
    _tester("+ Capacité",                     True,  True,  True,  False, False)
    _tester("+ Pickup & Delivery",            True,  True,  True,  True,  False)
    _tester("+ Nettoyage (toutes contraintes)",True,  True,  True,  True,  True)

# ============================================================
# RÉSOLUTION ITÉRATIVE : 1 → Nmax véhicules
# ============================================================

def _solve_type_iteratif(
    data: dict,
    time_limit_seconds: int = 60,
) -> dict | None:
    """
    Lance une unique tentative OR-Tools avec ceil(Nmax × 1.2) véhicules.
    OR-Tools minimise lui-même le nombre utilisés via le coût fixe élevé.
    Filet de sécurité à ceil(Nmax × 1.5) si la première tentative échoue.
    """
    if not data:
        return None

    nmax           = data.get('nmax', data['n_vehicles'])
    nmax_theorique = data.get('nmax_theorique', 1)
    pic_charge     = data.get('pic_charge_min', 0)
    n_jobs         = len(data.get('jobs', []))

    _log(
        f"  📊 Pic de charge lissée : {pic_charge:.1f} min | "
        f"Nmax théorique : {nmax_theorique} | "
        f"Borne haute : {nmax} véhicules",
        "info"
    )

    # ── Tentative principale : ceil(Nmax_theorique × 1.2) ──────────────────
    # On utilise nmax_theorique (pic de charge / 15min) comme base,
    # pas nmax (= nmax_theorique × 2) qui est trop conservateur.
    n_v = min(n_jobs, max(1, math.ceil(nmax_theorique * 1.2)))
    _log(f"  🔄 Tentative avec {n_v} véhicule(s) (budget : {time_limit_seconds}s)...", "info")
    sol = _solve_type({**data, 'n_vehicles': n_v}, time_limit_seconds=time_limit_seconds)
    if sol is not None:
        _log(f"  ✅ Solution trouvée avec {sol['n_vehicules']} véhicule(s) utilisé(s)", "success")
        return sol

    # ── Filet de sécurité : ceil(Nmax × 1.5) ───────────────────────────────
    n_v2 = min(n_jobs, max(1, math.ceil(nmax_theorique * 1.5)))
    if n_v2 > n_v:
        _log(f"  ↳ Échec avec {n_v}, filet : {n_v2} véhicule(s)...", "warning")
        sol = _solve_type({**data, 'n_vehicles': n_v2}, time_limit_seconds=time_limit_seconds)
        if sol is not None:
            _log(f"  ✅ Solution trouvée avec {sol['n_vehicules']} véhicule(s) utilisé(s)", "success")
            return sol
        n_v = n_v2

    # ── Diagnostic ───────────────────────────────────────────────────────────
    _log(f"  ❌ Aucune solution trouvée.", "error")
    _log("  🔬 Lancement du diagnostic d'infaisabilité...", "info")
    diagnostiquer_infaisabilite({**data, 'n_vehicles': n_v}, time_limit_seconds=60)
    capa_max = data['vehicle_capacity']
    jobs_hors_capa = [j for j in data['jobs'] if j.nb_contenants > capa_max]
    if jobs_hors_capa:
        _log(f"  ⚠️ {len(jobs_hors_capa)} job(s) avec nb_contenants > capacité ({capa_max}) :", 'error')
        for j in jobs_hors_capa[:10]:
            _log(f"    Job {j.job_id} : {j.origine}→{j.destination}, "
                 f"{j.nb_contenants} contenants (max={capa_max})", 'error')
    else:
        charge_max_job = max((j.nb_contenants for j in data['jobs']), default=0)
        _log(
            f"  📊 Charge max par job : {charge_max_job} | "
            f"vehicle_capacity : {capa_max} | "
            f"{'✅ OK' if charge_max_job <= capa_max else '❌ PROBLÈME : job dépasse capacité'}",
            'info'
        )
    return None


# ============================================================
# POINT D'ENTRÉE PRINCIPAL
# ============================================================

def run_flux_optimization(
    df_flux: pd.DataFrame,
    df_vehicules: pd.DataFrame,
    df_contenants: pd.DataFrame,
    df_sites: pd.DataFrame,
    matrice_duree: pd.DataFrame,
    params_logistique: dict,
    jour: str = "Lundi",
    time_limit_seconds: int = 60,
) -> dict:
    """
    Optimise les tournées de distribution pour une journée donnée.

    Paramètres
    ----------
    df_flux             : st.session_state["data"]["m_flux"]
    df_vehicules        : st.session_state["data"]["param_vehicules"]
    df_contenants       : st.session_state["data"]["param_contenants"]
    df_sites            : st.session_state["data"]["param_sites"]
    matrice_duree       : st.session_state["data"]["matrice_duree"]
    params_logistique   : st.session_state["params_logistique"]
    jour                : "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"
    time_limit_seconds  : budget temps du solveur OR-Tools par type de véhicule

    Retourne
    --------
    dict {
        "postes"          : list[PosteChauffeur]   plannings chauffeurs
        "rapport"         : dict                   métriques globales
        "jobs_par_type"   : dict                   jobs par type de véhicule
    }
    Stocké dans st.session_state["flux_resultats"] par app.py.
    """
    if not ORTOOLS_AVAILABLE:
        _log("⚠️ OR-Tools non disponible. Installez ortools pour utiliser ce moteur.", "error")
        return {}

    _log(f"🔍 Démarrage de l'optimisation — {jour}", "info")

    # Log des paramètres reçus pour déboguer
    vehicules_selectionnes = params_logistique.get('vehicules_selectionnes', [])
    _log(f"🚛 Véhicules sélectionnés : {vehicules_selectionnes}", "info")

    # ── 0. Pré-calcul bin-packing ────────────────────────────────────────────
    _log("📦 Calcul des capacités véhicules × contenants (bin-packing 2D)...", "info")
    capacites = precalculer_capacites(df_vehicules, df_contenants)

    df_cont_indexed = {
        _norm(row.iloc[0]): {_norm(k): v for k, v in row.items()}
        for _, row in df_contenants.iterrows()
    }

    # ── 1. Décomposition des flux en jobs élémentaires ───────────────────────
    _log(f"⚙️ Décomposition des flux du {jour} en jobs élémentaires...", "info")
    tous_les_jobs = decomposer_flux_en_jobs(
        df_flux, df_vehicules, df_contenants, df_sites,
        df_cont_indexed, capacites, params_logistique, jour
    )
    _log(f"  → {len(tous_les_jobs)} jobs élémentaires générés", "info")

    # ── Diagnostic répartition par type de véhicule ──────────────────────────
    from collections import Counter
    repartition = Counter(j.v_type_requis for j in tous_les_jobs)
    for v_type, nb in sorted(repartition.items(), key=lambda x: -x[1]):
        _log(f"  📦 {v_type} : {nb} jobs", "info")

    # ── Tableau de debug : détail de chaque job élémentaire ─────────────────
    # Affiché dans Streamlit avant le lancement du solveur OR-Tools
    if _ST and st is not None and tous_les_jobs:
        try:
            taux_debug = params_logistique.get('securite_remplissage', 0.85)
            rows_debug = []
            for j in tous_les_jobs:
                capa_brute = capacites.get(j.v_type_requis, {}).get(j.type_contenant, 0)
                capa_utile = max(1, math.floor(capa_brute * taux_debug))
                rows_debug.append({
                    'Job ID'          : j.job_id,
                    'Flux ID'         : j.flux_id,
                    'Origine'         : j.origine,
                    'Destination'     : j.destination,
                    'Contenant'       : j.type_contenant,
                    'Qté job'         : j.nb_contenants,
                    'Véhicule'        : j.v_type_requis,
                    'Capa brute'      : capa_brute,
                    f'Capa utile ({int(taux_debug*100)}%)' : capa_utile,
                    'Qté > capa utile': '⚠️ OUI' if j.nb_contenants > capa_utile else '✅ OK',
                    'H dispo'         : f"{int(j.h_dispo//60):02d}h{int(j.h_dispo%60):02d}",
                    'H deadline'      : f"{int(j.h_deadline//60):02d}h{int(j.h_deadline%60):02d}",
                    'Propre/Sale'     : j.propre_sale,
                    'Urgent'          : '🔴' if j.est_urgent else '',
                })
            df_debug = pd.DataFrame(rows_debug)
            with st.expander(
                f"🔍 Détail des {len(tous_les_jobs)} jobs élémentaires (avant solveur)",
                expanded=False
            ):
                # Résumé rapide
                nb_anomalies = sum(1 for r in rows_debug if r['Qté > capa utile'] == '⚠️ OUI')
                if nb_anomalies:
                    st.warning(f"⚠️ {nb_anomalies} job(s) ont une quantité supérieure à la capacité utile du véhicule.")
                else:
                    st.success("✅ Toutes les quantités respectent la capacité utile.")
                st.dataframe(df_debug, use_container_width=True, hide_index=True)
        except Exception as _e:
            print(f"Erreur tableau debug jobs : {_e}")

    if not tous_les_jobs:
        _log("⚠️ Aucun job à planifier pour ce jour.", "warning")
        return {}

    # Regroupement par type de véhicule
    jobs_par_type: dict[str, list[JobElementaire]] = {}
    for j in tous_les_jobs:
        jobs_par_type.setdefault(j.v_type_requis, []).append(j)

    # ── 2. Résolution OR-Tools par type de véhicule ──────────────────────────
    vehicules_autorises = [_norm(v) for v in params_logistique.get('vehicules_selectionnes', [])]
    alea = params_logistique.get('alea_circulation', 0.0)

    resultats_par_type: dict[str, dict | None] = {}
    data_par_type: dict[str, dict] = {}

    for v_type, jobs_v in jobs_par_type.items():
        if v_type not in vehicules_autorises:
            _log(f"  ⏭️ {v_type} non sélectionné dans la flotte, ignoré.", "warning")
            continue

        _log(f"🚛 Optimisation {v_type} ({len(jobs_v)} jobs)...", "info")

        # ── Séparation complets / incomplets + regroupement ──────────────
        # Capacité utile = floor(capa_brute × taux)
        taux_r     = params_logistique.get('securite_remplissage', 0.85)
        capa_brute = max(
            (capacites.get(_norm(v_type), {}).get(j.type_contenant, 1) for j in jobs_v),
            default=1
        )
        capa_utile_v = max(1, math.floor(capa_brute * taux_r))

        jobs_complets, super_jobs = preparer_liste_unifiee(jobs_v, capa_utile_v)

        nb_complets   = len(jobs_complets)
        nb_superjobs  = len(super_jobs)
        nb_groupes    = sum(1 for sj in super_jobs if len(sj.jobs) > 1)
        _log(
            f"  📦 {nb_complets} job(s) complet(s) + "
            f"{nb_superjobs} super-job(s) incomplet(s) "
            f"({nb_groupes} regroupement(s))",
            "info"
        )

        # ── Construire le modèle unifié ──────────────────────────────────
        data = _build_model_data(
            jobs_complets, super_jobs, matrice_duree, capacites,
            params_logistique, v_type, alea,
            df_vehicules=df_vehicules,
            df_sites=df_sites,
        )
        if not data:
            resultats_par_type[v_type] = None
            continue

        data_par_type[v_type] = data
        res = _solve_type_iteratif(data, time_limit_seconds=time_limit_seconds)

        if res is not None:
            resultats_par_type[v_type] = res
            _log(
                f"  ✅ {v_type} : {res['n_vehicules']} véhicule(s), "
                f"{res['n_postes']} poste(s)",
                "success"
            )
        else:
            resultats_par_type[v_type] = None
            # Diagnostic : afficher la borne utilisée
            if data:
                n_v = data.get('n_vehicles', '?')
                n_j = len(data.get('jobs', []))
                amp = data.get('max_poste_sc', 0) // data.get('SCALE', 1)
                _log(
                    f"  ❌ {v_type} : pas de solution trouvée. "
                    f"(modèle : {n_j} jobs, {n_v} véhicules max, "
                    f"amplitude={amp} min) "
                    f"→ Essayez d'augmenter le budget temps ou l'amplitude de poste.",
                    "error"
                )

    # ── 3. Post-traitement ───────────────────────────────────────────────────
    postes = construire_postes(
        resultats_par_type, data_par_type, params_logistique,
        df_vehicules=df_vehicules,
        df_sites=df_sites,
        matrice_duree=matrice_duree,
    )
    rapport = calculer_rapport(postes, jobs_par_type, resultats_par_type)

    # Stockage dans session_state si disponible
    if _ST and st is not None:
        try:
            st.session_state["flux_resultats"] = {
                "postes"       : postes,
                "rapport"      : rapport,
                "jobs_par_type": jobs_par_type,
                "jour"         : jour,
            }
            st.session_state["flux_rapport"] = rapport
        except Exception:
            pass

    # Rapport final
    _log(
        f"✅ Optimisation terminée — "
        f"{rapport['nb_vehicules_total']} véhicule(s), "
        f"{rapport['nb_postes_total']} poste(s), "
        f"taux moyen {rapport['taux_moyen']:.1%}"
        + (f" — ⚠️ {rapport['nb_jobs_non_planifies']} job(s) non planifié(s)"
           if rapport['nb_jobs_non_planifies'] > 0 else ""),
        "success" if rapport['nb_jobs_non_planifies'] == 0 else "warning"
    )

    return {
        "postes"       : postes,
        "rapport"      : rapport,
        "jobs_par_type": jobs_par_type,
        "jour"         : jour,
    }
