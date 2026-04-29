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
    """Normalise un nom de site/véhicule/contenant."""
    return str(s).strip().upper()


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

    def est_accessible(v_nom: str) -> bool:
        try:
            row_o = df_sites[df_sites[col_lib].apply(_norm) == _norm(origine)]
            row_d = df_sites[df_sites[col_lib].apply(_norm) == _norm(destination)]
            if row_o.empty or row_d.empty:
                return False
            return (str(row_o[v_nom].values[0]).upper() == 'OUI' and
                    str(row_d[v_nom].values[0]).upper() == 'OUI')
        except Exception:
            return False

    meilleur_type, meilleure_capa = None, 0

    for _, v in df_vehicules.iterrows():
        v_nom = _norm(v.iloc[0])
        if v_nom not in vehicules_autorises:
            continue
        # Filtrage par type demandé si renseigné
        if v_type_demande and v_type_demande not in ('', 'NAN', 'NC'):
            if _norm(v_type_demande) not in v_nom and v_nom not in _norm(v_type_demande):
                continue
        if not est_accessible(v_nom):
            continue
        capa = capacites.get(v_nom, {}).get(_norm(type_contenant), 0)
        if capa > meilleure_capa:
            meilleure_capa, meilleur_type = capa, v_nom

    if meilleur_type is None:
        return '', 0
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
            qte = float(row.get(col_qte, 0) or 0)
        except (ValueError, TypeError):
            qte = 0
        if qte <= 0:
            continue

        origine      = _norm(row.get('Point de départ', ''))
        destination  = _norm(row.get('Point de destination', ''))
        type_cont    = _norm(row.get('Nature de contenant', ''))
        propre_sale  = _norm(row.get('Sale / propre', 'PROPRE'))
        v_type_req   = _norm(str(row.get('Type de transporteur (camion VL frigo)', '') or ''))
        est_urgent   = str(row.get('Urgence / flux prioritaire   (Oui/Non)', 'Non')).upper() == 'OUI'

        # Fenêtres horaires
        h_dispo   = _excel_time_to_minutes(row.get('Heure de mise à disposition min départ'), h_debut_defaut)
        h_deadline = _excel_time_to_minutes(row.get('Plage horaire en semaine (Heure fin)'), h_fin_defaut)
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
                "aucun véhicule compatible trouvé, flux ignoré.",
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
# PHASE 2 — MODÈLE OR-TOOLS
# ============================================================

SCALE = 10  # précision à 0.1 minute


def _sc(minutes: float) -> int:
    """Convertit des minutes en entier scalé pour OR-Tools."""
    return int(round(minutes * SCALE))


def _build_model_data(
    jobs: list[JobElementaire],
    matrice_duree: pd.DataFrame,
    capacites: dict,
    params_logistique: dict,
    v_type: str,
    alea: float = 0.0,
) -> dict:
    """
    Construit les structures de données pour OR-Tools à partir des jobs
    d'un seul type de véhicule.

    Le modèle est un Pickup & Delivery Problem with Time Windows (PDPTW) :
    - Pour chaque job : nœud de pickup (chargement en origine) + nœud de delivery (livraison)
    - Contrainte : pickup doit précéder delivery sur le même véhicule
    - Contrainte : capacité cumulée ≤ capacité du véhicule
    - Contrainte : exclusion propre/sale (pas de mélange sur le même véhicule)

    Structure retournée
    -------------------
    dict avec :
        n_nodes         : nombre total de nœuds (dépôt + 2 × n_jobs)
        n_vehicles      : borne haute du nombre de véhicules
        depot           : index du dépôt (0)
        time_matrix     : list[list[int]]  durées scalées entre nœuds
        time_windows    : list[(int,int)]  fenêtres par nœud
        pickups_deliveries : list[(int,int)]  paires pickup→delivery
        demands         : list[int]  +n_cont au pickup, -n_cont à la delivery
        vehicle_capacity: int  capacité max par véhicule (en nombre de contenants)
        max_poste_sc    : int  amplitude max d'un poste (scalée)
        pause_seuil_sc  : int  seuil déclenchement pause (scalé)
        pause_duree_sc  : int  durée pause (scalée)
        jobs            : list[JobElementaire]  les jobs dans l'ordre des nœuds
        node_to_job     : list[int|None]  mapping nœud → index job
        node_is_pickup  : list[bool]
        propre_sale_par_job : list[str]
        SCALE           : int
    """
    rh = params_logistique.get('rh', {})
    h_debut = _excel_time_to_minutes(rh.get('h_prise_min'), 360.0)
    h_fin   = _excel_time_to_minutes(rh.get('h_fin_max'),   1260.0)
    amplitude_max = float(rh.get('amplitude_totale', 450))
    pause_seuil   = 180.0
    pause_duree   = float(rh.get('pause', 30))

    # Nettoyage de la matrice de durée
    df = matrice_duree.copy()
    col0 = df.columns[0]
    df = df.set_index(col0)
    df.index   = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    facteur = 1 + alea

    def duree(a: str, b: str) -> int:
        if a == b:
            return 0
        try:
            return _sc(float(df.loc[a, b]) * facteur)
        except Exception:
            return _sc(30 * facteur)  # fallback 30 min

    # Filtrer les jobs pour ce type de véhicule
    jobs_v = [j for j in jobs if j.v_type_requis == v_type]
    if not jobs_v:
        return {}

    # Capacité du véhicule
    # On prend la capacité min parmi tous les types de contenants présents
    # pour ne jamais dépasser le pire cas
    capa_v = min(
        (capacites.get(v_type, {}).get(j.type_contenant, 1) for j in jobs_v),
        default=1
    )

    # Construction des nœuds
    # Index 0 = dépôt
    # Index 2k+1 = pickup du job k
    # Index 2k+2 = delivery du job k
    depot = 0
    n_jobs = len(jobs_v)
    n_nodes = 1 + 2 * n_jobs  # dépôt + paires

    # Récupération du site dépôt (stationnement initial du véhicule)
    depot_site = "HLS"  # valeur par défaut

    # Noms de sites par nœud
    node_sites = [depot_site]
    for j in jobs_v:
        node_sites.append(j.origine)     # pickup
        node_sites.append(j.destination) # delivery

    # Matrice de durée entre nœuds
    time_matrix = [[0] * n_nodes for _ in range(n_nodes)]
    for i in range(n_nodes):
        for k in range(n_nodes):
            if i != k:
                time_matrix[i][k] = duree(node_sites[i], node_sites[k])

    # Fenêtres temporelles
    # Dépôt : disponible toute la journée
    time_windows = [(_sc(h_debut), _sc(h_fin))]
    for j in jobs_v:
        # Pickup : entre h_dispo du flux et deadline
        time_windows.append((_sc(j.h_dispo), _sc(j.h_deadline)))
        # Delivery : entre h_dispo et deadline (le pickup doit précéder)
        time_windows.append((_sc(j.h_dispo), _sc(j.h_deadline)))

    # Paires pickup → delivery
    pickups_deliveries = []
    for idx in range(n_jobs):
        pickup_node   = 1 + 2 * idx
        delivery_node = 2 + 2 * idx
        pickups_deliveries.append((pickup_node, delivery_node))

    # Demandes de capacité (+n au pickup, -n à la delivery)
    demands = [0]  # dépôt
    for j in jobs_v:
        demands.append(j.nb_contenants)   # pickup : charge
        demands.append(-j.nb_contenants)  # delivery : décharge

    # Mapping nœud → job
    node_to_job = [None]
    node_is_pickup = [False]
    propre_sale_par_noeud = ['']
    for idx, j in enumerate(jobs_v):
        node_to_job.extend([idx, idx])
        node_is_pickup.extend([True, False])
        propre_sale_par_noeud.extend([j.propre_sale, j.propre_sale])

    # Nombre de véhicules (borne haute = nombre de jobs, OR-Tools minimisera)
    n_vehicles = n_jobs

    return {
        'n_nodes'            : n_nodes,
        'n_vehicles'         : n_vehicles,
        'depot'              : depot,
        'time_matrix'        : time_matrix,
        'time_windows'       : time_windows,
        'pickups_deliveries' : pickups_deliveries,
        'demands'            : demands,
        'vehicle_capacity'   : capa_v,
        'max_poste_sc'       : _sc(amplitude_max),
        'pause_seuil_sc'     : _sc(pause_seuil),
        'pause_duree_sc'     : _sc(pause_duree),
        'h_debut_sc'         : _sc(h_debut),
        'h_fin_sc'           : _sc(h_fin),
        'jobs'               : jobs_v,
        'node_sites'         : node_sites,
        'node_to_job'        : node_to_job,
        'node_is_pickup'     : node_is_pickup,
        'propre_sale_par_noeud': propre_sale_par_noeud,
        'SCALE'              : SCALE,
    }


def _solve_type(data: dict, time_limit_seconds: int = 60) -> dict | None:
    """
    Résout le PDPTW pour un type de véhicule avec OR-Tools.

    Objectifs hiérarchiques :
      1. Minimiser le nombre de véhicules (coût fixe très élevé)
      2. Minimiser le temps total de trajet

    Contraintes :
      - Fenêtres temporelles sur chaque nœud
      - Pickup précède Delivery (même véhicule)
      - Capacité cumulée ≤ capacité véhicule
      - Pas de mélange propre/sale sur le même véhicule
      - Amplitude poste ≤ max_poste
      - Pause obligatoire si amplitude > 3h

    Retourne un dict de résultats ou None si infaisable.
    """
    if not data:
        return None

    SCALE      = data['SCALE']
    n_nodes    = data['n_nodes']
    n_vehicles = data['n_vehicles']
    depot      = data['depot']

    manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, depot)
    routing = pywrapcp.RoutingModel(manager)

    # ── Callback de transit (durée de trajet) ───────────────────────────────
    def time_callback(from_idx, to_idx):
        return data['time_matrix'][manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)]

    cb_time = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(cb_time)

    # ── Dimension temporelle ────────────────────────────────────────────────
    routing.AddDimension(
        cb_time,
        slack_max=_sc(120),           # attente max avant fenêtre
        capacity=_sc(1440),           # horizon journée
        fix_start_cumul_to_zero=False,
        name='Time'
    )
    time_dim = routing.GetDimensionOrDie('Time')

    for node in range(1, n_nodes):
        idx = manager.NodeToIndex(node)
        tw = data['time_windows'][node]
        time_dim.CumulVar(idx).SetRange(tw[0], tw[1])

    # Fenêtre du dépôt
    for v in range(n_vehicles):
        time_dim.CumulVar(routing.Start(v)).SetRange(
            data['h_debut_sc'], data['h_fin_sc']
        )
        time_dim.CumulVar(routing.End(v)).SetRange(
            data['h_debut_sc'], data['h_fin_sc']
        )

    # ── Amplitude maximale par poste ────────────────────────────────────────
    solver = routing.solver()
    for v in range(n_vehicles):
        solver.Add(
            time_dim.CumulVar(routing.End(v)) -
            time_dim.CumulVar(routing.Start(v))
            <= data['max_poste_sc']
        )

    # ── Pauses obligatoires ─────────────────────────────────────────────────
    for v in range(n_vehicles):
        break_start = solver.IntVar(
            data['pause_seuil_sc'],
            data['max_poste_sc'],
            f'break_start_{v}'
        )
        break_iv = solver.FixedDurationIntervalVar(
            break_start, data['pause_duree_sc'], f'break_{v}'
        )
        time_dim.SetBreakIntervalsOfVehicle(
            [break_iv], v,
            node_visit_transits=[_sc(5)] * n_nodes
        )

    # ── Capacité par véhicule ───────────────────────────────────────────────
    def demand_callback(from_idx):
        return data['demands'][manager.IndexToNode(from_idx)]

    cb_demand = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        cb_demand,
        0,                                              # pas de slack
        [data['vehicle_capacity']] * n_vehicles,        # capacité identique par véhicule
        True,                                           # start cumul to zero
        'Capacity'
    )

    # ── Contraintes Pickup & Delivery ───────────────────────────────────────
    for pickup_node, delivery_node in data['pickups_deliveries']:
        pickup_idx   = manager.NodeToIndex(pickup_node)
        delivery_idx = manager.NodeToIndex(delivery_node)
        routing.AddPickupAndDelivery(pickup_idx, delivery_idx)
        # Même véhicule pour pickup et delivery
        solver.Add(
            routing.VehicleVar(pickup_idx) == routing.VehicleVar(delivery_idx)
        )
        # Pickup avant delivery (dimension temporelle)
        solver.Add(
            time_dim.CumulVar(pickup_idx) <= time_dim.CumulVar(delivery_idx)
        )

    # ── Contrainte d'exclusion propre/sale ──────────────────────────────────
    # Implémentation : on crée une dimension "propre" et "sale" par véhicule.
    # Si un véhicule transporte du PROPRE, il ne peut pas prendre du SALE
    # dans la même tournée (et vice-versa).
    # Approche : pénalité prohibitive sur les arcs entre nœuds incompatibles.
    COUT_INTERDIT = int(1e9)
    ps = data['propre_sale_par_noeud']
    for i in range(1, n_nodes):
        for k in range(1, n_nodes):
            if i != k and ps[i] and ps[k] and ps[i] != ps[k]:
                # Arc interdit entre nœuds propre et sale
                i_idx = manager.NodeToIndex(i)
                k_idx = manager.NodeToIndex(k)
                routing.NextVar(i_idx).RemoveValue(k_idx)

    # ── Objectif : coût fixe fort par véhicule ──────────────────────────────
    COUT_FIXE_VEH = int(1e8)
    for v in range(n_vehicles):
        routing.SetFixedCostOfVehicle(COUT_FIXE_VEH, v)

    # ── Résolution ──────────────────────────────────────────────────────────
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.seconds = time_limit_seconds
    params.log_search = False

    solution = routing.SolveWithParameters(params)
    if solution is None:
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

    return {
        'routes'         : routes,
        'n_vehicules'    : len(routes),
        'n_postes'       : len(routes),   # 1 poste = 1 tournée dans ce modèle
        'jobs_resolus'   : data['jobs'],
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


def _calculer_taux_occupation(route: dict, data: dict, params_logistique: dict) -> float:
    """
    Taux d'occupation = (temps en mission + temps de trajet) / amplitude_max_poste.
    """
    rh = params_logistique.get('rh', {})
    amplitude_max = float(rh.get('amplitude_totale', 450))

    temps_productif = 0.0
    times = route['times']
    for i in range(len(times) - 1):
        temps_productif += (times[i + 1] - times[i])  # inclut trajets + manutention

    return min(temps_productif / amplitude_max, 1.0) if amplitude_max > 0 else 0.0


def construire_postes(
    resultats_par_type: dict[str, dict],
    data_par_type: dict[str, dict],
    params_logistique: dict,
) -> list[PosteChauffeur]:
    """
    Construit la liste des postes chauffeurs à partir des résultats OR-Tools.
    """
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
                job_idx = data['node_to_job'][node]
                if job_idx is None:
                    continue
                job = data['jobs'][job_idx]
                missions.append({
                    'heure'         : t,
                    'site'          : site,
                    'job_id'        : job.job_id,
                    'flux_id'       : job.flux_id,
                    'type_contenant': job.type_contenant,
                    'nb_contenants' : job.nb_contenants,
                    'is_pickup'     : data['node_is_pickup'][node],
                    'propre_sale'   : job.propre_sale,
                })
            missions.sort(key=lambda x: x['heure'])

            postes.append(PosteChauffeur(
                poste_id        = f"{v_type}_{compteur:03d}",
                v_type          = v_type,
                h_debut         = route['h_debut'],
                h_fin           = route['h_fin'],
                amplitude       = route['amplitude'],
                missions        = missions,
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
    jobs_planifies = {p.missions[i]['job_id'] for p in postes for i in range(len(p.missions))}
    jobs_non_planifies = []
    for v_type, jlist in jobs_par_type.items():
        for j in jlist:
            if j.job_id not in jobs_planifies:
                jobs_non_planifies.append(j)

    taux_tries  = sorted(taux_par_poste, reverse=True)
    taux_moyen  = sum(taux_tries) / len(taux_tries) if taux_tries else 0.0

    return {
        'nb_vehicules_par_type' : nb_vehicules_par_type,
        'nb_postes_par_type'    : nb_postes_par_type,
        'nb_vehicules_total'    : sum(nb_vehicules_par_type.values()),
        'nb_postes_total'       : sum(nb_postes_par_type.values()),
        'taux_moyen'            : taux_moyen,
        'taux_par_poste'        : taux_tries,
        'jobs_non_planifies'    : jobs_non_planifies,
        'nb_jobs_non_planifies' : len(jobs_non_planifies),
        'solveur'               : 'OR-Tools' if ORTOOLS_AVAILABLE else 'indisponible',
    }


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
        data = _build_model_data(
            jobs_v, matrice_duree, capacites,
            params_logistique, v_type, alea
        )
        if not data:
            resultats_par_type[v_type] = None
            continue

        data_par_type[v_type] = data
        res = _solve_type(data, time_limit_seconds=time_limit_seconds)

        if res is not None:
            resultats_par_type[v_type] = res
            _log(
                f"  ✅ {v_type} : {res['n_vehicules']} véhicule(s), "
                f"{res['n_postes']} poste(s)",
                "success"
            )
        else:
            resultats_par_type[v_type] = None
            _log(f"  ❌ {v_type} : pas de solution trouvée.", "error")

    # ── 3. Post-traitement ───────────────────────────────────────────────────
    postes = construire_postes(resultats_par_type, data_par_type, params_logistique)
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
