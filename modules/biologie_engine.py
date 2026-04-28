import warnings
import pandas as pd
import numpy as np
try:
    from ortools.constraint_solver import routing_enums_pb2  # noqa
    from ortools.constraint_solver import pywrapcp            # noqa
    ORTOOLS_AVAILABLE = True
except Exception:
    routing_enums_pb2 = None
    pywrapcp          = None
    ORTOOLS_AVAILABLE = False

# ==========================================
# PARTIE 1 : FONCTIONS UTILITAIRES
# ==========================================

def minutes_to_hhmm(minutes):
    """Convertit des minutes depuis minuit en format HH:MM."""
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h:02d}:{m:02d}"


def generate_target_windows(sites_config):
    """
    Génère les rendez-vous théoriques.
    - 1er passage : au plus tôt à l'heure 'open'.
    - Dernier passage : au plus tôt à l'heure 'close'.
    - Intermédiaires : répartis équitablement.

    Interface inchangée — utilisée en amont par st.session_state.
    """
    tasks = []
    for site_name, config in sites_config.items():
        ouv, fer, freq = config['open'], config['close'], config['freq']

        if freq <= 1:
            points_passage = [fer]
        else:
            intervalle = (fer - ouv) / (freq - 1)
            points_passage = [ouv + (i * intervalle) for i in range(freq)]

        marge_retard = 20

        for i, cible in enumerate(points_passage):
            is_premier = (i == 0)
            is_dernier = (i == len(points_passage) - 1)

            if is_premier or is_dernier:
                window = (cible, cible + marge_retard)
            else:
                window = (cible - 10, cible + 10)

            tasks.append({
                'site_name': str(site_name).strip().upper(),
                'window': window,
                'target_time': cible,
                'is_fixed': is_premier or is_dernier,
                'done': False
            })

    return sorted(tasks, key=lambda x: x['window'][0])


# ==========================================
# PARTIE 2 : CONSTRUCTION DU MODÈLE OR-TOOLS
# ==========================================

def _build_ortools_data(m_duree_df, tasks, temps_collecte, max_tournee, config_rh):
    """
    Traduit les données métier en structures attendues par OR-Tools.

    Retourne un dictionnaire 'data' contenant :
      - distance_matrix   : matrice de durées (int, en minutes × SCALE)
      - time_windows      : fenêtres [earliest, latest] par nœud
      - service_times     : durée de collecte par nœud
      - num_vehicles      : borne supérieure (nombre de tâches, on laisse OR-Tools réduire)
      - depot             : index du dépôt
      - node_to_task      : mapping index nœud → tâche originale
      - site_names        : liste ordonnée des nœuds
      - SCALE             : facteur d'échelle entier pour OR-Tools
    """
    SCALE = 10  # OR-Tools travaille en entiers : on multiplie les minutes × 10

    # --- Préparation de la matrice de durées ---
    df = m_duree_df.copy()
    nom_col = df.columns[0]
    df = df.set_index(nom_col)
    df.index = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    depot_name = "HLS"

    # Liste ordonnée des nœuds : dépôt en index 0, puis une entrée par tâche
    # (un même site peut apparaître plusieurs fois s'il a freq > 1)
    node_names = [depot_name]
    node_to_task = [None]  # index 0 = dépôt, pas de tâche associée

    for task in tasks:
        node_names.append(task['site_name'])
        node_to_task.append(task)

    n = len(node_names)

    # --- Matrice de durées (entiers) ---
    distance_matrix = []
    for i, from_site in enumerate(node_names):
        row = []
        for j, to_site in enumerate(node_names):
            if from_site not in df.index or to_site not in df.columns:
                row.append(0)
            elif i == j:
                row.append(0)
            else:
                val = df.loc[from_site, to_site]
                row.append(int(round(float(val) * SCALE)))
        distance_matrix.append(row)

    # --- Fenêtres temporelles (entières, scalées) ---
    # Dépôt : disponible toute la journée
    time_windows = [(int(200 * SCALE), int(1440 * SCALE))]
    for task in tasks:
        tw_open  = int(task['window'][0] * SCALE)
        tw_close = int(task['window'][1] * SCALE)
        time_windows.append((tw_open, tw_close))

    # --- Durées de service (collecte) ---
    service_times = [0]  # dépôt : pas de service
    for _ in tasks:
        service_times.append(int(temps_collecte * SCALE))

    # --- Nombre de véhicules (borne haute) ---
    # On démarre avec autant de véhicules que de tâches (OR-Tools minimisera)
    num_vehicles = len(tasks)

    # --- Amplitude maximale par poste (en scalé) ---
    max_poste_scaled = int(config_rh.get('amplitude', 450) * SCALE)
    pause_seuil_scaled = int(180 * SCALE)  # 3h avant obligation de pause
    pause_duree_scaled = int(config_rh.get('pause', 30) * SCALE)
    releve_scaled = int(config_rh.get('releve', 15) * SCALE)
    max_tournee_scaled = int(max_tournee * SCALE)

    return {
        'distance_matrix': distance_matrix,
        'time_windows': time_windows,
        'service_times': service_times,
        'num_vehicles': num_vehicles,
        'depot': 0,
        'node_to_task': node_to_task,
        'node_names': node_names,
        'SCALE': SCALE,
        'max_poste_scaled': max_poste_scaled,
        'max_tournee_scaled': max_tournee_scaled,
        'pause_seuil_scaled': pause_seuil_scaled,
        'pause_duree_scaled': pause_duree_scaled,
        'releve_scaled': releve_scaled,
        'n_tasks': len(tasks),
    }


def _solve_ortools(data, time_limit_seconds=30):
    """
    Résout le VRPTW avec OR-Tools.

    Objectif hiérarchique :
      1. Minimiser le nombre de véhicules utilisés
      2. Minimiser le temps total de trajet

    Retourne la solution brute OR-Tools (ou None si infaisable).
    """
    manager = pywrapcp.RoutingIndexManager(
        len(data['distance_matrix']),
        data['num_vehicles'],
        data['depot']
    )
    routing = pywrapcp.RoutingModel(manager)

    SCALE = data['SCALE']

    # --- Callback de durée de transit ---
    def time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node   = manager.IndexToNode(to_index)
        transit   = data['distance_matrix'][from_node][to_node]
        service   = data['service_times'][from_node]
        return transit + service

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # --- Dimension temporelle avec fenêtres ---
    routing.AddDimension(
        transit_callback_index,
        slack_max=int(60 * SCALE),          # attente max avant une fenêtre
        capacity=int(1440 * SCALE),          # horizon journée complète
        fix_start_cumul_to_zero=False,
        name='Time'
    )
    time_dimension = routing.GetDimensionOrDie('Time')

    # Appliquer les fenêtres temporelles sur chaque nœud
    for node_idx in range(1, len(data['time_windows'])):
        index = manager.NodeToIndex(node_idx)
        tw_open, tw_close = data['time_windows'][node_idx]
        time_dimension.CumulVar(index).SetRange(tw_open, tw_close)

    # Fenêtre dépôt de départ
    depot_start = manager.NodeToIndex(data['depot'])
    time_dimension.CumulVar(depot_start).SetRange(
        data['time_windows'][0][0],
        data['time_windows'][0][1]
    )

    # --- Contrainte d'amplitude de tournée (max_tournee) ---
    for v in range(data['num_vehicles']):
        start_var = time_dimension.CumulVar(routing.Start(v))
        end_var   = time_dimension.CumulVar(routing.End(v))
        routing.solver().Add(
            end_var - start_var <= data['max_tournee_scaled']
        )

    # --- Contrainte de pause obligatoire (> 3h → pause 30 min) ---
    # Modélisée via une dimension "temps de conduite cumulé" avec break
    # OR-Tools gère les breaks via IntervalVar sur chaque véhicule
    solver = routing.solver()
    for v in range(data['num_vehicles']):
        # Pause obligatoire si amplitude > 3h : on crée une IntervalVar de pause
        # La pause peut se placer n'importe où entre 3h et fin de poste
        break_start = solver.IntVar(
            data['pause_seuil_scaled'],
            data['max_poste_scaled'],
            f'break_start_v{v}'
        )
        break_interval = solver.FixedDurationIntervalVar(
            break_start,
            data['pause_duree_scaled'],
            f'break_v{v}'
        )
        time_dimension.SetBreakIntervalsOfVehicle(
            [break_interval], v,
            node_visit_transits=int(5 * data['SCALE'])
        )

    # --- Objectif : minimiser nombre de véhicules, puis temps total ---
    # Pénalité forte pour chaque véhicule utilisé
    for v in range(data['num_vehicles']):
        routing.SetFixedCostOfVehicle(int(1e7), v)  # coût fixe par véhicule

    # --- Stratégie de recherche ---
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.seconds = time_limit_seconds
    search_params.log_search = False

    solution = routing.SolveWithParameters(search_params)

    if solution:
        return manager, routing, solution, time_dimension
    return None, None, None, None


# ==========================================
# PARTIE 3 : EXTRACTION ET FORMATAGE
# ==========================================

def _extract_tournees(manager, routing, solution, time_dimension, data):
    """
    Extrait les tournées OR-Tools et les reformate en liste de dicts
    identiques à l'ancien format :
      [{'site': ..., 'heure': ...}, ...]

    Seules les tournées non vides (ayant au moins un nœud hors dépôt) sont retournées.
    """
    SCALE = data['SCALE']
    depot_name = data['node_names'][0]
    tournees = []

    for v in range(data['num_vehicles']):
        index = routing.Start(v)
        route_nodes = []

        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            time_var = time_dimension.CumulVar(index)
            t = solution.Min(time_var) / SCALE
            route_nodes.append({'node': node, 'heure': t})
            index = solution.Value(routing.NextVar(index))

        # Nœud final (retour dépôt)
        node = manager.IndexToNode(index)
        time_var = time_dimension.CumulVar(index)
        t = solution.Min(time_var) / SCALE
        route_nodes.append({'node': node, 'heure': t})

        # Filtrer les tournées vides (dépôt → dépôt directement)
        non_depot = [n for n in route_nodes if n['node'] != 0]
        if not non_depot:
            continue

        # Reformater en [{'site': ..., 'heure': ...}]
        tournee = []
        for entry in route_nodes:
            site_name = data['node_names'][entry['node']]
            tournee.append({'site': site_name, 'heure': entry['heure']})

        tournees.append(tournee)

    return tournees


# ==========================================
# PARTIE 4 : AFFECTATION VÉHICULES / POSTES
# ==========================================

def assign_to_vehicles(tournees, config_rh):
    """
    Répartit les tournées par véhicule et par chauffeur (vacation).
    Interface identique à l'original — utilisable indépendamment depuis st.session_state.
    """
    MAX_POSTE = config_rh.get('amplitude', 450)
    PAUSE     = config_rh.get('pause', 30)
    RELEVE    = config_rh.get('releve', 15)

    tournees_triees = sorted(tournees, key=lambda x: x[0]['heure'])
    flotte_vehicules = {}

    for trne in tournees_triees:
        debut_trne = trne[0]['heure']
        fin_trne   = trne[-1]['heure']
        assigned   = False

        for v_id, postes in flotte_vehicules.items():
            dernier_poste  = postes[-1]
            h_debut_poste  = dernier_poste[0][0]['heure']
            h_fin_poste    = dernier_poste[-1][-1]['heure']

            # 1. Ajout au chauffeur actuel (même poste)
            if (fin_trne - h_debut_poste) <= MAX_POSTE:
                marge = PAUSE if (h_fin_poste - h_debut_poste) > 180 else 0
                if h_fin_poste + marge <= debut_trne:
                    dernier_poste.append(trne)
                    assigned = True
                    break

            # 2. Relève sur le même véhicule (nouveau chauffeur)
            elif h_fin_poste + RELEVE <= debut_trne:
                postes.append([trne])
                assigned = True
                break

        if not assigned:
            v_num = len(flotte_vehicules) + 1
            flotte_vehicules[f"Véhicule {v_num}"] = [[trne]]

    return flotte_vehicules


def optimiser_postes_chauffeurs(flotte, config_rh, souplesse=False):
    """
    Tente de fusionner les vacations pour réduire le nombre de chauffeurs
    SANS ajouter de nouveaux véhicules.
    Interface identique à l'original.
    """
    MAX_POSTE = config_rh.get('amplitude', 450)
    PAUSE     = config_rh.get('pause', 30)
    RELEVE    = config_rh.get('releve', 15)

    toutes_vacations = []
    for v_id, vacations in flotte.items():
        for vac in vacations:
            toutes_vacations.append(vac)

    toutes_vacations.sort(key=lambda x: x[0][0]['heure'])

    nb_vehicules_max  = len(flotte)
    nouvelle_flotte   = {f"Véhicule {i+1}": [] for i in range(nb_vehicules_max)}

    for vac_a_placer in toutes_vacations:
        debut_v = vac_a_placer[0][0]['heure']
        fin_v   = vac_a_placer[-1][-1]['heure']
        placed  = False

        # Essai de fusion dans un poste existant
        for v_id, postes in nouvelle_flotte.items():
            for poste in postes:
                h_debut_poste = poste[0][0]['heure']
                h_fin_poste   = poste[-1][-1]['heure']
                nouvelle_amp  = max(h_fin_poste, fin_v) - min(h_debut_poste, debut_v)

                if nouvelle_amp <= MAX_POSTE:
                    if souplesse:
                        for decalage in range(20, 31):
                            if (fin_v + decalage >= h_debut_poste and debut_v <= h_fin_poste + decalage):
                                poste.extend(vac_a_placer)
                                poste.sort(key=lambda x: x[0]['heure'])
                                placed = True
                                break
                    else:
                        if fin_v + 5 <= h_debut_poste or debut_v >= h_fin_poste + 5:
                            poste.extend(vac_a_placer)
                            poste.sort(key=lambda x: x[0]['heure'])
                            placed = True
                            break
                if placed:
                    break
            if placed:
                break

        # Sinon : relève sur véhicule existant
        if not placed:
            for v_id in nouvelle_flotte:
                postes = nouvelle_flotte[v_id]
                if not postes:
                    postes.append(vac_a_placer)
                    placed = True
                    break
                else:
                    conflit = False
                    for poste in postes:
                        h_dep = poste[0][0]['heure']
                        h_fin = poste[-1][-1]['heure']
                        if not (fin_v + RELEVE <= h_dep or debut_v >= h_fin + RELEVE):
                            conflit = True
                            break
                    if not conflit:
                        postes.append(vac_a_placer)
                        postes.sort(key=lambda x: x[0][0]['heure'])
                        placed = True
                        break

        if not placed:
            v_id_secu = list(nouvelle_flotte.keys())[0]
            nouvelle_flotte[v_id_secu].append(vac_a_placer)

    return {k: v for k, v in nouvelle_flotte.items() if v}


# ==========================================
# PARTIE 5 : MOTEUR PRINCIPAL (point d'entrée)
# ==========================================

def run_optimization(
    m_duree_df,
    sites_config,
    temps_collecte,
    max_tournee,
    config_rh=None,
    souplesse=False,
    time_limit_seconds=30
):
    """
    Optimise les tournées de biologie — point d'entrée principal.
    Interface identique à l'original, compatible avec app.py, param_bio.py
    et resultats_bio.py sans aucune modification.

    Paramètres
    ----------
    m_duree_df         : pd.DataFrame
        Matrice de durées — st.session_state["data"]["matrice_duree"].
        La première colonne contient les noms de sites.
    sites_config       : dict  {nom_site: {"open": int, "close": int, "freq": int}}
        st.session_state["biologie_config"]["sites"].
    temps_collecte     : int  durée de collecte sur site (minutes).
    max_tournee        : int  durée maximale dépôt→dépôt (minutes).
    config_rh          : dict | None  {"amplitude": int, "pause": int, "releve": int}
        st.session_state["biologie_config"]["rh"].
        Si None (cas app.py v16), lu automatiquement depuis session_state
        avec fallback sur les valeurs par défaut.
    souplesse          : bool  st.session_state.get("souplesse_fusion", False).
    time_limit_seconds : int  budget temps du solveur OR-Tools (défaut 30 s).

    Retourne
    --------
    dict {"Véhicule N": [[tournee_1, tournee_2, ...], ...], ...}
    Structure identique à l'ancienne version.
    """
    # ── Résolution de config_rh (correction bug app.py v16) ──────────────
    if config_rh is None:
        try:
            import streamlit as st
            config_rh = st.session_state.get("biologie_config", {}).get(
                "rh", {"amplitude": 450, "pause": 30, "releve": 15}
            )
        except Exception:
            config_rh = {"amplitude": 450, "pause": 30, "releve": 15}

    # ── 1. Fenêtres cibles ────────────────────────────────────────────────
    clean_config = {str(k).strip().upper(): v for k, v in sites_config.items()}
    tasks        = generate_target_windows(clean_config)

    # ── 2. Matrice nettoyée ───────────────────────────────────────────────
    df = m_duree_df.copy()
    col_noms = df.columns[0]
    df = df.set_index(col_noms)
    df.index   = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    # ── 3. Tentative OR-Tools ─────────────────────────────────────────────
    tournees_unitaires = None

    if ORTOOLS_AVAILABLE:
        data = _build_ortools_data(
            m_duree_df, tasks, temps_collecte, max_tournee, config_rh
        )
        manager, routing, solution, time_dimension = _solve_ortools(
            data, time_limit_seconds=time_limit_seconds
        )
        if solution is not None:
            tournees_unitaires = _extract_tournees(
                manager, routing, solution, time_dimension, data
            )
        else:
            warnings.warn(
                f"OR-Tools n'a pas trouvé de solution en {time_limit_seconds}s. "
                "Bascule sur l'heuristique gloutonne.",
                RuntimeWarning,
                stacklevel=2
            )

    # ── 4. Fallback greedy ────────────────────────────────────────────────
    if tournees_unitaires is None:
        tournees_unitaires = _greedy_fallback(
            m_duree_df, tasks, temps_collecte, max_tournee
        )

    # ── 5. Affectation véhicules et chauffeurs ────────────────────────────
    resultat_initial  = assign_to_vehicles(tournees_unitaires, config_rh)

    # ── 6. Compactage des postes ──────────────────────────────────────────
    resultat_optimise = optimiser_postes_chauffeurs(
        resultat_initial, config_rh, souplesse=souplesse
    )

    return resultat_optimise


# ==========================================
# PARTIE 6 : FALLBACK GREEDY (filet de sécurité)
# ==========================================

def _greedy_fallback(m_duree_df, tasks, temps_collecte, max_tournee):
    """
    Heuristique gloutonne de l'ancienne version, conservée comme fallback
    au cas où OR-Tools ne trouve pas de solution (instances dégénérées,
    fenêtres très serrées, timeout).
    """
    df = m_duree_df.copy()
    nom_col = df.columns[0]
    df = df.set_index(nom_col)
    df.index = df.index.astype(str).str.strip().str.upper()
    df.columns = df.columns.astype(str).str.strip().str.upper()

    depot = "HLS"
    tasks_copy = [t.copy() for t in tasks]
    tournees_unitaires = []

    while any(not t['done'] for t in tasks_copy):
        remaining = [t for t in tasks_copy if not t['done']]
        if not remaining:
            break

        first_task = remaining[0]
        site_cible = first_task['site_name']
        if site_cible not in df.index:
            first_task['done'] = True
            continue

        heure_depart = max(300, first_task['window'][0] - df.loc[depot, site_cible])
        current_time = heure_depart
        tournee = [{'site': depot, 'heure': current_time}]
        current_site = depot
        sites_visites = set()

        while True:
            best_idx, score_min = None, float('inf')

            for idx, task in enumerate(tasks_copy):
                t_site = task['site_name']
                if task['done'] or t_site not in df.index or t_site in sites_visites:
                    continue
                trajet  = df.loc[current_site, t_site]
                retour  = df.loc[t_site, depot]
                arrivee = current_time + trajet
                debut   = max(arrivee, task['window'][0])
                fin     = debut + temps_collecte

                if (fin + retour - tournee[0]['heure']) <= max_tournee:
                    attente = max(0, task['window'][0] - arrivee)
                    score   = attente + (trajet * 2)
                    if score < score_min:
                        score_min, best_idx = score, idx

            if best_idx is not None:
                task   = tasks_copy[best_idx]
                t_site = task['site_name']
                h_reel = max(current_time + df.loc[current_site, t_site], task['window'][0])
                tournee.append({'site': t_site, 'heure': h_reel})
                current_time = h_reel + temps_collecte
                task['done'] = True
                current_site = t_site
                sites_visites.add(t_site)
                for autre in df.columns:
                    if df.loc[t_site, autre] == 0:
                        sites_visites.add(autre)
            else:
                tournee.append({
                    'site': depot,
                    'heure': current_time + df.loc[current_site, depot]
                })
                break

        tournees_unitaires.append(tournee)

    return tournees_unitaires
